"""Reusable Streamlit UI components for the AVWAP scanner."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard import theme
from scanner.models import ScanMatch, ScanReport


def render_summary(report: ScanReport) -> None:
    summary = report.summary()
    theme.render_metric_cards(
        [
            {"label": "Contracts scanned", "value": summary["scanned"], "tone": "accent"},
            {"label": "Matching patterns", "value": summary["matches"], "tone": "warn"},
            {"label": "AVWAP touches", "value": summary["touches"], "tone": "vwap"},
            {
                "label": "Consolidations",
                "value": summary["consolidations"],
                "tone": "neutral",
            },
            {"label": "AVWAP reclaims", "value": summary["reclaims"], "tone": "bull"},
        ]
    )
    if summary["errors"]:
        st.caption(f"{summary['errors']} contract(s) skipped — see the scan log below.")


def results_dataframe(report: ScanReport) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for match in report.sorted_matches():
        pattern = match.pattern
        rows.append(
            {
                "Symbol": match.symbol,
                "AVWAP": pattern.avwap,
                "Underlying": match.instrument.underlying,
                "Contract expiry": match.expiry,
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


def _selected_rows(event: Any) -> list[int]:
    try:
        return list(event.selection.rows)
    except AttributeError:
        try:
            return list(event.get("selection", {}).get("rows", []))
        except AttributeError:
            return []


def render_results_table(
    report: ScanReport,
    key: str = "avwap_results_table",
) -> tuple[pd.DataFrame, int | None]:
    table = results_dataframe(report)
    if table.empty:
        theme.render_empty_state(
            "No AVWAP matches",
            "No contracts currently match the configured AVWAP pattern.",
            "Relax the filters or run the scan again.",
        )
        return table, None

    event = st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        height=min(560, 34 * len(table) + 42),
        row_height=34,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
        column_config={
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
            "Pattern timestamp": st.column_config.DatetimeColumn(
                format="DD MMM YYYY HH:mm"
            ),
            "Pattern range (%)": st.column_config.NumberColumn(format="%.2f%%"),
            "ATR": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption("Sorted by absolute distance from the contract-start AVWAP (closest first).")
    rows = _selected_rows(event)
    return table, (int(rows[0]) if rows else None)


def render_scan_errors(report: ScanReport) -> None:
    if not report.errors:
        return
    with st.expander(
        f"Scan log / skipped contracts ({len(report.errors)})", expanded=False
    ):
        errors = pd.DataFrame(
            [{"Symbol": error.symbol, "Reason": error.message} for error in report.errors]
        )
        st.dataframe(errors, width="stretch", hide_index=True, height=220)


def select_match(report: ScanReport) -> ScanMatch | None:
    """Legacy selector kept for compatibility; prefer table row selection."""

    matches = report.sorted_matches()
    if not matches:
        return None
    labels = [
        (
            f"{match.symbol}  ·  {match.pattern.pattern_type}  ·  "
            f"{match.pattern.distance_pct:+.2f}%  ·  "
            f"{match.pattern.candle_count} candle(s)"
        )
        for match in matches
    ]
    choice = st.selectbox(
        "Open a matching contract chart",
        options=range(len(matches)),
        format_func=lambda index: labels[index],
        key="selected_avwap_match",
    )
    return matches[int(choice)]


def render_match_metrics(match: ScanMatch) -> None:
    pattern = match.pattern
    theme.render_metric_cards(
        [
            {"label": "Last price", "value": f"{pattern.last_price:,.2f}", "tone": "neutral"},
            {"label": "Contract AVWAP", "value": f"{pattern.avwap:,.2f}", "tone": "avwap"},
            {
                "label": "Distance",
                "value": f"{pattern.distance_pct:+.2f}%",
                "tone": "bull" if pattern.distance_pct >= 0 else "bear",
            },
            {"label": "Candles", "value": pattern.candle_count, "tone": "accent"},
            {
                "label": "Avg body ratio",
                "value": f"{pattern.avg_body_ratio:.3f}",
                "tone": "neutral",
            },
            {
                "label": "Classification",
                "value": pattern.pattern_type,
                "tone": "warn",
            },
        ]
    )
