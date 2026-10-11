"""Smoke test end-to-end: dirige as tools de leitura do MCP contra o backend
local vivo (lê .env). Não é parte da suíte — é um script de verificação manual."""

import asyncio

from vetflow_mcp import server


async def main() -> None:
    print("== metricas_dashboard ==")
    print(await server.metricas_dashboard())

    print("\n== buscar_cliente('') (todos) ==")
    clientes = await server.buscar_cliente("")
    print(f"{len(clientes)} cliente(s)")
    for c in clientes[:3]:
        print(" -", c.get("full_name"), "| id:", c.get("id"))

    if clientes:
        cid = clientes[0].get("id")
        print(f"\n== listar_pets_do_cliente({cid}) ==")
        pets = await server.listar_pets_do_cliente(cid)
        print(f"{len(pets)} pet(s)")
        for p in pets[:3]:
            print(" -", p.get("name"), "|", p.get("species"), "| id:", p.get("id"))

    await server._get_client().aclose()


if __name__ == "__main__":
    asyncio.run(main())
