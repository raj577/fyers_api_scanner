"""AVWAP consolidation scanner page: toolbar, scan execution, results and detail."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

import pandas as pd
import streamlit as st

from config import Settings
from dashboard import chart_theme, shell, theme
from dashboard.charts import plot_pattern_chart
from dashboard.components import (
    render_match_metrics,
    render_results_table,
    render_scan_errors,
    render_summary,
)
from dashboard.presets_ui import render_preset_manager
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
from scanner.models import PATTERN_TYPES, PatternConfig, ScanMatch, ScanReport
from scanner.presets import PresetStore
from scanner.scan_engine import ScanEngine

TIMEFRAMES: dict[str, str] = {
    "1 hour (60 min)": "60",
    "2 hours (120 min)": "120",
    "4 hours (240 min)": "240",
}

RESOLUTION_LABELS: dict[str, str] = {
    "60": "1h",
    "120": "2h",
    "240": "4h",
}

SORT_OPTIONS = (
    "Distance (closest first)",
    "Distance (farthest first)",
    "Symbol (A-Z)",
    "Candles (most first)",
    "Pattern time (newest)",
)

ALL_MATCH_COLUMNS = (
    "Symbol",
    "Underlying",
    "Contract expiry",
    "AVWAP",
    "Last price",
    "Distance from AVWAP (%)",
    "Number of candles",
    "Candle body ratio",
    "Pattern classification",
    "Pattern timestamp",
    "Pattern range (%)",
    "ATR",
)

DEFAULT_VISIBLE_COLUMNS = (
    "Symbol",
    "Contract expiry",
    "AVWAP",
    "Last price",
    "Distance from AVWAP (%)",
    "Number of candles",
    "Pattern classification",
    "Pattern timestamp",
)

LOOKBACK_OPTIONS = (40, 60, 90, 120, 180, 240)

FILTER_DEFAULTS: dict[str, Any] = {
    "f_timeframe": "1 hour (60 min)",
    "f_min": 1,
    "f_max": 5,
    "f_above": 0.50,
    "f_below": 0.20,
    "f_low": 0.30,
    "f_body": 0.35,
    "f_range": 2.0,
    "f_use_atr": True,
    "f_atr": 2.0,
    "f_cons_range": 1.0,
    "f_classes": list(PATTERN_TYPES),
    "f_close_above_avwap": False,
    "f_close_above_vwap": False,
    "f_below_ref": "off",
    "f_below_candle": "previous",
}


def init_filter_defaults(settings: Settings | None = None) -> None:
    """Seed widget state once so presets can update widgets without warnings."""
    for key, value in FILTER_DEFAULTS.items():
        st.session_state.setdefault(
            key, list(value) if isinstance(value, list) else value
        )
    if settings is not None:
        st.session_state.setdefault("u_index", settings.include_index_futures)
        st.session_state.setdefault("a_chart_lookback", settings.chart_lookback_candles)
    st.session_state.setdefault("u_expiry", "Current active expiry")
    st.session_state.setdefault("u_scope", "All active contracts")
    st.session_state.setdefault("u_symbols", [])
    st.session_state.setdefault("a_show_vwap", True)
    st.session_state.setdefault("a_search", "")
    st.session_state.setdefault("a_sort", SORT_OPTIONS[0])
    st.session_state.setdefault("a_visible_columns", list(DEFAULT_VISIBLE_COLUMNS))


def build_pattern_config() -> tuple[PatternConfig | None, str | None, str]:
    row = st.columns(4)
    min_candles = int(
        row[0].number_input(
            "Minimum candles",
            min_value=1,
            max_value=5,
            step=1,
            key="f_min",
        )
    )
    max_candles = int(
        row[1].number_input(
            "Maximum candles",
            min_value=1,
            max_value=5,
            step=1,
            key="f_max",
        )
    )
    max_above = float(
        row[2].number_input(
            "Max above AVWAP (%)",
            min_value=0.0,
            max_value=10.0,
            step=0.05,
            key="f_above",
        )
    )
    max_below = float(
        row[3].number_input(
            "Max below AVWAP (%)",
            min_value=0.0,
            max_value=10.0,
            step=0.05,
            key="f_below",
        )
    )
    if min_candles > max_candles:
        return None, "Minimum candles cannot exceed maximum candles.", ""

    row = st.columns(3)
    max_low_below = float(
        row[0].number_input(
            "Max low below AVWAP (%)",
            min_value=0.0,
            max_value=10.0,
            step=0.05,
            key="f_low",
        )
    )
    max_body = float(
        row[1].number_input(
            "Maximum body ratio",
            min_value=0.0,
            max_value=1.0,
            step=0.01,
            key="f_body",
            help="|close-open| / (high-low). Lower values mean smaller/doji candles.",
        )
    )

    with st.expander("Advanced pattern constraints", expanded=False):
        max_range = float(
            st.number_input(
                "Maximum pattern range (% of AVWAP)",
                min_value=0.1,
                max_value=10.0,
                step=0.1,
                key="f_range",
                help="High-low range across all candles in the detected window.",
            )
        )
        use_atr = st.checkbox(
            "Cap each candle range at an ATR multiple",
            key="f_use_atr",
            help="ATR is used only as a small-candle sanity check.",
        )
        atr_multiple = float(
            st.number_input(
                "ATR range multiple",
                min_value=0.1,
                max_value=10.0,
                step=0.1,
                key="f_atr",
                disabled=not use_atr,
            )
        )
        consolidation_range = float(
            st.number_input(
                "Consolidation classification range (%)",
                min_value=0.1,
                max_value=10.0,
                step=0.1,
                key="f_cons_range",
                help="Patterns at least 3 candles wide below this range are "
                "labelled AVWAP CONSOLIDATION.",
            )
        )

    confirmation = st.columns(2)
    require_close_above_avwap = confirmation[0].checkbox(
        "Close above AVWAP",
        key="f_close_above_avwap",
        help=(
            "Keep only contracts whose most recent completed candle closed "
            "strictly above the contract-start AVWAP."
        ),
    )
    require_close_above_vwap = confirmation[1].checkbox(
        "Close above session VWAP",
        key="f_close_above_vwap",
        help=(
            "Keep only contracts whose most recent completed candle closed "
            "strictly above the daily-reset session VWAP."
        ),
    )

    earlier = st.columns(2)
    below_reference = earlier[0].selectbox(
        "Earlier candle closed below",
        options=["off", "avwap", "vwap", "either"],
        format_func=lambda value: {
            "off": "Off",
            "avwap": "AVWAP",
            "vwap": "Session VWAP",
            "either": "AVWAP or session VWAP",
        }[value],
        key="f_below_ref",
        help=(
            "Require an earlier candle to close strictly below the selected "
            "line. 'AVWAP or session VWAP' accepts either one."
        ),
    )
    below_candle = earlier[1].selectbox(
        "Which earlier candle",
        options=["previous", "second_previous", "either"],
        format_func=lambda value: {
            "previous": "Previous candle",
            "second_previous": "Second previous candle",
            "either": "Previous or second previous",
        }[value],
        key="f_below_candle",
        disabled=below_reference == "off",
        help=(
            "Candles are counted backwards from the most recent completed "
            "candle. 'Either' accepts whichever of the two is below."
        ),
    )

    if "f_classes" not in st.session_state:
        st.session_state["f_classes"] = list(PATTERN_TYPES)
    classifications = st.multiselect(
        "Pattern classification filter",
        options=list(PATTERN_TYPES),
        default=list(st.session_state["f_classes"]),
    )
    if list(classifications) != list(st.session_state["f_classes"]):
        st.session_state["f_classes"] = list(classifications)
    if not classifications:
        return None, "Select at least one pattern classification.", ""

    try:
        config = PatternConfig(
            max_close_above_pct=max_above / 100.0,
            max_close_below_pct=max_below / 100.0,
            max_low_below_pct=max_low_below / 100.0,
            max_body_ratio=max_body,
            min_candles=min_candles,
            max_candles=max_candles,
            max_window_range_pct=max_range / 100.0,
            max_candle_range_atr=atr_multiple if use_atr else None,
            consolidation_range_pct=consolidation_range / 100.0,
            allowed_patterns=tuple(classifications),
            require_close_above_avwap=require_close_above_avwap,
            require_close_above_vwap=require_close_above_vwap,
            below_reference=below_reference,
            below_candle=below_candle,
        )
    except ValueError as exc:
        return None, str(exc), ""

    summary = (
        f"{max_above:.2f}% above / {max_below:.2f}% below · "
        f"low {max_low_below:.2f}% · body {max_body:.2f} · "
        f"{min_candles}-{max_candles} candles"
    )
    if require_close_above_avwap and require_close_above_vwap:
        summary += " · close > AVWAP & VWAP"
    elif require_close_above_avwap:
        summary += " · close > AVWAP"
    elif require_close_above_vwap:
        summary += " · close > VWAP"
    if below_reference != "off":
        reference_labels = {
            "avwap": "AVWAP",
            "vwap": "VWAP",
            "either": "AVWAP/VWAP",
        }
        candle_labels = {
            "previous": "prev close",
            "second_previous": "2nd prev close",
            "either": "prev/2nd prev close",
        }
        summary += (
            f" · {candle_labels[below_candle]} < "
            f"{reference_labels[below_reference]}"
        )
    return config, None, summary


def execute_scan(
    settings: Settings,
    status: AuthStatus,
    instruments: list,
    config: PatternConfig,
    resolution: str,
) -> ScanReport:
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
    engine = ScanEngine(history_service, settings)

    progress = st.progress(0.0, text=f"Preparing {len(instruments)} contracts...")

    def on_progress(position: int, total: int, symbol: str) -> None:
        progress.progress(
            min(position / max(total, 1), 1.0),
            text=f"Scanning {symbol} ({position}/{total})",
        )

    report = engine.scan(
        instruments,
        pattern_config=config,
        resolution=resolution,
        progress_callback=on_progress,
    )
    progress.progress(1.0, text="Scan complete")
    return report


def render_avwap_toolbar(
    settings: Settings,
    status: AuthStatus,
    provider: InstrumentMasterProvider,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "active_frame": pd.DataFrame(),
        "selected_instruments": [],
        "config": None,
        "config_error": None,
        "config_summary": "",
        "universe_label": "",
        "instrument_error": None,
        "resolution": "60",
        "scan_clicked": False,
    }

    with st.container(key="avwap_toolbar"):
        theme.render_section_header(
            "Scanner controls",
            "Contract-start AVWAP support patterns across 1h, 2h and 4h candles.",
            pills=[("Read-only", "neutral"), ("Contract AVWAP", "avwap")],
        )

        universe_row = st.columns([1.05, 1.15, 1.1, 0.7])
        with universe_row[0]:
            include_index = st.checkbox("Include index futures", key="u_index")
        with universe_row[1]:
            expiry_mode = st.segmented_control(
                "Expiry scope",
                options=["Current active expiry", "All active expiries"],
                key="u_expiry",
                width="stretch",
            )
        with universe_row[2]:
            universe_mode = st.segmented_control(
                "Scan scope",
                options=["All active contracts", "Selected contracts"],
                key="u_scope",
                width="stretch",
            )
        with universe_row[3]:
            if st.button(
                "Refresh master",
                icon=":material/refresh:",
                key="u_refresh",
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
            with st.spinner("Loading NSE futures master..."):
                active_frame = provider.active_futures(
                    current_expiry_only=current_only,
                    include_index=include_index,
                )
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
                for symbol in st.session_state.get("u_symbols", [])
                if symbol in valid_symbols
            ]
            if current_symbols != st.session_state.get("u_symbols"):
                st.session_state["u_symbols"] = current_symbols
            label_map = {
                str(row.symbol): f"{row.symbol}  ·  {row.expiry:%d %b %Y}"
                for row in active_frame.itertuples(index=False)
            }
            selected_symbols = st.multiselect(
                "Select contracts",
                options=active_frame["symbol"].tolist(),
                format_func=lambda symbol: label_map.get(symbol, symbol),
                key="u_symbols",
            )
            selected_frame = active_frame.loc[
                active_frame["symbol"].isin(selected_symbols)
            ].copy()

        timeframe_row = st.columns([1.0, 2.6, 0.8])
        with timeframe_row[0]:
            timeframe_label = st.selectbox(
                "Timeframe",
                options=list(TIMEFRAMES.keys()),
                key="f_timeframe",
            )
        with timeframe_row[1]:
            st.caption(
                "Patterns require small candles near the red AVWAP line; the whole "
                "window must stay narrow relative to AVWAP."
            )
        with timeframe_row[2]:
            st.caption(f"{len(active_frame)} contracts")

        resolution = TIMEFRAMES[timeframe_label]
        context["resolution"] = resolution

        config, config_error, config_summary = build_pattern_config()
        context["config"] = config
        context["config_error"] = config_error
        context["config_summary"] = config_summary

        if config is not None and not selected_frame.empty:
            context["selected_instruments"] = to_instruments(selected_frame)
            context["universe_label"] = (
                f"{len(context['selected_instruments'])} selected contract(s)"
                if universe_mode == "Selected contracts"
                else f"all {len(context['selected_instruments'])} active contract(s)"
            )

        render_preset_manager(
            PresetStore(settings.presets_file),
            config,
            config_error,
            resolution,
            TIMEFRAMES,
        )

        action_row = st.columns([0.9, 2.6])
        with action_row[0]:
            context["scan_clicked"] = st.button(
                "Run scan",
                type="primary",
                icon=":material/play_arrow:",
                width="stretch",
                disabled=config is None or not context["selected_instruments"],
            )
        with action_row[1]:
            if context["config_summary"]:
                st.caption(
                    f"Universe: {context['universe_label'] or '—'} · "
                    f"{context['config_summary']}"
                )
            else:
                st.caption("Choose at least one contract to scan.")

    return context


def _matches_dataframe(matches: Iterable[ScanMatch]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for match in matches:
        pattern = match.pattern
        rows.append(
            {
                "Symbol": match.symbol,
                "Underlying": match.instrument.underlying,
                "Contract expiry": match.expiry,
                "AVWAP": pattern.avwap,
                "Last price": pattern.last_price,
                "Distance from AVWAP (%)": pattern.distance_pct,
                "Number of candles": pattern.candle_count,
                "Candle body ratio": pattern.avg_body_ratio,
                "Pattern classification": pattern.pattern_type,
                "Pattern timestamp": pattern.pattern_end,
                "Pattern range (%)": pattern.window_range_pct * 100.0,
                "ATR": pattern.latest_atr,
            }
        )
    return pd.DataFrame(rows)


def _filter_matches(
    matches: list[ScanMatch],
    query: str,
    sort_key: str,
) -> list[ScanMatch]:
    filtered = matches
    query = (query or "").strip().lower()
    if query:
        filtered = [
            match
            for match in filtered
            if query in match.symbol.lower()
            or query in match.instrument.underlying.lower()
            or query in match.pattern.pattern_type.lower()
        ]

    if sort_key == "Distance (farthest first)":
        return sorted(filtered, key=lambda match: abs(match.pattern.distance_pct), reverse=True)
    if sort_key == "Symbol (A-Z)":
        return sorted(filtered, key=lambda match: match.symbol)
    if sort_key == "Candles (most first)":
        return sorted(filtered, key=lambda match: match.pattern.candle_count, reverse=True)
    if sort_key == "Pattern time (newest)":
        return sorted(filtered, key=lambda match: match.pattern.pattern_end, reverse=True)
    return sorted(filtered, key=lambda match: abs(match.pattern.distance_pct))


def _selected_table_rows(event: Any) -> list[int]:
    try:
        return list(event.selection.rows)
    except AttributeError:
        try:
            return list(event.get("selection", {}).get("rows", []))
        except AttributeError:
            return []


def _match_column_config() -> dict[str, Any]:
    return {
        "Symbol": st.column_config.TextColumn(width="medium"),
        "AVWAP": st.column_config.NumberColumn(
            format="%.2f",
            help="Contract-start AVWAP from the first available contract candle.",
        ),
        "Contract expiry": st.column_config.DateColumn(format="DD MMM YYYY"),
        "Last price": st.column_config.NumberColumn(format="%.2f"),
        "Distance from AVWAP (%)": st.column_config.NumberColumn(
            format="%.2f%%",
            help="Signed percentage distance from the contract-start AVWAP.",
        ),
        "Number of candles": st.column_config.NumberColumn(format="%d"),
        "Candle body ratio": st.column_config.NumberColumn(
            format="%.3f",
            help="Average |close-open| / (high-low). Lower is more doji-like.",
        ),
        "Pattern classification": st.column_config.TextColumn(width="medium"),
        "Pattern timestamp": st.column_config.DatetimeColumn(format="DD MMM YYYY HH:mm"),
        "Pattern range (%)": st.column_config.NumberColumn(format="%.2f%%"),
        "ATR": st.column_config.NumberColumn(format="%.2f"),
    }


def _render_match_detail(
    settings: Settings,
    report: ScanReport,
    match: ScanMatch,
) -> None:
    pattern = match.pattern
    theme.render_section_header(
        "Pattern detail",
        f"{match.symbol} · {pattern.pattern_type}",
        pills=[
            (f"{RESOLUTION_LABELS.get(report.resolution, report.resolution)}", "accent"),
            (f"{pattern.candle_count} candle(s)", "warn"),
        ],
    )

    theme.render_info_grid(
        [
            {"label": "Underlying", "value": match.instrument.underlying},
            {"label": "Expiry", "value": f"{match.expiry:%d %b %Y}"},
            {"label": "Last price", "value": f"{pattern.last_price:,.2f}", "tone": "accent"},
            {"label": "Contract AVWAP", "value": f"{pattern.avwap:,.2f}", "tone": "avwap"},
            {
                "label": "Distance",
                "value": f"{pattern.distance_pct:+.2f}%",
                "tone": "bull" if pattern.distance_pct >= 0 else "bear",
            },
            {"label": "Candles", "value": pattern.candle_count},
            {"label": "Avg body", "value": f"{pattern.avg_body_ratio:.3f}"},
            {"label": "Max body", "value": f"{pattern.max_body_ratio:.3f}"},
            {"label": "Pattern range", "value": f"{pattern.window_range_pct * 100.0:.2f}%"},
            {
                "label": "ATR",
                "value": (
                    f"{pattern.latest_atr:.2f}"
                    if pattern.latest_atr is not None
                    else "—"
                ),
            },
            {"label": "Pattern start", "value": f"{pattern.pattern_start:%d %b %H:%M}"},
            {"label": "Pattern end", "value": f"{pattern.pattern_end:%d %b %H:%M}"},
            {"label": "Classification", "value": pattern.pattern_type, "tone": "warn"},
        ]
    )

    controls = st.columns([1.0, 0.9, 2.1])
    with controls[0]:
        show_vwap = st.toggle(
            "Session VWAP",
            key="a_show_vwap",
            help="Overlay the daily-reset session VWAP in blue.",
        )
    with controls[1]:
        lookback = st.selectbox(
            "Lookback",
            options=list(LOOKBACK_OPTIONS),
            key="a_chart_lookback",
            format_func=lambda candles: f"{candles} candles",
        )
    with controls[2]:
        st.caption(
            "Scroll to zoom, drag to pan. The highlighted window marks the "
            "detected pattern candles."
        )

    frame = report.histories.get(match.symbol)
    if frame is None or frame.empty:
        theme.render_empty_state(
            "Chart unavailable",
            "Candle history was not retained for this contract in the last scan.",
            "Run the scan again to refresh the chart data.",
        )
        return

    try:
        figure = plot_pattern_chart(
            frame,
            match,
            lookback=int(lookback or settings.chart_lookback_candles),
            show_vwap=bool(show_vwap),
        )
    except ValueError as exc:
        st.warning(f"Could not draw the chart: {exc}")
        return

    st.plotly_chart(
        figure,
        use_container_width=True,
        theme=None,
        config=chart_theme.chart_config(
            f"avwap_{match.symbol.replace(':', '_')}_{report.resolution}.png"
        ),
        key=(
            f"avwap_chart_{match.symbol}_{report.resolution}_"
            f"{pattern.pattern_end.value}"
        ),
    )


@st.fragment
def render_avwap_results(settings: Settings, report: ScanReport) -> None:
    matches = report.sorted_matches()
    if not matches:
        theme.render_empty_state(
            "No AVWAP matches",
            "No contracts currently match the configured AVWAP pattern.",
            "Relax the filters or run the scan again.",
        )
        return

    theme.render_section_header(
        "Matching contracts",
        "Search, sort and select a row to inspect the AVWAP pattern.",
        pills=[
            (f"{len(matches)} match(es)", "accent"),
            (RESOLUTION_LABELS.get(report.resolution, report.resolution), "avwap"),
        ],
    )

    controls = st.columns([1.5, 1.2, 0.8, 0.8])
    with controls[0]:
        query = st.text_input(
            "Search matches",
            key="a_search",
            placeholder="Symbol, underlying or classification",
        )
    with controls[1]:
        sort_key = st.selectbox("Sort by", options=list(SORT_OPTIONS), key="a_sort")
    with controls[2]:
        with st.popover("Columns", icon=":material/view_column:"):
            valid_columns = list(ALL_MATCH_COLUMNS)
            current = [
                column
                for column in st.session_state.get("a_visible_columns", [])
                if column in valid_columns
            ]
            if not current:
                current = list(DEFAULT_VISIBLE_COLUMNS)
            if current != st.session_state.get("a_visible_columns"):
                st.session_state["a_visible_columns"] = current
            visible_selection = st.multiselect(
                "Visible columns",
                options=valid_columns,
                default=list(st.session_state["a_visible_columns"]),
            )
            if list(visible_selection) != list(st.session_state["a_visible_columns"]):
                st.session_state["a_visible_columns"] = list(visible_selection)
    with controls[3]:
        filtered_for_csv = _filter_matches(matches, query, sort_key)
        csv_table = _matches_dataframe(filtered_for_csv)
        st.download_button(
            "CSV",
            data=csv_table.to_csv(index=False).encode("utf-8"),
            file_name=f"avwap_scan_{date.today():%Y%m%d}.csv",
            mime="text/csv",
            key="avwap_download_csv",
            icon=":material/download:",
            width="stretch",
        )

    filtered = _filter_matches(matches, query, sort_key)
    if not filtered:
        theme.render_empty_state(
            "No matching rows",
            "No contracts match the current search.",
            "Clear the search to see every match.",
        )
        return

    table = _matches_dataframe(filtered)
    visible_columns = [
        column
        for column in st.session_state.get("a_visible_columns", [])
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
        key="avwap_results_table",
        column_order=visible_columns,
        column_config=_match_column_config(),
    )
    st.caption("Sorted by absolute distance from AVWAP by default. Select a row for the chart.")

    rows = _selected_table_rows(event)
    selected_index = int(rows[0]) if rows else 0
    selected_index = min(selected_index, len(filtered) - 1)
    selected = filtered[selected_index]
    _render_match_detail(settings, report, selected)


def render_avwap_report(
    settings: Settings,
    report: ScanReport,
    resolution: str,
) -> None:
    generated = pd.Timestamp(report.generated_at)
    if generated.tzinfo is None:
        generated = generated.tz_localize("UTC")
    generated = generated.tz_convert(settings.timezone)
    st.caption(
        f"Scan finished {generated:%d %b %Y %H:%M} IST · "
        f"{report.scanned_count} contracts · {TIMEFRAMES.get(resolution, resolution)}"
    )
    render_summary(report)
    render_avwap_results(settings, report)
    render_scan_errors(report)


def _history_record(
    report: ScanReport,
    resolution: str,
    universe_label: str,
) -> dict[str, Any]:
    timeframe = RESOLUTION_LABELS.get(resolution, resolution)
    signals = []
    for match in report.sorted_matches():
        pattern = match.pattern
        signals.append(
            {
                "symbol": match.symbol,
                "underlying": match.instrument.underlying,
                "expiry": match.expiry.isoformat(),
                "signal": pattern.pattern_type,
                "timeframe": timeframe,
                "signal_time": pattern.pattern_end.isoformat(),
                "distance_pct": pattern.distance_pct,
                "close": pattern.last_price,
                "reference": pattern.avwap,
                "volume_ratio": None,
                "candles": pattern.candle_count,
            }
        )
    generated = report.generated_at.isoformat() if report.generated_at else None
    return {
        "scanner": "avwap",
        "generated_at": generated,
        "timeframe": timeframe,
        "universe": universe_label,
        "scanned": report.scanned_count,
        "matches": len(report.matches),
        "errors": len(report.errors),
        "signals": signals,
    }


def render_avwap_page(
    settings: Settings,
    status: AuthStatus,
    provider: InstrumentMasterProvider,
) -> None:
    init_filter_defaults(settings)
    theme.render_page_header(
        "AVWAP Patterns",
        "Contract-start AVWAP support scan for 1-5 small candles touching, hugging "
        "or consolidating around the red AVWAP line.",
        eyebrow="Support structure",
        chips=[("Read-only", "neutral"), ("Contract AVWAP", "avwap")],
    )

    if not status.authenticated:
        shell.render_auth_required(settings, "run the AVWAP pattern scanner")
        return

    context = render_avwap_toolbar(settings, status, provider)
    if context["instrument_error"]:
        st.error(context["instrument_error"])
        return

    active_frame = context["active_frame"]
    if active_frame.empty:
        st.warning("No active NSE futures contracts matched the current universe filters.")
        return

    config: PatternConfig | None = context["config"]
    config_error = context["config_error"]
    selected_instruments = context["selected_instruments"]
    resolution = context["resolution"]
    universe_label = context["universe_label"]

    if config_error:
        st.error(config_error)

    signature = None
    if config is not None and selected_instruments:
        signature = (
            resolution,
            tuple(sorted(instrument.symbol for instrument in selected_instruments)),
            config,
        )

    if context["scan_clicked"] and signature is not None and config is not None:
        st.session_state.pop("selected_avwap_match", None)
        try:
            with st.spinner("Fetching historical candles and scanning AVWAP..."):
                report = execute_scan(
                    settings,
                    status,
                    selected_instruments,
                    config,
                    resolution,
                )
        except AuthenticationError as exc:
            TokenStore(settings).clear()
            st.session_state.pop("auth_status", None)
            st.session_state.pop("auth_token_mtime", None)
            st.session_state["scan_error"] = (
                f"FYERS authentication failed: {exc}. Please login again."
            )
        except FyersError as exc:
            st.session_state["scan_error"] = f"FYERS request failed: {exc}"
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            st.session_state["scan_error"] = f"Scan failed: {exc}"
        else:
            st.session_state["scan_report"] = report
            st.session_state["scan_signature"] = signature
            st.session_state["scan_flash"] = (
                f"Scan complete: {len(report.matches)} matching contract(s)."
            )
            try:
                ScanHistoryStore(settings.cache_dir / "scan_history.json").append(
                    _history_record(report, resolution, universe_label)
                )
            except ScanHistoryError as exc:
                st.warning(f"Scan finished but history could not be saved: {exc}")
        st.rerun()

    flash = st.session_state.pop("scan_flash", None)
    if flash:
        st.success(flash)
    error_flash = st.session_state.pop("scan_error", None)
    if error_flash:
        st.error(error_flash)

    report = st.session_state.get("scan_report")
    if isinstance(report, ScanReport):
        if signature is not None and st.session_state.get("scan_signature") != signature:
            st.info(
                "Filters or contract selection changed since this scan. "
                "Run the scan again to refresh."
            )
        render_avwap_report(settings, report, report.resolution)
    else:
        theme.render_empty_state(
            "No scan yet",
            "Choose a universe and pattern filters, then run the AVWAP scan.",
            "The latest completed candles are evaluated for every selected contract.",
        )

    with st.expander("Methodology and data notes", expanded=False):
        st.markdown(
            """
            - The red line is the **contract-start AVWAP**: cumulative typical price
              `(high+low+close)/3` weighted by volume from the contract's first
              available candle. It is **never reset daily**.
            - The blue line is the regular **session VWAP**, reset each trading day.
              It is chart context only; the scanner uses the contract-start AVWAP.
            - Only completed candles are used for final pattern detection. The
              currently forming candle is discarded.
            - A match requires every candle in its window to be close to AVWAP,
              have a low within the configured distance below AVWAP, and have a
              small body. The whole window must also be narrow relative to AVWAP.
            - Patterns with 1, 2, 3, 4 or 5 candles are eligible. No breakout or
              fixed 6-10 candle requirement is applied.
            - The scanner is read-only and never calls any order endpoint.
            """
        )
