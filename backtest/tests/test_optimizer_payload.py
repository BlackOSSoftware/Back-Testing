from __future__ import annotations

import sys
import unittest
from datetime import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.server import scan_time_profiles  # noqa: E402


class OptimizerPayloadTest(unittest.TestCase):
    def test_blank_optimizer_cutoff_uses_setup_last_entry_time(self) -> None:
        profiles = scan_time_profiles(
            {
                "range_start_values": "08:30",
                "range_duration_values": "60",
                "entry_cutoff": "19:00",
                "entry_cutoff_values": "",
                "session_end_values": "21:30",
            }
        )

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["entry_cutoff"], time(19, 0))
        self.assertEqual(profiles[0]["session_end"], time(21, 30))


if __name__ == "__main__":
    unittest.main()
