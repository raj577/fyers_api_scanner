"""Unit tests for the VWAP reclaim detector."""

from __future__ import annotations

import pandas as pd
import pytest

from scanner.vwap_reclaim.config import VwapReclaimConfig
from scanner.vwap_reclaim.detector import detect_latest, detect_signals
from scanner.vwap_reclaim.models import SIGNAL_BOUNCE, SIGNAL_RECLAIM

TZ = "Asia/Kolkata"


def _frame(rows: list[tuple[str, float, float, float, float, float]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        rows,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize(TZ)
    return frame


def _config(**overrides: object) -> VwapReclaimConfig:
    values = {
        "timeframe": "15m",
        "max_distance_pct": 0.05,
        "min_distance_pct": 0.0,
        "max_previous_below_pct": 0.10,
        "volume_lookback": 20,
    }
    values.update(overrides)
    return VwapReclaimConfig(**values)  # type: ignore[arg-type]


def test_reclaim_detected_when_previous_close_below_vwap() -> None:
    candles = _frame(
        [
            ("2026-01-05 09:15", 100.0, 101.0, 99.0, 99.5, 100.0),
            ("2026-01-05 09:30", 100.0, 106.0, 99.5, 105.0, 100.0),
        ]
    )

    signals = detect_signals(candles, _config(), symbol="NSE:TESTFUT")

    assert len(signals) == 1
    assert signals[0].signal_type == SIGNAL_RECLAIM
    assert signals[0].symbol == "NSE:TESTFUT"
    assert signals[0].distance_pct > 0

    filtered = _config(require_current_low_below_vwap=True)
    assert len(detect_signals(candles, filtered, symbol="NSE:TESTFUT")) == 1


def test_bounce_takes_precedence_when_previous_low_wicked_below() -> None:
    candles = _frame(
        [
            ("2026-01-05 09:15", 100.0, 103.0, 98.0, 102.0, 100.0),
            ("2026-01-05 09:30", 102.0, 106.0, 101.5, 105.0, 100.0),
        ]
    )

    signals = detect_signals(candles, _config(), symbol="NSE:TESTFUT")

    assert len(signals) == 1
    assert signals[0].signal_type == SIGNAL_BOUNCE


def test_no_signal_when_current_close_stays_below_vwap() -> None:
    candles = _frame(
        [
            ("2026-01-05 09:15", 100.0, 101.0, 95.0, 96.0, 100.0),
            ("2026-01-05 09:30", 96.0, 98.0, 95.0, 96.5, 100.0),
        ]
    )

    assert detect_signals(candles, _config(), symbol="NSE:TESTFUT") == []


def test_distance_filter_rejects_extended_reclaim() -> None:
    candles = _frame(
        [
            ("2026-01-05 09:15", 100.0, 101.0, 99.0, 99.5, 100.0),
            ("2026-01-05 09:30", 100.0, 106.0, 99.5, 105.0, 100.0),
        ]
    )

    config = _config(max_distance_pct=0.005)
    assert detect_signals(candles, config, symbol="NSE:TESTFUT") == []


def test_current_low_below_vwap_filter() -> None:
    candles = _frame(
        [
            ("2026-01-05 09:15", 100.0, 101.0, 98.0, 99.0, 100.0),
            ("2026-01-05 09:30", 105.0, 108.0, 104.0, 107.0, 100.0),
        ]
    )

    assert len(detect_signals(candles, _config(), symbol="NSE:TESTFUT")) == 1

    filtered = _config(require_current_low_below_vwap=True)
    assert detect_signals(candles, filtered, symbol="NSE:TESTFUT") == []


def test_volume_confirmation_filter() -> None:
    candles = _frame(
        [
            ("2026-01-05 09:15", 100.0, 101.0, 99.0, 99.5, 100.0),
            ("2026-01-05 09:30", 100.0, 106.0, 99.5, 105.0, 100.0),
        ]
    )

    config = _config(require_volume_confirmation=True, min_volume_ratio=5.0)
    assert detect_signals(candles, config, symbol="NSE:TESTFUT") == []


def test_detect_latest_only_checks_last_pair() -> None:
    candles = _frame(
        [
            ("2026-01-05 09:15", 100.0, 101.0, 99.0, 99.5, 100.0),
            ("2026-01-05 09:30", 100.0, 106.0, 99.5, 105.0, 100.0),
            ("2026-01-05 09:45", 105.0, 105.5, 100.5, 101.0, 100.0),
        ]
    )

    assert detect_latest(candles, _config(), symbol="NSE:TESTFUT") is None


def test_config_rejects_unknown_timeframe() -> None:
    with pytest.raises(ValueError):
        VwapReclaimConfig(timeframe="7m")
