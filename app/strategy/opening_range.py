from datetime import datetime, time
from zoneinfo import ZoneInfo


def _as_tz(dt: datetime, timezone: str) -> datetime:
    tz = ZoneInfo(timezone)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def calculate_range(candles: list[dict], start_time: time, end_time: time, timezone: str) -> dict | None:
    selected = []
    for candle in candles:
        candle_time = candle.get("candle_time") or candle.get("time")
        if not isinstance(candle_time, datetime):
            continue
        local_time = _as_tz(candle_time, timezone).time()
        if start_time <= local_time < end_time:
            selected.append(candle)

    if not selected:
        return None

    return {
        "high": max(float(c["high"]) for c in selected),
        "low": min(float(c["low"]) for c in selected),
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "candles": len(selected),
    }


def calculate_ny_opening_ranges(candles: list[dict], timezone: str = "America/New_York") -> dict:
    return {
        "ny_15m": calculate_range(candles, time(9, 30), time(9, 45), timezone),
        "ny_30m": calculate_range(candles, time(9, 30), time(10, 0), timezone),
    }
