from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PaperAccount, PaperTrade
from app.paper.auto_paper import total_open_risk_percent


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


def _make_account(db, balance=1000):
    account = PaperAccount(name="default", currency="USD", starting_balance=balance, balance=balance, equity=balance)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def test_total_open_risk_percent_sums_pending_and_active_only(db_session):
    account = _make_account(db_session, balance=1000)
    db_session.add_all(
        [
            PaperTrade(symbol="US100", direction="BUY", status="PENDING", risk_amount=Decimal("10")),
            PaperTrade(symbol="GOLD", direction="SELL", status="ACTIVE", risk_amount=Decimal("15")),
            PaperTrade(symbol="UK100", direction="BUY", status="CLOSED", risk_amount=Decimal("100")),
        ]
    )
    db_session.commit()

    result = total_open_risk_percent(db_session, account)

    assert result == pytest.approx(2.5)  # (10 + 15) / 1000 * 100


def test_total_open_risk_percent_is_zero_with_no_open_trades(db_session):
    account = _make_account(db_session, balance=1000)
    result = total_open_risk_percent(db_session, account)
    assert result == 0.0


from datetime import datetime

from app.models import Signal


def _make_signal(db, symbol, strategy):
    signal = Signal(
        symbol=symbol,
        signal_time=datetime.utcnow(),
        direction="BUY",
        setup_type="test",
        strategy=strategy,
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)
    return signal


def test_get_open_trades_scoped_by_strategy_does_not_see_other_strategy(db_session):
    from app.paper.auto_paper import get_open_trades

    sig_a = _make_signal(db_session, "US100", "SWEEP_FVG_OPENING_RANGE")
    sig_b = _make_signal(db_session, "US100", "SWEEP_FVG_PDH_PDL")
    db_session.add_all(
        [
            PaperTrade(signal_id=sig_a.id, symbol="US100", direction="BUY", status="ACTIVE"),
            PaperTrade(signal_id=sig_b.id, symbol="US100", direction="BUY", status="PENDING"),
        ]
    )
    db_session.commit()

    opening_range_trades = get_open_trades(db_session, "US100", "SWEEP_FVG_OPENING_RANGE")
    pdh_pdl_trades = get_open_trades(db_session, "US100", "SWEEP_FVG_PDH_PDL")
    all_trades_for_epic = get_open_trades(db_session, "US100")

    assert len(opening_range_trades) == 1
    assert len(pdh_pdl_trades) == 1
    assert len(all_trades_for_epic) == 2


def test_trades_today_count_scoped_by_strategy(db_session):
    from app.paper.auto_paper import trades_today_count

    sig_a = _make_signal(db_session, "US100", "SWEEP_FVG_OPENING_RANGE")
    sig_b = _make_signal(db_session, "US100", "VWAP_MEAN_REVERSION")
    db_session.add_all(
        [
            PaperTrade(signal_id=sig_a.id, symbol="US100", direction="BUY", status="CLOSED"),
            PaperTrade(signal_id=sig_b.id, symbol="US100", direction="SELL", status="CLOSED"),
        ]
    )
    db_session.commit()

    assert trades_today_count(db_session, "US100", "SWEEP_FVG_OPENING_RANGE") == 1
    assert trades_today_count(db_session, "US100", "VWAP_MEAN_REVERSION") == 1
    assert trades_today_count(db_session, "US100") == 2


def test_losses_today_count_scoped_by_strategy(db_session):
    from app.paper.auto_paper import losses_today_count

    sig_a = _make_signal(db_session, "US100", "SWEEP_FVG_OPENING_RANGE")
    sig_b = _make_signal(db_session, "US100", "VWAP_MEAN_REVERSION")
    db_session.add_all(
        [
            PaperTrade(signal_id=sig_a.id, symbol="US100", direction="BUY", status="CLOSED", result="LOSS"),
            PaperTrade(signal_id=sig_b.id, symbol="US100", direction="SELL", status="CLOSED", result="WIN"),
        ]
    )
    db_session.commit()

    assert losses_today_count(db_session, "US100", "SWEEP_FVG_OPENING_RANGE") == 1
    assert losses_today_count(db_session, "US100", "VWAP_MEAN_REVERSION") == 0


def test_stop_trading_today_scoped_by_strategy_only_cancels_that_strategy(db_session):
    from app.paper.auto_paper import is_stopped_today, stop_trading_today

    sig_a = _make_signal(db_session, "US100", "SWEEP_FVG_OPENING_RANGE")
    sig_b = _make_signal(db_session, "US100", "VWAP_MEAN_REVERSION")
    trade_a = PaperTrade(signal_id=sig_a.id, symbol="US100", direction="BUY", status="PENDING")
    trade_b = PaperTrade(signal_id=sig_b.id, symbol="US100", direction="SELL", status="PENDING")
    db_session.add_all([trade_a, trade_b])
    db_session.commit()

    stop_trading_today(db_session, "US100", "SWEEP_FVG_OPENING_RANGE")

    db_session.refresh(trade_a)
    db_session.refresh(trade_b)
    assert trade_a.status == "CANCELLED"
    assert trade_b.status == "PENDING"
    assert is_stopped_today(db_session, "US100", "SWEEP_FVG_OPENING_RANGE") is True
    assert is_stopped_today(db_session, "US100", "VWAP_MEAN_REVERSION") is False
