from fastapi import APIRouter, HTTPException

from app.alerts.telegram_alerts import TelegramAlert, format_signal_alert
from app.config import get_settings

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post("/test")
def send_test_alert():
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
    if not sent and settings.telegram_enabled:
        raise HTTPException(status_code=500, detail="Telegram alert failed. Check token/chat_id.")
    return {"sent": sent, "telegram_enabled": settings.telegram_enabled, "message": "Test alert processed."}
