import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.alerts.telegram_alerts import TelegramAlert, format_signal_alert
from app.config import get_settings

settings = get_settings()

signal = {
    "symbol": settings.default_symbol,
    "direction": "SELL",
    "setup_type": "NY Open Sweep + Bearish FVG",
    "entry_price": 18420.5,
    "stop_loss": 18455.0,
    "take_profit": 18350.0,
    "risk_percent": settings.risk_per_trade_percent,
    "session_high": 18480.0,
    "session_low": 18290.0,
    "opening_range_high": 18410.0,
    "opening_range_low": 18340.0,
    "sweep_level": "15-min range high",
    "fvg_low": 18405.0,
    "fvg_high": 18418.0,
    "mode": settings.trading_mode,
}

sent = TelegramAlert().send_message(format_signal_alert(signal))
print("Telegram alert sent successfully." if sent else "Telegram alert not sent. Check TELEGRAM_ENABLED/token/chat_id.")
