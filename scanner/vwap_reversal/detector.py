"""Detection of large bearish candles followed by indecision at session VWAP.

The detector is look-ahead free: session VWAP and ATR are cumulative series, so
each row only depends on that row and earlier rows. The caller is responsible
for passing completed candles only (see
:func:`scanner.vwap_reclaim.timeframes.prepare_candles`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from indicators.atr import compute_atr
from indicators.avwap import validate_ohlcv
from indicators.vwap import compute_session_vwap
from scanner.vwap_reversal.candle_patterns import (
    candle_shape,
    classify_second_candle,
    classify_vwap_interaction,
    is_large_bearish_candle,
    vwap_distance_pct,
)
from scanner.vwap_reversal.config import VwapReversalConfig
from scanner.vwap_reversal.models import (
    CONFIRMATION_ABOVE_HIGH,
    CONFIRMATION_AWAITING,
    CONFIRMATION_BULLISH,
    CONFIRMATION_PATTERN_DETECTED,
    SIGNAL_DOJI,
    SIGNAL_INVERTED_HAMMER,
    SIGNAL_NEAR_TOUCH,
    CANDLE_DOJI,
    TOUCH_EXACT,
    VwapReversalSignal,
)


@dataclass
class DetectionResult:
    """Signals plus scan counters for one contract's candle history."""

    signals: list[VwapReversalSignal] = field(default_factory=list)
    large_bearish_count: int = 0


def annotate_frame(
    candles: pd.DataFrame,
    timezone: str = "Asia/Kolkata",
    atr_period: int = 14,
) -> pd.DataFrame:
    """Return a copy with ``vwap`` and ``atr`` columns, sorted by timestamp."""

    validate_ohlcv(candles)
    frame = candles.copy()
    if "timestamp" in frame.columns:
        frame = frame.sort_values("timestamp").reset_index(drop=True)
    if "vwap" not in frame.columns:
        frame["vwap"] = compute_session_vwap(frame, timezone=timezone)
    if "atr" not in frame.columns:
        frame["atr"] = compute_atr(frame, period=atr_period)
    return frame


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _confirmation(
    frame: pd.DataFrame,
    second_index: int,
    second_high: float,
    second_close: float,
    config: VwapReversalConfig,
) -> tuple[str, str, pd.Timestamp | None, float | None]:
    if not config.enable_bullish_confirmation:
        return (
            CONFIRMATION_PATTERN_DETECTED,
            "Bullish confirmation not requested",
            None,
            None,
        )

    if second_index + 1 >= len(frame):
        return (
            CONFIRMATION_AWAITING,
            "No completed third candle yet",
            None,
            None,
        )

    third = frame.iloc[second_index + 1]
    third_close = _finite(third["close"])
    third_timestamp = pd.Timestamp(third["timestamp"])
    if third_close is None:
        return CONFIRMATION_AWAITING, "Third candle close unavailable", third_timestamp, None

    if config.confirmation_mode == CONFIRMATION_ABOVE_HIGH:
        reference = second_high
        label = "second high"
    else:
        reference = second_close
        label = "second close"

    if third_close > reference:
        return (
            CONFIRMATION_BULLISH,
            f"Third close {third_close:.2f} > {label} {reference:.2f}",
            third_timestamp,
            third_close,
        )
    return (
        CONFIRMATION_AWAITING,
        f"Third close {third_close:.2f} <= {label} {reference:.2f}",
        third_timestamp,
        third_close,
    )


def _build_signal(
    frame: pd.DataFrame,
    second_index: int,
    config: VwapReversalConfig,
    symbol: str,
    underlying: str,
    expiry: date | None,
) -> tuple[VwapReversalSignal | None, bool]:
    """Evaluate one adjacent candle pair.

    Returns ``(signal, candle1_is_large_bearish)``.
    """

    first = frame.iloc[second_index - 1]
    second = frame.iloc[second_index]

    first_open = _finite(first["open"])
    first_high = _finite(first["high"])
    first_low = _finite(first["low"])
    first_close = _finite(first["close"])
    second_open = _finite(second["open"])
    second_high = _finite(second["high"])
    second_low = _finite(second["low"])
    second_close = _finite(second["close"])
    second_volume = _finite(second.get("volume")) or 0.0
    vwap = _finite(second.get("vwap"))
    atr = _finite(first.get("atr"))

    if None in (
        first_open,
        first_high,
        first_low,
        first_close,
        second_open,
        second_high,
        second_low,
        second_close,
        vwap,
    ):
        return None, False

    first_shape = candle_shape(first_open, first_high, first_low, first_close)
    large, atr_multiple = is_large_bearish_candle(
        first_shape,
        atr=atr,
        min_body_ratio=config.min_bearish_body_ratio,
        min_atr_multiple=config.min_bearish_atr_multiple,
    )
    if not large:
        return None, False

    second_shape = candle_shape(second_open, second_high, second_low, second_close)
    candle_type = classify_second_candle(
        second_shape,
        doji_body_max_ratio=config.doji_body_max_ratio,
        inverted_hammer_upper_wick_ratio=config.inverted_hammer_upper_wick_ratio,
        inverted_hammer_body_max_ratio=config.inverted_hammer_body_max_ratio,
    )
    if candle_type is None:
        return None, True

    if second_volume < config.min_volume:
        return None, True

    touch_type = classify_vwap_interaction(
        low=second_low,
        high=second_high,
        close=second_close,
        vwap=vwap,
        tolerance_pct=config.vwap_tolerance_pct,
    )
    if touch_type is None or touch_type not in config.allowed_touch_types:
        return None, True

    distance = vwap_distance_pct(second_close, vwap)
    if distance is None:
        return None, True

    if touch_type == TOUCH_EXACT:
        signal_type = (
            SIGNAL_DOJI if candle_type == CANDLE_DOJI else SIGNAL_INVERTED_HAMMER
        )
    else:
        signal_type = SIGNAL_NEAR_TOUCH

    status, detail, third_timestamp, third_close = _confirmation(
        frame,
        second_index,
        second_high,
        second_close,
        config,
    )

    signal = VwapReversalSignal(
        symbol=symbol,
        underlying=underlying,
        expiry=expiry,
        timeframe=config.timeframe,
        signal_type=signal_type,
        candle_type=candle_type,
        touch_type=touch_type,
        first_timestamp=pd.Timestamp(first["timestamp"]),
        second_timestamp=pd.Timestamp(second["timestamp"]),
        first_open=first_open,
        first_high=first_high,
        first_low=first_low,
        first_close=first_close,
        second_open=second_open,
        second_high=second_high,
        second_low=second_low,
        second_close=second_close,
        second_volume=second_volume,
        vwap=vwap,
        vwap_distance_pct=distance,
        bearish_body_size=first_shape.body,
        bearish_body_ratio=first_shape.body_ratio,
        bearish_atr_multiple=atr_multiple or 0.0,
        second_body_ratio=second_shape.body_ratio,
        second_upper_wick=second_shape.upper_wick,
        second_lower_wick=second_shape.lower_wick,
        confirmation_status=status,
        confirmation_detail=detail,
        third_timestamp=third_timestamp,
        third_close=third_close,
    )
    return signal, True


def detect(
    candles: pd.DataFrame,
    config: VwapReversalConfig | None = None,
    symbol: str = "",
    underlying: str = "",
    expiry: date | None = None,
    timezone: str = "Asia/Kolkata",
    lookback_candles: int | None = None,
) -> DetectionResult:
    """Detect every qualifying pair in the evaluated window."""

    config = config or VwapReversalConfig()
    if candles is None or len(candles) < 2:
        return DetectionResult()

    frame = annotate_frame(candles, timezone=timezone, atr_period=config.atr_period)
    frame = frame.reset_index(drop=True)
    if len(frame) < 2:
        return DetectionResult()

    start = 1
    if lookback_candles is not None:
        window = max(int(lookback_candles), 3)
        start = max(1, len(frame) - window)

    result = DetectionResult()
    for second_index in range(start, len(frame)):
        signal, large_bearish = _build_signal(
            frame,
            second_index,
            config,
            symbol=symbol,
            underlying=underlying,
            expiry=expiry,
        )
        if large_bearish:
            result.large_bearish_count += 1
        if signal is not None:
            result.signals.append(signal)
    return result


def detect_signals(
    candles: pd.DataFrame,
    config: VwapReversalConfig | None = None,
    symbol: str = "",
    underlying: str = "",
    expiry: date | None = None,
    timezone: str = "Asia/Kolkata",
    lookback_candles: int | None = None,
) -> list[VwapReversalSignal]:
    """Return every qualifying signal in the evaluated window."""

    return detect(
        candles,
        config=config,
        symbol=symbol,
        underlying=underlying,
        expiry=expiry,
        timezone=timezone,
        lookback_candles=lookback_candles,
    ).signals


def detect_latest(
    candles: pd.DataFrame,
    config: VwapReversalConfig | None = None,
    symbol: str = "",
    underlying: str = "",
    expiry: date | None = None,
    timezone: str = "Asia/Kolkata",
    lookback_candles: int | None = None,
) -> VwapReversalSignal | None:
    """Return the most recent qualifying signal in the window, if any."""

    signals = detect_signals(
        candles,
        config=config,
        symbol=symbol,
        underlying=underlying,
        expiry=expiry,
        timezone=timezone,
        lookback_candles=lookback_candles,
    )
    if not signals:
        return None
    return max(signals, key=lambda signal: signal.second_timestamp)


def dedupe_signals(
    signals: list[VwapReversalSignal],
) -> list[VwapReversalSignal]:
    """Keep only the newest signal per symbol/timeframe combination."""

    latest: dict[tuple[str, str], VwapReversalSignal] = {}
    for signal in signals:
        key = (signal.symbol, signal.timeframe)
        existing = latest.get(key)
        if existing is None or signal.second_timestamp >= existing.second_timestamp:
            latest[key] = signal
    return sorted(
        latest.values(),
        key=lambda signal: signal.second_timestamp,
        reverse=True,
    )
