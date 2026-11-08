"""Application configuration and logging setup.

Credentials are read exclusively from environment variables / a local .env
file. Nothing in this module ever logs secret values.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_INDEX_UNDERLYINGS = (
    "NIFTY",
    "BANKNIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "NIFTYNXT50",
)

DEFAULT_INSTRUMENT_MASTER_URL = "https://public.fyers.in/sym_details/NSE_FO.csv"


def _streamlit_secret(name: str) -> str | None:
    """Read a Streamlit Community Cloud secret without requiring Streamlit.

    Local runs and tests never reach this path because environment variables are
    resolved first; Streamlit raises when no secrets file exists, so every
    failure degrades to ``None`` (missing), not an empty string.
    """

    try:
        import streamlit as st

        value = st.secrets.get(name)
    except Exception:
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        value = _streamlit_secret(name)
    if value is None:
        return default
    text = value.strip()
    return text or default


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env_str(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def _env_path(name: str, default: Path) -> Path:
    raw = _env_str(name)
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = _env_str(name)
    if not raw:
        return default
    return tuple(part.strip().upper() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings for the scanner."""

    client_id: str = ""
    secret_key: str = ""
    redirect_uri: str = ""

    token_file: Path = field(default_factory=lambda: PROJECT_ROOT / "access_token.txt")
    token_meta_file: Path = field(default_factory=lambda: PROJECT_ROOT / "token_meta.json")
    cache_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "cache")
    log_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "logs")
    presets_file: Path = field(
        default_factory=lambda: PROJECT_ROOT / "presets" / "scanner_presets.json"
    )

    instrument_master_url: str = DEFAULT_INSTRUMENT_MASTER_URL
    instrument_cache_ttl_seconds: int = 6 * 60 * 60
    instrument_timeout_seconds: int = 60

    resolution: str = "60"
    history_cache_ttl_seconds: int = 15 * 60
    history_chunk_days: int = 60
    contract_lookback_days: int = 120
    history_cont_flag: int = 0

    request_rate_per_sec: float = 5.0
    request_max_retries: int = 3
    retry_base_seconds: float = 1.0

    include_index_futures: bool = False
    index_underlyings: tuple[str, ...] = DEFAULT_INDEX_UNDERLYINGS

    timezone: str = "Asia/Kolkata"
    chart_lookback_candles: int = 120
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            client_id=_env_str("FYERS_CLIENT_ID"),
            secret_key=_env_str("FYERS_SECRET_KEY"),
            redirect_uri=_env_str("FYERS_REDIRECT_URI", "http://localhost:8501"),
            token_file=_env_path("FYERS_TOKEN_FILE", PROJECT_ROOT / "access_token.txt"),
            token_meta_file=_env_path(
                "FYERS_TOKEN_META_FILE", PROJECT_ROOT / "token_meta.json"
            ),
            cache_dir=_env_path("FYERS_CACHE_DIR", PROJECT_ROOT / "cache"),
            log_dir=_env_path("FYERS_LOG_DIR", PROJECT_ROOT / "logs"),
            presets_file=_env_path(
                "FYERS_PRESETS_FILE",
                PROJECT_ROOT / "presets" / "scanner_presets.json",
            ),
            instrument_master_url=_env_str(
                "FYERS_INSTRUMENT_MASTER_URL", DEFAULT_INSTRUMENT_MASTER_URL
            ),
            instrument_cache_ttl_seconds=_env_int(
                "FYERS_INSTRUMENT_CACHE_TTL", 6 * 60 * 60
            ),
            instrument_timeout_seconds=_env_int("FYERS_INSTRUMENT_TIMEOUT", 60),
            resolution=_env_str("FYERS_RESOLUTION", "60"),
            history_cache_ttl_seconds=_env_int(
                "FYERS_HISTORY_CACHE_TTL", 15 * 60
            ),
            history_chunk_days=_env_int("FYERS_HISTORY_CHUNK_DAYS", 60),
            contract_lookback_days=_env_int("FYERS_CONTRACT_LOOKBACK_DAYS", 120),
            history_cont_flag=_env_int("FYERS_HISTORY_CONT_FLAG", 0),
            request_rate_per_sec=_env_float("FYERS_REQUEST_RATE_PER_SEC", 5.0),
            request_max_retries=_env_int("FYERS_REQUEST_MAX_RETRIES", 3),
            retry_base_seconds=_env_float("FYERS_RETRY_BASE_SECONDS", 1.0),
            include_index_futures=_env_bool("FYERS_INCLUDE_INDEX_FUTURES", False),
            index_underlyings=_env_csv(
                "FYERS_INDEX_UNDERLYINGS", DEFAULT_INDEX_UNDERLYINGS
            ),
            timezone=_env_str("FYERS_TIMEZONE", "Asia/Kolkata"),
            chart_lookback_candles=_env_int("FYERS_CHART_LOOKBACK", 120),
            log_level=_env_str("FYERS_LOG_LEVEL", "INFO").upper(),
        )

    @property
    def credentials_configured(self) -> bool:
        return bool(self.client_id and self.secret_key and self.redirect_uri)

    def missing_credentials(self) -> list[str]:
        missing: list[str] = []
        if not self.client_id:
            missing.append("FYERS_CLIENT_ID")
        if not self.secret_key:
            missing.append("FYERS_SECRET_KEY")
        if not self.redirect_uri:
            missing.append("FYERS_REDIRECT_URI")
        return missing

    def ensure_directories(self) -> None:
        for path in (self.cache_dir, self.log_dir, self.presets_file.parent):
            path.mkdir(parents=True, exist_ok=True)

    def public_summary(self) -> dict[str, object]:
        """Non-secret settings suitable for diagnostics/UI."""
        return {
            "client_id_configured": bool(self.client_id),
            "secret_key_configured": bool(self.secret_key),
            "redirect_uri": self.redirect_uri,
            "resolution": self.resolution,
            "timezone": self.timezone,
            "include_index_futures": self.include_index_futures,
            "cache_dir": str(self.cache_dir),
            "presets_file": str(self.presets_file),
        }


def setup_logging(settings: Settings) -> logging.Logger:
    """Configure application logging without ever logging credentials."""

    logger = logging.getLogger("fyers_avwap")
    if getattr(logger, "_configured", False):
        return logger

    logger.setLevel(getattr(logging, settings.log_level, logging.INFO))
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            settings.log_dir / "scanner.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        pass

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    logger._configured = True  # type: ignore[attr-defined]
    return logger
