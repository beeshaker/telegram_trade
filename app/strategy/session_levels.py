from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def _as_tz(dt: datetime, timezone: str) -> datetime:
    tz = ZoneInfo(timezone)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def calculate_overnight_levels(candles: list[dict], session_date, timezone: str = "America/New_York") -> dict | None:
    """Calculate overnight high/low: previous day 18:00 to session day 09:29:59."""
    tz = ZoneInfo(timezone)
    start_dt = datetime.combine(session_date - timedelta(days=1), time(18, 0), tzinfo=tz)
    end_dt = datetime.combine(session_date, time(9, 30), tzinfo=tz)

    selected = []
    for candle in candles:
        candle_time = candle.get("candle_time") or candle.get("time")
        if not isinstance(candle_time, datetime):
            continue
        local_dt = _as_tz(candle_time, timezone)
        if start_dt <= local_dt < end_dt:
            selected.append(candle)

    if not selected:
        return None

    return {
        "previous_session_high": max(float(c["high"]) for c in selected),
        "previous_session_low": min(float(c["low"]) for c in selected),
        "candles": len(selected),
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
    }
