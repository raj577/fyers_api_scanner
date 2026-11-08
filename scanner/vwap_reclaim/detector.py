"""Detection of completed-candle VWAP reclaim and bounce pairs."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from indicators.avwap import clean_volume, validate_ohlcv
from indicators.vwap import compute_session_vwap
from scanner.vwap_reclaim.config import VwapReclaimConfig
from scanner.vwap_reclaim.models import (
    SIGNAL_BOUNCE,
    SIGNAL_RECLAIM,
    VwapReclaimSignal,
)


def compute_volume_average(candles: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """Average volume of the previous ``lookback`` candles (current excluded)."""

    volume = clean_volume(candles)
    window = max(int(lookback), 1)
    return (
        volume.rolling(window=window, min_periods=1)
        .mean()
        .shift(1)
        .rename("avg_volume")
    )


def annotate_frame(
    candles: pd.DataFrame,
    timezone: str = "Asia/Kolkata",
    volume_lookback: int = 20,
) -> pd.DataFrame:
    """Return a copy with ``vwap`` and ``avg_volume`` columns."""

    validate_ohlcv(candles)
    frame = candles.copy()
    if "vwap" not in frame.columns:
        frame["vwap"] = compute_session_vwap(frame, timezone=timezone)
    if "avg_volume" not in frame.columns:
        frame["avg_volume"] = compute_volume_average(frame, volume_lookback)
    return frame


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _build_signal(
    frame: pd.DataFrame,
    index: int,
    config: VwapReclaimConfig,
    symbol: str,
    underlying: str,
    expiry: date | None,
) -> VwapReclaimSignal | None:
    previous = frame.iloc[index - 1]
    current = frame.iloc[index]

    previous_close = _finite(previous["close"])
    previous_low = _finite(previous["low"])
    previous_vwap = _finite(previous["vwap"])
    current_close = _finite(current["close"])
    current_low = _finite(current["low"])
    current_vwap = _finite(current["vwap"])
    current_volume = _finite(current["volume"]) or 0.0
    average_volume = _finite(current.get("avg_volume"))

    if None in (
        previous_close,
        previous_low,
        previous_vwap,
        current_close,
        current_low,
        current_vwap,
    ):
        return None
    if previous_vwap <= 0 or current_vwap <= 0:
        return None

    if not (previous_close < previous_vwap or previous_low < previous_vwap):
        return None
    if not current_close > current_vwap:
        return None
    if config.require_current_low_below_vwap and not current_low < current_vwap:
        return None

    is_bounce = (
        previous_low < previous_vwap
        and previous_close > previous_vwap
        and current_close > current_vwap
    )
    signal_type = SIGNAL_BOUNCE if is_bounce else SIGNAL_RECLAIM
    if signal_type not in config.allowed_signal_types:
        return None

    distance_pct = (current_close / current_vwap - 1.0) * 100.0
    if distance_pct < config.min_distance_pct * 100.0:
        return None
    if distance_pct > config.max_distance_pct * 100.0:
        return None

    previous_distance_pct = (previous_close / previous_vwap - 1.0) * 100.0
    if (
        config.max_previous_below_pct is not None
        and previous_distance_pct < -config.max_previous_below_pct * 100.0
    ):
        return None

    volume_ratio = None
    if average_volume is not None and average_volume > 0:
        volume_ratio = current_volume / average_volume
    if config.requires_volume_ratio:
        if volume_ratio is None or volume_ratio < config.min_volume_ratio:
            return None

    turnover = current_close * current_volume
    if config.min_turnover > 0 and turnover < config.min_turnover:
        return None

    return VwapReclaimSignal(
        symbol=symbol,
        underlying=underlying,
        expiry=expiry,
        signal_type=signal_type,
        timeframe=config.timeframe,
        signal_timestamp=pd.Timestamp(current["timestamp"]),
        previous_timestamp=pd.Timestamp(previous["timestamp"]),
        previous_close=previous_close,
        previous_low=previous_low,
        previous_vwap=previous_vwap,
        current_close=current_close,
        current_vwap=current_vwap,
        current_volume=current_volume,
        average_volume=average_volume,
        volume_ratio=volume_ratio,
        distance_pct=distance_pct,
        previous_distance_pct=previous_distance_pct,
        turnover=turnover,
    )


def detect_signals(
    candles: pd.DataFrame,
    config: VwapReclaimConfig | None = None,
    symbol: str = "",
    underlying: str = "",
    expiry: date | None = None,
    timezone: str = "Asia/Kolkata",
) -> list[VwapReclaimSignal]:
    """Return every qualifying reclaim/bounce pair in chronological order."""

    config = config or VwapReclaimConfig()
    if candles is None or len(candles) < 2:
        return []

    frame = annotate_frame(candles, timezone=timezone, volume_lookback=config.volume_lookback)
    frame = frame.reset_index(drop=True)

    signals: list[VwapReclaimSignal] = []
    for index in range(1, len(frame)):
        signal = _build_signal(
            frame,
            index,
            config,
            symbol=symbol,
            underlying=underlying,
            expiry=expiry,
        )
        if signal is not None:
            signals.append(signal)
    return signals


def detect_latest(
    candles: pd.DataFrame,
    config: VwapReclaimConfig | None = None,
    symbol: str = "",
    underlying: str = "",
    expiry: date | None = None,
    timezone: str = "Asia/Kolkata",
) -> VwapReclaimSignal | None:
    """Return a signal only when the latest completed candle pair qualifies."""

    config = config or VwapReclaimConfig()
    if candles is None or len(candles) < 2:
        return None

    frame = annotate_frame(candles, timezone=timezone, volume_lookback=config.volume_lookback)
    frame = frame.reset_index(drop=True)
    return _build_signal(
        frame,
        len(frame) - 1,
        config,
        symbol=symbol,
        underlying=underlying,
        expiry=expiry,
    )


def dedupe_signals(signals: list[VwapReclaimSignal]) -> list[VwapReclaimSignal]:
    """Keep the most recent signal per symbol."""

    latest: dict[str, VwapReclaimSignal] = {}
    for signal in signals:
        existing = latest.get(signal.symbol)
        if existing is None or signal.signal_timestamp >= existing.signal_timestamp:
            latest[signal.symbol] = signal
    return sorted(latest.values(), key=lambda signal: signal.distance_pct)
