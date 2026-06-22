import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import SessionLocal
from app.models import Candle

load_dotenv()


NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


def ny_to_utc_naive(dt):
    return dt.astimezone(UTC_TZ).replace(tzinfo=None)


def get_range(db, symbol, timeframe, start_utc, end_utc):
    candles = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol,
            Candle.timeframe == timeframe,
            Candle.candle_time >= start_utc,
            Candle.candle_time < end_utc,
        )
        .order_by(Candle.candle_time.asc())
        .all()
    )

    if not candles:
        return None

    return {
        "count": len(candles),
        "high": max(float(c.high) for c in candles),
        "low": min(float(c.low) for c in candles),
        "first": candles[0].candle_time,
        "last": candles[-1].candle_time,
    }


def main():
    symbol = os.getenv("CAPITAL_EPIC", "US100")

    db = SessionLocal()

    try:
        latest = (
            db.query(Candle)
            .filter(Candle.symbol == symbol, Candle.timeframe == "M1")
            .order_by(Candle.candle_time.desc())
            .first()
        )

        if not latest:
            print("No M1 candles found.")
            return

        latest_utc = latest.candle_time.replace(tzinfo=UTC_TZ)
        latest_ny = latest_utc.astimezone(NY_TZ)

        session_date = latest_ny.date()

        overnight_start_ny = datetime.combine(
            session_date - timedelta(days=1),
            time(18, 0),
            tzinfo=NY_TZ,
        )

        overnight_end_ny = datetime.combine(
            session_date,
            time(9, 30),
            tzinfo=NY_TZ,
        )

        ny_15_start = datetime.combine(session_date, time(9, 30), tzinfo=NY_TZ)
        ny_15_end = datetime.combine(session_date, time(9, 45), tzinfo=NY_TZ)

        ny_30_start = datetime.combine(session_date, time(9, 30), tzinfo=NY_TZ)
        ny_30_end = datetime.combine(session_date, time(10, 0), tzinfo=NY_TZ)

        overnight = get_range(
            db,
            symbol,
            "M1",
            ny_to_utc_naive(overnight_start_ny),
            ny_to_utc_naive(overnight_end_ny),
        )

        ny15 = get_range(
            db,
            symbol,
            "M1",
            ny_to_utc_naive(ny_15_start),
            ny_to_utc_naive(ny_15_end),
        )

        ny30 = get_range(
            db,
            symbol,
            "M1",
            ny_to_utc_naive(ny_30_start),
            ny_to_utc_naive(ny_30_end),
        )

        print("Symbol:", symbol)
        print("Latest candle UTC:", latest.candle_time)
        print("Latest candle NY:", latest_ny.strftime("%Y-%m-%d %H:%M:%S %Z"))
        print("Session date NY:", session_date)

        print("\nOvernight session:")
        print("NY:", overnight_start_ny, "to", overnight_end_ny)
        print("Data:", overnight)

        print("\nNY 15-minute opening range:")
        print("NY:", ny_15_start, "to", ny_15_end)
        print("Data:", ny15)

        print("\nNY 30-minute opening range:")
        print("NY:", ny_30_start, "to", ny_30_end)
        print("Data:", ny30)

        if latest_ny.time() < time(9, 30):
            print("\nStatus: Before NY open. Keep syncing candles.")
        elif latest_ny.time() < time(9, 45):
            print("\nStatus: NY open started. Waiting for 15-minute range to complete.")
        elif latest_ny.time() < time(10, 0):
            print("\nStatus: 15-minute range ready. 30-minute range still forming.")
        else:
            print("\nStatus: NY ranges should be ready. Strategy scan can run.")

    finally:
        db.close()


if __name__ == "__main__":
    main()