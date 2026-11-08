"""Unit tests for the bearish-impulse VWAP reversal scanner."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from scanner.vwap_reclaim.timeframes import prepare_candles, resample_candles
from scanner.vwap_reversal.candle_patterns import (
    candle_shape,
    classify_vwap_interaction,
    is_doji,
    is_inverted_hammer,
    is_large_bearish_candle,
)
from scanner.vwap_reversal.config import VwapReversalConfig
from scanner.vwap_reversal.detector import (
    dedupe_signals,
    detect_signals,
)
from scanner.vwap_reversal.models import (
    CONFIRMATION_ABOVE_CLOSE,
    CONFIRMATION_AWAITING,
    CONFIRMATION_BULLISH,
    SIGNAL_DOJI,
    SIGNAL_INVERTED_HAMMER,
    SIGNAL_NEAR_TOUCH,
    TOUCH_EXACT,
    TOUCH_NEAR,
)

TZ = "Asia/Kolkata"
START = pd.Timestamp("2026-01-05 09:15", tz=TZ)


def _ts(index: int) -> pd.Timestamp:
    return START + pd.Timedelta(minutes=15 * index)


def _flat_rows(count: int, price: float = 100.0, start: int = 0) -> list[dict]:
    rows = []
    for index in range(start, start + count):
        rows.append(
            {
                "timestamp": _ts(index),
                "open": price,
                "high": price + 0.05,
                "low": price - 0.05,
                "close": price,
                "volume": 100.0,
            }
        )
    return rows


def _row(
    index: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
) -> dict:
    return {
        "timestamp": _ts(index),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _frame(rows: list[dict], vwap: float | None = 100.0) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if vwap is not None:
        frame["vwap"] = vwap
    return frame


def _config(**overrides: object) -> VwapReversalConfig:
    values: dict[str, object] = {
        "timeframe": "15m",
        "min_bearish_body_ratio": 0.60,
        "min_bearish_atr_multiple": 1.5,
        "doji_body_max_ratio": 0.15,
        "inverted_hammer_upper_wick_ratio": 2.0,
        "inverted_hammer_body_max_ratio": 0.35,
        "vwap_tolerance_pct": 0.10,
        "allowed_touch_types": (TOUCH_EXACT,),
        "enable_bullish_confirmation": False,
        "lookback_candles": 60,
    }
    values.update(overrides)
    return VwapReversalConfig(**values)  # type: ignore[arg-type]


def _bearish(index: int) -> dict:
    return _row(index, 105.0, 105.2, 99.8, 100.0)


def _doji(index: int) -> dict:
    return _row(index, 100.05, 100.3, 99.8, 100.0)


def _inverted_hammer(index: int) -> dict:
    return _row(index, 100.4, 101.2, 99.9, 100.0)


def _pair_frame(second: dict, vwap: float = 100.0) -> pd.DataFrame:
    rows = _flat_rows(20) + [_bearish(20), second]
    return _frame(rows, vwap=vwap)


def test_large_bearish_candle_detection() -> None:
    shape = candle_shape(105.0, 105.2, 99.8, 100.0)
    qualifies, multiple = is_large_bearish_candle(
        shape, atr=2.0, min_body_ratio=0.60, min_atr_multiple=1.5
    )

    assert qualifies is True
    assert multiple is not None and multiple == pytest.approx(2.5)

    bullish = candle_shape(100.0, 105.2, 99.8, 105.0)
    assert is_large_bearish_candle(
        bullish, atr=2.0, min_body_ratio=0.60, min_atr_multiple=1.5
    )[0] is False


def test_small_body_doji_detection() -> None:
    shape = candle_shape(100.05, 100.3, 99.8, 100.0)

    assert is_doji(shape, body_max_ratio=0.15) is True
    assert is_inverted_hammer(
        shape, upper_wick_ratio=2.0, body_max_ratio=0.35
    ) is False


def test_inverted_hammer_detection() -> None:
    shape = candle_shape(100.4, 101.2, 99.9, 100.0)

    assert is_inverted_hammer(
        shape, upper_wick_ratio=2.0, body_max_ratio=0.35
    ) is True
    assert is_doji(shape, body_max_ratio=0.15) is False


def test_invalid_inverted_hammer_rejection() -> None:
    long_lower = candle_shape(100.4, 101.2, 98.9, 100.0)
    assert is_inverted_hammer(
        long_lower, upper_wick_ratio=2.0, body_max_ratio=0.35
    ) is False

    big_body = candle_shape(102.0, 103.0, 99.0, 100.0)
    assert is_inverted_hammer(
        big_body, upper_wick_ratio=2.0, body_max_ratio=0.15
    ) is False

    zero_wick = candle_shape(100.0, 100.0, 100.0, 100.0)
    assert is_inverted_hammer(
        zero_wick, upper_wick_ratio=2.0, body_max_ratio=0.35
    ) is False


def test_exact_vwap_touch() -> None:
    assert (
        classify_vwap_interaction(
            low=99.5, high=100.5, close=100.1, vwap=100.0, tolerance_pct=0.10
        )
        == TOUCH_EXACT
    )


def test_near_vwap_classification() -> None:
    assert (
        classify_vwap_interaction(
            low=100.02, high=100.4, close=100.08, vwap=100.0, tolerance_pct=0.10
        )
        == TOUCH_NEAR
    )


def test_candle_that_does_not_touch_vwap() -> None:
    assert (
        classify_vwap_interaction(
            low=100.5, high=101.0, close=100.8, vwap=100.0, tolerance_pct=0.10
        )
        is None
    )


def test_correct_candle_sequence_detects_doji_and_hammer() -> None:
    doji_signals = detect_signals(
        _pair_frame(_doji(21)), _config(), symbol="NSE:DOJIFUT"
    )
    assert len(doji_signals) == 1
    assert doji_signals[0].signal_type == SIGNAL_DOJI
    assert doji_signals[0].touch_type == TOUCH_EXACT

    hammer_signals = detect_signals(
        _pair_frame(_inverted_hammer(21)), _config(), symbol="NSE:IHFUT"
    )
    assert len(hammer_signals) == 1
    assert hammer_signals[0].signal_type == SIGNAL_INVERTED_HAMMER


def test_wrong_candle_sequence_is_rejected() -> None:
    bullish_first = _row(20, 100.0, 105.2, 99.8, 105.0)
    rows = _flat_rows(20) + [bullish_first, _doji(21)]
    assert detect_signals(_frame(rows), _config(), symbol="NSE:BULLFUT") == []

    bearish_second = _row(21, 101.0, 101.0, 95.0, 96.0)
    rows = _flat_rows(20) + [_bearish(20), bearish_second]
    assert detect_signals(_frame(rows), _config(), symbol="NSE:SEQ2FUT") == []


def test_incomplete_candle_is_excluded() -> None:
    rows = _flat_rows(20) + [_bearish(20), _doji(21), _row(22, 100.5, 101.0, 100.2, 100.8)]
    frame = _frame(rows)

    completed = prepare_candles(
        frame, "15m", now=pd.Timestamp("2026-01-05 14:46", tz=TZ), timezone=TZ
    )
    assert completed["timestamp"].max() == _ts(21)

    not_completed = prepare_candles(
        frame, "15m", now=pd.Timestamp("2026-01-05 14:37", tz=TZ), timezone=TZ
    )
    assert not_completed["timestamp"].max() == _ts(20)


def test_historical_vwap_uses_the_second_candles_own_session() -> None:
    day_one = [
        {
            "timestamp": pd.Timestamp("2026-01-05 09:15", tz=TZ)
            + pd.Timedelta(minutes=15 * index),
            "open": 200.0,
            "high": 200.05,
            "low": 199.95,
            "close": 200.0,
            "volume": 100.0,
        }
        for index in range(20)
    ]
    day_two = [
        {
            "timestamp": pd.Timestamp("2026-01-06 09:15", tz=TZ)
            + pd.Timedelta(minutes=15 * index),
            "open": 100.0,
            "high": 100.05,
            "low": 99.95,
            "close": 100.0,
            "volume": 100.0,
        }
        for index in range(5)
    ]
    day_two.append(
        {
            "timestamp": pd.Timestamp("2026-01-06 10:30", tz=TZ),
            "open": 105.0,
            "high": 105.2,
            "low": 99.8,
            "close": 100.0,
            "volume": 100.0,
        }
    )
    day_two.append(
        {
            "timestamp": pd.Timestamp("2026-01-06 10:45", tz=TZ),
            "open": 100.05,
            "high": 100.8,
            "low": 99.8,
            "close": 100.0,
            "volume": 100.0,
        }
    )

    frame = pd.DataFrame(day_one + day_two)
    signals = detect_signals(
        frame,
        _config(min_bearish_atr_multiple=0.0),
        symbol="NSE:SESSIONFUT",
    )

    assert len(signals) == 1
    signal = signals[0]
    assert 99.0 < signal.vwap < 102.0
    assert signal.touch_type == TOUCH_EXACT


def test_third_candle_bullish_confirmation() -> None:
    confirmed_rows = _flat_rows(20) + [
        _bearish(20),
        _doji(21),
        _row(22, 100.5, 101.6, 100.4, 101.2),
    ]
    confirmed = detect_signals(
        _frame(confirmed_rows),
        _config(enable_bullish_confirmation=True),
        symbol="NSE:CONFFUT",
    )
    assert len(confirmed) == 1
    assert confirmed[0].confirmation_status == CONFIRMATION_BULLISH
    assert confirmed[0].is_confirmed is True

    weak_rows = _flat_rows(20) + [
        _bearish(20),
        _doji(21),
        _row(22, 100.1, 100.4, 100.05, 100.15),
    ]
    weak = detect_signals(
        _frame(weak_rows),
        _config(enable_bullish_confirmation=True),
        symbol="NSE:WEAKFUT",
    )
    assert len(weak) == 1
    assert weak[0].confirmation_status == CONFIRMATION_AWAITING

    relaxed = detect_signals(
        _frame(weak_rows),
        _config(
            enable_bullish_confirmation=True,
            confirmation_mode=CONFIRMATION_ABOVE_CLOSE,
        ),
        symbol="NSE:WEAKFUT",
    )
    assert len(relaxed) == 1
    assert relaxed[0].confirmation_status == CONFIRMATION_BULLISH


def test_multi_timeframe_resampling_and_detection() -> None:
    five_minute = []
    for index in range(75):
        timestamp = pd.Timestamp("2026-01-05 09:15", tz=TZ) + pd.Timedelta(
            minutes=5 * index
        )
        price = 100.0 + index * 0.01
        five_minute.append(
            {
                "timestamp": timestamp,
                "open": price,
                "high": price + 0.05,
                "low": price - 0.05,
                "close": price,
                "volume": 10.0,
            }
        )
    base = pd.DataFrame(five_minute)

    fifteen = resample_candles(base, "15m", timezone=TZ)
    hourly = resample_candles(base, "1h", timezone=TZ)
    daily = resample_candles(base, "1d", timezone=TZ)

    assert len(fifteen) == 25
    assert len(hourly) == 6
    assert hourly["timestamp"].iloc[0] == pd.Timestamp("2026-01-05 09:15", tz=TZ)
    assert len(daily) == 1

    assert isinstance(
        detect_signals(fifteen, _config(timeframe="15m"), symbol="NSE:MTFFUT"),
        list,
    )

    with pytest.raises(ValueError):
        VwapReversalConfig(timeframe="7m")


def test_no_look_ahead_bias() -> None:
    base_rows = _flat_rows(20) + [_bearish(20), _doji(21)]
    future_rows = base_rows + _flat_rows(3, start=22)
    future_rows[-1] = _row(24, 100.0, 140.0, 99.0, 130.0)

    base_signals = detect_signals(
        _frame(base_rows), _config(), symbol="NSE:LOOKAHEADFUT"
    )
    future_signals = detect_signals(
        _frame(future_rows), _config(), symbol="NSE:LOOKAHEADFUT"
    )

    assert len(base_signals) == 1
    earlier = [
        signal
        for signal in future_signals
        if signal.second_timestamp <= base_signals[0].second_timestamp
    ]
    assert len(earlier) == 1
    assert earlier[0].to_history_dict() == base_signals[0].to_history_dict()


def test_duplicate_signal_prevention() -> None:
    signal = detect_signals(
        _pair_frame(_doji(21)), _config(), symbol="NSE:DUPFUT"
    )[0]
    older = replace(
        signal,
        second_timestamp=signal.second_timestamp - pd.Timedelta(minutes=15),
    )
    newer = replace(signal, second_timestamp=signal.second_timestamp)

    deduped = dedupe_signals([older, newer, newer])

    assert len(deduped) == 1
    assert deduped[0].second_timestamp == newer.second_timestamp


def test_near_touch_is_labelled_separately() -> None:
    near_second = _row(21, 100.08, 100.4, 100.02, 100.08)
    rows = _flat_rows(20) + [_bearish(20), near_second]

    exact_only = detect_signals(
        _frame(rows), _config(), symbol="NSE:NEARFUT"
    )
    assert exact_only == []

    both = detect_signals(
        _frame(rows),
        _config(allowed_touch_types=(TOUCH_EXACT, TOUCH_NEAR)),
        symbol="NSE:NEARFUT",
    )
    assert len(both) == 1
    assert both[0].signal_type == SIGNAL_NEAR_TOUCH
    assert both[0].touch_type == TOUCH_NEAR


def test_atr_multiple_filter_rejects_weak_impulse() -> None:
    rows = _flat_rows(20) + [_bearish(20), _doji(21)]
    signals = detect_signals(
        _frame(rows),
        _config(min_bearish_atr_multiple=100.0),
        symbol="NSE:ATRFUT",
    )
    assert signals == []


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        VwapReversalConfig(min_bearish_body_ratio=1.5)
    with pytest.raises(ValueError):
        VwapReversalConfig(doji_body_max_ratio=-0.1)
    with pytest.raises(ValueError):
        VwapReversalConfig(allowed_touch_types=())
