import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.data.capital_client import CapitalClient
from app.db import SessionLocal
from app.models import Candle

load_dotenv()


def mid_price(price_dict: dict) -> float:
    bid = price_dict.get("bid")
    ask = price_dict.get("ask")

    if bid is None and ask is None:
        raise ValueError("Both bid and ask are missing.")

    if bid is None:
        return float(ask)

    if ask is None:
        return float(bid)

    return (float(bid) + float(ask)) / 2


def parse_capital_candle(price: dict) -> dict:
    candle_time = datetime.fromisoformat(price["snapshotTimeUTC"])

    return {
        "candle_time": candle_time,
        "open": mid_price(price["openPrice"]),
        "high": mid_price(price["highPrice"]),
        "low": mid_price(price["lowPrice"]),
        "close": mid_price(price["closePrice"]),
        "volume": float(price.get("lastTradedVolume") or 0),
    }


def upsert_candle(db, symbol: str, timeframe: str, candle: dict):
    existing = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol,
            Candle.timeframe == timeframe,
            Candle.candle_time == candle["candle_time"],
        )
        .first()
    )

    if existing:
        existing.open = candle["open"]
        existing.high = candle["high"]
        existing.low = candle["low"]
        existing.close = candle["close"]
        existing.volume = candle["volume"]
        existing.source = "capital"
        return "updated"

    new_candle = Candle(
        symbol=symbol,
        timeframe=timeframe,
        candle_time=candle["candle_time"],
        open=candle["open"],
        high=candle["high"],
        low=candle["low"],
        close=candle["close"],
        volume=candle["volume"],
        source="capital",
    )

    db.add(new_candle)
    return "inserted"


def main():
    epic = os.getenv("CAPITAL_EPIC", "US100")
    resolution = os.getenv("CAPITAL_RESOLUTION", "MINUTE")
    max_count = int(os.getenv("CAPITAL_MAX_CANDLES", "1000"))

    print("Syncing Capital.com candles")
    print("EPIC:", epic)
    print("Resolution:", resolution)
    print("Max candles:", max_count)

    client = CapitalClient()
    client.create_session()

    response = client.get_prices(
        epic=epic,
        resolution=resolution,
        max_count=max_count,
    )

    prices = response.get("prices", [])

    if not prices:
        print("No prices returned.")
        print(response)
        return

    db = SessionLocal()

    inserted = 0
    updated = 0

    try:
        for price in prices:
            candle = parse_capital_candle(price)
            result = upsert_candle(
                db=db,
                symbol=epic,
                timeframe="M1",
                candle=candle,
            )

            if result == "inserted":
                inserted += 1
            else:
                updated += 1

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

    print("Sync complete.")
    print("Inserted:", inserted)
    print("Updated:", updated)
    print("First candle UTC:", prices[0].get("snapshotTimeUTC"))
    print("Last candle UTC:", prices[-1].get("snapshotTimeUTC"))


if __name__ == "__main__":
    main()