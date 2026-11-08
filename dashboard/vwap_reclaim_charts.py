"""Plotly chart for a VWAP reclaim / bounce signal."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dashboard import chart_theme
from indicators.avwap import compute_contract_avwap
from scanner.vwap_reclaim.models import VwapReclaimSignal


def _view(candles: pd.DataFrame, lookback: int) -> tuple[pd.DataFrame, int]:
    required = {"timestamp", "open", "high", "low", "close", "volume", "vwap"}
    missing = required - set(candles.columns)
    if missing:
        raise ValueError(f"Chart data missing columns: {sorted(missing)}")

    lookback = max(int(lookback), 20)
    offset = max(len(candles) - lookback, 0)
    view = candles.iloc[offset:].reset_index(drop=True).copy()
    if len(view) < 2:
        raise ValueError("Not enough candles to draw a chart.")
    return view, offset


def _contract_avwap(candles: pd.DataFrame, offset: int) -> np.ndarray:
    values = compute_contract_avwap(candles.reset_index(drop=True), anchor_index=0)
    return values.iloc[offset:].to_numpy(dtype="float64", copy=False)


def plot_vwap_reclaim_chart(
    candles: pd.DataFrame,
    signal: VwapReclaimSignal,
    lookback: int = 120,
    show_avwap: bool = True,
) -> go.Figure:
    """Candles + session VWAP + optional contract AVWAP + highlighted pair."""

    if candles is None or candles.empty:
        raise ValueError("No candles available for chart.")

    view, offset = _view(candles, lookback)
    timestamps = pd.to_datetime(view["timestamp"])

    previous_index = len(view) - 2
    current_index = len(view) - 1
    matches = timestamps[timestamps == signal.previous_timestamp]
    if len(matches):
        previous_index = int(matches.index[0])
    matches = timestamps[timestamps == signal.signal_timestamp]
    if len(matches):
        current_index = int(matches.index[0])

    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.035,
        row_heights=[0.78, 0.22],
    )

    figure.add_trace(
        go.Candlestick(
            x=view["timestamp"],
            open=view["open"],
            high=view["high"],
            low=view["low"],
            close=view["close"],
            name="Candles",
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
            y=view["vwap"],
            mode="lines",
            name="Session VWAP",
            line={"color": chart_theme.VWAP_BLUE, "width": 2.4},
            hovertemplate="VWAP %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    if show_avwap:
        avwap_values = _contract_avwap(candles, offset)
        figure.add_trace(
            go.Scatter(
                x=view["timestamp"],
                y=avwap_values,
                mode="lines",
                name="Contract AVWAP",
                line={"color": chart_theme.AVWAP_RED, "width": 1.7, "dash": "dot"},
                hovertemplate="AVWAP %{y:.2f}<extra></extra>",
            ),
            row=1,
            col=1,
        )

    spacing = timestamps.diff().median()
    if pd.isna(spacing) or spacing <= pd.Timedelta(0):
        spacing = pd.Timedelta(minutes=15)
    figure.add_vrect(
        x0=timestamps.iloc[previous_index] - spacing / 2,
        x1=timestamps.iloc[current_index] + spacing / 2,
        fillcolor="rgba(99, 102, 241, 0.12)",
        line_width=1,
        line_color="rgba(99, 102, 241, 0.50)",
        layer="below",
        row=1,
        col=1,
    )

    figure.add_trace(
        go.Scatter(
            x=[timestamps.iloc[previous_index]],
            y=[float(view["low"].iloc[previous_index])],
            mode="markers",
            name="Previous candle",
            marker={
                "symbol": "diamond-open",
                "size": 11,
                "color": chart_theme.AMBER,
                "line": {"width": 2, "color": chart_theme.AMBER},
            },
            hovertemplate="Previous low %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=[timestamps.iloc[current_index]],
            y=[float(view["close"].iloc[current_index])],
            mode="markers",
            name="Signal candle",
            marker={
                "symbol": "circle",
                "size": 12,
                "color": chart_theme.BULL,
                "line": {"width": 2, "color": "#DCFCE7"},
            },
            hovertemplate="Signal close %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    figure.add_annotation(
        x=timestamps.iloc[current_index],
        y=float(view["high"].iloc[previous_index : current_index + 1].max()),
        text=f"{signal.signal_type} · {signal.distance_pct:+.2f}%",
        showarrow=True,
        arrowhead=2,
        arrowcolor=chart_theme.AMBER,
        font={"color": "#FCD34D", "size": 11},
        bgcolor="rgba(11, 16, 32, 0.92)",
        bordercolor="rgba(245, 158, 11, 0.45)",
        ax=0,
        ay=-30,
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

    expiry = f" · expiry {signal.expiry:%d %b %Y}" if signal.expiry else ""
    chart_theme.price_annotation(
        figure,
        (
            f"<b>{signal.symbol}</b>{expiry} · {signal.timeframe} · "
            f"VWAP {signal.current_vwap:,.2f} · close {signal.current_close:,.2f}"
        ),
    )

    chart_theme.style_figure(figure, height=640)
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
