"""Configuração do servidor, lida de variáveis de ambiente (VETFLOW_*).

Em desenvolvimento, um arquivo .env na raiz do projeto é carregado
automaticamente. Em produção, defina as variáveis no ambiente do processo
(ou no config do cliente MCP, ex.: Claude Desktop).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # no-op se não houver .env


@dataclass(frozen=True)
class Settings:
    """Parâmetros de conexão com o backend VetFlow.

    Attributes:
        base_url: URL raiz da API Go, ex. "http://localhost:8080".
        email: credencial usada no POST /auth/login.
        password: senha correspondente.
        timeout_s: timeout por requisição HTTP, em segundos.
        enable_writes: habilita as tools de escrita (fase 2). Default: False.
    """

    base_url: str
    email: str
    password: str
    timeout_s: float = 15.0
    enable_writes: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        """Monta Settings a partir do ambiente, validando o que é obrigatório."""
        base_url = os.environ.get("VETFLOW_BASE_URL", "").rstrip("/")
        email = os.environ.get("VETFLOW_EMAIL", "")
        password = os.environ.get("VETFLOW_PASSWORD", "")

        missing = [
            name
            for name, value in (
                ("VETFLOW_BASE_URL", base_url),
                ("VETFLOW_EMAIL", email),
                ("VETFLOW_PASSWORD", password),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Variáveis de ambiente obrigatórias ausentes: "
                + ", ".join(missing)
                + ". Defina-as no ambiente ou em um arquivo .env."
            )

        timeout_s = float(os.environ.get("VETFLOW_TIMEOUT_S", "15"))
        enable_writes = os.environ.get("VETFLOW_ENABLE_WRITES", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        return cls(
            base_url=base_url,
            email=email,
            password=password,
            timeout_s=timeout_s,
            enable_writes=enable_writes,
        )
