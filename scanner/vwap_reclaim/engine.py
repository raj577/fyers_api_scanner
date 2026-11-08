"""Scan orchestration for the VWAP reclaim / bounce scanner."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Callable, Sequence

import pandas as pd

from config import Settings
from data.fyers_client import AuthenticationError
from data.historical_data import HistoricalDataService
from indicators.vwap import compute_session_vwap
from scanner.models import Instrument
from scanner.vwap_reclaim.config import VwapReclaimConfig
from scanner.vwap_reclaim.detector import compute_volume_average, detect_latest
from scanner.vwap_reclaim.models import (
    VwapScanError,
    VwapScanReport,
)
from scanner.vwap_reclaim.timeframes import get_timeframe, prepare_candles

logger = logging.getLogger("fyers_avwap.vwap_reclaim")

ProgressCallback = Callable[[int, int, str], None]


class VwapReclaimEngine:
    """Sequential VWAP reclaim scanner. Rate limiting lives in the FYERS client."""

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
        config: VwapReclaimConfig | None = None,
        progress_callback: ProgressCallback | None = None,
        as_of: date | None = None,
        force_refresh: bool = False,
    ) -> VwapScanReport:
        config = config or VwapReclaimConfig()
        spec = get_timeframe(config.timeframe)
        today = as_of or pd.Timestamp.now(tz=self.settings.timezone).date()
        now_ts = pd.Timestamp.now(tz=self.settings.timezone)

        report = VwapScanReport(
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
                    resolution=spec.resolution,
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
                frame["avg_volume"] = compute_volume_average(
                    frame, config.volume_lookback
                )

                signal = detect_latest(
                    frame,
                    config=config,
                    symbol=instrument.symbol,
                    underlying=instrument.underlying,
                    expiry=instrument.expiry,
                    timezone=self.settings.timezone,
                )
                if signal is not None:
                    report.signals.append(signal)
                    report.histories[instrument.symbol] = frame
            except AuthenticationError:
                raise
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                logger.warning("VWAP scan skipped %s: %s", instrument.symbol, message)
                report.errors.append(VwapScanError(instrument.symbol, message))
            finally:
                if progress_callback is not None:
                    try:
                        progress_callback(position, total, instrument.symbol)
                    except Exception:
                        logger.debug("Progress callback failed", exc_info=True)

        logger.info(
            "VWAP reclaim scan complete: %s contracts, %s signals, %s errors (%s)",
            report.scanned_count,
            len(report.signals),
            len(report.errors),
            config.timeframe,
        )
        return report
