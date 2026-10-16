"""Testes das tools de escrita (fase 2).

Registram as tools num MCPServer isolado, apontando para o backend fake, e
verificam o payload enviado a cada endpoint POST/PUT.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp.server.mcpserver import MCPServer

from vetflow_mcp.client import VetFlowClient
from vetflow_mcp.config import Settings
from vetflow_mcp.write_tools import register_write_tools

BASE = "http://backend.test"


def _sent(route: respx.Route) -> dict:
    """Corpo JSON da última requisição que a rota recebeu."""
    return json.loads(route.calls.last.request.content)


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
        "registrar_consulta",
        "registrar_vacina",
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
    sent = _sent(route)
    assert sent["owner_id"] == "c1"
    assert sent["name"] == "Rex"
    assert sent["species"] == "dog"
    assert sent["weight"] == 12.5
    # call_tool devolve (content, structured) — o id criado está no resultado.
    assert "p1" in str(result)


async def test_atualizar_pet_envia_put_com_o_mesmo_payload_do_criar(write_setup) -> None:
    mcp, _client, mock = write_setup
    criar = mock.post(f"{BASE}/pets").mock(return_value=httpx.Response(201, json={"id": "p1"}))
    atualizar = mock.put(f"{BASE}/pets/p1").mock(return_value=httpx.Response(200, json={}))
    campos = {"owner_id": "c1", "name": "Rex", "species": "dog", "age": 3, "neutered": True}

    await mcp.call_tool("criar_pet", campos)
    await mcp.call_tool("atualizar_pet", {"pet_id": "p1", **campos})

    assert _sent(atualizar) == _sent(criar)
    assert _sent(atualizar)["age_unit"] == "years"  # default preservado


async def test_registrar_consulta_payload_minimo(write_setup) -> None:
    mcp, _client, mock = write_setup
    route = mock.post(f"{BASE}/consultations").mock(
        return_value=httpx.Response(201, json={"id": "k1"})
    )
    await mcp.call_tool(
        "registrar_consulta",
        {"pet_id": "p1", "main_complaint": "vômito há 2 dias", "pain": True},
    )
    sent = _sent(route)
    assert sent["pet_id"] == "p1"
    assert sent["anamnesis"]["main_complaint"] == "vômito há 2 dias"
    assert sent["anamnesis"]["pain"] is True
    # Campos opcionais vazios não vão no payload.
    assert "date" not in sent
    assert "medications" not in sent["summary"]


async def test_registrar_consulta_com_data_e_resumo(write_setup) -> None:
    mcp, _client, mock = write_setup
    route = mock.post(f"{BASE}/consultations").mock(
        return_value=httpx.Response(201, json={"id": "k1"})
    )
    await mcp.call_tool(
        "registrar_consulta",
        {
            "pet_id": "p1",
            "main_complaint": "tosse",
            "date": "2026-10-12T14:00:00Z",
            "notes_summary": "xarope 5ml 2x ao dia",
        },
    )
    sent = _sent(route)
    assert sent["date"] == "2026-10-12T14:00:00Z"
    assert sent["summary"]["medications"] == "xarope 5ml 2x ao dia"


async def test_registrar_vacina_payload(write_setup) -> None:
    mcp, _client, mock = write_setup
    route = mock.post(f"{BASE}/vaccines").mock(
        return_value=httpx.Response(201, json={"id": "vac1"})
    )
    await mcp.call_tool(
        "registrar_vacina",
        {
            "pet_id": "p1",
            "vaccine_name": "V8",
            "next_dose_date": "2027-01-01T00:00:00Z",
            "batch": "L123",
        },
    )
    sent = _sent(route)
    assert sent["vaccine_name"] == "V8"
    assert sent["next_dose_date"] == "2027-01-01T00:00:00Z"
    assert sent["batch"] == "L123"
    # date_applied vazio não deve ir no payload
    assert "date_applied" not in sent


async def test_registrar_vacina_com_data_de_aplicacao(write_setup) -> None:
    mcp, _client, mock = write_setup
    route = mock.post(f"{BASE}/vaccines").mock(
        return_value=httpx.Response(201, json={"id": "vac1"})
    )
    await mcp.call_tool(
        "registrar_vacina",
        {"pet_id": "p1", "vaccine_name": "V8", "date_applied": "2026-10-01T00:00:00Z"},
    )
    sent = _sent(route)
    assert sent["date_applied"] == "2026-10-01T00:00:00Z"
    assert "next_dose_date" not in sent


async def test_criar_cliente_monta_address(write_setup) -> None:
    mcp, _client, mock = write_setup
    route = mock.post(f"{BASE}/clients").mock(
        return_value=httpx.Response(201, json={"id": "c1"})
    )
    await mcp.call_tool(
        "criar_cliente",
        {"full_name": "Ana", "phone": "99999", "street": "Rua X", "zip_code": "58000"},
    )
    sent = _sent(route)
    assert sent["full_name"] == "Ana"
    assert sent["address"]["street"] == "Rua X"
    assert sent["address"]["zip_code"] == "58000"


async def test_atualizar_cliente_envia_put_com_o_mesmo_payload_do_criar(write_setup) -> None:
    mcp, _client, mock = write_setup
    criar = mock.post(f"{BASE}/clients").mock(return_value=httpx.Response(201, json={"id": "c1"}))
    atualizar = mock.put(f"{BASE}/clients/c1").mock(return_value=httpx.Response(200, json={}))
    campos = {"full_name": "Ana", "phone": "99999", "cpf": "123", "number": "10"}

    await mcp.call_tool("criar_cliente", campos)
    await mcp.call_tool("atualizar_cliente", {"cliente_id": "c1", **campos})

    assert _sent(atualizar) == _sent(criar)
    assert _sent(atualizar)["address"]["number"] == "10"


async def test_agendar_compromisso_default_status_scheduled(write_setup) -> None:
    mcp, _client, mock = write_setup
    route = mock.post(f"{BASE}/appointments").mock(
        return_value=httpx.Response(201, json={"id": "a1"})
    )
    await mcp.call_tool(
        "agendar_compromisso",
        {
            "pet_id": "p1",
            "title": "Consulta Rex",
            "start_time": "2026-10-12T14:00:00Z",
            "end_time": "2026-10-12T14:30:00Z",
        },
    )
    sent = _sent(route)
    assert sent["status"] == "scheduled"
    assert sent["type"] == "consultation"


async def test_mudar_status_compromisso_envia_so_o_status(write_setup) -> None:
    mcp, _client, mock = write_setup
    route = mock.put(f"{BASE}/appointments/a1").mock(
        return_value=httpx.Response(200, json={"id": "a1", "status": "confirmed"})
    )
    await mcp.call_tool(
        "mudar_status_compromisso", {"appointment_id": "a1", "status": "confirmed"}
    )
    assert _sent(route) == {"status": "confirmed"}
