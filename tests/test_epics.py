from datetime import time as dtime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registers EpicConfig with Base.metadata
from app.config import Settings
from app.db import Base
from app.epics import effective_risk, ensure_seeded, upsert_epic_config
from app.models import EpicConfig


@pytest.fixture()
def db_session(monkeypatch):
    monkeypatch.delenv("CAPITAL_EPICS", raising=False)
    monkeypatch.delenv("CAPITAL_EPIC", raising=False)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_ensure_seeded_is_idempotent(db_session):
    ensure_seeded(db_session)
    count_after_first = db_session.query(EpicConfig).count()
    assert count_after_first == 19

    ensure_seeded(db_session)
    count_after_second = db_session.query(EpicConfig).count()
    assert count_after_second == count_after_first


def test_effective_risk_falls_back_to_settings_when_overrides_none():
    settings = Settings(risk_per_trade_percent=0.75, max_trades_per_day=3, max_losses_per_day=4)
    cfg = EpicConfig(
        epic="TEST",
        risk_per_trade_percent=None,
        max_trades_per_day=None,
        max_losses_per_day=None,
    )

    risk_percent, max_trades, max_losses = effective_risk(cfg, settings)

    assert risk_percent == 0.75
    assert max_trades == 3
    assert max_losses == 4


def test_effective_risk_uses_override_when_set():
    settings = Settings(risk_per_trade_percent=0.75, max_trades_per_day=3, max_losses_per_day=4)
    cfg = EpicConfig(
        epic="TEST",
        risk_per_trade_percent=1.5,
        max_trades_per_day=2,
        max_losses_per_day=1,
    )

    risk_percent, max_trades, max_losses = effective_risk(cfg, settings)

    assert risk_percent == 1.5
    assert max_trades == 2
    assert max_losses == 1


def test_upsert_epic_config_creates_then_updates(db_session):
    created = upsert_epic_config(
        db_session,
        epic="TESTEPIC",
        strategy="SWEEP_FVG_OPENING_RANGE",
        session_name="Test Session",
        enabled=True,
        timezone="UTC",
        range_short_start=dtime(9, 0),
        range_short_end=dtime(9, 15),
        range_long_start=dtime(9, 0),
        range_long_end=dtime(9, 30),
        trade_start=dtime(9, 15),
        trade_end=dtime(10, 0),
        risk_per_trade_percent=None,
        max_trades_per_day=None,
        max_losses_per_day=None,
        params=None,
    )

    assert created.id is not None
    assert created.session_name == "Test Session"

    updated = upsert_epic_config(
        db_session,
        epic="TESTEPIC",
        strategy="SWEEP_FVG_OPENING_RANGE",
        session_name="Test Session",
        enabled=False,
        timezone="UTC",
        range_short_start=dtime(9, 0),
        range_short_end=dtime(9, 15),
        range_long_start=dtime(9, 0),
        range_long_end=dtime(9, 30),
        trade_start=dtime(9, 15),
        trade_end=dtime(10, 0),
        risk_per_trade_percent=1.0,
        max_trades_per_day=5,
        max_losses_per_day=2,
        params=None,
    )

    assert updated.id == created.id
    assert updated.enabled is False
    assert float(updated.risk_per_trade_percent) == 1.0

    count = db_session.query(EpicConfig).filter(EpicConfig.epic == "TESTEPIC").count()
    assert count == 1


def test_ensure_seeded_new_strategies_default_disabled(db_session, monkeypatch):
    monkeypatch.setenv("CAPITAL_EPICS", "US100,UK100,GOLD,USDJPY,NATURALGAS")
    ensure_seeded(db_session)

    pdh_pdl_rows = db_session.query(EpicConfig).filter(EpicConfig.strategy == "SWEEP_FVG_PDH_PDL").all()
    assert len(pdh_pdl_rows) == 5
    assert all(not row.enabled for row in pdh_pdl_rows)

    vwap_rows = db_session.query(EpicConfig).filter(EpicConfig.strategy == "VWAP_MEAN_REVERSION").all()
    assert len(vwap_rows) == 5
    assert all(not row.enabled for row in vwap_rows)


def test_upsert_epic_config_same_epic_strategy_different_session_creates_new_row(db_session):
    upsert_epic_config(
        db_session, epic="US100", strategy="SWEEP_FVG_OPENING_RANGE", session_name="NY Open",
        enabled=True, timezone="America/New_York",
        range_short_start=dtime(9, 30), range_short_end=dtime(9, 45),
        range_long_start=dtime(9, 30), range_long_end=dtime(10, 0),
        trade_start=dtime(9, 45), trade_end=dtime(10, 30),
        risk_per_trade_percent=None, max_trades_per_day=None, max_losses_per_day=None, params=None,
    )
    upsert_epic_config(
        db_session, epic="US100", strategy="SWEEP_FVG_OPENING_RANGE", session_name="NY PM",
        enabled=True, timezone="America/New_York",
        range_short_start=dtime(14, 0), range_short_end=dtime(14, 15),
        range_long_start=dtime(14, 0), range_long_end=dtime(14, 30),
        trade_start=dtime(14, 15), trade_end=dtime(15, 0),
        risk_per_trade_percent=None, max_trades_per_day=None, max_losses_per_day=None, params=None,
    )

    count = db_session.query(EpicConfig).filter(EpicConfig.epic == "US100").count()
    assert count == 2
