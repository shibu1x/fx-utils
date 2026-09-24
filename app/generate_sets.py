#!/usr/bin/env python3
"""Generate MT5 EA .set files per account from data/input/sets/<name1>/default.set, overridden by data/input/sets/<name1>/<name2>.set."""

import argparse
import csv
import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import date, timedelta


DEFAULT_SET_FILENAME = "default.set"
ACCOUNTS_DIR = "/data/input/sets"
EVENTS_PATH = "/data/input/events.tsv"
DB_PATH = "/data/db/fx_utils.db"
OUTPUT_DIR = "/data/output/sets"
HISTORY_OUTPUT_PATH = "/data/output/generate_sets_history.tsv"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
EVENT_SELL_ENTRY_DISTANCE_PIPS = {"日銀": 130, "FOMC": 90}
BOLLINGER_PAIR = "USD/JPY"
BOLLINGER_PERIOD = 20
BOLLINGER_SIGMA = 2
FIB_LOOKBACK = 15
FIB_MIN_RANGE_PIPS = 250
FIB_RETRACEMENT = 0.5
FIB_SELL_ENTRY_DISTANCE_PIPS = 170
BEARISH_CANDLE_SELL_ENTRY_DISTANCE_PIPS = 130


def pip_factor(pair: str) -> int:
    return 100 if pair.upper().endswith("JPY") else 10000


def load_overrides(path: str) -> dict[str, str]:
    overrides: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            overrides[key.strip()] = value.strip()
    return overrides


def load_events(path: str) -> dict[str, list[str]]:
    events: dict[str, list[str]] = {}
    if not os.path.exists(path):
        print(f"Warning: No events file at {path}, skipping event-based overrides")
        return events
    with open(path, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            events.setdefault(row["date"], []).append(row["event"])
    return events


def sell_entry_distance_override(events: dict[str, list[str]], d: date) -> int | None:
    names = events.get(d.isoformat(), [])
    matches = [dist for name in names for event, dist in EVENT_SELL_ENTRY_DISTANCE_PIPS.items() if event in name]
    return max(matches) if matches else None


def is_above_upper_band(
    db_path: str, pair: str, period: int, sigma: int, today: date, verbose: bool = True
) -> bool:
    if not os.path.exists(db_path):
        if verbose:
            print(f"Warning: No database at {db_path}, skipping Bollinger Bands check")
        return False

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT date, close FROM price_history WHERE pair = ? AND date < ? ORDER BY date DESC LIMIT ?",
            (pair, today.isoformat(), period),
        ).fetchall()

    if len(rows) < period:
        if verbose:
            print(f"Warning: Not enough data for {pair} Bollinger Bands ({len(rows)}/{period} bars), skipping")
        return False

    rows.reverse()
    last_date, last_close = rows[-1]
    closes = [close for _, close in rows]
    mean = sum(closes) / period
    variance = sum((c - mean) ** 2 for c in closes) / period
    stddev = variance**0.5
    upper = mean + sigma * stddev
    lower = mean - sigma * stddev

    if verbose:
        print(f"\n[Bollinger Bands: {pair}]")
        print(f"Last confirmed date: {last_date}")
        print(f"Close: {last_close:.2f}  Mean: {mean:.2f}  Upper({sigma}σ): {upper:.2f}  Lower({sigma}σ): {lower:.2f}")
        if last_close > upper:
            print(f"Close is above +{sigma}σ")
        elif last_close < lower:
            print(f"Close is below -{sigma}σ")
        else:
            print(f"Close is within ±{sigma}σ")

    return last_close > upper


def is_below_fib_retracement(
    db_path: str,
    pair: str,
    lookback: int,
    min_range_pips: int,
    retracement: float,
    today: date,
    verbose: bool = True,
) -> bool:
    """True if the last `lookback` closes swung down >= min_range_pips (low more recent than high)
    and the latest close hasn't yet retraced back above the `retracement` Fibonacci level."""
    if not os.path.exists(db_path):
        if verbose:
            print(f"Warning: No database at {db_path}, skipping Fibonacci Retracement check")
        return False

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT date, close FROM price_history WHERE pair = ? AND date < ? ORDER BY date DESC LIMIT ?",
            (pair, today.isoformat(), lookback),
        ).fetchall()

    if len(rows) < lookback:
        if verbose:
            print(f"Warning: Not enough data for {pair} Fibonacci Retracement ({len(rows)}/{lookback} bars), skipping")
        return False

    rows.reverse()
    closes = [close for _, close in rows]
    last_close = closes[-1]
    high = max(closes)
    low = min(closes)
    high_index = closes.index(high)
    low_index = closes.index(low)
    range_pips = (high - low) * pip_factor(pair)
    fib_level = low + (high - low) * retracement

    if verbose:
        print(f"\n[Fibonacci Retracement: {pair}]")
        print(
            f"High: {high:.2f} ({rows[high_index][0]})  Low: {low:.2f} ({rows[low_index][0]})  "
            f"Range: {range_pips:.0f} pips"
        )
        print(f"Last close: {last_close:.2f}  {retracement:.0%} level: {fib_level:.2f}")

    if range_pips < min_range_pips:
        return False
    if low_index <= high_index:
        return False

    return last_close <= fib_level


def is_last_close_bearish(db_path: str, pair: str, today: date, verbose: bool = True) -> bool:
    """True if the last confirmed candle (before `today`) closed below its open."""
    if not os.path.exists(db_path):
        if verbose:
            print(f"Warning: No database at {db_path}, skipping bearish candle check")
        return False

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT date, open, close FROM price_history WHERE pair = ? AND date < ? ORDER BY date DESC LIMIT 1",
            (pair, today.isoformat()),
        ).fetchone()

    if row is None:
        if verbose:
            print(f"Warning: No confirmed candle for {pair}, skipping bearish candle check")
        return False

    last_date, last_open, last_close = row
    if verbose:
        print(f"\n[Bearish Candle: {pair}]")
        print(f"Last confirmed date: {last_date}")
        print(f"Open: {last_open:.2f}  Close: {last_close:.2f}")
        print("Bearish" if last_close < last_open else "Not bearish")

    return last_close < last_open


def decide_entry_distances(
    events: dict[str, list[str]],
    db_path: str,
    pair: str,
    period: int,
    sigma: int,
    today: date,
    verbose: bool = True,
) -> tuple[int | None, int | None, str]:
    """Priority: 1. today's event, 2. Fibonacci Retracement, 3. Bollinger Bands +sigma, 4. tomorrow's event,
    5. last confirmed candle bearish."""
    today_event_pips = sell_entry_distance_override(events, today)
    below_fib_retracement = is_below_fib_retracement(
        db_path, pair, FIB_LOOKBACK, FIB_MIN_RANGE_PIPS, FIB_RETRACEMENT, today, verbose=verbose
    )
    above_upper_band = is_above_upper_band(db_path, pair, period, sigma, today, verbose=verbose)
    tomorrow_event_pips = sell_entry_distance_override(events, today + timedelta(days=1))
    last_close_bearish = is_last_close_bearish(db_path, pair, today, verbose=verbose)

    if today_event_pips is not None:
        return today_event_pips, None, "today event"
    if below_fib_retracement:
        return FIB_SELL_ENTRY_DISTANCE_PIPS, None, f"{pair} below {FIB_RETRACEMENT:.0%} fib retracement"
    if above_upper_band:
        return 0, 70, f"{pair} above +{sigma}σ"
    if tomorrow_event_pips is not None:
        return tomorrow_event_pips, None, "tomorrow event"
    if last_close_bearish:
        return BEARISH_CANDLE_SELL_ENTRY_DISTANCE_PIPS, None, f"{pair} last candle bearish"
    return None, None, "none"


def all_price_history_dates(db_path: str, pair: str) -> list[date]:
    if not os.path.exists(db_path):
        print(f"Error: No database at {db_path}")
        return []

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT date FROM price_history WHERE pair = ? ORDER BY date", (pair,)
        ).fetchall()
    return [date.fromisoformat(row[0]) for row in rows]


def run_history(events: dict[str, list[str]]) -> None:
    dates = all_price_history_dates(DB_PATH, BOLLINGER_PAIR)
    if not dates:
        print(f"No price history found for {BOLLINGER_PAIR}, nothing to write")
        return

    os.makedirs(os.path.dirname(HISTORY_OUTPUT_PATH), exist_ok=True)
    written = 0
    with open(HISTORY_OUTPUT_PATH, "w") as f:
        f.write("date\tsell_entry_distance_pips\tbuy_entry_distance_pips\treason\n")
        for d in dates:
            sell_pips, buy_pips, reason = decide_entry_distances(
                events, DB_PATH, BOLLINGER_PAIR, BOLLINGER_PERIOD, BOLLINGER_SIGMA, d, verbose=False
            )
            if reason == "none":
                continue
            sell_str = "" if sell_pips is None else str(sell_pips)
            buy_str = "" if buy_pips is None else str(buy_pips)
            f.write(f"{d.isoformat()}\t{sell_str}\t{buy_str}\t{reason}\n")
            written += 1

    print(f"Wrote {written} rows to {HISTORY_OUTPUT_PATH}")


def notify_discord(webhook_url: str | None, message: str) -> None:
    if not webhook_url:
        return
    body = json.dumps({"content": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except urllib.error.URLError as e:
        print(f"Warning: Failed to send Discord notification: {e}")


def render(template_lines: list[str], overrides: dict[str, str]) -> str:
    used_keys: set[str] = set()
    output_lines: list[str] = []
    for line in template_lines:
        stripped = line.rstrip("\n")
        if not stripped.startswith(";") and "=" in stripped:
            key, _, _ = stripped.partition("=")
            key = key.strip()
            if key in overrides:
                stripped = f"{key}={overrides[key]}"
                used_keys.add(key)
        output_lines.append(stripped)

    unused_keys = overrides.keys() - used_keys
    for key in unused_keys:
        print(f"Warning: '{key}' is not a known .set key, ignoring")

    return "\n".join(output_lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history",
        action="store_true",
        help=(
            f"write the daily SellEntryDistancePips/BuyEntryDistancePips judgment history for all "
            f"{BOLLINGER_PAIR} dates in the database to {HISTORY_OUTPUT_PATH} instead of generating .set files"
        ),
    )
    args = parser.parse_args()

    events = load_events(EVENTS_PATH)

    if args.history:
        run_history(events)
        return

    if not os.path.isdir(ACCOUNTS_DIR):
        print(f"Error: No accounts directory at {ACCOUNTS_DIR}")
        return

    today = date.today()
    sell_entry_distance_pips, buy_entry_distance_pips, reason = decide_entry_distances(
        events, DB_PATH, BOLLINGER_PAIR, BOLLINGER_PERIOD, BOLLINGER_SIGMA, today
    )
    if buy_entry_distance_pips is not None:
        message = f"SellEntryDistancePips: {sell_entry_distance_pips}, BuyEntryDistancePips: {buy_entry_distance_pips} ({reason})"
    elif sell_entry_distance_pips is not None:
        message = f"SellEntryDistancePips: {sell_entry_distance_pips} ({reason})"
    else:
        message = f"No override (reason: {reason})"

    print(message)

    changed = False
    for name1 in sorted(os.listdir(ACCOUNTS_DIR)):
        name1_dir = os.path.join(ACCOUNTS_DIR, name1)
        if not os.path.isdir(name1_dir):
            continue

        default_set_path = os.path.join(name1_dir, DEFAULT_SET_FILENAME)
        if not os.path.exists(default_set_path):
            print(f"Warning: No {DEFAULT_SET_FILENAME} in {name1_dir}, skipping")
            continue
        with open(default_set_path) as f:
            template_lines = f.readlines()

        for filename in sorted(os.listdir(name1_dir)):
            if not filename.endswith(".set") or filename == DEFAULT_SET_FILENAME:
                continue
            name2 = filename[: -len(".set")]
            overrides = load_overrides(os.path.join(name1_dir, filename))
            if sell_entry_distance_pips is not None:
                overrides["SellEntryDistancePips"] = str(sell_entry_distance_pips)
            if buy_entry_distance_pips is not None:
                overrides["BuyEntryDistancePips"] = str(buy_entry_distance_pips)
            content = render(template_lines, overrides)

            out_dir = os.path.join(OUTPUT_DIR, name2)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{name1}.set")
            if os.path.exists(out_path):
                with open(out_path) as f:
                    file_changed = f.read() != content
            else:
                file_changed = True
            changed = changed or file_changed
            with open(out_path, "w") as f:
                f.write(content)
            print(f"Written: {out_path}")

    if changed:
        notify_discord(DISCORD_WEBHOOK_URL, message)


if __name__ == "__main__":
    main()
