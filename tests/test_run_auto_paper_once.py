from datetime import date, datetime, time as dtime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import Candle, EpicConfig, PaperAccount, PaperTrade, Signal

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


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


def _insert_candle(db, symbol, timeframe, local_dt, h, l, c, v=100):
    db.add(
        Candle(
            symbol=symbol,
            timeframe=timeframe,
            candle_time=local_dt.astimezone(UTC).replace(tzinfo=None),
            open=c,
            high=h,
            low=l,
            close=c,
            volume=v,
        )
    )


def test_run_opening_range_strategy_creates_signal_and_trade(db_session):
    from scripts.run_auto_paper_once import run_opening_range_strategy

    session_date = date(2026, 6, 1)

    _insert_candle(db_session, "US100", "M1", datetime.combine(session_date, dtime(9, 0), tzinfo=NY), 101, 99, 100)
    _insert_candle(db_session, "US100", "M1", datetime.combine(session_date, dtime(9, 30), tzinfo=NY), 105, 95, 102)

    m5_specs = [
        (dtime(9, 45), 104, 99, 103),
        (dtime(9, 50), 106, 102, 104),
        (dtime(9, 55), 107, 101, 103),
        (dtime(10, 0), 108, 102, 104),
        (dtime(10, 5), 109, 105, 106),
        (dtime(10, 10), 107, 98, 99),
        (dtime(10, 15), 100, 95, 96),
    ]
    last_candle_time = None
    for t, h, l, c in m5_specs:
        local_dt = datetime.combine(session_date, t, tzinfo=NY)
        _insert_candle(db_session, "US100", "M5", local_dt, h, l, c)
        last_candle_time = local_dt.astimezone(UTC).replace(tzinfo=None)
    db_session.commit()

    account = PaperAccount(name="default", currency="USD", starting_balance=1000, balance=1000, equity=1000)
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    cfg = EpicConfig(
        epic="US100",
        strategy="SWEEP_FVG_OPENING_RANGE",
        enabled=True,
        timezone="America/New_York",
        session_name="NY Open",
        range_short_start=dtime(9, 30),
        range_short_end=dtime(9, 45),
        range_long_start=dtime(9, 30),
        range_long_end=dtime(11, 0),  # widened so this test's 10:15 "now" stays on the short-range branch
        trade_start=dtime(9, 45),
        trade_end=dtime(16, 0),
    )

    settings = Settings(risk_per_trade_percent=0.5, max_trades_per_day=1, max_losses_per_day=2, min_risk_reward=2.0)
    latest_candle = SimpleNamespace(candle_time=last_candle_time)
    latest_local = datetime.combine(session_date, dtime(10, 15), tzinfo=NY)

    run_opening_range_strategy(
        db_session, cfg, settings, sweep_buffer=0.0, stop_buffer=0.5,
        account=account, latest_candle=latest_candle, latest_local=latest_local, session_date=session_date,
    )

    signal = db_session.query(Signal).one()
    assert signal.strategy == "SWEEP_FVG_OPENING_RANGE"
    assert signal.symbol == "US100"
    assert signal.direction == "SELL"
    assert float(signal.entry_price) == pytest.approx(102.5)
    assert float(signal.stop_loss) == pytest.approx(106.5)
    assert float(signal.take_profit) == pytest.approx(94.5)
    assert float(signal.risk_reward) == pytest.approx(2.0)

    trade = db_session.query(PaperTrade).one()
    assert trade.signal_id == signal.id


def test_run_opening_range_strategy_does_nothing_outside_trade_window(db_session):
    from scripts.run_auto_paper_once import run_opening_range_strategy

    session_date = date(2026, 6, 1)
    account = PaperAccount(name="default", currency="USD", starting_balance=1000, balance=1000, equity=1000)
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    cfg = EpicConfig(
        epic="US100", strategy="SWEEP_FVG_OPENING_RANGE", enabled=True, timezone="America/New_York",
        session_name="NY Open", range_short_start=dtime(9, 30), range_short_end=dtime(9, 45),
        range_long_start=dtime(9, 30), range_long_end=dtime(10, 0),
        trade_start=dtime(9, 45), trade_end=dtime(10, 30),
    )
    settings = Settings()
    latest_candle = SimpleNamespace(candle_time=datetime(2026, 6, 1, 20, 0))
    latest_local = datetime.combine(session_date, dtime(20, 0), tzinfo=NY)  # after trade_end

    run_opening_range_strategy(
        db_session, cfg, settings, sweep_buffer=0.0, stop_buffer=0.5,
        account=account, latest_candle=latest_candle, latest_local=latest_local, session_date=session_date,
    )

    assert db_session.query(Signal).count() == 0


def test_run_pdh_pdl_strategy_creates_signal_and_trade(db_session):
    from scripts.run_auto_paper_once import run_pdh_pdl_strategy

    previous_day = date(2026, 6, 1)
    session_date = date(2026, 6, 2)

    _insert_candle(db_session, "US100", "M1", datetime.combine(previous_day, dtime(12, 0), tzinfo=NY), 105, 95, 100)

    m5_specs = [
        (dtime(9, 45), 104, 99, 103),
        (dtime(9, 50), 106, 102, 104),
        (dtime(9, 55), 107, 101, 103),
        (dtime(10, 0), 108, 102, 104),
        (dtime(10, 5), 109, 105, 106),
        (dtime(10, 10), 107, 98, 99),
        (dtime(10, 15), 100, 95, 96),
    ]
    last_candle_time = None
    for t, h, l, c in m5_specs:
        local_dt = datetime.combine(session_date, t, tzinfo=NY)
        _insert_candle(db_session, "US100", "M5", local_dt, h, l, c)
        last_candle_time = local_dt.astimezone(UTC).replace(tzinfo=None)
    db_session.commit()

    account = PaperAccount(name="default", currency="USD", starting_balance=1000, balance=1000, equity=1000)
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    cfg = EpicConfig(
        epic="US100",
        strategy="SWEEP_FVG_PDH_PDL",
        enabled=True,
        timezone="America/New_York",
        session_name="PDH/PDL All Day",
        trade_start=dtime(0, 5),
        trade_end=dtime(23, 55),
    )

    settings = Settings(risk_per_trade_percent=0.5, max_trades_per_day=1, max_losses_per_day=2, min_risk_reward=2.0)
    latest_candle = SimpleNamespace(candle_time=last_candle_time)
    latest_local = datetime.combine(session_date, dtime(10, 15), tzinfo=NY)

    run_pdh_pdl_strategy(
        db_session, cfg, settings, sweep_buffer=0.0, stop_buffer=0.5,
        account=account, latest_candle=latest_candle, latest_local=latest_local, session_date=session_date,
    )

    signal = db_session.query(Signal).one()
    assert signal.strategy == "SWEEP_FVG_PDH_PDL"
    assert signal.direction == "SELL"
    assert float(signal.opening_range_high) == pytest.approx(105.0)
    assert float(signal.opening_range_low) == pytest.approx(95.0)
    assert float(signal.entry_price) == pytest.approx(102.5)
    assert float(signal.stop_loss) == pytest.approx(106.5)
    assert float(signal.take_profit) == pytest.approx(94.5)

    trade = db_session.query(PaperTrade).one()
    assert trade.signal_id == signal.id


def test_run_pdh_pdl_strategy_does_nothing_when_previous_day_range_missing(db_session):
    from scripts.run_auto_paper_once import run_pdh_pdl_strategy

    session_date = date(2026, 6, 2)
    account = PaperAccount(name="default", currency="USD", starting_balance=1000, balance=1000, equity=1000)
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    cfg = EpicConfig(
        epic="US100", strategy="SWEEP_FVG_PDH_PDL", enabled=True, timezone="America/New_York",
        session_name="PDH/PDL All Day", trade_start=dtime(0, 5), trade_end=dtime(23, 55),
    )
    settings = Settings()
    latest_candle = SimpleNamespace(candle_time=datetime(2026, 6, 2, 14, 15))
    latest_local = datetime.combine(session_date, dtime(10, 15), tzinfo=NY)

    run_pdh_pdl_strategy(
        db_session, cfg, settings, sweep_buffer=0.0, stop_buffer=0.5,
        account=account, latest_candle=latest_candle, latest_local=latest_local, session_date=session_date,
    )

    assert db_session.query(Signal).count() == 0


def test_run_vwap_strategy_creates_signal_and_trade_on_fade(db_session):
    from scripts.run_auto_paper_once import run_vwap_strategy

    session_date = date(2026, 6, 1)

    m5_specs = [
        (dtime(9, 45), 100, 100, 100),
        (dtime(9, 50), 100, 100, 100),
        (dtime(9, 55), 100, 100, 100),
        (dtime(10, 0), 100, 100, 100),
        (dtime(10, 5), 110, 101, 100.5),
        (dtime(10, 10), 101, 99, 100),
        (dtime(10, 15), 100.9, 97, 98.5),
    ]
    last_candle_time = None
    for t, h, l, c in m5_specs:
        local_dt = datetime.combine(session_date, t, tzinfo=NY)
        _insert_candle(db_session, "US100", "M5", local_dt, h, l, c, v=1)
        last_candle_time = local_dt.astimezone(UTC).replace(tzinfo=None)
    db_session.commit()

    account = PaperAccount(name="default", currency="USD", starting_balance=1000, balance=1000, equity=1000)
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    cfg = EpicConfig(
        epic="US100",
        strategy="VWAP_MEAN_REVERSION",
        enabled=True,
        timezone="America/New_York",
        session_name="NY Midday VWAP",
        trade_start=dtime(9, 45),
        trade_end=dtime(13, 30),
        params={"deviation_threshold": 1.5},
    )

    settings = Settings(risk_per_trade_percent=0.5, max_trades_per_day=1, max_losses_per_day=2, min_risk_reward=2.0)
    latest_candle = SimpleNamespace(candle_time=last_candle_time)
    latest_local = datetime.combine(session_date, dtime(10, 15), tzinfo=NY)

    run_vwap_strategy(
        db_session, cfg, settings, sweep_buffer=0.0, stop_buffer=0.5,
        account=account, latest_candle=latest_candle, latest_local=latest_local, session_date=session_date,
    )

    signal = db_session.query(Signal).one()
    assert signal.strategy == "VWAP_MEAN_REVERSION"
    assert signal.direction == "SELL"
    assert float(signal.entry_price) == pytest.approx(100.95, abs=0.01)
    assert float(signal.stop_loss) == pytest.approx(110.5, abs=0.01)
    assert float(signal.take_profit) == pytest.approx(100.77, abs=0.01)
    assert float(signal.take_profit) < float(signal.entry_price)

    trade = db_session.query(PaperTrade).one()
    assert trade.signal_id == signal.id


def test_run_vwap_strategy_does_nothing_with_too_few_candles(db_session):
    from scripts.run_auto_paper_once import run_vwap_strategy

    session_date = date(2026, 6, 1)
    for t in [dtime(9, 45), dtime(9, 50), dtime(9, 55)]:
        local_dt = datetime.combine(session_date, t, tzinfo=NY)
        _insert_candle(db_session, "US100", "M5", local_dt, 100, 100, 100, v=1)
    db_session.commit()

    account = PaperAccount(name="default", currency="USD", starting_balance=1000, balance=1000, equity=1000)
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    cfg = EpicConfig(
        epic="US100", strategy="VWAP_MEAN_REVERSION", enabled=True, timezone="America/New_York",
        session_name="NY Midday VWAP", trade_start=dtime(9, 45), trade_end=dtime(13, 30),
        params={"deviation_threshold": 1.5},
    )
    settings = Settings()
    latest_candle = SimpleNamespace(candle_time=datetime(2026, 6, 1, 13, 55))
    latest_local = datetime.combine(session_date, dtime(9, 55), tzinfo=NY)

    run_vwap_strategy(
        db_session, cfg, settings, sweep_buffer=0.0, stop_buffer=0.5,
        account=account, latest_candle=latest_candle, latest_local=latest_local, session_date=session_date,
    )

    assert db_session.query(Signal).count() == 0
