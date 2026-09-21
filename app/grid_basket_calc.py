#!/usr/bin/env python3
"""
Grid basket (Martingale) margin and P&L calculator for USD/JPY.

Given a level-0 entry price, the furthest (extreme) price reached, and the
current price, computes which grid levels would be open under
BollingerBasket.mq5's grid rules (see the mt5_ea project; lot size scales by
--lot-multiplier per level, grid step scales by --grid-step-multiplier per
level) and the resulting margin and unrealized P&L.

Assumes price moved monotonically from entry to the extreme, then retraced
to price - order fills and basket TP/SL are not simulated, only the final
levels reached (determined by the extreme) and their P&L at price.
"""

import argparse

PIP = 0.01
CONTRACT_SIZE = 100_000  # 1 lot = 100,000 USD
LOT_STEP = 0.01


def round_lot(lot: float) -> float:
    return round(round(lot / LOT_STEP) * LOT_STEP, 2)


def calc_step_price(step_price_base: float, step_multiplier: float, level: int) -> float:
    """Grid step distance required to open `level` (0-indexed; level 1 = first add)."""
    return step_price_base * (step_multiplier ** max(level - 1, 0))


def build_positions(entry: float, extreme: float, direction: str, lot: float, lot_multiplier: float,
                     step_pips: float, step_multiplier: float, max_levels: int) -> list[dict[str, float]]:
    is_buy = direction == "buy"
    step_price_base = step_pips * PIP
    positions = [{"level": 0, "lot": round_lot(lot), "price": entry}]
    boundary = entry

    while max_levels == 0 or len(positions) < max_levels:
        level = len(positions)
        step_price = calc_step_price(step_price_base, step_multiplier, level)
        trigger = boundary - step_price if is_buy else boundary + step_price
        reached = extreme <= trigger if is_buy else extreme >= trigger
        if not reached:
            break
        positions.append({"level": level, "lot": round_lot(lot * (lot_multiplier ** level)), "price": trigger})
        boundary = trigger

    return positions


def calc_margin(lot: float, leverage: int) -> float:
    return lot * CONTRACT_SIZE / leverage


def calc_pnl(entry: float, price: float, lot: float, direction: str) -> float:
    diff = (price - entry) if direction == "buy" else (entry - price)
    return diff * lot * CONTRACT_SIZE / price


def print_report(direction: str, entry: float, extreme: float, price: float,
                  positions: list[dict[str, float]], leverage: int) -> float:
    total_lot = sum(p["lot"] for p in positions)
    total_margin = sum(calc_margin(p["lot"], leverage) for p in positions)
    total_pnl_extreme = sum(calc_pnl(p["price"], extreme, p["lot"], direction) for p in positions)
    total_pnl = sum(calc_pnl(p["price"], price, p["lot"], direction) for p in positions)
    avg_price = sum(p["price"] * p["lot"] for p in positions) / total_lot
    avg_pips = (price - avg_price) / PIP if direction == "buy" else (avg_price - price) / PIP

    print(f"\n=== {direction.upper()} basket: entry {entry:.3f} -> extreme {extreme:.3f} -> price {price:.3f} ===")
    print(f"Leverage           : {leverage}x")
    print(f"Levels open        : {len(positions)}")
    print(f"Total lots         : {total_lot:.2f}")
    print(f"Avg price          : {avg_price:.3f} ({avg_pips:+.1f} pips)")
    print(f"Total margin       : {total_margin:,.2f} USD")
    print(f"Total P&L @extreme : {total_pnl_extreme:,.2f} USD")
    print(f"Total P&L @price   : {total_pnl:,.2f} USD")

    print(f"\n{'Lvl':>3}  {'Entry':>8}  {'Lot':>6}  {'Pips':>7}  {'Margin (USD)':>14}  {'P&L@extreme (USD)':>18}  {'P&L@price (USD)':>16}")
    print("-" * 97)
    for p in positions:
        pips = (price - p["price"]) / PIP if direction == "buy" else (p["price"] - price) / PIP
        margin = calc_margin(p["lot"], leverage)
        pnl_extreme = calc_pnl(p["price"], extreme, p["lot"], direction)
        pnl = calc_pnl(p["price"], price, p["lot"], direction)
        print(f"{p['level']:>3}  {p['price']:>8.3f}  {p['lot']:>6.2f}  {pips:>+7.1f}  {margin:>14,.2f}  {pnl_extreme:>18,.2f}  {pnl:>16,.2f}")
    print("-" * 97)
    print(f"{'':>3}  {'Total':>8}  {total_lot:>6.2f}  {'':>7}  {total_margin:>14,.2f}  {total_pnl_extreme:>18,.2f}  {total_pnl:>16,.2f}")

    return total_pnl


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calculate grid basket (Martingale) margin and unrealized P&L for USD/JPY, "
                     "given a level-0 entry price, the furthest (extreme) price reached, and the current price"
    )
    parser.add_argument("--entry", type=float, required=True, help="Level-0 entry price")
    parser.add_argument("--extreme", type=float, required=True,
                         help="Furthest price reached after entry, before retracing to --price "
                              "(determines which grid levels opened)")
    parser.add_argument("--price", type=float, required=True,
                         help="Current price to evaluate the open basket's P&L at, after retracing from --extreme")
    parser.add_argument("--direction", choices=["buy", "sell", "both"], default="buy", help="Basket direction (default: buy)")
    parser.add_argument("--lot", type=float, default=0.01, help="Level-0 lot size (default: 0.01)")
    parser.add_argument("--lot-multiplier", type=float, default=1.5, help="Lot multiplier per grid level (default: 1.5)")
    parser.add_argument("--grid-step-pips", type=float, default=10, help="Grid step (pips, default: 10)")
    parser.add_argument("--grid-step-multiplier", type=float, default=1.2, help="Grid step multiplier per level (default: 1.2)")
    parser.add_argument("--max-levels", type=int, default=10, help="Max grid levels (0 = unlimited, default: 10)")
    parser.add_argument("--leverage", type=int, default=1000, help="Leverage (default: 1000)")
    args = parser.parse_args()

    if args.max_levels < 0:
        parser.error("--max-levels must be non-negative")

    directions = ["buy", "sell"] if args.direction == "both" else [args.direction]
    grand_total = 0.0
    for direction in directions:
        positions = build_positions(args.entry, args.extreme, direction, args.lot, args.lot_multiplier,
                                     args.grid_step_pips, args.grid_step_multiplier, args.max_levels)
        grand_total += print_report(direction, args.entry, args.extreme, args.price, positions, args.leverage)

    if len(directions) > 1:
        print(f"\n=== Combined Total P&L: {grand_total:,.2f} USD ===")


if __name__ == "__main__":
    main()
