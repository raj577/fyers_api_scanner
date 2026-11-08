"""Discover and validate NSE futures contracts from the FYERS symbol master.

The public FYERS NSE_FO CSV has no header. The layout used here is validated
by parsing the symbol, expiry and underlying fields instead of hardcoding a
list of futures symbols.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import re
import time
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

from config import DEFAULT_INDEX_UNDERLYINGS, Settings
from data.cache import TextFileCache
from scanner.models import Instrument

logger = logging.getLogger("fyers_avwap.instruments")

FUTURES_PATTERN = re.compile(r"^NSE:(?P<root>.+?)(?P<expiry>\d{2}[A-Z]{3})FUT$")
MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

# Known column positions in the headerless public NSE_FO master.
COL_FY_TOKEN = 0
COL_DESCRIPTION = 1
COL_LOT_SIZE = 2
COL_TICK_SIZE = 3
COL_EXPIRY_EPOCH = 8
COL_SYMBOL = 9
COL_UNDERLYING = 13

_HEADER_ALIASES = {
    "symbol": ("symbol", "ticker", "trading_symbol"),
    "description": ("description", "desc", "name"),
    "underlying": ("underlying", "underlying_symbol", "asset"),
    "expiry": ("expiry", "expiry_epoch", "expiry_date", "expiry_timestamp"),
    "lot_size": ("lot_size", "lotsize", "lot"),
    "tick_size": ("tick_size", "ticksize", "tick"),
    "fy_token": ("fy_token", "fytoken", "token"),
}


class InstrumentMasterError(RuntimeError):
    """Raised when the futures universe cannot be loaded or validated."""


def _normalize_underlying(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def _find_header_mapping(header: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    normalized = [cell.strip().lower() for cell in header]
    for target, aliases in _HEADER_ALIASES.items():
        for index, cell in enumerate(normalized):
            if cell in aliases:
                mapping[target] = index
                break
    return mapping


def _parse_expiry(value: object, timezone: str) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None and numeric > 0:
        divisor = 1000.0 if numeric > 10_000_000_000 else 1.0
        try:
            stamp = pd.Timestamp(float(numeric) / divisor, unit="s", tz="UTC")
            return stamp.tz_convert(timezone).date()
        except (ValueError, OverflowError, OSError):
            return None
    try:
        stamp = pd.to_datetime(text, errors="coerce", dayfirst=True)
    except (ValueError, TypeError):
        return None
    if pd.isna(stamp):
        return None
    try:
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(timezone)
        else:
            stamp = stamp.tz_convert(timezone)
        return stamp.date()
    except (ValueError, TypeError):
        return stamp.date()


def _valid_futures_symbol(symbol: str, underlying: str, description: str) -> bool:
    match = FUTURES_PATTERN.match(symbol)
    if not match:
        return False
    if "FUT" not in description.upper():
        return False
    if match.group("expiry")[-3:] not in MONTHS:
        return False
    # Verify the symbol prefix agrees with the master's underlying field.
    if _normalize_underlying(match.group("root")) != _normalize_underlying(underlying):
        return False
    return True


def parse_instrument_master(
    text: str,
    timezone: str = "Asia/Kolkata",
    index_underlyings: Iterable[str] = DEFAULT_INDEX_UNDERLYINGS,
) -> pd.DataFrame:
    """Parse the FYERS NSE_FO master into a validated futures DataFrame.

    Expired rows are retained here so callers can test/filter them explicitly;
    use :func:`select_active_futures` for the tradable universe.
    """

    if not text or not text.strip():
        raise InstrumentMasterError("Instrument master is empty.")

    reader = csv.reader(io.StringIO(text))
    try:
        first_row = next(reader)
    except StopIteration as exc:
        raise InstrumentMasterError("Instrument master has no rows.") from exc

    header_map = _find_header_mapping(first_row)
    has_header = "symbol" in header_map and "expiry" in header_map
    rows = reader if has_header else _chain_first(first_row, reader)

    index_set = {_normalize_underlying(item) for item in index_underlyings}
    records: list[dict[str, object]] = []
    rejected = 0

    for row in rows:
        if not row or all(not cell.strip() for cell in row):
            continue
        try:
            if has_header:
                symbol = row[header_map["symbol"]].strip()
                description = row[header_map.get("description", -1)].strip() if "description" in header_map else ""
                underlying = row[header_map["underlying"]].strip() if "underlying" in header_map else ""
                expiry_raw = row[header_map["expiry"]]
                lot_raw = row[header_map["lot_size"]] if "lot_size" in header_map else None
                tick_raw = row[header_map["tick_size"]] if "tick_size" in header_map else None
                token = row[header_map["fy_token"]] if "fy_token" in header_map else ""
            else:
                if len(row) <= COL_UNDERLYING:
                    rejected += 1
                    continue
                token = row[COL_FY_TOKEN].strip()
                description = row[COL_DESCRIPTION].strip()
                lot_raw = row[COL_LOT_SIZE]
                tick_raw = row[COL_TICK_SIZE]
                expiry_raw = row[COL_EXPIRY_EPOCH]
                symbol = row[COL_SYMBOL].strip()
                underlying = row[COL_UNDERLYING].strip()
        except IndexError:
            rejected += 1
            continue

        if not symbol.endswith("FUT"):
            continue
        if not _valid_futures_symbol(symbol, underlying, description):
            rejected += 1
            continue

        expiry = _parse_expiry(expiry_raw, timezone)
        if expiry is None:
            rejected += 1
            continue

        records.append(
            {
                "fy_token": token,
                "symbol": symbol,
                "description": description,
                "underlying": underlying,
                "expiry": expiry,
                "lot_size": _to_float(lot_raw),
                "tick_size": _to_float(tick_raw),
                "is_index": _normalize_underlying(underlying) in index_set,
            }
        )

    if not records:
        raise InstrumentMasterError(
            "No valid NSE futures contracts found in the instrument master."
        )

    frame = pd.DataFrame.from_records(records)
    frame = frame.drop_duplicates(subset=["symbol"], keep="first")
    frame = frame.sort_values(["underlying", "expiry", "symbol"]).reset_index(drop=True)
    frame["expiry"] = pd.to_datetime(frame["expiry"]).dt.date
    logger.info(
        "Parsed %s NSE futures contracts (%s rejected rows)",
        len(frame),
        rejected,
    )
    return frame


def _chain_first(first_row: list[str], remainder) -> Iterable[list[str]]:
    yield first_row
    yield from remainder


def _to_float(value: object) -> float | None:
    try:
        number = float(str(value).strip())
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def select_active_futures(
    master: pd.DataFrame,
    as_of: date | None = None,
    current_expiry_only: bool = False,
    include_index: bool = True,
) -> pd.DataFrame:
    """Return non-expired NSE futures, optionally only nearest expiry per underlying."""

    if master is None or master.empty:
        return master.copy() if master is not None else pd.DataFrame()
    if as_of is None:
        as_of = pd.Timestamp.now(tz="Asia/Kolkata").date()

    frame = master.copy()
    if "is_index" not in frame.columns:
        frame["is_index"] = False
    frame["expiry"] = pd.to_datetime(frame["expiry"]).dt.date
    frame = frame.loc[frame["expiry"] >= as_of].copy()
    if not include_index:
        frame = frame.loc[~frame["is_index"].astype(bool)].copy()
    if frame.empty:
        return frame

    frame["is_current_expiry"] = frame.groupby("underlying")["expiry"].transform("min") == frame["expiry"]
    if current_expiry_only:
        frame = frame.loc[frame["is_current_expiry"]].copy()
    return frame.sort_values(["underlying", "expiry", "symbol"]).reset_index(drop=True)


def to_instruments(frame: pd.DataFrame) -> list[Instrument]:
    instruments: list[Instrument] = []
    for row in frame.itertuples(index=False):
        instruments.append(
            Instrument(
                symbol=str(row.symbol),
                underlying=str(row.underlying),
                expiry=row.expiry,
                description=str(getattr(row, "description", "") or ""),
                lot_size=(
                    float(row.lot_size)
                    if getattr(row, "lot_size", None) is not None
                    and pd.notna(getattr(row, "lot_size"))
                    else None
                ),
                is_index=bool(getattr(row, "is_index", False)),
            )
        )
    return instruments


class InstrumentMasterProvider:
    """Downloads, caches and parses the FYERS NSE_FO symbol master."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.raw_path = Path(settings.cache_dir) / "instruments" / "NSE_FO.csv"
        self._cache = TextFileCache(self.raw_path, settings.instrument_cache_ttl_seconds)
        self._futures: pd.DataFrame | None = None
        self._loaded_at: float = 0.0

    def _download(self) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; fyers-avwap-scanner/1.0; "
                "+https://fyers.in)"
            )
        }
        response = requests.get(
            self.settings.instrument_master_url,
            timeout=self.settings.instrument_timeout_seconds,
            headers=headers,
        )
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        text = response.text
        if not text or "NSE:" not in text:
            raise InstrumentMasterError("Downloaded instrument master does not look valid.")
        self._cache.set(text)
        logger.info("Downloaded FYERS instrument master (%s bytes)", len(text))
        return text

    def load(self, force: bool = False) -> pd.DataFrame:
        if not force and self._futures is not None and (time.time() - self._loaded_at) <= self.settings.instrument_cache_ttl_seconds:
            return self._futures.copy()

        text = None if force else self._cache.get()
        if text is None:
            try:
                text = self._download()
            except Exception as exc:
                stale = self._cache.get(allow_stale=True)
                if stale is None:
                    raise InstrumentMasterError(
                        f"Could not download the FYERS instrument master: {exc}"
                    ) from exc
                logger.warning(
                    "Instrument master download failed; using stale cache: %s", exc
                )
                text = stale

        self._futures = parse_instrument_master(
            text,
            timezone=self.settings.timezone,
            index_underlyings=self.settings.index_underlyings,
        )
        self._loaded_at = time.time()
        return self._futures.copy()

    def active_futures(
        self,
        as_of: date | None = None,
        current_expiry_only: bool = False,
        include_index: bool | None = None,
        force: bool = False,
    ) -> pd.DataFrame:
        if include_index is None:
            include_index = self.settings.include_index_futures
        master = self.load(force=force)
        return select_active_futures(
            master,
            as_of=as_of,
            current_expiry_only=current_expiry_only,
            include_index=include_index,
        )

    def instruments(
        self,
        as_of: date | None = None,
        current_expiry_only: bool = False,
        include_index: bool | None = None,
        force: bool = False,
    ) -> list[Instrument]:
        return to_instruments(
            self.active_futures(
                as_of=as_of,
                current_expiry_only=current_expiry_only,
                include_index=include_index,
                force=force,
            )
        )
