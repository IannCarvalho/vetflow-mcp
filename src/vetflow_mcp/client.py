"""Cliente HTTP autenticado para o backend VetFlow (Go + chi).

Responsabilidades, mantidas FORA das tools MCP para manter o server.py fino:
  * fazer login (POST /auth/login) e guardar o JWT em cache;
  * injetar `Authorization: Bearer <token>` em toda requisição protegida;
  * re-autenticar automaticamente UMA vez quando o backend responde 401
    (token expirado), e aí repetir a requisição original;
  * traduzir erros HTTP, de rede (timeout, conexão recusada) e respostas
    não-JSON em uma exceção única e legível para a LLM (`VetFlowError`).

Toda rota de dados do backend passa por AuthMiddleware e é escopada ao vet
autenticado pelo próprio token, então o cliente NÃO precisa passar vet_id em
lugar nenhum — o login define o escopo.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from .config import Settings


class VetFlowError(ToolError, RuntimeError):
    """Erro já formatado para ser devolvido à LLM (mensagem curta e útil).

    Herda de `ToolError` do SDK de propósito: no mcp 2.x só uma `ToolError` tem
    a mensagem repassada ao modelo. Qualquer outra exceção é tratada como crash
    e o modelo recebe apenas "Error executing tool <nome>" — sem a causa, ele
    não consegue explicar ao usuário que a sessão expirou ou que o pet não
    existe. `RuntimeError` segue como segunda base por compatibilidade com
    quem já capturava esse tipo.
    """


class VetFlowClient:
    """Wrapper fino sobre httpx.AsyncClient com login e refresh de token.

    Uso:
        client = VetFlowClient(Settings.from_env())
        pets = await client.get("/pets", params={"owner_id": "..."})
        await client.aclose()
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.timeout_s,
        )
        # Modo pass-through: um JWT pré-fornecido (ex. do usuário logado). Nesse
        # caso o cliente NÃO faz login nem re-login — usa o token como está.
        self._token: str | None = settings.token or None
        self._passthrough = bool(settings.token)
        # Evita duas corrotinas logando ao mesmo tempo no primeiro acesso.
        self._login_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- autenticação -------------------------------------------------------

    async def _login(self) -> None:
        """Obtém um JWT novo em POST /auth/login e o guarda em cache.

        Não é chamado no modo pass-through (token pré-fornecido).
        """
        async with self._login_lock:
            # Checagem dupla: quem esperava o lock enquanto outra corrotina
            # logava reaproveita o token novo em vez de logar de novo.
            if self._token is not None:
                return
            resp = await self._send(
                "POST",
                "/auth/login",
                json={"email": self._settings.email, "password": self._settings.password},
            )
            if resp.status_code != httpx.codes.OK:
                raise VetFlowError(
                    f"Falha no login no VetFlow (HTTP {resp.status_code}). "
                    "Verifique VETFLOW_EMAIL / VETFLOW_PASSWORD."
                )
            try:
                data = resp.json()
            except ValueError:  # corpo não-JSON
                data = None
            body = data if isinstance(data, dict) else {}
            token = body.get("token") or body.get("access_token")
            if not token:
                raise VetFlowError(
                    "Login retornou 200 mas sem campo 'token' no corpo — "
                    "confira o formato da resposta de /auth/login."
                )
            self._token = token

    async def _auth_headers(self) -> dict[str, str]:
        if self._token is None:
            await self._login()
        return {"Authorization": f"Bearer {self._token}"}

    # ---- requisições --------------------------------------------------------

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET autenticado com re-login automático em 401. Retorna JSON decodificado."""
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return await self._request("POST", path, json=json)

    async def put(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return await self._request("PUT", path, json=json)

    async def delete(self, path: str) -> Any:
        return await self._request("DELETE", path)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        headers = await self._auth_headers()
        resp = await self._send(method, path, params=params, json=json, headers=headers)

        # Token expirado (401). No modo login, re-autentica UMA vez e repete.
        # No modo pass-through, o token é do usuário e não pode ser renovado
        # aqui — devolve um erro claro para a LLM explicar que a sessão expirou.
        if resp.status_code == httpx.codes.UNAUTHORIZED:
            if self._passthrough:
                raise VetFlowError(
                    "Sessão expirada: o token do usuário não é mais válido "
                    "(faça login novamente no VetFlow)."
                )
            # Só descarta o token se ainda for o que falhou: outra corrotina
            # pode tê-lo renovado enquanto esta requisição estava em voo.
            if headers["Authorization"] == f"Bearer {self._token}":
                self._token = None
            headers = await self._auth_headers()
            resp = await self._send(method, path, params=params, json=json, headers=headers)

        if resp.status_code >= 400:
            raise VetFlowError(self._explain_error(method, path, resp))

        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:  # 2xx com corpo não-JSON (ex.: página de proxy)
            raise VetFlowError(
                f"{method} {path}: resposta inesperada do backend "
                f"(HTTP {resp.status_code}, corpo não-JSON)."
            ) from exc

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Envia a requisição, traduzindo falhas de transporte em `VetFlowError`.

        Sem isto, um timeout ou conexão recusada chega à LLM como um erro cru do
        httpx. O caso típico é o backend no plano free do Render "dormindo": o
        primeiro acesso demora a acordar e estoura o timeout.
        """
        try:
            return await self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise VetFlowError(
                f"{method} {path}: o backend VetFlow não respondeu em "
                f"{self._settings.timeout_s:g}s. Ele pode estar iniciando ou fora do "
                "ar; tente novamente em instantes."
            ) from exc
        except httpx.RequestError as exc:
            raise VetFlowError(
                f"{method} {path}: não consegui conectar ao backend VetFlow em "
                f"{self._settings.base_url} ({type(exc).__name__})."
            ) from exc

    @staticmethod
    def _explain_error(method: str, path: str, resp: httpx.Response) -> str:
        """Transforma um erro HTTP em uma linha legível para a LLM."""
        detail = ""
        try:
            body = resp.json()
            detail = body.get("error") or body.get("message") or ""
        except (ValueError, AttributeError):  # corpo não-JSON ou não-objeto
            detail = (resp.text or "")[:200]
        suffix = f" — {detail}" if detail else ""
        return f"{method} {path} falhou (HTTP {resp.status_code}){suffix}"
