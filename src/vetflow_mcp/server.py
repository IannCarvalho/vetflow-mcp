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

from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.mcpserver import MCPServer

from .client import VetFlowClient
from .config import Settings

# mcp 2.x: FastMCP foi renomeado para MCPServer. O decorator .tool() e o .run()
# (transporte stdio por padrão) permanecem iguais.
mcp = MCPServer("vetflow")

# --------------------------------------------------------------------------- #
# Resolução do cliente: por REQUEST (contextvars), não por processo.          #
#                                                                             #
# Multi-tenancy é uma propriedade da INTERFACE, não um efeito de rodar em     #
# processos separados. `_get_client()` resolve, nesta ordem:                  #
#   1. o cliente do CONTEXTO atual (setado por `use_request_client`) — é o    #
#      caminho por-request: cada chamada ao MCP in-process pode escopar o seu #
#      próprio token sem vazar para outra. Dois usuários concorrentes no mesmo #
#      processo ficam isolados.                                               #
#   2. o cliente de MÓDULO (`_client`) — usado pelo modo subprocesso stdio    #
#      (1 processo por request, Claude Desktop) e como ponto de injeção dos   #
#      testes. Preguiçoso: criado do ambiente no primeiro uso.                #
#                                                                             #
# No modo subprocesso nada seta o contexto, então o comportamento é idêntico  #
# ao do singleton anterior. O in-process ganha isolamento sem reescrever as   #
# tools — todas continuam chamando `_get_client()`.                           #
# --------------------------------------------------------------------------- #

# Cliente da request atual (None fora de um `use_request_client`).
_client_var: ContextVar[VetFlowClient | None] = ContextVar("vetflow_client", default=None)

# Cliente de módulo (modo subprocesso / override de teste). Preguiçoso.
_client: VetFlowClient | None = None


def _get_client() -> VetFlowClient:
    """Resolve o VetFlowClient: contexto da request primeiro, senão o de módulo.

    Interface preservada: as 11 tools (e o `write_tools`) continuam chamando
    isto sem mudança.
    """
    scoped = _client_var.get()
    if scoped is not None:
        return scoped
    global _client
    if _client is None:
        _client = VetFlowClient(Settings.from_env())
    return _client


@asynccontextmanager
async def use_request_client(token: str | None = None):
    """Escopa um VetFlowClient à request atual (caminho in-process por-usuário).

    Cria um cliente a partir do ambiente, sobrescrevendo o token com `token`
    quando fornecido (o JWT do usuário logado, pass-through), e o publica no
    `ContextVar` enquanto o bloco estiver ativo. As tools chamadas dentro do
    bloco resolvem ESTE cliente, isolado de outras requests no mesmo processo.
    Ao sair, fecha o cliente e restaura o contexto anterior.

    Uso (futuro mcp_bridge in-process):
        async with use_request_client(token=user_jwt):
            await server.buscar_cliente("ana")
    """
    base = Settings.from_env()
    settings = base if not (token or "").strip() else replace(base, token=token.strip())
    client = VetFlowClient(settings)
    reset_token = _client_var.set(client)
    try:
        yield client
    finally:
        _client_var.reset(reset_token)
        await client.aclose()


# --------------------------------------------------------------------------- #
# Predicados de filtragem client-side (o SEAM "filtro aqui no MCP").          #
#                                                                             #
# Quatro tools de leitura compartilham um mesmo PORQUÊ: o backend lista sem   #
# o filtro que a LLM precisa (busca por nome, status aberto, janela de data), #
# então o filtro mora AQUI. Esse conceito aparecia 4x sem nome. Em vez de um  #
# QueryBuilder genérico (YAGNI — são 4 usos reais, não um framework), damos   #
# NOME aos predicados: a locality do "porquê filtramos no MCP" fica num lugar #
# só, e cada predicado é testável sem subir o servidor. As tools passam a     #
# COMPOR estes predicados em vez de reimplementar o laço.                     #
# --------------------------------------------------------------------------- #

# Status de fatura considerados "em aberto" (não quitados).
_OPEN_INVOICE_STATUSES = frozenset({"pending", "partial", "overdue"})


def matches_name(item: dict[str, Any], needle: str, *fields: str) -> bool:
    """O item casa com `needle` em algum dos `fields` (match de substring).

    Case-insensitive e tolerante a None. `needle` vazio/branco casa com tudo
    (o chamador decide se nem filtra). Generaliza o antigo `_contains`: as
    entidades do backend divergem no campo do nome (Client usa `full_name`,
    as demais usam `name`), então o chamador passa os campos candidatos e o
    primeiro que casar basta.

    Args:
        item: dict da entidade (cliente, pet, ...).
        needle: trecho a procurar.
        *fields: nomes de campo a inspecionar, em ordem de preferência.
    """
    alvo = needle.strip().lower()
    if not alvo:
        return True
    return any(alvo in str(item.get(f) or "").lower() for f in fields)


def within_window(
    item: dict[str, Any], field: str, start: datetime, end: datetime
) -> bool:
    """O campo de data `field` do item cai no intervalo fechado [start, end].

    O valor é um timestamp ISO (o sufixo 'Z' é aceito). Ausente ou inválido =>
    False (o item não entra na janela), espelhando o comportamento anterior de
    `vacinas_a_vencer`.

    Args:
        item: dict da entidade.
        field: nome do campo de data a ler.
        start: início da janela (inclusive).
        end: fim da janela (inclusive).
    """
    raw = item.get(field)
    if not raw:
        return False
    try:
        quando = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    return start <= quando <= end


def is_open_invoice(item: dict[str, Any]) -> bool:
    """A fatura está em aberto (status pending, partial ou overdue).

    Case-insensitive; status ausente/desconhecido => False.
    """
    return str(item.get("status", "")).lower() in _OPEN_INVOICE_STATUSES


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
    # O model Client do backend usa `full_name`; as demais entidades usam `name`.
    # `matches_name` tolera o needle vazio (devolve tudo), então não precisamos
    # mais do short-circuit manual.
    return [c for c in clientes if matches_name(c, nome, "full_name", "name")]


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
    return [p for p in pets if matches_name(p, nome, "name")]


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
    a_vencer = [v for v in vacinas if within_window(v, "next_dose_date", agora, limite)]
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
    return [f for f in faturas if is_open_invoice(f)]


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
