"""Regular session VWAP (reset every trading day).

This is deliberately separate from the contract-start AVWAP used by the
scanner. It exists for chart context only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from indicators.avwap import clean_volume, validate_ohlcv


def _session_dates(candles: pd.DataFrame, timezone: str) -> pd.Series:
    timestamps = pd.to_datetime(candles["timestamp"])
    if isinstance(timestamps.dtype, pd.DatetimeTZDtype):
        timestamps = timestamps.dt.tz_convert(timezone)
    return timestamps.dt.date


def compute_session_vwap(
    candles: pd.DataFrame, timezone: str = "Asia/Kolkata"
) -> pd.Series:
    """Cumulative VWAP that resets at the start of each trading session."""

    validate_ohlcv(candles)
    if len(candles) == 0:
        return pd.Series(dtype="float64", name="session_vwap", index=candles.index)
    if "timestamp" not in candles.columns:
        raise ValueError("Candle data is missing the timestamp column")

    typical = (candles["high"] + candles["low"] + candles["close"]) / 3.0
    volume = clean_volume(candles)
    value = typical * volume

    sessions = _session_dates(candles, timezone)
    cumulative_value = value.groupby(sessions, sort=False).cumsum()
    cumulative_volume = volume.groupby(sessions, sort=False).cumsum()

    vwap = cumulative_value / cumulative_volume.replace(0.0, np.nan)
    return vwap.rename("session_vwap")
