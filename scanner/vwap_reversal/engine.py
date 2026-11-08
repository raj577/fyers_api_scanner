"""Scan orchestration for the bearish-impulse VWAP reversal scanner."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Callable, Sequence

import pandas as pd

from config import Settings
from data.fyers_client import AuthenticationError
from data.historical_data import HistoricalDataService
from indicators.atr import compute_atr
from indicators.vwap import compute_session_vwap
from scanner.models import Instrument
from scanner.vwap_reclaim.timeframes import fetch_resolution, prepare_candles
from scanner.vwap_reversal.config import VwapReversalConfig
from scanner.vwap_reversal.detector import dedupe_signals, detect
from scanner.vwap_reversal.models import (
    VwapReversalReport,
    VwapReversalScanError,
)

logger = logging.getLogger("fyers_avwap.vwap_reversal")

ProgressCallback = Callable[[int, int, str], None]


class VwapReversalEngine:
    """Sequential scanner. Rate limiting lives in the shared FYERS client."""

    def __init__(
        self,
        history_service: HistoricalDataService,
        settings: Settings,
    ) -> None:
        self.history_service = history_service
        self.settings = settings

    def scan(
        self,
        instruments: Sequence[Instrument],
        config: VwapReversalConfig | None = None,
        progress_callback: ProgressCallback | None = None,
        as_of: date | None = None,
        force_refresh: bool = False,
    ) -> VwapReversalReport:
        config = config or VwapReversalConfig()
        today = as_of or pd.Timestamp.now(tz=self.settings.timezone).date()
        now_ts = pd.Timestamp.now(tz=self.settings.timezone)
        resolution = fetch_resolution(config.timeframe)

        report = VwapReversalReport(
            generated_at=datetime.now(timezone.utc),
            timeframe=config.timeframe,
            config=config,
        )
        total = len(instruments)
        report.scanned_count = total

        for position, instrument in enumerate(instruments, start=1):
            try:
                if instrument.expiry < today:
                    raise ValueError(f"Contract expired on {instrument.expiry:%Y-%m-%d}")

                candles = self.history_service.fetch(
                    instrument,
                    resolution=resolution,
                    end_date=today,
                    force_refresh=force_refresh,
                )
                prepared = prepare_candles(
                    candles,
                    config.timeframe,
                    now=now_ts,
                    timezone=self.settings.timezone,
                )
                if prepared.empty:
                    raise ValueError("No completed candles available")

                frame = prepared.copy()
                frame["vwap"] = compute_session_vwap(
                    frame, timezone=self.settings.timezone
                )
                frame["atr"] = compute_atr(frame, period=config.atr_period)

                result = detect(
                    frame,
                    config=config,
                    symbol=instrument.symbol,
                    underlying=instrument.underlying,
                    expiry=instrument.expiry,
                    timezone=self.settings.timezone,
                    lookback_candles=config.lookback_candles,
                )
                report.large_bearish_count += result.large_bearish_count

                latest = dedupe_signals(result.signals)
                if latest:
                    report.signals.extend(latest)
                    report.histories[instrument.symbol] = frame
            except AuthenticationError:
                raise
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                logger.warning(
                    "VWAP reversal scan skipped %s: %s", instrument.symbol, message
                )
                report.errors.append(VwapReversalScanError(instrument.symbol, message))
            finally:
                if progress_callback is not None:
                    try:
                        progress_callback(position, total, instrument.symbol)
                    except Exception:
                        logger.debug("Progress callback failed", exc_info=True)

        logger.info(
            "VWAP reversal scan complete: %s contracts, %s signals, %s errors (%s)",
            report.scanned_count,
            len(report.signals),
            len(report.errors),
            config.timeframe,
        )
        return report
