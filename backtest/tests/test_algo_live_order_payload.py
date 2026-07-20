from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from algo.python import algo_worker  # noqa: E402


class FakeResult(SimpleNamespace):
    def _asdict(self):
        return dict(self.__dict__)


class FakeMt5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_STOP = 5
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    POSITION_TYPE_BUY = 0

    def __init__(self):
        self.requests: list[dict] = []

    def initialize(self):
        return True

    def shutdown(self):
        return None

    def last_error(self):
        return (0, "OK")

    def symbol_select(self, _symbol, _enable):
        return True

    def symbol_info_tick(self, _symbol):
        return SimpleNamespace(ask=100.0, bid=99.5, last=99.75)

    def symbol_info(self, _symbol):
        return SimpleNamespace(point=0.01, digits=2, trade_stops_level=0, volume_min=0.01, volume_max=100.0, volume_step=0.01)

    def positions_get(self, symbol=None):
        return []

    def orders_get(self, symbol=None):
        return []

    def order_check(self, request):
        return FakeResult(retcode=0, comment="OK")

    def order_send(self, request):
        self.requests.append(dict(request))
        return FakeResult(retcode=self.TRADE_RETCODE_DONE, comment="Done", order=12345, deal=67890)


def memory_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE trade_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL,
            stop_loss REAL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            day_key TEXT NOT NULL,
            level TEXT NOT NULL,
            event TEXT NOT NULL,
            strategy_id TEXT NOT NULL DEFAULT '',
            symbol TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE runtime (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            pending_order_day TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO runtime (id) VALUES (1);
        """
    )
    return connection


def strategy(**overrides):
    base = {
        "id": "test-strategy",
        "symbol": "XAUUSD",
        "data_source": "MT5",
        "entry_pattern": "BOTH",
        "stop_points": 5.0,
        "stop_points_unit": "POINTS",
        "first_trail_profit": 7.0,
        "first_trail_profit_unit": "POINTS",
        "first_trail_lock_loss": 2.0,
        "first_trail_lock_loss_unit": "POINTS",
        "second_trail_profit": 12.0,
        "second_trail_profit_unit": "POINTS",
        "target_points": 10.0,
        "volume": 0.01,
        "live_trading_enabled": True,
        "max_open_positions": 1,
        "max_trades_per_day": 1,
        "trail_timeframe": "M5",
    }
    return {**base, **overrides}


class AlgoLiveOrderPayloadTest(unittest.TestCase):
    def setUp(self):
        self.original_mt5 = algo_worker.mt5
        self.fake_mt5 = FakeMt5()
        algo_worker.mt5 = self.fake_mt5

    def tearDown(self):
        algo_worker.mt5 = self.original_mt5

    def test_market_buy_order_sends_sl_and_tp_to_mt5(self):
        connection = memory_connection()
        try:
            action = algo_worker.execute_mt5_order(
                connection,
                strategy(),
                {"side": "BUY", "stop_loss": 95.0, "entry_reference": 100.0, "trigger_candle_time": "2026-07-20T09:30:00+05:30"},
            )
        finally:
            connection.close()

        request = self.fake_mt5.requests[-1]
        self.assertEqual(action["status"], "ORDER_PLACED")
        self.assertEqual(request["action"], self.fake_mt5.TRADE_ACTION_DEAL)
        self.assertEqual(request["type"], self.fake_mt5.ORDER_TYPE_BUY)
        self.assertEqual(request["sl"], 95.0)
        self.assertEqual(request["tp"], 110.0)

    def test_market_sell_order_sends_sl_and_tp_to_mt5(self):
        connection = memory_connection()
        try:
            algo_worker.execute_mt5_order(
                connection,
                strategy(),
                {"side": "SELL", "stop_loss": 104.5, "entry_reference": 99.5, "trigger_candle_time": "2026-07-20T09:30:00+05:30"},
            )
        finally:
            connection.close()

        request = self.fake_mt5.requests[-1]
        self.assertEqual(request["action"], self.fake_mt5.TRADE_ACTION_DEAL)
        self.assertEqual(request["type"], self.fake_mt5.ORDER_TYPE_SELL)
        self.assertEqual(request["sl"], 104.5)
        self.assertEqual(request["tp"], 89.5)

    def test_pending_orders_send_buy_and_sell_sl_tp_to_mt5(self):
        connection = memory_connection()
        timestamp = algo_worker.now_ist()
        try:
            result = algo_worker.manage_pending_orders(
                connection,
                strategy(),
                True,
                timestamp,
                timestamp,
                timestamp,
                105.0,
                95.0,
            )
        finally:
            connection.close()

        self.assertEqual(result["status"], "pending_live")
        buy_request = next(item for item in self.fake_mt5.requests if item["type"] == self.fake_mt5.ORDER_TYPE_BUY_STOP)
        sell_request = next(item for item in self.fake_mt5.requests if item["type"] == self.fake_mt5.ORDER_TYPE_SELL_STOP)
        self.assertEqual(buy_request["price"], 105.0)
        self.assertEqual(buy_request["sl"], 100.0)
        self.assertEqual(buy_request["tp"], 115.0)
        self.assertEqual(sell_request["price"], 95.0)
        self.assertEqual(sell_request["sl"], 100.0)
        self.assertEqual(sell_request["tp"], 85.0)

    def test_target_points_zero_means_no_broker_tp_field(self):
        connection = memory_connection()
        try:
            algo_worker.execute_mt5_order(
                connection,
                strategy(target_points=0.0),
                {"side": "BUY", "stop_loss": 95.0, "entry_reference": 100.0, "trigger_candle_time": "2026-07-20T09:30:00+05:30"},
            )
        finally:
            connection.close()

        request = self.fake_mt5.requests[-1]
        self.assertEqual(request["sl"], 95.0)
        self.assertNotIn("tp", request)


if __name__ == "__main__":
    unittest.main()
