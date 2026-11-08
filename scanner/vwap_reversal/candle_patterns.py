"""Pure candlestick-shape helpers for the VWAP reversal detector.

All helpers are deterministic functions over already-completed OHLC values and
never look at volume, VWAP or future candles. The detector combines them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scanner.vwap_reversal.models import (
    CANDLE_DOJI,
    CANDLE_INVERTED_HAMMER,
    TOUCH_EXACT,
    TOUCH_NEAR,
)

_EPSILON = 1e-12


@dataclass(frozen=True)
class CandleShape:
    """Normalized geometry of one OHLC candle."""

    open: float
    high: float
    low: float
    close: float
    body: float
    candle_range: float
    body_ratio: float
    upper_wick: float
    lower_wick: float

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def candle_shape(
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> CandleShape:
    """Compute body/wick geometry, safely handling zero-range candles."""

    body = abs(close - open_price)
    candle_range = max(high - low, 0.0)
    body_ratio = body / candle_range if candle_range > _EPSILON else 0.0
    upper_wick = max(high - max(open_price, close), 0.0)
    lower_wick = max(min(open_price, close) - low, 0.0)
    return CandleShape(
        open=open_price,
        high=high,
        low=low,
        close=close,
        body=body,
        candle_range=candle_range,
        body_ratio=body_ratio,
        upper_wick=upper_wick,
        lower_wick=lower_wick,
    )


def is_large_bearish_candle(
    shape: CandleShape,
    atr: float | None,
    min_body_ratio: float,
    min_atr_multiple: float,
) -> tuple[bool, float | None]:
    """Return ``(qualifies, body_to_atr)`` for a large bearish impulse candle.

    A candle qualifies when it is bearish, its real body covers at least
    ``min_body_ratio`` of its range and the body is at least
    ``min_atr_multiple`` times the ATR. When ATR is unavailable (the first
    ``period`` candles) the candle cannot be validated and does not qualify.
    """

    if not shape.is_bearish:
        return False, None
    if shape.body <= _EPSILON:
        return False, None
    if shape.candle_range <= _EPSILON:
        return False, None
    if shape.body_ratio < min_body_ratio:
        return False, None
    if atr is None or not math.isfinite(atr) or atr <= 0:
        return False, None
    multiple = shape.body / atr
    if multiple < min_atr_multiple:
        return False, None
    return True, multiple


def is_doji(shape: CandleShape, body_max_ratio: float) -> bool:
    """Indecision candle: real body is a small fraction of the range."""

    return shape.body_ratio <= body_max_ratio


def is_inverted_hammer(
    shape: CandleShape,
    upper_wick_ratio: float,
    body_max_ratio: float,
) -> bool:
    """Rejection candle: long upper wick, short lower wick, small body.

    Zero-body candles are handled explicitly: the lower wick must be zero and a
    real upper wick must be present, otherwise the candle is only a doji.
    """

    if shape.candle_range <= _EPSILON:
        return False
    if shape.body_ratio > body_max_ratio:
        return False
    if shape.upper_wick <= _EPSILON:
        return False
    if shape.upper_wick + _EPSILON < upper_wick_ratio * shape.body:
        return False
    if shape.lower_wick > shape.body + _EPSILON:
        return False
    return True


def classify_second_candle(
    shape: CandleShape,
    doji_body_max_ratio: float,
    inverted_hammer_upper_wick_ratio: float,
    inverted_hammer_body_max_ratio: float,
) -> str | None:
    """Classify the indecision candle, giving the more specific shape priority.

    A candle that satisfies the inverted-hammer geometry is labelled as an
    inverted hammer; otherwise a small-body candle is a doji.
    """

    if is_inverted_hammer(
        shape,
        upper_wick_ratio=inverted_hammer_upper_wick_ratio,
        body_max_ratio=inverted_hammer_body_max_ratio,
    ):
        return CANDLE_INVERTED_HAMMER
    if is_doji(shape, body_max_ratio=doji_body_max_ratio):
        return CANDLE_DOJI
    return None


def vwap_distance_pct(close: float, vwap: float) -> float | None:
    """Signed distance of the close from VWAP in percent."""

    if not math.isfinite(close) or not math.isfinite(vwap) or vwap <= 0:
        return None
    return (close / vwap - 1.0) * 100.0


def classify_vwap_interaction(
    low: float,
    high: float,
    close: float,
    vwap: float,
    tolerance_pct: float,
) -> str | None:
    """Return an exact/near VWAP interaction, or ``None`` when untouched.

    An exact touch means the candle's trading range intersects VWAP. A near
    touch is a separate, weaker classification based on the close distance and
    the configured tolerance; it is never reported as an exact touch.
    """

    if not all(math.isfinite(value) for value in (low, high, close, vwap)):
        return None
    if vwap <= 0:
        return None

    if low <= vwap <= high:
        return TOUCH_EXACT

    if tolerance_pct >= 0:
        distance = abs(close - vwap) / vwap * 100.0
        if distance <= tolerance_pct:
            return TOUCH_NEAR
    return None
