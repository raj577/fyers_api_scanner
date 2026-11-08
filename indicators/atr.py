"""Average True Range used as an optional small-candle sanity check."""

from __future__ import annotations

import pandas as pd

from indicators.avwap import validate_ohlcv


def compute_atr(candles: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR. Returns NaN until ``period`` true-range values exist."""

    if period < 1:
        raise ValueError("ATR period must be >= 1")
    validate_ohlcv(candles)
    if len(candles) == 0:
        return pd.Series(dtype="float64", name="atr", index=candles.index)

    high = candles["high"].astype("float64")
    low = candles["low"].astype("float64")
    close = candles["close"].astype("float64")
    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return atr.rename("atr")
