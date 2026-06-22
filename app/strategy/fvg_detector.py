from dataclasses import dataclass
from typing import Literal, Sequence


@dataclass(frozen=True)
class FVG:
    direction: Literal["BUY", "SELL"]
    fvg_low: float
    fvg_high: float
    midpoint: float
    index: int


def _get(candle: dict, key: str) -> float:
    return float(candle[key])


def detect_fvg_at(candles: Sequence[dict], index: int) -> FVG | None:
    """Detect a 3-candle FVG ending at candles[index].

    Bullish FVG: candle1.high < candle3.low
    Bearish FVG: candle1.low > candle3.high
    """
    if index < 2 or index >= len(candles):
        return None

    c1 = candles[index - 2]
    c3 = candles[index]

    c1_high = _get(c1, "high")
    c1_low = _get(c1, "low")
    c3_high = _get(c3, "high")
    c3_low = _get(c3, "low")

    if c1_high < c3_low:
        fvg_low = c1_high
        fvg_high = c3_low
        return FVG("BUY", fvg_low, fvg_high, (fvg_low + fvg_high) / 2, index)

    if c1_low > c3_high:
        fvg_low = c3_high
        fvg_high = c1_low
        return FVG("SELL", fvg_low, fvg_high, (fvg_low + fvg_high) / 2, index)

    return None


def find_latest_fvg(candles: Sequence[dict], direction: Literal["BUY", "SELL"] | None = None) -> FVG | None:
    for index in range(len(candles) - 1, 1, -1):
        fvg = detect_fvg_at(candles, index)
        if fvg and (direction is None or fvg.direction == direction):
            return fvg
    return None
