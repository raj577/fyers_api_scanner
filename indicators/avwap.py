"""Contract-start Anchored VWAP.

The AVWAP is cumulative from the first available candle of a futures
contract and is intentionally never reset at session boundaries.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_OHLCV = ("open", "high", "low", "close", "volume")


def validate_ohlcv(candles: pd.DataFrame) -> None:
    missing = [column for column in REQUIRED_OHLCV if column not in candles.columns]
    if missing:
        raise ValueError(f"Candle data is missing columns: {missing}")


def typical_price(candles: pd.DataFrame) -> pd.Series:
    """Return (high + low + close) / 3 aligned to the input index."""
    return (candles["high"] + candles["low"] + candles["close"]) / 3.0


def clean_volume(candles: pd.DataFrame) -> pd.Series:
    """Coerce volume to a non-negative float series; missing volume counts as 0."""
    volume = pd.to_numeric(candles["volume"], errors="coerce").fillna(0.0)
    return volume.clip(lower=0.0)


def compute_contract_avwap(
    candles: pd.DataFrame,
    anchor_index: int = 0,
    price_column: str | None = None,
) -> pd.Series:
    """Compute cumulative AVWAP from ``anchor_index`` through the latest candle.

    Parameters
    ----------
    candles:
        Chronologically sorted OHLCV DataFrame.
    anchor_index:
        Position of the first available contract candle. Defaults to 0.
    price_column:
        Optional pre-computed source price. By default the typical price is used.

    Rows before the anchor (and rows where cumulative volume is zero) are NaN.
    """

    validate_ohlcv(candles)
    if len(candles) == 0:
        return pd.Series(dtype="float64", name="avwap", index=candles.index)
    if anchor_index < 0 or anchor_index >= len(candles):
        raise ValueError(f"anchor_index {anchor_index} outside candle data")

    if price_column is None:
        price = typical_price(candles)
    else:
        if price_column not in candles.columns:
            raise ValueError(f"Missing price column: {price_column}")
        price = pd.to_numeric(candles[price_column], errors="coerce")

    price_values = price.to_numpy(dtype="float64", copy=True)
    volume_values = clean_volume(candles).to_numpy(dtype="float64", copy=True)
    price_values[~np.isfinite(price_values)] = np.nan

    result = np.full(len(candles), np.nan, dtype="float64")
    anchored_price = price_values[anchor_index:]
    anchored_volume = volume_values[anchor_index:]

    cumulative_volume = np.cumsum(anchored_volume)
    cumulative_value = np.cumsum(anchored_price * anchored_volume)
    with np.errstate(divide="ignore", invalid="ignore"):
        anchored_avwap = np.where(
            cumulative_volume > 0.0,
            cumulative_value / cumulative_volume,
            np.nan,
        )
    result[anchor_index:] = anchored_avwap

    return pd.Series(result, index=candles.index, name="avwap")


def distance_from_avwap_pct(price: float, avwap: float) -> float:
    """Signed distance of price from AVWAP in percent."""
    if not np.isfinite(price) or not np.isfinite(avwap) or avwap == 0:
        return float("nan")
    return (float(price) / float(avwap) - 1.0) * 100.0
