from app.strategy.fvg_detector import detect_fvg_at


def test_bullish_fvg():
    candles = [
        {"high": 100, "low": 95},
        {"high": 102, "low": 96},
        {"high": 108, "low": 103},
    ]
    fvg = detect_fvg_at(candles, 2)
    assert fvg is not None
    assert fvg.direction == "BUY"
    assert fvg.fvg_low == 100
    assert fvg.fvg_high == 103


def test_bearish_fvg():
    candles = [
        {"high": 110, "low": 105},
        {"high": 108, "low": 104},
        {"high": 100, "low": 95},
    ]
    fvg = detect_fvg_at(candles, 2)
    assert fvg is not None
    assert fvg.direction == "SELL"
    assert fvg.fvg_low == 100
    assert fvg.fvg_high == 105
