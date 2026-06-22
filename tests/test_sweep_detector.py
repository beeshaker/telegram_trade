from app.strategy.sweep_detector import detect_sweep


def test_bearish_sweep():
    candle = {"high": 106, "low": 99, "close": 104}
    sweep = detect_sweep(candle, opening_range_high=105, opening_range_low=95)
    assert sweep is not None
    assert sweep.direction == "SELL"


def test_bullish_sweep():
    candle = {"high": 100, "low": 94, "close": 96}
    sweep = detect_sweep(candle, opening_range_high=105, opening_range_low=95)
    assert sweep is not None
    assert sweep.direction == "BUY"
