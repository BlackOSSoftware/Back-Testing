from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest import server as server_module  # noqa: E402
from backtest.server import (  # noqa: E402
    execute_optimizer,
    load_saved_optimizer_scan,
    optimizer_payload,
    optimizer_top_metric_indices,
    save_optimizer_output,
    saved_optimizer_scans,
    scan_time_profiles,
)


DAY = date(2026, 5, 15)


def candle_frame(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "time_ist": pd.Timestamp.combine(DAY, time.fromisoformat(start)).tz_localize("Asia/Kolkata"),
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


class OptimizerPayloadTest(unittest.TestCase):
    def test_blank_optimizer_cutoff_uses_setup_last_entry_time(self) -> None:
        profiles = scan_time_profiles(
            {
                "range_start_values": "08:30",
                "range_duration_values": "60",
                "entry_cutoff": "19:00",
                "entry_cutoff_values": "",
                "session_end": "21:30",
                "session_end_values": "21:30",
            }
        )

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["entry_cutoff"], time(19, 0))
        self.assertEqual(profiles[0]["session_end"], time(21, 30))

    def test_optimizer_includes_setup_force_exit_time(self) -> None:
        profiles = scan_time_profiles(
            {
                "range_start_values": "08:30",
                "range_duration_values": "60",
                "entry_cutoff": "19:00",
                "entry_cutoff_values": "",
                "session_end": "21:30",
                "session_end_values": "19:30,20:00",
            }
        )

        self.assertIn(time(21, 30), {profile["session_end"] for profile in profiles})

    def test_optimizer_rejects_cutoff_at_force_exit_time(self) -> None:
        profiles = scan_time_profiles(
            {
                "range_start_values": "08:30",
                "range_duration_values": "60",
                "entry_cutoff": "19:00",
                "entry_cutoff_values": "",
                "session_end_values": "19:00,19:30",
            }
        )

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["entry_cutoff"], time(19, 0))
        self.assertEqual(profiles[0]["session_end"], time(19, 30))

    def test_fast_optimizer_top_rows_skip_zero_trade_settings(self) -> None:
        metrics = np.asarray(
            [
                [0, 0, 0, 0, 0, 0, 0],
                [3, 2, 1, 20, -10, 10, -2],
                [0, 0, 0, 0, 0, 0, 0],
            ],
            dtype=np.float64,
        )

        self.assertEqual(list(optimizer_top_metric_indices(metrics, 1, 50, "BALANCED", 10)), [1])

    def test_optimizer_allows_signed_first_trail_lock_values(self) -> None:
        payload = optimizer_payload(
            {
                "data_source": "MT5",
                "symbol": "TEST",
                "from_date": DAY.isoformat(),
                "to_date": DAY.isoformat(),
                "entry_timeframes": ["M1"],
                "trail_timeframes": ["M1"],
                "entry_patterns": ["BOTH"],
                "range_start_values": ["06:30"],
                "range_duration_values": ["60"],
                "entry_cutoff_values": ["19:00"],
                "session_end_values": ["21:30"],
                "entry_buffer_values": ["0.25"],
                "stop_points_values": ["300"],
                "first_trail_profit_values": ["600"],
                "first_trail_lock_values": ["-300", "300"],
                "second_trail_profit_values": ["2000"],
                "max_combinations": "4",
            }
        )

        self.assertEqual(payload["parameters"]["first_trail_lock_loss"], [-300.0, 300.0])

    def test_optimizer_results_are_saved_and_loaded_from_sqlite(self) -> None:
        payload = optimizer_payload(
            {
                "data_source": "MT5",
                "symbol": "TESTSQL",
                "from_date": DAY.isoformat(),
                "to_date": DAY.isoformat(),
                "entry_timeframes": ["M1"],
                "trail_timeframes": ["M1"],
                "entry_patterns": ["BOTH"],
                "range_start_values": ["06:30"],
                "range_duration_values": ["60"],
                "entry_cutoff_values": ["19:00"],
                "session_end": "21:30",
                "session_end_values": ["21:30"],
                "entry_buffer_values": ["0.25"],
                "stop_points_values": ["10"],
                "first_trail_profit_values": ["400"],
                "first_trail_lock_values": ["0"],
                "second_trail_profit_values": ["700"],
                "target_win_rate": "0",
                "minimum_trades": "1",
                "max_combinations": "1",
            }
        )
        job = {
            "scan_id": "sqlite-unit-scan",
            "source": "MT5",
            "symbol": "TESTSQL",
            "status": "completed",
            "message": "Target reached.",
            "tested": 1,
            "total": 1,
            "progress_pct": 100.0,
            "target_win_rate": payload["target_win_rate"],
            "minimum_trades": payload["minimum_trades"],
            "result_sort": payload["result_sort"],
            "save_full_csv": payload["save_full_csv"],
            "created_at": "2026-05-15T00:00:00+00:00",
            "updated_at": "2026-05-15T00:00:01+00:00",
            "target_found": True,
            "result_count": 1,
            "results_truncated": False,
        }
        row = {
            "rank": 1,
            "qualified": True,
            "entry_pattern": "BOTH",
            "timeframe": "M1",
            "trail_timeframe": "M1",
            "range_start": "06:30",
            "range_end": "07:30",
            "session_start": "07:30",
            "entry_cutoff": "19:00",
            "session_end": "21:30",
            "entry_buffer_pct": 0.25,
            "stop_points": 10.0,
            "stop_points_unit": "POINTS",
            "first_trail_profit": 400.0,
            "first_trail_profit_unit": "POINTS",
            "first_trail_lock_loss": 0.0,
            "first_trail_lock_loss_unit": "POINTS",
            "second_trail_profit": 700.0,
            "second_trail_profit_unit": "POINTS",
            "total_trades": 1,
            "wins": 1,
            "losses": 0,
            "win_rate_pct": 100.0,
            "net_points": 10.0,
            "profit_factor": None,
            "max_drawdown_points": 0.0,
        }

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            server_module, "OPTIMIZER_DB_FILE", Path(temp_dir) / "optimizer.sqlite3"
        ):
            save_optimizer_output(job, payload, [row], total_result_count=1)

            records = saved_optimizer_scans("MT5", "TESTSQL")
            loaded = load_saved_optimizer_scan("MT5", "TESTSQL", "sqlite-unit-scan")

        self.assertEqual(len(records), 1)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["scan_config"]["engine_version"], server_module.OPTIMIZER_ENGINE_VERSION)
        self.assertEqual(loaded["results"][0]["total_trades"], 1)
        self.assertEqual(loaded["results"][0]["net_points"], 10.0)

    def test_optimizer_keeps_full_signal_candle_execution_window_after_cutoff(self) -> None:
        signal_h4 = candle_frame(
            [
                ("06:30", 95.0, 100.0, 90.0, 95.0),
                ("07:30", 95.0, 99.0, 91.0, 95.0),
                ("11:30", 95.0, 99.0, 91.0, 95.0),
                ("15:30", 95.0, 101.0, 95.0, 100.0),
            ]
        )
        execution_m1 = candle_frame(
            [
                ("07:30", 95.0, 99.0, 91.0, 95.0),
                ("18:59", 95.0, 99.0, 91.0, 95.0),
                ("19:15", 100.0, 101.0, 100.0, 100.5),
                ("21:30", 100.5, 100.5, 100.5, 100.5),
            ]
        )

        def fake_fetch(config):
            if config.timeframe == "H4":
                return signal_h4.copy()
            if config.timeframe == "M1":
                return execution_m1.copy()
            raise AssertionError(f"Unexpected timeframe: {config.timeframe}")

        def fake_save(job, payload, rows, **kwargs):
            stored = server_module.optimizer_public_result(job, list(rows))
            total_result_count = kwargs.get("total_result_count")
            if total_result_count is not None:
                stored["result_count"] = total_result_count
                stored["results_truncated"] = total_result_count > server_module.OPTIMIZER_VISIBLE_RESULTS
            return stored

        payload = optimizer_payload(
            {
                "data_source": "MT5",
                "symbol": "TEST",
                "from_date": DAY.isoformat(),
                "to_date": DAY.isoformat(),
                "entry_timeframes": ["H4"],
                "trail_timeframes": ["M1"],
                "entry_patterns": ["BOTH"],
                "range_start_values": ["06:30"],
                "range_duration_values": ["60"],
                "entry_cutoff_values": ["19:00"],
                "session_end": "21:30",
                "session_end_values": ["21:30"],
                "entry_buffer_values": ["0.25"],
                "stop_points_values": ["10"],
                "first_trail_profit_values": ["400"],
                "first_trail_lock_values": ["0"],
                "second_trail_profit_values": ["700"],
                "target_win_rate": "0",
                "minimum_trades": "1",
                "max_combinations": "1",
            }
        )
        job_id = "unit-test-signal-cutoff"
        created_at = "2026-05-15T00:00:00+00:00"
        server_module._optimizer_jobs[job_id] = {
            "scan_id": job_id,
            "source": "MT5",
            "symbol": "TEST",
            "status": "queued",
            "message": "Scan queued...",
            "tested": 0,
            "total": payload["total"],
            "progress_pct": 0.0,
            "target_win_rate": payload["target_win_rate"],
            "minimum_trades": payload["minimum_trades"],
            "result_sort": payload["result_sort"],
            "save_full_csv": payload["save_full_csv"],
            "created_at": created_at,
            "updated_at": created_at,
            "result_count": 0,
            "results_truncated": False,
            "results": [],
        }

        try:
            with patch.object(server_module, "fetch_source_rates", side_effect=fake_fetch), patch.object(
                server_module, "save_optimizer_output", side_effect=fake_save
            ):
                execute_optimizer(job_id, payload)
            job = server_module._optimizer_jobs[job_id]
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["results"][0]["total_trades"], 1)
        finally:
            server_module._optimizer_jobs.pop(job_id, None)


if __name__ == "__main__":
    unittest.main()
