import pytest

from app.strategy.vwap import calculate_vwap_bands


def _candle(h, l, c, v=100):
    return {"high": h, "low": l, "close": c, "volume": v}


def test_calculate_vwap_bands_returns_none_before_min_lookback():
    candles = [_candle(101, 99, 100) for _ in range(4)]
    bands = calculate_vwap_bands(candles, deviation_threshold=1.5)

    assert bands == [None, None, None, None]


def test_calculate_vwap_bands_produces_band_once_lookback_satisfied():
    candles = [_candle(101, 99, 100) for _ in range(5)]
    bands = calculate_vwap_bands(candles, deviation_threshold=1.5)

    assert bands[0] is None
    assert bands[4] is not None
    assert bands[4]["vwap"] == pytest.approx(100.0)
    assert bands[4]["high"] >= bands[4]["vwap"]
    assert bands[4]["low"] <= bands[4]["vwap"]


def test_calculate_vwap_bands_widens_with_more_deviation():
    candles = [_candle(105, 95, 100 + i) for i in range(6)]
    narrow = calculate_vwap_bands(candles, deviation_threshold=1.0)
    wide = calculate_vwap_bands(candles, deviation_threshold=3.0)

    assert (wide[5]["high"] - wide[5]["low"]) > (narrow[5]["high"] - narrow[5]["low"])


def test_calculate_vwap_bands_falls_back_to_equal_weight_when_volume_missing():
    candles = [_candle(101, 99, 100, v=0) for _ in range(5)]
    bands = calculate_vwap_bands(candles, deviation_threshold=1.5)

    assert bands[4]["vwap"] == pytest.approx(100.0)


def test_calculate_vwap_bands_same_length_as_input():
    candles = [_candle(101, 99, 100) for _ in range(8)]
    bands = calculate_vwap_bands(candles, deviation_threshold=1.5)

    assert len(bands) == len(candles)
