from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "NY_OPEN_FVG_BOT"
    environment: str = "development"

    database_url: str = "sqlite:///./trading_bot.db"

    trading_mode: Literal["BACKTEST", "PAPER", "LIVE"] = "PAPER"
    live_trading_confirm: bool = False

    default_symbol: str = "NAS100"
    default_timezone: str = "America/New_York"

    risk_per_trade_percent: float = Field(default=0.5, ge=0.01, le=10)
    max_trades_per_day: int = Field(default=1, ge=1)
    max_losses_per_day: int = Field(default=2, ge=1)
    min_risk_reward: float = Field(default=2.0, ge=0.1)

    telegram_enabled: bool = False
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    mt5_enabled: bool = False
    mt5_login: Optional[int] = None
    mt5_password: Optional[str] = None
    mt5_server: Optional[str] = None
    mt5_path: Optional[str] = None

    def live_trading_allowed(self) -> bool:
        """Hard gate to prevent accidental live trading."""
        return self.trading_mode == "LIVE" and self.live_trading_confirm is True and self.mt5_enabled is True


@lru_cache
def get_settings() -> Settings:
    return Settings()
