"""Tools de ESCRITA (fase 2) — registradas condicionalmente.

Escrita num MCP é poderosa e arriscada: uma LLM conectada poderia criar ou
alterar dados de produção. Por isso estas tools:

  * só são registradas quando VETFLOW_ENABLE_WRITES=true (default: desligado);
  * cobrem apenas CREATE e UPDATE dos recursos de valor operacional;
  * NÃO expõem DELETE — remoção é destrutiva e irreversível, deixada de fora
    de propósito (o endpoint existe no backend, mas não é cablado aqui).

Os endpoints POST/PUT do backend decodificam o próprio model e setam vet_id /
created_at no servidor a partir do token — então as tools enviam só os campos
de negócio; o escopo ao vet autenticado é garantido pelo backend.

`register_write_tools(mcp)` é chamado por server.py apenas quando o flag está
ligado, mantendo a superfície de leitura intocada no modo padrão.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # evita import circular em runtime
    from mcp.server.mcpserver import MCPServer

    from .client import VetFlowClient


def register_write_tools(
    mcp: MCPServer, get_client: Callable[[], VetFlowClient]
) -> None:
    """Registra as tools de escrita no servidor MCP já existente.

    Args:
        mcp: a instância MCPServer onde as tools serão anexadas.
        get_client: fábrica que devolve o VetFlowClient compartilhado.
    """

    # ----- Clientes --------------------------------------------------------- #

    @mcp.tool()
    async def criar_cliente(
        full_name: str,
        phone: str,
        cpf: str = "",
        street: str = "",
        number: str = "",
        complement: str = "",
        zip_code: str = "",
    ) -> dict[str, Any]:
        """Cadastra um novo tutor (cliente) na clínica.

        Args:
            full_name: nome completo (obrigatório).
            phone: telefone de contato (obrigatório).
            cpf: CPF, opcional.
            street, number, complement, zip_code: endereço, opcionais.

        Returns:
            O cliente criado (com id atribuído pelo backend).
        """
        payload = {
            "full_name": full_name,
            "phone": phone,
            "cpf": cpf,
            "address": {
                "street": street,
                "number": number,
                "complement": complement,
                "zip_code": zip_code,
            },
        }
        return await get_client().post("/clients", json=payload) or {}

    @mcp.tool()
    async def atualizar_cliente(
        cliente_id: str,
        full_name: str,
        phone: str,
        cpf: str = "",
        street: str = "",
        number: str = "",
        complement: str = "",
        zip_code: str = "",
    ) -> dict[str, Any]:
        """Atualiza os dados de um tutor existente (envie o estado completo).

        Args:
            cliente_id: id do cliente a atualizar.
            full_name, phone: obrigatórios.
            cpf, street, number, complement, zip_code: opcionais.
        """
        payload = {
            "full_name": full_name,
            "phone": phone,
            "cpf": cpf,
            "address": {
                "street": street,
                "number": number,
                "complement": complement,
                "zip_code": zip_code,
            },
        }
        return await get_client().put(f"/clients/{cliente_id}", json=payload) or {}

    # ----- Pets ------------------------------------------------------------- #

    @mcp.tool()
    async def criar_pet(
        owner_id: str,
        name: str,
        species: str,
        breed: str = "",
        age: int = 0,
        age_unit: str = "years",
        sex: str = "",
        neutered: bool = False,
        weight: float = 0.0,
    ) -> dict[str, Any]:
        """Cadastra um novo pet para um tutor existente.

        Args:
            owner_id: id do tutor dono (obrigatório; valide-o antes com buscar_cliente).
            name: nome do animal (obrigatório).
            species: espécie, ex. "dog"/"cat" (obrigatório).
            breed, sex: opcionais.
            age: idade; age_unit: "years" ou "months".
            neutered: castrado?
            weight: peso em kg.
        """
        payload = {
            "owner_id": owner_id,
            "name": name,
            "species": species,
            "breed": breed,
            "age": age,
            "age_unit": age_unit,
            "sex": sex,
            "neutered": neutered,
            "weight": weight,
        }
        return await get_client().post("/pets", json=payload) or {}

    @mcp.tool()
    async def atualizar_pet(
        pet_id: str,
        owner_id: str,
        name: str,
        species: str,
        breed: str = "",
        age: int = 0,
        age_unit: str = "years",
        sex: str = "",
        neutered: bool = False,
        weight: float = 0.0,
    ) -> dict[str, Any]:
        """Atualiza um pet existente (envie o estado completo).

        Args:
            pet_id: id do pet a atualizar.
            owner_id, name, species: obrigatórios.
            demais campos: opcionais (ver criar_pet).
        """
        payload = {
            "owner_id": owner_id,
            "name": name,
            "species": species,
            "breed": breed,
            "age": age,
            "age_unit": age_unit,
            "sex": sex,
            "neutered": neutered,
            "weight": weight,
        }
        return await get_client().put(f"/pets/{pet_id}", json=payload) or {}

    # ----- Agenda ----------------------------------------------------------- #

    @mcp.tool()
    async def agendar_compromisso(
        pet_id: str,
        title: str,
        start_time: str,
        end_time: str,
        tipo: str = "consultation",
        client_id: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        """Cria um compromisso na agenda.

        Args:
            pet_id: id do pet (obrigatório).
            title: título do compromisso (obrigatório).
            start_time, end_time: ISO 8601, ex. "2026-10-12T14:00:00Z".
            tipo: um de consultation|vaccine|return|surgery|exam|grooming|other.
            client_id: id do tutor (opcional).
            notes: observações (opcional).
        """
        payload = {
            "pet_id": pet_id,
            "client_id": client_id,
            "title": title,
            "type": tipo,
            "status": "scheduled",
            "start_time": start_time,
            "end_time": end_time,
            "notes": notes,
        }
        return await get_client().post("/appointments", json=payload) or {}

    @mcp.tool()
    async def mudar_status_compromisso(appointment_id: str, status: str) -> dict[str, Any]:
        """Atualiza o status de um compromisso da agenda.

        Args:
            appointment_id: id do compromisso.
            status: scheduled|confirmed|completed|cancelled|no_show.
        """
        payload = {"status": status}
        return await get_client().put(f"/appointments/{appointment_id}", json=payload) or {}

    # ----- Consultas -------------------------------------------------------- #

    @mcp.tool()
    async def registrar_consulta(
        pet_id: str,
        main_complaint: str,
        date: str = "",
        evolution_time: str = "",
        pain: bool = False,
        notes_summary: str = "",
    ) -> dict[str, Any]:
        """Registra uma consulta clínica para um pet.

        O backend exige apenas `pet_id` e a queixa principal; os demais campos
        da ficha (dieta, fisiológico, resumo) são opcionais e podem ser
        preenchidos depois pela UI do VetFlow.

        Args:
            pet_id: id do pet (obrigatório).
            main_complaint: queixa principal / motivo da consulta (obrigatório).
            date: data da consulta em ISO 8601; vazio = agora no backend.
            evolution_time: há quanto tempo evolui o quadro (opcional).
            pain: o animal apresenta dor? (opcional).
            notes_summary: medicações em uso ou receitadas (opcional). Vai para
                o campo "Medicações" do resumo da ficha (`summary.medications`),
                que também pré-preenche o receituário. Não é um resumo livre da
                consulta: o backend não tem campo para isso.
        """
        payload: dict[str, Any] = {
            "pet_id": pet_id,
            "anamnesis": {
                "main_complaint": main_complaint,
                "evolution_time": evolution_time,
                "pain": pain,
            },
            "summary": {"evolution_time": evolution_time},
        }
        if date:
            payload["date"] = date
        if notes_summary:
            payload["summary"]["medications"] = notes_summary
        return await get_client().post("/consultations", json=payload) or {}

    # ----- Vacinas ---------------------------------------------------------- #

    @mcp.tool()
    async def registrar_vacina(
        pet_id: str,
        vaccine_name: str,
        date_applied: str = "",
        next_dose_date: str = "",
        batch: str = "",
        manufacturer: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        """Registra uma vacina aplicada a um pet.

        O backend exige apenas `pet_id` e `vaccine_name`.

        Args:
            pet_id: id do pet (obrigatório).
            vaccine_name: nome da vacina (obrigatório).
            date_applied: data da aplicação em ISO 8601 (opcional).
            next_dose_date: data da próxima dose em ISO 8601 (opcional) —
                é o que alimenta a tool de leitura `vacinas_a_vencer`.
            batch: lote (opcional).
            manufacturer: fabricante (opcional).
            notes: observações (opcional).
        """
        payload: dict[str, Any] = {
            "pet_id": pet_id,
            "vaccine_name": vaccine_name,
            "batch": batch,
            "manufacturer": manufacturer,
            "notes": notes,
        }
        if date_applied:
            payload["date_applied"] = date_applied
        if next_dose_date:
            payload["next_dose_date"] = next_dose_date
        return await get_client().post("/vaccines", json=payload) or {}
