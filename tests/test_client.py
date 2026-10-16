"""Testes do VetFlowClient com o backend mockado (respx).

Rodar:  uv run pytest   (ou: pytest)
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

from vetflow_mcp.client import VetFlowClient, VetFlowError
from vetflow_mcp.config import Settings

BASE = "http://backend.test"


def _settings() -> Settings:
    return Settings(base_url=BASE, email="vet@test", password="pw", timeout_s=5)


@respx.mock
async def test_get_faz_login_e_injeta_bearer() -> None:
    login = respx.post(f"{BASE}/auth/login").mock(
        return_value=httpx.Response(200, json={"token": "jwt-123"})
    )
    pets = respx.get(f"{BASE}/pets").mock(
        return_value=httpx.Response(200, json=[{"id": "p1", "name": "Rex"}])
    )

    client = VetFlowClient(_settings())
    try:
        result = await client.get("/pets")
    finally:
        await client.aclose()

    assert result == [{"id": "p1", "name": "Rex"}]
    assert login.called
    assert pets.calls.last.request.headers["Authorization"] == "Bearer jwt-123"


@respx.mock
async def test_reautentica_uma_vez_em_401() -> None:
    respx.post(f"{BASE}/auth/login").mock(
        side_effect=[
            httpx.Response(200, json={"token": "velho"}),
            httpx.Response(200, json={"token": "novo"}),
        ]
    )
    # Primeiro GET devolve 401 (token expirado); o segundo passa.
    route = respx.get(f"{BASE}/clients").mock(
        side_effect=[
            httpx.Response(401, json={"error": "invalid or expired token"}),
            httpx.Response(200, json=[{"id": "c1", "name": "Ana"}]),
        ]
    )

    client = VetFlowClient(_settings())
    try:
        result = await client.get("/clients")
    finally:
        await client.aclose()

    assert result == [{"id": "c1", "name": "Ana"}]
    assert route.call_count == 2
    assert route.calls.last.request.headers["Authorization"] == "Bearer novo"


@respx.mock
async def test_erro_http_vira_vetflowerror_legivel() -> None:
    respx.post(f"{BASE}/auth/login").mock(
        return_value=httpx.Response(200, json={"token": "jwt"})
    )
    respx.get(f"{BASE}/pets").mock(
        return_value=httpx.Response(500, json={"error": "Falha ao recuperar pets"})
    )

    client = VetFlowClient(_settings())
    try:
        with pytest.raises(VetFlowError, match="Falha ao recuperar pets"):
            await client.get("/pets")
    finally:
        await client.aclose()


def test_vetflowerror_e_toolerror_do_sdk() -> None:
    """Regressão: no mcp 2.x só ToolError tem a mensagem repassada ao modelo.

    Qualquer outra exceção vira "Error executing tool <nome>", sem a causa.
    RuntimeError segue como base por compatibilidade.
    """
    erro = VetFlowError("x")
    assert isinstance(erro, ToolError)
    assert isinstance(erro, RuntimeError)


@respx.mock
async def test_timeout_vira_vetflowerror_legivel() -> None:
    respx.post(f"{BASE}/auth/login").mock(
        return_value=httpx.Response(200, json={"token": "jwt"})
    )
    respx.get(f"{BASE}/pets").mock(side_effect=httpx.ConnectTimeout("timed out"))

    client = VetFlowClient(_settings())
    try:
        with pytest.raises(VetFlowError, match="não respondeu em 5s"):
            await client.get("/pets")
    finally:
        await client.aclose()


@respx.mock
async def test_conexao_recusada_vira_vetflowerror_legivel() -> None:
    respx.post(f"{BASE}/auth/login").mock(side_effect=httpx.ConnectError("refused"))

    client = VetFlowClient(_settings())
    try:
        with pytest.raises(VetFlowError, match="não consegui conectar ao backend") as exc:
            await client.get("/pets")
    finally:
        await client.aclose()
    assert isinstance(exc.value.__cause__, httpx.ConnectError)  # causa preservada


@respx.mock
async def test_resposta_2xx_nao_json_vira_vetflowerror() -> None:
    respx.post(f"{BASE}/auth/login").mock(
        return_value=httpx.Response(200, json={"token": "jwt"})
    )
    respx.get(f"{BASE}/pets").mock(return_value=httpx.Response(200, text="<html>oi</html>"))

    client = VetFlowClient(_settings())
    try:
        with pytest.raises(VetFlowError, match="corpo não-JSON"):
            await client.get("/pets")
    finally:
        await client.aclose()


@respx.mock
async def test_login_recusado_vira_vetflowerror() -> None:
    respx.post(f"{BASE}/auth/login").mock(
        return_value=httpx.Response(401, json={"error": "invalid credentials"})
    )

    client = VetFlowClient(_settings())
    try:
        with pytest.raises(VetFlowError, match="Falha no login"):
            await client.get("/pets")
    finally:
        await client.aclose()


@respx.mock
async def test_login_200_sem_token_ou_nao_json_vira_vetflowerror() -> None:
    respx.post(f"{BASE}/auth/login").mock(
        side_effect=[
            httpx.Response(200, json={"user": "sem token"}),
            httpx.Response(200, text="nao-e-json"),
        ]
    )

    client = VetFlowClient(_settings())
    try:
        for _ in range(2):
            with pytest.raises(VetFlowError, match="sem campo 'token'"):
                await client.get("/pets")
    finally:
        await client.aclose()


@respx.mock
async def test_requests_concorrentes_fazem_um_login_so() -> None:
    """O lock evita logins duplicados: quem esperou reaproveita o token novo."""

    async def login_lento(request: httpx.Request) -> httpx.Response:
        # Suspende de verdade: as outras corrotinas chegam ao lock enquanto isso.
        await asyncio.sleep(0.01)
        return httpx.Response(200, json={"token": "jwt"})

    login = respx.post(f"{BASE}/auth/login").mock(side_effect=login_lento)
    respx.get(f"{BASE}/pets").mock(return_value=httpx.Response(200, json=[]))

    client = VetFlowClient(_settings())
    try:
        await asyncio.gather(*(client.get("/pets") for _ in range(5)))
    finally:
        await client.aclose()

    assert login.call_count == 1



# --------------------------------------------------------------------------- #
# Modo pass-through (JWT pré-fornecido, ex. do usuário logado)
# --------------------------------------------------------------------------- #


def _settings_token() -> Settings:
    return Settings(base_url=BASE, email="", password="", token="jwt-user", timeout_s=5)


@respx.mock
async def test_passthrough_usa_token_sem_login() -> None:
    login = respx.post(f"{BASE}/auth/login").mock(
        return_value=httpx.Response(200, json={"token": "NAO-DEVE-SER-USADO"})
    )
    pets = respx.get(f"{BASE}/pets").mock(
        return_value=httpx.Response(200, json=[{"id": "p1"}])
    )

    client = VetFlowClient(_settings_token())
    try:
        result = await client.get("/pets")
    finally:
        await client.aclose()

    assert result == [{"id": "p1"}]
    assert not login.called  # pass-through NÃO faz login
    assert pets.calls.last.request.headers["Authorization"] == "Bearer jwt-user"


@respx.mock
async def test_passthrough_401_nao_reautentica_e_erro_claro() -> None:
    login = respx.post(f"{BASE}/auth/login").mock(
        return_value=httpx.Response(200, json={"token": "x"})
    )
    respx.get(f"{BASE}/pets").mock(return_value=httpx.Response(401, json={}))

    client = VetFlowClient(_settings_token())
    try:
        with pytest.raises(VetFlowError) as exc:
            await client.get("/pets")
    finally:
        await client.aclose()

    assert "expirada" in str(exc.value).lower()
    assert not login.called  # não tenta re-logar com credenciais que não tem


def test_config_aceita_token_sem_email_senha(monkeypatch) -> None:
    monkeypatch.setenv("VETFLOW_BASE_URL", BASE)
    monkeypatch.setenv("VETFLOW_TOKEN", "jwt-user")
    monkeypatch.delenv("VETFLOW_EMAIL", raising=False)
    monkeypatch.delenv("VETFLOW_PASSWORD", raising=False)

    s = Settings.from_env()
    assert s.token == "jwt-user"
    assert s.base_url == BASE


def test_config_token_override_dispensa_email_senha(monkeypatch) -> None:
    """Regressão: o pass-through in-process passa o JWT como token_override.

    O ambiente do deploy NÃO tem VETFLOW_TOKEN nem EMAIL/PASSWORD (auth é
    pass-through). from_env precisa aceitar o token do request via argumento,
    SEM exigir email/senha. Antes do fix, from_env() validava o ambiente vazio
    e levantava RuntimeError antes de o token do request ser injetado — o que
    quebrava o /chat logado com 'faltam VETFLOW_EMAIL, VETFLOW_PASSWORD'.
    """
    monkeypatch.setenv("VETFLOW_BASE_URL", BASE)
    monkeypatch.delenv("VETFLOW_TOKEN", raising=False)
    monkeypatch.delenv("VETFLOW_EMAIL", raising=False)
    monkeypatch.delenv("VETFLOW_PASSWORD", raising=False)

    s = Settings.from_env(token_override="jwt-do-usuario")
    assert s.token == "jwt-do-usuario"
    assert s.email == ""
    assert s.base_url == BASE


def test_config_sem_token_nenhum_ainda_exige_email_senha(monkeypatch) -> None:
    """O caminho sem token (modo login/Claude Desktop) segue exigindo credencial."""
    monkeypatch.setenv("VETFLOW_BASE_URL", BASE)
    monkeypatch.delenv("VETFLOW_TOKEN", raising=False)
    monkeypatch.delenv("VETFLOW_EMAIL", raising=False)
    monkeypatch.delenv("VETFLOW_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="VETFLOW_EMAIL"):
        Settings.from_env()


@pytest.mark.parametrize("valor,esperado", [("", 15.0), ("  ", 15.0), ("30", 30.0), ("2.5", 2.5)])
def test_config_timeout_vazio_usa_default(monkeypatch, valor, esperado) -> None:
    monkeypatch.setenv("VETFLOW_BASE_URL", BASE)
    monkeypatch.setenv("VETFLOW_TOKEN", "jwt")
    monkeypatch.setenv("VETFLOW_TIMEOUT_S", valor)
    assert Settings.from_env().timeout_s == esperado


@pytest.mark.parametrize("valor", ["15s", "abc", "0", "-3", "nan", "inf"])
def test_config_timeout_invalido_da_erro_claro(monkeypatch, valor) -> None:
    """Antes: ValueError cru do float(). Agora: RuntimeError nomeando a variável."""
    monkeypatch.setenv("VETFLOW_BASE_URL", BASE)
    monkeypatch.setenv("VETFLOW_TOKEN", "jwt")
    monkeypatch.setenv("VETFLOW_TIMEOUT_S", valor)
    with pytest.raises(RuntimeError, match="VETFLOW_TIMEOUT_S"):
        Settings.from_env()
