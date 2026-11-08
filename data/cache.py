"""Local disk caches for the instrument master and historical candles."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("fyers_avwap.cache")

_SAFE_KEY = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_key(value: str) -> str:
    return _SAFE_KEY.sub("_", value).strip("_")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


class TextFileCache:
    """Small TTL cache used for the public instrument master CSV."""

    def __init__(self, path: Path, ttl_seconds: int) -> None:
        self.path = Path(path)
        self.ttl_seconds = max(int(ttl_seconds), 0)

    def age_seconds(self) -> float | None:
        if not self.path.exists():
            return None
        return max(time.time() - self.path.stat().st_mtime, 0.0)

    def get(self, allow_stale: bool = False) -> str | None:
        age = self.age_seconds()
        if age is None:
            return None
        if not allow_stale and age > self.ttl_seconds:
            return None
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read cache %s: %s", self.path.name, exc)
            return None

    def set(self, text: str) -> None:
        _atomic_write_text(self.path, text)


class HistoricalCache:
    """Per-contract OHLCV cache stored as CSV + JSON metadata."""

    META_VERSION = 1

    def __init__(self, cache_dir: Path, ttl_seconds: int, timezone: str) -> None:
        self.directory = Path(cache_dir) / "history"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = max(int(ttl_seconds), 0)
        self.timezone = timezone
        self._lock = threading.RLock()

    def _paths(self, symbol: str, resolution: str, cont_flag: int) -> tuple[Path, Path]:
        key = f"{_safe_key(symbol)}_{_safe_key(str(resolution))}_{int(cont_flag)}"
        return self.directory / f"{key}.csv", self.directory / f"{key}.meta.json"

    def load(
        self, symbol: str, resolution: str, cont_flag: int
    ) -> tuple[pd.DataFrame, dict[str, Any]] | None:
        csv_path, meta_path = self._paths(symbol, resolution, cont_flag)
        if not csv_path.exists() or not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("version") != self.META_VERSION:
                return None
            if meta.get("symbol") != symbol or str(meta.get("resolution")) != str(resolution):
                return None
            if int(meta.get("cont_flag", -1)) != int(cont_flag):
                return None
            frame = pd.read_csv(csv_path)
            if frame.empty or "timestamp" not in frame.columns:
                return None
            return frame, meta
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring corrupt history cache for %s: %s", symbol, exc)
            return None

    def is_fresh(self, meta: dict[str, Any]) -> bool:
        fetched = meta.get("fetched_at_epoch")
        if fetched is None:
            return False
        return (time.time() - float(fetched)) <= self.ttl_seconds

    def store(
        self,
        symbol: str,
        resolution: str,
        cont_flag: int,
        candles: pd.DataFrame,
        requested_from: date,
        requested_to: date,
    ) -> None:
        if candles.empty:
            return
        csv_path, meta_path = self._paths(symbol, resolution, cont_flag)
        frame = candles.copy()
        timestamps = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
        frame = frame.loc[timestamps.notna()].copy()
        # Store integer epoch seconds independent of pandas' datetime resolution.
        epoch = (
            timestamps.loc[timestamps.notna()]
            - pd.Timestamp("1970-01-01", tz="UTC")
        ).dt.total_seconds()
        frame["timestamp"] = epoch.round().astype("int64")
        columns = ["timestamp", "open", "high", "low", "close", "volume"]
        frame = frame[[column for column in columns if column in frame.columns]]

        meta = {
            "version": self.META_VERSION,
            "symbol": symbol,
            "resolution": str(resolution),
            "cont_flag": int(cont_flag),
            "requested_from": requested_from.isoformat(),
            "requested_to": requested_to.isoformat(),
            "fetched_at_epoch": time.time(),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "rows": int(len(frame)),
        }

        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            temp_csv = csv_path.with_suffix(".csv.tmp")
            frame.to_csv(temp_csv, index=False)
            os.replace(temp_csv, csv_path)
            _atomic_write_text(meta_path, json.dumps(meta, indent=2))

    def clear(self) -> None:
        with self._lock:
            for path in self.directory.glob("*"):
                try:
                    path.unlink()
                except OSError:
                    logger.warning("Could not remove cache file %s", path.name)
