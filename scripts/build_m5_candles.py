import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import SessionLocal
from app.epics import list_enabled_epics
from app.models import Candle

load_dotenv()


def upsert_candle(db, symbol: str, timeframe: str, candle_time, row):
    existing = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol,
            Candle.timeframe == timeframe,
            Candle.candle_time == candle_time,
        )
        .first()
    )

    if existing:
        existing.open = float(row["open"])
        existing.high = float(row["high"])
        existing.low = float(row["low"])
        existing.close = float(row["close"])
        existing.volume = float(row["volume"])
        existing.source = "capital_resampled"
        return "updated"

    new_candle = Candle(
        symbol=symbol,
        timeframe=timeframe,
        candle_time=candle_time,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        source="capital_resampled",
    )

    db.add(new_candle)
    return "inserted"


def main():
    db = SessionLocal()
    symbols = [c.epic for c in list_enabled_epics(db)]

    try:
        for symbol in symbols:
            candles = (
                db.query(Candle)
                .filter(
                    Candle.symbol == symbol,
                    Candle.timeframe == "M1",
                )
                .order_by(Candle.candle_time.asc())
                .all()
            )

            if not candles:
                print(f"No M1 candles found for {symbol}")
                continue

            rows = []

            for c in candles:
                rows.append(
                    {
                        "time": c.candle_time,
                        "open": float(c.open),
                        "high": float(c.high),
                        "low": float(c.low),
                        "close": float(c.close),
                        "volume": float(c.volume or 0),
                    }
                )

            df = pd.DataFrame(rows)
            df["time"] = pd.to_datetime(df["time"])
            df = df.set_index("time").sort_index()

            # Build M5 candles and keep only complete 5-minute groups.
            m5 = (
                df.resample("5min", label="left", closed="left")
                .agg(
                    open=("open", "first"),
                    high=("high", "max"),
                    low=("low", "min"),
                    close=("close", "last"),
                    volume=("volume", "sum"),
                    candle_count=("close", "count"),
                )
                .dropna()
            )

            complete_m5 = m5[m5["candle_count"] == 5].copy()
            complete_m5 = complete_m5.drop(columns=["candle_count"])

            inserted = 0
            updated = 0

            for candle_time, row in complete_m5.iterrows():
                result = upsert_candle(
                    db=db,
                    symbol=symbol,
                    timeframe="M5",
                    candle_time=candle_time.to_pydatetime(),
                    row=row,
                )

                if result == "inserted":
                    inserted += 1
                else:
                    updated += 1

            db.commit()

            print("M5 build complete.")
            print("Symbol:", symbol)
            print("M1 candles used:", len(df))
            print("Complete M5 candles:", len(complete_m5))
            print("Inserted:", inserted)
            print("Updated:", updated)
            print("First M5 candle:", complete_m5.index.min())
            print("Last M5 candle:", complete_m5.index.max())

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()