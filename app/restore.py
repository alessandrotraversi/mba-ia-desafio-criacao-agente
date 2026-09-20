"""Restaura reservas e visitantes ao estado inicial de `dados/` e apaga as
sessões existentes (decisão de projeto: um "restore" limpo e previsível para
o avaliador, documentado no README).

Uso: uv run python -m app.restore
"""

from __future__ import annotations

from . import config, db


def main() -> None:
    config.ensure_data_dir()
    db.reset_db()

    for sidecar in ("", "-wal", "-shm"):
        caminho = config.SESSIONS_DB.with_name(config.SESSIONS_DB.name + sidecar)
        if caminho.exists():
            caminho.unlink()

    print("Dados restaurados: reservas, visitantes e sessões voltaram ao estado inicial.")


if __name__ == "__main__":
    main()
