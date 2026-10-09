#!/usr/bin/env python3
"""Generate MT5 EA .set files per account from data/input/sets/<name1>/default.set, overridden by data/input/sets/<name1>/<name2>.set."""

import argparse
import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import date


DEFAULT_SET_FILENAME = "default.set"
FADE_NAME1 = "fade"
BREAKOUT_NAME1 = "breakout"
ACCOUNTS_DIR = "/data/input/sets"
DB_PATH = "/data/db/fx_utils.db"
OUTPUT_DIR = "/data/output/sets"
HISTORY_OUTPUT_PATH = "/data/output/generate_sets_history.tsv"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
BOLLINGER_PAIR = "USD/JPY"
BOLLINGER_PERIOD = 20
BOLLINGER_SIGMA = 2
BOLLINGER_BUY_ENTRY_DISTANCE_PIPS = 70
BREAKOUT_HIGH_MINUS_CLOSE_MIN_PIPS = 100


def pip_factor(pair: str) -> int:
    return 100 if pair.upper().endswith("JPY") else 10000


def load_overrides(path: str) -> dict[str, str]:
    overrides: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            overrides[key.strip()] = value.strip()
    return overrides


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


def is_last_high_minus_close_at_least(
    db_path: str, pair: str, min_pips: int, today: date, verbose: bool = True
) -> bool:
    """True if the last confirmed candle's high - close >= min_pips."""
    if not os.path.exists(db_path):
        if verbose:
            print(f"Warning: No database at {db_path}, skipping high-close pip check")
        return False

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT date, high, close FROM price_history WHERE pair = ? AND date < ? ORDER BY date DESC LIMIT 1",
            (pair, today.isoformat()),
        ).fetchone()

    if row is None:
        if verbose:
            print(f"Warning: No confirmed candle for {pair}, skipping high-close pip check")
        return False

    last_date, last_high, last_close = row
    diff_pips = (last_high - last_close) * pip_factor(pair)
    if verbose:
        print(f"\n[High - Close: {pair}]")
        print(f"Last confirmed date: {last_date}")
        print(f"High: {last_high:.2f}  Close: {last_close:.2f}  Diff: {diff_pips:.0f} pips")

    return diff_pips >= min_pips


def decide_buy_entry_distance(
    db_path: str,
    pair: str,
    period: int,
    sigma: int,
    today: date,
    verbose: bool = True,
) -> tuple[int | None, str]:
    """If the last confirmed close is above the Bollinger upper band, override BuyEntryDistancePips."""
    if is_above_upper_band(db_path, pair, period, sigma, today, verbose=verbose):
        return BOLLINGER_BUY_ENTRY_DISTANCE_PIPS, f"{pair} above +{sigma}σ"
    return None, "none"


def all_price_history_dates(db_path: str, pair: str) -> list[date]:
    if not os.path.exists(db_path):
        print(f"Error: No database at {db_path}")
        return []

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT date FROM price_history WHERE pair = ? ORDER BY date", (pair,)
        ).fetchall()
    return [date.fromisoformat(row[0]) for row in rows]


def run_history() -> None:
    dates = all_price_history_dates(DB_PATH, BOLLINGER_PAIR)
    if not dates:
        print(f"No price history found for {BOLLINGER_PAIR}, nothing to write")
        return

    os.makedirs(os.path.dirname(HISTORY_OUTPUT_PATH), exist_ok=True)
    written = 0
    with open(HISTORY_OUTPUT_PATH, "w") as f:
        f.write("date\tbuy_entry_distance_pips\treason\n")
        for d in dates:
            buy_pips, reason = decide_buy_entry_distance(
                DB_PATH, BOLLINGER_PAIR, BOLLINGER_PERIOD, BOLLINGER_SIGMA, d, verbose=False
            )
            if reason == "none":
                continue
            f.write(f"{d.isoformat()}\t{buy_pips}\t{reason}\n")
            written += 1

    print(f"Wrote {written} rows to {HISTORY_OUTPUT_PATH}")


def notify_discord(webhook_url: str | None, message: str) -> None:
    if not webhook_url:
        return
    body = json.dumps({"content": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "fx-utils-bot/1.0"},
        method="POST",
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
            f"write the daily BuyEntryDistancePips judgment history for all "
            f"{BOLLINGER_PAIR} dates in the database to {HISTORY_OUTPUT_PATH} instead of generating .set files"
        ),
    )
    args = parser.parse_args()

    if args.history:
        run_history()
        return

    if not os.path.isdir(ACCOUNTS_DIR):
        print(f"Error: No accounts directory at {ACCOUNTS_DIR}")
        return

    today = date.today()
    buy_entry_distance_pips, reason = decide_buy_entry_distance(
        DB_PATH, BOLLINGER_PAIR, BOLLINGER_PERIOD, BOLLINGER_SIGMA, today
    )
    breakout_high_minus_close = is_last_high_minus_close_at_least(
        DB_PATH, BOLLINGER_PAIR, BREAKOUT_HIGH_MINUS_CLOSE_MIN_PIPS, today
    )
    messages = []
    if buy_entry_distance_pips is not None:
        messages.append(f"[{FADE_NAME1}] {reason}: BuyEntryDistancePips: {buy_entry_distance_pips}")
    if breakout_high_minus_close:
        messages.append(f"[{BREAKOUT_NAME1}] high - close >= {BREAKOUT_HIGH_MINUS_CLOSE_MIN_PIPS} pips: SellOpenNew: false")
    message = "\n".join(messages)

    print(message)

    changed_files: list[str] = []
    for name1 in sorted(os.listdir(ACCOUNTS_DIR)):
        name1_dir = os.path.join(ACCOUNTS_DIR, name1)
        if not os.path.isdir(name1_dir):
            continue

        default_set_path = os.path.join(name1_dir, DEFAULT_SET_FILENAME)
        if not os.path.exists(default_set_path):
            print(f"Warning: No {DEFAULT_SET_FILENAME} in {name1_dir}, skipping")
            continue
        with open(default_set_path, encoding="utf-8") as f:
            template_lines = f.readlines()

        apply_entry_distance_override = name1 == FADE_NAME1
        apply_breakout_override = name1 == BREAKOUT_NAME1

        for filename in sorted(os.listdir(name1_dir)):
            if not filename.endswith(".set") or filename == DEFAULT_SET_FILENAME:
                continue
            name2 = filename[: -len(".set")]
            overrides = load_overrides(os.path.join(name1_dir, filename))
            if apply_entry_distance_override and buy_entry_distance_pips is not None:
                overrides["BuyEntryDistancePips"] = str(buy_entry_distance_pips)
            if apply_breakout_override and breakout_high_minus_close:
                overrides["SellOpenNew"] = "false"
            content = render(template_lines, overrides)

            out_dir = os.path.join(OUTPUT_DIR, name2)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{name1}.set")
            if os.path.exists(out_path):
                with open(out_path, encoding="utf-8") as f:
                    file_changed = f.read() != content
            else:
                file_changed = True
            if file_changed:
                changed_files.append(os.path.relpath(out_path, OUTPUT_DIR))
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"Written: {out_path}")

    if changed_files:
        files_list = "\n".join(f"- {path}" for path in changed_files)
        message = f"{message}\n\nUpdated files:\n{files_list}"
        notify_discord(DISCORD_WEBHOOK_URL, message)


if __name__ == "__main__":
    main()
