from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")


def previous_day_range_window(session_date, timezone: str) -> tuple[datetime, datetime]:
    """UTC-naive (start, end) bounds for the previous full calendar day in `timezone`.

    Distinct from the existing "overnight" window (18:00 prev day -> 09:30) used by
    the opening-range strategy: this covers the full 00:00-23:59:59 prior day, for
    the PDH/PDL sweep+FVG strategy.
    """
    tz = ZoneInfo(timezone)
    start_local = datetime.combine(session_date - timedelta(days=1), time(0, 0), tzinfo=tz)
    end_local = datetime.combine(session_date, time(0, 0), tzinfo=tz)
    return (
        start_local.astimezone(UTC).replace(tzinfo=None),
        end_local.astimezone(UTC).replace(tzinfo=None),
    )
