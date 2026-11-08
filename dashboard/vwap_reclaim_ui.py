"""VWAP reclaim scanner page: toolbar, scan execution, results and detail."""

from __future__ import annotations

import time
from datetime import date
from typing import Any, Iterable

import pandas as pd
import streamlit as st

from config import Settings
from dashboard import chart_theme, shell, theme
from dashboard.vwap_reclaim_charts import plot_vwap_reclaim_chart
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
from scanner.vwap_reclaim.config import VwapReclaimConfig
from scanner.vwap_reclaim.engine import VwapReclaimEngine
from scanner.vwap_reclaim.models import (
    SIGNAL_TYPES,
    VwapReclaimSignal,
    VwapScanReport,
)
from scanner.vwap_reclaim.timeframes import DEFAULT_TIMEFRAME, TIMEFRAMES

SORT_OPTIONS = (
    "Distance (closest first)",
    "Distance (farthest first)",
    "Signal time (newest)",
    "Symbol (A-Z)",
    "Volume x (highest)",
)

ALL_SIGNAL_COLUMNS = (
    "Symbol",
    "Underlying",
    "Expiry",
    "Signal",
    "Timeframe",
    "Signal time",
    "Previous close",
    "Previous VWAP",
    "Close",
    "VWAP",
    "Distance (%)",
    "Volume",
    "Avg volume",
    "Volume x",
    "Turnover",
)

DEFAULT_VISIBLE_COLUMNS = (
    "Symbol",
    "Expiry",
    "Signal",
    "Timeframe",
    "Signal time",
    "Close",
    "VWAP",
    "Distance (%)",
    "Volume x",
)

LOOKBACK_OPTIONS = (40, 60, 90, 120, 180, 240)

VWAP_DEFAULTS: dict[str, Any] = {
    "v_timeframe": DEFAULT_TIMEFRAME,
    "v_max_distance": 0.75,
    "v_min_distance": 0.0,
    "v_max_prev_below": 5.0,
    "v_use_volume": False,
    "v_current_low_below": False,
    "v_vol_ratio": 1.0,
    "v_vol_lookback": 20,
    "v_min_turnover": 0.0,
    "v_signal_types": list(SIGNAL_TYPES),
    "vu_expiry": "Current active expiry",
    "vu_scope": "All active contracts",
    "vu_symbols": [],
    "v_search": "",
    "v_sort": SORT_OPTIONS[0],
    "v_show_avwap": True,
    "v_chart_lookback": 120,
    "v_auto_refresh": False,
    "v_visible_columns": list(DEFAULT_VISIBLE_COLUMNS),
}


def init_vwap_defaults(settings: Settings | None = None) -> None:
    for key, value in VWAP_DEFAULTS.items():
        st.session_state.setdefault(
            key, list(value) if isinstance(value, list) else value
        )
    if settings is not None:
        st.session_state.setdefault("vu_index", settings.include_index_futures)
    st.session_state.setdefault(
        "pref_default_timeframe",
        st.session_state.get("v_timeframe", DEFAULT_TIMEFRAME),
    )
    st.session_state.setdefault("v_auto_tick_at", 0.0)
    st.session_state.setdefault("v_auto_last_run", 0.0)


def _timeframe_label(key: str) -> str:
    spec = TIMEFRAMES.get(key)
    return spec.label if spec else key


def build_vwap_config() -> tuple[VwapReclaimConfig | None, str | None, str]:
    timeframe = st.selectbox(
        "Timeframe",
        options=list(TIMEFRAMES),
        format_func=_timeframe_label,
        key="v_timeframe",
    )

    max_distance = float(
        st.number_input(
            "Max close above VWAP (%)",
            min_value=0.0,
            max_value=20.0,
            step=0.05,
            key="v_max_distance",
            help="Reject signals that have already extended too far above VWAP.",
        )
    )
    max_previous_below = float(
        st.number_input(
            "Max previous close below VWAP (%)",
            min_value=0.0,
            max_value=50.0,
            step=0.5,
            key="v_max_prev_below",
            help="Reject deep breakdowns; keep only shallow reclaim setups.",
        )
    )
    use_volume = st.checkbox(
        "Require volume expansion",
        key="v_use_volume",
        help="Compare the reclaim candle volume with the average of prior candles.",
    )
    volume_ratio = float(
        st.number_input(
            "Minimum volume multiple",
            min_value=0.1,
            max_value=10.0,
            step=0.1,
            key="v_vol_ratio",
            disabled=not use_volume,
        )
    )
    volume_lookback = int(
        st.number_input(
            "Volume average lookback (candles)",
            min_value=1,
            max_value=100,
            step=1,
            key="v_vol_lookback",
            disabled=not use_volume,
        )
    )
    signal_types = st.multiselect(
        "Signal types",
        options=list(SIGNAL_TYPES),
        key="v_signal_types",
    )

    with st.expander("Advanced filters", expanded=False):
        min_distance = float(
            st.number_input(
                "Min close above VWAP (%)",
                min_value=0.0,
                max_value=20.0,
                step=0.05,
                key="v_min_distance",
            )
        )
        min_turnover = float(
            st.number_input(
                "Minimum turnover (price x volume)",
                min_value=0.0,
                max_value=1_000_000_000.0,
                step=100_000.0,
                key="v_min_turnover",
                help="A rough liquidity floor; FYERS volume units vary by contract.",
            )
        )

    if not signal_types:
        return None, "Select at least one signal type.", ""
    if min_distance > max_distance:
        return None, "Minimum distance cannot exceed maximum distance.", ""

    try:
        config = VwapReclaimConfig(
            timeframe=timeframe,
            max_distance_pct=max_distance / 100.0,
            min_distance_pct=min_distance / 100.0,
            max_previous_below_pct=max_previous_below / 100.0,
            require_volume_confirmation=use_volume,
            min_volume_ratio=volume_ratio,
            volume_lookback=volume_lookback,
            min_turnover=min_turnover,
            allowed_signal_types=tuple(signal_types),
        )
    except ValueError as exc:
        return None, str(exc), ""

    summary = (
        f"{_timeframe_label(timeframe)} · close <= {max_distance:.2f}% above VWAP · "
        f"prev >= -{max_previous_below:.2f}%"
    )
    if use_volume:
        summary += f" · volume >= {volume_ratio:.1f}x ({volume_lookback})"
    return config, None, summary


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


def render_vwap_toolbar(
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
        "auto_refresh": bool(st.session_state.get("v_auto_refresh")),
    }

    with st.container(key="vwap_toolbar"):
        theme.render_section_header(
            "Scanner controls",
            "Universe, timeframe and signal filters. The scanner reads completed candles only.",
            pills=[("Read-only", "neutral"), ("Session VWAP", "vwap")],
        )

        universe_row = st.columns([1.05, 1.15, 1.1, 0.7])
        with universe_row[0]:
            include_index = st.checkbox(
                "Include index futures",
                key="vu_index",
            )
        with universe_row[1]:
            expiry_mode = st.segmented_control(
                "Expiry scope",
                options=["Current active expiry", "All active expiries"],
                key="vu_expiry",
                width="stretch",
            )
        with universe_row[2]:
            universe_mode = st.segmented_control(
                "Scan scope",
                options=["All active contracts", "Selected contracts"],
                key="vu_scope",
                width="stretch",
            )
        with universe_row[3]:
            if st.button(
                "Refresh master",
                icon=":material/refresh:",
                key="vu_refresh",
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
                for symbol in st.session_state.get("vu_symbols", [])
                if symbol in valid_symbols
            ]
            if current_symbols != st.session_state.get("vu_symbols"):
                st.session_state["vu_symbols"] = current_symbols
            label_map = {
                str(row.symbol): f"{row.symbol}  ·  {row.expiry:%d %b %Y}"
                for row in active_frame.itertuples(index=False)
            }
            selected_symbols = st.multiselect(
                "Select contracts",
                options=active_frame["symbol"].tolist(),
                format_func=lambda symbol: label_map.get(symbol, symbol),
                key="vu_symbols",
            )
            selected_frame = active_frame.loc[
                active_frame["symbol"].isin(selected_symbols)
            ].copy()

        config_row = st.columns([0.9, 1.1, 1.2, 1.4])
        with config_row[0]:
            timeframe = st.selectbox(
                "Timeframe",
                options=list(TIMEFRAMES),
                format_func=_timeframe_label,
                key="v_timeframe",
            )
        with config_row[1]:
            max_distance = float(
                st.number_input(
                    "Max above VWAP (%)",
                    min_value=0.0,
                    max_value=20.0,
                    step=0.05,
                    key="v_max_distance",
                    help="Reject signals already extended too far above VWAP.",
                )
            )
        with config_row[2]:
            max_previous_below = float(
                st.number_input(
                    "Max prev below VWAP (%)",
                    min_value=0.0,
                    max_value=50.0,
                    step=0.5,
                    key="v_max_prev_below",
                    help="Reject deep breakdowns; keep only shallow reclaim setups.",
                )
            )
        with config_row[3]:
            if "v_signal_types" not in st.session_state:
                st.session_state["v_signal_types"] = list(SIGNAL_TYPES)
            signal_types = st.multiselect(
                "Signal types",
                options=list(SIGNAL_TYPES),
                default=list(st.session_state["v_signal_types"]),
            )
            if list(signal_types) != list(st.session_state["v_signal_types"]):
                st.session_state["v_signal_types"] = list(signal_types)

        volume_row = st.columns([1.0, 0.8, 1.0, 1.2, 1.0])
        with volume_row[0]:
            use_volume = st.checkbox(
                "Require volume expansion",
                key="v_use_volume",
                help="Compare the reclaim candle with the prior average.",
            )
        with volume_row[1]:
            volume_ratio = float(
                st.number_input(
                    "Min volume x",
                    min_value=0.1,
                    max_value=10.0,
                    step=0.1,
                    key="v_vol_ratio",
                    disabled=not use_volume,
                )
            )
        with volume_row[2]:
            volume_lookback = int(
                st.number_input(
                    "Volume lookback",
                    min_value=1,
                    max_value=100,
                    step=1,
                    key="v_vol_lookback",
                    disabled=not use_volume,
                )
            )
        with volume_row[3]:
            with st.expander("Advanced filters", expanded=False):
                current_low_below = st.checkbox(
                    "Current low below VWAP, close above VWAP",
                    key="v_current_low_below",
                    help=(
                        "The current completed candle must trade below VWAP "
                        "(low < VWAP) and close above VWAP."
                    ),
                )
                min_distance = float(
                    st.number_input(
                        "Min above VWAP (%)",
                        min_value=0.0,
                        max_value=20.0,
                        step=0.05,
                        key="v_min_distance",
                    )
                )
                min_turnover = float(
                    st.number_input(
                        "Minimum turnover",
                        min_value=0.0,
                        max_value=1_000_000_000.0,
                        step=100_000.0,
                        key="v_min_turnover",
                        help="Rough liquidity floor; FYERS volume units vary.",
                    )
                )
        with volume_row[4]:
            st.caption(
                "Higher timeframes are session-anchored from 09:15 IST and use "
                "completed candles only."
            )

        if not signal_types:
            config, config_error, config_summary = (
                None,
                "Select at least one signal type.",
                "",
            )
        elif min_distance > max_distance:
            config, config_error, config_summary = (
                None,
                "Minimum distance cannot exceed maximum distance.",
                "",
            )
        else:
            try:
                config = VwapReclaimConfig(
                    timeframe=timeframe,
                    max_distance_pct=max_distance / 100.0,
                    min_distance_pct=min_distance / 100.0,
                    max_previous_below_pct=max_previous_below / 100.0,
                    require_current_low_below_vwap=current_low_below,
                    require_volume_confirmation=use_volume,
                    min_volume_ratio=volume_ratio,
                    volume_lookback=volume_lookback,
                    min_turnover=min_turnover,
                    allowed_signal_types=tuple(signal_types),
                )
            except ValueError as exc:
                config, config_error, config_summary = None, str(exc), ""
            else:
                config_error = None
                config_summary = (
                    f"{_timeframe_label(timeframe)} · close <= {max_distance:.2f}% "
                    f"above VWAP · prev >= -{max_previous_below:.2f}%"
                )
                if current_low_below:
                    config_summary += " · current low < VWAP < close"
                if use_volume:
                    config_summary += (
                        f" · volume >= {volume_ratio:.1f}x ({volume_lookback})"
                    )

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

        action_row = st.columns([0.9, 0.9, 2.4])
        with action_row[0]:
            context["scan_clicked"] = st.button(
                "Run scan",
                type="primary",
                icon=":material/play_arrow:",
                width="stretch",
                disabled=config is None or not context["instruments"],
            )
        with action_row[1]:
            context["auto_refresh"] = st.toggle(
                "Auto 60s",
                key="v_auto_refresh",
                help="Re-run the last scan every 60 seconds. Run once to arm it.",
            )
        with action_row[2]:
            status_text = (
                f"Universe: {context['universe_label'] or '—'}"
                if context["universe_label"]
                else "Select at least one contract to scan."
            )
            if context["config_summary"]:
                status_text += f" · {context['config_summary']}"
            st.caption(status_text)

    return context


def execute_vwap_scan(
    settings: Settings,
    status: AuthStatus,
    instruments: list,
    config: VwapReclaimConfig,
    show_progress: bool = True,
) -> VwapScanReport:
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
    engine = VwapReclaimEngine(history_service, settings)

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


def _signals_dataframe(signals: Iterable[VwapReclaimSignal]) -> pd.DataFrame:
    return pd.DataFrame([signal.to_row() for signal in signals])


def _filter_signals(
    signals: list[VwapReclaimSignal],
    query: str,
    sort_key: str,
) -> list[VwapReclaimSignal]:
    filtered = signals
    query = (query or "").strip().lower()
    if query:
        filtered = [
            signal
            for signal in filtered
            if query in signal.symbol.lower()
            or query in (signal.underlying or "").lower()
            or query in signal.signal_type.lower()
            or query in signal.timeframe.lower()
        ]

    if sort_key == "Distance (farthest first)":
        return sorted(filtered, key=lambda signal: signal.distance_pct, reverse=True)
    if sort_key == "Signal time (newest)":
        return sorted(
            filtered, key=lambda signal: signal.signal_timestamp, reverse=True
        )
    if sort_key == "Symbol (A-Z)":
        return sorted(filtered, key=lambda signal: signal.symbol)
    if sort_key == "Volume x (highest)":
        return sorted(
            filtered,
            key=lambda signal: signal.volume_ratio or 0.0,
            reverse=True,
        )
    return sorted(filtered, key=lambda signal: signal.distance_pct)


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
        "Expiry": st.column_config.DateColumn(format="DD MMM YYYY"),
        "Signal time": st.column_config.DatetimeColumn(format="DD MMM YYYY HH:mm"),
        "Previous close": st.column_config.NumberColumn(format="%.2f"),
        "Previous VWAP": st.column_config.NumberColumn(format="%.2f"),
        "Close": st.column_config.NumberColumn(format="%.2f"),
        "VWAP": st.column_config.NumberColumn(format="%.2f"),
        "Distance (%)": st.column_config.NumberColumn(format="%.2f%%"),
        "Volume": st.column_config.NumberColumn(format="%d"),
        "Avg volume": st.column_config.NumberColumn(format="%d"),
        "Volume x": st.column_config.NumberColumn(format="%.2f"),
        "Turnover": st.column_config.NumberColumn(format="%.0f"),
    }


def _render_signal_detail(
    settings: Settings,
    report: VwapScanReport,
    signal: VwapReclaimSignal,
) -> None:
    theme.render_section_header(
        "Signal detail",
        f"{signal.symbol} · {signal.signal_type}",
        pills=[
            (signal.timeframe, "accent"),
            (
                "Bounce" if signal.is_bounce else "Reclaim",
                "bull" if signal.is_bounce else "vwap",
            ),
        ],
    )

    expiry = signal.expiry.strftime("%d %b %Y") if signal.expiry else "—"
    theme.render_info_grid(
        [
            {"label": "Underlying", "value": signal.underlying or "—"},
            {"label": "Expiry", "value": expiry},
            {"label": "Signal time", "value": f"{signal.signal_timestamp:%d %b %H:%M}"},
            {"label": "Close", "value": f"{signal.current_close:,.2f}", "tone": "accent"},
            {"label": "Session VWAP", "value": f"{signal.current_vwap:,.2f}", "tone": "vwap"},
            {
                "label": "Distance",
                "value": f"{signal.distance_pct:+.2f}%",
                "tone": "bull" if signal.distance_pct >= 0 else "bear",
            },
            {"label": "Previous close", "value": f"{signal.previous_close:,.2f}"},
            {"label": "Previous VWAP", "value": f"{signal.previous_vwap:,.2f}", "tone": "vwap"},
            {
                "label": "Previous distance",
                "value": f"{signal.previous_distance_pct:+.2f}%",
                "tone": "bear" if signal.previous_distance_pct < 0 else "bull",
            },
            {"label": "Volume", "value": f"{signal.current_volume:,.0f}"},
            {
                "label": "Avg volume",
                "value": (
                    f"{signal.average_volume:,.0f}"
                    if signal.average_volume is not None
                    else "—"
                ),
            },
            {
                "label": "Volume x",
                "value": (
                    f"{signal.volume_ratio:.2f}x"
                    if signal.volume_ratio is not None
                    else "—"
                ),
                "tone": "warn",
            },
            {"label": "Turnover", "value": f"{signal.turnover:,.0f}"},
        ]
    )

    controls = st.columns([1.0, 0.9, 2.1])
    with controls[0]:
        show_avwap = st.toggle(
            "Contract AVWAP",
            key="v_show_avwap",
            help="Overlay the contract-start AVWAP in red.",
        )
    with controls[1]:
        lookback = st.selectbox(
            "Lookback",
            options=list(LOOKBACK_OPTIONS),
            key="v_chart_lookback",
            format_func=lambda candles: f"{candles} candles",
        )
    with controls[2]:
        st.caption(
            "Scroll to zoom, drag to pan. The highlighted pair marks the previous "
            "and signal candles."
        )

    frame = report.histories.get(signal.symbol)
    if frame is None or frame.empty:
        theme.render_empty_state(
            "Chart unavailable",
            "Candle history was not retained for this contract in the last scan.",
            "Run the scan again to refresh the chart data.",
        )
        return

    try:
        figure = plot_vwap_reclaim_chart(
            frame,
            signal,
            lookback=int(lookback or settings.chart_lookback_candles),
            show_avwap=bool(show_avwap),
        )
    except ValueError as exc:
        st.warning(f"Could not draw the chart: {exc}")
        return

    st.plotly_chart(
        figure,
        use_container_width=True,
        theme=None,
        config=chart_theme.chart_config(
            f"vwap_{signal.symbol.replace(':', '_')}_{report.timeframe}.png"
        ),
        key=(
            f"vwap_chart_{signal.symbol}_{report.timeframe}_"
            f"{signal.signal_timestamp.value}"
        ),
    )


@st.fragment
def render_vwap_results(settings: Settings, report: VwapScanReport) -> None:
    signals = report.sorted_signals()
    if not signals:
        theme.render_empty_state(
            "No VWAP signals",
            "No contracts currently satisfy the configured reclaim filters.",
            "Relax the distance or volume filters and run the scan again.",
        )
        return

    theme.render_section_header(
        "Signal results",
        "Search, sort and select a row to inspect the candle pair and chart.",
        pills=[
            (f"{len(signals)} signal(s)", "accent"),
            (report.timeframe, "vwap"),
        ],
    )

    controls = st.columns([1.5, 1.2, 0.8, 0.8])
    with controls[0]:
        query = st.text_input(
            "Search signals",
            key="v_search",
            placeholder="Symbol, underlying, signal or timeframe",
        )
    with controls[1]:
        sort_key = st.selectbox("Sort by", options=list(SORT_OPTIONS), key="v_sort")
    with controls[2]:
        with st.popover("Columns", icon=":material/view_column:"):
            valid_columns = [column for column in ALL_SIGNAL_COLUMNS]
            current = [
                column
                for column in st.session_state.get("v_visible_columns", [])
                if column in valid_columns
            ]
            if not current:
                current = list(DEFAULT_VISIBLE_COLUMNS)
                st.session_state["v_visible_columns"] = current
            visible_selection = st.multiselect(
                "Visible columns",
                options=valid_columns,
                default=list(st.session_state["v_visible_columns"]),
            )
            if list(visible_selection) != list(st.session_state["v_visible_columns"]):
                st.session_state["v_visible_columns"] = list(visible_selection)
    with controls[3]:
        filtered_for_csv = _filter_signals(signals, query, sort_key)
        csv_table = _signals_dataframe(filtered_for_csv)
        st.download_button(
            "CSV",
            data=csv_table.to_csv(index=False).encode("utf-8"),
            file_name=f"vwap_reclaim_{date.today():%Y%m%d}.csv",
            mime="text/csv",
            key="vwap_download_csv",
            icon=":material/download:",
            width="stretch",
        )

    filtered = _filter_signals(signals, query, sort_key)
    if not filtered:
        theme.render_empty_state(
            "No matching rows",
            "No signals match the current search.",
            "Clear the search to see every signal.",
        )
        return

    table = _signals_dataframe(filtered)
    visible_columns = [
        column
        for column in st.session_state.get("v_visible_columns", [])
        if column in table.columns
    ]
    if not visible_columns:
        visible_columns = list(DEFAULT_VISIBLE_COLUMNS)

    event = st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        height=min(380, 34 * len(table) + 42),
        row_height=34,
        on_select="rerun",
        selection_mode="single-row",
        key="vwap_results_table",
        column_order=visible_columns,
        column_config=_signal_column_config(),
    )
    st.caption("Sorted by distance above VWAP by default. Select a row for the chart.")

    rows = _selected_table_rows(event)
    selected_index = int(rows[0]) if rows else 0
    selected_index = min(selected_index, len(filtered) - 1)
    selected = filtered[selected_index]
    _render_signal_detail(settings, report, selected)


def render_vwap_report(settings: Settings, report: VwapScanReport) -> None:
    generated = pd.Timestamp(report.generated_at)
    if generated.tzinfo is None:
        generated = generated.tz_localize("UTC")
    generated = generated.tz_convert(settings.timezone)

    summary = report.summary()
    theme.render_metric_cards(
        [
            {"label": "Contracts scanned", "value": summary["scanned"], "tone": "accent"},
            {"label": "Signals", "value": summary["signals"], "tone": "warn"},
            {"label": "Reclaims", "value": summary["reclaims"], "tone": "vwap"},
            {"label": "Bounces", "value": summary["bounces"], "tone": "bull"},
            {"label": "Skipped", "value": summary["errors"], "tone": "bear"},
        ]
    )
    st.caption(
        f"Scan finished {generated:%d %b %Y %H:%M} IST · "
        f"{report.scanned_count} contracts · {_timeframe_label(report.timeframe)}"
    )

    render_vwap_results(settings, report)

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


@st.fragment(run_every=60)
def _auto_refresh_tick() -> None:
    if not st.session_state.get("v_auto_refresh"):
        return
    if st.session_state.get("vwap_scan_report") is None:
        return
    now = time.time()
    last_tick = float(st.session_state.get("v_auto_tick_at", 0.0))
    if now - last_tick < 55:
        return
    st.session_state["v_auto_tick_at"] = now
    st.session_state["v_auto_due"] = True
    st.rerun(scope="app")


def _handle_auth_failure(settings: Settings, exc: Exception) -> None:
    TokenStore(settings).clear()
    st.session_state.pop("auth_status", None)
    st.session_state.pop("auth_token_mtime", None)
    st.session_state["vwap_scan_error"] = (
        f"FYERS authentication failed: {exc}. Please login again."
    )


def _run_auto_scan(
    settings: Settings,
    status: AuthStatus,
    config: VwapReclaimConfig,
    instruments: list,
    signature: Any,
) -> None:
    report = st.session_state.get("vwap_scan_report")
    if report is None:
        return
    if st.session_state.get("vwap_scan_signature") != signature:
        return
    now = time.time()
    if now - float(st.session_state.get("v_auto_last_run", 0.0)) < 55:
        return
    try:
        with st.spinner("Auto-refreshing VWAP scan..."):
            refreshed = execute_vwap_scan(
                settings, status, instruments, config, show_progress=False
            )
    except AuthenticationError as exc:
        _handle_auth_failure(settings, exc)
    except FyersError as exc:
        st.session_state["vwap_scan_error"] = f"FYERS request failed: {exc}"
    except Exception as exc:  # pragma: no cover - defensive UI boundary
        st.session_state["vwap_scan_error"] = f"Auto-refresh failed: {exc}"
    else:
        st.session_state["vwap_scan_report"] = refreshed
        st.session_state["vwap_scan_signature"] = signature
        st.session_state["v_auto_last_run"] = now
        st.session_state["v_auto_tick_at"] = now
        st.session_state["vwap_auto_flash"] = (
            f"Auto-refreshed at {pd.Timestamp.now(tz=settings.timezone):%H:%M:%S} · "
            f"{len(refreshed.signals)} signal(s)."
        )


def render_vwap_page(
    settings: Settings,
    status: AuthStatus,
    provider: InstrumentMasterProvider,
) -> None:
    init_vwap_defaults(settings)
    theme.render_page_header(
        "VWAP Reclaim",
        "Finds contracts that lose session VWAP on one completed candle and close "
        "back above it on the next. Scans the latest completed pair only.",
        eyebrow="Momentum reversal",
        chips=[("Read-only", "neutral"), ("Session anchored", "vwap")],
    )

    if not status.authenticated:
        shell.render_auth_required(settings, "run the VWAP reclaim scanner")
        return

    context = render_vwap_toolbar(settings, status, provider)
    if context["instrument_error"]:
        st.error(context["instrument_error"])
        return

    active_frame = context["active_frame"]
    if active_frame.empty:
        st.warning("No active NSE futures contracts matched the current universe filters.")
        return

    config: VwapReclaimConfig | None = context["config"]
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
        st.session_state.pop("selected_vwap_signal", None)
        try:
            with st.spinner("Fetching candles and scanning VWAP reclaims..."):
                report = execute_vwap_scan(settings, status, instruments, config)
        except AuthenticationError as exc:
            _handle_auth_failure(settings, exc)
        except FyersError as exc:
            st.session_state["vwap_scan_error"] = f"FYERS request failed: {exc}"
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            st.session_state["vwap_scan_error"] = f"Scan failed: {exc}"
        else:
            st.session_state["vwap_scan_report"] = report
            st.session_state["vwap_scan_signature"] = signature
            st.session_state["v_auto_last_run"] = time.time()
            st.session_state["v_auto_tick_at"] = time.time()
            st.session_state["vwap_scan_flash"] = (
                f"Scan complete: {len(report.signals)} VWAP signal(s)."
            )
            try:
                ScanHistoryStore(settings.cache_dir / "scan_history.json").append(
                    report.to_history_record(universe_label)
                )
            except ScanHistoryError as exc:
                st.warning(f"Scan finished but history could not be saved: {exc}")
        st.rerun()

    flash = st.session_state.pop("vwap_scan_flash", None)
    if flash:
        st.success(flash)
    auto_flash = st.session_state.pop("vwap_auto_flash", None)
    if auto_flash:
        st.caption(auto_flash)
    error_flash = st.session_state.pop("vwap_scan_error", None)
    if error_flash:
        st.error(error_flash)

    _auto_refresh_tick()
    if st.session_state.pop("v_auto_due", False):
        if (
            context["auto_refresh"]
            and config is not None
            and instruments
            and signature is not None
        ):
            _run_auto_scan(settings, status, config, instruments, signature)

    report = st.session_state.get("vwap_scan_report")
    if isinstance(report, VwapScanReport):
        if signature is not None and st.session_state.get("vwap_scan_signature") != signature:
            st.info(
                "Filters or contract selection changed since this scan. "
                "Run the scan again to refresh."
            )
        render_vwap_report(settings, report)
    else:
        theme.render_empty_state(
            "No scan yet",
            "Choose a universe and filters, then run the VWAP reclaim scan.",
            "The latest completed candle pair is evaluated for every selected contract.",
        )

    with st.expander("How this scanner works", expanded=False):
        st.markdown(
            """
            - **VWAP** is the regular session VWAP, reset at 09:15 IST each trading day.
            - **Reclaim:** the previous completed candle closed below VWAP *or* traded
              below it, and the current completed candle closes strictly above VWAP.
            - **Bounce:** the previous candle wicked below VWAP but closed back above it,
              and the current candle also closes above VWAP.
            - Only completed candles are used; the forming candle is discarded.
            - Higher timeframes are built from session-anchored 09:15 buckets.
            - The scanner is read-only and never calls an order endpoint.
            """
        )
