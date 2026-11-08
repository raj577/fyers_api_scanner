"""Plotly chart for a bearish-impulse VWAP reversal signal."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dashboard import chart_theme
from scanner.vwap_reversal.models import VwapReversalSignal


def _view(candles: pd.DataFrame, lookback: int) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close", "volume", "vwap"}
    missing = required - set(candles.columns)
    if missing:
        raise ValueError(f"Chart data missing columns: {sorted(missing)}")

    lookback = max(int(lookback), 10)
    view = candles.iloc[-lookback:].reset_index(drop=True).copy()
    if len(view) < 2:
        raise ValueError("Not enough candles to draw a chart.")
    return view


def _locate(view: pd.DataFrame, timestamp: pd.Timestamp, fallback: int) -> int:
    timestamps = pd.to_datetime(view["timestamp"])
    matches = timestamps[timestamps == timestamp]
    if len(matches):
        return int(matches.index[0])
    return fallback


def plot_vwap_reversal_chart(
    candles: pd.DataFrame,
    signal: VwapReversalSignal,
    lookback: int = 120,
) -> go.Figure:
    """Candles + blue session VWAP + highlighted impulse and reversal candles."""

    if candles is None or candles.empty:
        raise ValueError("No candles available for chart.")

    view = _view(candles, lookback)
    timestamps = pd.to_datetime(view["timestamp"])

    first_index = len(view) - 2
    second_index = len(view) - 1
    first_index = _locate(view, signal.first_timestamp, first_index)
    second_index = _locate(view, signal.second_timestamp, second_index)
    if second_index <= first_index:
        first_index = max(second_index - 1, 0)

    spacing = timestamps.diff().median()
    if pd.isna(spacing) or spacing <= pd.Timedelta(0):
        spacing = pd.Timedelta(minutes=15)
    half = spacing / 2

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
            hoverlabel={"namelength": 0},
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
            line={"color": chart_theme.VWAP_BLUE, "width": 2.6},
            hovertemplate="VWAP %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    figure.add_vrect(
        x0=timestamps.iloc[first_index] - half,
        x1=timestamps.iloc[first_index] + half,
        fillcolor="rgba(239, 68, 68, 0.18)",
        line_width=1,
        line_color="rgba(239, 68, 68, 0.55)",
        layer="below",
        row=1,
        col=1,
        annotation_text="Impulse",
        annotation_position="top left",
        annotation_font={"size": 10, "color": chart_theme.BEAR},
    )
    figure.add_vrect(
        x0=timestamps.iloc[second_index] - half,
        x1=timestamps.iloc[second_index] + half,
        fillcolor="rgba(56, 189, 248, 0.16)",
        line_width=1,
        line_color="rgba(56, 189, 248, 0.55)",
        layer="below",
        row=1,
        col=1,
        annotation_text=signal.candle_type.title(),
        annotation_position="top right",
        annotation_font={"size": 10, "color": chart_theme.VWAP_BLUE},
    )

    figure.add_trace(
        go.Scatter(
            x=[timestamps.iloc[first_index]],
            y=[float(view["low"].iloc[first_index])],
            mode="markers",
            name="Large bearish candle",
            marker={
                "symbol": "diamond",
                "size": 12,
                "color": chart_theme.BEAR,
                "line": {"width": 1.5, "color": "#FECACA"},
            },
            hovertemplate=(
                f"Large bearish · body {signal.bearish_body_size:.2f}"
                "<extra></extra>"
            ),
        ),
        row=1,
        col=1,
    )

    touch_y = signal.vwap if signal.is_exact_touch else signal.second_close
    figure.add_trace(
        go.Scatter(
            x=[timestamps.iloc[second_index]],
            y=[touch_y],
            mode="markers+text",
            name=f"VWAP {signal.touch_type.lower()}",
            marker={
                "symbol": "star" if signal.is_exact_touch else "circle-open",
                "size": 16 if signal.is_exact_touch else 13,
                "color": chart_theme.AMBER,
                "line": {"width": 2, "color": chart_theme.AMBER},
            },
            text=["VWAP touch" if signal.is_exact_touch else "near VWAP"],
            textposition="top center",
            textfont={"size": 10, "color": chart_theme.AMBER},
            hovertemplate="VWAP %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    pair_high = float(
        view["high"].iloc[first_index : second_index + 1].max()
    )
    figure.add_annotation(
        x=timestamps.iloc[second_index],
        y=pair_high,
        text=f"{signal.candle_type} · {signal.touch_type}",
        showarrow=True,
        arrowhead=2,
        arrowcolor=chart_theme.AMBER,
        font={"color": "#FCD34D", "size": 11},
        bgcolor="rgba(11, 16, 32, 0.92)",
        bordercolor="rgba(245, 158, 11, 0.45)",
        ax=0,
        ay=-34,
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
            f"{signal.signal_type} · VWAP {signal.vwap:,.2f}"
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
