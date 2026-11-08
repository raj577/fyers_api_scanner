"""Thin, defensive wrapper around the official FYERS API v3 SDK."""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import date
from typing import Any

import requests
from fyers_apiv3 import fyersModel

from config import Settings

logger = logging.getLogger("fyers_avwap.fyers")

RATE_LIMIT_CODES = {429, -429}
RETRYABLE_MESSAGE_PARTS = (
    "rate limit",
    "request limit",
    "too many requests",
    "temporarily unavailable",
    "internal server",
    "timeout",
)
AUTH_MESSAGE_PARTS = (
    "access token",
    "invalid token",
    "token expired",
    "token has expired",
    "unauthor",
    "authenticate",
    "authentication",
    "invalid client",
    "please login",
    "session expired",
)


class FyersError(RuntimeError):
    """Base error for FYERS integration problems."""


class AuthenticationError(FyersError):
    """The access token is missing, invalid or expired."""


class RateLimitError(FyersError):
    """FYERS kept rate-limiting the request after all retries."""


class ApiError(FyersError):
    """A non-retryable FYERS API response or unexpected transport error."""


class RateLimiter:
    """Simple process-wide thread-safe limiter based on minimum call spacing."""

    def __init__(self, calls_per_second: float) -> None:
        self._interval = 1.0 / calls_per_second if calls_per_second > 0 else 0.0
        self._next_allowed = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)
                now = time.monotonic()
            self._next_allowed = now + self._interval


def _is_auth_failure(code: Any, message: str) -> bool:
    text = message.lower()
    if any(part in text for part in AUTH_MESSAGE_PARTS):
        return True
    return code in {-8, -16, -17, -18, 401}


def _is_rate_limit(code: Any, message: str) -> bool:
    text = message.lower()
    if code in RATE_LIMIT_CODES:
        return True
    return any(part in text for part in RETRYABLE_MESSAGE_PARTS)


class FyersClient:
    """Authenticated FYERS client with rate limiting, retries and safe errors."""

    def __init__(
        self,
        settings: Settings,
        access_token: str,
        api: Any | None = None,
    ) -> None:
        if not access_token:
            raise AuthenticationError("No FYERS access token available. Please login.")
        if not settings.client_id:
            raise AuthenticationError("FYERS_CLIENT_ID is not configured.")

        self.settings = settings
        self._limiter = RateLimiter(settings.request_rate_per_sec)
        self._max_retries = max(int(settings.request_max_retries), 0)
        self._base_backoff = max(float(settings.retry_base_seconds), 0.1)

        if api is not None:
            self._api = api
        else:
            # Keep SDK logs inside the gitignored logs/ directory. The SDK logs
            # request/response metadata but not the Authorization header token.
            try:
                settings.log_dir.mkdir(parents=True, exist_ok=True)
                sdk_log_path = str(settings.log_dir)
            except OSError:
                sdk_log_path = None
            self._api = fyersModel.FyersModel(
                client_id=settings.client_id,
                token=access_token,
                is_async=False,
                log_path=sdk_log_path,
                log_level="ERROR",
            )

    def verify_connection(self) -> dict[str, Any]:
        """Return profile data when the token is valid; raise AuthenticationError."""
        self._limiter.wait()
        try:
            response = self._api.get_profile()
        except requests.RequestException as exc:
            raise ApiError(f"Could not reach FYERS profile endpoint: {exc}") from exc
        except Exception as exc:  # SDK may raise its own transport exceptions
            raise ApiError(f"Unexpected FYERS profile error: {type(exc).__name__}") from exc

        if not isinstance(response, dict):
            raise ApiError("Unexpected profile response from FYERS.")
        if str(response.get("s", "")).lower() == "ok":
            data = response.get("data")
            return data if isinstance(data, dict) else {}
        code = response.get("code")
        message = str(response.get("message") or "FYERS authentication failed")
        if _is_auth_failure(code, message):
            raise AuthenticationError(message)
        raise ApiError(f"FYERS profile error ({code}): {message}")

    def is_authenticated(self) -> bool:
        try:
            self.verify_connection()
            return True
        except FyersError:
            return False

    def history(
        self,
        symbol: str,
        resolution: str,
        range_from: date,
        range_to: date,
        cont_flag: int = 0,
    ) -> list[list[Any]]:
        """Fetch raw candles for one contiguous date range.

        Returns a list of ``[epoch, open, high, low, close, volume]`` rows.
        Empty data is represented as an empty list. Retries only transient
        failures; authentication and invalid-symbol errors fail fast.
        """

        payload = {
            "symbol": symbol,
            "resolution": str(resolution),
            "date_format": "1",
            "range_from": range_from.isoformat(),
            "range_to": range_to.isoformat(),
            "cont_flag": str(int(cont_flag)),
        }
        last_error: str = "unknown error"

        for attempt in range(self._max_retries + 1):
            self._limiter.wait()
            try:
                response = self._api.history(payload)
            except (
                requests.RequestException,
                TimeoutError,
                ConnectionError,
                OSError,
            ) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self._max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise ApiError(f"FYERS history request failed for {symbol}: {last_error}") from exc
            except Exception as exc:
                raise ApiError(
                    f"Unexpected FYERS history error for {symbol}: {type(exc).__name__}"
                ) from exc

            if not isinstance(response, dict):
                raise ApiError(f"Unexpected history response for {symbol}.")

            status = str(response.get("s", "")).lower()
            if status == "ok":
                candles = response.get("candles") or []
                if not isinstance(candles, list):
                    raise ApiError(f"Malformed candles returned for {symbol}.")
                return candles
            if status in {"no_data", "no data"}:
                return []

            code = response.get("code")
            message = str(response.get("message") or "FYERS API error")
            last_error = f"code={code} message={message}"

            if _is_auth_failure(code, message):
                logger.warning("FYERS authentication failed for %s", symbol)
                raise AuthenticationError(message)
            if _is_rate_limit(code, message) and attempt < self._max_retries:
                logger.warning(
                    "FYERS transient/rate-limit response for %s (attempt %s/%s): %s",
                    symbol,
                    attempt + 1,
                    self._max_retries + 1,
                    message,
                )
                self._sleep_backoff(attempt)
                continue
            if _is_rate_limit(code, message):
                raise RateLimitError(f"FYERS rate limit for {symbol}: {last_error}")
            if isinstance(code, int) and 500 <= code < 600 and attempt < self._max_retries:
                logger.warning("FYERS server error for %s: %s", symbol, last_error)
                self._sleep_backoff(attempt)
                continue
            raise ApiError(f"FYERS history error for {symbol}: {last_error}")

        raise ApiError(f"FYERS history failed for {symbol}: {last_error}")

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self._base_backoff * (2**attempt) + random.uniform(0.0, 0.4)
        time.sleep(delay)
