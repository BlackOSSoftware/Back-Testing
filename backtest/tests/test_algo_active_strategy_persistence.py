from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from algo.python import algo_worker  # noqa: E402


def make_strategy(strategy_id: str, symbol: str, updated_at: str) -> dict:
    return algo_worker.normalize_strategy(
        {
            "id": strategy_id,
            "name": f"{symbol} M5 breakout",
            "symbol": symbol,
            "timeframe": "M5",
            "trail_timeframe": "M5",
            "entry_pattern": "BOTH",
            "from_date": "2026-06-01",
            "to_date": "2026-06-30",
            "range_start": "08:30",
            "range_end": "09:30",
            "session_start": "09:30",
            "entry_cutoff": "18:00",
            "session_end": "19:30",
            "entry_buffer_pct": 0.25,
            "stop_points": 5.0,
            "first_trail_profit": 7.0,
            "first_trail_lock_loss": 2.0,
            "second_trail_profit": 12.0,
            "volume": 0.01,
            "updated_at": updated_at,
        },
        strategy_id,
    )


class AlgoActiveStrategyPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="algo-active-test-"))
        self.originals = {
            "DATA_DIR": algo_worker.DATA_DIR,
            "INSTANCE_DIR": algo_worker.INSTANCE_DIR,
            "DB_FILE": algo_worker.DB_FILE,
            "LEGACY_JSON": algo_worker.LEGACY_JSON,
            "mt5": algo_worker.mt5,
        }
        algo_worker.DATA_DIR = self.temp_dir / "data"
        algo_worker.INSTANCE_DIR = self.temp_dir / "instance"
        algo_worker.DB_FILE = algo_worker.DATA_DIR / "algo.sqlite3"
        algo_worker.LEGACY_JSON = algo_worker.INSTANCE_DIR / "algo.json"
        algo_worker.mt5 = None

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(algo_worker, name, value)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def seed_two_strategies(self):
        connection = algo_worker.connect()
        with connection:
            algo_worker.upsert_strategy(connection, make_strategy("saved-active", "XAUUSD", "2026-07-01T00:00:00+00:00"))
            algo_worker.upsert_strategy(connection, make_strategy("newer-default", "BTCUSD", "2026-07-02T00:00:00+00:00"))
        return connection

    def test_start_without_strategy_uses_saved_active_not_latest_updated(self):
        connection = self.seed_two_strategies()
        with connection:
            connection.execute("UPDATE runtime SET running = 0, active_strategy_id = ? WHERE id = 1", ("saved-active",))

        result = algo_worker.command_control(connection, {"action": "start"})

        self.assertTrue(result["running"])
        self.assertEqual(result["active_strategy_id"], "saved-active")
        self.assertEqual(result["active_strategy"]["symbol"], "XAUUSD")
        connection.close()

    def test_select_persists_active_strategy_when_stopped(self):
        connection = self.seed_two_strategies()
        with connection:
            connection.execute("UPDATE runtime SET running = 0, active_strategy_id = ? WHERE id = 1", ("newer-default",))

        result = algo_worker.command_control(connection, {"action": "select", "strategy_id": "saved-active"})
        connection.close()

        reopened = algo_worker.connect()
        try:
            state = algo_worker.runtime_state(reopened)
            self.assertEqual(result["selection"]["status"], "STRATEGY_SELECTED")
            self.assertEqual(state["active_strategy_id"], "saved-active")
            self.assertEqual(state["active_strategy"]["symbol"], "XAUUSD")
        finally:
            reopened.close()

    def test_startup_restores_legacy_active_when_runtime_active_is_blank(self):
        algo_worker.INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
        algo_worker.LEGACY_JSON.write_text(json.dumps({"active_strategy_id": "saved-active"}), encoding="utf-8")
        connection = self.seed_two_strategies()
        with connection:
            connection.execute("UPDATE runtime SET active_strategy_id = '' WHERE id = 1")
        connection.close()

        reopened = algo_worker.connect()
        try:
            state = algo_worker.runtime_state(reopened)
            self.assertEqual(state["active_strategy_id"], "saved-active")
            self.assertEqual(state["active_strategy"]["symbol"], "XAUUSD")
        finally:
            reopened.close()

    def test_clear_all_removes_saved_strategies_runtime_and_logs(self):
        connection = self.seed_two_strategies()
        with connection:
            connection.execute("UPDATE runtime SET running = 1, active_strategy_id = ?, last_signal_json = ? WHERE id = 1", ("saved-active", '{"side":"BUY"}'))
            connection.execute(
                "INSERT INTO signal_log (created_at, strategy_id, symbol, timeframe, side, status, message, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-07-20T09:30:00+05:30", "saved-active", "XAUUSD", "M5", "BUY", "BUY signal", "test", "{}"),
            )
            connection.execute(
                "INSERT INTO trade_log (created_at, strategy_id, symbol, side, entry_price, stop_loss, status, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-07-20T09:31:00+05:30", "saved-active", "XAUUSD", "BUY", 100.0, 95.0, "ORDER_PLACED", "{}"),
            )

        result = algo_worker.command_control(connection, {"action": "clear_all"})

        self.assertEqual(result["cleared"]["status"], "ALL_SETTINGS_CLEARED")
        self.assertFalse(result["running"])
        self.assertEqual(result["active_strategy_id"], "")
        self.assertIsNone(result["active_strategy"])
        self.assertEqual(result["strategies"], [])
        self.assertEqual(result["signal_log"], [])
        self.assertEqual(result["trade_log"], [])
        self.assertEqual(json.loads(connection.execute("SELECT last_signal_json FROM runtime WHERE id = 1").fetchone()["last_signal_json"]), {})
        connection.close()


if __name__ == "__main__":
    unittest.main()
