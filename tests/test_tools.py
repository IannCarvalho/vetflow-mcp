"""Testes das tools MCP, com o backend mockado (respx) e o cliente global resetado.

As tools usam um VetFlowClient global preguiçoso; cada teste injeta um cliente
apontando para o backend fake e o remove no fim.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from vetflow_mcp import server
from vetflow_mcp.client import VetFlowClient
from vetflow_mcp.config import Settings

BASE = "http://backend.test"


@pytest.fixture
def fake_backend():
    """Monta um VetFlowClient contra o backend fake e o injeta em server._client."""
    with respx.mock:
        respx.post(f"{BASE}/auth/login").mock(
            return_value=httpx.Response(200, json={"token": "jwt"})
        )
        server._client = VetFlowClient(
            Settings(base_url=BASE, email="v@t", password="pw", timeout_s=5)
        )
        yield respx
        server._client = None


async def test_buscar_cliente_filtra_por_nome(fake_backend) -> None:
    fake_backend.get(f"{BASE}/clients").mock(
        return_value=httpx.Response(
            200, json=[{"id": "1", "name": "Ana Lima"}, {"id": "2", "name": "Bruno"}]
        )
    )
    assert await server.buscar_cliente("ana") == [{"id": "1", "name": "Ana Lima"}]


async def test_listar_pets_do_cliente_passa_owner_id(fake_backend) -> None:
    route = fake_backend.get(f"{BASE}/pets").mock(
        return_value=httpx.Response(200, json=[{"id": "p1", "name": "Rex"}])
    )
    result = await server.listar_pets_do_cliente("c1")
    assert result == [{"id": "p1", "name": "Rex"}]
    assert route.calls.last.request.url.params["owner_id"] == "c1"


async def test_metricas_dashboard(fake_backend) -> None:
    fake_backend.get(f"{BASE}/dashboard/stats").mock(
        return_value=httpx.Response(200, json={"total_pets": 7, "total_clients": 3})
    )
    assert await server.metricas_dashboard() == {"total_pets": 7, "total_clients": 3}


async def test_vacinas_a_vencer_filtra_janela(fake_backend) -> None:
    from datetime import datetime, timedelta, timezone

    agora = datetime.now(timezone.utc)
    dentro = (agora + timedelta(days=10)).isoformat()
    fora = (agora + timedelta(days=90)).isoformat()
    fake_backend.get(f"{BASE}/vaccines").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "v1", "vaccine_name": "V8", "next_dose_date": dentro},
                {"id": "v2", "vaccine_name": "Antirrábica", "next_dose_date": fora},
            ],
        )
    )
    result = await server.vacinas_a_vencer(dias=30)
    assert [v["id"] for v in result] == ["v1"]


async def test_faturas_pendentes_filtra_status(fake_backend) -> None:
    fake_backend.get(f"{BASE}/invoices").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "i1", "status": "pending"},
                {"id": "i2", "status": "paid"},
                {"id": "i3", "status": "overdue"},
            ],
        )
    )
    result = await server.faturas_pendentes()
    assert {f["id"] for f in result} == {"i1", "i3"}
