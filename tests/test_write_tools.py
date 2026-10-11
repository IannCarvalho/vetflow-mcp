"""Testes das tools de escrita (fase 2).

Registram as tools num MCPServer isolado, apontando para o backend fake, e
verificam o payload enviado a cada endpoint POST/PUT.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from mcp.server.mcpserver import MCPServer

from vetflow_mcp.client import VetFlowClient
from vetflow_mcp.config import Settings
from vetflow_mcp.write_tools import register_write_tools

BASE = "http://backend.test"


def test_enable_writes_lido_do_ambiente(monkeypatch) -> None:
    monkeypatch.setenv("VETFLOW_BASE_URL", BASE)
    monkeypatch.setenv("VETFLOW_EMAIL", "v@t")
    monkeypatch.setenv("VETFLOW_PASSWORD", "pw")
    monkeypatch.delenv("VETFLOW_ENABLE_WRITES", raising=False)
    assert Settings.from_env().enable_writes is False

    monkeypatch.setenv("VETFLOW_ENABLE_WRITES", "true")
    assert Settings.from_env().enable_writes is True


@pytest.fixture
def write_setup():
    """MCPServer isolado + VetFlowClient contra o backend fake."""
    with respx.mock:
        respx.post(f"{BASE}/auth/login").mock(
            return_value=httpx.Response(200, json={"token": "jwt"})
        )
        client = VetFlowClient(Settings(base_url=BASE, email="v@t", password="pw", timeout_s=5))
        mcp = MCPServer("vetflow-test")
        register_write_tools(mcp, lambda: client)
        yield mcp, client, respx


async def test_write_tools_registradas(write_setup) -> None:
    mcp, _client, _ = write_setup
    nomes = {t.name for t in await mcp.list_tools()}
    assert {
        "criar_cliente",
        "atualizar_cliente",
        "criar_pet",
        "atualizar_pet",
        "agendar_compromisso",
        "mudar_status_compromisso",
    } <= nomes


async def test_criar_pet_monta_payload(write_setup) -> None:
    mcp, _client, mock = write_setup
    route = mock.post(f"{BASE}/pets").mock(
        return_value=httpx.Response(201, json={"id": "p1", "name": "Rex"})
    )
    result = await mcp.call_tool(
        "criar_pet",
        {"owner_id": "c1", "name": "Rex", "species": "dog", "weight": 12.5},
    )
    assert route.called
    import json as _json

    sent = _json.loads(route.calls.last.request.content)
    assert sent["owner_id"] == "c1"
    assert sent["name"] == "Rex"
    assert sent["species"] == "dog"
    assert sent["weight"] == 12.5
    # call_tool devolve (content, structured) — o id criado está no resultado.
    assert "p1" in str(result)
