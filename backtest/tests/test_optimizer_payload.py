from __future__ import annotations

import sys
import unittest
from datetime import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.server import optimizer_top_metric_indices, scan_time_profiles  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
