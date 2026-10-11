"""Smoke test de ESCRITA end-to-end contra o backend local vivo.

Forca VETFLOW_ENABLE_WRITES antes de importar, registra as write tools e
exercita criar_cliente -> criar_pet -> registrar_vacina, confirmando por leitura.
Script de verificacao manual, nao faz parte da suite.

Uso: VETFLOW_ENABLE_WRITES e definido abaixo em runtime (antes de ler Settings),
entao basta `python scripts/smoke_write_e2e.py` com o .env preenchido.
"""

import asyncio
import os


async def main() -> None:
    os.environ["VETFLOW_ENABLE_WRITES"] = "true"

    from vetflow_mcp import server
    from vetflow_mcp.config import Settings
    from vetflow_mcp.write_tools import register_write_tools

    assert Settings.from_env().enable_writes is True
    register_write_tools(server.mcp, server._get_client)

    nomes = {t.name for t in await server.mcp.list_tools()}
    print("tools totais:", len(nomes), "| escrita presente:", "criar_cliente" in nomes)

    def structured(res: object) -> dict:
        return getattr(res, "structured_content", None) or {}

    print("\n== criar_cliente ==")
    cliente = await server.mcp.call_tool(
        "criar_cliente",
        {"full_name": "Teste MCP E2E", "phone": "83999990000"},
    )
    cid = structured(cliente).get("id")
    print("criado cliente id:", cid)

    print("\n== criar_pet ==")
    pet = await server.mcp.call_tool(
        "criar_pet",
        {"owner_id": cid, "name": "RexE2E", "species": "dog", "weight": 10.0},
    )
    pid = structured(pet).get("id")
    print("criado pet id:", pid)

    print("\n== registrar_vacina ==")
    vac = await server.mcp.call_tool(
        "registrar_vacina",
        {"pet_id": pid, "vaccine_name": "V8 E2E", "batch": "LOTE-E2E"},
    )
    print("criada vacina id:", structured(vac).get("id"))

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
