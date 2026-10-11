# vetflow-mcp

Servidor **MCP (Model Context Protocol)** que expõe o backend do
[VetFlow](../vetflow-backend) — um SaaS de gestão veterinária — como ferramentas
que qualquer LLM compatível (Claude Desktop, etc.) pode chamar em linguagem
natural: *"quantas consultas tenho hoje?"*, *"as vacinas do Rex estão em dia?"*,
*"me dá o resumo da clínica"*.

## Arquitetura — "por fora"

O MCP é um processo **separado** do backend. Ele conversa com o VetFlow pela
mesma API HTTP que o frontend usa — não acessa o banco direto, não é compilado
dentro do binário Go. Isso mantém o produto intocado e torna o MCP um projeto
autocontido.

```
┌─────────────────┐   protocolo MCP (stdio)   ┌──────────────┐   HTTP + Bearer   ┌────────────────┐
│ Claude Desktop  │ ────────────────────────► │  vetflow-mcp │ ────────────────► │ VetFlow backend│
│   (ou outro)    │                           │   (Python)   │                   │   (Go + chi)   │
└─────────────────┘                           └──────────────┘                   └───────┬────────┘
                                                                                          │
                                                                                     ┌────▼────┐
                                                                                     │ MongoDB │
                                                                                     └─────────┘
```

Autenticação: o MCP faz `POST /auth/login` uma vez, guarda o JWT e o injeta como
`Authorization: Bearer <token>` em cada chamada. O backend escopa todos os dados
ao vet dono do token — o MCP não gerencia multi-tenancy.

## Tools expostas

Fase 1 — **só-leitura** (10 tools, todas em `server.py`):

| Tool | Endpoint | Pergunta que responde |
|---|---|---|
| `buscar_cliente(nome)` | `GET /clients` (filtra no MCP) | "qual o telefone do dono do Rex?" |
| `listar_pets_do_cliente(cliente_id)` | `GET /pets?owner_id=` | "quantos pets a Ana tem?" |
| `buscar_pet(nome)` | `GET /pets` (filtra no MCP) | "acha o pet chamado Rex" |
| `ficha_do_pet(pet_id)` | `GET /pets/{id}` | espécie, raça, idade, peso |
| `historico_consultas(pet_id)` | `GET /consultations?pet_id=` | "última consulta do Rex?" |
| `historico_vacinas(pet_id)` | `GET /vaccines?pet_id=` | "vacinas do Rex" |
| `vacinas_a_vencer(dias=30)` | `GET /vaccines` (filtra janela no MCP) | alerta proativo |
| `agenda_do_dia(data=hoje)` | `GET /appointments?start=&end=` | "o que tenho hoje?" |
| `faturas_pendentes()` | `GET /invoices` (filtra status no MCP) | financeiro em aberto |
| `metricas_dashboard()` | `GET /dashboard/stats` | "resumo da clínica" |

Escrita (criar consulta, lançar fatura) é fase 2 opcional, deixada de fora de
propósito para um MCP seguro de expor.

## Setup

```bash
# requer Python >= 3.10
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux/macOS:  source .venv/bin/activate

pip install -e ".[dev]"

cp .env.example .env   # preencha VETFLOW_BASE_URL / EMAIL / PASSWORD

pytest                 # testes (backend mockado, não precisa do VetFlow no ar)
python -m vetflow_mcp.server   # sobe o servidor via stdio
```

## Ligar no Claude Desktop

No `claude_desktop_config.json` (aponte para o python do venv):

```json
{
  "mcpServers": {
    "vetflow": {
      "command": "C:/caminho/para/vetflow-mcp/.venv/Scripts/python.exe",
      "args": ["-m", "vetflow_mcp.server"],
      "env": {
        "VETFLOW_BASE_URL": "http://localhost:8080",
        "VETFLOW_EMAIL": "vet@exemplo.com",
        "VETFLOW_PASSWORD": "..."
      }
    }
  }
}
```

## Estrutura

```
vetflow-mcp/
├── pyproject.toml
├── .env.example
├── src/vetflow_mcp/
│   ├── config.py     # lê VETFLOW_* do ambiente
│   ├── client.py     # HTTP autenticado: login, cache de token, refresh em 401
│   └── server.py     # FastMCP + as tools
└── tests/
    └── test_client.py
```
