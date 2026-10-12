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
            200, json=[{"id": "1", "full_name": "Ana Lima"}, {"id": "2", "full_name": "Bruno"}]
        )
    )
    assert await server.buscar_cliente("ana") == [{"id": "1", "full_name": "Ana Lima"}]


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


async def test_buscar_cliente_usa_full_name(fake_backend) -> None:
    """Regressão (pego no teste e2e): o model Client usa full_name, não name."""
    fake_backend.get(f"{BASE}/clients").mock(
        return_value=httpx.Response(200, json=[{"id": "1", "full_name": "Teste E2E"}])
    )
    assert len(await server.buscar_cliente("teste")) == 1


async def test_buscar_cliente_sem_nome_retorna_todos(fake_backend) -> None:
    fake_backend.get(f"{BASE}/clients").mock(
        return_value=httpx.Response(200, json=[{"id": "1", "full_name": "Ana"}, {"id": "2", "full_name": "Bia"}])
    )
    assert len(await server.buscar_cliente("  ")) == 2


async def test_buscar_pet_filtra_por_nome(fake_backend) -> None:
    fake_backend.get(f"{BASE}/pets").mock(
        return_value=httpx.Response(
            200, json=[{"id": "p1", "name": "Rex"}, {"id": "p2", "name": "Mia"}]
        )
    )
    assert await server.buscar_pet("rex") == [{"id": "p1", "name": "Rex"}]
    # nome vazio devolve todos
    assert len(await server.buscar_pet("")) == 2


async def test_ficha_do_pet(fake_backend) -> None:
    fake_backend.get(f"{BASE}/pets/p1").mock(
        return_value=httpx.Response(200, json={"id": "p1", "name": "Rex", "species": "dog"})
    )
    ficha = await server.ficha_do_pet("p1")
    assert ficha["species"] == "dog"


async def test_historico_consultas_ordena_desc(fake_backend) -> None:
    route = fake_backend.get(f"{BASE}/consultations").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "a", "date": "2026-01-01T00:00:00Z"},
                {"id": "b", "date": "2026-06-01T00:00:00Z"},
            ],
        )
    )
    result = await server.historico_consultas("p1")
    assert [c["id"] for c in result] == ["b", "a"]  # mais recente primeiro
    assert route.calls.last.request.url.params["pet_id"] == "p1"


async def test_historico_vacinas_ordena_desc(fake_backend) -> None:
    fake_backend.get(f"{BASE}/vaccines").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "v1", "date_applied": "2025-01-01T00:00:00Z"},
                {"id": "v2", "date_applied": "2026-01-01T00:00:00Z"},
            ],
        )
    )
    result = await server.historico_vacinas("p1")
    assert [v["id"] for v in result] == ["v2", "v1"]


async def test_vacinas_a_vencer_ignora_data_invalida(fake_backend) -> None:
    fake_backend.get(f"{BASE}/vaccines").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "v1", "next_dose_date": "data-podre"},
                {"id": "v2"},  # sem next_dose_date
            ],
        )
    )
    assert await server.vacinas_a_vencer() == []


async def test_agenda_do_dia_monta_janela(fake_backend) -> None:
    route = fake_backend.get(f"{BASE}/appointments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "a2", "start_time": "2026-10-12T15:00:00Z"},
                {"id": "a1", "start_time": "2026-10-12T09:00:00Z"},
            ],
        )
    )
    result = await server.agenda_do_dia("2026-10-12")
    assert [a["id"] for a in result] == ["a1", "a2"]  # ordenado por horário
    params = route.calls.last.request.url.params
    assert "2026-10-12" in params["start"]
    assert "2026-10-13" in params["end"]


def test_main_sem_credencial_sai_com_erro(monkeypatch) -> None:
    for var in ("VETFLOW_BASE_URL", "VETFLOW_EMAIL", "VETFLOW_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit):
        server.main()


def test_main_registra_writes_sob_flag(monkeypatch) -> None:
    """Com o flag ligado, main() registra as tools de escrita antes de rodar."""
    monkeypatch.setenv("VETFLOW_BASE_URL", BASE)
    monkeypatch.setenv("VETFLOW_EMAIL", "v@t")
    monkeypatch.setenv("VETFLOW_PASSWORD", "pw")
    monkeypatch.setenv("VETFLOW_ENABLE_WRITES", "true")
    # Não deixa o servidor realmente subir (mcp.run bloquearia).
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: None)
    server.main()
    # As tools de escrita devem agora existir no servidor global.
    import asyncio

    nomes = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert "registrar_consulta" in nomes
    assert "registrar_vacina" in nomes


# --------------------------------------------------------------------------- #
# Resolução do cliente por-request (contextvars) — Candidato 1 do refactor.   #
# --------------------------------------------------------------------------- #


async def test_use_request_client_tem_precedencia_sobre_o_global(monkeypatch) -> None:
    """Dentro de use_request_client, _get_client resolve o cliente do contexto."""
    monkeypatch.setenv("VETFLOW_BASE_URL", BASE)
    monkeypatch.setenv("VETFLOW_EMAIL", "v@t")
    monkeypatch.setenv("VETFLOW_PASSWORD", "pw")
    sentinela_global = object()
    server._client = sentinela_global  # type: ignore[assignment]
    try:
        with respx.mock:
            respx.post(f"{BASE}/auth/login").mock(
                return_value=httpx.Response(200, json={"token": "jwt"})
            )
            async with server.use_request_client(token="jwt-do-usuario") as scoped:
                # Dentro do contexto: ganha o cliente escopado, não o global.
                assert server._get_client() is scoped
                assert scoped is not sentinela_global
        # Fora do contexto: volta ao global (fallback / modo subprocesso).
        assert server._get_client() is sentinela_global
    finally:
        server._client = None


async def test_use_request_client_isola_requests_concorrentes(monkeypatch) -> None:
    """Dois contextos concorrentes resolvem clientes diferentes (sem vazamento)."""
    import asyncio

    monkeypatch.setenv("VETFLOW_BASE_URL", BASE)
    monkeypatch.setenv("VETFLOW_EMAIL", "v@t")
    monkeypatch.setenv("VETFLOW_PASSWORD", "pw")
    server._client = None

    async def capture(token: str) -> int:
        with respx.mock:
            respx.post(f"{BASE}/auth/login").mock(
                return_value=httpx.Response(200, json={"token": token})
            )
            async with server.use_request_client(token=token):
                await asyncio.sleep(0)  # cede o loop para intercalar as corrotinas
                return id(server._get_client())

    a, b = await asyncio.gather(capture("token-A"), capture("token-B"))
    assert a != b  # cada request viu o seu próprio cliente


async def test_use_request_client_sem_token_cai_no_env(monkeypatch) -> None:
    """Sem token explícito, usa o Settings.from_env (modo login/conta de serviço)."""
    monkeypatch.setenv("VETFLOW_BASE_URL", BASE)
    monkeypatch.setenv("VETFLOW_EMAIL", "v@t")
    monkeypatch.setenv("VETFLOW_PASSWORD", "pw")
    monkeypatch.delenv("VETFLOW_TOKEN", raising=False)
    server._client = None
    with respx.mock:
        respx.post(f"{BASE}/auth/login").mock(
            return_value=httpx.Response(200, json={"token": "jwt"})
        )
        async with server.use_request_client() as scoped:
            assert scoped is server._get_client()


# --------------------------------------------------------------------------- #
# Predicados de filtragem client-side — Candidato 3 do refactor.              #
# Puros: testáveis sem subir o MCP nem mockar o backend.                      #
# --------------------------------------------------------------------------- #


def test_matches_name_full_name_e_name() -> None:
    """Casa tanto em full_name (Client) quanto em name (demais entidades)."""
    assert server.matches_name({"full_name": "Ana Lima"}, "ana", "full_name", "name")
    assert server.matches_name({"name": "Rex"}, "rex", "full_name", "name")


def test_matches_name_case_insensitive() -> None:
    assert server.matches_name({"full_name": "Ana Lima"}, "LIMA", "full_name", "name")
    assert server.matches_name({"name": "REX"}, "rex", "name")


def test_matches_name_needle_vazio_casa_tudo() -> None:
    """Needle vazio/branco casa com qualquer item (o chamador decide filtrar)."""
    assert server.matches_name({"full_name": "Ana"}, "", "full_name", "name")
    assert server.matches_name({"full_name": "Ana"}, "   ", "full_name", "name")


def test_matches_name_sem_match_e_campo_ausente() -> None:
    assert not server.matches_name({"full_name": "Ana"}, "bruno", "full_name", "name")
    # Campo ausente/None não quebra (tolerante a None).
    assert not server.matches_name({"full_name": None}, "ana", "full_name", "name")
    assert not server.matches_name({}, "ana", "full_name", "name")


def test_within_window_dentro_e_fora() -> None:
    from datetime import datetime, timedelta, timezone

    agora = datetime.now(timezone.utc)
    fim = agora + timedelta(days=30)
    dentro = {"next_dose_date": (agora + timedelta(days=10)).isoformat()}
    fora = {"next_dose_date": (agora + timedelta(days=90)).isoformat()}
    passado = {"next_dose_date": (agora - timedelta(days=1)).isoformat()}
    assert server.within_window(dentro, "next_dose_date", agora, fim)
    assert not server.within_window(fora, "next_dose_date", agora, fim)
    assert not server.within_window(passado, "next_dose_date", agora, fim)


def test_within_window_data_invalida_ou_ausente() -> None:
    from datetime import datetime, timedelta, timezone

    agora = datetime.now(timezone.utc)
    fim = agora + timedelta(days=30)
    assert not server.within_window({"next_dose_date": "data-podre"}, "next_dose_date", agora, fim)
    assert not server.within_window({}, "next_dose_date", agora, fim)
    assert not server.within_window({"next_dose_date": None}, "next_dose_date", agora, fim)


def test_within_window_aceita_sufixo_z() -> None:
    from datetime import datetime, timezone

    inicio = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fim = datetime(2026, 12, 31, tzinfo=timezone.utc)
    item = {"next_dose_date": "2026-06-01T00:00:00Z"}
    assert server.within_window(item, "next_dose_date", inicio, fim)


def test_is_open_invoice() -> None:
    for status in ("pending", "partial", "overdue"):
        assert server.is_open_invoice({"status": status})
    # Case-insensitive.
    assert server.is_open_invoice({"status": "OVERDUE"})
    # Fechadas / desconhecidas / ausentes => False.
    assert not server.is_open_invoice({"status": "paid"})
    assert not server.is_open_invoice({"status": "cancelled"})
    assert not server.is_open_invoice({})
