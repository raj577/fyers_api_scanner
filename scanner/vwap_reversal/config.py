"""Configuration for the bearish-impulse VWAP reversal scanner."""

from __future__ import annotations

from dataclasses import dataclass

from scanner.vwap_reclaim.timeframes import DEFAULT_TIMEFRAME, TIMEFRAMES
from scanner.vwap_reversal.models import (
    CONFIRMATION_ABOVE_HIGH,
    CONFIRMATION_MODES,
    TOUCH_EXACT,
    TOUCH_TYPES,
)


@dataclass(frozen=True)
class VwapReversalConfig:
    """Thresholds for the large-bearish -> indecision-at-VWAP sequence.

    Percentages exposed to users are converted to decimals here where noted.
    ``vwap_tolerance_pct`` is already expressed in percent (0.10 == 0.10%).
    """

    timeframe: str = DEFAULT_TIMEFRAME

    min_bearish_body_ratio: float = 0.60
    min_bearish_atr_multiple: float = 1.5
    atr_period: int = 14

    doji_body_max_ratio: float = 0.15
    inverted_hammer_upper_wick_ratio: float = 2.0
    inverted_hammer_body_max_ratio: float = 0.35

    vwap_tolerance_pct: float = 0.10
    allowed_touch_types: tuple[str, ...] = (TOUCH_EXACT,)

    enable_bullish_confirmation: bool = False
    confirmation_mode: str = CONFIRMATION_ABOVE_HIGH

    min_volume: float = 0.0
    lookback_candles: int = 120

    def __post_init__(self) -> None:
        if self.timeframe not in TIMEFRAMES:
            raise ValueError(
                f"Unknown timeframe {self.timeframe!r}. "
                f"Choose one of: {', '.join(TIMEFRAMES)}."
            )
        for name in (
            "min_bearish_body_ratio",
            "doji_body_max_ratio",
            "inverted_hammer_body_max_ratio",
        ):
            value = float(getattr(self, name))
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        for name in (
            "min_bearish_atr_multiple",
            "inverted_hammer_upper_wick_ratio",
            "vwap_tolerance_pct",
            "min_volume",
        ):
            if float(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be >= 0")
        if int(self.atr_period) < 1:
            raise ValueError("atr_period must be >= 1")
        if int(self.lookback_candles) < 3:
            raise ValueError("lookback_candles must be >= 3")
        if self.confirmation_mode not in CONFIRMATION_MODES:
            raise ValueError(
                f"Unknown confirmation mode {self.confirmation_mode!r}. "
                f"Choose one of: {', '.join(CONFIRMATION_MODES)}."
            )
        unknown = set(self.allowed_touch_types) - set(TOUCH_TYPES)
        if unknown:
            raise ValueError(f"Unknown touch types: {sorted(unknown)}")
        if not self.allowed_touch_types:
            raise ValueError("Select at least one VWAP interaction type.")
