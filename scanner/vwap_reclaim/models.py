"""Domain models for the VWAP reclaim / bounce scanner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

SIGNAL_RECLAIM = "VWAP RECLAIM"
SIGNAL_BOUNCE = "VWAP BOUNCE"

SIGNAL_TYPES: tuple[str, ...] = (SIGNAL_RECLAIM, SIGNAL_BOUNCE)


@dataclass(frozen=True)
class VwapReclaimSignal:
    """A completed-candle pair that crossed back above session VWAP."""

    symbol: str
    signal_type: str
    timeframe: str
    signal_timestamp: pd.Timestamp
    previous_timestamp: pd.Timestamp
    previous_close: float
    previous_low: float
    previous_vwap: float
    current_close: float
    current_vwap: float
    current_volume: float
    average_volume: float | None
    volume_ratio: float | None
    distance_pct: float
    previous_distance_pct: float
    turnover: float
    underlying: str = ""
    expiry: date | None = None

    @property
    def is_bounce(self) -> bool:
        return self.signal_type == SIGNAL_BOUNCE

    def to_row(self) -> dict[str, Any]:
        return {
            "Symbol": self.symbol,
            "Underlying": self.underlying,
            "Expiry": self.expiry,
            "Signal": self.signal_type,
            "Timeframe": self.timeframe,
            "Signal time": self.signal_timestamp,
            "Previous close": self.previous_close,
            "Previous VWAP": self.previous_vwap,
            "Close": self.current_close,
            "VWAP": self.current_vwap,
            "Distance (%)": self.distance_pct,
            "Volume": self.current_volume,
            "Avg volume": self.average_volume,
            "Volume x": self.volume_ratio,
            "Turnover": self.turnover,
        }

    def to_history_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "underlying": self.underlying,
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "signal": self.signal_type,
            "timeframe": self.timeframe,
            "signal_time": self.signal_timestamp.isoformat(),
            "previous_close": self.previous_close,
            "previous_vwap": self.previous_vwap,
            "close": self.current_close,
            "vwap": self.current_vwap,
            "distance_pct": self.distance_pct,
            "volume": self.current_volume,
            "average_volume": self.average_volume,
            "volume_ratio": self.volume_ratio,
            "turnover": self.turnover,
        }


@dataclass
class VwapScanError:
    symbol: str
    message: str


@dataclass
class VwapScanReport:
    """Result of one VWAP reclaim scan."""

    signals: list[VwapReclaimSignal] = field(default_factory=list)
    errors: list[VwapScanError] = field(default_factory=list)
    histories: dict[str, pd.DataFrame] = field(default_factory=dict)
    scanned_count: int = 0
    generated_at: datetime | None = None
    timeframe: str = "15m"
    config: Any = None

    def sorted_signals(self) -> list[VwapReclaimSignal]:
        return sorted(self.signals, key=lambda signal: signal.distance_pct)

    def signal_type_counts(self) -> dict[str, int]:
        counts = {signal_type: 0 for signal_type in SIGNAL_TYPES}
        for signal in self.signals:
            counts[signal.signal_type] = counts.get(signal.signal_type, 0) + 1
        return counts

    def summary(self) -> dict[str, int]:
        counts = self.signal_type_counts()
        return {
            "scanned": self.scanned_count,
            "signals": len(self.signals),
            "reclaims": counts.get(SIGNAL_RECLAIM, 0),
            "bounces": counts.get(SIGNAL_BOUNCE, 0),
            "errors": len(self.errors),
        }

    def to_history_record(self, universe_label: str = "") -> dict[str, Any]:
        generated = self.generated_at.isoformat() if self.generated_at else None
        return {
            "scanner": "vwap_reclaim",
            "generated_at": generated,
            "timeframe": self.timeframe,
            "universe": universe_label,
            "scanned": self.scanned_count,
            "matches": len(self.signals),
            "errors": len(self.errors),
            "signals": [signal.to_history_dict() for signal in self.sorted_signals()],
        }
