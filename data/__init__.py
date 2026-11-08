"""FYERS data access, instrument discovery and historical candle services."""

from data.cache import HistoricalCache, TextFileCache
from data.fyers_client import (
    ApiError,
    AuthenticationError,
    FyersClient,
    FyersError,
    RateLimitError,
)
from data.historical_data import (
    HistoricalDataService,
    NoMarketDataError,
    drop_incomplete_candles,
    normalize_candles,
)
from data.instrument_master import (
    InstrumentMasterError,
    InstrumentMasterProvider,
    parse_instrument_master,
    select_active_futures,
    to_instruments,
)

__all__ = [
    "ApiError",
    "AuthenticationError",
    "FyersClient",
    "FyersError",
    "HistoricalCache",
    "HistoricalDataService",
    "InstrumentMasterError",
    "InstrumentMasterProvider",
    "NoMarketDataError",
    "RateLimitError",
    "TextFileCache",
    "drop_incomplete_candles",
    "normalize_candles",
    "parse_instrument_master",
    "select_active_futures",
    "to_instruments",
]
