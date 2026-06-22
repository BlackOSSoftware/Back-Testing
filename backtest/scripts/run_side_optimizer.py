from __future__ import annotations

import csv
import json
from datetime import date, time, timedelta
from itertools import product
from pathlib import Path

from backtest.backtest_mt5 import BacktestConfig
from backtest.market_data import fetch_source_rates
from backtest.optimizer_engine import build_entry_layout, build_scan_context, evaluate_scan_batch


SOURCE = "MT5"
SYMBOL = "BTCUSD"
FROM_DATE = date(2024, 1, 1)
TO_DATE = date(2026, 5, 25)
ENTRY_TIMEFRAMES = ["M1", "M2", "M3", "M4", "M5", "M10", "M15", "M30", "H1", "H4"]
TRAIL_TIMEFRAMES = ["M1", "M2", "M3", "M4", "M5", "M10", "M15", "M30", "H1", "H4"]
ENTRY_BUFFERS = [0.15, 0.20]
STOP_POINTS = [650.0, 700.0, 750.0, 800.0, 850.0]
FIRST_TRAIL_PROFITS = [400.0, 300.0, 500.0, 700.0]
FIRST_TRAIL_LOCKS = [350.0, 400.0, 450.0, 500.0]
SECOND_TRAIL_PROFITS = [300.0]
TARGET_WIN_RATE = 70.0
MINIMUM_TRADES = 20


def row_for(mode: str, side_filter: int, tested: int, entry_tf: str, trail_tf: str, values: tuple[float, float, float, float, float], stats: dict) -> dict:
    buffer_pct, stop, first_profit, first_lock, second_profit = values
    return {
        "rank": tested,
        "mode": mode,
        "qualified": bool(stats["total_trades"] >= MINIMUM_TRADES and stats["win_rate_pct"] >= TARGET_WIN_RATE),
        "timeframe": entry_tf,
        "trail_timeframe": trail_tf,
        "entry_buffer_pct": buffer_pct,
        "stop_points": stop,
        "first_trail_profit": first_profit,
        "first_trail_lock_loss": first_lock,
        "second_trail_profit": second_profit,
        "total_trades": stats["total_trades"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "win_rate_pct": stats["win_rate_pct"],
        "net_points": stats["net_points"],
        "profit_factor": stats["profit_factor"],
        "max_drawdown_points": stats["max_drawdown_points"],
        "side_filter": side_filter,
    }


def main() -> None:
    common = {
        "symbol": SYMBOL,
        "from_date": FROM_DATE,
        "to_date": TO_DATE,
        "data_source": SOURCE,
        "range_start": time(8, 30),
        "range_end": time(9, 30),
        "session_start": time(9, 30),
        "entry_cutoff": time(18, 0),
        "session_end": time(19, 30),
    }
    parameters = list(product(ENTRY_BUFFERS, STOP_POINTS, FIRST_TRAIL_PROFITS, FIRST_TRAIL_LOCKS, SECOND_TRAIL_PROFITS))
    numeric_parameters = [(buffer / 100, stop, first_profit, first_lock, second_profit) for buffer, stop, first_profit, first_lock, second_profit in parameters]
    modes = [("BOTH", 0), ("BUY_ONLY", 1), ("SELL_ONLY", -1)]

    frames = {}
    layouts = {}
    contexts = {}
    rows = []
    tested = 0

    def candle_frame(timeframe: str, padded: bool):
        key = (timeframe, padded)
        if key not in frames:
            config = BacktestConfig(
                symbol=SYMBOL,
                from_date=FROM_DATE - timedelta(days=1) if padded else FROM_DATE,
                to_date=TO_DATE,
                data_source=SOURCE,
                timeframe=timeframe,
            )
            frames[key] = fetch_source_rates(config)
        return frames[key]

    def scan_context(entry_tf: str, trail_tf: str):
        key = (entry_tf, trail_tf)
        if key not in contexts:
            entry_df = candle_frame(entry_tf, False)
            if entry_tf not in layouts:
                layouts[entry_tf] = build_entry_layout(entry_df, BacktestConfig(**common, timeframe=entry_tf, trail_timeframe=trail_tf))
            trail_df = entry_df if trail_tf == entry_tf else candle_frame(trail_tf, True)
            contexts[key] = build_scan_context(layouts[entry_tf], trail_df, trail_tf)
        return contexts[key]

    for mode, side_filter in modes:
        for entry_tf in ENTRY_TIMEFRAMES:
            for trail_tf in TRAIL_TIMEFRAMES:
                context = scan_context(entry_tf, trail_tf)
                stats_rows = evaluate_scan_batch(context, numeric_parameters, side_filter=side_filter)
                for values, stats in zip(parameters, stats_rows):
                    tested += 1
                    rows.append(row_for(mode, side_filter, tested, entry_tf, trail_tf, values, stats))

    ranked = sorted(
        rows,
        key=lambda item: (item["qualified"], item["win_rate_pct"], item["net_points"], item["total_trades"]),
        reverse=True,
    )
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank

    output_dir = Path("results") / "side_optimizer"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "btc_usd_side_optimizer.csv"
    json_path = output_dir / "btc_usd_side_optimizer.json"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ranked[0].keys()))
        writer.writeheader()
        writer.writerows(ranked)
    json_path.write_text(
        json.dumps(
            {
                "config": {
                    "source": SOURCE,
                    "symbol": SYMBOL,
                    "from_date": FROM_DATE.isoformat(),
                    "to_date": TO_DATE.isoformat(),
                    "target_win_rate": TARGET_WIN_RATE,
                    "minimum_trades": MINIMUM_TRADES,
                    "tests": len(rows),
                    "entry_timeframes": ENTRY_TIMEFRAMES,
                    "trail_timeframes": TRAIL_TIMEFRAMES,
                },
                "best": ranked[:50],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"tested={len(rows)} csv={csv_path} json={json_path}")
    by_mode = {}
    for row in ranked:
        by_mode.setdefault(row["mode"], row)
    print(json.dumps({"best_by_mode": by_mode}, indent=2))
    print(json.dumps(ranked[:15], indent=2))


if __name__ == "__main__":
    main()
