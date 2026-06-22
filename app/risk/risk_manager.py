from dataclasses import dataclass
from typing import Literal

from app.config import get_settings


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    direction: Literal["BUY", "SELL"]
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    risk_percent: float


class RiskManager:
    def __init__(self):
        self.settings = get_settings()

    def build_trade_plan(self, symbol: str, direction: Literal["BUY", "SELL"], entry_price: float, sweep_price: float, buffer: float = 0.0) -> TradePlan:
        risk_percent = self.settings.risk_per_trade_percent
        min_rr = self.settings.min_risk_reward

        if direction == "BUY":
            stop_loss = sweep_price - buffer
            risk = entry_price - stop_loss
            take_profit = entry_price + (risk * min_rr)
        else:
            stop_loss = sweep_price + buffer
            risk = stop_loss - entry_price
            take_profit = entry_price - (risk * min_rr)

        if risk <= 0:
            raise ValueError("Invalid trade plan: risk must be greater than zero.")

        return TradePlan(
            symbol=symbol,
            direction=direction,
            entry_price=round(entry_price, 5),
            stop_loss=round(stop_loss, 5),
            take_profit=round(take_profit, 5),
            risk_reward=min_rr,
            risk_percent=risk_percent,
        )

    def is_valid(self, plan: TradePlan) -> bool:
        if plan.risk_reward < self.settings.min_risk_reward:
            return False
        if self.settings.trading_mode == "LIVE" and not self.settings.live_trading_allowed():
            return False
        return True
