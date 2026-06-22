from __future__ import annotations

import os
from datetime import datetime, date
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import BotState, Candle, PaperAccount, PaperTrade, Signal

UTC = ZoneInfo("UTC")
NY = ZoneInfo("America/New_York")


def utc_now() -> datetime:
    return datetime.utcnow()


def today_ny() -> date:
    return datetime.now(tz=NY).date()


def ensure_paper_account(db: Session) -> PaperAccount:
    account = db.query(PaperAccount).filter(PaperAccount.name == "default").first()
    if account:
        return account

    starting_balance = Decimal(os.getenv("PAPER_START_BALANCE", "1000"))
    account = PaperAccount(
        name="default",
        currency=os.getenv("PAPER_CURRENCY", "USD"),
        starting_balance=starting_balance,
        balance=starting_balance,
        equity=starting_balance,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def reset_paper_account(db: Session) -> PaperAccount:
    account = ensure_paper_account(db)
    starting_balance = Decimal(os.getenv("PAPER_START_BALANCE", "1000"))
    account.starting_balance = starting_balance
    account.balance = starting_balance
    account.equity = starting_balance
    account.updated_at = utc_now()

    open_trades = (
        db.query(PaperTrade)
        .filter(PaperTrade.status.in_(["PENDING", "ACTIVE"]))
        .all()
    )
    for trade in open_trades:
        trade.status = "CANCELLED"
        trade.result = "RESET"
        trade.updated_at = utc_now()

    db.commit()
    db.refresh(account)
    return account


def get_state(db: Session, key: str, default: str = "") -> str:
    row = db.query(BotState).filter(BotState.key == key).order_by(BotState.id.asc()).first()
    return row.value if row else default


def set_state(db: Session, key: str, value: str) -> None:
    row = db.query(BotState).filter(BotState.key == key).order_by(BotState.id.asc()).first()
    if not row:
        row = BotState(key=key, value=value)
        db.add(row)
    else:
        row.value = value
        row.updated_at = utc_now()
    db.commit()


def is_paused(db: Session) -> bool:
    return get_state(db, "trading_paused", "false").lower() == "true"


def set_paused(db: Session, paused: bool) -> None:
    set_state(db, "trading_paused", "true" if paused else "false")


def stop_today_key() -> str:
    return f"stop_today_{today_ny().isoformat()}"


def is_stopped_today(db: Session) -> bool:
    return get_state(db, stop_today_key(), "false").lower() == "true"


def stop_trading_today(db: Session) -> None:
    set_state(db, stop_today_key(), "true")
    cancel_pending_trades(db)


def get_latest_price(db: Session, symbol: str) -> float | None:
    candle = (
        db.query(Candle)
        .filter(Candle.symbol == symbol, Candle.timeframe == "M1")
        .order_by(Candle.candle_time.desc())
        .first()
    )
    return float(candle.close) if candle else None


def get_latest_candle(db: Session, symbol: str) -> Candle | None:
    return (
        db.query(Candle)
        .filter(Candle.symbol == symbol, Candle.timeframe == "M1")
        .order_by(Candle.candle_time.desc())
        .first()
    )


def get_open_trades(db: Session) -> list[PaperTrade]:
    return (
        db.query(PaperTrade)
        .filter(PaperTrade.status.in_(["PENDING", "ACTIVE"]))
        .order_by(PaperTrade.created_at.asc())
        .all()
    )


def cancel_pending_trades(db: Session) -> int:
    trades = db.query(PaperTrade).filter(PaperTrade.status == "PENDING").all()
    for trade in trades:
        trade.status = "CANCELLED"
        trade.result = "CANCELLED"
        trade.updated_at = utc_now()
    db.commit()
    return len(trades)


def risk_amount_for_account(account: PaperAccount, risk_percent: float) -> Decimal:
    return Decimal(str(float(account.balance))) * Decimal(str(risk_percent)) / Decimal("100")


def create_trade_from_signal(db: Session, signal: Signal, risk_percent: float) -> PaperTrade:
    existing = db.query(PaperTrade).filter(PaperTrade.signal_id == signal.id).first()
    if existing:
        return existing

    account = ensure_paper_account(db)
    risk_amount = risk_amount_for_account(account, risk_percent)

    entry = Decimal(str(signal.entry_price))
    stop = Decimal(str(signal.stop_loss))
    per_unit_risk = abs(entry - stop)
    if per_unit_risk <= 0:
        raise ValueError("Cannot create paper trade with zero/negative risk.")

    position_size = risk_amount / per_unit_risk

    trade = PaperTrade(
        signal_id=signal.id,
        symbol=signal.symbol,
        direction=signal.direction,
        status="PENDING",
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        risk_amount=risk_amount,
        position_size=position_size,
    )
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return trade


def _close_trade(db: Session, trade: PaperTrade, exit_price: float, result: str, r_multiple: float) -> dict:
    account = ensure_paper_account(db)
    old_balance = Decimal(str(account.balance))
    risk_amount = Decimal(str(trade.risk_amount or 0))
    pnl = risk_amount * Decimal(str(r_multiple))
    new_balance = old_balance + pnl

    trade.status = "CLOSED"
    trade.result = result
    trade.exit_time = utc_now()
    trade.exit_price = Decimal(str(exit_price))
    trade.pnl_amount = pnl
    trade.r_multiple = Decimal(str(r_multiple))
    trade.updated_at = utc_now()

    account.balance = new_balance
    account.equity = new_balance
    account.updated_at = utc_now()

    db.commit()
    db.refresh(trade)
    db.refresh(account)

    return {
        "event": "closed",
        "trade": trade,
        "account": account,
        "old_balance": float(old_balance),
        "new_balance": float(new_balance),
        "pnl": float(pnl),
        "r_multiple": float(r_multiple),
        "result": result,
    }


def close_trade_manually(db: Session, trade: PaperTrade, current_price: float) -> dict:
    entry = float(trade.entry_price)
    stop = float(trade.stop_loss)
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        r_multiple = 0.0
    elif trade.direction == "BUY":
        r_multiple = (current_price - entry) / risk_per_unit
    else:
        r_multiple = (entry - current_price) / risk_per_unit

    return _close_trade(db, trade, current_price, "MANUAL_CLOSE", round(r_multiple, 2))


def monitor_trades(db: Session, symbol: str, current_price: float) -> list[dict]:
    events: list[dict] = []
    trades = get_open_trades(db)

    for trade in trades:
        if trade.symbol != symbol:
            continue

        entry = float(trade.entry_price)
        stop = float(trade.stop_loss)
        take = float(trade.take_profit)

        if trade.status == "PENDING":
            triggered = False
            if trade.direction == "BUY" and current_price <= entry:
                triggered = True
            elif trade.direction == "SELL" and current_price >= entry:
                triggered = True

            if triggered:
                trade.status = "ACTIVE"
                trade.entry_time = utc_now()
                trade.updated_at = utc_now()
                db.commit()
                db.refresh(trade)
                events.append({"event": "entry_triggered", "trade": trade})

        if trade.status == "ACTIVE":
            if trade.direction == "BUY":
                if current_price <= stop:
                    events.append(_close_trade(db, trade, stop, "LOSS", -1.0))
                elif current_price >= take:
                    events.append(_close_trade(db, trade, take, "WIN", 2.0))
            else:
                if current_price >= stop:
                    events.append(_close_trade(db, trade, stop, "LOSS", -1.0))
                elif current_price <= take:
                    events.append(_close_trade(db, trade, take, "WIN", 2.0))

    return events


def trades_today_count(db: Session) -> int:
    start_ny = datetime.combine(today_ny(), datetime.min.time(), tzinfo=NY)
    start_utc = start_ny.astimezone(UTC).replace(tzinfo=None)
    return db.query(PaperTrade).filter(PaperTrade.created_at >= start_utc).count()


def losses_today_count(db: Session) -> int:
    start_ny = datetime.combine(today_ny(), datetime.min.time(), tzinfo=NY)
    start_utc = start_ny.astimezone(UTC).replace(tzinfo=None)
    return (
        db.query(PaperTrade)
        .filter(PaperTrade.created_at >= start_utc, PaperTrade.result == "LOSS")
        .count()
    )
