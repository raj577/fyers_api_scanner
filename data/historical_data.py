"""Fetch, normalize and cache FYERS historical OHLCV candles."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype

from config import Settings
from data.cache import HistoricalCache
from data.fyers_client import AuthenticationError, FyersClient, FyersError
from scanner.models import Instrument

logger = logging.getLogger("fyers_avwap.history")

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class NoMarketDataError(RuntimeError):
    """No usable candles were returned for a contract."""


def _epoch_unit(values: pd.Series) -> str:
    numeric = pd.to_numeric(values, errors="coerce")
    median = float(numeric.dropna().abs().median()) if numeric.notna().any() else 0.0
    if median >= 1e15:
        return "us"
    if median >= 1e11:
        return "ms"
    return "s"


def normalize_candles(
    raw: Iterable[Iterable[Any]] | pd.DataFrame,
    timezone: str = "Asia/Kolkata",
) -> pd.DataFrame:
    """Convert raw FYERS rows to a clean, sorted, tz-aware OHLCV DataFrame."""

    if isinstance(raw, pd.DataFrame):
        frame = raw.copy()
        if "timestamp" not in frame.columns:
            if frame.index.name == "timestamp":
                frame = frame.reset_index()
            else:
                raise ValueError("Candle DataFrame has no timestamp column")
    else:
        rows = [list(row)[:6] for row in raw if row is not None and len(row) >= 6]
        if not rows:
            return pd.DataFrame(columns=OHLCV_COLUMNS)
        frame = pd.DataFrame(rows, columns=OHLCV_COLUMNS)

    if frame.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    source_timestamps = frame["timestamp"]
    if is_datetime64_any_dtype(source_timestamps):
        parsed = pd.to_datetime(source_timestamps, errors="coerce", utc=True)
    elif is_numeric_dtype(source_timestamps):
        unit = _epoch_unit(source_timestamps)
        parsed = pd.to_datetime(
            pd.to_numeric(source_timestamps, errors="coerce"),
            unit=unit,
            utc=True,
            errors="coerce",
        )
    else:
        numeric = pd.to_numeric(source_timestamps, errors="coerce")
        looks_numeric = numeric.notna().mean() >= 0.8
        if looks_numeric and abs(float(numeric.dropna().median())) > 1e8:
            unit = _epoch_unit(numeric)
            parsed = pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")
        else:
            parsed = pd.to_datetime(source_timestamps, errors="coerce", utc=True)
    frame["timestamp"] = pd.Series(parsed, index=frame.index).dt.tz_convert(timezone)

    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close"])
    frame["volume"] = frame["volume"].fillna(0.0).clip(lower=0.0)
    frame = frame.loc[frame["high"] >= frame["low"]]
    frame = (
        frame.sort_values("timestamp")
        .drop_duplicates(subset=["timestamp"], keep="last")
        .reset_index(drop=True)
    )
    return frame[OHLCV_COLUMNS]


def resolution_minutes(resolution: str) -> int | None:
    """Return interval minutes for intraday resolutions, or None for daily."""
    text = str(resolution).strip().upper()
    if text in {"D", "1D", "DAY", "DAILY"}:
        return None
    match = re.fullmatch(r"(\d+)\s*(?:MIN|M)?", text)
    if match:
        minutes = int(match.group(1))
        if minutes <= 0:
            raise ValueError(f"Invalid resolution: {resolution}")
        return minutes
    raise ValueError(f"Unsupported resolution: {resolution}")


def drop_incomplete_candles(
    candles: pd.DataFrame,
    resolution: str,
    now: pd.Timestamp | datetime | None = None,
    timezone: str = "Asia/Kolkata",
) -> pd.DataFrame:
    """Drop the currently forming candle so detection uses completed bars only."""

    if candles.empty:
        return candles.copy()

    if now is None:
        now_ts = pd.Timestamp.now(tz=timezone)
    else:
        now_ts = pd.Timestamp(now)
        if now_ts.tzinfo is None:
            now_ts = now_ts.tz_localize(timezone)
        else:
            now_ts = now_ts.tz_convert(timezone)

    timestamps = pd.to_datetime(candles["timestamp"])
    if not isinstance(timestamps.dtype, pd.DatetimeTZDtype):
        timestamps = timestamps.dt.tz_localize(timezone)
    else:
        timestamps = timestamps.dt.tz_convert(timezone)

    minutes = resolution_minutes(resolution)
    if minutes is None:
        expected_end = timestamps + pd.Timedelta(days=1)
    else:
        expected_end = timestamps + pd.Timedelta(minutes=minutes)
        # NSE equity/F&O sessions close at 15:30 IST. A final short candle
        # (for example 15:15-15:30) is complete at the session close, not one
        # full resolution later.
        session_close = timestamps.dt.normalize() + pd.Timedelta(hours=15, minutes=30)
        expected_end = expected_end.where(expected_end <= session_close, session_close)
    completed = expected_end <= now_ts
    return candles.loc[completed].reset_index(drop=True)


def _parse_iso_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class HistoricalDataService:
    """Chunked, cached, rate-limited historical candle retrieval."""

    def __init__(
        self,
        client: FyersClient,
        cache: HistoricalCache,
        settings: Settings,
    ) -> None:
        self.client = client
        self.cache = cache
        self.settings = settings

    def fetch(
        self,
        instrument: Instrument,
        resolution: str | None = None,
        end_date: date | None = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Return the contract's history from its first available candle.

        The lookback window is intentionally longer than an NSE futures
        contract's maximum tenure; the first returned candle is the AVWAP anchor.
        """

        resolution = str(resolution or self.settings.resolution)
        cont_flag = int(self.settings.history_cont_flag)
        if end_date is None:
            end_date = pd.Timestamp.now(tz=self.settings.timezone).date()
        desired_end = min(end_date, instrument.expiry)
        desired_start = desired_end - timedelta(days=max(self.settings.contract_lookback_days, 1))

        cached = None if force_refresh else self.cache.load(
            instrument.symbol, resolution, cont_flag
        )
        cached_frame: pd.DataFrame | None = None
        cached_meta: dict[str, Any] = {}
        if cached is not None:
            cached_frame, cached_meta = cached
            cached_frame = normalize_candles(cached_frame, self.settings.timezone)
            requested_from = _parse_iso_date(cached_meta.get("requested_from"))
            requested_to = _parse_iso_date(cached_meta.get("requested_to"))
            covers = (
                requested_from is not None
                and requested_to is not None
                and requested_from <= desired_start
                and requested_to >= desired_end
            )
            if self.cache.is_fresh(cached_meta) and covers:
                logger.debug("Fresh cache hit for %s", instrument.symbol)
                return self._clip(cached_frame, desired_end)

        try:
            if cached_frame is not None and not cached_frame.empty:
                requested_from = _parse_iso_date(cached_meta.get("requested_from"))
                if requested_from is not None and requested_from <= desired_start:
                    last_date = cached_frame["timestamp"].max().date()
                    fetch_start = max(
                        desired_start,
                        last_date - timedelta(days=1),
                    )
                    new_frame = self._fetch_range(
                        instrument.symbol,
                        resolution,
                        fetch_start,
                        desired_end,
                        cont_flag,
                    )
                    merged = self._merge(cached_frame, new_frame)
                    store_from = requested_from
                else:
                    merged = self._fetch_range(
                        instrument.symbol,
                        resolution,
                        desired_start,
                        desired_end,
                        cont_flag,
                    )
                    store_from = desired_start
            else:
                merged = self._fetch_range(
                    instrument.symbol,
                    resolution,
                    desired_start,
                    desired_end,
                    cont_flag,
                )
                store_from = desired_start
        except AuthenticationError:
            raise
        except FyersError as exc:
            if cached_frame is not None and not cached_frame.empty:
                logger.warning(
                    "History refresh failed for %s; using cached candles: %s",
                    instrument.symbol,
                    exc,
                )
                return self._clip(cached_frame, desired_end)
            raise

        merged = self._clip(merged, desired_end)
        if merged.empty:
            raise NoMarketDataError(
                f"No completed historical candles available for {instrument.symbol}."
            )

        try:
            self.cache.store(
                instrument.symbol,
                resolution,
                cont_flag,
                merged,
                requested_from=store_from,
                requested_to=desired_end,
            )
        except OSError as exc:
            logger.warning("Could not write history cache for %s: %s", instrument.symbol, exc)

        logger.debug(
            "Fetched %s candles for %s (%s to %s)",
            len(merged),
            instrument.symbol,
            merged["timestamp"].min().date(),
            merged["timestamp"].max().date(),
        )
        return merged

    def _fetch_range(
        self,
        symbol: str,
        resolution: str,
        start: date,
        end: date,
        cont_flag: int,
    ) -> pd.DataFrame:
        if start > end:
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        chunk_days = max(int(self.settings.history_chunk_days), 1)
        rows: list[list[Any]] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
            rows.extend(
                self.client.history(
                    symbol=symbol,
                    resolution=resolution,
                    range_from=cursor,
                    range_to=chunk_end,
                    cont_flag=cont_flag,
                )
            )
            cursor = chunk_end + timedelta(days=1)

        if not rows:
            return pd.DataFrame(columns=OHLCV_COLUMNS)
        return normalize_candles(rows, self.settings.timezone)

    @staticmethod
    def _merge(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
        if existing.empty:
            return new.copy()
        if new.empty:
            return existing.copy()
        merged = pd.concat([existing, new], ignore_index=True)
        return (
            merged.sort_values("timestamp")
            .drop_duplicates(subset=["timestamp"], keep="last")
            .reset_index(drop=True)
        )

    @staticmethod
    def _clip(candles: pd.DataFrame, end_date: date) -> pd.DataFrame:
        if candles.empty:
            return candles.copy()
        timestamps = pd.to_datetime(candles["timestamp"])
        mask = timestamps.dt.date <= end_date
        return candles.loc[mask].reset_index(drop=True)
