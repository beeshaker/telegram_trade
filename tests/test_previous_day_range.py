from datetime import date, datetime

from app.strategy.previous_day_range import previous_day_range_window


def test_previous_day_range_window_returns_utc_naive_bounds_for_ny():
    session_date = date(2026, 6, 2)  # Tuesday

    start_utc, end_utc = previous_day_range_window(session_date, "America/New_York")

    # 2026-06-01 00:00 America/New_York == 2026-06-01 04:00 UTC (EDT, UTC-4)
    assert start_utc == datetime(2026, 6, 1, 4, 0)
    # 2026-06-02 00:00 America/New_York == 2026-06-02 04:00 UTC
    assert end_utc == datetime(2026, 6, 2, 4, 0)


def test_previous_day_range_window_spans_exactly_24_hours():
    session_date = date(2026, 1, 15)

    start_utc, end_utc = previous_day_range_window(session_date, "Europe/London")

    assert (end_utc - start_utc).total_seconds() == 24 * 3600


def test_previous_day_range_window_returns_naive_datetimes():
    start_utc, end_utc = previous_day_range_window(date(2026, 6, 2), "Asia/Tokyo")

    assert start_utc.tzinfo is None
    assert end_utc.tzinfo is None
