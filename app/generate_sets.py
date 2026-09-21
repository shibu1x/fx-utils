#!/usr/bin/env python3
"""Generate MT5 EA .set files per account from data/input/sets/<name1>/default.set, overridden by data/input/sets/<name1>/<name2>.set."""

import csv
import os
import sqlite3
from datetime import date, timedelta


DEFAULT_SET_FILENAME = "default.set"
ACCOUNTS_DIR = "/data/input/sets"
EVENTS_PATH = "/data/input/events.tsv"
DB_PATH = "/data/db/fx_utils.db"
OUTPUT_DIR = "/data/output/sets"
EVENT_SELL_ENTRY_DISTANCE_PIPS = {"日銀": 130, "FOMC": 90}
BOLLINGER_PAIR = "USD/JPY"
BOLLINGER_PERIOD = 20
BOLLINGER_SIGMA = 2


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


def is_above_upper_band(db_path: str, pair: str, period: int, sigma: int, today: date) -> bool:
    if not os.path.exists(db_path):
        print(f"Warning: No database at {db_path}, skipping Bollinger Bands check")
        return False

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT date, close FROM price_history WHERE pair = ? AND date < ? ORDER BY date DESC LIMIT ?",
            (pair, today.isoformat(), period),
        ).fetchall()

    if len(rows) < period:
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
    if not os.path.isdir(ACCOUNTS_DIR):
        print(f"Error: No accounts directory at {ACCOUNTS_DIR}")
        return

    events = load_events(EVENTS_PATH)
    today = date.today()
    today_event_pips = sell_entry_distance_override(events, today)
    above_upper_band = is_above_upper_band(DB_PATH, BOLLINGER_PAIR, BOLLINGER_PERIOD, BOLLINGER_SIGMA, today)
    tomorrow_event_pips = sell_entry_distance_override(events, today + timedelta(days=1))

    # Priority: 1. today's event, 2. Bollinger Bands +2σ, 3. tomorrow's event.
    sell_entry_distance_pips: int | None = None
    buy_entry_distance_pips: int | None = None
    if today_event_pips is not None:
        sell_entry_distance_pips = today_event_pips
        print(f"SellEntryDistancePips: {sell_entry_distance_pips} (today event)")
    elif above_upper_band:
        sell_entry_distance_pips = 0
        buy_entry_distance_pips = 70
        print(f"SellEntryDistancePips: 0, BuyEntryDistancePips: 70 ({BOLLINGER_PAIR} above +{BOLLINGER_SIGMA}σ)")
    elif tomorrow_event_pips is not None:
        sell_entry_distance_pips = tomorrow_event_pips
        print(f"SellEntryDistancePips: {sell_entry_distance_pips} (tomorrow event)")

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
            with open(out_path, "w") as f:
                f.write(content)
            print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
