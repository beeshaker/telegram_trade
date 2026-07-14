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
