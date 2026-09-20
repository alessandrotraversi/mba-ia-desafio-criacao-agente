"""Armazenamento do domínio do condomínio (reservas, visitantes, sessões).

Usa um arquivo SQLite próprio (fora de `dados/`, que é somente leitura e
nunca é alterado). A Garantia 5 (exclusividade de reserva) é implementada
aqui: uma reserva ativa por (área, data) é imposta por um índice único
parcial no banco, e a criação roda dentro de uma transação `BEGIN
IMMEDIATE`, de modo que a exclusividade valha no instante da gravação e não
apenas numa checagem prévia — mesmo com dois pedidos concorrentes vindo de
sessões (e threads) diferentes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from . import config

_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reservas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE,
    apartamento TEXT NOT NULL,
    area TEXT NOT NULL,
    data TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ativa'
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa_area_data
    ON reservas(area, data)
    WHERE status = 'ativa';

CREATE TABLE IF NOT EXISTS visitantes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL,
    nome TEXT NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessoes (
    session_id TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS codigo_seq (
    n INTEGER NOT NULL
);
"""

_CODIGO_INICIAL = 9000


def _connect() -> sqlite3.Connection:
    config.ensure_data_dir()
    conn = sqlite3.connect(
        str(config.CONDOMINIO_DB),
        timeout=30.0,
        isolation_level=None,  # autocommit; controlamos transações manualmente
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def get_conn() -> sqlite3.Connection:
    """Retorna uma conexão por thread (sqlite3 não é thread-safe entre threads)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


def _load_json(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _seeded(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key = 'seeded'").fetchone()
    return row is not None


def _seed(conn: sqlite3.Connection) -> None:
    reservas = _load_json(config.RESERVAS_JSON)
    visitantes = _load_json(config.VISITANTES_JSON)

    conn.execute("BEGIN IMMEDIATE")
    try:
        max_codigo_num = _CODIGO_INICIAL
        for r in reservas:
            conn.execute(
                "INSERT INTO reservas(codigo, apartamento, area, data, status)"
                " VALUES (?, ?, ?, ?, 'ativa')",
                (r["codigo"], r["apartamento"], r["area"], r["data"]),
            )
        for v in visitantes:
            conn.execute(
                "INSERT INTO visitantes(apartamento, nome, data) VALUES (?, ?, ?)",
                (v["apartamento"], v["nome"], v["data"]),
            )
        conn.execute("DELETE FROM codigo_seq")
        conn.execute("INSERT INTO codigo_seq(n) VALUES (?)", (max_codigo_num,))
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('seeded', '1')"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    """Cria o schema se necessário e popula com o estado inicial de `dados/`
    apenas na primeira execução (nunca sobrescreve dados já gravados)."""
    conn = get_conn()
    conn.executescript(_SCHEMA)
    if not _seeded(conn):
        _seed(conn)


def reset_db() -> None:
    """Restaura reservas e visitantes ao estado inicial de `dados/`.

    Usado apenas pelo comando de restauração (nunca chamado durante a
    operação normal da API), por isso pode ser destrutivo.
    """
    conn = get_conn()
    conn.executescript(_SCHEMA)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM reservas")
        conn.execute("DELETE FROM visitantes")
        conn.execute("DELETE FROM sessoes")
        conn.execute("DELETE FROM meta")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _seed(conn)


# ---------------------------------------------------------------------------
# Áreas comuns (referência estática, carregada uma vez em memória)
# ---------------------------------------------------------------------------

_AREAS: list[dict[str, Any]] = _load_json(config.AREAS_JSON)
_AREAS_POR_ID: dict[str, dict[str, Any]] = {a["id"]: a for a in _AREAS}


def listar_areas() -> list[dict[str, Any]]:
    return list(_AREAS)


def area_por_id(area_id: str) -> Optional[dict[str, Any]]:
    return _AREAS_POR_ID.get(area_id)


def taxa_da_area(area_id: str) -> Optional[float]:
    area = _AREAS_POR_ID.get(area_id)
    if area is None:
        return None
    return float(area["taxa"])


# ---------------------------------------------------------------------------
# Reservas
# ---------------------------------------------------------------------------


def disponibilidade(area_id: str, data: str) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'",
        (area_id, data),
    ).fetchone()
    return "ocupada" if row else "livre"


def criar_reserva(apartamento: str, area_id: str, data: str) -> tuple[bool, Optional[str]]:
    """Cria a reserva de forma atômica.

    A exclusividade (Garantia 5) é garantida pelo índice único parcial em
    (area, data) para reservas ativas: a checagem de disponibilidade e a
    gravação acontecem como uma única operação atômica do SQLite, dentro de
    uma transação `BEGIN IMMEDIATE`, e não como "verifica depois grava".
    Se duas reservas concorrentes disputarem a mesma área/data, a segunda
    falha aqui com IntegrityError e não é convertida em erro de servidor.
    """
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "UPDATE codigo_seq SET n = n + 1 RETURNING n"
        )
        numero = cur.fetchone()[0]
        codigo = f"RSV-{numero}"
        conn.execute(
            "INSERT INTO reservas(codigo, apartamento, area, data, status)"
            " VALUES (?, ?, ?, ?, 'ativa')",
            (codigo, apartamento, area_id, data),
        )
        conn.commit()
        return True, codigo
    except sqlite3.IntegrityError:
        conn.rollback()
        return False, None
    except Exception:
        conn.rollback()
        raise


def cancelar_reserva(apartamento: str, codigo: str) -> bool:
    """Cancela uma reserva do próprio apartamento. Nunca revela se o código
    pertence a outro apartamento ou simplesmente não existe: ambos os casos
    retornam False (Garantia 2)."""
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "UPDATE reservas SET status = 'cancelada'"
            " WHERE codigo = ? AND apartamento = ? AND status = 'ativa'",
            (codigo, apartamento),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise


def reservas_do_apartamento(apartamento: str) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT codigo, area, data FROM reservas"
        " WHERE apartamento = ? AND status = 'ativa' ORDER BY data",
        (apartamento,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Visitantes
# ---------------------------------------------------------------------------


def autorizar_visitante(apartamento: str, nome: str, data: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO visitantes(apartamento, nome, data) VALUES (?, ?, ?)",
        (apartamento, nome, data),
    )


def visitantes_do_apartamento(apartamento: str) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data",
        (apartamento,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Sessões (apenas mapeamento session_id -> apartamento, usado para saber qual
# user_id do ADK consultar; o valor de verdade que as tools enxergam vem do
# state da sessão do ADK, definido uma única vez na criação — ver Garantia 2)
# ---------------------------------------------------------------------------


def registrar_sessao(session_id: str, apartamento: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO sessoes(session_id, apartamento) VALUES (?, ?)",
        (session_id, apartamento),
    )


def apartamento_da_sessao(session_id: str) -> Optional[str]:
    conn = get_conn()
    row = conn.execute(
        "SELECT apartamento FROM sessoes WHERE session_id = ?", (session_id,)
    ).fetchone()
    return row["apartamento"] if row else None
