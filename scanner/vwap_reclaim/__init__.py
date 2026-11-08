"""VWAP reclaim / bounce scanner package."""

from scanner.vwap_reclaim.config import VwapReclaimConfig
from scanner.vwap_reclaim.detector import (
    annotate_frame,
    compute_volume_average,
    dedupe_signals,
    detect_latest,
    detect_signals,
)
from scanner.vwap_reclaim.engine import VwapReclaimEngine
from scanner.vwap_reclaim.models import (
    SIGNAL_BOUNCE,
    SIGNAL_RECLAIM,
    SIGNAL_TYPES,
    VwapReclaimSignal,
    VwapScanError,
    VwapScanReport,
)
from scanner.vwap_reclaim.timeframes import (
    DEFAULT_TIMEFRAME,
    TIMEFRAMES,
    TimeframeSpec,
    get_timeframe,
    prepare_candles,
    resample_candles,
)

__all__ = [
    "DEFAULT_TIMEFRAME",
    "SIGNAL_BOUNCE",
    "SIGNAL_RECLAIM",
    "SIGNAL_TYPES",
    "TIMEFRAMES",
    "TimeframeSpec",
    "VwapReclaimConfig",
    "VwapReclaimEngine",
    "VwapReclaimSignal",
    "VwapScanError",
    "VwapScanReport",
    "annotate_frame",
    "compute_volume_average",
    "dedupe_signals",
    "detect_latest",
    "detect_signals",
    "get_timeframe",
    "prepare_candles",
    "resample_candles",
]
