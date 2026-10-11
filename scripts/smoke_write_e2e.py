"""Smoke test de ESCRITA end-to-end contra o backend local vivo.

ForÃ§a VETFLOW_ENABLE_WRITES antes de importar, registra as write tools e
exercita criar_cliente -> criar_pet -> registrar_vacina, confirmando por leitura.
Script de verificaÃ§Ã£o manual, nÃ£o faz parte da suÃ­te.
"""

import asyncio
import os

os.environ["VETFLOW_ENABLE_WRITES"] = "true"

from vetflow_mcp import server  # noqa: E402
from vetflow_mcp.config import Settings  # noqa: E402
from vetflow_mcp.write_tools import register_write_tools  # noqa: E402


async def main() -> None:
    assert Settings.from_env().enable_writes is True
    register_write_tools(server.mcp, server._get_client)

    nomes = {t.name for t in await server.mcp.list_tools()}
    print("tools totais:", len(nomes), "| escrita presente:", "criar_cliente" in nomes)

    print("\n== criar_cliente ==")
    cliente = await server.mcp.call_tool(
        "criar_cliente",
        {"full_name": "Teste MCP E2E", "phone": "83999990000"},
    )
    cliente_obj = getattr(cliente, "structured_content", None) or {}
    cid = (cliente_obj or {}).get("id")
    print("criado cliente id:", cid)

    print("\n== criar_pet ==")
    pet = await server.mcp.call_tool(
        "criar_pet",
        {"owner_id": cid, "name": "RexE2E", "species": "dog", "weight": 10.0},
    )
    pet_obj = getattr(pet, "structured_content", None) or {}
    pid = (pet_obj or {}).get("id")
    print("criado pet id:", pid)

    print("\n== registrar_vacina ==")
    vac = await server.mcp.call_tool(
        "registrar_vacina",
        {"pet_id": pid, "vaccine_name": "V8 E2E", "batch": "LOTE-E2E"},
    )
    vac_obj = getattr(vac, "structured_content", None) or {}
    print("criada vacina id:", (vac_obj or {}).get("id"))

    print("\n== verificacao por leitura ==")
    achados = await server.buscar_cliente("Teste MCP E2E")
    print("buscar_cliente achou:", [c.get("full_name") for c in achados])
    pets = await server.listar_pets_do_cliente(cid)
    print("pets do cliente:", [p.get("name") for p in pets])
    vacinas = await server.historico_vacinas(pid)
    print("vacinas do pet:", [v.get("vaccine_name") for v in vacinas])

    await server._get_client().aclose()


if __name__ == "__main__":
    asyncio.run(main())

