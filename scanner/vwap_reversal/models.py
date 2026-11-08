"""Domain models for the bearish-impulse VWAP reversal scanner.

This scanner is deliberately independent from the VWAP reclaim scanner. It
looks for a large bearish impulse candle followed immediately by indecision
(doji / inverted hammer) at the session VWAP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

CANDLE_DOJI = "DOJI"
CANDLE_INVERTED_HAMMER = "INVERTED HAMMER"
CANDLE_TYPES: tuple[str, ...] = (CANDLE_DOJI, CANDLE_INVERTED_HAMMER)

TOUCH_EXACT = "EXACT TOUCH"
TOUCH_NEAR = "NEAR VWAP"
TOUCH_TYPES: tuple[str, ...] = (TOUCH_EXACT, TOUCH_NEAR)

SIGNAL_DOJI = "BEARISH IMPULSE + VWAP DOJI"
SIGNAL_INVERTED_HAMMER = "BEARISH IMPULSE + VWAP INVERTED HAMMER"
SIGNAL_NEAR_TOUCH = "VWAP NEAR-TOUCH"
SIGNAL_TYPES: tuple[str, ...] = (
    SIGNAL_DOJI,
    SIGNAL_INVERTED_HAMMER,
    SIGNAL_NEAR_TOUCH,
)

CONFIRMATION_PATTERN_DETECTED = "Pattern Detected"
CONFIRMATION_AWAITING = "Awaiting Confirmation"
CONFIRMATION_BULLISH = "Bullish Confirmation"
CONFIRMATION_STATUSES: tuple[str, ...] = (
    CONFIRMATION_PATTERN_DETECTED,
    CONFIRMATION_AWAITING,
    CONFIRMATION_BULLISH,
)

CONFIRMATION_ABOVE_HIGH = "Third close > second high"
CONFIRMATION_ABOVE_CLOSE = "Third close > second close"
CONFIRMATION_MODES: tuple[str, ...] = (
    CONFIRMATION_ABOVE_HIGH,
    CONFIRMATION_ABOVE_CLOSE,
)


@dataclass(frozen=True)
class VwapReversalSignal:
    """A bearish impulse / indecision pair that interacted with session VWAP."""

    symbol: str
    underlying: str
    expiry: date | None
    timeframe: str
    signal_type: str
    candle_type: str
    touch_type: str
    first_timestamp: pd.Timestamp
    second_timestamp: pd.Timestamp
    first_open: float
    first_high: float
    first_low: float
    first_close: float
    second_open: float
    second_high: float
    second_low: float
    second_close: float
    second_volume: float
    vwap: float
    vwap_distance_pct: float
    bearish_body_size: float
    bearish_body_ratio: float
    bearish_atr_multiple: float
    second_body_ratio: float
    second_upper_wick: float
    second_lower_wick: float
    confirmation_status: str
    confirmation_detail: str = ""
    third_timestamp: pd.Timestamp | None = None
    third_close: float | None = None

    @property
    def is_exact_touch(self) -> bool:
        return self.touch_type == TOUCH_EXACT

    @property
    def is_near_touch(self) -> bool:
        return self.touch_type == TOUCH_NEAR

    @property
    def is_confirmed(self) -> bool:
        return self.confirmation_status == CONFIRMATION_BULLISH

    def to_row(self) -> dict[str, Any]:
        return {
            "Symbol": self.symbol,
            "Underlying": self.underlying,
            "Expiry": self.expiry,
            "Timeframe": self.timeframe,
            "Pattern type": self.signal_type,
            "Prev body size": self.bearish_body_size,
            "Prev body ratio (%)": self.bearish_body_ratio * 100.0,
            "ATR x": self.bearish_atr_multiple,
            "Second candle": self.candle_type,
            "Touch": self.touch_type,
            "Second low": self.second_low,
            "Second high": self.second_high,
            "Second close": self.second_close,
            "Second VWAP": self.vwap,
            "VWAP distance (%)": self.vwap_distance_pct,
            "Volume": self.second_volume,
            "Confirmation": self.confirmation_status,
            "Signal time": self.second_timestamp,
        }

    def to_history_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "underlying": self.underlying,
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "scanner": "vwap_reversal",
            "signal": self.signal_type,
            "candle_type": self.candle_type,
            "touch_type": self.touch_type,
            "timeframe": self.timeframe,
            "signal_time": self.second_timestamp.isoformat(),
            "previous_time": self.first_timestamp.isoformat(),
            "previous_body_size": self.bearish_body_size,
            "previous_body_ratio": self.bearish_body_ratio,
            "atr_multiple": self.bearish_atr_multiple,
            "second_low": self.second_low,
            "second_high": self.second_high,
            "second_close": self.second_close,
            "vwap": self.vwap,
            "distance_pct": self.vwap_distance_pct,
            "volume": self.second_volume,
            "confirmation": self.confirmation_status,
            "confirmed": self.is_confirmed,
            "vwap_touch": self.touch_type,
        }


@dataclass
class VwapReversalScanError:
    symbol: str
    message: str


@dataclass
class VwapReversalReport:
    """Result of one VWAP reversal scan."""

    signals: list[VwapReversalSignal] = field(default_factory=list)
    errors: list[VwapReversalScanError] = field(default_factory=list)
    histories: dict[str, pd.DataFrame] = field(default_factory=dict)
    scanned_count: int = 0
    large_bearish_count: int = 0
    generated_at: datetime | None = None
    timeframe: str = "15m"
    config: Any = None

    def sorted_signals(self) -> list[VwapReversalSignal]:
        return sorted(
            self.signals,
            key=lambda signal: signal.second_timestamp,
            reverse=True,
        )

    def signal_type_counts(self) -> dict[str, int]:
        counts = {signal_type: 0 for signal_type in SIGNAL_TYPES}
        for signal in self.signals:
            counts[signal.signal_type] = counts.get(signal.signal_type, 0) + 1
        return counts

    def candle_type_counts(self) -> dict[str, int]:
        counts = {candle_type: 0 for candle_type in CANDLE_TYPES}
        for signal in self.signals:
            counts[signal.candle_type] = counts.get(signal.candle_type, 0) + 1
        return counts

    def touch_type_counts(self) -> dict[str, int]:
        counts = {touch_type: 0 for touch_type in TOUCH_TYPES}
        for signal in self.signals:
            counts[signal.touch_type] = counts.get(signal.touch_type, 0) + 1
        return counts

    def summary(self) -> dict[str, int]:
        candles = self.candle_type_counts()
        touches = self.touch_type_counts()
        return {
            "scanned": self.scanned_count,
            "signals": len(self.signals),
            "large_bearish": self.large_bearish_count,
            "doji_setups": candles.get(CANDLE_DOJI, 0),
            "inverted_hammer_setups": candles.get(CANDLE_INVERTED_HAMMER, 0),
            "exact_touches": touches.get(TOUCH_EXACT, 0),
            "near_touches": touches.get(TOUCH_NEAR, 0),
            "confirmations": sum(1 for signal in self.signals if signal.is_confirmed),
            "errors": len(self.errors),
        }

    def to_history_record(self, universe_label: str = "") -> dict[str, Any]:
        generated = self.generated_at.isoformat() if self.generated_at else None
        return {
            "scanner": "vwap_reversal",
            "generated_at": generated,
            "timeframe": self.timeframe,
            "universe": universe_label,
            "scanned": self.scanned_count,
            "matches": len(self.signals),
            "errors": len(self.errors),
            "signals": [signal.to_history_dict() for signal in self.sorted_signals()],
        }
