"""FYERS OAuth login, token persistence and connection verification."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from fyers_apiv3 import fyersModel

from config import Settings
from data.fyers_client import AuthenticationError, FyersClient, FyersError

logger = logging.getLogger("fyers_avwap.login")

AUTH_URL_STATE = "fyers_avwap_scanner"
TOKEN_META_VERSION = 1


def _atomic_write(path: Path, text: str, private: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        if private and os.name != "nt":
            os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        if private and os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


class TokenStore:
    """Stores only the access token and non-sensitive issuance metadata."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.token_path = Path(settings.token_file)
        self.meta_path = Path(settings.token_meta_file)

    def load_token(self) -> str | None:
        try:
            token = self.token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return token or None

    def load_meta(self) -> dict[str, Any]:
        try:
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def save_token(self, token: str) -> None:
        token = (token or "").strip()
        if not token:
            raise AuthenticationError("FYERS returned an empty access token.")
        _atomic_write(self.token_path, token, private=True)
        meta = {
            "version": TOKEN_META_VERSION,
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "issued_at_epoch": time.time(),
        }
        _atomic_write(self.meta_path, json.dumps(meta, indent=2), private=True)
        logger.info("FYERS access token stored locally")

    def clear(self) -> None:
        for path in (self.token_path, self.meta_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove token file %s", path.name)

    def age_hours(self) -> float | None:
        issued = self.load_meta().get("issued_at_epoch")
        if issued is None:
            return None
        try:
            return max((time.time() - float(issued)) / 3600.0, 0.0)
        except (TypeError, ValueError):
            return None

    def has_token(self) -> bool:
        return bool(self.load_token())


def _assert_credentials(settings: Settings) -> None:
    missing = settings.missing_credentials()
    if missing:
        raise AuthenticationError(
            "Missing FYERS configuration: " + ", ".join(missing)
        )


def _new_session(settings: Settings) -> fyersModel.SessionModel:
    _assert_credentials(settings)
    return fyersModel.SessionModel(
        client_id=settings.client_id,
        secret_key=settings.secret_key,
        redirect_uri=settings.redirect_uri,
        response_type="code",
        grant_type="authorization_code",
        state=AUTH_URL_STATE,
    )


def generate_auth_url(settings: Settings) -> str:
    """Build the FYERS OAuth authorization URL."""
    return _new_session(settings).generate_authcode()


def exchange_auth_code(settings: Settings, auth_code: str) -> str:
    """Exchange an OAuth authorization code for an access token and persist it."""
    auth_code = (auth_code or "").strip()
    if not auth_code:
        raise AuthenticationError("Authorization code is empty.")

    session = _new_session(settings)
    session.set_token(auth_code)
    try:
        response = session.generate_token()
    except Exception as exc:
        raise AuthenticationError(
            f"Could not exchange the authorization code: {type(exc).__name__}"
        ) from exc

    if not isinstance(response, dict):
        raise AuthenticationError("Unexpected response while exchanging auth code.")
    access_token = response.get("access_token")
    if access_token:
        TokenStore(settings).save_token(str(access_token))
        return str(access_token)

    message = str(response.get("message") or response.get("error") or "unknown error")
    # Never include the auth code or token in the error.
    raise AuthenticationError(f"FYERS token exchange failed: {message}")


def extract_auth_code(params: Mapping[str, Any]) -> str | None:
    """Extract FYERS auth_code from Streamlit/query-string parameters safely."""
    auth_code = params.get("auth_code")
    if isinstance(auth_code, (list, tuple)):
        auth_code = auth_code[0] if auth_code else None
    if isinstance(auth_code, str) and auth_code.strip():
        return auth_code.strip()

    # Some redirects use ?code=<jwt>. Avoid mistaking ?code=200 for a token.
    code = params.get("code")
    if isinstance(code, (list, tuple)):
        code = code[0] if code else None
    if isinstance(code, str) and len(code.strip()) > 40:
        return code.strip()
    return None


@dataclass
class AuthStatus:
    authenticated: bool
    token_present: bool
    message: str
    profile: dict[str, Any] | None = None
    client: FyersClient | None = None


def check_authentication(settings: Settings, verify: bool = True) -> AuthStatus:
    """Load the stored token and verify it against the FYERS profile endpoint."""
    store = TokenStore(settings)
    token = store.load_token()
    if not token:
        return AuthStatus(
            authenticated=False,
            token_present=False,
            message="No access token found. Generate an authorization URL to login.",
        )

    try:
        client = FyersClient(settings, token)
        if not verify:
            return AuthStatus(
                authenticated=True,
                token_present=True,
                message="Access token loaded (not verified).",
                client=client,
            )
        profile = client.verify_connection()
        return AuthStatus(
            authenticated=True,
            token_present=True,
            message="Connected to FYERS.",
            profile=profile,
            client=client,
        )
    except AuthenticationError as exc:
        logger.warning("Stored FYERS token is no longer valid")
        return AuthStatus(
            authenticated=False,
            token_present=True,
            message=f"Stored token is invalid or expired. Please login again. ({exc})",
        )
    except FyersError as exc:
        return AuthStatus(
            authenticated=False,
            token_present=True,
            message=f"Could not verify the FYERS connection: {exc}",
        )
