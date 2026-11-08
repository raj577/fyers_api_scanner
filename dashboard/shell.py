"""Application shell: page registry, sidebar navigation and FYERS connection."""

from __future__ import annotations

from typing import Any

import streamlit as st

from config import Settings
from dashboard import theme
from data.fyers_client import AuthenticationError
from login import (
    AuthStatus,
    TokenStore,
    exchange_auth_code,
    generate_auth_url,
)

PAGE_DASHBOARD = "Dashboard"
PAGE_VWAP = "VWAP Reclaim"
PAGE_VWAP_REVERSAL = "VWAP Reversal"
PAGE_AVWAP = "AVWAP Patterns"
PAGE_HISTORY = "Scan History"
PAGE_SETTINGS = "Settings"

PAGES: tuple[str, ...] = (
    PAGE_DASHBOARD,
    PAGE_VWAP,
    PAGE_VWAP_REVERSAL,
    PAGE_AVWAP,
    PAGE_HISTORY,
    PAGE_SETTINGS,
)

_NAV: tuple[tuple[str, str], ...] = (
    (PAGE_DASHBOARD, ":material/space_dashboard:"),
    (PAGE_VWAP, ":material/show_chart:"),
    (PAGE_VWAP_REVERSAL, ":material/query_stats:"),
    (PAGE_AVWAP, ":material/candlestick_chart:"),
    (PAGE_HISTORY, ":material/history:"),
    (PAGE_SETTINGS, ":material/settings:"),
)

_SETUP_SNIPPET = (
    "FYERS_CLIENT_ID=YOUR_APP_ID\n"
    "FYERS_SECRET_KEY=YOUR_SECRET_KEY\n"
    "FYERS_REDIRECT_URI=http://localhost:8501"
)


def navigate(page: str) -> None:
    st.session_state["nav_page"] = page


def page_slug(page: str) -> str:
    return page.lower().replace(" ", "_")


def clear_session_state() -> None:
    """Drop cached scan results and page state, keeping the shell alive."""

    prefixes = ("v_", "vr_", "a_", "pref_", "hist_", "nav_")
    for key in list(st.session_state.keys()):
        if key.startswith(prefixes):
            del st.session_state[key]
    st.session_state["nav_page"] = PAGE_DASHBOARD


def _profile_label(profile: dict[str, Any] | None) -> str:
    if not profile:
        return ""
    for key in ("display_name", "name", "fy_id", "email_id"):
        value = profile.get(key)
        if value:
            return str(value)
    return ""


def _render_login(settings: Settings, key_prefix: str) -> None:
    status = st.session_state.get(f"{key_prefix}_login_status", "")
    if status:
        st.caption(status)

    if st.button(
        "Generate login URL",
        key=f"{key_prefix}_gen_url",
        icon=":material/open_in_new:",
        width="stretch",
    ):
        try:
            st.session_state[f"{key_prefix}_auth_url"] = generate_auth_url(settings)
            st.session_state[f"{key_prefix}_login_status"] = (
                "Open the FYERS URL, authorise the app, then paste the auth code."
            )
            st.rerun()
        except AuthenticationError as exc:
            st.error(str(exc))

    auth_url = st.session_state.get(f"{key_prefix}_auth_url")
    if auth_url:
        st.link_button(
            "Open FYERS login",
            auth_url,
            icon=":material/arrow_outward:",
            width="stretch",
        )

    code = st.text_input(
        "Authorization code",
        key=f"{key_prefix}_auth_code",
        type="password",
        help="Copy the auth_code value from the FYERS redirect URL.",
    )
    if st.button(
        "Exchange code",
        key=f"{key_prefix}_exchange",
        type="primary",
        width="stretch",
        disabled=not code.strip(),
    ):
        try:
            exchange_auth_code(settings, code)
        except AuthenticationError as exc:
            st.error(str(exc))
        else:
            st.session_state[f"{key_prefix}_login_status"] = ""
            st.success("FYERS connected.")
            st.rerun()


def render_connection_panel(
    settings: Settings,
    status: AuthStatus,
    key_prefix: str = "sidebar",
    compact: bool = True,
) -> None:
    store = TokenStore(settings)
    theme.render_connection_card(
        authenticated=status.authenticated,
        token_present=status.token_present,
        account=_profile_label(status.profile),
        token_age_hours=store.age_hours(),
        message=status.message,
        credentials_configured=settings.credentials_configured,
    )

    if not settings.credentials_configured:
        with st.expander("Setup credentials", expanded=compact):
            st.caption("Add these values to `.env` in the project root, then restart.")
            st.code(_SETUP_SNIPPET, language="bash")
        return

    if status.authenticated:
        if st.button(
            "Logout",
            key=f"{key_prefix}_logout",
            icon=":material/logout:",
            width="stretch",
        ):
            store.clear()
            clear_session_state()
            st.rerun()
        return

    with st.expander("Connect FYERS", expanded=not status.token_present):
        _render_login(settings, key_prefix)


def render_sidebar_nav(current: str) -> None:
    theme.render_brand()
    st.markdown(
        '<div class="term-nav-label">Workspace</div>', unsafe_allow_html=True
    )
    for page, icon in _NAV:
        st.button(
            page,
            key=f"nav_{page_slug(page)}",
            icon=icon,
            type="primary" if page == current else "secondary",
            on_click=navigate,
            args=(page,),
            width="stretch",
        )


def render_sidebar(
    settings: Settings,
    status: AuthStatus,
    current: str,
) -> None:
    with st.sidebar:
        render_sidebar_nav(current)
        with st.container(key="sidebar_connection"):
            render_connection_panel(settings, status, key_prefix="sidebar")


def render_auth_required(settings: Settings, context: str) -> None:
    theme.render_empty_state(
        "FYERS connection required",
        f"Connect your FYERS account to {context}.",
        "Use the connection panel at the bottom of the sidebar.",
    )
    if not settings.credentials_configured:
        with st.expander("Connection setup"):
            st.caption("Add these values to `.env` in the project root, then restart.")
            st.code(_SETUP_SNIPPET, language="bash")
