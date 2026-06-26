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
    losses_today_count,
    reset_paper_account,
    set_paused,
    stop_trading_today,
    trades_today_count,
)

load_dotenv()
NY = ZoneInfo("America/New_York")


def parse_epics() -> list[str]:
    multi = os.getenv("CAPITAL_EPICS", "")
    if multi:
        return [e.strip() for e in multi.split(",") if e.strip()]
    return [os.getenv("CAPITAL_EPIC", "US100")]


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
    epics = parse_epics()
    account = ensure_paper_account(db)
    paused = is_paused(db)

    EPIC_DISPLAY = {
        "NATURALGAS": "NATGAS",
    }
    SESSION_DISPLAY = {
        "US100": "NY Open",
        "NATURALGAS": "NY Open",
        "UK100": "London",
        "GOLD": "London",
        "USDJPY": "Tokyo",
    }

    lines = [
        "🤖 <b>Bot Status</b>",
        "",
        f"Balance: ${float(account.balance):.2f}",
        f"Mode: AUTO_PAPER | Paused: {paused}",
        "",
    ]

    for epic in epics:
        display = EPIC_DISPLAY.get(epic, epic)
        session = SESSION_DISPLAY.get(epic, "")
        t = trades_today_count(db, epic)
        l = losses_today_count(db, epic)
        lines.append(f"{display:<8} | {session:<10} | Trades: {t} | Losses: {l}")

    return "\n".join(lines)


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
