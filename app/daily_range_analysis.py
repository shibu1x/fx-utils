#!/usr/bin/env python3
"""
Analyze daily high/low pip range from open price for FX pairs.
"""

import csv
import os
import sqlite3


def pip_factor(pair: str) -> int:
    return 100 if pair.upper().endswith("JPY") else 10000


def load_events(path: str) -> dict[str, str]:
    events: dict[str, list[str]] = {}
    if not os.path.exists(path):
        return events
    with open(path, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            date = row["date"]
            event = row["event"]
            seen = events.setdefault(date, [])
            if event not in seen:
                seen.append(event)
    return {date: ",".join(names) for date, names in events.items()}


def fetch_data(conn: sqlite3.Connection) -> list[tuple]:
    query = "SELECT pair, date, open, high, low, close FROM price_history ORDER BY pair, date"
    return conn.execute(query).fetchall()


def main() -> None:
    conn = sqlite3.connect("/data/db/fx_utils.db")
    try:
        rows = fetch_data(conn)
    finally:
        conn.close()

    if not rows:
        print("No data found.")
        return

    events = load_events("/data/input/events.tsv")

    output_path = "/data/output/daily_range_analysis.tsv"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w") as f:
        f.write("pair\tdate\topen\thigh\tlow\tclose\thigh_diff\tlow_diff\tclose_open_diff\tevent\tnote\n")
        for pair, date, open_, high, low, close in rows:
            factor = pip_factor(pair)
            high_pips = (high - open_) * factor
            low_pips = (open_ - low) * factor
            close_open_diff_pips = (close - open_) * factor
            event = events.get(date, "")
            f.write(
                f"{pair}\t{date}\t{open_:.2f}\t{high:.2f}\t{low:.2f}\t{close:.2f}"
                f"\t{high_pips:.0f}\t{low_pips:.0f}\t{close_open_diff_pips:.0f}\t{event}\t\n"
            )

    print(f"Wrote {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
