"""Interactive Plotly candlestick chart for a matched AVWAP setup."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dashboard import chart_theme
from scanner.models import ScanMatch


def _pattern_window(
    frame: pd.DataFrame, match: ScanMatch, offset: int
) -> tuple[int, int]:
    start = max(match.pattern.start_idx - offset, 0)
    end = min(match.pattern.end_idx - offset, len(frame) - 1)
    if end < start:
        start, end = 0, len(frame) - 1
    return start, end


def plot_pattern_chart(
    candles: pd.DataFrame,
    match: ScanMatch,
    lookback: int = 120,
    show_vwap: bool = True,
) -> go.Figure:
    """Candles + contract AVWAP + optional session VWAP + pattern highlight."""

    if candles is None or candles.empty:
        raise ValueError("No candles available for chart.")

    required = {"timestamp", "open", "high", "low", "close", "avwap"}
    missing = required - set(candles.columns)
    if missing:
        raise ValueError(f"Chart data missing columns: {sorted(missing)}")

    lookback = max(int(lookback), 20)
    offset = max(len(candles) - lookback, 0)
    view = candles.iloc[offset:].reset_index(drop=True).copy()
    pattern_start, pattern_end = _pattern_window(view, match, offset)

    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.035,
        row_heights=[0.78, 0.22],
    )

    candle_label = (
        f"{match.resolution} min candles"
        if str(match.resolution).isdigit()
        else f"{match.resolution} candles"
    )
    figure.add_trace(
        go.Candlestick(
            x=view["timestamp"],
            open=view["open"],
            high=view["high"],
            low=view["low"],
            close=view["close"],
            name=candle_label,
            increasing_line_color=chart_theme.BULL,
            increasing_fillcolor=chart_theme.BULL,
            decreasing_line_color=chart_theme.BEAR,
            decreasing_fillcolor=chart_theme.BEAR,
            whiskerwidth=0.35,
        ),
        row=1,
        col=1,
    )

    figure.add_trace(
        go.Scatter(
            x=view["timestamp"],
            y=view["avwap"],
            mode="lines",
            name="Contract-start AVWAP",
            line={"color": chart_theme.AVWAP_RED, "width": 2.4},
            hovertemplate="AVWAP %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    if show_vwap and "session_vwap" in view.columns:
        figure.add_trace(
            go.Scatter(
                x=view["timestamp"],
                y=view["session_vwap"],
                mode="lines",
                name="Session VWAP",
                line={"color": chart_theme.VWAP_BLUE, "width": 1.7, "dash": "dot"},
                hovertemplate="Session VWAP %{y:.2f}<extra></extra>",
            ),
            row=1,
            col=1,
        )

    spacing = view["timestamp"].diff().median()
    if pd.isna(spacing) or spacing <= pd.Timedelta(0):
        spacing = pd.Timedelta(minutes=60)
    highlight_start = view["timestamp"].iloc[pattern_start] - spacing / 2
    highlight_end = view["timestamp"].iloc[pattern_end] + spacing / 2
    figure.add_vrect(
        x0=highlight_start,
        x1=highlight_end,
        fillcolor="rgba(245, 158, 11, 0.12)",
        line_width=1,
        line_color="rgba(245, 158, 11, 0.50)",
        layer="below",
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=view["timestamp"].iloc[pattern_start : pattern_end + 1],
            y=view["low"].iloc[pattern_start : pattern_end + 1],
            mode="markers",
            name="Pattern candles",
            marker={
                "symbol": "diamond-open",
                "size": 9,
                "color": chart_theme.AMBER,
                "line": {"width": 2, "color": chart_theme.AMBER},
            },
            hovertemplate="Pattern low %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    figure.add_annotation(
        x=view["timestamp"].iloc[pattern_start],
        y=view["high"].iloc[pattern_start : pattern_end + 1].max(),
        text=f"{match.pattern.pattern_type} · {match.pattern.candle_count} candle(s)",
        showarrow=True,
        arrowhead=2,
        arrowcolor=chart_theme.AMBER,
        font={"color": "#FCD34D", "size": 11},
        bgcolor="rgba(11, 16, 32, 0.92)",
        bordercolor="rgba(245, 158, 11, 0.45)",
        ax=0,
        ay=-28,
        row=1,
        col=1,
    )

    colors = np.where(view["close"] >= view["open"], chart_theme.BULL, chart_theme.BEAR)
    figure.add_trace(
        go.Bar(
            x=view["timestamp"],
            y=view["volume"],
            name="Volume",
            marker_color=colors,
            opacity=0.5,
        ),
        row=2,
        col=1,
    )

    last = view.iloc[-1]
    avwap_value = float(last["avwap"])
    last_price = float(last["close"])
    distance = (
        (last_price / avwap_value - 1.0) * 100.0
        if avwap_value and np.isfinite(avwap_value)
        else float("nan")
    )
    figure.add_annotation(
        x=last["timestamp"],
        y=last_price,
        text=f"LTP {last_price:,.2f}",
        showarrow=True,
        arrowhead=2,
        arrowcolor="#E2E8F0",
        font={"color": "#F8FAFC", "size": 11},
        bgcolor="rgba(29, 41, 59, 0.95)",
        borderpad=4,
        ay=-32,
        row=1,
        col=1,
    )
    chart_theme.price_annotation(
        figure,
        (
            f"<b>{match.symbol}</b> · expiry {match.expiry:%d %b %Y} · "
            f"AVWAP {avwap_value:,.2f} · distance {distance:+.2f}%"
        ),
    )

    chart_theme.style_figure(figure, height=650)
    figure.update_yaxes(
        title_text="Price",
        row=1,
        col=1,
        title_font={"size": 10, "color": chart_theme.MUTED},
    )
    figure.update_yaxes(
        title_text="Volume",
        showgrid=False,
        row=2,
        col=1,
        title_font={"size": 10, "color": chart_theme.MUTED},
    )
    return figure
