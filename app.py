"""FYERS NSE futures terminal: application shell, dashboard and history pages."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Futures Terminal · FYERS NSE",
    layout="wide",
    initial_sidebar_state="expanded",
)

from config import Settings, setup_logging  # noqa: E402
from dashboard import shell, theme  # noqa: E402
from dashboard.avwap_ui import init_filter_defaults, render_avwap_page  # noqa: E402
from dashboard.vwap_reclaim_ui import init_vwap_defaults, render_vwap_page  # noqa: E402
from dashboard.vwap_reversal_ui import (  # noqa: E402
    init_vwap_reversal_defaults,
    render_vwap_reversal_page,
)
from data.cache import HistoricalCache  # noqa: E402
from data.fyers_client import AuthenticationError  # noqa: E402
from data.instrument_master import (  # noqa: E402
    InstrumentMasterError,
    InstrumentMasterProvider,
)
from data.scan_history import ScanHistoryError, ScanHistoryStore  # noqa: E402
from login import (  # noqa: E402
    AuthStatus,
    TokenStore,
    check_authentication,
    exchange_auth_code,
    extract_auth_code,
)
from scanner.presets import PresetError, PresetStore  # noqa: E402

logger = logging.getLogger("fyers_avwap.app")

SCANNER_LABELS = {
    "vwap_reclaim": "VWAP Reclaim",
    "vwap_reversal": "VWAP Reversal",
    "avwap": "AVWAP Patterns",
}

SCANNER_FILTERS = {
    "All scanners": None,
    "VWAP Reclaim": "vwap_reclaim",
    "VWAP Reversal": "vwap_reversal",
    "AVWAP Patterns": "avwap",
}


def _query_params() -> dict[str, Any]:
    try:
        return st.query_params.to_dict()
    except Exception:
        try:
            return st.experimental_get_query_params()
        except Exception:
            return {}


def _clear_query_params() -> None:
    try:
        st.query_params.clear()
    except Exception:
        try:
            st.experimental_set_query_params()
        except Exception:
            pass


def handle_oauth_callback(settings: Settings) -> None:
    code = extract_auth_code(_query_params())
    if not code:
        return
    if st.session_state.get("_processed_auth_code") == code:
        _clear_query_params()
        return

    st.session_state["_processed_auth_code"] = code
    try:
        exchange_auth_code(settings, code)
        st.session_state["auth_flash"] = (
            "FYERS login successful. Access token stored locally."
        )
    except AuthenticationError as exc:
        st.session_state["auth_flash_error"] = str(exc)
    finally:
        _clear_query_params()
        st.session_state.pop("auth_status", None)
        st.session_state.pop("auth_token_mtime", None)
    st.rerun()


def resolve_auth_status(settings: Settings) -> AuthStatus:
    store = TokenStore(settings)
    try:
        mtime = store.token_path.stat().st_mtime_ns
    except OSError:
        mtime = None

    cached = st.session_state.get("auth_status")
    if cached is not None and st.session_state.get("auth_token_mtime") == mtime:
        return cached

    status = check_authentication(settings, verify=True)
    st.session_state["auth_status"] = status
    st.session_state["auth_token_mtime"] = mtime
    return status


@st.cache_resource(show_spinner=False)
def get_instrument_provider(settings: Settings) -> InstrumentMasterProvider:
    return InstrumentMasterProvider(settings)


def _history_store(settings: Settings) -> ScanHistoryStore:
    return ScanHistoryStore(settings.cache_dir / "scan_history.json")


def _recorded_timestamp(raw: Any, settings: Settings) -> pd.Timestamp | None:
    timestamp = pd.to_datetime(raw, utc=True, errors="coerce")
    if pd.isna(timestamp):
        return None
    return timestamp.tz_convert(settings.timezone)


def _format_timestamp(raw: Any, settings: Settings, fallback: str = "—") -> str:
    timestamp = _recorded_timestamp(raw, settings)
    if timestamp is None:
        return fallback
    return f"{timestamp:%d %b %Y %H:%M} IST"


def _selected_rows(event: Any) -> list[int]:
    try:
        return list(event.selection.rows)
    except AttributeError:
        try:
            return list(event.get("selection", {}).get("rows", []))
        except AttributeError:
            return []


def _dir_stats(path: Path) -> tuple[int, int]:
    files = 0
    size = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                files += 1
                size += entry.stat().st_size
    except OSError:
        pass
    return files, size


def _human_size(size: int) -> str:
    value = float(max(size, 0))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return f"{value:,.1f} {unit}"
        value /= 1024.0
    return f"{value:,.1f} GB"


def _scanner_label(value: Any) -> str:
    key = str(value or "")
    return SCANNER_LABELS.get(key, key or "—")


def _history_dataframe(
    scans: list[dict[str, Any]],
    settings: Settings,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for scan in scans:
        recorded = _recorded_timestamp(scan.get("recorded_at"), settings)
        rows.append(
            {
                "Recorded": recorded.tz_localize(None) if recorded is not None else pd.NaT,
                "Scanner": _scanner_label(scan.get("scanner")),
                "Timeframe": scan.get("timeframe", "—"),
                "Universe": scan.get("universe", "—"),
                "Scanned": int(scan.get("scanned", 0) or 0),
                "Matches": int(scan.get("matches", 0) or 0),
                "Errors": int(scan.get("errors", 0) or 0),
            }
        )
    return pd.DataFrame(rows)


def _history_column_config() -> dict[str, Any]:
    return {
        "Recorded": st.column_config.DatetimeColumn(
            "Recorded", format="DD MMM YYYY HH:mm", width="medium"
        ),
        "Scanner": st.column_config.TextColumn(width="medium"),
        "Timeframe": st.column_config.TextColumn(width="small"),
        "Universe": st.column_config.TextColumn(width="large"),
        "Scanned": st.column_config.NumberColumn(format="%d", width="small"),
        "Matches": st.column_config.NumberColumn(format="%d", width="small"),
        "Errors": st.column_config.NumberColumn(format="%d", width="small"),
    }


def _quick_action(
    key: str,
    title: str,
    body: str,
    button_label: str,
    page: str,
    primary: bool = False,
) -> None:
    with st.container(key=key):
        st.markdown(f"**{title}**")
        st.caption(body)
        st.button(
            button_label,
            key=f"{key}_button",
            type="primary" if primary else "secondary",
            on_click=shell.navigate,
            args=(page,),
            width="stretch",
        )


def render_dashboard_page(
    settings: Settings,
    status: AuthStatus,
    provider: InstrumentMasterProvider,
) -> None:
    theme.render_page_header(
        "Futures Terminal",
        "One workspace for session VWAP reclaims, bearish-impulse VWAP reversals "
        "and contract-start AVWAP support patterns. Read-only market data, no order "
        "placement.",
        eyebrow="Overview",
        chips=[
            ("FYERS connected" if status.authenticated else "FYERS offline", "ok" if status.authenticated else "bad"),
            ("NSE futures", "neutral"),
        ],
    )

    active_count = 0
    underlying_count = 0
    universe_error: str | None = None
    if status.authenticated:
        try:
            frame = provider.active_futures()
            active_count = len(frame)
            underlying_count = int(frame["underlying"].nunique())
        except InstrumentMasterError as exc:
            universe_error = str(exc)

    scans: list[dict[str, Any]] = []
    history_error: str | None = None
    try:
        scans = _history_store(settings).list(limit=200)
    except ScanHistoryError as exc:
        history_error = str(exc)

    latest_vwap = next(
        (scan for scan in scans if scan.get("scanner") == "vwap_reclaim"), None
    )
    latest_reversal = next(
        (scan for scan in scans if scan.get("scanner") == "vwap_reversal"), None
    )
    latest_avwap = next(
        (scan for scan in scans if scan.get("scanner") == "avwap"), None
    )

    theme.render_metric_cards(
        [
            {
                "label": "Active contracts",
                "value": f"{active_count:,}" if status.authenticated else "—",
                "hint": "NSE futures universe" if status.authenticated else "Connect FYERS",
                "tone": "accent",
            },
            {
                "label": "Underlyings",
                "value": f"{underlying_count:,}" if status.authenticated else "—",
                "hint": "Futures roots",
                "tone": "neutral",
            },
            {
                "label": "Stored scans",
                "value": f"{len(scans):,}",
                "hint": "Local history records",
                "tone": "vwap",
            },
            {
                "label": "Latest VWAP",
                "value": f"{int(latest_vwap.get('matches', 0)):,}" if latest_vwap else "—",
                "hint": "Signals in latest run",
                "tone": "bull",
            },
            {
                "label": "Latest reversal",
                "value": (
                    f"{int(latest_reversal.get('matches', 0)):,}"
                    if latest_reversal
                    else "—"
                ),
                "hint": "Impulse + VWAP indecision",
                "tone": "vwap",
            },
            {
                "label": "Latest AVWAP",
                "value": f"{int(latest_avwap.get('matches', 0)):,}" if latest_avwap else "—",
                "hint": "Matches in latest run",
                "tone": "avwap",
            },
        ]
    )

    if universe_error:
        st.warning(f"Instrument master unavailable: {universe_error}")
    if history_error:
        st.warning(f"Could not read scan history: {history_error}")

    theme.render_section_header(
        "Quick actions",
        "Jump straight into a scanner or review the local audit trail.",
    )
    quick_columns = st.columns(4)
    with quick_columns[0]:
        _quick_action(
            "quick_vwap",
            "VWAP reclaim",
            "Previous candle below session VWAP, current candle closing back above it.",
            "Open VWAP scanner",
            shell.PAGE_VWAP,
            primary=True,
        )
    with quick_columns[1]:
        _quick_action(
            "quick_reversal",
            "VWAP reversal",
            "Large bearish impulse followed by a doji or inverted hammer at session VWAP.",
            "Open reversal scanner",
            shell.PAGE_VWAP_REVERSAL,
        )
    with quick_columns[2]:
        _quick_action(
            "quick_avwap",
            "AVWAP patterns",
            "1–5 small candles touching, hugging or consolidating around contract AVWAP.",
            "Open AVWAP scanner",
            shell.PAGE_AVWAP,
        )
    with quick_columns[3]:
        _quick_action(
            "quick_history",
            "Scan history",
            "Inspect prior runs, matches and skipped contracts from local storage.",
            "Review history",
            shell.PAGE_HISTORY,
        )

    theme.render_section_header(
        "Recent scans",
        "Newest first. Select any row in Scan History for full details.",
        pills=[(f"{len(scans)} record(s)", "neutral")],
    )
    if not scans:
        theme.render_empty_state(
            "No scans recorded yet",
            "Run any scanner to create the first local history record.",
            "History is stored under the project cache directory.",
        )
    else:
        recent = _history_dataframe(scans[:8], settings)
        st.dataframe(
            recent,
            width="stretch",
            hide_index=True,
            height=min(300, 34 * len(recent) + 42),
            row_height=34,
            column_config=_history_column_config(),
            key="dashboard_recent_scans",
        )

    theme.render_section_header("Workspace", "Connection and local cache health.")
    workspace_columns = st.columns(3)
    with workspace_columns[0]:
        age = TokenStore(settings).age_hours()
        theme.render_card(
            "FYERS token",
            "Connected" if status.authenticated else "Not connected",
            f"Stored {age:.1f} h ago" if age is not None else "No local token",
            tone="bull" if status.authenticated else "neutral",
        )
    with workspace_columns[1]:
        files, size = _dir_stats(settings.cache_dir)
        theme.render_card(
            "Historical cache",
            f"{files:,} files",
            _human_size(size),
            tone="vwap",
        )
    with workspace_columns[2]:
        theme.render_card(
            "Local history",
            f"{len(scans):,} scans",
            str(settings.cache_dir / "scan_history.json"),
            tone="accent",
        )


def _scan_filter(
    scans: list[dict[str, Any]],
    scanner: str,
    timeframe: str,
    query: str,
    from_date: date | None,
    to_date: date | None,
    settings: Settings,
) -> list[dict[str, Any]]:
    scanner_key = SCANNER_FILTERS.get(scanner)
    query = (query or "").strip().lower()
    filtered: list[dict[str, Any]] = []

    for scan in scans:
        if scanner_key is not None and scan.get("scanner") != scanner_key:
            continue
        if timeframe != "All timeframes" and str(scan.get("timeframe", "")) != timeframe:
            continue

        timestamp = _recorded_timestamp(scan.get("recorded_at"), settings)
        if from_date is not None and (timestamp is None or timestamp.date() < from_date):
            continue
        if to_date is not None and (timestamp is None or timestamp.date() > to_date):
            continue

        if query:
            signals = scan.get("signals") or []
            symbols = " ".join(str(signal.get("symbol", "")) for signal in signals)
            haystack = " ".join(
                [
                    str(scan.get("universe", "")),
                    _scanner_label(scan.get("scanner")),
                    symbols,
                ]
            ).lower()
            if query not in haystack:
                continue
        filtered.append(scan)
    return filtered


def _signal_dataframe(signals: list[dict[str, Any]], settings: Settings) -> pd.DataFrame:
    if not signals:
        return pd.DataFrame()
    table = pd.DataFrame(signals)
    rename = {
        "symbol": "Symbol",
        "underlying": "Underlying",
        "expiry": "Expiry",
        "signal": "Signal",
        "timeframe": "Timeframe",
        "signal_time": "Signal time",
        "distance_pct": "Distance (%)",
        "close": "Close",
        "vwap": "VWAP",
        "reference": "Reference",
        "volume_ratio": "Volume x",
        "candles": "Candles",
        "previous_close": "Previous close",
        "previous_vwap": "Previous VWAP",
        "volume": "Volume",
        "average_volume": "Avg volume",
        "turnover": "Turnover",
    }
    table = table.rename(columns=rename)
    if "Signal time" in table.columns:
        parsed = pd.to_datetime(table["Signal time"], utc=True, errors="coerce")
        table["Signal time"] = parsed.dt.tz_convert(settings.timezone).dt.tz_localize(None)
    if "Expiry" in table.columns:
        table["Expiry"] = pd.to_datetime(table["Expiry"], errors="coerce")
    preferred = [
        "Symbol",
        "Signal",
        "Timeframe",
        "Signal time",
        "Distance (%)",
        "Close",
        "VWAP",
        "Reference",
        "Candles",
        "Volume x",
        "Expiry",
        "Underlying",
    ]
    ordered = [column for column in preferred if column in table.columns]
    ordered.extend(column for column in table.columns if column not in ordered)
    return table[ordered]


def _render_scan_detail(settings: Settings, scan: dict[str, Any]) -> None:
    scanner = _scanner_label(scan.get("scanner"))
    recorded = _format_timestamp(scan.get("recorded_at"), settings)
    signals = scan.get("signals") or []
    theme.render_section_header(
        "Scan detail",
        f"{scanner} · recorded {recorded}",
        pills=[
            (f"{int(scan.get('matches', 0) or 0)} match(es)", "accent"),
            (f"{int(scan.get('errors', 0) or 0)} skipped", "warn"),
        ],
    )
    theme.render_metric_cards(
        [
            {"label": "Contracts scanned", "value": int(scan.get("scanned", 0) or 0), "tone": "accent"},
            {"label": "Matches", "value": int(scan.get("matches", 0) or 0), "tone": "bull"},
            {"label": "Errors", "value": int(scan.get("errors", 0) or 0), "tone": "warn"},
            {"label": "Recorded signals", "value": len(signals), "tone": "neutral"},
        ],
        columns=4,
    )
    theme.render_info_grid(
        [
            {"label": "Scanner", "value": scanner},
            {"label": "Timeframe", "value": scan.get("timeframe", "—")},
            {"label": "Universe", "value": scan.get("universe", "—")},
            {"label": "Generated", "value": _format_timestamp(scan.get("generated_at"), settings)},
            {"label": "Recorded", "value": recorded},
            {"label": "Record ID", "value": scan.get("id", "—")},
        ]
    )

    table = _signal_dataframe(signals, settings)
    if table.empty:
        theme.render_empty_state(
            "No signals in this scan",
            "The scanner completed without any matching contracts.",
            "Review the filters used for that run.",
        )
        return

    st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        height=min(360, 34 * len(table) + 42),
        row_height=34,
        key=f"history_signals_{scan.get('id', 'scan')}",
    )
    st.download_button(
        "Download signals CSV",
        data=table.to_csv(index=False).encode("utf-8"),
        file_name=f"scan_{scan.get('id', 'signals')}.csv",
        mime="text/csv",
        key=f"history_signals_csv_{scan.get('id', 'scan')}",
        icon=":material/download:",
    )


def render_history_page(settings: Settings) -> None:
    theme.render_page_header(
        "Scan History",
        "Every completed scanner run is stored locally, newest first. Filter, "
        "inspect and export the recorded matches.",
        eyebrow="Audit trail",
        chips=[("Local JSON", "neutral"), ("Read-only", "neutral")],
    )

    try:
        scans = _history_store(settings).list()
    except ScanHistoryError as exc:
        st.error(str(exc))
        return

    if not scans:
        theme.render_empty_state(
            "No scans recorded yet",
            "Run the VWAP reclaim, VWAP reversal or AVWAP scanner to create the first record.",
            "History is capped at 200 records and stored locally.",
        )
        return

    timeframes = sorted(
        {str(scan.get("timeframe", "")) for scan in scans if scan.get("timeframe")}
    )

    with st.container(key="history_filters"):
        theme.render_section_header(
            "Filters",
            "Narrow the audit trail by scanner, timeframe, date or symbol.",
        )
        filter_columns = st.columns([1.0, 1.0, 1.1, 1.0, 1.0])
        with filter_columns[0]:
            scanner = st.selectbox(
                "Scanner",
                options=list(SCANNER_FILTERS.keys()),
                key="hist_scanner",
            )
        with filter_columns[1]:
            timeframe = st.selectbox(
                "Timeframe",
                options=["All timeframes"] + timeframes,
                key="hist_timeframe",
            )
        with filter_columns[2]:
            query = st.text_input(
                "Search",
                key="hist_query",
                placeholder="Symbol or universe",
            )
        with filter_columns[3]:
            from_date = st.date_input("From", value=None, key="hist_from")
        with filter_columns[4]:
            to_date = st.date_input("To", value=None, key="hist_to")

    filtered = _scan_filter(
        scans,
        scanner,
        timeframe,
        query,
        from_date,
        to_date,
        settings,
    )
    theme.render_section_header(
        "Runs",
        "Select a row to inspect its recorded signals.",
        pills=[(f"{len(filtered)} of {len(scans)}", "neutral")],
    )
    if not filtered:
        theme.render_empty_state(
            "No scans match the filters",
            "Adjust the scanner, timeframe, date range or search text.",
            "Clear the filters to see every stored run.",
        )
        return

    table = _history_dataframe(filtered, settings)
    event = st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        height=min(380, 34 * len(table) + 42),
        row_height=34,
        on_select="rerun",
        selection_mode="single-row",
        key="history_table",
        column_config=_history_column_config(),
    )
    st.download_button(
        "Download filtered runs CSV",
        data=table.to_csv(index=False).encode("utf-8"),
        file_name=f"scan_history_{date.today():%Y%m%d}.csv",
        mime="text/csv",
        key="history_download_csv",
        icon=":material/download:",
    )

    rows = _selected_rows(event)
    if rows:
        index = min(int(rows[0]), len(filtered) - 1)
        _render_scan_detail(settings, filtered[index])
    else:
        st.caption("Select a scan row above to inspect the recorded signals.")


def render_settings_page(settings: Settings, status: AuthStatus) -> None:
    theme.render_page_header(
        "Settings",
        "Runtime configuration, local storage, scanner presets and the FYERS "
        "connection. Secrets are never rendered.",
        eyebrow="Workspace",
        chips=[("Local-first", "neutral"), ("No secrets shown", "neutral")],
    )

    summary = settings.public_summary()
    with st.container(key="settings_panel"):
        theme.render_section_header(
            "Runtime configuration",
            "Environment-derived values. Edit `.env` and restart to change them.",
        )
        theme.render_info_grid(
            [
                {
                    "label": "Client ID",
                    "value": "Configured" if summary["client_id_configured"] else "Missing",
                    "tone": "bull" if summary["client_id_configured"] else "bear",
                },
                {
                    "label": "Secret key",
                    "value": "Configured" if summary["secret_key_configured"] else "Missing",
                    "tone": "bull" if summary["secret_key_configured"] else "bear",
                },
                {"label": "Redirect URI", "value": summary["redirect_uri"] or "—"},
                {"label": "Default resolution", "value": summary["resolution"]},
                {"label": "Timezone", "value": summary["timezone"]},
                {
                    "label": "Index futures",
                    "value": "Included" if summary["include_index_futures"] else "Excluded",
                },
                {"label": "Cache directory", "value": summary["cache_dir"]},
                {"label": "Presets file", "value": summary["presets_file"]},
            ]
        )

    try:
        history_count = len(_history_store(settings).list())
    except ScanHistoryError:
        history_count = 0
    try:
        presets = PresetStore(settings.presets_file).list()
    except PresetError:
        presets = []
    cache_files, cache_size = _dir_stats(settings.cache_dir)

    theme.render_section_header("Local storage", "Cache and history are local to this machine.")
    theme.render_metric_cards(
        [
            {"label": "Cache files", "value": f"{cache_files:,}", "hint": _human_size(cache_size), "tone": "vwap"},
            {"label": "History records", "value": f"{history_count:,}", "hint": "Capped at 200", "tone": "accent"},
            {"label": "AVWAP presets", "value": f"{len(presets):,}", "hint": "Saved filter sets", "tone": "avwap"},
        ],
        columns=3,
    )

    storage_columns = st.columns(2)
    with storage_columns[0]:
        if st.button(
            "Clear historical cache",
            icon=":material/delete_sweep:",
            width="stretch",
            key="settings_clear_cache",
        ):
            try:
                HistoricalCache(
                    settings.cache_dir,
                    settings.history_cache_ttl_seconds,
                    settings.timezone,
                ).clear()
            except Exception as exc:
                st.error(f"Could not clear cache: {exc}")
            else:
                st.session_state["settings_flash"] = "Historical candle cache cleared."
                st.rerun()
    with storage_columns[1]:
        if st.button(
            "Clear scan history",
            icon=":material/history_toggle_off:",
            width="stretch",
            key="settings_clear_history",
        ):
            try:
                _history_store(settings).clear()
            except ScanHistoryError as exc:
                st.error(str(exc))
            else:
                st.session_state["settings_flash"] = "Scan history cleared."
                st.rerun()

    theme.render_section_header(
        "Scanner presets",
        "Saved AVWAP timeframe and pattern-filter combinations.",
        pills=[(f"{len(presets)} preset(s)", "neutral")],
    )
    if not presets:
        theme.render_empty_state(
            "No presets saved",
            "Save a filter set from the AVWAP scanner toolbar.",
            "Presets do not include universe or contract selection.",
        )
    else:
        preset_table = pd.DataFrame(
            [
                {
                    "Name": preset.name,
                    "Timeframe": preset.timeframe,
                    "Min candles": preset.config.min_candles,
                    "Max candles": preset.config.max_candles,
                    "Max body": preset.config.max_body_ratio,
                    "Saved at": _format_timestamp(preset.saved_at, settings),
                }
                for preset in presets
            ]
        )
        st.dataframe(
            preset_table,
            width="stretch",
            hide_index=True,
            height=min(260, 34 * len(preset_table) + 42),
            row_height=34,
            key="settings_presets_table",
        )

    theme.render_section_header(
        "FYERS connection",
        "The same connection is available at the bottom of the sidebar.",
    )
    shell.render_connection_panel(
        settings,
        status,
        key_prefix="settings",
        compact=False,
    )

    with st.expander("About this build", expanded=False):
        st.markdown(
            """
            - Three independent read-only scanners share the FYERS historical-data
              pipeline and the local instrument master.
            - VWAP reclaim and VWAP reversal both use the daily-reset session VWAP.
              AVWAP patterns use the contract-start AVWAP.
            - The VWAP reversal scanner looks for a large bearish impulse candle
              followed by a doji or inverted hammer touching that session VWAP.
            - No order, position or fund endpoint is called anywhere in the app.
            - Scan history and candle cache stay on this machine.
            """
        )


def _current_page() -> str:
    page = st.session_state.get("nav_page", shell.PAGE_DASHBOARD)
    if page not in shell.PAGES:
        page = shell.PAGE_DASHBOARD
        st.session_state["nav_page"] = page
    return page


def main() -> None:
    settings = Settings.from_env()
    setup_logging(settings)
    settings.ensure_directories()
    theme.apply_theme()
    init_filter_defaults(settings)
    init_vwap_defaults(settings)
    init_vwap_reversal_defaults(settings)

    handle_oauth_callback(settings)
    status = resolve_auth_status(settings)
    provider = get_instrument_provider(settings)
    page = _current_page()

    shell.render_sidebar(settings, status, page)

    for key, kind in (
        ("auth_flash", "success"),
        ("auth_flash_error", "error"),
        ("settings_flash", "success"),
        ("preset_flash", "success"),
        ("preset_error", "error"),
    ):
        message = st.session_state.pop(key, None)
        if not message:
            continue
        if kind == "success":
            st.success(message)
        else:
            st.error(message)

    if page == shell.PAGE_VWAP:
        render_vwap_page(settings, status, provider)
    elif page == shell.PAGE_VWAP_REVERSAL:
        render_vwap_reversal_page(settings, status, provider)
    elif page == shell.PAGE_AVWAP:
        render_avwap_page(settings, status, provider)
    elif page == shell.PAGE_HISTORY:
        render_history_page(settings)
    elif page == shell.PAGE_SETTINGS:
        render_settings_page(settings, status)
    else:
        render_dashboard_page(settings, status, provider)


if __name__ == "__main__":
    main()
