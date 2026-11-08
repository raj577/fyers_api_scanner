"""Unit tests for session-anchored timeframe resampling."""

from __future__ import annotations

import pandas as pd

from scanner.vwap_reclaim.timeframes import prepare_candles, resample_candles

TZ = "Asia/Kolkata"


def _five_minute_session() -> pd.DataFrame:
    timestamps = pd.date_range(
        "2026-01-05 09:15",
        "2026-01-05 15:25",
        freq="5min",
        tz=TZ,
    )
    rows = []
    for index, timestamp in enumerate(timestamps):
        base = 100.0 + index
        rows.append(
            {
                "timestamp": timestamp,
                "open": base,
                "high": base + 1.0,
                "low": base - 1.0,
                "close": base + 0.5,
                "volume": 10.0,
            }
        )
    return pd.DataFrame(rows)


def test_resample_15m_is_anchored_to_session_open() -> None:
    candles = _five_minute_session()
    result = resample_candles(candles, "15m", timezone=TZ)

    assert len(result) == 25
    assert result["timestamp"].iloc[0] == pd.Timestamp("2026-01-05 09:15", tz=TZ)
    assert result["timestamp"].iloc[-1] == pd.Timestamp("2026-01-05 15:15", tz=TZ)

    first_three = candles.iloc[:3]
    assert result["open"].iloc[0] == first_three["open"].iloc[0]
    assert result["high"].iloc[0] == first_three["high"].max()
    assert result["low"].iloc[0] == first_three["low"].min()
    assert result["close"].iloc[0] == first_three["close"].iloc[-1]
    assert result["volume"].iloc[0] == first_three["volume"].sum()


def test_resample_45m_drops_partial_final_bucket() -> None:
    candles = _five_minute_session()
    result = resample_candles(candles, "45m", timezone=TZ)

    assert len(result) == 8
    assert result["timestamp"].iloc[-1] == pd.Timestamp("2026-01-05 14:30", tz=TZ)
    bucket_end = result["timestamp"].iloc[-1] + pd.Timedelta(minutes=45)
    assert bucket_end <= pd.Timestamp("2026-01-05 15:30", tz=TZ)


def test_daily_resample_aggregates_one_row_per_session() -> None:
    candles = _five_minute_session()
    result = resample_candles(candles, "1d", timezone=TZ)

    assert len(result) == 1
    assert result["open"].iloc[0] == candles["open"].iloc[0]
    assert result["close"].iloc[0] == candles["close"].iloc[-1]
    assert result["volume"].iloc[0] == candles["volume"].sum()


def test_prepare_candles_drops_forming_candle() -> None:
    candles = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-01-05 09:15",
                    "2026-01-05 09:30",
                    "2026-01-05 09:45",
                    "2026-01-05 10:00",
                ]
            ).tz_localize(TZ),
            "open": [100.0, 101.0, 102.0, 103.0],
            "high": [101.0, 102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0, 102.0],
            "close": [100.5, 101.5, 102.5, 103.5],
            "volume": [10.0, 10.0, 10.0, 10.0],
        }
    )

    result = prepare_candles(
        candles,
        "15m",
        now=pd.Timestamp("2026-01-05 10:05", tz=TZ),
        timezone=TZ,
    )

    assert len(result) == 3
    assert result["timestamp"].iloc[-1] == pd.Timestamp("2026-01-05 09:45", tz=TZ)
