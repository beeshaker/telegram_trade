from fastapi import APIRouter, Depends
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import PaperTrade

router = APIRouter(prefix="/paper-trades", tags=["paper-trades"])


@router.get("")
def list_paper_trades(limit: int = 50, db: Session = Depends(get_db)):
    trades = db.query(PaperTrade).order_by(desc(PaperTrade.created_at)).limit(limit).all()
    return [
        {
            "id": t.id,
            "signal_id": t.signal_id,
            "symbol": t.symbol,
            "direction": t.direction,
            "status": t.status,
            "entry_price": float(t.entry_price) if t.entry_price is not None else None,
            "stop_loss": float(t.stop_loss) if t.stop_loss is not None else None,
            "take_profit": float(t.take_profit) if t.take_profit is not None else None,
            "result": t.result,
            "r_multiple": float(t.r_multiple) if t.r_multiple is not None else None,
        }
        for t in trades
    ]
