"""Testes do VetFlowClient com o backend mockado (respx).

Rodar:  uv run pytest   (ou: pytest)
"""

from __future__ import annotations

import httpx
import pytest
import respx

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
