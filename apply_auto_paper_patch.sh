#!/usr/bin/env bash
set -e

if [ ! -d "app" ] || [ ! -f "app/models.py" ]; then
  echo "Run this from the ny_open_fvg_bot project root."
  exit 1
fi

mkdir -p app/paper app/services scripts

# Patch models.py with PaperAccount and BotState if missing
python - <<'PY'
from pathlib import Path
p = Path('app/models.py')
text = p.read_text()
append = r'''


class PaperAccount(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), default="default", index=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    starting_balance: Mapped[float] = mapped_column(Numeric(18, 2), default=1000)
    balance: Mapped[float] = mapped_column(Numeric(18, 2), default=1000)
    equity: Mapped[float] = mapped_column(Numeric(18, 2), default=1000)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BotState(Base):
    __tablename__ = "bot_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), index=True)
    value: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
'''
if 'class PaperAccount(Base)' not in text:
    text += append
    p.write_text(text)
    print('Patched app/models.py with PaperAccount and BotState')
else:
    print('app/models.py already has PaperAccount')
PY

# Create paper account helper
cat > app/paper/auto_paper.py <<'PY'
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
PY

# Auto paper strategy runner
cat > scripts/run_auto_paper_once.py <<'PY'
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.alerts.telegram_alerts import TelegramAlert, format_signal_alert
from app.db import SessionLocal
from app.models import Candle, Signal
from app.paper.auto_paper import (
    create_trade_from_signal,
    ensure_paper_account,
    get_latest_candle,
    get_latest_price,
    get_open_trades,
    is_paused,
    is_stopped_today,
    losses_today_count,
    monitor_trades,
    trades_today_count,
)
from app.risk.risk_manager import RiskManager
from app.strategy.fvg_detector import detect_fvg_at
from app.strategy.sweep_detector import detect_sweep

load_dotenv()

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def send(msg: str):
    TelegramAlert().send_message(msg)


def ny_to_utc_naive(dt):
    return dt.astimezone(UTC).replace(tzinfo=None)


def get_range(db, symbol, timeframe, start_utc, end_utc):
    candles = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol,
            Candle.timeframe == timeframe,
            Candle.candle_time >= start_utc,
            Candle.candle_time < end_utc,
        )
        .order_by(Candle.candle_time.asc())
        .all()
    )
    if not candles:
        return None
    return {
        "count": len(candles),
        "high": max(float(c.high) for c in candles),
        "low": min(float(c.low) for c in candles),
        "first": candles[0].candle_time,
        "last": candles[-1].candle_time,
    }


def get_candles(db, symbol, timeframe, start_utc, end_utc):
    rows = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol,
            Candle.timeframe == timeframe,
            Candle.candle_time >= start_utc,
            Candle.candle_time <= end_utc,
        )
        .order_by(Candle.candle_time.asc())
        .all()
    )
    return [
        {
            "candle_time": c.candle_time,
            "time": c.candle_time,
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume or 0),
        }
        for c in rows
    ]


def notify_trade_created(signal, trade, account):
    msg = format_signal_alert(
        {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "setup_type": signal.setup_type,
            "entry_price": float(signal.entry_price),
            "stop_loss": float(signal.stop_loss),
            "take_profit": float(signal.take_profit),
            "risk_percent": os.getenv("RISK_PER_TRADE_PERCENT", "0.5"),
            "session_high": float(signal.session_high) if signal.session_high is not None else None,
            "session_low": float(signal.session_low) if signal.session_low is not None else None,
            "opening_range_high": float(signal.opening_range_high),
            "opening_range_low": float(signal.opening_range_low),
            "sweep_level": signal.sweep_level,
            "fvg_low": float(signal.fvg_low),
            "fvg_high": float(signal.fvg_high),
            "mode": "AUTO_PAPER",
        }
    )
    msg += f"\n\n<b>Paper trade:</b> Created automatically"
    msg += f"\n<b>Status:</b> PENDING ENTRY"
    msg += f"\n<b>Risk amount:</b> ${float(trade.risk_amount):.2f}"
    msg += f"\n<b>Paper balance:</b> ${float(account.balance):.2f}"
    send(msg)


def notify_trade_event(event):
    trade = event["trade"]
    if event["event"] == "entry_triggered":
        send(
            f"✅ <b>Paper Entry Triggered</b>\n\n"
            f"<b>Symbol:</b> {trade.symbol}\n"
            f"<b>Direction:</b> {trade.direction}\n"
            f"<b>Entry:</b> {float(trade.entry_price):.2f}\n"
            f"<b>SL:</b> {float(trade.stop_loss):.2f}\n"
            f"<b>TP:</b> {float(trade.take_profit):.2f}"
        )
    elif event["event"] == "closed":
        emoji = "🎯" if event["result"] == "WIN" else "❌"
        send(
            f"{emoji} <b>Paper Trade Closed</b>\n\n"
            f"<b>Symbol:</b> {trade.symbol}\n"
            f"<b>Direction:</b> {trade.direction}\n"
            f"<b>Result:</b> {event['result']}\n"
            f"<b>R Multiple:</b> {event['r_multiple']:.2f}R\n"
            f"<b>P/L:</b> ${event['pnl']:.2f}\n"
            f"<b>Old Balance:</b> ${event['old_balance']:.2f}\n"
            f"<b>New Balance:</b> ${event['new_balance']:.2f}"
        )


def main():
    symbol = os.getenv("CAPITAL_EPIC", "US100")
    risk_percent = float(os.getenv("RISK_PER_TRADE_PERCENT", "0.5"))
    max_trades_per_day = int(os.getenv("MAX_TRADES_PER_DAY", "1"))
    max_losses_per_day = int(os.getenv("MAX_LOSSES_PER_DAY", "2"))
    sweep_buffer = float(os.getenv("SWEEP_BUFFER_POINTS", "0"))
    stop_buffer = float(os.getenv("STOP_BUFFER_POINTS", "2"))

    db = SessionLocal()

    try:
        account = ensure_paper_account(db)
        latest_candle = get_latest_candle(db, symbol)
        latest_price = get_latest_price(db, symbol)

        if not latest_candle or latest_price is None:
            print("No latest candle/price available.")
            return

        # Always monitor existing trades first.
        events = monitor_trades(db, symbol, latest_price)
        for event in events:
            notify_trade_event(event)

        latest_utc = latest_candle.candle_time.replace(tzinfo=UTC)
        latest_ny = latest_utc.astimezone(NY)
        session_date = latest_ny.date()

        print("Latest NY time:", latest_ny.strftime("%Y-%m-%d %H:%M:%S %Z"))
        print("Latest price:", latest_price)
        print("Paper balance:", float(account.balance))

        if is_paused(db):
            print("Trading paused. Monitoring only.")
            return

        if is_stopped_today(db):
            print("Trading stopped for today. Monitoring only.")
            return

        if get_open_trades(db):
            print("Open/pending trade exists. No new trade.")
            return

        if trades_today_count(db) >= max_trades_per_day:
            print("Max trades per day reached.")
            return

        if losses_today_count(db) >= max_losses_per_day:
            print("Max losses per day reached.")
            send("🛑 <b>Daily Risk Limit Hit</b>\n\nNo more AUTO_PAPER trades today.")
            return

        if latest_ny.time() < time(9, 45):
            print("Before 09:45 NY. Strategy not active yet.")
            return

        if latest_ny.time() > time(11, 30):
            print("After 11:30 NY. No new trades.")
            return

        overnight_start_ny = datetime.combine(session_date - timedelta(days=1), time(18, 0), tzinfo=NY)
        overnight_end_ny = datetime.combine(session_date, time(9, 30), tzinfo=NY)
        overnight = get_range(db, symbol, "M1", ny_to_utc_naive(overnight_start_ny), ny_to_utc_naive(overnight_end_ny))

        if latest_ny.time() >= time(10, 0):
            range_start_ny = datetime.combine(session_date, time(9, 30), tzinfo=NY)
            range_end_ny = datetime.combine(session_date, time(10, 0), tzinfo=NY)
            range_name = "30-min opening range"
        else:
            range_start_ny = datetime.combine(session_date, time(9, 30), tzinfo=NY)
            range_end_ny = datetime.combine(session_date, time(9, 45), tzinfo=NY)
            range_name = "15-min opening range"

        opening_range = get_range(db, symbol, "M1", ny_to_utc_naive(range_start_ny), ny_to_utc_naive(range_end_ny))
        if not opening_range:
            print("Opening range not ready.")
            return

        scan_start_utc = ny_to_utc_naive(range_end_ny)
        scan_end_utc = latest_candle.candle_time
        candles = get_candles(db, symbol, "M5", scan_start_utc, scan_end_utc)

        if len(candles) < 5:
            print("Not enough M5 candles to scan.")
            return

        found = None
        for i, candle in enumerate(candles):
            sweep = detect_sweep(candle, opening_range["high"], opening_range["low"], buffer=sweep_buffer)
            if not sweep:
                continue

            for j in range(max(i + 2, 2), len(candles)):
                fvg = detect_fvg_at(candles, j)
                if fvg and fvg.direction == sweep.direction:
                    found = (sweep, fvg, candles[j])
                    break

            if found:
                break

        if not found:
            print("No sweep + FVG setup found.")
            return

        sweep, fvg, fvg_candle = found
        plan = RiskManager().build_trade_plan(symbol, sweep.direction, fvg.midpoint, sweep.sweep_price, buffer=stop_buffer)

        existing_signal = (
            db.query(Signal)
            .filter(
                Signal.symbol == symbol,
                Signal.direction == sweep.direction,
                Signal.signal_time == fvg_candle["candle_time"],
                Signal.setup_type == f"NY Open Sweep + FVG AUTO_PAPER ({range_name})",
            )
            .first()
        )

        if existing_signal:
            print("Signal already exists. No duplicate.")
            return

        signal = Signal(
            symbol=symbol,
            signal_time=fvg_candle["candle_time"],
            direction=sweep.direction,
            setup_type=f"NY Open Sweep + FVG AUTO_PAPER ({range_name})",
            status="DETECTED",
            session_high=overnight["high"] if overnight else None,
            session_low=overnight["low"] if overnight else None,
            opening_range_high=opening_range["high"],
            opening_range_low=opening_range["low"],
            sweep_level=sweep.sweep_level,
            sweep_price=sweep.sweep_price,
            fvg_low=fvg.fvg_low,
            fvg_high=fvg.fvg_high,
            entry_price=plan.entry_price,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            risk_reward=plan.risk_reward,
            mode="AUTO_PAPER",
        )
        db.add(signal)
        db.commit()
        db.refresh(signal)

        trade = create_trade_from_signal(db, signal, risk_percent)
        account = ensure_paper_account(db)
        notify_trade_created(signal, trade, account)
        print("Auto paper trade created.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
PY

# Auto paper loop
cat > scripts/auto_paper_loop.py <<'PY'
import subprocess
import time
from datetime import datetime


def run_command(command):
    print("\n" + "=" * 80)
    print("Running:", " ".join(command))
    print("Time:", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))
    print("=" * 80)

    result = subprocess.run(command, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)


def main():
    print("Starting AUTO_PAPER loop.")
    print("Press CTRL + C to stop.")

    while True:
        run_command(["python", "scripts/sync_capital_candles.py"])
        run_command(["python", "scripts/build_m5_candles.py"])
        run_command(["python", "scripts/run_auto_paper_once.py"])
        print("\nSleeping for 60 seconds...")
        time.sleep(60)


if __name__ == "__main__":
    main()
PY

# Telegram command loop
cat > scripts/telegram_command_loop.py <<'PY'
import os
import sys
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.db import SessionLocal
from app.models import Candle, PaperTrade
from app.paper.auto_paper import (
    cancel_pending_trades,
    close_trade_manually,
    ensure_paper_account,
    get_latest_candle,
    get_latest_price,
    get_open_trades,
    is_paused,
    is_stopped_today,
    reset_paper_account,
    set_paused,
    stop_trading_today,
    trades_today_count,
)

load_dotenv()
NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

settings = get_settings()
TOKEN = settings.telegram_bot_token
ALLOWED_CHAT_ID = str(settings.telegram_chat_id)


def send(chat_id, message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    requests.post(url, json=payload, timeout=10)


def ny_to_utc_naive(dt):
    return dt.astimezone(UTC).replace(tzinfo=None)


def levels_text(db, symbol):
    latest = get_latest_candle(db, symbol)
    if not latest:
        return "No candles found."

    latest_utc = latest.candle_time.replace(tzinfo=UTC)
    latest_ny = latest_utc.astimezone(NY)
    session_date = latest_ny.date()

    def range_query(start_ny, end_ny):
        candles = (
            db.query(Candle)
            .filter(
                Candle.symbol == symbol,
                Candle.timeframe == "M1",
                Candle.candle_time >= ny_to_utc_naive(start_ny),
                Candle.candle_time < ny_to_utc_naive(end_ny),
            )
            .all()
        )
        if not candles:
            return None
        return max(float(c.high) for c in candles), min(float(c.low) for c in candles), len(candles)

    overnight = range_query(
        datetime.combine(session_date - timedelta(days=1), dtime(18, 0), tzinfo=NY),
        datetime.combine(session_date, dtime(9, 30), tzinfo=NY),
    )
    ny15 = range_query(
        datetime.combine(session_date, dtime(9, 30), tzinfo=NY),
        datetime.combine(session_date, dtime(9, 45), tzinfo=NY),
    )
    ny30 = range_query(
        datetime.combine(session_date, dtime(9, 30), tzinfo=NY),
        datetime.combine(session_date, dtime(10, 0), tzinfo=NY),
    )

    def fmt(label, item):
        if not item:
            return f"<b>{label}:</b> Not ready"
        high, low, count = item
        return f"<b>{label}:</b> High {high:.2f} / Low {low:.2f} / Candles {count}"

    return (
        f"📊 <b>{symbol} NY Levels</b>\n\n"
        f"<b>Latest NY time:</b> {latest_ny.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
        f"<b>Current price:</b> {float(latest.close):.2f}\n\n"
        f"{fmt('Overnight', overnight)}\n"
        f"{fmt('NY 15m', ny15)}\n"
        f"{fmt('NY 30m', ny30)}"
    )


def status_text(db):
    symbol = os.getenv("CAPITAL_EPIC", "US100")
    account = ensure_paper_account(db)
    latest = get_latest_candle(db, symbol)
    open_trades = get_open_trades(db)

    latest_text = "No candle"
    if latest:
        latest_utc = latest.candle_time.replace(tzinfo=UTC)
        latest_ny = latest_utc.astimezone(NY)
        latest_text = latest_ny.strftime("%Y-%m-%d %H:%M:%S %Z")

    return (
        f"🤖 <b>Bot Status</b>\n\n"
        f"<b>Mode:</b> AUTO_PAPER\n"
        f"<b>Symbol:</b> {symbol}\n"
        f"<b>Paused:</b> {is_paused(db)}\n"
        f"<b>Stopped today:</b> {is_stopped_today(db)}\n"
        f"<b>Latest candle NY:</b> {latest_text}\n"
        f"<b>Open/pending trades:</b> {len(open_trades)}\n"
        f"<b>Trades today:</b> {trades_today_count(db)}\n"
        f"<b>Paper balance:</b> ${float(account.balance):.2f}"
    )


def open_trades_text(db):
    trades = get_open_trades(db)
    if not trades:
        return "No open or pending paper trades."

    parts = ["📌 <b>Open/Pending Paper Trades</b>"]
    for t in trades:
        parts.append(
            f"\n<b>ID:</b> {t.id}\n"
            f"<b>Symbol:</b> {t.symbol}\n"
            f"<b>Direction:</b> {t.direction}\n"
            f"<b>Status:</b> {t.status}\n"
            f"<b>Entry:</b> {float(t.entry_price):.2f}\n"
            f"<b>SL:</b> {float(t.stop_loss):.2f}\n"
            f"<b>TP:</b> {float(t.take_profit):.2f}\n"
            f"<b>Risk:</b> ${float(t.risk_amount or 0):.2f}"
        )
    return "\n".join(parts)


def summary_text(db):
    account = ensure_paper_account(db)
    total = trades_today_count(db)
    wins = db.query(PaperTrade).filter(PaperTrade.result == "WIN").count()
    losses = db.query(PaperTrade).filter(PaperTrade.result == "LOSS").count()
    closed = db.query(PaperTrade).filter(PaperTrade.status == "CLOSED").count()
    return (
        f"📊 <b>Paper Summary</b>\n\n"
        f"<b>Trades today:</b> {total}\n"
        f"<b>Total closed:</b> {closed}\n"
        f"<b>Total wins:</b> {wins}\n"
        f"<b>Total losses:</b> {losses}\n"
        f"<b>Starting balance:</b> ${float(account.starting_balance):.2f}\n"
        f"<b>Current balance:</b> ${float(account.balance):.2f}"
    )


def handle_command(chat_id, text):
    db = SessionLocal()
    symbol = os.getenv("CAPITAL_EPIC", "US100")
    try:
        cmd = text.strip().split()[0].lower()

        if cmd == "/help":
            send(chat_id, """
<b>Available commands</b>

/status - Bot status
/pause - Pause new trades
/resume - Resume new trades
/stop_today - Stop trading for today
/open - Show open paper trade
/close - Close active paper trade at current price
/cancel - Cancel pending paper trades
/levels - Show NY levels
/summary - Show paper summary
/reset_paper - Ask to reset paper account
/confirm_reset - Confirm paper reset
/help - Show commands
""".strip())

        elif cmd == "/status":
            send(chat_id, status_text(db))

        elif cmd == "/pause":
            set_paused(db, True)
            send(chat_id, "⏸ <b>Bot paused</b>\n\nNo new paper trades will be opened.")

        elif cmd == "/resume":
            set_paused(db, False)
            send(chat_id, "▶️ <b>Bot resumed</b>\n\nAUTO_PAPER trades are allowed.")

        elif cmd == "/stop_today":
            stop_trading_today(db)
            send(chat_id, "🛑 <b>Trading stopped for today</b>\n\nPending paper trades cancelled.")

        elif cmd == "/open":
            send(chat_id, open_trades_text(db))

        elif cmd == "/cancel":
            count = cancel_pending_trades(db)
            send(chat_id, f"❌ Cancelled {count} pending paper trade(s).")

        elif cmd == "/close":
            trades = get_open_trades(db)
            active = [t for t in trades if t.status == "ACTIVE"]
            if not active:
                send(chat_id, "No active paper trade to close.")
            else:
                current_price = get_latest_price(db, symbol)
                event = close_trade_manually(db, active[0], current_price)
                send(
                    chat_id,
                    f"✅ <b>Paper trade manually closed</b>\n\n"
                    f"<b>Symbol:</b> {active[0].symbol}\n"
                    f"<b>Exit:</b> {current_price:.2f}\n"
                    f"<b>Result:</b> {event['r_multiple']:.2f}R\n"
                    f"<b>New Balance:</b> ${event['new_balance']:.2f}"
                )

        elif cmd == "/levels":
            send(chat_id, levels_text(db, symbol))

        elif cmd == "/summary":
            send(chat_id, summary_text(db))

        elif cmd == "/reset_paper":
            send(chat_id, "⚠️ <b>Confirm reset?</b>\n\nReply with /confirm_reset to reset paper balance and cancel open trades.")

        elif cmd == "/confirm_reset":
            account = reset_paper_account(db)
            send(chat_id, f"✅ <b>Paper account reset</b>\n\nBalance: ${float(account.balance):.2f}")

        else:
            send(chat_id, "Unknown command. Send /help")

    finally:
        db.close()


def main():
    if not TOKEN or not ALLOWED_CHAT_ID:
        raise SystemExit("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env")

    print("Starting Telegram command loop.")
    print("Allowed chat:", ALLOWED_CHAT_ID)
    print("Press CTRL + C to stop.")

    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset

            response = requests.get(url, params=params, timeout=40)
            data = response.json()

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = str(chat.get("id"))
                text = message.get("text", "")

                if not text:
                    continue

                if chat_id != ALLOWED_CHAT_ID:
                    print("Ignoring unauthorized chat:", chat_id)
                    continue

                print("Command:", text)
                handle_command(chat_id, text)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            print("Telegram command loop error:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
PY

# Append env defaults if missing
python - <<'PY'
from pathlib import Path
p = Path('.env')
if p.exists():
    text = p.read_text()
else:
    text = ''
adds = {
    'PAPER_START_BALANCE': '1000',
    'PAPER_CURRENCY': 'USD',
    'SWEEP_BUFFER_POINTS': '0',
    'STOP_BUFFER_POINTS': '2',
    'TRADING_MODE': 'AUTO_PAPER',
}
for k, v in adds.items():
    if f'{k}=' not in text:
        text += f'\n{k}={v}'
p.write_text(text)
print('Updated .env defaults')
PY

echo "Patch complete. Now run:"
echo "python scripts/create_tables.py"
echo "python scripts/auto_paper_loop.py"
echo "python scripts/telegram_command_loop.py"
