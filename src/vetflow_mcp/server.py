"""Servidor MCP do VetFlow.

Expõe o backend veterinário como ferramentas MCP que qualquer LLM compatível
(Claude Desktop, etc.) pode chamar em linguagem natural. Mantém-se FINO: cada
tool traduz a intenção em uma chamada HTTP autenticada (via VetFlowClient) e
formata a resposta — nenhuma regra de negócio é duplicada aqui.

As tools são de LEITURA (fase 1). Escrita (criar consulta, lançar fatura, etc.)
fica como fase 2 opcional, intencionalmente de fora para um MCP seguro de expor.

Execução (stdio, o transporte padrão para clientes desktop):
    uv run vetflow-mcp
    # ou:  python -m vetflow_mcp.server
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.mcpserver import MCPServer

from .client import VetFlowClient
from .config import Settings

# mcp 2.x: FastMCP foi renomeado para MCPServer. O decorator .tool() e o .run()
# (transporte stdio por padrão) permanecem iguais.
mcp = MCPServer("vetflow")

# Cliente único e preguiçoso: criado no primeiro uso, reaproveitado entre tools
# (reusa a conexão httpx e o cache de token).
_client: VetFlowClient | None = None


def _get_client() -> VetFlowClient:
    global _client
    if _client is None:
        _client = VetFlowClient(Settings.from_env())
    return _client


def _contains(haystack: Any, needle: str) -> bool:
    """Match case-insensitive de substring, tolerante a None."""
    return needle.strip().lower() in str(haystack or "").lower()


# --------------------------------------------------------------------------- #
# Clientes / Pets                                                             #
# --------------------------------------------------------------------------- #


@mcp.tool()
async def buscar_cliente(nome: str) -> list[dict[str, Any]]:
    """Busca tutores (clientes) cujo nome contém o texto informado.

    O backend lista por GET /clients sem busca por nome, então a filtragem é
    feita aqui, no lado do MCP (o backend só filtra por id).

    Args:
        nome: trecho do nome a procurar (case-insensitive). Vazio = todos.

    Returns:
        Lista de clientes que casam com o trecho (pode ser vazia).
    """
    clientes = await _get_client().get("/clients") or []
    if not nome.strip():
        return clientes
    return [c for c in clientes if _contains(c.get("name"), nome)]


@mcp.tool()
async def listar_pets_do_cliente(cliente_id: str) -> list[dict[str, Any]]:
    """Lista os pets de um tutor específico.

    Args:
        cliente_id: id do cliente (obtido via `buscar_cliente`).
    """
    return await _get_client().get("/pets", params={"owner_id": cliente_id}) or []


@mcp.tool()
async def buscar_pet(nome: str) -> list[dict[str, Any]]:
    """Busca pets por nome (filtragem feita no MCP).

    Args:
        nome: trecho do nome do animal (case-insensitive).
    """
    pets = await _get_client().get("/pets") or []
    if not nome.strip():
        return pets
    return [p for p in pets if _contains(p.get("name"), nome)]


@mcp.tool()
async def ficha_do_pet(pet_id: str) -> dict[str, Any]:
    """Retorna a ficha completa de um pet (espécie, raça, idade, peso, etc.).

    Args:
        pet_id: id do pet.
    """
    return await _get_client().get(f"/pets/{pet_id}") or {}


# --------------------------------------------------------------------------- #
# Histórico clínico                                                           #
# --------------------------------------------------------------------------- #


@mcp.tool()
async def historico_consultas(pet_id: str) -> list[dict[str, Any]]:
    """Lista as consultas de um pet, da mais recente para a mais antiga.

    Args:
        pet_id: id do pet.
    """
    consultas = await _get_client().get("/consultations", params={"pet_id": pet_id}) or []
    return sorted(consultas, key=lambda c: str(c.get("date", "")), reverse=True)


@mcp.tool()
async def historico_vacinas(pet_id: str) -> list[dict[str, Any]]:
    """Lista as vacinas aplicadas a um pet (nome, lote, data e próxima dose).

    Args:
        pet_id: id do pet.
    """
    vacinas = await _get_client().get("/vaccines", params={"pet_id": pet_id}) or []
    return sorted(vacinas, key=lambda v: str(v.get("date_applied", "")), reverse=True)


@mcp.tool()
async def vacinas_a_vencer(dias: int = 30) -> list[dict[str, Any]]:
    """Vacinas cuja próxima dose vence dentro da janela informada.

    Varre as vacinas da clínica e filtra por `next_dose_date` entre agora e
    agora+`dias`. Útil para a LLM alertar proativamente o vet.

    Args:
        dias: tamanho da janela em dias à frente (padrão 30).
    """
    vacinas = await _get_client().get("/vaccines") or []
    agora = datetime.now(timezone.utc)
    limite = agora + timedelta(days=dias)
    a_vencer: list[dict[str, Any]] = []
    for v in vacinas:
        raw = v.get("next_dose_date")
        if not raw:
            continue
        try:
            proxima = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if agora <= proxima <= limite:
            a_vencer.append(v)
    return sorted(a_vencer, key=lambda v: str(v.get("next_dose_date", "")))


# --------------------------------------------------------------------------- #
# Agenda / Financeiro / Dashboard                                             #
# --------------------------------------------------------------------------- #


@mcp.tool()
async def agenda_do_dia(data: str | None = None) -> list[dict[str, Any]]:
    """Compromissos de um dia específico (padrão: hoje), ordenados por horário.

    Usa a janela [início do dia, início do dia seguinte) via os query params
    `start`/`end` que o endpoint de agenda aceita.

    Args:
        data: dia no formato ISO 'YYYY-MM-DD'. Omitido = hoje (UTC).
    """
    dia = datetime.fromisoformat(data) if data else datetime.now(timezone.utc)
    inicio = datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc)
    fim = inicio + timedelta(days=1)
    ags = await _get_client().get(
        "/appointments",
        params={"start": inicio.isoformat(), "end": fim.isoformat()},
    ) or []
    return sorted(ags, key=lambda a: str(a.get("start_time", "")))


@mcp.tool()
async def faturas_pendentes() -> list[dict[str, Any]]:
    """Faturas ainda não quitadas (status pending, partial ou overdue).

    O endpoint não filtra por status, então a seleção é feita no MCP.
    """
    faturas = await _get_client().get("/invoices") or []
    abertas = {"pending", "partial", "overdue"}
    return [f for f in faturas if str(f.get("status", "")).lower() in abertas]


@mcp.tool()
async def metricas_dashboard() -> dict[str, int]:
    """Resumo operacional da clínica (os 6 contadores do dashboard).

    Campos: total_clients, total_pets, consultations_this_month,
    upcoming_vaccinations, pending_invoices, appointments_today.
    """
    return await _get_client().get("/dashboard/stats") or {}


# --------------------------------------------------------------------------- #
# Entry point                                                                 #
# --------------------------------------------------------------------------- #


def main() -> None:
    """Ponto de entrada do console script `vetflow-mcp`. Roda via stdio."""
    try:
        settings = Settings.from_env()  # falha cedo e claro se faltar credencial
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    # Fase 2: tools de escrita só são registradas sob flag explícito. No modo
    # padrão, apenas a superfície de leitura acima fica exposta.
    if settings.enable_writes:
        from .write_tools import register_write_tools

        register_write_tools(mcp, _get_client)

    mcp.run()  # transporte stdio por padrão


if __name__ == "__main__":
    main()
