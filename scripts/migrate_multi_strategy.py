import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import inspect, text

from app.db import engine


def _column_names(inspector, table_name) -> set[str]:
    return {col["name"] for col in inspector.get_columns(table_name)}


def migrate_epic_configs(conn, inspector) -> None:
    columns = _column_names(inspector, "epic_configs")
    if "strategy" in columns:
        print("epic_configs already migrated. Skipping.")
        return

    print("Migrating epic_configs to support multiple strategies per epic...")
    conn.execute(text("ALTER TABLE epic_configs RENAME TO epic_configs_old"))
    conn.execute(
        text(
            """
            CREATE TABLE epic_configs (
                id INTEGER PRIMARY KEY,
                epic VARCHAR(50) NOT NULL,
                strategy VARCHAR(50) NOT NULL DEFAULT 'SWEEP_FVG_OPENING_RANGE',
                enabled BOOLEAN NOT NULL,
                timezone VARCHAR(50) NOT NULL,
                session_name VARCHAR(100) NOT NULL,
                range_short_start TIME,
                range_short_end TIME,
                range_long_start TIME,
                range_long_end TIME,
                trade_start TIME NOT NULL,
                trade_end TIME NOT NULL,
                risk_per_trade_percent NUMERIC(5, 2),
                max_trades_per_day INTEGER,
                max_losses_per_day INTEGER,
                params JSON,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT uq_epic_strategy_session UNIQUE (epic, strategy, session_name)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            INSERT INTO epic_configs (
                id, epic, strategy, enabled, timezone, session_name,
                range_short_start, range_short_end, range_long_start, range_long_end,
                trade_start, trade_end, risk_per_trade_percent, max_trades_per_day,
                max_losses_per_day, created_at, updated_at
            )
            SELECT
                id, epic, 'SWEEP_FVG_OPENING_RANGE', enabled, timezone, session_name,
                range_short_start, range_short_end, range_long_start, range_long_end,
                trade_start, trade_end, risk_per_trade_percent, max_trades_per_day,
                max_losses_per_day, created_at, updated_at
            FROM epic_configs_old
            """
        )
    )
    conn.execute(text("DROP TABLE epic_configs_old"))
    print("epic_configs migrated.")


def migrate_signals(conn, inspector) -> None:
    columns = _column_names(inspector, "signals")
    if "strategy" in columns:
        print("signals already migrated. Skipping.")
        return

    print("Adding strategy column to signals...")
    conn.execute(
        text("ALTER TABLE signals ADD COLUMN strategy VARCHAR(50) NOT NULL DEFAULT 'SWEEP_FVG_OPENING_RANGE'")
    )
    print("signals migrated.")


def main() -> None:
    with engine.begin() as conn:
        inspector = inspect(conn)
        migrate_epic_configs(conn, inspector)
        migrate_signals(conn, inspector)
    print("Migration complete.")


if __name__ == "__main__":
    main()
