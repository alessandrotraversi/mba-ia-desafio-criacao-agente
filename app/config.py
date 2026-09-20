"""Configuração central de caminhos e parâmetros da aplicação."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent

# Dados de entrada do desafio: somente leitura, nunca são alterados em runtime.
DADOS_DIR = BASE_DIR / "dados"
APARTAMENTOS_JSON = DADOS_DIR / "apartamentos.json"
AREAS_JSON = DADOS_DIR / "areas.json"
RESERVAS_JSON = DADOS_DIR / "reservas.json"
VISITANTES_JSON = DADOS_DIR / "visitantes.json"
REGULAMENTO_MD = DADOS_DIR / "regulamento.md"

# Estado de runtime (reservas/visitantes/sessões). Fica fora de `dados/` para
# que os arquivos de entrada nunca sejam tocados, e fora do git.
DATA_DIR = Path(os.environ.get("AURORA_DATA_DIR", BASE_DIR / ".data"))
CONDOMINIO_DB = DATA_DIR / "condominio.db"
SESSIONS_DB = DATA_DIR / "sessions.db"

APP_NAME = "residencial_aurora"

GOOGLE_MODEL = os.environ.get("GOOGLE_MODEL", "gemini-flash-latest")


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def build_model(name: Optional[str] = None):
    """Constrói o modelo Gemini usado por um agente, com retries mais
    tolerantes que o padrão do ADK: a API do Google AI Studio devolve 503
    ("high demand") com alguma frequência, e sem isso um pico transitório do
    lado do Google vira um 500 desnecessário na nossa API."""
    from google.adk.models import Gemini
    from google.genai import types as genai_types

    return Gemini(
        model=name or GOOGLE_MODEL,
        retry_options=genai_types.HttpRetryOptions(
            attempts=6,
            initial_delay=1.0,
            max_delay=20.0,
            exp_base=2.0,
        ),
    )
