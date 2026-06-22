from fastapi import APIRouter, Depends
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Signal

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("")
def list_signals(limit: int = 50, db: Session = Depends(get_db)):
    signals = db.query(Signal).order_by(desc(Signal.signal_time)).limit(limit).all()
    return [
        {
            "id": s.id,
            "symbol": s.symbol,
            "signal_time": s.signal_time,
            "direction": s.direction,
            "setup_type": s.setup_type,
            "status": s.status,
            "entry_price": float(s.entry_price) if s.entry_price is not None else None,
            "stop_loss": float(s.stop_loss) if s.stop_loss is not None else None,
            "take_profit": float(s.take_profit) if s.take_profit is not None else None,
            "telegram_sent": s.telegram_sent,
        }
        for s in signals
    ]
