"""Shared Plotly palette and configuration for the terminal charts."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go

BG = "#0B1020"
PANEL = "#151F30"
BORDER = "#28364B"
TEXT = "#F1F5F9"
MUTED = "#94A3B8"
ACCENT = "#6366F1"
BULL = "#22C55E"
BEAR = "#EF4444"
VWAP_BLUE = "#38BDF8"
AVWAP_RED = "#F87171"
AMBER = "#F59E0B"
GRID = "rgba(148, 163, 184, 0.10)"
AXIS_LINE = "rgba(148, 163, 184, 0.22)"

FONT_FAMILY = (
    'Inter, "Segoe UI", "SF Pro Text", -apple-system, BlinkMacSystemFont, sans-serif'
)


def chart_config(filename: str = "chart.png") -> dict[str, Any]:
    """Modebar configuration shared by every scanner chart."""

    return {
        "displaylogo": False,
        "responsive": True,
        "scrollZoom": True,
        "modeBarButtonsToRemove": [
            "lasso2d",
            "select2d",
            "autoScale2d",
            "toggleSpikelines",
        ],
        "toImageButtonOptions": {
            "format": "png",
            "filename": filename,
            "height": 900,
            "width": 1600,
            "scale": 2,
        },
    }


def style_figure(figure: go.Figure, height: int = 620) -> go.Figure:
    """Apply the terminal look to a two-row price/volume figure."""

    figure.update_layout(
        template=None,
        height=height,
        paper_bgcolor=PANEL,
        plot_bgcolor=PANEL,
        font={"family": FONT_FAMILY, "color": TEXT, "size": 12},
        margin={"l": 10, "r": 24, "t": 54, "b": 14},
        hovermode="x unified",
        hoverlabel={
            "bgcolor": "#0F172A",
            "bordercolor": BORDER,
            "font": {"color": TEXT, "size": 11},
        },
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1,
            "font": {"size": 11, "color": MUTED},
            "bgcolor": "rgba(0,0,0,0)",
        },
        dragmode="pan",
        bargap=0.18,
    )
    figure.update_xaxes(
        rangeslider_visible=False,
        showgrid=True,
        gridcolor=GRID,
        zeroline=False,
        showline=False,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="rgba(148, 163, 184, 0.45)",
        spikethickness=1,
        tickfont={"color": MUTED, "size": 10},
    )
    figure.update_yaxes(
        showgrid=True,
        gridcolor=GRID,
        zeroline=False,
        showline=False,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="rgba(148, 163, 184, 0.40)",
        spikethickness=1,
        tickfont={"color": MUTED, "size": 10},
    )
    return figure


def price_annotation(figure: go.Figure, text: str, y: float = 1.055) -> None:
    """Small symbol/context annotation above the price panel."""

    figure.add_annotation(
        xref="paper",
        yref="paper",
        x=0.0,
        y=y,
        text=text,
        showarrow=False,
        font={"color": TEXT, "size": 12},
        align="left",
    )
