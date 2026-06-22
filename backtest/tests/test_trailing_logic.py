from __future__ import annotations

import sys
import unittest
from datetime import date, time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.backtest_mt5 import (  # noqa: E402
    BacktestConfig,
    IST,
    current_and_previous_high,
    manage_sell_trade,
    price_at_or_above,
)
from backtest.optimizer_engine import EntryLayout, build_scan_context  # noqa: E402


DAY = date(2026, 5, 15)


def candle_time(value: str) -> pd.Timestamp:
    hour, minute = (int(part) for part in value.split(":"))
    return pd.Timestamp.combine(DAY, time(hour, minute)).tz_localize(IST)


def candle_frame(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "time_ist": candle_time(start),
                "trade_date": DAY,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "tick_volume": 1,
            }
            for start, open_, high, low, close in rows
        ]
    )


class TrailingStopLogicTest(unittest.TestCase):
    def test_trail_uses_only_fully_closed_candles(self) -> None:
        trail = candle_frame(
            [
                ("10:30", 4606.41, 4606.41, 4599.06, 4599.06),
                ("10:35", 4599.06, 4601.31, 4592.55, 4592.55),
                ("10:40", 4592.55, 4593.55, 4588.41, 4588.41),
                ("10:45", 4592.14, 4595.30, 4581.57, 4583.01),
                ("10:50", 4583.02, 4589.24, 4580.85, 4584.85),
                ("10:55", 4584.80, 4590.16, 4583.48, 4588.82),
                ("11:00", 4588.88, 4589.77, 4567.39, 4569.65),
            ]
        )

        self.assertEqual(current_and_previous_high(trail, candle_time("10:55"), "M5"), 4595.30)
        self.assertEqual(current_and_previous_high(trail, candle_time("10:59"), "M5"), 4595.30)
        self.assertEqual(current_and_previous_high(trail, candle_time("11:00"), "M5"), 4590.16)

    def test_sell_trail_does_not_exit_inside_candle_that_created_sl(self) -> None:
        execution = candle_frame(
            [
                ("10:48", 4587.51, 4589.26, 4584.39, 4584.50),
                ("10:49", 4584.40, 4584.70, 4581.57, 4583.01),
                ("10:50", 4583.02, 4583.41, 4580.85, 4581.84),
                ("10:51", 4581.96, 4588.18, 4581.56, 4587.37),
                ("10:52", 4587.35, 4589.24, 4585.95, 4587.55),
                ("10:53", 4587.56, 4587.56, 4583.44, 4583.96),
                ("10:54", 4583.92, 4586.72, 4582.07, 4584.85),
                ("10:55", 4584.80, 4586.91, 4584.08, 4585.20),
                ("10:56", 4585.11, 4585.98, 4583.48, 4585.07),
                ("10:57", 4585.13, 4587.19, 4584.27, 4585.15),
                ("10:58", 4585.36, 4588.85, 4585.36, 4588.21),
                ("10:59", 4588.24, 4590.16, 4587.38, 4588.82),
                ("11:00", 4588.88, 4589.77, 4580.59, 4580.74),
                ("11:01", 4580.77, 4581.26, 4578.03, 4578.35),
            ]
        )
        trail = candle_frame(
            [
                ("10:30", 4606.41, 4606.41, 4599.06, 4599.06),
                ("10:35", 4599.06, 4601.31, 4592.55, 4592.55),
                ("10:40", 4592.55, 4593.55, 4588.41, 4588.41),
                ("10:45", 4592.14, 4595.30, 4581.57, 4583.01),
                ("10:50", 4583.02, 4589.24, 4580.85, 4584.85),
                ("10:55", 4584.80, 4590.16, 4583.48, 4588.82),
                ("11:00", 4588.88, 4589.77, 4567.39, 4569.65),
            ]
        )
        config = BacktestConfig(
            symbol="GOLD.i#",
            from_date=DAY,
            to_date=DAY,
            data_source="MT5",
            timeframe="M5",
            trail_timeframe="M5",
            session_end=time(11, 1),
            stop_points=10.0,
            first_trail_profit=10.0,
            first_trail_lock_loss=0.0,
            second_trail_profit=20.0,
        )

        result = manage_sell_trade(execution, trail, 0, 4606.50, config)

        self.assertEqual(result[0], "2026-05-15T11:01:00+05:30")
        self.assertEqual(result[2], "FORCE_EXIT")
        self.assertEqual(result[7], "2026-05-15T11:00:00+05:30")
        self.assertEqual(result[8], 4590.16)

    def test_optimizer_context_uses_same_closed_candle_rule(self) -> None:
        trail = candle_frame(
            [
                ("10:30", 4606.41, 4606.41, 4599.06, 4599.06),
                ("10:35", 4599.06, 4601.31, 4592.55, 4592.55),
                ("10:40", 4592.55, 4593.55, 4588.41, 4588.41),
                ("10:45", 4592.14, 4595.30, 4581.57, 4583.01),
                ("10:50", 4583.02, 4589.24, 4580.85, 4584.85),
                ("10:55", 4584.80, 4590.16, 4583.48, 4588.82),
                ("11:00", 4588.88, 4589.77, 4567.39, 4569.65),
            ]
        )
        times = np.asarray([candle_time(value).value for value in ("10:55", "10:59", "11:00")], dtype=np.int64)
        prices = np.zeros(len(times), dtype=np.float64)
        layout = EntryLayout(
            day_starts=np.asarray([0], dtype=np.int64),
            day_ends=np.asarray([len(times)], dtype=np.int64),
            cutoff_ends=np.asarray([len(times)], dtype=np.int64),
            range_highs=np.asarray([0.0], dtype=np.float64),
            range_lows=np.asarray([0.0], dtype=np.float64),
            times_ns=times,
            opens=prices,
            highs=prices,
            lows=prices,
            closes=prices,
            at_session_end=np.asarray([False, False, True], dtype=np.bool_),
        )

        context = build_scan_context(layout, trail, "M5")

        self.assertEqual(context.trail_highs.tolist(), [4595.30, 4595.30, 4590.16])

    def test_price_epsilon_does_not_hide_real_price_gap(self) -> None:
        self.assertFalse(price_at_or_above(4589.77, 4590.16))
        self.assertFalse(price_at_or_above(4590.11, 4590.16))
        self.assertTrue(price_at_or_above(4590.16 - 5e-10, 4590.16))


if __name__ == "__main__":
    unittest.main()
