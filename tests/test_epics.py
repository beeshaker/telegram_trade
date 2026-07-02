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
    assert count_after_first == 5

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
        enabled=True,
        timezone="UTC",
        session_name="Test Session",
        range_short_start=dtime(9, 0),
        range_short_end=dtime(9, 15),
        range_long_start=dtime(9, 0),
        range_long_end=dtime(9, 30),
        trade_start=dtime(9, 15),
        trade_end=dtime(10, 0),
        risk_per_trade_percent=None,
        max_trades_per_day=None,
        max_losses_per_day=None,
    )

    assert created.id is not None
    assert created.session_name == "Test Session"

    updated = upsert_epic_config(
        db_session,
        epic="TESTEPIC",
        enabled=False,
        timezone="UTC",
        session_name="Updated Session",
        range_short_start=dtime(9, 0),
        range_short_end=dtime(9, 15),
        range_long_start=dtime(9, 0),
        range_long_end=dtime(9, 30),
        trade_start=dtime(9, 15),
        trade_end=dtime(10, 0),
        risk_per_trade_percent=1.0,
        max_trades_per_day=5,
        max_losses_per_day=2,
    )

    assert updated.id == created.id
    assert updated.enabled is False
    assert updated.session_name == "Updated Session"
    assert float(updated.risk_per_trade_percent) == 1.0

    count = db_session.query(EpicConfig).filter(EpicConfig.epic == "TESTEPIC").count()
    assert count == 1
