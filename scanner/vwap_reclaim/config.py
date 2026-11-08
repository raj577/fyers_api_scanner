"""Configuration for the VWAP reclaim / bounce scanner."""

from __future__ import annotations

from dataclasses import dataclass

from scanner.vwap_reclaim.models import SIGNAL_TYPES
from scanner.vwap_reclaim.timeframes import DEFAULT_TIMEFRAME, TIMEFRAMES


@dataclass(frozen=True)
class VwapReclaimConfig:
    """Filters applied to a confirmed VWAP reclaim pair.

    Percentage thresholds are decimals (0.005 == 0.5%).
    """

    timeframe: str = DEFAULT_TIMEFRAME
    max_distance_pct: float = 0.0075
    min_distance_pct: float = 0.0
    max_previous_below_pct: float | None = 0.05
    require_current_low_below_vwap: bool = False
    require_volume_confirmation: bool = False
    min_volume_ratio: float = 1.0
    volume_lookback: int = 20
    min_turnover: float = 0.0
    allowed_signal_types: tuple[str, ...] = SIGNAL_TYPES

    def __post_init__(self) -> None:
        if self.timeframe not in TIMEFRAMES:
            raise ValueError(
                f"Unknown timeframe {self.timeframe!r}. "
                f"Choose one of: {', '.join(TIMEFRAMES)}."
            )
        for name in (
            "max_distance_pct",
            "min_distance_pct",
            "min_volume_ratio",
            "min_turnover",
        ):
            if float(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.max_distance_pct < self.min_distance_pct:
            raise ValueError("max_distance_pct must be >= min_distance_pct")
        if self.max_previous_below_pct is not None and self.max_previous_below_pct < 0:
            raise ValueError("max_previous_below_pct must be >= 0 or None")
        if int(self.volume_lookback) < 1:
            raise ValueError("volume_lookback must be >= 1")
        unknown = set(self.allowed_signal_types) - set(SIGNAL_TYPES)
        if unknown:
            raise ValueError(f"Unknown signal types: {sorted(unknown)}")
        if not self.allowed_signal_types:
            raise ValueError("Select at least one signal type.")

    @property
    def requires_volume_ratio(self) -> bool:
        return self.require_volume_confirmation and self.min_volume_ratio > 0
