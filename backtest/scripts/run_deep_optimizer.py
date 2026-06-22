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
TARGET_WIN_RATE = 70.0
MINIMUM_TRADES = 20


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
    candidates = [
        ("SELL_ONLY", -1, "M2", "M10"),
        ("SELL_ONLY", -1, "M2", "M5"),
        ("SELL_ONLY", -1, "M2", "M15"),
        ("BOTH", 0, "M2", "M2"),
        ("BUY_ONLY", 1, "M1", "M1"),
    ]
    entry_buffers = [0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20]
    stop_points = list(range(100, 1001, 50))
    first_trail_profits = list(range(100, 1001, 50))
    first_trail_locks = list(range(0, 551, 50))
    second_trail_profits = list(range(300, 1601, 100))
    parameters = list(product(entry_buffers, stop_points, first_trail_profits, first_trail_locks, second_trail_profits))
    numeric_parameters = [(buffer / 100, stop, first_profit, first_lock, second_profit) for buffer, stop, first_profit, first_lock, second_profit in parameters]

    frames = {}
    layouts = {}
    contexts = {}
    rows = []

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

    tested = 0
    for mode, side_filter, entry_tf, trail_tf in candidates:
        context = scan_context(entry_tf, trail_tf)
        stats_rows = evaluate_scan_batch(context, numeric_parameters, side_filter=side_filter)
        for values, stats in zip(parameters, stats_rows):
            tested += 1
            if stats["total_trades"] < MINIMUM_TRADES:
                continue
            buffer_pct, stop, first_profit, first_lock, second_profit = values
            rows.append(
                {
                    "mode": mode,
                    "qualified": bool(stats["win_rate_pct"] >= TARGET_WIN_RATE),
                    "timeframe": entry_tf,
                    "trail_timeframe": trail_tf,
                    "entry_buffer_pct": buffer_pct,
                    "stop_points": float(stop),
                    "first_trail_profit": float(first_profit),
                    "first_trail_lock_loss": float(first_lock),
                    "second_trail_profit": float(second_profit),
                    "total_trades": stats["total_trades"],
                    "wins": stats["wins"],
                    "losses": stats["losses"],
                    "win_rate_pct": stats["win_rate_pct"],
                    "net_points": stats["net_points"],
                    "profit_factor": stats["profit_factor"],
                    "max_drawdown_points": stats["max_drawdown_points"],
                }
            )

    ranked = sorted(rows, key=lambda row: (row["qualified"], row["win_rate_pct"], row["net_points"], row["total_trades"]), reverse=True)
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
    output_dir = Path("results") / "side_optimizer"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "btc_usd_deep_optimizer.csv"
    json_path = output_dir / "btc_usd_deep_optimizer.json"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ranked[0].keys()))
        writer.writeheader()
        writer.writerows(ranked)
    json_path.write_text(json.dumps({"tested": tested, "kept": len(ranked), "best": ranked[:100]}, indent=2), encoding="utf-8")
    print(f"tested={tested} kept={len(ranked)} csv={csv_path}")
    print(json.dumps(ranked[:20], indent=2))


if __name__ == "__main__":
    main()
