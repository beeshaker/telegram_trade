from datetime import time as dtime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import EpicConfig


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_config(**overrides):
    fields = dict(
        epic="US100",
        strategy="SWEEP_FVG_OPENING_RANGE",
        enabled=True,
        timezone="America/New_York",
        session_name="NY Open",
        range_short_start=dtime(9, 30),
        range_short_end=dtime(9, 45),
        range_long_start=dtime(9, 30),
        range_long_end=dtime(10, 0),
        trade_start=dtime(9, 45),
        trade_end=dtime(10, 30),
    )
    fields.update(overrides)
    return EpicConfig(**fields)


def test_same_epic_different_strategy_is_allowed(db_session):
    db_session.add(_make_config(strategy="SWEEP_FVG_OPENING_RANGE"))
    db_session.add(_make_config(strategy="SWEEP_FVG_PDH_PDL", session_name="PDH/PDL All Day"))
    db_session.commit()

    count = db_session.query(EpicConfig).filter(EpicConfig.epic == "US100").count()
    assert count == 2


def test_same_epic_strategy_session_is_rejected(db_session):
    db_session.add(_make_config())
    db_session.commit()

    db_session.add(_make_config())
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_pdh_pdl_row_can_omit_range_columns(db_session):
    cfg = _make_config(
        strategy="SWEEP_FVG_PDH_PDL",
        session_name="PDH/PDL All Day",
        range_short_start=None,
        range_short_end=None,
        range_long_start=None,
        range_long_end=None,
    )
    db_session.add(cfg)
    db_session.commit()

    saved = db_session.query(EpicConfig).filter(EpicConfig.epic == "US100").one()
    assert saved.range_short_start is None


def test_epic_config_default_strategy_is_opening_range(db_session):
    cfg = EpicConfig(
        epic="TESTDEFAULT",
        enabled=True,
        timezone="UTC",
        session_name="Test",
        range_short_start=dtime(9, 30),
        range_short_end=dtime(9, 45),
        range_long_start=dtime(9, 30),
        range_long_end=dtime(10, 0),
        trade_start=dtime(9, 45),
        trade_end=dtime(10, 30),
    )
    db_session.add(cfg)
    db_session.commit()
    db_session.refresh(cfg)
    assert cfg.strategy == "SWEEP_FVG_OPENING_RANGE"
