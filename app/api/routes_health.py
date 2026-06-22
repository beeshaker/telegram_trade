from fastapi import APIRouter

from app.config import get_settings
from app.db import database_is_connected

router = APIRouter(prefix="", tags=["health"])


@router.get("/health")
def health_check():
    settings = get_settings()
    return {
        "status": "ok",
        "app_name": settings.app_name,
        "environment": settings.environment,
        "mode": settings.trading_mode,
        "live_trading_allowed": settings.live_trading_allowed(),
        "telegram_enabled": settings.telegram_enabled,
        "database_connected": database_is_connected(),
    }
