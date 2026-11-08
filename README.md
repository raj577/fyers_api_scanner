# FYERS Futures Scanners (standalone Streamlit app)

Self-contained Streamlit application with two independent NSE futures scanners:

- **AVWAP Scanner** — anchored VWAP reclaim/bounce patterns (unchanged logic).
- **VWAP Reclaim Scanner** — multi-timeframe session VWAP reclaim/bounce.

It also ships a dark trading-terminal UI, a local scan history, and a settings
page. Everything the app needs lives in this folder (`app.py`, `dashboard/`,
`config.py`, `login.py`, `data/`, `scanner/`, `indicators/`), so the folder can
be copied elsewhere and run on its own.

## Run

### Windows (PowerShell)

```powershell
cd streamlit_app
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# edit .env with your FYERS_CLIENT_ID and FYERS_SECRET_KEY
.\run.ps1
```

`run.ps1` uses `.venv\Scripts\python.exe` when it exists, so the correct
Streamlit version (1.50+) is picked up automatically.

### macOS / Linux

```bash
cd streamlit_app
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with your FYERS_CLIENT_ID and FYERS_SECRET_KEY
./run.sh
```

The app opens at http://localhost:8501.

> Streamlit 1.50 or newer is required (`width="stretch"` layout). Older global
> installs such as 1.32 will fail.

## Pages

| Page | Purpose |
| --- | --- |
| **Dashboard** | Session status, universe counts, shortcuts and recent history. |
| **AVWAP Patterns** | Contract-start AVWAP scan, presets, selectable table and charts. |
| **VWAP Reclaim** | Multi-timeframe session-VWAP scan with filters and charts. |
| **Scan History** | Local history for both scanners (newest first), filters and CSV export. |
| **Settings** | Runtime config, cache/history controls, presets and FYERS connection. |

Navigation uses icon buttons in the sidebar. Each page keeps scanner controls in
the main panel, with a compact FYERS connection card at the bottom of the
sidebar. The shared terminal styling and Plotly theme live in `dashboard/theme.py`
and `dashboard/chart_theme.py`.

## VWAP Reclaim methodology

For every instrument, session VWAP is recomputed from the 09:15 IST open on
each timeframe.

A signal fires on the latest **completed** candle pair `(previous, current)`
when either:

- **Reclaim** — `previous_close < previous_vwap` (or
  `previous_low < previous_vwap`) and `current_close > current_vwap`.
- **Bounce** — `previous_low < previous_vwap`, `previous_close >
  previous_vwap`, and `current_close > current_vwap`. Bounce takes precedence
  when both patterns match.

Optional filters: distance above VWAP (min/max), how far the previous close was
below VWAP, whether the current candle low must trade below VWAP before closing
above it, volume ratio versus a lookback average, minimum turnover, and allowed
signal types.

Timeframes: `5m, 15m, 30m, 45m, 1h, 2h, 4h, 1d` (default `15m`). Resampling is
session-anchored: buckets start at 09:15 and any bucket ending after 15:30 is
discarded. Daily candles are one row per session, so daily session VWAP is
close to the daily typical price; today's forming daily candle is excluded.

## Tests

Synthetic-data unit tests, no live FYERS calls:

```powershell
pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

## Configuration

All settings come from `.env` in this folder (or from environment variables).
Copy `.env.example` to `.env` and fill in:

- `FYERS_CLIENT_ID`
- `FYERS_SECRET_KEY`
- `FYERS_REDIRECT_URI` (must match the URI registered in the FYERS API
  dashboard; default `http://localhost:8501`)

Optional tuning variables are documented in `.env.example`.

## Runtime files

Created inside this folder on first use and gitignored:

- `access_token.txt`, `token_meta.json` — FYERS OAuth token
- `cache/` — instrument master, candle cache, `scan_history.json`
- `logs/` — rotating application log
- `presets/scanner_presets.json` — saved AVWAP presets

## Files

```
streamlit_app/
  app.py                       Streamlit entry point and page router
  config.py                    Settings, paths, logging
  login.py                     FYERS OAuth helpers and token store
  dashboard/                   Theme, charts, AVWAP/VWAP pages
  data/                        FYERS client, history cache, instrument master
  scanner/                     AVWAP pipeline and scanner/vwap_reclaim package
  indicators/                  AVWAP, session VWAP, ATR
  tests/                       Pytest unit tests
  .streamlit/config.toml       Theme and server settings
  .env.example                 Environment template
  requirements.txt             Runtime dependencies
  requirements-dev.txt         Test dependencies
  run.ps1 / run.sh             Convenience launchers
  README.md
```

## Limitations

- FYERS history calls are rate-limited; large universes take time and the
  engine reports per-instrument errors instead of aborting the whole scan.
- Signals use the latest completed candle pair only; intra-candle signals are
  not reported.
- Scan history is a local JSON file (capped at 200 entries), not a database.
- The app is a research tool, not an order router; it never places trades.
