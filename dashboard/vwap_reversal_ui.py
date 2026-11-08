"""VWAP reversal scanner page: toolbar, scan execution, results and detail.

The page is intentionally independent from the VWAP reclaim and AVWAP pages.
It detects a large bearish impulse candle followed immediately by a doji or
inverted hammer that touches the session VWAP.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

import pandas as pd
import streamlit as st

from config import Settings
from dashboard import chart_theme, shell, theme
from dashboard.vwap_reversal_charts import plot_vwap_reversal_chart
from data.cache import HistoricalCache
from data.fyers_client import AuthenticationError, FyersClient, FyersError
from data.historical_data import HistoricalDataService
from data.instrument_master import (
    InstrumentMasterError,
    InstrumentMasterProvider,
    to_instruments,
)
from data.scan_history import ScanHistoryError, ScanHistoryStore
from login import AuthStatus, TokenStore
from scanner.vwap_reclaim.timeframes import DEFAULT_TIMEFRAME, TIMEFRAMES
from scanner.vwap_reversal.config import VwapReversalConfig
from scanner.vwap_reversal.engine import VwapReversalEngine
from scanner.vwap_reversal.models import (
    CONFIRMATION_ABOVE_CLOSE,
    CONFIRMATION_ABOVE_HIGH,
    CONFIRMATION_AWAITING,
    CONFIRMATION_BULLISH,
    CONFIRMATION_PATTERN_DETECTED,
    SIGNAL_TYPES,
    TOUCH_EXACT,
    TOUCH_NEAR,
    VwapReversalReport,
    VwapReversalSignal,
)

SORT_OPTIONS = (
    "Signal time (newest)",
    "Signal time (oldest)",
    "VWAP distance (closest)",
    "Bearish body ratio (largest)",
    "Symbol (A-Z)",
)

TOUCH_FILTER_OPTIONS = ("Exact only", "Near only", "Both")
TOUCH_FILTER_MAP: dict[str, tuple[str, ...]] = {
    "Exact only": (TOUCH_EXACT,),
    "Near only": (TOUCH_NEAR,),
    "Both": (TOUCH_EXACT, TOUCH_NEAR),
}

CONFIRMATION_FILTER_OPTIONS = (
    "All",
    CONFIRMATION_BULLISH,
    CONFIRMATION_AWAITING,
    CONFIRMATION_PATTERN_DETECTED,
)

CONFIRMATION_MODE_OPTIONS = (CONFIRMATION_ABOVE_HIGH, CONFIRMATION_ABOVE_CLOSE)

ALL_SIGNAL_COLUMNS = (
    "Symbol",
    "Underlying",
    "Expiry",
    "Timeframe",
    "Pattern type",
    "Prev body size",
    "Prev body ratio (%)",
    "ATR x",
    "Second candle",
    "Touch",
    "Second low",
    "Second high",
    "Second close",
    "Second VWAP",
    "VWAP distance (%)",
    "Volume",
    "Confirmation",
    "Signal time",
)

DEFAULT_VISIBLE_COLUMNS = (
    "Symbol",
    "Underlying",
    "Expiry",
    "Timeframe",
    "Pattern type",
    "Prev body ratio (%)",
    "Second candle",
    "Second low",
    "Second high",
    "Second close",
    "Second VWAP",
    "VWAP distance (%)",
    "Volume",
    "Confirmation",
    "Signal time",
)

LOOKBACK_OPTIONS = (40, 60, 90, 120, 180, 240)

VR_DEFAULTS: dict[str, Any] = {
    "vr_timeframe": DEFAULT_TIMEFRAME,
    "vr_min_body_ratio": 0.60,
    "vr_min_atr_multiple": 1.5,
    "vr_doji_body": 0.15,
    "vr_ih_upper_ratio": 2.0,
    "vr_ih_body_ratio": 0.35,
    "vr_touch_tolerance": 0.10,
    "vr_touch_filter": TOUCH_FILTER_OPTIONS[0],
    "vr_confirm_enabled": False,
    "vr_confirm_mode": CONFIRMATION_ABOVE_HIGH,
    "vr_min_volume": 0.0,
    "vr_lookback": 120,
    "vr_expiry": "Current active expiry",
    "vr_scope": "All active contracts",
    "vr_symbols": [],
    "vr_search": "",
    "vr_sort": SORT_OPTIONS[0],
    "vr_pattern_filter": list(SIGNAL_TYPES),
    "vr_confirmation_filter": CONFIRMATION_FILTER_OPTIONS[0],
    "vr_chart_lookback": 120,
    "vr_visible_columns": list(DEFAULT_VISIBLE_COLUMNS),
}


def init_vwap_reversal_defaults(settings: Settings | None = None) -> None:
    for key, value in VR_DEFAULTS.items():
        st.session_state.setdefault(
            key, list(value) if isinstance(value, list) else value
        )
    if settings is not None:
        st.session_state.setdefault("vr_index", settings.include_index_futures)


def _timeframe_label(key: str) -> str:
    spec = TIMEFRAMES.get(key)
    return spec.label if spec else key


def _load_active_frame(
    provider: InstrumentMasterProvider,
    current_only: bool,
    include_index: bool,
) -> pd.DataFrame:
    with st.spinner("Loading NSE futures master..."):
        return provider.active_futures(
            current_expiry_only=current_only,
            include_index=include_index,
        )


def build_vwap_reversal_config() -> tuple[
    VwapReversalConfig | None, str | None, str
]:
    """Render the threshold panel and build a validated config."""

    with st.container(key="vr_thresholds"):
        theme.render_section_header(
            "Pattern thresholds",
            "The impulse candle is validated against ATR(14); the second candle "
            "must be a doji or inverted hammer at session VWAP.",
        )
        row_one = st.columns([1.0, 1.0, 1.0, 1.0])
        with row_one[0]:
            timeframe = st.selectbox(
                "Timeframe",
                options=list(TIMEFRAMES),
                format_func=_timeframe_label,
                key="vr_timeframe",
            )
        with row_one[1]:
            min_body_ratio = float(
                st.number_input(
                    "Min bearish body ratio",
                    min_value=0.0,
                    max_value=1.0,
                    step=0.01,
                    key="vr_min_body_ratio",
                    help="Body / range of the first candle (0.60 = 60%).",
                )
            )
        with row_one[2]:
            min_atr_multiple = float(
                st.number_input(
                    "Min bearish ATR multiple",
                    min_value=0.0,
                    max_value=20.0,
                    step=0.1,
                    key="vr_min_atr_multiple",
                    help="Body size must be at least this many ATR(14).",
                )
            )
        with row_one[3]:
            doji_body_ratio = float(
                st.number_input(
                    "Doji body threshold",
                    min_value=0.0,
                    max_value=1.0,
                    step=0.01,
                    key="vr_doji_body",
                    help="Second candle body / range at or below this is a doji.",
                )
            )

        row_two = st.columns([1.0, 1.0, 1.0, 1.0])
        with row_two[0]:
            ih_upper_ratio = float(
                st.number_input(
                    "Inverted hammer upper-wick x body",
                    min_value=0.0,
                    max_value=20.0,
                    step=0.1,
                    key="vr_ih_upper_ratio",
                )
            )
        with row_two[1]:
            ih_body_ratio = float(
                st.number_input(
                    "Inverted hammer max body ratio",
                    min_value=0.0,
                    max_value=1.0,
                    step=0.01,
                    key="vr_ih_body_ratio",
                )
            )
        with row_two[2]:
            touch_tolerance = float(
                st.number_input(
                    "VWAP near-touch tolerance (%)",
                    min_value=0.0,
                    max_value=5.0,
                    step=0.01,
                    key="vr_touch_tolerance",
                    help="Close distance from VWAP for a separately-labelled near touch.",
                )
            )
        with row_two[3]:
            touch_filter = st.selectbox(
                "VWAP interaction",
                options=list(TOUCH_FILTER_OPTIONS),
                key="vr_touch_filter",
                help="Exact touches are candle-range intersections with VWAP.",
            )

        row_three = st.columns([1.0, 1.0, 1.0, 1.0])
        with row_three[0]:
            confirm_enabled = st.checkbox(
                "Enable bullish confirmation",
                key="vr_confirm_enabled",
                help="Optional; the primary pattern is recorded when candle 2 closes.",
            )
        with row_three[1]:
            confirm_mode = st.selectbox(
                "Confirmation rule",
                options=list(CONFIRMATION_MODE_OPTIONS),
                key="vr_confirm_mode",
                disabled=not confirm_enabled,
            )
        with row_three[2]:
            min_volume = float(
                st.number_input(
                    "Minimum second-candle volume",
                    min_value=0.0,
                    max_value=1_000_000_000.0,
                    step=10_000.0,
                    key="vr_min_volume",
                    help="Absolute volume floor; FYERS units vary by contract.",
                )
            )
        with row_three[3]:
            lookback = int(
                st.number_input(
                    "Evaluation lookback (candles)",
                    min_value=3,
                    max_value=500,
                    step=10,
                    key="vr_lookback",
                    help="Only completed candle pairs inside this window are evaluated.",
                )
            )

    try:
        config = VwapReversalConfig(
            timeframe=timeframe,
            min_bearish_body_ratio=min_body_ratio,
            min_bearish_atr_multiple=min_atr_multiple,
            doji_body_max_ratio=doji_body_ratio,
            inverted_hammer_upper_wick_ratio=ih_upper_ratio,
            inverted_hammer_body_max_ratio=ih_body_ratio,
            vwap_tolerance_pct=touch_tolerance,
            allowed_touch_types=TOUCH_FILTER_MAP[touch_filter],
            enable_bullish_confirmation=confirm_enabled,
            confirmation_mode=confirm_mode,
            min_volume=min_volume,
            lookback_candles=lookback,
        )
    except ValueError as exc:
        return None, str(exc), ""

    summary = (
        f"{_timeframe_label(timeframe)} · body >= {min_body_ratio:.0%} of range "
        f"and >= {min_atr_multiple:.1f}x ATR · {touch_filter.lower()}"
    )
    if confirm_enabled:
        summary += " · bullish confirmation"
    if min_volume > 0:
        summary += f" · volume >= {min_volume:,.0f}"
    return config, None, summary


def render_vwap_reversal_toolbar(
    settings: Settings,
    status: AuthStatus,
    provider: InstrumentMasterProvider,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "active_frame": pd.DataFrame(),
        "instruments": [],
        "config": None,
        "config_error": None,
        "config_summary": "",
        "universe_label": "",
        "instrument_error": None,
        "scan_clicked": False,
    }

    with st.container(key="vr_toolbar"):
        theme.render_section_header(
            "Scanner controls",
            "Universe and expiry scope. Only completed candles are evaluated.",
            pills=[("Read-only", "neutral"), ("Session VWAP", "vwap")],
        )

        universe_row = st.columns([1.05, 1.15, 1.1, 0.7])
        with universe_row[0]:
            include_index = st.checkbox("Include index futures", key="vr_index")
        with universe_row[1]:
            expiry_mode = st.segmented_control(
                "Expiry scope",
                options=["Current active expiry", "All active expiries"],
                key="vr_expiry",
                width="stretch",
            )
        with universe_row[2]:
            universe_mode = st.segmented_control(
                "Scan scope",
                options=["All active contracts", "Selected contracts"],
                key="vr_scope",
                width="stretch",
            )
        with universe_row[3]:
            if st.button(
                "Refresh master",
                icon=":material/refresh:",
                key="vr_refresh",
                width="stretch",
            ):
                try:
                    with st.spinner("Downloading FYERS NSE_FO master..."):
                        provider.load(force=True)
                    st.rerun()
                except InstrumentMasterError as exc:
                    st.error(str(exc))

        expiry_mode = expiry_mode or "Current active expiry"
        universe_mode = universe_mode or "All active contracts"
        current_only = expiry_mode == "Current active expiry"

        try:
            active_frame = _load_active_frame(provider, current_only, include_index)
        except InstrumentMasterError as exc:
            context["instrument_error"] = str(exc)
            return context

        context["active_frame"] = active_frame
        if active_frame.empty:
            return context

        selected_frame = active_frame
        if universe_mode == "Selected contracts":
            valid_symbols = set(active_frame["symbol"].tolist())
            current_symbols = [
                symbol
                for symbol in st.session_state.get("vr_symbols", [])
                if symbol in valid_symbols
            ]
            if current_symbols != st.session_state.get("vr_symbols"):
                st.session_state["vr_symbols"] = current_symbols
            label_map = {
                str(row.symbol): f"{row.symbol}  ·  {row.expiry:%d %b %Y}"
                for row in active_frame.itertuples(index=False)
            }
            selected_symbols = st.multiselect(
                "Select contracts",
                options=active_frame["symbol"].tolist(),
                format_func=lambda symbol: label_map.get(symbol, symbol),
                key="vr_symbols",
            )
            selected_frame = active_frame.loc[
                active_frame["symbol"].isin(selected_symbols)
            ].copy()

        config, config_error, config_summary = build_vwap_reversal_config()

        context["config"] = config
        context["config_error"] = config_error
        context["config_summary"] = config_summary

        if config is not None and not selected_frame.empty:
            context["instruments"] = to_instruments(selected_frame)
            context["universe_label"] = (
                f"{len(context['instruments'])} selected contract(s)"
                if universe_mode == "Selected contracts"
                else f"all {len(context['instruments'])} active contract(s)"
            )

        action_row = st.columns([0.9, 2.9])
        with action_row[0]:
            context["scan_clicked"] = st.button(
                "Run scan",
                type="primary",
                icon=":material/play_arrow:",
                width="stretch",
                disabled=config is None or not context["instruments"],
            )
        with action_row[1]:
            status_text = (
                f"Universe: {context['universe_label'] or '—'}"
                if context["universe_label"]
                else "Select at least one contract to scan."
            )
            if context["config_summary"]:
                status_text += f" · {context['config_summary']}"
            st.caption(status_text)

    return context


def execute_vwap_reversal_scan(
    settings: Settings,
    status: AuthStatus,
    instruments: list,
    config: VwapReversalConfig,
    show_progress: bool = True,
) -> VwapReversalReport:
    client = status.client
    if client is None:
        token = TokenStore(settings).load_token()
        if not token:
            raise AuthenticationError("No stored FYERS token. Please login.")
        client = FyersClient(settings, token)

    cache = HistoricalCache(
        settings.cache_dir,
        settings.history_cache_ttl_seconds,
        settings.timezone,
    )
    history_service = HistoricalDataService(client, cache, settings)
    engine = VwapReversalEngine(history_service, settings)

    progress = None
    if show_progress:
        progress = st.progress(0.0, text=f"Preparing {len(instruments)} contracts...")

    def on_progress(position: int, total: int, symbol: str) -> None:
        if progress is not None:
            progress.progress(
                min(position / max(total, 1), 1.0),
                text=f"Scanning {symbol} ({position}/{total})",
            )

    report = engine.scan(instruments, config=config, progress_callback=on_progress)
    if progress is not None:
        progress.progress(1.0, text="Scan complete")
    return report


def _signals_dataframe(signals: Iterable[VwapReversalSignal]) -> pd.DataFrame:
    return pd.DataFrame([signal.to_row() for signal in signals])


def _sort_signals(
    signals: list[VwapReversalSignal],
    sort_key: str,
) -> list[VwapReversalSignal]:
    if sort_key == "Signal time (oldest)":
        return sorted(signals, key=lambda signal: signal.second_timestamp)
    if sort_key == "VWAP distance (closest)":
        return sorted(signals, key=lambda signal: abs(signal.vwap_distance_pct))
    if sort_key == "Bearish body ratio (largest)":
        return sorted(
            signals,
            key=lambda signal: signal.bearish_body_ratio,
            reverse=True,
        )
    if sort_key == "Symbol (A-Z)":
        return sorted(signals, key=lambda signal: signal.symbol)
    return sorted(
        signals,
        key=lambda signal: signal.second_timestamp,
        reverse=True,
    )


def _filter_signals(
    signals: list[VwapReversalSignal],
    query: str,
    patterns: list[str],
    confirmation: str,
    sort_key: str,
) -> list[VwapReversalSignal]:
    filtered = signals
    if patterns:
        filtered = [
            signal for signal in filtered if signal.signal_type in patterns
        ]
    else:
        filtered = []
    if confirmation != "All":
        filtered = [
            signal
            for signal in filtered
            if signal.confirmation_status == confirmation
        ]

    query = (query or "").strip().lower()
    if query:
        filtered = [
            signal
            for signal in filtered
            if query in signal.symbol.lower()
            or query in (signal.underlying or "").lower()
            or query in signal.signal_type.lower()
            or query in signal.candle_type.lower()
            or query in signal.touch_type.lower()
            or query in signal.timeframe.lower()
        ]
    return _sort_signals(filtered, sort_key)


def _selected_table_rows(event: Any) -> list[int]:
    try:
        return list(event.selection.rows)
    except AttributeError:
        try:
            return list(event.get("selection", {}).get("rows", []))
        except AttributeError:
            return []


def _signal_column_config() -> dict[str, Any]:
    return {
        "Symbol": st.column_config.TextColumn(width="medium"),
        "Underlying": st.column_config.TextColumn(width="small"),
        "Expiry": st.column_config.DateColumn(format="DD MMM YYYY"),
        "Timeframe": st.column_config.TextColumn(width="small"),
        "Pattern type": st.column_config.TextColumn(width="large"),
        "Prev body size": st.column_config.NumberColumn(format="%.2f"),
        "Prev body ratio (%)": st.column_config.NumberColumn(format="%.1f%%"),
        "ATR x": st.column_config.NumberColumn(format="%.2f"),
        "Second candle": st.column_config.TextColumn(width="small"),
        "Touch": st.column_config.TextColumn(width="small"),
        "Second low": st.column_config.NumberColumn(format="%.2f"),
        "Second high": st.column_config.NumberColumn(format="%.2f"),
        "Second close": st.column_config.NumberColumn(format="%.2f"),
        "Second VWAP": st.column_config.NumberColumn(format="%.2f"),
        "VWAP distance (%)": st.column_config.NumberColumn(format="%.2f%%"),
        "Volume": st.column_config.NumberColumn(format="%d"),
        "Confirmation": st.column_config.TextColumn(width="medium"),
        "Signal time": st.column_config.DatetimeColumn(format="DD MMM YYYY HH:mm"),
    }


def _explanation_text(signal: VwapReversalSignal) -> str:
    if signal.is_exact_touch:
        interaction = "EXACT TOUCH"
    else:
        interaction = f"NEAR VWAP ({signal.vwap_distance_pct:+.2f}%)"
    status = signal.confirmation_status.upper()
    lines = [
        "VWAP REVERSAL PATTERN DETECTED",
        "",
        "CANDLE 1:",
        "Large bearish candle",
        f"Body ratio: {signal.bearish_body_ratio * 100:.0f}%",
        f"ATR multiple: {signal.bearish_atr_multiple:.1f}x",
        "",
        "CANDLE 2:",
        signal.candle_type.title(),
        f"VWAP interaction: {interaction}",
        f"VWAP: {signal.vwap:,.2f}",
        "",
        "STATUS:",
        status,
    ]
    if signal.confirmation_detail:
        lines.append(signal.confirmation_detail)
    return "\n".join(lines)


def _render_signal_detail(
    settings: Settings,
    report: VwapReversalReport,
    signal: VwapReversalSignal,
) -> None:
    theme.render_section_header(
        "Signal detail",
        f"{signal.symbol} · {signal.signal_type}",
        pills=[
            (signal.timeframe, "accent"),
            (
                "Exact touch" if signal.is_exact_touch else "Near VWAP",
                "vwap" if signal.is_exact_touch else "warn",
            ),
        ],
    )

    expiry = signal.expiry.strftime("%d %b %Y") if signal.expiry else "—"
    theme.render_info_grid(
        [
            {"label": "Underlying", "value": signal.underlying or "—"},
            {"label": "Expiry", "value": expiry},
            {"label": "Signal time", "value": f"{signal.second_timestamp:%d %b %H:%M}"},
            {"label": "Second candle", "value": signal.candle_type.title()},
            {"label": "VWAP interaction", "value": signal.touch_type.title(), "tone": "vwap"},
            {"label": "Session VWAP", "value": f"{signal.vwap:,.2f}", "tone": "vwap"},
            {
                "label": "VWAP distance",
                "value": f"{signal.vwap_distance_pct:+.2f}%",
                "tone": "bull" if signal.vwap_distance_pct >= 0 else "bear",
            },
            {"label": "Impulse body", "value": f"{signal.bearish_body_size:,.2f}"},
            {
                "label": "Impulse body ratio",
                "value": f"{signal.bearish_body_ratio * 100:.0f}%",
            },
            {
                "label": "Impulse ATR x",
                "value": f"{signal.bearish_atr_multiple:.2f}x",
            },
            {"label": "Second volume", "value": f"{signal.second_volume:,.0f}"},
            {"label": "Confirmation", "value": signal.confirmation_status},
        ]
    )

    left, right = st.columns([1.05, 1.15])
    with left:
        st.code(_explanation_text(signal), language="text")
        st.caption(
            "This is a price-action observation, not a guaranteed buy signal. "
            "The primary pattern is recorded when the second candle closes."
        )
    with right:
        theme.render_info_grid(
            [
                {"label": "Candle 1 open", "value": f"{signal.first_open:,.2f}"},
                {"label": "Candle 1 high", "value": f"{signal.first_high:,.2f}"},
                {"label": "Candle 1 low", "value": f"{signal.first_low:,.2f}"},
                {"label": "Candle 1 close", "value": f"{signal.first_close:,.2f}"},
                {"label": "Candle 2 open", "value": f"{signal.second_open:,.2f}"},
                {"label": "Candle 2 high", "value": f"{signal.second_high:,.2f}"},
                {"label": "Candle 2 low", "value": f"{signal.second_low:,.2f}"},
                {"label": "Candle 2 close", "value": f"{signal.second_close:,.2f}"},
            ]
        )
        if signal.third_timestamp is not None:
            st.caption(
                f"Third candle {signal.third_timestamp:%d %b %H:%M} · "
                f"close {signal.third_close:,.2f}"
                if signal.third_close is not None
                else f"Third candle {signal.third_timestamp:%d %b %H:%M}"
            )

    frame = report.histories.get(signal.symbol)
    if frame is None or frame.empty:
        theme.render_empty_state(
            "Chart unavailable",
            "Candle history was not retained for this contract in the last scan.",
            "Run the scan again to refresh the chart data.",
        )
        return

    controls = st.columns([1.0, 2.8])
    with controls[0]:
        lookback = st.selectbox(
            "Chart lookback",
            options=list(LOOKBACK_OPTIONS),
            key="vr_chart_lookback",
            format_func=lambda candles: f"{candles} candles",
        )
    with controls[1]:
        st.caption(
            "Scroll to zoom, drag to pan. The red band is the bearish impulse; "
            "the blue band is the doji / inverted hammer."
        )

    try:
        figure = plot_vwap_reversal_chart(
            frame,
            signal,
            lookback=int(lookback or settings.chart_lookback_candles),
        )
    except ValueError as exc:
        st.warning(f"Could not draw the chart: {exc}")
        return

    st.plotly_chart(
        figure,
        use_container_width=True,
        theme=None,
        config=chart_theme.chart_config(
            f"vwap_reversal_{signal.symbol.replace(':', '_')}_{report.timeframe}.png"
        ),
        key=(
            f"vr_chart_{signal.symbol}_{report.timeframe}_"
            f"{signal.second_timestamp.value}"
        ),
    )


@st.fragment
def render_vwap_reversal_results(
    settings: Settings, report: VwapReversalReport
) -> None:
    signals = report.sorted_signals()
    if not signals:
        theme.render_empty_state(
            "No VWAP reversal setups",
            "No contract currently shows a large bearish candle followed by an "
            "indecision candle at session VWAP.",
            "Relax the body, ATR or VWAP interaction filters and scan again.",
        )
        return

    theme.render_section_header(
        "Signal results",
        "Latest qualifying setup per contract. Search, filter, sort and select a "
        "row to inspect the chart.",
        pills=[
            (f"{len(signals)} signal(s)", "accent"),
            (report.timeframe, "vwap"),
        ],
    )

    controls = st.columns([1.5, 1.1, 1.1, 0.8])
    with controls[0]:
        query = st.text_input(
            "Search signals",
            key="vr_search",
            placeholder="Symbol, underlying, pattern or timeframe",
        )
    with controls[1]:
        sort_key = st.selectbox("Sort by", options=list(SORT_OPTIONS), key="vr_sort")
    with controls[2]:
        pattern_filter = st.multiselect(
            "Pattern types",
            options=list(SIGNAL_TYPES),
            key="vr_pattern_filter",
        )
    with controls[3]:
        with st.popover("Columns", icon=":material/view_column:"):
            current = [
                column
                for column in st.session_state.get("vr_visible_columns", [])
                if column in ALL_SIGNAL_COLUMNS
            ]
            if not current:
                current = list(DEFAULT_VISIBLE_COLUMNS)
                st.session_state["vr_visible_columns"] = current
            visible_selection = st.multiselect(
                "Visible columns",
                options=list(ALL_SIGNAL_COLUMNS),
                default=list(st.session_state["vr_visible_columns"]),
            )
            if list(visible_selection) != list(st.session_state["vr_visible_columns"]):
                st.session_state["vr_visible_columns"] = list(visible_selection)

    filter_row = st.columns([1.1, 3.0])
    with filter_row[0]:
        confirmation_filter = st.selectbox(
            "Confirmation",
            options=list(CONFIRMATION_FILTER_OPTIONS),
            key="vr_confirmation_filter",
        )

    filtered = _filter_signals(
        signals,
        query=query,
        patterns=list(pattern_filter),
        confirmation=confirmation_filter,
        sort_key=sort_key,
    )
    if not filtered:
        theme.render_empty_state(
            "No matching rows",
            "No signals match the current search and filters.",
            "Clear the search or widen the pattern selection.",
        )
        return

    with filter_row[1]:
        csv_table = _signals_dataframe(filtered)
        st.download_button(
            "Download filtered CSV",
            data=csv_table.to_csv(index=False).encode("utf-8"),
            file_name=f"vwap_reversal_{date.today():%Y%m%d}.csv",
            mime="text/csv",
            key="vr_download_csv",
            icon=":material/download:",
        )

    table = _signals_dataframe(filtered)
    visible_columns = [
        column
        for column in st.session_state.get("vr_visible_columns", [])
        if column in table.columns
    ]
    if not visible_columns:
        visible_columns = list(DEFAULT_VISIBLE_COLUMNS)

    event = st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        height=min(420, 34 * len(table) + 42),
        row_height=34,
        on_select="rerun",
        selection_mode="single-row",
        key="vr_results_table",
        column_order=visible_columns,
        column_config=_signal_column_config(),
    )
    st.caption(
        "Rows are the latest setup per contract inside the evaluation window. "
        "Select a row for the interactive chart."
    )

    rows = _selected_table_rows(event)
    selected_index = int(rows[0]) if rows else 0
    selected_index = min(selected_index, len(filtered) - 1)
    _render_signal_detail(settings, report, filtered[selected_index])


def render_vwap_reversal_report(
    settings: Settings, report: VwapReversalReport
) -> None:
    generated = pd.Timestamp(report.generated_at)
    if generated.tzinfo is None:
        generated = generated.tz_localize("UTC")
    generated = generated.tz_convert(settings.timezone)

    summary = report.summary()
    theme.render_metric_cards(
        [
            {
                "label": "Contracts scanned",
                "value": f"{summary['scanned']:,}",
                "tone": "accent",
            },
            {
                "label": "Large bearish candles",
                "value": f"{summary['large_bearish']:,}",
                "hint": "Impulse candidates in window",
                "tone": "bear",
            },
            {
                "label": "VWAP doji setups",
                "value": f"{summary['doji_setups']:,}",
                "tone": "vwap",
            },
            {
                "label": "VWAP inverted hammer setups",
                "value": f"{summary['inverted_hammer_setups']:,}",
                "tone": "warn",
            },
            {
                "label": "Exact VWAP touches",
                "value": f"{summary['exact_touches']:,}",
                "tone": "bull",
            },
            {
                "label": "Near VWAP setups",
                "value": f"{summary['near_touches']:,}",
                "hint": "Separate classification",
                "tone": "neutral",
            },
            {
                "label": "Bullish confirmations",
                "value": f"{summary['confirmations']:,}",
                "tone": "bull",
            },
            {
                "label": "Skipped contracts",
                "value": f"{summary['errors']:,}",
                "tone": "bear",
            },
        ],
        columns=4,
    )
    st.caption(
        f"Scan finished {generated:%d %b %Y %H:%M} IST · "
        f"{report.scanned_count} contracts · {_timeframe_label(report.timeframe)}"
    )

    render_vwap_reversal_results(settings, report)

    if report.errors:
        with st.expander(
            f"Scan log / skipped contracts ({len(report.errors)})", expanded=False
        ):
            errors = pd.DataFrame(
                [
                    {"Symbol": error.symbol, "Reason": error.message}
                    for error in report.errors
                ]
            )
            st.dataframe(errors, width="stretch", hide_index=True, height=220)


def _handle_auth_failure(settings: Settings, exc: Exception) -> None:
    TokenStore(settings).clear()
    st.session_state.pop("auth_status", None)
    st.session_state.pop("auth_token_mtime", None)
    st.session_state["vr_scan_error"] = (
        f"FYERS authentication failed: {exc}. Please login again."
    )


def render_vwap_reversal_page(
    settings: Settings,
    status: AuthStatus,
    provider: InstrumentMasterProvider,
) -> None:
    init_vwap_reversal_defaults(settings)
    theme.render_page_header(
        "VWAP Reversal Scanner",
        "Finds a large bearish impulse candle followed immediately by a doji or "
        "inverted hammer that touches the blue session VWAP line.",
        eyebrow="Price action at VWAP",
        chips=[("Read-only", "neutral"), ("Session anchored", "vwap")],
    )

    if not status.authenticated:
        shell.render_auth_required(settings, "run the VWAP reversal scanner")
        return

    context = render_vwap_reversal_toolbar(settings, status, provider)
    if context["instrument_error"]:
        st.error(context["instrument_error"])
        return

    active_frame = context["active_frame"]
    if active_frame.empty:
        st.warning("No active NSE futures contracts matched the current universe filters.")
        return

    config: VwapReversalConfig | None = context["config"]
    config_error = context["config_error"]
    instruments = context["instruments"]
    universe_label = context["universe_label"]

    if config_error:
        st.error(config_error)

    signature = None
    if config is not None and instruments:
        signature = (
            config,
            tuple(sorted(instrument.symbol for instrument in instruments)),
        )

    if context["scan_clicked"] and config is not None and instruments:
        try:
            with st.spinner("Fetching candles and scanning VWAP reversals..."):
                report = execute_vwap_reversal_scan(
                    settings, status, instruments, config
                )
        except AuthenticationError as exc:
            _handle_auth_failure(settings, exc)
        except FyersError as exc:
            st.session_state["vr_scan_error"] = f"FYERS request failed: {exc}"
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            st.session_state["vr_scan_error"] = f"Scan failed: {exc}"
        else:
            st.session_state["vr_scan_report"] = report
            st.session_state["vr_scan_signature"] = signature
            st.session_state["vr_scan_flash"] = (
                f"Scan complete: {len(report.signals)} VWAP reversal signal(s)."
            )
            try:
                ScanHistoryStore(settings.cache_dir / "scan_history.json").append(
                    report.to_history_record(universe_label)
                )
            except ScanHistoryError as exc:
                st.warning(f"Scan finished but history could not be saved: {exc}")
        st.rerun()

    flash = st.session_state.pop("vr_scan_flash", None)
    if flash:
        st.success(flash)
    error_flash = st.session_state.pop("vr_scan_error", None)
    if error_flash:
        st.error(error_flash)

    report = st.session_state.get("vr_scan_report")
    if isinstance(report, VwapReversalReport):
        if (
            signature is not None
            and st.session_state.get("vr_scan_signature") != signature
        ):
            st.info(
                "Filters, timeframe or contract selection changed since this scan. "
                "Run the scan again to refresh."
            )
        render_vwap_reversal_report(settings, report)
    else:
        theme.render_empty_state(
            "No scan yet",
            "Choose a universe, timeframe and thresholds, then run the scanner.",
            "The scanner evaluates completed candle pairs only.",
        )

    with st.expander("How this scanner works", expanded=False):
        st.markdown(
            """
            - **Candle 1** must be bearish, with a real body covering at least the
              configured share of its range and at least the configured ATR(14)
              multiple.
            - **Candle 2** must be the immediately following completed candle and
              must be a doji or an inverted hammer.
            - **VWAP touch** is a separate test: the second candle's high/low range
              must intersect that candle's own session VWAP. A close-proximity
              miss inside the tolerance is labelled **NEAR VWAP**, never an exact
              touch.
            - **VWAP** is the regular session VWAP reset at 09:15 IST, computed
              cumulatively so earlier candles never see later data.
            - **Confirmation** is optional and uses the third completed candle;
              the original setup is still recorded when candle 2 closes.
            - This is not a guaranteed buy signal. It is a read-only observation.
            """
        )
