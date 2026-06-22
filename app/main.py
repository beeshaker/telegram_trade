from fastapi import FastAPI

from app.api.routes_alerts import router as alerts_router
from app.api.routes_health import router as health_router
from app.api.routes_signals import router as signals_router
from app.api.routes_trades import router as trades_router
from app.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")

app.include_router(health_router)
app.include_router(alerts_router)
app.include_router(signals_router)
app.include_router(trades_router)


@app.get("/")
def root():
    return {
        "app": settings.app_name,
        "mode": settings.trading_mode,
        "message": "NY Open Liquidity Sweep + FVG Bot API is running.",
    }
