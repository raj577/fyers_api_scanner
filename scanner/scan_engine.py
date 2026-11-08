"""Scan orchestration: history -> indicators -> pattern detection."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Callable, Sequence

import pandas as pd

from config import Settings
from data.fyers_client import AuthenticationError
from data.historical_data import HistoricalDataService, drop_incomplete_candles
from indicators.atr import compute_atr
from indicators.avwap import compute_contract_avwap
from indicators.vwap import compute_session_vwap
from scanner.models import (
    Instrument,
    PatternConfig,
    ScanError,
    ScanMatch,
    ScanReport,
)
from scanner.pattern_detector import PatternDetector

logger = logging.getLogger("fyers_avwap.scanner")

ProgressCallback = Callable[[int, int, str], None]


class ScanEngine:
    """Sequential scanner. Rate limiting happens inside the FYERS client."""

    def __init__(
        self,
        history_service: HistoricalDataService,
        settings: Settings,
        detector: PatternDetector | None = None,
    ) -> None:
        self.history_service = history_service
        self.settings = settings
        self.detector = detector or PatternDetector()

    def scan(
        self,
        instruments: Sequence[Instrument],
        pattern_config: PatternConfig | None = None,
        resolution: str | None = None,
        as_of: date | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ScanReport:
        resolution = str(resolution or self.settings.resolution)
        config = pattern_config or self.detector.config
        detector = PatternDetector(config)
        today = as_of or pd.Timestamp.now(tz=self.settings.timezone).date()
        now_ts = pd.Timestamp.now(tz=self.settings.timezone)

        report = ScanReport(
            generated_at=datetime.now(timezone.utc),
            resolution=resolution,
            pattern_config=config,
        )
        total = len(instruments)
        report.scanned_count = total

        for position, instrument in enumerate(instruments, start=1):
            try:
                if instrument.expiry < today:
                    raise ValueError(
                        f"Contract expired on {instrument.expiry:%Y-%m-%d}"
                    )

                candles = self.history_service.fetch(
                    instrument,
                    resolution=resolution,
                    end_date=today,
                )
                candles = drop_incomplete_candles(
                    candles,
                    resolution=resolution,
                    now=now_ts,
                    timezone=self.settings.timezone,
                )
                if candles.empty:
                    raise ValueError("No completed candles available")

                frame = candles.copy()
                frame["avwap"] = compute_contract_avwap(frame, anchor_index=0)
                frame["session_vwap"] = compute_session_vwap(
                    frame, timezone=self.settings.timezone
                )
                frame["atr"] = compute_atr(frame, period=14)

                detected = detector.detect(frame)
                if detected is not None:
                    match = ScanMatch(
                        instrument=instrument,
                        pattern=detected,
                        resolution=resolution,
                    )
                    report.matches.append(match)
                    report.histories[instrument.symbol] = frame
            except AuthenticationError:
                # A dead token invalidates the whole run; let the UI prompt login.
                raise
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                logger.warning("Scan skipped %s: %s", instrument.symbol, message)
                report.errors.append(ScanError(instrument.symbol, message))
            finally:
                if progress_callback is not None:
                    try:
                        progress_callback(position, total, instrument.symbol)
                    except Exception:
                        logger.debug("Progress callback failed", exc_info=True)

        logger.info(
            "Scan complete: %s contracts, %s matches, %s errors",
            report.scanned_count,
            len(report.matches),
            len(report.errors),
        )
        return report
