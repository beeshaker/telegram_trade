from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Sweep:
    direction: Literal["BUY", "SELL"]
    sweep_level: str
    sweep_price: float
    candle_time: object | None = None


def detect_sweep(candle: dict, opening_range_high: float, opening_range_low: float, buffer: float = 0.0) -> Sweep | None:
    """Detect a reversal sweep of the NY opening range.

    Bearish setup: candle wicks above high then closes back below high.
    Bullish setup: candle wicks below low then closes back above low.
    """
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])
    candle_time = candle.get("candle_time") or candle.get("time")

    if high > float(opening_range_high) + buffer and close < float(opening_range_high):
        return Sweep("SELL", "opening_range_high", high, candle_time)

    if low < float(opening_range_low) - buffer and close > float(opening_range_low):
        return Sweep("BUY", "opening_range_low", low, candle_time)

    return None
