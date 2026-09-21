#!/usr/bin/env python3
"""
Fetch hourly FX price data (USD/JPY, EUR/USD, GBP/USD) from Dukascopy
and resample it into daily OHLC bars using a New York 17:00 session boundary.
"""

import argparse
import logging
import math
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import dukascopy_python
import pandas as pd

logging.disable(logging.INFO)

DEFAULT_PAIRS = ["USD/JPY"]
HISTORY_DAYS = 400
NY_TZ = ZoneInfo("America/New_York")
SESSION_CLOSE_HOUR = 17  # daily session boundary: 17:00 New York time


def create_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            pair TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            PRIMARY KEY (pair, date)
        )
    """)
    conn.commit()


def fetch_hourly(pair: str) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=HISTORY_DAYS)
    return dukascopy_python.fetch(
        pair,
        dukascopy_python.INTERVAL_HOUR_1,
        dukascopy_python.OFFER_SIDE_BID,
        start,
        end,
    )


def session_date(ts: datetime) -> date:
    """Date of the NY-17:00-to-17:00 trading session an hourly bar belongs to.

    A session opens at 17:00 NY time and is labeled with the date it closes on
    (matching the "New York close" convention used by MT4/MT5 and most brokers).
    """
    ny_time = ts.astimezone(NY_TZ)
    day = ny_time.date()
    return day + timedelta(days=1) if ny_time.hour >= SESSION_CLOSE_HOUR else day


def resample_to_daily(hourly: pd.DataFrame) -> pd.DataFrame:
    hourly = hourly.sort_index()
    sessions = hourly.index.to_series().apply(session_date)
    daily = hourly.groupby(sessions).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    )
    daily.index.name = "date"
    return daily


def truncate(value: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.floor(value * factor) / factor


def save_to_sqlite(pair: str, data: pd.DataFrame, conn: sqlite3.Connection) -> None:
    decimals = 3 if "JPY" in pair else 5
    cursor = conn.cursor()
    for date, row in data.iterrows():
        cursor.execute(
            """
            INSERT OR REPLACE INTO price_history
            (pair, date, open, high, low, close)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                pair,
                date.isoformat(),
                truncate(float(row["open"]), decimals),
                truncate(float(row["high"]), decimals),
                truncate(float(row["low"]), decimals),
                truncate(float(row["close"]), decimals),
            ),
        )
    conn.commit()
    print(f"  Saved {len(data)} records")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch daily FX OHLC data from Dukascopy (built from 1H bars, NY 17:00 session close)"
    )
    parser.add_argument(
        "pairs",
        nargs="*",
        metavar="PAIR",
        help=f"Pairs to fetch (default: {', '.join(DEFAULT_PAIRS)}), e.g. USD/JPY, EUR/USD, GBP/USD",
    )
    args = parser.parse_args()

    target_pairs = [p.upper() for p in args.pairs] if args.pairs else DEFAULT_PAIRS

    os.makedirs("/data/db", exist_ok=True)
    conn = sqlite3.connect("/data/db/fx_utils.db")

    try:
        create_table(conn)

        for pair in target_pairs:
            print(f"Fetching {pair}...")
            try:
                hourly = fetch_hourly(pair)
            except Exception as e:
                print(f"  Failed to fetch {pair}: {e}")
                continue
            if hourly.empty:
                print("  No data returned")
                continue
            data = resample_to_daily(hourly)
            print(f"  {len(data)} records: {data.index[0]} to {data.index[-1]}")
            save_to_sqlite(pair, data, conn)

    finally:
        conn.close()

    print("Done!")


if __name__ == "__main__":
    main()
