def calculate_vwap_bands(candles: list[dict], deviation_threshold: float, min_lookback: int = 5) -> list[dict | None]:
    """Session-anchored VWAP with rolling deviation bands, one entry per candle.

    `candles` must be in chronological order starting from the session anchor
    (e.g. the strategy's trade_start). VWAP and stddev are cumulative from
    candles[0] through the current candle. Returns None for the first
    `min_lookback - 1` candles, since a band computed from too few candles
    is not a meaningful reference level.
    """
    bands: list[dict | None] = []
    cum_pv = 0.0
    cum_vol = 0.0
    typical_prices: list[float] = []

    for i, candle in enumerate(candles):
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        # Some CFD instruments report zero/missing volume; fall back to an
        # equal-weighted average so VWAP degrades gracefully instead of
        # dividing by zero.
        volume = float(candle.get("volume") or 0) or 1.0
        typical_price = (high + low + close) / 3.0

        cum_pv += typical_price * volume
        cum_vol += volume
        vwap = cum_pv / cum_vol
        typical_prices.append(typical_price)

        if i + 1 < min_lookback:
            bands.append(None)
            continue

        mean = sum(typical_prices) / len(typical_prices)
        variance = sum((p - mean) ** 2 for p in typical_prices) / len(typical_prices)
        stddev = variance ** 0.5

        bands.append(
            {
                "candle_time": candle.get("candle_time") or candle.get("time"),
                "vwap": vwap,
                "stddev": stddev,
                "high": vwap + deviation_threshold * stddev,
                "low": vwap - deviation_threshold * stddev,
            }
        )

    return bands
