import html
from typing import Any

import requests

from app.config import get_settings


class TelegramAlert:
    """Small Telegram Bot API client for alert messages."""

    def __init__(self):
        self.settings = get_settings()
        self.enabled = self.settings.telegram_enabled
        self.bot_token = self.settings.telegram_bot_token
        self.chat_id = self.settings.telegram_chat_id

        if self.enabled and (not self.bot_token or not self.chat_id):
            raise ValueError("Telegram is enabled but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing.")

    def send_message(self, message: str) -> bool:
        if not self.enabled:
            print("[TELEGRAM DISABLED]", message)
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            print(f"[TELEGRAM ERROR] {exc}")
            return False


def safe(value: Any) -> str:
    if value is None:
        return "-"
    return html.escape(str(value))


def format_signal_alert(signal: dict) -> str:
    return f"""
🚨 <b>Trading Setup Detected</b>

<b>Symbol:</b> {safe(signal.get("symbol"))}
<b>Direction:</b> {safe(signal.get("direction"))}
<b>Setup:</b> {safe(signal.get("setup_type"))}

<b>Entry:</b> {safe(signal.get("entry_price"))}
<b>Stop Loss:</b> {safe(signal.get("stop_loss"))}
<b>Take Profit:</b> {safe(signal.get("take_profit"))}
<b>Risk:</b> {safe(signal.get("risk_percent"))}%

<b>Session High:</b> {safe(signal.get("session_high"))}
<b>Session Low:</b> {safe(signal.get("session_low"))}

<b>Opening Range High:</b> {safe(signal.get("opening_range_high"))}
<b>Opening Range Low:</b> {safe(signal.get("opening_range_low"))}

<b>Sweep Level:</b> {safe(signal.get("sweep_level"))}
<b>FVG:</b> {safe(signal.get("fvg_low"))} - {safe(signal.get("fvg_high"))}

<b>Mode:</b> {safe(signal.get("mode", "Paper Trading"))}
""".strip()


def format_trade_update(trade: dict) -> str:
    return f"""
📌 <b>Trade Update</b>

<b>Symbol:</b> {safe(trade.get("symbol"))}
<b>Direction:</b> {safe(trade.get("direction"))}
<b>Status:</b> {safe(trade.get("status"))}

<b>Entry:</b> {safe(trade.get("entry_price"))}
<b>Current Price:</b> {safe(trade.get("current_price"))}
<b>SL:</b> {safe(trade.get("stop_loss"))}
<b>TP:</b> {safe(trade.get("take_profit"))}

<b>P/L:</b> {safe(trade.get("pnl"))}
<b>R Multiple:</b> {safe(trade.get("r_multiple"))}
""".strip()


def format_daily_summary(summary: dict) -> str:
    return f"""
📊 <b>Daily Trading Summary</b>

<b>Date:</b> {safe(summary.get("date"))}
<b>Symbol:</b> {safe(summary.get("symbol"))}

<b>Total Setups:</b> {safe(summary.get("total_setups"))}
<b>Total Trades:</b> {safe(summary.get("total_trades"))}
<b>Wins:</b> {safe(summary.get("wins"))}
<b>Losses:</b> {safe(summary.get("losses"))}

<b>Net R:</b> {safe(summary.get("net_r"))}
<b>Win Rate:</b> {safe(summary.get("win_rate"))}%
<b>Notes:</b> {safe(summary.get("notes"))}
""".strip()


def format_error_alert(error_message: str) -> str:
    return f"""
⚠️ <b>Bot Error</b>

{safe(error_message)}
""".strip()
