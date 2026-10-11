"""Cliente HTTP autenticado para o backend VetFlow (Go + chi).

Responsabilidades, mantidas FORA das tools MCP para manter o server.py fino:
  * fazer login (POST /auth/login) e guardar o JWT em cache;
  * injetar `Authorization: Bearer <token>` em toda requisição protegida;
  * re-autenticar automaticamente UMA vez quando o backend responde 401
    (token expirado), e aí repetir a requisição original;
  * traduzir erros HTTP em uma exceção única e legível para a LLM.

Toda rota de dados do backend passa por AuthMiddleware e é escopada ao vet
autenticado pelo próprio token, então o cliente NÃO precisa passar vet_id em
lugar nenhum — o login define o escopo.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import Settings


class VetFlowError(RuntimeError):
    """Erro já formatado para ser devolvido à LLM (mensagem curta e útil)."""


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
        self._token: str | None = None
        # Evita duas corrotinas logando ao mesmo tempo no primeiro acesso.
        self._login_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- autenticação -------------------------------------------------------

    async def _login(self) -> None:
        """Obtém um JWT novo em POST /auth/login e o guarda em cache."""
        async with self._login_lock:
            resp = await self._http.post(
                "/auth/login",
                json={"email": self._settings.email, "password": self._settings.password},
            )
            if resp.status_code != httpx.codes.OK:
                raise VetFlowError(
                    f"Falha no login no VetFlow (HTTP {resp.status_code}). "
                    "Verifique VETFLOW_EMAIL / VETFLOW_PASSWORD."
                )
            data = resp.json()
            token = data.get("token") or data.get("access_token")
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
        resp = await self._http.request(method, path, params=params, json=json, headers=headers)

        # Token expirado: re-autentica UMA vez e repete.
        if resp.status_code == httpx.codes.UNAUTHORIZED:
            self._token = None
            headers = await self._auth_headers()
            resp = await self._http.request(method, path, params=params, json=json, headers=headers)

        if resp.status_code >= 400:
            raise VetFlowError(self._explain_error(method, path, resp))

        if not resp.content:
            return None
        return resp.json()

    @staticmethod
    def _explain_error(method: str, path: str, resp: httpx.Response) -> str:
        """Transforma um erro HTTP em uma linha legível para a LLM."""
        detail = ""
        try:
            body = resp.json()
            detail = body.get("error") or body.get("message") or ""
        except Exception:  # corpo não-JSON
            detail = (resp.text or "")[:200]
        suffix = f" — {detail}" if detail else ""
        return f"{method} {path} falhou (HTTP {resp.status_code}){suffix}"
