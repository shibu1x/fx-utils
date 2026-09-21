# CLAUDE.md

FX utilities for multiple currency pairs: OHLC data collection, MT4/MT5 EA settings generation, and daily range analysis.

## Scripts

| Script | Description |
|--------|-------------|
| `fetch.py` | Fetch 1H OHLC data for FX pairs from Dukascopy and resample to daily bars using a New York 17:00 session close (matches MT4/MT5 broker convention), save to SQLite; optional positional `[PAIR ...]` (default: USD/JPY) |
| `grid_settings.py` | Generate MT4/MT5 EA grid `.set` files for multiple pairs from `fx_utils.db` |
| `daily_range_analysis.py` | Analyze daily high/low % change from open for all pairs/dates in `fx_utils.db`; joins in event labels from `data/input/events.tsv` (by `date`); outputs TSV to `data/output/daily_range_analysis.tsv` |
| `grid_basket_calc.py` | Calculate grid basket (Martingale) margin and unrealized P&L for USD/JPY, following the grid rules of BollingerBasket.mq5 (see the `mt5_ea` project); assumes price moved entry -> extreme -> price — requires `--entry` (level-0 price), `--extreme` (furthest price reached, determines opened levels), `--price` (current price to evaluate P&L at); optional `--direction` buy/sell/both, `--lot`, `--lot-multiplier`, `--grid-step-pips`, `--grid-step-multiplier`, `--max-levels`, `--leverage` (USD account) |
| `generate_sets.py` | Generate MT5 EA `.set` files by taking each `data/input/sets/<name1>/default.set` as the base template and overriding any keys present in that account's `data/input/sets/<name1>/<name2>.set`; processes every `<name1>` subdirectory and every other `.set` file within it automatically, outputs to `data/output/sets/<name2>/<name1>.set`; warns and ignores keys in the account file that don't exist in the template; overrides `SellEntryDistancePips` to `130`/`90` (for every account) when today or tomorrow has a `日銀`/`FOMC` event in `data/input/events.tsv` (the larger wins if both hit) |

## Environment Variables

### `grid_settings.py`

`PAIRS` uses `/`-separated pair names (e.g. `USD/JPY`); the pair-prefixed variables below strip the `/` (`{PAIR}` = `USDJPY`, `EURUSD`, `GBPUSD`, etc.) since env var names can't contain `/`.

| Variable | Default | Description |
|----------|---------|-------------|
| `PAIRS` | _(required)_ | Comma-separated list of pairs to process, e.g. `USD/JPY,EUR/USD,GBP/USD` |
| `{PAIR}_ACCOUNTS` | _(none)_ | `name:lot[:direction]` comma-separated; direction: `long`, `short`, `both` (default: `both`); controls output dirs |
| `{PAIR}_MAGIC_NUMBER` | `8001` | Magic number for the grid EA |
| `{PAIR}_GRID_STEP_PIPS` | `5` | Grid step size in pips |
| `{PAIR}_GRID_CENTER_ADJUSTMENT` | `0` | % adjustment to previous close for center price |
| `{PAIR}_GRID_RANGE` | `1` | Range % of previous close for buy/sell pips |
| `{PAIR}_GRID_CENTER_MAX` | _(none)_ | Hard cap on center price |
| `{PAIR}_GRID_CENTER_MIN` | _(none)_ | Hard floor on center price |

## Directory Structure

```
data/
  db/fx_utils.db                      # Multi-pair daily OHLC data (via fetch.py)
  input/events.tsv                     # Event labels by date (date, event), joined in by daily_range_analysis.py
  input/sets/<name1>/default.set      # Base .set template (all keys/defaults) used by generate_sets.py
  input/sets/<name1>/<name2>.set      # Per-account overrides; used by generate_sets.py (any key in default.set)
  output/sets/<account>/              # .set files from grid_settings.py (dirs from account names)
  output/sets/<name2>/<name1>.set     # .set files from generate_sets.py
  output/daily_range_analysis.tsv     # Daily range analysis output
```

## Database Schema

### `fx_utils.db`

**price_history** — daily OHLC data for multiple pairs, PK: `(pair, date)`
- `pair` TEXT — e.g. `USD/JPY`, `EUR/USD`, `GBP/USD`
- `date` TEXT — YYYY-MM-DD
- `open` REAL
- `high` REAL
- `low` REAL
- `close` REAL
