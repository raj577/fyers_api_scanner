"""Timeframe definitions and session-anchored candle resampling.

FYERS intraday candles are anchored to the NSE session open (09:15 IST), so
higher timeframes are built by bucketing each session from 09:15 rather than
from midnight. Every bucket must fit inside the 09:15-15:30 session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Iterable

import numpy as np
import pandas as pd

from data.historical_data import drop_incomplete_candles

SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
SESSION_MINUTES = 375

BASE_RESOLUTION = "5"
SUPPORTED_NATIVE_RESOLUTIONS = frozenset(
    {"1", "2", "3", "5", "10", "15", "20", "30", "45", "60", "120", "240", "D"}
)

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class TimeframeSpec:
    """A selectable scanner timeframe."""

    key: str
    label: str
    resolution: str
    minutes: int | None

    @property
    def is_daily(self) -> bool:
        return self.minutes is None


TIMEFRAMES: dict[str, TimeframeSpec] = {
    "5m": TimeframeSpec("5m", "5 minutes", "5", 5),
    "15m": TimeframeSpec("15m", "15 minutes", "15", 15),
    "30m": TimeframeSpec("30m", "30 minutes", "30", 30),
    "45m": TimeframeSpec("45m", "45 minutes", "45", 45),
    "1h": TimeframeSpec("1h", "1 hour", "60", 60),
    "2h": TimeframeSpec("2h", "2 hours", "120", 120),
    "4h": TimeframeSpec("4h", "4 hours", "240", 240),
    "1d": TimeframeSpec("1d", "1 day", "D", None),
}

DEFAULT_TIMEFRAME = "15m"

TIMEFRAME_LABELS: dict[str, str] = {
    spec.key: spec.label for spec in TIMEFRAMES.values()
}
LABEL_TO_TIMEFRAME: dict[str, str] = {
    spec.label: spec.key for spec in TIMEFRAMES.values()
}


def get_timeframe(key: str) -> TimeframeSpec:
    """Return a timeframe spec, raising a helpful error for unknown keys."""

    cleaned = str(key).strip()
    if cleaned in TIMEFRAMES:
        return TIMEFRAMES[cleaned]
    for spec in TIMEFRAMES.values():
        if cleaned.lower() == spec.label.lower():
            return spec
    raise ValueError(
        f"Unsupported timeframe {key!r}. Choose one of: {', '.join(TIMEFRAMES)}."
    )


def fetch_resolution(key: str) -> str:
    """Return the FYERS resolution to request for a timeframe.

    All configured timeframes are native FYERS resolutions today. The helper
    keeps a single place to fall back to the base resolution if that changes.
    """

    spec = get_timeframe(key)
    return spec.resolution if spec.resolution in SUPPORTED_NATIVE_RESOLUTIONS else BASE_RESOLUTION


def _as_local_timestamps(candles: pd.DataFrame, timezone: str) -> pd.Series:
    if "timestamp" not in candles.columns:
        raise ValueError("Candle data is missing the timestamp column")
    timestamps = pd.to_datetime(candles["timestamp"])
    if isinstance(timestamps.dtype, pd.DatetimeTZDtype):
        return timestamps.dt.tz_convert(timezone)
    return timestamps.dt.tz_localize(timezone)


def _aggregate(frame: pd.DataFrame, interval: int) -> pd.DataFrame:
    grouped = frame.groupby(["_session", "_bucket"], sort=True)
    result = grouped.agg(
        timestamp=("timestamp", "first"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    result = result.reset_index()
    session = pd.to_datetime(result["_session"])
    offset = pd.to_timedelta(result["_bucket"].astype("int64") * interval, unit="m")
    result["timestamp"] = (
        session
        + offset
        + pd.Timedelta(hours=SESSION_OPEN.hour, minutes=SESSION_OPEN.minute)
    )
    return result[OHLCV_COLUMNS]


def _resample_daily(candles: pd.DataFrame, timezone: str) -> pd.DataFrame:
    frame = candles.copy()
    frame["timestamp"] = _as_local_timestamps(frame, timezone)
    frame["_session"] = frame["timestamp"].dt.normalize()
    grouped = frame.groupby("_session", sort=True)
    result = grouped.agg(
        timestamp=("_session", "first"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    return result.reset_index(drop=True)[OHLCV_COLUMNS]


def resample_candles(
    candles: pd.DataFrame,
    timeframe: str,
    timezone: str = "Asia/Kolkata",
) -> pd.DataFrame:
    """Aggregate candles into session-anchored buckets for ``timeframe``.

    The result is idempotent for candles that already use the target resolution.
    Partial buckets that extend past 15:30 are discarded.
    """

    spec = get_timeframe(timeframe)
    if candles is None or candles.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    if spec.is_daily:
        return _resample_daily(candles, timezone)

    interval = int(spec.minutes or 0)
    if interval <= 0:
        raise ValueError(f"Invalid timeframe interval for {timeframe!r}")

    frame = candles.copy()
    timestamps = _as_local_timestamps(frame, timezone)
    frame["timestamp"] = timestamps
    sessions = timestamps.dt.normalize()
    open_offset = pd.Timedelta(hours=SESSION_OPEN.hour, minutes=SESSION_OPEN.minute)
    minutes_since_open = (timestamps - (sessions + open_offset)).dt.total_seconds() / 60.0

    frame["_session"] = sessions
    frame["_bucket"] = np.floor(minutes_since_open / interval).astype("int64")
    inside = (minutes_since_open >= 0) & (minutes_since_open < SESSION_MINUTES)
    frame = frame.loc[inside].copy()
    if frame.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    result = _aggregate(frame, interval)
    close_offset = pd.Timedelta(hours=SESSION_CLOSE.hour, minutes=SESSION_CLOSE.minute)
    bucket_end = result["timestamp"] + pd.Timedelta(minutes=interval)
    result = result.loc[bucket_end <= (result["timestamp"].dt.normalize() + close_offset)]
    return result.reset_index(drop=True)


def prepare_candles(
    candles: pd.DataFrame,
    timeframe: str,
    now: pd.Timestamp | None = None,
    timezone: str = "Asia/Kolkata",
) -> pd.DataFrame:
    """Resample to the requested timeframe and drop the forming candle."""

    spec = get_timeframe(timeframe)
    resampled = resample_candles(candles, timeframe, timezone=timezone)
    if resampled.empty:
        return resampled
    resolution = "D" if spec.is_daily else spec.resolution
    return drop_incomplete_candles(
        resampled,
        resolution=resolution,
        now=now,
        timezone=timezone,
    )


def timeframe_choices() -> Iterable[tuple[str, str]]:
    """Ordered ``(key, label)`` pairs for UI selectboxes."""

    return [(spec.key, spec.label) for spec in TIMEFRAMES.values()]
