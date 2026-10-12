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

    Autenticação por UM de dois modos:
      * pass-through: `token` já é um JWT válido (ex.: o do usuário logado,
        repassado pelo agente). O cliente usa-o direto, sem fazer login.
      * login: `email` + `password` -> POST /auth/login (modo Claude Desktop).

    Attributes:
        base_url: URL raiz da API Go, ex. "http://localhost:8080".
        token: JWT pré-fornecido (pass-through). Vazio = usar email/senha.
        email: credencial usada no POST /auth/login (se não houver token).
        password: senha correspondente.
        timeout_s: timeout por requisição HTTP, em segundos.
        enable_writes: habilita as tools de escrita (fase 2). Default: False.
    """

    base_url: str
    email: str
    password: str
    token: str = ""
    timeout_s: float = 15.0
    enable_writes: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        """Monta Settings a partir do ambiente, validando o que é obrigatório."""
        base_url = os.environ.get("VETFLOW_BASE_URL", "").rstrip("/")
        email = os.environ.get("VETFLOW_EMAIL", "")
        password = os.environ.get("VETFLOW_PASSWORD", "")
        token = os.environ.get("VETFLOW_TOKEN", "").strip()

        # base_url é sempre obrigatório. Para auth, aceita token (pass-through)
        # OU o par email+senha (login). Só exige email/senha quando não há token.
        missing = []
        if not base_url:
            missing.append("VETFLOW_BASE_URL")
        if not token:
            if not email:
                missing.append("VETFLOW_EMAIL")
            if not password:
                missing.append("VETFLOW_PASSWORD")
        if missing:
            raise RuntimeError(
                "Variáveis de ambiente obrigatórias ausentes: "
                + ", ".join(missing)
                + ". Defina VETFLOW_TOKEN (pass-through) ou VETFLOW_EMAIL+"
                "VETFLOW_PASSWORD, além de VETFLOW_BASE_URL."
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
            token=token,
            timeout_s=timeout_s,
            enable_writes=enable_writes,
        )
