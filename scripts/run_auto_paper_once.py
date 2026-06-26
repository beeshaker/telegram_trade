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

        if latest_ny.time() > time(10, 30):
            print("After 10:30 NY. No new trades.")
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
