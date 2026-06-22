from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import PaperTrade, Signal


class PaperBroker:
    def __init__(self, starting_balance: float = 10000.0, risk_percent: float = 0.5):
        self.starting_balance = starting_balance
        self.risk_percent = risk_percent

    def create_trade_from_signal(self, db: Session, signal: Signal) -> PaperTrade:
        risk_amount = Decimal(str(self.starting_balance * (self.risk_percent / 100)))
        trade = PaperTrade(
            signal_id=signal.id,
            symbol=signal.symbol,
            direction=signal.direction,
            status="PENDING",
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            risk_amount=risk_amount,
        )
        db.add(trade)
        db.commit()
        db.refresh(trade)
        return trade

    def update_trade_with_price(self, db: Session, trade: PaperTrade, price: float, current_time: datetime | None = None) -> PaperTrade:
        current_time = current_time or datetime.utcnow()
        price_dec = Decimal(str(price))

        if trade.status == "PENDING":
            if trade.direction == "BUY" and price_dec <= trade.entry_price:
                trade.status = "ACTIVE"
                trade.entry_time = current_time
            elif trade.direction == "SELL" and price_dec >= trade.entry_price:
                trade.status = "ACTIVE"
                trade.entry_time = current_time

        if trade.status == "ACTIVE":
            if trade.direction == "BUY":
                if price_dec <= trade.stop_loss:
                    trade.status = "CLOSED"
                    trade.result = "LOSS"
                    trade.exit_price = trade.stop_loss
                    trade.exit_time = current_time
                    trade.r_multiple = Decimal("-1")
                elif price_dec >= trade.take_profit:
                    trade.status = "CLOSED"
                    trade.result = "WIN"
                    trade.exit_price = trade.take_profit
                    trade.exit_time = current_time
                    trade.r_multiple = Decimal("2")
            else:
                if price_dec >= trade.stop_loss:
                    trade.status = "CLOSED"
                    trade.result = "LOSS"
                    trade.exit_price = trade.stop_loss
                    trade.exit_time = current_time
                    trade.r_multiple = Decimal("-1")
                elif price_dec <= trade.take_profit:
                    trade.status = "CLOSED"
                    trade.result = "WIN"
                    trade.exit_price = trade.take_profit
                    trade.exit_time = current_time
                    trade.r_multiple = Decimal("2")

        trade.updated_at = current_time
        db.commit()
        db.refresh(trade)
        return trade
