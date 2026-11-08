"""Centralized design system for the FYERS futures terminal.

All colors, spacing and component styles live here so pages only compose
reusable primitives instead of carrying their own CSS.
"""

from __future__ import annotations

import html
from typing import Any, Iterable, Mapping, Sequence

import streamlit as st

_CSS = """
<style>
:root {
  --bg: #0B1020;
  --sidebar: #101827;
  --card: #151F30;
  --secondary: #1D293B;
  --border: #28364B;
  --border-soft: rgba(148, 163, 184, 0.14);
  --accent: #6366F1;
  --accent-soft: rgba(99, 102, 241, 0.14);
  --accent-line: rgba(99, 102, 241, 0.45);
  --accent-text: #C7D2FE;
  --bull: #22C55E;
  --bull-soft: rgba(34, 197, 94, 0.12);
  --bear: #EF4444;
  --bear-soft: rgba(239, 68, 68, 0.12);
  --warn: #F59E0B;
  --warn-soft: rgba(245, 158, 11, 0.12);
  --vwap: #38BDF8;
  --vwap-soft: rgba(56, 189, 248, 0.12);
  --avwap: #F87171;
  --avwap-soft: rgba(248, 113, 113, 0.12);
  --text: #F1F5F9;
  --muted: #94A3B8;
  --muted-2: #64748B;
  --radius: 12px;
  --radius-sm: 8px;
  --shadow: 0 1px 0 rgba(255, 255, 255, 0.02), 0 10px 26px rgba(0, 0, 0, 0.18);
  --font: Inter, "Segoe UI", "SF Pro Text", -apple-system, BlinkMacSystemFont, sans-serif;
}

html, body, .stApp, [class*="st-"] {
  font-family: var(--font);
}

[data-testid="stIconMaterial"] {
  font-family: "Material Symbols Rounded" !important;
  font-weight: normal;
  font-style: normal;
  line-height: 1;
}

.stApp {
  background:
    radial-gradient(900px 420px at 18% -8%, rgba(99, 102, 241, 0.10), transparent 60%),
    radial-gradient(820px 380px at 88% -4%, rgba(56, 189, 248, 0.06), transparent 55%),
    var(--bg);
  color: var(--text);
}

#MainMenu, footer, [data-testid="stAppDeployButton"], [data-testid="stStatusWidget"] {
  visibility: hidden;
}

header[data-testid="stHeader"] {
  background: transparent;
}

[data-testid="stDecoration"] {
  display: none;
}

.block-container {
  max-width: 1720px;
  padding: 1.35rem 2.2rem 3rem;
}

section[data-testid="stSidebar"] {
  background: var(--sidebar);
  border-right: 1px solid var(--border);
  width: 280px !important;
  min-width: 280px !important;
}

section[data-testid="stSidebar"] > div {
  width: 280px !important;
}

[data-testid="stSidebarUserContent"] {
  padding: 0.85rem 0.9rem 1.25rem;
}

[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
  gap: 0.35rem;
}

::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: #2A3850;
  border-radius: 10px;
  border: 2px solid var(--bg);
}

/* ---------- brand + navigation ---------- */

.term-brand {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  padding: 0.2rem 0.2rem 0.85rem;
}

.term-brand-mark {
  width: 2.25rem;
  height: 2.25rem;
  border-radius: 10px;
  background: linear-gradient(145deg, #6366F1 0%, #4F46E5 100%);
  display: grid;
  place-items: center;
  color: #FFFFFF;
  box-shadow: 0 8px 20px rgba(99, 102, 241, 0.30);
  flex: 0 0 auto;
}

.term-brand-title {
  font-size: 1rem;
  font-weight: 750;
  letter-spacing: -0.01em;
  color: var(--text);
  line-height: 1.15;
}

.term-brand-sub {
  font-size: 0.66rem;
  color: var(--muted);
  letter-spacing: 0.13em;
  text-transform: uppercase;
  margin-top: 0.15rem;
}

.term-nav-label {
  color: var(--muted-2);
  font-size: 0.63rem;
  font-weight: 750;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  padding: 0.55rem 0.35rem 0.3rem;
}

[data-testid="stSidebar"] .stButton > button {
  width: 100%;
  justify-content: flex-start;
  text-align: left;
  background: transparent;
  border: 1px solid transparent;
  color: var(--muted);
  padding: 0.5rem 0.62rem;
  min-height: 2.25rem;
  border-radius: var(--radius-sm);
  font-weight: 600;
  font-size: 0.86rem;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

[data-testid="stSidebar"] .stButton > button:hover {
  background: rgba(148, 163, 184, 0.08);
  color: var(--text);
  border-color: var(--border);
}

[data-testid="stSidebar"] .stButton > button[kind="primary"] {
  background: var(--accent-soft);
  color: var(--accent-text);
  border-color: var(--accent-line);
  box-shadow: inset 3px 0 0 var(--accent);
}

[data-testid="stSidebar"] .stButton > button p {
  font-weight: 600;
}

[data-testid="stSidebar"] .stButton > button [data-testid="stIconMaterial"] {
  color: inherit;
}

/* ---------- page header ---------- */

.term-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
  margin: 0.1rem 0 1.1rem;
}

.term-eyebrow {
  color: var(--accent-text);
  font-size: 0.68rem;
  font-weight: 750;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  margin-bottom: 0.35rem;
}

.term-title {
  color: var(--text);
  font-size: 1.85rem;
  font-weight: 750;
  letter-spacing: -0.02em;
  line-height: 1.12;
  margin: 0;
}

.term-subtitle {
  color: var(--muted);
  font-size: 0.9rem;
  line-height: 1.5;
  margin-top: 0.4rem;
  max-width: 820px;
}

.term-head-chips {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
  padding-bottom: 0.2rem;
}

/* ---------- sections ---------- */

.term-section {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 0.8rem;
  flex-wrap: wrap;
  margin: 1.05rem 0 0.6rem;
}

.term-section-title {
  color: var(--text);
  font-size: 0.86rem;
  font-weight: 750;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.term-section-sub {
  color: var(--muted);
  font-size: 0.78rem;
  margin-top: 0.2rem;
}

.term-section-right {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}

/* ---------- panels ---------- */

.term-panel {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem 1.05rem;
  box-shadow: var(--shadow);
}

.st-key-vwap_toolbar,
.st-key-avwap_toolbar,
.st-key-history_filters,
.st-key-settings_panel,
.st-key-quick_avwap,
.st-key-quick_vwap,
.st-key-quick_history {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem 1.05rem;
  box-shadow: var(--shadow);
}

.st-key-vwap_chart_panel,
.st-key-avwap_chart_panel {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.55rem 0.55rem 0.15rem;
  box-shadow: var(--shadow);
}

.st-key-quick_avwap,
.st-key-quick_vwap,
.st-key-quick_history {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 118px;
}

.st-key-quick_avwap [data-testid="stVerticalBlock"],
.st-key-quick_vwap [data-testid="stVerticalBlock"],
.st-key-quick_history [data-testid="stVerticalBlock"] {
  gap: 0.45rem;
}

/* ---------- metric cards ---------- */

.term-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(158px, 1fr));
  gap: 0.7rem;
  margin: 0.15rem 0 1rem;
}

.term-metric {
  position: relative;
  overflow: hidden;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.018), transparent 70%), var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.75rem 0.85rem 0.7rem;
  min-height: 84px;
}

.term-metric::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--tone, var(--border));
}

.term-metric .label {
  color: var(--muted);
  font-size: 0.66rem;
  letter-spacing: 0.10em;
  text-transform: uppercase;
  font-weight: 750;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.term-metric .value {
  color: var(--text);
  font-size: 1.52rem;
  font-weight: 750;
  line-height: 1.18;
  margin-top: 0.3rem;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
}

.term-metric .hint {
  color: var(--muted);
  font-size: 0.71rem;
  margin-top: 0.2rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.term-metric.accent { --tone: var(--accent); }
.term-metric.bull { --tone: var(--bull); }
.term-metric.bear { --tone: var(--bear); }
.term-metric.warn { --tone: var(--warn); }
.term-metric.vwap { --tone: var(--vwap); }
.term-metric.avwap { --tone: var(--avwap); }
.term-metric.neutral { --tone: var(--border); }

.term-metric.accent .value { color: var(--accent-text); }
.term-metric.bull .value { color: #86EFAC; }
.term-metric.bear .value { color: #FCA5A5; }
.term-metric.warn .value { color: #FCD34D; }
.term-metric.vwap .value { color: #7DD3FC; }
.term-metric.avwap .value { color: #FCA5A5; }

.term-card {
  background: linear-gradient(160deg, var(--card) 0%, #18243A 100%);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.95rem 1rem;
  height: 100%;
  box-shadow: var(--shadow);
}

.term-card-label {
  color: var(--muted);
  font-size: 0.66rem;
  letter-spacing: 0.10em;
  text-transform: uppercase;
  font-weight: 750;
}

.term-card-value {
  color: var(--text);
  font-size: 1.6rem;
  font-weight: 750;
  line-height: 1.2;
  margin-top: 0.35rem;
  font-variant-numeric: tabular-nums;
}

.term-card-hint {
  color: var(--muted);
  font-size: 0.74rem;
  margin-top: 0.3rem;
}

/* ---------- key/value grid ---------- */

.term-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
  gap: 0.55rem;
}

.term-kv {
  background: rgba(29, 41, 59, 0.55);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.55rem 0.65rem;
  min-width: 0;
}

.term-kv .k {
  color: var(--muted);
  font-size: 0.63rem;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  font-weight: 750;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.term-kv .v {
  color: var(--text);
  font-size: 0.88rem;
  font-weight: 650;
  margin-top: 0.2rem;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.term-kv.bull .v { color: #86EFAC; }
.term-kv.bear .v { color: #FCA5A5; }
.term-kv.accent .v { color: var(--accent-text); }
.term-kv.vwap .v { color: #7DD3FC; }
.term-kv.avwap .v { color: #FCA5A5; }
.term-kv.warn .v { color: #FCD34D; }

/* ---------- pills + badges ---------- */

.term-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.03em;
  border: 1px solid var(--border);
  background: var(--secondary);
  color: var(--muted);
  white-space: nowrap;
}

.term-pill.ok {
  color: #86EFAC;
  background: var(--bull-soft);
  border-color: rgba(34, 197, 94, 0.35);
}

.term-pill.warn {
  color: #FCD34D;
  background: var(--warn-soft);
  border-color: rgba(245, 158, 11, 0.35);
}

.term-pill.bad {
  color: #FCA5A5;
  background: var(--bear-soft);
  border-color: rgba(239, 68, 68, 0.35);
}

.term-pill.accent {
  color: var(--accent-text);
  background: var(--accent-soft);
  border-color: var(--accent-line);
}

.term-pill.vwap {
  color: #7DD3FC;
  background: var(--vwap-soft);
  border-color: rgba(56, 189, 248, 0.35);
}

.term-pill.avwap {
  color: #FCA5A5;
  background: var(--avwap-soft);
  border-color: rgba(248, 113, 113, 0.35);
}

.term-pill.neutral {
  color: #B7C4D6;
  background: rgba(148, 163, 184, 0.10);
  border-color: rgba(148, 163, 184, 0.24);
}

/* ---------- connection card ---------- */

.term-connection {
  background: var(--secondary);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.7rem 0.75rem;
}

.term-connection-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.term-connection-title {
  color: var(--text);
  font-size: 0.82rem;
  font-weight: 700;
}

.term-connection-meta {
  color: var(--muted);
  font-size: 0.72rem;
  margin-top: 0.3rem;
  line-height: 1.45;
  word-break: break-word;
}

.term-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--muted-2);
  box-shadow: 0 0 0 3px rgba(148, 163, 184, 0.12);
  flex: 0 0 auto;
}

.term-dot.ok {
  background: var(--bull);
  box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.16);
}

.term-dot.warn {
  background: var(--warn);
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.16);
}

.term-dot.bad {
  background: var(--bear);
  box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.16);
}

.st-key-sidebar_connection {
  position: sticky;
  bottom: 0;
  background: var(--sidebar);
  padding-top: 0.6rem;
  border-top: 1px solid var(--border);
}

/* ---------- empty state ---------- */

.term-empty {
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  background: rgba(21, 31, 48, 0.55);
  padding: 1.5rem 1.4rem;
  text-align: center;
}

.term-empty-mark {
  width: 2.4rem;
  height: 2.4rem;
  margin: 0 auto 0.65rem;
  border-radius: 10px;
  background: var(--accent-soft);
  border: 1px solid var(--accent-line);
  display: grid;
  place-items: center;
  color: var(--accent-text);
}

.term-empty-title {
  color: var(--text);
  font-size: 0.95rem;
  font-weight: 700;
}

.term-empty-body {
  color: var(--muted);
  font-size: 0.82rem;
  line-height: 1.55;
  max-width: 620px;
  margin: 0.35rem auto 0;
}

.term-empty-hint {
  color: var(--muted-2);
  font-size: 0.74rem;
  margin-top: 0.55rem;
}

/* ---------- widgets ---------- */

.stButton > button,
.stDownloadButton > button,
.stLinkButton > a {
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--secondary);
  color: var(--text);
  font-weight: 600;
  font-size: 0.84rem;
  min-height: 2.3rem;
  transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}

.stButton > button:hover,
.stDownloadButton > button:hover,
.stLinkButton > a:hover {
  border-color: #3B4A63;
  background: #22304A;
  color: #FFFFFF;
}

.stButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"],
.stLinkButton > a[kind="primary"] {
  background: var(--accent);
  border-color: var(--accent);
  color: #FFFFFF;
  box-shadow: 0 6px 16px rgba(99, 102, 241, 0.22);
}

.stButton > button[kind="primary"]:hover,
.stDownloadButton > button[kind="primary"]:hover,
.stLinkButton > a[kind="primary"]:hover {
  background: #4F46E5;
  border-color: #4F46E5;
}

.stButton > button:disabled,
.stDownloadButton > button:disabled {
  opacity: 0.45;
}

[data-baseweb="input"],
[data-baseweb="select"] > div,
[data-baseweb="base-input"],
[data-baseweb="textarea"] {
  background: var(--secondary) !important;
  border-color: var(--border) !important;
  border-radius: var(--radius-sm) !important;
}

[data-baseweb="input"] input,
[data-baseweb="base-input"] input,
textarea {
  color: var(--text) !important;
}

[data-baseweb="select"] div {
  color: var(--text);
}

[data-testid="stWidgetLabel"] p {
  color: var(--muted);
  font-size: 0.75rem;
  font-weight: 650;
  letter-spacing: 0.01em;
}

[data-testid="stWidgetLabel"] {
  margin-bottom: 0.15rem;
}

[data-testid="stSegmentedControl"] {
  background: var(--secondary);
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 3px;
}

[data-testid="stSegmentedControl"] button {
  border-radius: 7px;
  border: none;
  background: transparent;
  color: var(--muted);
  font-weight: 650;
  font-size: 0.78rem;
}

[data-testid="stSegmentedControl"] button[aria-checked="true"],
[data-testid="stSegmentedControl"] button[aria-pressed="true"] {
  background: var(--accent);
  color: #FFFFFF;
}

[data-testid="stPills"] button {
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--secondary);
  color: var(--muted);
  font-weight: 650;
  font-size: 0.76rem;
}

[data-testid="stPills"] button[aria-checked="true"],
[data-testid="stPills"] button[aria-pressed="true"] {
  background: var(--accent);
  border-color: var(--accent);
  color: #FFFFFF;
}

div[data-testid="stExpander"] {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: rgba(21, 31, 48, 0.55);
  overflow: hidden;
}

div[data-testid="stExpander"] summary {
  color: var(--text);
  font-weight: 650;
  font-size: 0.84rem;
}

div[data-testid="stExpander"] summary:hover {
  color: #FFFFFF;
}

[data-testid="stDataFrame"] {
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  background: var(--card);
}

[data-testid="stDataFrame"] [role="columnheader"] {
  background: var(--secondary) !important;
  color: var(--muted) !important;
  font-weight: 750 !important;
  text-transform: uppercase;
  font-size: 0.66rem !important;
  letter-spacing: 0.06em;
}

[data-testid="stPopover"] > button {
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--secondary);
  color: var(--text);
  font-weight: 600;
}

div[data-testid="stPopoverBody"] {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
}

[data-testid="stAlert"] {
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--card);
}

[data-testid="stProgress"] > div > div > div {
  background: var(--accent);
}

[data-testid="stCaptionContainer"],
.stCaption {
  color: var(--muted);
}

[data-testid="stMarkdownContainer"] code {
  background: var(--secondary);
  color: #C7D2FE;
  border-radius: 5px;
  padding: 0.1rem 0.3rem;
}

hr {
  border-color: var(--border);
}

/* ---------- responsive ---------- */

@media (max-width: 1400px) {
  .block-container {
    padding: 1.05rem 1.25rem 2.5rem;
  }
  .term-title {
    font-size: 1.65rem;
  }
  .term-metrics {
    grid-template-columns: repeat(auto-fit, minmax(145px, 1fr));
  }
}

@media (max-width: 980px) {
  .term-head {
    align-items: flex-start;
  }
  .term-head-chips {
    padding-bottom: 0;
  }
  .term-title {
    font-size: 1.45rem;
  }
  .term-metrics {
    grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
  }
}
</style>
"""


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _pills_html(pills: Iterable[Sequence[str] | Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for pill in pills:
        if isinstance(pill, Mapping):
            label = pill.get("label", "")
            tone = pill.get("tone", "neutral")
        else:
            values = list(pill)
            label = values[0] if values else ""
            tone = values[1] if len(values) > 1 else "neutral"
        parts.append(f'<span class="term-pill {_esc(tone)}">{_esc(label)}</span>')
    return "".join(parts)


def apply_theme() -> None:
    """Inject the terminal stylesheet once per rerun."""

    st.markdown(_CSS, unsafe_allow_html=True)


def render_brand(title: str = "Futures Terminal", subtitle: str = "FYERS · NSE F&O") -> None:
    st.markdown(
        f"""
        <div class="term-brand">
          <div class="term-brand-mark">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none"
                 stroke="currentColor" stroke-width="2.1" stroke-linecap="round">
              <path d="M4 19V10M9.3 19V5M14.7 19v-6M20 19V3"/>
            </svg>
          </div>
          <div>
            <div class="term-brand-title">{_esc(title)}</div>
            <div class="term-brand-sub">{_esc(subtitle)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_page_header(
    title: str,
    subtitle: str = "",
    eyebrow: str = "",
    chips: Iterable[Sequence[str] | Mapping[str, Any]] | None = None,
) -> None:
    eyebrow_html = f'<div class="term-eyebrow">{_esc(eyebrow)}</div>' if eyebrow else ""
    chips_html = (
        f'<div class="term-head-chips">{_pills_html(chips)}</div>' if chips else ""
    )
    st.markdown(
        f"""
        <div class="term-head">
          <div>
            {eyebrow_html}
            <div class="term-title">{_esc(title)}</div>
            <div class="term-subtitle">{_esc(subtitle)}</div>
          </div>
          {chips_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_header(
    title: str,
    subtitle: str = "",
    pills: Iterable[Sequence[str] | Mapping[str, Any]] | None = None,
) -> None:
    sub_html = f'<div class="term-section-sub">{_esc(subtitle)}</div>' if subtitle else ""
    right_html = (
        f'<div class="term-section-right">{_pills_html(pills)}</div>' if pills else ""
    )
    st.markdown(
        f"""
        <div class="term-section">
          <div>
            <div class="term-section-title">{_esc(title)}</div>
            {sub_html}
          </div>
          {right_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metric_cards(
    items: Iterable[Mapping[str, Any]],
    columns: int | None = None,
) -> None:
    cards: list[str] = []
    for item in items:
        tone = str(item.get("tone", "neutral"))
        hint = item.get("hint")
        hint_html = f'<div class="hint">{_esc(hint)}</div>' if hint else ""
        cards.append(
            f'<div class="term-metric {_esc(tone)}">'
            f'<div class="label">{_esc(item.get("label", ""))}</div>'
            f'<div class="value">{_esc(item.get("value", "—"))}</div>'
            f"{hint_html}"
            f"</div>"
        )
    style = ""
    if columns:
        style = f' style="grid-template-columns: repeat({int(columns)}, minmax(0, 1fr));"'
    st.markdown(
        f'<div class="term-metrics"{style}>{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )


def render_info_grid(items: Iterable[Mapping[str, Any]]) -> None:
    cells: list[str] = []
    for item in items:
        tone = str(item.get("tone", "neutral"))
        cells.append(
            f'<div class="term-kv {_esc(tone)}">'
            f'<div class="k">{_esc(item.get("label", ""))}</div>'
            f'<div class="v">{_esc(item.get("value", "—"))}</div>'
            f"</div>"
        )
    st.markdown(f'<div class="term-grid">{"".join(cells)}</div>', unsafe_allow_html=True)


def render_card(label: str, value: str, hint: str = "", tone: str = "neutral") -> None:
    hint_html = f'<div class="term-card-hint">{_esc(hint)}</div>' if hint else ""
    st.markdown(
        f'<div class="term-card term-metric {_esc(tone)}" '
        f'style="border-left: 3px solid var(--tone);">'
        f'<div class="term-card-label">{_esc(label)}</div>'
        f'<div class="term-card-value">{_esc(value)}</div>'
        f"{hint_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_pill(label: str, tone: str = "neutral") -> None:
    st.markdown(
        f'<span class="term-pill {_esc(tone)}">{_esc(label)}</span>',
        unsafe_allow_html=True,
    )


def render_empty_state(title: str, body: str = "", hint: str = "") -> None:
    body_html = f'<div class="term-empty-body">{_esc(body)}</div>' if body else ""
    hint_html = f'<div class="term-empty-hint">{_esc(hint)}</div>' if hint else ""
    st.markdown(
        f'<div class="term-empty">'
        f'<div class="term-empty-mark">'
        f'<svg viewBox="0 0 24 24" width="18" height="18" fill="none" '
        f'stroke="currentColor" stroke-width="2" stroke-linecap="round">'
        f'<path d="M4 19V10M9.3 19V5M14.7 19v-6M20 19V3"/>'
        f"</svg>"
        f"</div>"
        f'<div class="term-empty-title">{_esc(title)}</div>'
        f"{body_html}"
        f"{hint_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_connection_card(
    authenticated: bool,
    token_present: bool,
    account: str = "",
    token_age_hours: float | None = None,
    message: str = "",
    credentials_configured: bool = True,
) -> None:
    if not credentials_configured:
        tone, label = "bad", "Not configured"
    elif authenticated:
        tone, label = "ok", "Connected"
    elif token_present:
        tone, label = "warn", "Token expired"
    else:
        tone, label = "bad", "Not connected"

    meta: list[str] = []
    if account:
        meta.append(f"Account: {_esc(account)}")
    if token_age_hours is not None:
        if token_age_hours < 1:
            age = f"{max(token_age_hours * 60.0, 0.0):.0f} min"
        else:
            age = f"{token_age_hours:.1f} h"
        meta.append(f"Token age: {age}")
    if not meta:
        meta.append("Read-only market data")
    if message and not authenticated:
        meta.append(_esc(message))

    st.markdown(
        f"""
        <div class="term-connection">
          <div class="term-connection-row">
            <span class="term-dot {tone}"></span>
            <span class="term-connection-title">FYERS · {label}</span>
          </div>
          <div class="term-connection-meta">{"<br/>".join(meta)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
