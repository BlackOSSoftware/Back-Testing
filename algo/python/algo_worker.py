from __future__ import annotations

import json
import sqlite3
import sys
import traceback
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None


ROOT_DIR = Path(__file__).resolve().parents[1]
INSTANCE_DIR = ROOT_DIR / "instance"
DATA_DIR = ROOT_DIR / "data"
DB_FILE = DATA_DIR / "algo.sqlite3"
LEGACY_JSON = INSTANCE_DIR / "algo.json"
IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
PRICE_EPSILON = 1e-9
FALLBACK_SYMBOLS = ["BTCUSD#", "BTCUSD", "ETHUSD", "XAUUSD", "GOLD.i#", "US30", "NAS100", "SPX500"]
TRADE_MAGIC = 260624
ORDER_DEVIATION = 50


TIMEFRAMES = {
    "M1": "TIMEFRAME_M1",
    "M2": "TIMEFRAME_M2",
    "M3": "TIMEFRAME_M3",
    "M4": "TIMEFRAME_M4",
    "M5": "TIMEFRAME_M5",
    "M10": "TIMEFRAME_M10",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
}


DEFAULT_STRATEGY = {
    "name": "XAUUSD M5 breakout",
    "data_source": "MT5",
    "symbol": "XAUUSD",
    "timeframe": "M5",
    "trail_timeframe": "M5",
    "entry_pattern": "BOTH",
    "range_start": "08:30",
    "range_end": "09:30",
    "session_start": "09:30",
    "entry_cutoff": "18:00",
    "session_end": "19:30",
    "entry_buffer_pct": 0.25,
    "entry_buffer_points": 0.0,
    "stop_points": 500.0,
    "first_trail_profit": 700.0,
    "first_trail_lock_loss": 200.0,
    "second_trail_profit": 700.0,
    "volume": 0.01,
    "target_points": 0.0,
    "max_trades_per_day": 1,
    "max_open_positions": 1,
    "live_trading_enabled": 0,
}


def now_utc() -> str:
    return datetime.now(UTC).isoformat()


def now_ist() -> datetime:
    return datetime.now(IST)


def parse_time(value: str) -> time:
    return datetime.strptime(str(value), "%H:%M").time()


def output(data: dict[str, Any]) -> None:
    print(json.dumps(data, separators=(",", ":"), default=str))


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_FILE, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS runtime (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            running INTEGER NOT NULL DEFAULT 0,
            active_strategy_id TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL DEFAULT '',
            stopped_at TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            algo_status TEXT NOT NULL DEFAULT 'Ready.',
            pending_order_day TEXT NOT NULL DEFAULT '',
            last_signal_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS strategies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            data_source TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            trail_timeframe TEXT NOT NULL,
            entry_pattern TEXT NOT NULL,
            range_start TEXT NOT NULL,
            range_end TEXT NOT NULL,
            session_start TEXT NOT NULL,
            entry_cutoff TEXT NOT NULL,
            session_end TEXT NOT NULL,
            entry_buffer_pct REAL NOT NULL,
            entry_buffer_points REAL NOT NULL DEFAULT 0,
            stop_points REAL NOT NULL,
            first_trail_profit REAL NOT NULL,
            first_trail_lock_loss REAL NOT NULL,
            second_trail_profit REAL NOT NULL,
            volume REAL NOT NULL,
            target_points REAL NOT NULL DEFAULT 0,
            max_trades_per_day INTEGER NOT NULL DEFAULT 1,
            max_open_positions INTEGER NOT NULL DEFAULT 1,
            live_trading_enabled INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS signal_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trade_log (
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
        CREATE INDEX IF NOT EXISTS idx_signal_log_time ON signal_log(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_trade_log_time ON trade_log(created_at DESC);
        """
    )
    ensure_column(connection, "strategies", "live_trading_enabled", "INTEGER NOT NULL DEFAULT 0")
    connection.execute(
        """
        INSERT OR IGNORE INTO runtime (id, running, algo_status)
        VALUES (1, 0, 'Ready.')
        """
    )
    return connection


def ensure_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def row_to_strategy(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "data_source": row["data_source"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "trail_timeframe": row["trail_timeframe"],
        "entry_pattern": row["entry_pattern"],
        "range_start": row["range_start"],
        "range_end": row["range_end"],
        "session_start": row["session_start"],
        "entry_cutoff": row["entry_cutoff"],
        "session_end": row["session_end"],
        "entry_buffer_pct": row["entry_buffer_pct"],
        "entry_buffer_points": row["entry_buffer_points"],
        "stop_points": row["stop_points"],
        "first_trail_profit": row["first_trail_profit"],
        "first_trail_lock_loss": row["first_trail_lock_loss"],
        "second_trail_profit": row["second_trail_profit"],
        "volume": row["volume"],
        "target_points": row["target_points"],
        "max_trades_per_day": row["max_trades_per_day"],
        "max_open_positions": row["max_open_positions"],
        "live_trading_enabled": bool(row["live_trading_enabled"]),
        "updated_at": row["updated_at"],
    }


def normalize_strategy(values: dict[str, Any], existing_id: str | None = None) -> dict[str, Any]:
    merged = {**DEFAULT_STRATEGY, **(values or {})}
    strategy_id = existing_id or str(merged.get("id") or f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{id(merged) & 0xFFFFFF:x}")
    symbol = str(merged.get("symbol") or "").strip()
    if not symbol:
        raise ValueError("Symbol is required.")
    timeframe = str(merged.get("timeframe", "M5")).upper()
    trail_timeframe = str(merged.get("trail_timeframe") or timeframe).upper()
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if trail_timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported trail timeframe: {trail_timeframe}")
    entry_pattern = str(merged.get("entry_pattern", "BOTH")).upper()
    if entry_pattern not in {"BOTH", "BUY_ONLY", "SELL_ONLY"}:
        raise ValueError("Entry pattern must be BOTH, BUY_ONLY or SELL_ONLY.")
    for key in ("range_start", "range_end", "session_start", "entry_cutoff", "session_end"):
        parse_time(str(merged[key]))
    return {
        "id": strategy_id,
        "name": str(merged.get("name") or f"{symbol} {timeframe} breakout").strip(),
        "data_source": str(merged.get("data_source", "MT5")).upper(),
        "symbol": symbol,
        "timeframe": timeframe,
        "trail_timeframe": trail_timeframe,
        "entry_pattern": entry_pattern,
        "range_start": str(merged["range_start"]),
        "range_end": str(merged["range_end"]),
        "session_start": str(merged["session_start"]),
        "entry_cutoff": str(merged["entry_cutoff"]),
        "session_end": str(merged["session_end"]),
        "entry_buffer_pct": float(merged.get("entry_buffer_pct", 0.25)),
        "entry_buffer_points": float(merged.get("entry_buffer_points", 0.0)),
        "stop_points": float(merged.get("stop_points", 500.0)),
        "first_trail_profit": float(merged.get("first_trail_profit", 700.0)),
        "first_trail_lock_loss": float(merged.get("first_trail_lock_loss", 200.0)),
        "second_trail_profit": float(merged.get("second_trail_profit", 700.0)),
        "volume": float(merged.get("volume", 0.01)),
        "target_points": float(merged.get("target_points", 0.0)),
        "max_trades_per_day": int(merged.get("max_trades_per_day", 1)),
        "max_open_positions": int(merged.get("max_open_positions", 1)),
        "live_trading_enabled": 1 if merged.get("live_trading_enabled") else 0,
        "updated_at": str(merged.get("updated_at") or now_utc()),
    }


def upsert_strategy(connection: sqlite3.Connection, strategy: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO strategies (
            id, name, data_source, symbol, timeframe, trail_timeframe, entry_pattern,
            range_start, range_end, session_start, entry_cutoff, session_end,
            entry_buffer_pct, entry_buffer_points, stop_points, first_trail_profit,
            first_trail_lock_loss, second_trail_profit, volume, target_points,
            max_trades_per_day, max_open_positions, live_trading_enabled, updated_at
        )
        VALUES (
            :id, :name, :data_source, :symbol, :timeframe, :trail_timeframe, :entry_pattern,
            :range_start, :range_end, :session_start, :entry_cutoff, :session_end,
            :entry_buffer_pct, :entry_buffer_points, :stop_points, :first_trail_profit,
            :first_trail_lock_loss, :second_trail_profit, :volume, :target_points,
            :max_trades_per_day, :max_open_positions, :live_trading_enabled, :updated_at
        )
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            data_source=excluded.data_source,
            symbol=excluded.symbol,
            timeframe=excluded.timeframe,
            trail_timeframe=excluded.trail_timeframe,
            entry_pattern=excluded.entry_pattern,
            range_start=excluded.range_start,
            range_end=excluded.range_end,
            session_start=excluded.session_start,
            entry_cutoff=excluded.entry_cutoff,
            session_end=excluded.session_end,
            entry_buffer_pct=excluded.entry_buffer_pct,
            entry_buffer_points=excluded.entry_buffer_points,
            stop_points=excluded.stop_points,
            first_trail_profit=excluded.first_trail_profit,
            first_trail_lock_loss=excluded.first_trail_lock_loss,
            second_trail_profit=excluded.second_trail_profit,
            volume=excluded.volume,
            target_points=excluded.target_points,
            max_trades_per_day=excluded.max_trades_per_day,
            max_open_positions=excluded.max_open_positions,
            live_trading_enabled=excluded.live_trading_enabled,
            updated_at=excluded.updated_at
        """,
        strategy,
    )


def migrate_legacy(connection: sqlite3.Connection) -> None:
    existing_count = connection.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    if existing_count:
        return
    if LEGACY_JSON.exists():
        try:
            legacy = json.loads(LEGACY_JSON.read_text(encoding="utf-8"))
        except Exception:
            legacy = {}
    else:
        legacy = {}
    strategies = legacy.get("strategies") or [DEFAULT_STRATEGY]
    for item in strategies:
        upsert_strategy(connection, normalize_strategy(item, str(item.get("id") or "")))
    active = str(legacy.get("active_strategy_id") or "")
    running = 1 if legacy.get("running") else 0
    last_signal = legacy.get("last_signal") or {}
    connection.execute(
        """
        UPDATE runtime
        SET running = ?, active_strategy_id = ?, started_at = ?, stopped_at = ?,
            last_error = ?, algo_status = ?, pending_order_day = ?, last_signal_json = ?
        WHERE id = 1
        """,
        (
            running,
            active,
            str(legacy.get("started_at") or ""),
            str(legacy.get("stopped_at") or ""),
            str(legacy.get("last_error") or ""),
            str(legacy.get("algo_status") or "Migrated from algo.json."),
            str(legacy.get("pending_order_day") or ""),
            json.dumps(last_signal, separators=(",", ":")),
        ),
    )
    for item in legacy.get("signal_log") or []:
        payload = item if isinstance(item, dict) else {"message": str(item)}
        connection.execute(
            """
            INSERT INTO signal_log (created_at, strategy_id, symbol, timeframe, side, status, message, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(payload.get("checked_at") or payload.get("created_at") or now_utc()),
                str(payload.get("strategy_id") or active),
                str(payload.get("symbol") or ""),
                str(payload.get("timeframe") or ""),
                str(payload.get("side") or ""),
                str(payload.get("status") or ""),
                str(payload.get("message") or ""),
                json.dumps(payload, separators=(",", ":")),
            ),
        )
    for item in legacy.get("trade_log") or []:
        payload = item if isinstance(item, dict) else {"message": str(item)}
        connection.execute(
            """
            INSERT INTO trade_log (created_at, strategy_id, symbol, side, entry_price, stop_loss, status, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(payload.get("created_at") or now_utc()),
                str(payload.get("strategy_id") or active),
                str(payload.get("symbol") or ""),
                str(payload.get("side") or ""),
                payload.get("entry_price"),
                payload.get("stop_loss"),
                str(payload.get("status") or ""),
                json.dumps(payload, separators=(",", ":")),
            ),
        )


def init_db() -> sqlite3.Connection:
    connection = connect()
    with connection:
        migrate_legacy(connection)
    return connection


def list_strategies(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute("SELECT * FROM strategies ORDER BY updated_at DESC").fetchall()
    return [row_to_strategy(row) for row in rows]


def runtime_state(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM runtime WHERE id = 1").fetchone()
    last_signal = {}
    if row["last_signal_json"]:
        try:
            last_signal = json.loads(row["last_signal_json"])
        except Exception:
            last_signal = {}
    active = None
    if row["active_strategy_id"]:
        active_row = connection.execute("SELECT * FROM strategies WHERE id = ?", (row["active_strategy_id"],)).fetchone()
        active = row_to_strategy(active_row) if active_row else None
    live_quote = fetch_live_quote(active["symbol"]) if active and active.get("data_source") == "MT5" else {}
    signals = [
        {**dict(item), "payload": json.loads(item["payload_json"] or "{}")}
        for item in connection.execute("SELECT * FROM signal_log ORDER BY created_at DESC LIMIT 50").fetchall()
    ]
    trades = [
        {**dict(item), "payload": json.loads(item["payload_json"] or "{}")}
        for item in connection.execute("SELECT * FROM trade_log ORDER BY created_at DESC LIMIT 50").fetchall()
    ]
    for item in signals:
        item.pop("payload_json", None)
    for item in trades:
        item.pop("payload_json", None)
    return {
        "running": bool(row["running"]),
        "active_strategy_id": row["active_strategy_id"],
        "active_strategy": active,
        "started_at": row["started_at"],
        "stopped_at": row["stopped_at"],
        "last_error": row["last_error"],
        "algo_status": row["algo_status"],
        "pending_order_day": row["pending_order_day"],
        "last_signal": last_signal,
        "live_quote": live_quote,
        "strategies": list_strategies(connection),
        "signal_log": signals,
        "trade_log": trades,
        "database": str(DB_FILE),
    }


def mt5_timeframe(name: str) -> Any:
    if mt5 is None:
        raise RuntimeError("MetaTrader5 Python package is not installed.")
    attr = TIMEFRAMES.get(str(name).upper())
    if not attr or not hasattr(mt5, attr):
        raise RuntimeError(f"Unsupported MT5 timeframe: {name}")
    return getattr(mt5, attr)


def fetch_live_quote(symbol: str) -> dict[str, Any]:
    if mt5 is None or not symbol:
        return {}
    if not mt5.initialize():
        return {"symbol": symbol, "error": f"MT5 initialize failed: {mt5.last_error()}"}
    try:
        if not mt5.symbol_select(symbol, True):
            return {"symbol": symbol, "error": f"Symbol not available in MT5 Market Watch: {symbol}"}
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return {"symbol": symbol, "error": f"No live tick returned for {symbol}"}
        point = float(getattr(info, "point", 0.0) or 0.0) if info is not None else 0.0
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        last = float(getattr(tick, "last", 0.0) or 0.0)
        quote_time = datetime.fromtimestamp(int(getattr(tick, "time", 0) or 0), tz=UTC).astimezone(IST)
        return {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "last": last or bid,
            "point": point,
            "spread": ask - bid if ask and bid else None,
            "time": quote_time.isoformat(),
        }
    finally:
        mt5.shutdown()


def fetch_mt5_candles(strategy: dict[str, Any], day: datetime) -> list[dict[str, Any]]:
    if mt5 is None:
        raise RuntimeError("MetaTrader5 Python package is not installed.")
    timeframe = mt5_timeframe(strategy["timeframe"])
    symbol = strategy["symbol"]
    start = datetime.combine(day.date(), time(0, 0), tzinfo=IST)
    end = start + timedelta(days=1)
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Symbol not available in MT5 Market Watch: {symbol}")
        rates = mt5.copy_rates_range(symbol, timeframe, start.astimezone(UTC), end.astimezone(UTC))
    finally:
        mt5.shutdown()
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No MT5 candle data returned for {symbol} {strategy['timeframe']}.")
    candles: list[dict[str, Any]] = []
    for row in rates:
        candle_time = datetime.fromtimestamp(int(row["time"]), tz=UTC).astimezone(IST)
        candles.append(
            {
                "time_ist": candle_time,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
        )
    candles.sort(key=lambda item: item["time_ist"])
    return candles


def choose_signal(candle: dict[str, Any], buy_trigger: float, sell_trigger: float, pattern: str) -> str:
    buy_hit = float(candle["high"]) + PRICE_EPSILON >= buy_trigger
    sell_hit = float(candle["low"]) <= sell_trigger + PRICE_EPSILON
    if pattern == "BUY_ONLY":
        return "BUY" if buy_hit else ""
    if pattern == "SELL_ONLY":
        return "SELL" if sell_hit else ""
    if buy_hit and not sell_hit:
        return "BUY"
    if sell_hit and not buy_hit:
        return "SELL"
    if buy_hit and sell_hit:
        open_price = float(candle["open"])
        return "BUY" if abs(buy_trigger - open_price) <= abs(open_price - sell_trigger) else "SELL"
    return ""


def first_trail_lock_stop(entry_price: float, lock_distance: float, side: str) -> float:
    effective_lock = max(float(lock_distance), 0.0)
    return entry_price + effective_lock if side == "BUY" else entry_price - effective_lock


def scan_signal(connection: sqlite3.Connection, strategy_id: str | None = None) -> dict[str, Any]:
    if strategy_id:
        row = connection.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    else:
        runtime = connection.execute("SELECT active_strategy_id FROM runtime WHERE id = 1").fetchone()
        row = connection.execute("SELECT * FROM strategies WHERE id = ?", (runtime["active_strategy_id"],)).fetchone()
    if row is None:
        raise ValueError("Active strategy not found.")
    strategy = row_to_strategy(row)
    runtime = connection.execute("SELECT running FROM runtime WHERE id = 1").fetchone()
    running = bool(runtime["running"]) if runtime else False
    checked_at = now_ist()
    if strategy["data_source"] != "MT5":
        result = {
            "checked_at": checked_at.isoformat(),
            "strategy_id": strategy["id"],
            "symbol": strategy["symbol"],
            "timeframe": strategy["timeframe"],
            "phase": "WAITING",
            "status": "MT5 only",
            "message": "Live algo scanner currently supports local MT5 data source.",
            "side": "",
        }
        save_signal_result(connection, result)
        return result

    range_start = parse_time(strategy["range_start"])
    range_end = parse_time(strategy["range_end"])
    session_start = parse_time(strategy["session_start"])
    entry_cutoff = parse_time(strategy["entry_cutoff"])
    session_end = parse_time(strategy["session_end"])
    candles = fetch_mt5_candles(strategy, checked_at)
    day = checked_at.date()
    range_left = datetime.combine(day, range_start, tzinfo=IST)
    range_right = datetime.combine(day, range_end, tzinfo=IST)
    session_left = datetime.combine(day, session_start, tzinfo=IST)
    cutoff_right = datetime.combine(day, entry_cutoff, tzinfo=IST)
    session_right = datetime.combine(day, session_end, tzinfo=IST)

    range_candles = [item for item in candles if range_left <= item["time_ist"] < range_right]
    session_candles = [item for item in candles if session_left <= item["time_ist"] <= min(checked_at, session_right)]
    result = {
        "checked_at": checked_at.isoformat(),
        "strategy_id": strategy["id"],
        "symbol": strategy["symbol"],
        "timeframe": strategy["timeframe"],
        "phase": "WAIT_RANGE",
        "status": "Waiting",
        "message": "Waiting for range candles.",
        "side": "",
    }
    if not range_candles:
        save_signal_result(connection, result)
        return result
    range_high = max(float(item["high"]) for item in range_candles)
    range_low = min(float(item["low"]) for item in range_candles)
    buy_trigger = range_high * (1 + strategy["entry_buffer_pct"] / 100) + strategy["entry_buffer_points"]
    sell_trigger = range_low * (1 - strategy["entry_buffer_pct"] / 100) - strategy["entry_buffer_points"]
    result.update(
        {
            "last_candle_time": candles[-1]["time_ist"].isoformat() if candles else "",
            "last_close": candles[-1]["close"] if candles else None,
            "range_high": range_high,
            "range_low": range_low,
            "buy_trigger": buy_trigger,
            "sell_trigger": sell_trigger,
            "buffer": f"{strategy['entry_buffer_pct']}%",
        }
    )
    if checked_at < range_right:
        result.update({"message": "Range window is still forming."})
        save_signal_result(connection, result)
        return result
    if checked_at < session_left:
        result.update({"phase": "WAIT_SESSION", "message": "Waiting for session start."})
        save_signal_result(connection, result)
        return result
    if checked_at > session_right:
        result.update({"phase": "SESSION_DONE", "status": "Done", "message": "Session finished."})
        save_signal_result(connection, result)
        return result

    eligible = [item for item in session_candles if item["time_ist"] <= cutoff_right]
    result.update({"phase": "SIGNAL", "status": "No signal", "message": "No trigger crossed yet."})
    for candle in eligible:
        side = choose_signal(candle, buy_trigger, sell_trigger, strategy["entry_pattern"])
        if not side:
            continue
        entry_reference = buy_trigger if side == "BUY" else sell_trigger
        stop_loss = entry_reference - strategy["stop_points"] if side == "BUY" else entry_reference + strategy["stop_points"]
        first_trail_trigger = (
            entry_reference + strategy["first_trail_profit"]
            if side == "BUY"
            else entry_reference - strategy["first_trail_profit"]
        )
        first_trail_stop = first_trail_lock_stop(entry_reference, strategy["first_trail_lock_loss"], side)
        result.update(
            {
                "status": f"{side} signal",
                "message": f"{side} trigger crossed.",
                "side": side,
                "entry_reference": entry_reference,
                "stop_loss": stop_loss,
                "first_trail_trigger": first_trail_trigger,
                "first_trail_stop": first_trail_stop,
                "trigger_candle_time": candle["time_ist"].isoformat(),
            }
        )
        break
    if result.get("side"):
        result["live_quote"] = fetch_live_quote(strategy["symbol"])
        result["trade_action"] = maybe_execute_live_trade(connection, strategy, result, running)
    save_signal_result(connection, result)
    return result


def save_signal_result(connection: sqlite3.Connection, result: dict[str, Any]) -> None:
    connection.execute(
        """
        UPDATE runtime
        SET last_signal_json = ?, algo_status = ?, last_error = ''
        WHERE id = 1
        """,
        (json.dumps(result, separators=(",", ":")), f"{result.get('checked_at', '')} - {result.get('message', '')}"),
    )
    if result.get("side") and not signal_already_logged(connection, result):
        connection.execute(
            """
            INSERT INTO signal_log (created_at, strategy_id, symbol, timeframe, side, status, message, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.get("checked_at") or now_utc(),
                result.get("strategy_id") or "",
                result.get("symbol") or "",
                result.get("timeframe") or "",
                result.get("side") or "",
                result.get("status") or "",
                result.get("message") or "",
                json.dumps(result, separators=(",", ":")),
            ),
        )


def signal_already_logged(connection: sqlite3.Connection, result: dict[str, Any]) -> bool:
    trigger_time = str(result.get("trigger_candle_time") or "")
    if not trigger_time:
        return False
    entry_reference = result.get("entry_reference")
    stop_loss = result.get("stop_loss")
    rows = connection.execute(
        """
        SELECT payload_json
        FROM signal_log
        WHERE strategy_id = ?
          AND symbol = ?
          AND timeframe = ?
          AND side = ?
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (
            result.get("strategy_id") or "",
            result.get("symbol") or "",
            result.get("timeframe") or "",
            result.get("side") or "",
        ),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            continue
        if str(payload.get("trigger_candle_time") or "") != trigger_time:
            continue
        if float(payload.get("entry_reference") or 0) != float(entry_reference or 0):
            continue
        if float(payload.get("stop_loss") or 0) != float(stop_loss or 0):
            continue
        return True
    return False


def parse_iso_date(value: str) -> datetime.date | None:
    try:
        return datetime.fromisoformat(str(value)).astimezone(IST).date()
    except Exception:
        return None


def payload_float(payload: dict[str, Any], key: str) -> float:
    try:
        return float(payload.get(key) or 0)
    except Exception:
        return 0.0


def trade_rows_for_today(connection: sqlite3.Connection, strategy_id: str) -> list[sqlite3.Row]:
    today = now_ist().date()
    rows = connection.execute(
        """
        SELECT *
        FROM trade_log
        WHERE strategy_id = ?
        ORDER BY created_at DESC
        LIMIT 100
        """,
        (strategy_id,),
    ).fetchall()
    return [row for row in rows if parse_iso_date(row["created_at"]) == today]


def signal_trade_log(connection: sqlite3.Connection, result: dict[str, Any]) -> sqlite3.Row | None:
    trigger_time = str(result.get("trigger_candle_time") or "")
    if not trigger_time:
        return None
    rows = connection.execute(
        """
        SELECT *
        FROM trade_log
        WHERE strategy_id = ?
          AND symbol = ?
          AND side = ?
        ORDER BY created_at DESC
        LIMIT 50
        """,
        (
            result.get("strategy_id") or "",
            result.get("symbol") or "",
            result.get("side") or "",
        ),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            continue
        if str(payload.get("trigger_candle_time") or "") != trigger_time:
            continue
        if payload_float(payload, "entry_reference") != float(result.get("entry_reference") or 0):
            continue
        if payload_float(payload, "stop_loss") != float(result.get("stop_loss") or 0):
            continue
        return row
    return None


def maybe_execute_live_trade(
    connection: sqlite3.Connection,
    strategy: dict[str, Any],
    signal: dict[str, Any],
    running: bool,
) -> dict[str, Any]:
    if not running:
        return {"status": "not_running", "message": "Algo is not running."}
    if not strategy.get("live_trading_enabled"):
        return {"status": "disabled", "message": "Auto Trade is disabled in settings."}
    if strategy.get("data_source") != "MT5":
        return {"status": "unsupported", "message": "Live order execution currently supports MT5 only."}

    existing = signal_trade_log(connection, signal)
    if existing is not None:
        return {"status": existing["status"], "message": "Trade already handled for this signal."}

    placed_today = [
        row
        for row in trade_rows_for_today(connection, strategy["id"])
        if str(row["status"]).upper() in {"ORDER_PLACED", "ORDER_DONE", "DONE", "PLACED"}
    ]
    if len(placed_today) >= int(strategy.get("max_trades_per_day") or 1):
        return {"status": "max_trades_hit", "message": "Max trades per day reached."}

    try:
        return execute_mt5_order(connection, strategy, signal)
    except Exception as exc:
        failure = {
            "status": "ORDER_FAILED",
            "message": str(exc),
            "trigger_candle_time": signal.get("trigger_candle_time"),
            "entry_reference": signal.get("entry_reference"),
            "stop_loss": signal.get("stop_loss"),
        }
        save_trade_log(connection, strategy, signal, failure, None, None)
        return failure


def mt5_position_count(symbol: str) -> int:
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return 0
    return len(positions)


def execute_mt5_order(
    connection: sqlite3.Connection,
    strategy: dict[str, Any],
    signal: dict[str, Any],
) -> dict[str, Any]:
    if mt5 is None:
        raise RuntimeError("MetaTrader5 Python package is not installed.")
    symbol = strategy["symbol"]
    side = str(signal.get("side") or "").upper()
    if side not in {"BUY", "SELL"}:
        raise RuntimeError("Signal side is missing.")
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Symbol not available in MT5 Market Watch: {symbol}")
        open_count = mt5_position_count(symbol)
        if open_count >= int(strategy.get("max_open_positions") or 1):
            return {"status": "max_open_positions_hit", "message": "Max open positions reached."}

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"No live tick returned for {symbol}.")
        order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
        price = float(tick.ask if side == "BUY" else tick.bid)
        stop_loss = float(signal.get("stop_loss") or 0.0)
        target_points = float(strategy.get("target_points") or 0.0)
        take_profit = 0.0
        if target_points > 0:
            take_profit = price + target_points if side == "BUY" else price - target_points

        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(strategy.get("volume") or 0.01),
            "type": order_type,
            "price": price,
            "sl": stop_loss,
            "deviation": ORDER_DEVIATION,
            "magic": TRADE_MAGIC,
            "comment": f"AlgoDesk {strategy['id'][:8]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if take_profit:
            request["tp"] = take_profit

        order_result = mt5.order_send(request)
        if order_result is None:
            raise RuntimeError(f"MT5 order_send returned no result: {mt5.last_error()}")
        result_data = order_result._asdict() if hasattr(order_result, "_asdict") else dict(order_result)
        success_codes = {
            getattr(mt5, "TRADE_RETCODE_DONE", 10009),
            getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010),
            getattr(mt5, "TRADE_RETCODE_PLACED", 10008),
        }
        status = "ORDER_PLACED" if int(result_data.get("retcode", 0)) in success_codes else "ORDER_FAILED"
        message = str(result_data.get("comment") or result_data.get("retcode") or status)
        action = {
            "status": status,
            "message": message,
            "ticket": result_data.get("order") or result_data.get("deal"),
            "retcode": result_data.get("retcode"),
            "price": price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trigger_candle_time": signal.get("trigger_candle_time"),
            "entry_reference": signal.get("entry_reference"),
        }
        save_trade_log(connection, strategy, signal, action, price, stop_loss)
        return action
    finally:
        mt5.shutdown()


def save_trade_log(
    connection: sqlite3.Connection,
    strategy: dict[str, Any],
    signal: dict[str, Any],
    action: dict[str, Any],
    entry_price: float | None,
    stop_loss: float | None,
) -> None:
    payload = {
        **action,
        "strategy_id": strategy["id"],
        "symbol": strategy["symbol"],
        "side": signal.get("side") or "",
        "trigger_candle_time": signal.get("trigger_candle_time"),
        "entry_reference": signal.get("entry_reference"),
        "stop_loss": stop_loss if stop_loss is not None else signal.get("stop_loss"),
        "checked_at": signal.get("checked_at"),
    }
    connection.execute(
        """
        INSERT INTO trade_log (created_at, strategy_id, symbol, side, entry_price, stop_loss, status, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_ist().isoformat(),
            strategy["id"],
            strategy["symbol"],
            signal.get("side") or "",
            entry_price,
            stop_loss if stop_loss is not None else signal.get("stop_loss"),
            action.get("status") or "",
            json.dumps(payload, separators=(",", ":")),
        ),
    )


def command_status(connection: sqlite3.Connection, _: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **runtime_state(connection)}


def command_strategies(connection: sqlite3.Connection, _: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "strategies": list_strategies(connection)}


def command_strategy_create(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    strategy = normalize_strategy(payload)
    with connection:
        upsert_strategy(connection, strategy)
        connection.execute(
            "UPDATE runtime SET active_strategy_id = CASE WHEN active_strategy_id = '' THEN ? ELSE active_strategy_id END WHERE id = 1",
            (strategy["id"],),
        )
    return {"ok": True, "strategy": strategy}


def command_strategy_update(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    strategy_id = str(payload.get("id") or "")
    if not strategy_id:
        raise ValueError("Strategy id is required.")
    current = connection.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    if current is None:
        raise ValueError("Strategy not found.")
    strategy = normalize_strategy({**row_to_strategy(current), **payload}, strategy_id)
    with connection:
        upsert_strategy(connection, strategy)
    return {"ok": True, "strategy": strategy}


def command_strategy_delete(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    strategy_id = str(payload.get("id") or "")
    if not strategy_id:
        raise ValueError("Strategy id is required.")
    with connection:
        connection.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
        runtime = connection.execute("SELECT active_strategy_id FROM runtime WHERE id = 1").fetchone()
        if runtime["active_strategy_id"] == strategy_id:
            next_row = connection.execute("SELECT id FROM strategies ORDER BY updated_at DESC LIMIT 1").fetchone()
            connection.execute(
                "UPDATE runtime SET active_strategy_id = ?, running = 0 WHERE id = 1",
                (next_row["id"] if next_row else "",),
            )
    return {"ok": True}


def command_control(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").lower()
    strategy_id = str(payload.get("strategy_id") or payload.get("id") or "")
    if action == "start":
        if not strategy_id:
            row = connection.execute("SELECT id FROM strategies ORDER BY updated_at DESC LIMIT 1").fetchone()
            if row is None:
                raise ValueError("No strategy available to start.")
            strategy_id = row["id"]
        if connection.execute("SELECT 1 FROM strategies WHERE id = ?", (strategy_id,)).fetchone() is None:
            raise ValueError("Strategy not found.")
        with connection:
            connection.execute(
                """
                UPDATE runtime
                SET running = 1, active_strategy_id = ?, started_at = ?, stopped_at = '',
                    last_error = '', algo_status = 'Algo running.'
                WHERE id = 1
                """,
                (strategy_id, now_utc()),
            )
        return {"ok": True, **runtime_state(connection)}
    if action == "stop":
        with connection:
            connection.execute(
                "UPDATE runtime SET running = 0, stopped_at = ?, algo_status = 'Algo stopped.' WHERE id = 1",
                (now_utc(),),
            )
        return {"ok": True, **runtime_state(connection)}
    if action == "check":
        try:
            with connection:
                result = scan_signal(connection, strategy_id or None)
            return {"ok": True, "last_signal": result, **runtime_state(connection)}
        except Exception as exc:
            with connection:
                connection.execute(
                    "UPDATE runtime SET last_error = ?, algo_status = ? WHERE id = 1",
                    (str(exc), f"Signal check failed: {exc}"),
                )
            raise
    raise ValueError("Unsupported control action.")


def command_symbols(_: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    query = str((payload or {}).get("query") or "").strip().upper()
    if mt5 is None:
        symbols = [name for name in FALLBACK_SYMBOLS if not query or query in name.upper()]
        return {"ok": True, "symbols": symbols, "source": "fallback"}
    if not mt5.initialize():
        symbols = [name for name in FALLBACK_SYMBOLS if not query or query in name.upper()]
        return {"ok": True, "symbols": symbols, "source": "fallback"}
    try:
        symbols = mt5.symbols_get()
        if not symbols:
            fallback = [name for name in FALLBACK_SYMBOLS if not query or query in name.upper()]
            return {"ok": True, "symbols": fallback, "source": "fallback"}

        priority = ("BTC", "ETH", "XAU", "GOLD", "US30", "NAS", "SPX")
        names = sorted({item.name for item in symbols})
        if query:
            names = [name for name in names if query in name.upper()]
        names.sort(key=lambda name: (not any(term in name.upper() for term in priority), name.upper()))

        selectable: list[str] = []
        for name in names:
            try:
                if mt5.symbol_select(name, True):
                    selectable.append(name)
            except Exception:
                continue
            if len(selectable) >= 300:
                break
    finally:
        mt5.shutdown()

    if selectable:
        return {"ok": True, "symbols": selectable, "source": "mt5"}
    fallback = [name for name in FALLBACK_SYMBOLS if not query or query in name.upper()]
    return {"ok": True, "symbols": fallback, "source": "fallback"}


COMMANDS = {
    "status": command_status,
    "strategies": command_strategies,
    "strategy_create": command_strategy_create,
    "strategy_update": command_strategy_update,
    "strategy_delete": command_strategy_delete,
    "control": command_control,
    "symbols": command_symbols,
}


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    payload: dict[str, Any] = {}
    if len(sys.argv) > 2 and sys.argv[2].strip():
        payload = json.loads(sys.argv[2])
    connection = init_db()
    try:
        handler = COMMANDS.get(command)
        if handler is None:
            raise ValueError(f"Unknown command: {command}")
        output(handler(connection, payload))
        return 0
    except Exception as exc:
        output({"ok": False, "error": str(exc), "trace": traceback.format_exc()})
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
