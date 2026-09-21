#!/usr/bin/env python3
"""Generate MT4/MT5 EA grid .set files for multiple FX pairs from fx_utils.db."""

import os
import sqlite3
from dataclasses import dataclass
from datetime import date


DB_PATH = "/data/db/fx_utils.db"
OUTPUT_DIR = "/data/output/sets"


def pip_factor(pair: str) -> int:
    return 100 if pair.upper().endswith("JPY") else 10000


def price_to_pips(price: float, pair: str) -> int:
    return round(price * pip_factor(pair))


def pips_to_price(pips: int, pair: str) -> float:
    return pips / pip_factor(pair)


def format_price(price: float, pair: str) -> str:
    return f"{price:.2f}" if pair.upper().endswith("JPY") else f"{price:.4f}"


def round_center_pips(pips: int, grid_step: int) -> int:
    if grid_step == 5:
        return (pips // 5) * 5 + 2
    if grid_step == 4:
        return (pips // 4) * 4 + 1
    if grid_step == 3:
        return (pips // 3) * 3
    return pips


def env_prefix(pair: str) -> str:
    return pair.replace("/", "")


def env_val(key: str, default, cast):
    raw = os.environ.get(key, str(default))
    try:
        return cast(raw)
    except (ValueError, TypeError):
        print(f"Warning: Invalid {key}='{raw}', using {default}")
        return cast(default)


@dataclass
class Account:
    name: str
    lot: float
    direction: str = "both"  # "long", "short", or "both"

    @classmethod
    def parse(cls, entry: str) -> "Account | None":
        parts = entry.strip().split(":")
        if len(parts) < 2:
            print(f"Warning: Invalid account entry '{entry}', skipping")
            return None
        direction = parts[2].lower() if len(parts) >= 3 else "both"
        if direction not in ("long", "short", "both"):
            print(f"Warning: Invalid direction '{direction}' in '{entry}', defaulting to 'both'")
            direction = "both"
        return cls(name=parts[0], lot=float(parts[1]), direction=direction)


@dataclass
class PairConfig:
    pair: str
    magic_number: int
    grid_step_pips: int
    center_adjustment: float
    grid_range: float
    center_max: float | None
    center_min: float | None
    accounts: list[Account]

    @classmethod
    def from_env(cls, pair: str) -> "PairConfig":
        p = pair.upper()
        e = env_prefix(p)
        center_max_str = os.environ.get(f"{e}_GRID_CENTER_MAX")
        center_min_str = os.environ.get(f"{e}_GRID_CENTER_MIN")
        raw_accounts = os.environ.get(f"{e}_ACCOUNTS", "")
        accounts = [a for entry in raw_accounts.split(",") if entry.strip() for a in [Account.parse(entry)] if a]
        return cls(
            pair=p,
            magic_number=env_val(f"{e}_MAGIC_NUMBER", 8001, int),
            grid_step_pips=env_val(f"{e}_GRID_STEP_PIPS", 5, int),
            center_adjustment=env_val(f"{e}_GRID_CENTER_ADJUSTMENT", 0, float),
            grid_range=env_val(f"{e}_GRID_RANGE", 1, float),
            center_max=float(center_max_str) if center_max_str else None,
            center_min=float(center_min_str) if center_min_str else None,
            accounts=accounts,
        )


@dataclass
class GridResult:
    date: str
    previous_close: float
    center_price: float
    sell_range_pips: int
    buy_range_pips: int


def calculate_grid(config: PairConfig, previous_date: str, previous_close: float) -> GridResult:
    pair = config.pair
    prev_pips = price_to_pips(previous_close, pair)
    center_pips = price_to_pips(previous_close * (1 + config.center_adjustment / 100), pair)
    base_pips = price_to_pips(previous_close * config.grid_range / 100, pair)

    if config.center_max is not None:
        center_pips = min(center_pips, price_to_pips(config.center_max, pair))
        center_pips = max(center_pips, prev_pips - base_pips)
    if config.center_min is not None:
        center_pips = max(center_pips, price_to_pips(config.center_min, pair))
        center_pips = min(center_pips, prev_pips + base_pips)

    adjustment_pips = center_pips - prev_pips
    center_price = pips_to_price(round_center_pips(center_pips, config.grid_step_pips), pair)

    return GridResult(
        date=previous_date,
        previous_close=previous_close,
        center_price=center_price,
        sell_range_pips=max(0, base_pips - adjustment_pips),
        buy_range_pips=max(0, base_pips + adjustment_pips),
    )


def write_set_file(config: PairConfig, account: Account, result: GridResult) -> None:
    path = f"{OUTPUT_DIR}/{account.name}/{env_prefix(config.pair).lower()}.set"
    content = (
        f"; === Basic Settings ===\n"
        f"GridStepPips={config.grid_step_pips}\n"
        f"LotSize={account.lot}\n"
        f"MagicNumber={config.magic_number}\n"
        f"GridCenterPrice={format_price(result.center_price, config.pair)}\n"
        f"; === Sell Grid Settings ===\n"
        f"SellEnabled={'false' if account.direction == 'long' else 'true'}\n"
        f"SellRangePips={result.sell_range_pips}\n"
        f"; === Buy Grid Settings ===\n"
        f"BuyEnabled={'false' if account.direction == 'short' else 'true'}\n"
        f"BuyRangePips={result.buy_range_pips}\n"
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    print(f"Written: {path}")


def process_pair(conn: sqlite3.Connection, config: PairConfig) -> None:
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT date, close FROM price_history WHERE pair = ? AND date < ? ORDER BY date DESC LIMIT 1",
        (config.pair, today),
    ).fetchone()
    if not row:
        print(f"Error: No data for {config.pair}")
        return

    result = calculate_grid(config, *row)

    print(f"\n[{config.pair}]")
    print(f"Date: {result.date}")
    print(f"Previous Close: {format_price(result.previous_close, config.pair)}")
    print(f"GridCenterPrice: {format_price(result.center_price, config.pair)}")
    print(f"SellRangePips: {result.sell_range_pips}")
    print(f"BuyRangePips: {result.buy_range_pips}")

    for account in config.accounts:
        write_set_file(config, account, result)


def main() -> None:
    pairs_str = os.environ.get("PAIRS", "")
    pairs = [p.strip().upper() for p in pairs_str.split(",") if p.strip()]
    if not pairs:
        print("Error: PAIRS environment variable is not set")
        return

    configs = [PairConfig.from_env(pair) for pair in pairs]

    with sqlite3.connect(DB_PATH) as conn:
        for config in configs:
            process_pair(conn, config)


if __name__ == "__main__":
    main()
