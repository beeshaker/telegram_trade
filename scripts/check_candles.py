import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func

from app.db import SessionLocal
from app.models import Candle


def main():
    db = SessionLocal()

    try:
        result = (
            db.query(
                Candle.symbol,
                Candle.timeframe,
                func.count(Candle.id),
                func.min(Candle.candle_time),
                func.max(Candle.candle_time),
            )
            .group_by(Candle.symbol, Candle.timeframe)
            .all()
        )

        if not result:
            print("No candles found.")
            return

        for row in result:
            print("Symbol:", row[0])
            print("Timeframe:", row[1])
            print("Count:", row[2])
            print("First candle:", row[3])
            print("Last candle:", row[4])
            print("-" * 50)

    finally:
        db.close()


if __name__ == "__main__":
    main()