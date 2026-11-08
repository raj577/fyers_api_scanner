"""Domain models shared by the scanner, dashboard and tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

PATTERN_TOUCH = "AVWAP TOUCH"
PATTERN_HUGGING = "AVWAP HUGGING"
PATTERN_CONSOLIDATION = "AVWAP CONSOLIDATION"
PATTERN_RECLAIM = "AVWAP RECLAIM"

PATTERN_TYPES: tuple[str, ...] = (
    PATTERN_TOUCH,
    PATTERN_HUGGING,
    PATTERN_CONSOLIDATION,
    PATTERN_RECLAIM,
)

BELOW_REFERENCES: tuple[str, ...] = ("off", "avwap", "vwap", "either")
BELOW_CANDLES: tuple[str, ...] = ("previous", "second_previous", "either")


@dataclass(frozen=True)
class Instrument:
    """A validated NSE futures contract from the FYERS instrument master."""

    symbol: str
    underlying: str
    expiry: date
    description: str = ""
    lot_size: float | None = None
    is_index: bool = False

    @property
    def label(self) -> str:
        return f"{self.symbol}  ·  {self.expiry:%d %b %Y}"


@dataclass(frozen=True)
class PatternConfig:
    """Configuration for the contract-start AVWAP pattern detector.

    Percentage thresholds are expressed as decimals (0.005 == 0.5%).
    """

    max_close_above_pct: float = 0.005
    max_close_below_pct: float = 0.002
    max_low_below_pct: float = 0.003
    max_body_ratio: float = 0.35
    min_candles: int = 1
    max_candles: int = 5
    max_window_range_pct: float = 0.02
    max_candle_range_atr: float | None = 2.0
    touch_tolerance_pct: float = 0.001
    consolidation_min_candles: int = 3
    consolidation_range_pct: float = 0.01
    hugging_min_candles: int = 2
    allowed_patterns: tuple[str, ...] = PATTERN_TYPES
    require_close_above_avwap: bool = False
    require_close_above_vwap: bool = False
    below_reference: str = "off"
    below_candle: str = "previous"

    def __post_init__(self) -> None:
        if self.min_candles < 1:
            raise ValueError("min_candles must be >= 1")
        if self.max_candles < self.min_candles:
            raise ValueError("max_candles must be >= min_candles")
        for name in (
            "max_close_above_pct",
            "max_close_below_pct",
            "max_low_below_pct",
            "max_body_ratio",
            "max_window_range_pct",
            "touch_tolerance_pct",
            "consolidation_range_pct",
        ):
            value = float(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.max_body_ratio > 1:
            raise ValueError("max_body_ratio must be <= 1")
        if self.max_candle_range_atr is not None and self.max_candle_range_atr <= 0:
            raise ValueError("max_candle_range_atr must be > 0 or None")
        unknown = set(self.allowed_patterns) - set(PATTERN_TYPES)
        if unknown:
            raise ValueError(f"Unknown pattern types: {sorted(unknown)}")
        if self.below_reference not in BELOW_REFERENCES:
            raise ValueError(f"Unknown below reference: {self.below_reference!r}")
        if self.below_candle not in BELOW_CANDLES:
            raise ValueError(f"Unknown below candle: {self.below_candle!r}")


@dataclass(frozen=True)
class DetectedPattern:
    """A pattern found at the right edge of a completed-candle series."""

    pattern_type: str
    candle_count: int
    start_idx: int
    end_idx: int
    pattern_start: pd.Timestamp
    pattern_end: pd.Timestamp
    last_price: float
    avwap: float
    distance_pct: float
    avg_body_ratio: float
    max_body_ratio: float
    window_range_pct: float
    touched: bool
    anchor_timestamp: pd.Timestamp | None = None
    latest_atr: float | None = None


@dataclass(frozen=True)
class ScanMatch:
    """A detected pattern linked to its futures contract."""

    instrument: Instrument
    pattern: DetectedPattern
    resolution: str = "60"

    @property
    def symbol(self) -> str:
        return self.instrument.symbol

    @property
    def expiry(self) -> date:
        return self.instrument.expiry

    @property
    def pattern_type(self) -> str:
        return self.pattern.pattern_type

    def to_row(self) -> dict[str, Any]:
        pattern = self.pattern
        return {
            "Symbol": self.instrument.symbol,
            "Underlying": self.instrument.underlying,
            "Expiry": self.instrument.expiry,
            "Last Price": pattern.last_price,
            "Contract AVWAP": pattern.avwap,
            "Distance (%)": pattern.distance_pct,
            "Candles": pattern.candle_count,
            "Body Ratio": pattern.avg_body_ratio,
            "Max Body Ratio": pattern.max_body_ratio,
            "Pattern Range (%)": pattern.window_range_pct * 100.0,
            "Classification": pattern.pattern_type,
            "Pattern Timestamp": pattern.pattern_end,
            "ATR": pattern.latest_atr,
        }


@dataclass
class ScanError:
    symbol: str
    message: str


@dataclass
class ScanReport:
    """Result of one scanner run."""

    matches: list[ScanMatch] = field(default_factory=list)
    errors: list[ScanError] = field(default_factory=list)
    histories: dict[str, pd.DataFrame] = field(default_factory=dict)
    scanned_count: int = 0
    generated_at: datetime | None = None
    resolution: str = "60"
    pattern_config: PatternConfig = field(default_factory=PatternConfig)

    def sorted_matches(self) -> list[ScanMatch]:
        return sorted(self.matches, key=lambda match: abs(match.pattern.distance_pct))

    def classification_counts(self) -> dict[str, int]:
        counts = {pattern_type: 0 for pattern_type in PATTERN_TYPES}
        for match in self.matches:
            counts[match.pattern_type] = counts.get(match.pattern_type, 0) + 1
        return counts

    def summary(self) -> dict[str, int]:
        counts = self.classification_counts()
        return {
            "scanned": self.scanned_count,
            "matches": len(self.matches),
            "touches": counts.get(PATTERN_TOUCH, 0),
            "hugging": counts.get(PATTERN_HUGGING, 0),
            "consolidations": counts.get(PATTERN_CONSOLIDATION, 0),
            "reclaims": counts.get(PATTERN_RECLAIM, 0),
            "errors": len(self.errors),
        }
