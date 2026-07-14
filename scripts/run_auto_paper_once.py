import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.alerts.telegram_alerts import TelegramAlert, format_signal_alert
from app.config import get_settings
from app.db import SessionLocal
from app.epics import STRATEGY_SWEEP_FVG_OPENING_RANGE, effective_risk, list_enabled_epics
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
    stop_trading_today,
    total_open_risk_percent,
    trades_today_count,
)
from app.risk.risk_manager import RiskManager
from app.strategy.fvg_detector import detect_fvg_at
from app.strategy.sweep_detector import detect_sweep

load_dotenv()

UTC = ZoneInfo("UTC")


def send(msg: str):
    TelegramAlert().send_message(msg)


def local_to_utc_naive(dt):
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


def notify_trade_created(signal, trade, account, risk_percent):
    msg = format_signal_alert(
        {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "setup_type": signal.setup_type,
            "entry_price": float(signal.entry_price),
            "stop_loss": float(signal.stop_loss),
            "take_profit": float(signal.take_profit),
            "risk_percent": risk_percent,
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


def portfolio_risk_available(db, account, settings, risk_percent) -> bool:
    current = total_open_risk_percent(db, account)
    return (current + risk_percent) <= settings.max_portfolio_risk_percent


def run_opening_range_strategy(db, cfg, settings, sweep_buffer, stop_buffer, account, latest_candle, latest_local, session_date):
    epic = cfg.epic
    TZ = ZoneInfo(cfg.timezone)
    trade_start = cfg.trade_start
    trade_end = cfg.trade_end
    range_short_start, range_short_end = cfg.range_short_start, cfg.range_short_end
    range_long_start, range_long_end = cfg.range_long_start, cfg.range_long_end

    if latest_local.time() < trade_start:
        print(f"[{epic}/{cfg.strategy}] Before {trade_start}. Strategy not active yet.")
        return
    if latest_local.time() > trade_end:
        print(f"[{epic}/{cfg.strategy}] After {trade_end}. No new trades.")
        return

    overnight_start_local = datetime.combine(session_date - timedelta(days=1), time(18, 0), tzinfo=TZ)
    overnight_end_local = datetime.combine(session_date, range_short_start, tzinfo=TZ)
    overnight = get_range(db, epic, "M1", local_to_utc_naive(overnight_start_local), local_to_utc_naive(overnight_end_local))

    if latest_local.time() >= range_long_end:
        range_start_local = datetime.combine(session_date, range_long_start, tzinfo=TZ)
        range_end_local = datetime.combine(session_date, range_long_end, tzinfo=TZ)
        range_name = "30-min opening range"
    else:
        range_start_local = datetime.combine(session_date, range_short_start, tzinfo=TZ)
        range_end_local = datetime.combine(session_date, range_short_end, tzinfo=TZ)
        range_name = "15-min opening range"

    opening_range = get_range(db, epic, "M1", local_to_utc_naive(range_start_local), local_to_utc_naive(range_end_local))
    if not opening_range:
        print(f"[{epic}/{cfg.strategy}] Opening range not ready.")
        return

    scan_start_utc = local_to_utc_naive(range_end_local)
    scan_end_utc = latest_candle.candle_time
    candles = get_candles(db, epic, "M5", scan_start_utc, scan_end_utc)

    if len(candles) < 5:
        print(f"[{epic}/{cfg.strategy}] Not enough M5 candles to scan.")
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
        print(f"[{epic}/{cfg.strategy}] No sweep + FVG setup found.")
        return

    sweep, fvg, fvg_candle = found
    risk_percent, _, _ = effective_risk(cfg, settings)
    plan = RiskManager().build_trade_plan(epic, sweep.direction, fvg.midpoint, sweep.sweep_price, buffer=stop_buffer)

    if not portfolio_risk_available(db, account, settings, risk_percent):
        print(f"[{epic}/{cfg.strategy}] Portfolio risk ceiling reached. Skipping trade.")
        return

    setup_type = f"{cfg.session_name} Sweep + FVG AUTO_PAPER ({range_name})"
    existing_signal = (
        db.query(Signal)
        .filter(
            Signal.symbol == epic,
            Signal.strategy == cfg.strategy,
            Signal.direction == sweep.direction,
            Signal.signal_time == fvg_candle["candle_time"],
            Signal.setup_type == setup_type,
        )
        .first()
    )
    if existing_signal:
        print(f"[{epic}/{cfg.strategy}] Signal already exists. No duplicate.")
        return

    signal = Signal(
        symbol=epic,
        strategy=cfg.strategy,
        signal_time=fvg_candle["candle_time"],
        direction=sweep.direction,
        setup_type=setup_type,
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
    notify_trade_created(signal, trade, account, risk_percent)
    print(f"[{epic}/{cfg.strategy}] Auto paper trade created.")


STRATEGY_HANDLERS = {
    STRATEGY_SWEEP_FVG_OPENING_RANGE: run_opening_range_strategy,
}


def main():
    settings = get_settings()
    sweep_buffer = float(os.getenv("SWEEP_BUFFER_POINTS", "0"))
    stop_buffer = float(os.getenv("STOP_BUFFER_POINTS", "2"))

    db = SessionLocal()

    try:
        account = ensure_paper_account(db)
        epic_configs = list_enabled_epics(db)

        for cfg in epic_configs:
            epic = cfg.epic
            strategy = cfg.strategy
            risk_percent, max_trades_per_day, max_losses_per_day = effective_risk(cfg, settings)

            TZ = ZoneInfo(cfg.timezone)

            latest_candle = get_latest_candle(db, epic)
            latest_price = get_latest_price(db, epic)

            if not latest_candle or latest_price is None:
                print(f"[{epic}/{strategy}] No latest candle/price. Skipping.")
                continue

            events = monitor_trades(db, epic, latest_price)
            for event in events:
                notify_trade_event(event)

            latest_utc = latest_candle.candle_time.replace(tzinfo=UTC)
            latest_local = latest_utc.astimezone(TZ)
            session_date = latest_local.date()

            print(f"[{epic}/{strategy}] Latest local time: {latest_local.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            print(f"[{epic}/{strategy}] Paper balance: {float(account.balance)}")

            if is_paused(db):
                print(f"[{epic}/{strategy}] Trading paused. Monitoring only.")
                continue

            if is_stopped_today(db) or is_stopped_today(db, epic, strategy):
                print(f"[{epic}/{strategy}] Stopped for today. Monitoring only.")
                continue

            if get_open_trades(db, epic, strategy):
                print(f"[{epic}/{strategy}] Open/pending trade exists. No new trade.")
                continue

            if trades_today_count(db, epic, strategy) >= max_trades_per_day:
                print(f"[{epic}/{strategy}] Max trades per day reached.")
                continue

            if losses_today_count(db, epic, strategy) >= max_losses_per_day:
                print(f"[{epic}/{strategy}] Max losses per day reached.")
                stop_trading_today(db, epic, strategy)
                send(f"🛑 <b>Daily Risk Limit Hit ({epic} / {strategy})</b>\n\nNo more AUTO_PAPER trades today for {epic} on {strategy}.")
                continue

            handler = STRATEGY_HANDLERS.get(strategy)
            if handler is None:
                print(f"[{epic}/{strategy}] Unknown strategy. Skipping.")
                continue

            handler(db, cfg, settings, sweep_buffer, stop_buffer, account, latest_candle, latest_local, session_date)

    finally:
        db.close()


if __name__ == "__main__":
    main()
