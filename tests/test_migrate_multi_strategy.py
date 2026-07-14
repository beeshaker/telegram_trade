from sqlalchemy import create_engine, inspect, text

from scripts.migrate_multi_strategy import (
    migrate_epic_configs,
    migrate_signals,
    migrate_signals_session_name,
)


def _build_old_schema(engine):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE epic_configs (
                    id INTEGER PRIMARY KEY,
                    epic VARCHAR(50) NOT NULL UNIQUE,
                    enabled BOOLEAN NOT NULL,
                    timezone VARCHAR(50) NOT NULL,
                    session_name VARCHAR(100) NOT NULL,
                    range_short_start TIME NOT NULL,
                    range_short_end TIME NOT NULL,
                    range_long_start TIME NOT NULL,
                    range_long_end TIME NOT NULL,
                    trade_start TIME NOT NULL,
                    trade_end TIME NOT NULL,
                    risk_per_trade_percent NUMERIC(5, 2),
                    max_trades_per_day INTEGER,
                    max_losses_per_day INTEGER,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO epic_configs (
                    id, epic, enabled, timezone, session_name,
                    range_short_start, range_short_end, range_long_start, range_long_end,
                    trade_start, trade_end, created_at, updated_at
                ) VALUES (
                    1, 'US100', 1, 'America/New_York', 'NY Open',
                    '09:30:00', '09:45:00', '09:30:00', '10:00:00',
                    '09:45:00', '10:30:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE signals (
                    id INTEGER PRIMARY KEY,
                    symbol VARCHAR(50) NOT NULL,
                    signal_time DATETIME NOT NULL,
                    direction VARCHAR(10) NOT NULL,
                    setup_type VARCHAR(100) NOT NULL,
                    status VARCHAR(50) NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO signals (id, symbol, signal_time, direction, setup_type, status, created_at)
                VALUES (1, 'US100', '2026-01-01 09:50:00', 'BUY',
                        'NY Open Sweep + FVG AUTO_PAPER (15-min opening range)', 'DETECTED',
                        '2026-01-01 09:50:00')
                """
            )
        )


def test_migrate_epic_configs_preserves_data_and_backfills_strategy():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _build_old_schema(engine)

    with engine.begin() as conn:
        migrate_epic_configs(conn, inspect(conn))

    with engine.begin() as conn:
        rows = conn.execute(text("SELECT epic, strategy, session_name FROM epic_configs")).fetchall()
        assert rows == [("US100", "SWEEP_FVG_OPENING_RANGE", "NY Open")]


def test_migrate_epic_configs_is_idempotent():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _build_old_schema(engine)

    with engine.begin() as conn:
        migrate_epic_configs(conn, inspect(conn))
    with engine.begin() as conn:
        migrate_epic_configs(conn, inspect(conn))  # must no-op, not raise
        count = conn.execute(text("SELECT COUNT(*) FROM epic_configs")).fetchone()[0]
        assert count == 1


def test_migrate_signals_backfills_strategy():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _build_old_schema(engine)

    with engine.begin() as conn:
        migrate_signals(conn, inspect(conn))

    with engine.begin() as conn:
        row = conn.execute(text("SELECT strategy FROM signals WHERE id = 1")).fetchone()
        assert row[0] == "SWEEP_FVG_OPENING_RANGE"


def test_migrate_signals_is_idempotent():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _build_old_schema(engine)

    with engine.begin() as conn:
        migrate_signals(conn, inspect(conn))
    with engine.begin() as conn:
        migrate_signals(conn, inspect(conn))  # must no-op, not raise
        row = conn.execute(text("SELECT strategy FROM signals WHERE id = 1")).fetchone()
        assert row[0] == "SWEEP_FVG_OPENING_RANGE"


def test_migrate_signals_session_name_backfills_from_epic_configs():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _build_old_schema(engine)

    with engine.begin() as conn:
        migrate_epic_configs(conn, inspect(conn))
        migrate_signals(conn, inspect(conn))
        migrate_signals_session_name(conn, inspect(conn))

    with engine.begin() as conn:
        row = conn.execute(text("SELECT session_name FROM signals WHERE id = 1")).fetchone()
        assert row[0] == "NY Open"


def test_migrate_signals_session_name_is_idempotent():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    _build_old_schema(engine)

    with engine.begin() as conn:
        migrate_epic_configs(conn, inspect(conn))
        migrate_signals(conn, inspect(conn))
        migrate_signals_session_name(conn, inspect(conn))
    with engine.begin() as conn:
        migrate_signals_session_name(conn, inspect(conn))  # must no-op, not raise
        row = conn.execute(text("SELECT session_name FROM signals WHERE id = 1")).fetchone()
        assert row[0] == "NY Open"
