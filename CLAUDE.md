# CLAUDE.md

FX utilities for multiple currency pairs: OHLC data collection, MT4/MT5 EA settings generation, and daily range analysis.

## Scripts

| Script | Description |
|--------|-------------|
| `fetch.py` | Fetch 1H OHLC data for FX pairs from Dukascopy and resample to daily bars using a New York 17:00 session close (matches MT4/MT5 broker convention), save to SQLite; optional positional `[PAIR ...]` (default: USD/JPY) |
| `daily_range_analysis.py` | Analyze daily high/low % change from open for all pairs/dates in `fx_utils.db`; joins in event labels from `data/input/events.tsv` (by `date`); outputs TSV to `data/output/daily_range_analysis.tsv` |
| `grid_basket_calc.py` | Calculate grid basket (Martingale) margin and unrealized P&L for USD/JPY, following the grid rules of BollingerBasket.mq5 (see the `mt5_ea` project); assumes price moved entry -> extreme -> price — requires `--entry` (level-0 price), `--extreme` (furthest price reached, determines opened levels), `--price` (current price to evaluate P&L at); optional `--direction` buy/sell/both, `--lot`, `--lot-multiplier`, `--grid-step-pips`, `--grid-step-multiplier`, `--max-levels`, `--leverage` (USD account) |
| `generate_sets.py` | Generate MT5 EA `.set` files (UTF-8) by taking each `data/input/sets/<name1>/default.set` as the base template and overriding any keys present in that account's `data/input/sets/<name1>/<name2>.set`; processes every `<name1>` subdirectory and every other `.set` file within it automatically, outputs to `data/output/sets/<name2>/<name1>.set`; warns and ignores keys in the account file that don't exist in the template; for `<name1> == feed` only, overrides `SellEntryDistancePips`/`BuyEntryDistancePips` for every account based on, in priority order: (1) a `日銀`/`FOMC` event in `data/input/events.tsv` today (`SellEntryDistancePips` to `130`/`90`, the larger wins if both hit), (2) USD/JPY: last 15 confirmed daily closes swung down >= 250 pips (high before low) and the latest close hasn't yet retraced back above the 50% Fibonacci level (`SellEntryDistancePips`=`170`), (3) USD/JPY daily close above the 20-day Bollinger +2σ band in `fx_utils.db` (`SellEntryDistancePips`=`0`, `BuyEntryDistancePips`=`70`), (4) the same event check for tomorrow, (5) USD/JPY last confirmed daily candle bearish (close < open) (`SellEntryDistancePips`=`130`); for `<name1> == breakout` only, overrides `SellOpenNew`=`false` when USD/JPY's last confirmed daily candle's high - close >= 100 pips (independent of `feed`'s priority chain); other `<name1>` directories are written as-is with no override; whenever the resulting `feed/<name2>.set` file content differs from what was previously written to that output path, posts the judgment (pips + reason, or "No override" if none of the conditions matched) to Discord via `DISCORD_WEBHOOK_URL` if set; `--history` flag skips `.set` generation and instead writes this same day-by-day judgment (date, chosen pips, reason) for every USD/JPY date in `fx_utils.db` to `data/output/generate_sets_history.tsv` (no Discord notification in this mode) |

## Environment Variables

### `generate_sets.py`

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_WEBHOOK_URL` | _(none)_ | Discord webhook URL; when set, notifies it with the `SellEntryDistancePips`/`BuyEntryDistancePips` judgment whenever one of the override conditions matches (not used with `--history`) |

## Directory Structure

```
data/
  db/fx_utils.db                      # Multi-pair daily OHLC data (via fetch.py)
  input/events.tsv                     # Event labels by date (date, event), joined in by daily_range_analysis.py
  input/sets/<name1>/default.set      # Base .set template (all keys/defaults) used by generate_sets.py
  input/sets/<name1>/<name2>.set      # Per-account overrides; used by generate_sets.py (any key in default.set)
  output/sets/<name2>/<name1>.set     # .set files from generate_sets.py
  output/generate_sets_history.tsv    # Daily entry-distance judgment history from generate_sets.py --history
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
