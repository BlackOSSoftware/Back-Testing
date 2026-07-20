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
    "name": "BTCUSD# M5 breakout",
    "data_source": "MT5",
    "symbol": "BTCUSD#",
    "timeframe": "M5",
    "trail_timeframe": "M5",
    "entry_pattern": "BOTH",
    "from_date": "2026-06-01",
    "to_date": "2026-06-30",
    "range_start": "08:15",
    "range_end": "09:30",
    "session_start": "09:30",
    "entry_cutoff": "18:00",
    "session_end": "19:30",
    "entry_buffer_pct": 0.05,
    "entry_buffer_points": 0.0,
    "stop_points": 400.0,
    "stop_points_unit": "POINTS",
    "first_trail_profit": 400.0,
    "first_trail_profit_unit": "POINTS",
    "first_trail_lock_loss": 200.0,
    "first_trail_lock_loss_unit": "POINTS",
    "second_trail_profit": 700.0,
    "second_trail_profit_unit": "POINTS",
    "volume": 1.0,
    "target_points": 0.0,
    "max_trades_per_day": 1,
    "max_open_positions": 1,
    "live_trading_enabled": 0,
    "execution_mode": "PENDING_BEFORE_SIGNAL",
}


def now_utc() -> str:
    return datetime.now(UTC).isoformat()


def now_ist() -> datetime:
    return datetime.now(IST)


def parse_time(value: str) -> time:
    return datetime.strptime(str(value), "%H:%M").time()


def normalize_distance_unit(value: Any) -> str:
    text = str(value or "POINTS").strip().upper()
    if text in {"POINT", "POINTS", "PTS"}:
        return "POINTS"
    if text in {"PERCENT", "PERCENTAGE", "PCT", "%"}:
        return "PERCENT"
    raise ValueError("Distance unit must be POINTS or PERCENT.")


def distance_to_points(value: float, unit: str, entry_price: float) -> float:
    amount = float(value)
    return float(entry_price) * amount / 100.0 if normalize_distance_unit(unit) == "PERCENT" else amount


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
            from_date TEXT NOT NULL DEFAULT '2026-06-01',
            to_date TEXT NOT NULL DEFAULT '2026-06-30',
            range_start TEXT NOT NULL,
            range_end TEXT NOT NULL,
            session_start TEXT NOT NULL,
            entry_cutoff TEXT NOT NULL,
            session_end TEXT NOT NULL,
            entry_buffer_pct REAL NOT NULL,
            entry_buffer_points REAL NOT NULL DEFAULT 0,
            stop_points REAL NOT NULL,
            stop_points_unit TEXT NOT NULL DEFAULT 'POINTS',
            first_trail_profit REAL NOT NULL,
            first_trail_profit_unit TEXT NOT NULL DEFAULT 'POINTS',
            first_trail_lock_loss REAL NOT NULL,
            first_trail_lock_loss_unit TEXT NOT NULL DEFAULT 'POINTS',
            second_trail_profit REAL NOT NULL,
            second_trail_profit_unit TEXT NOT NULL DEFAULT 'POINTS',
            volume REAL NOT NULL,
            target_points REAL NOT NULL DEFAULT 0,
            max_trades_per_day INTEGER NOT NULL DEFAULT 1,
            max_open_positions INTEGER NOT NULL DEFAULT 1,
            live_trading_enabled INTEGER NOT NULL DEFAULT 0,
            execution_mode TEXT NOT NULL DEFAULT 'PENDING_BEFORE_SIGNAL',
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
        CREATE TABLE IF NOT EXISTS event_log (
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
        CREATE INDEX IF NOT EXISTS idx_signal_log_time ON signal_log(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_trade_log_time ON trade_log(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_event_log_day_time ON event_log(day_key, created_at DESC);
        """
    )
    ensure_column(connection, "strategies", "from_date", "TEXT NOT NULL DEFAULT '2026-06-01'")
    ensure_column(connection, "strategies", "to_date", "TEXT NOT NULL DEFAULT '2026-06-30'")
    ensure_column(connection, "strategies", "stop_points_unit", "TEXT NOT NULL DEFAULT 'POINTS'")
    ensure_column(connection, "strategies", "first_trail_profit_unit", "TEXT NOT NULL DEFAULT 'POINTS'")
    ensure_column(connection, "strategies", "first_trail_lock_loss_unit", "TEXT NOT NULL DEFAULT 'POINTS'")
    ensure_column(connection, "strategies", "second_trail_profit_unit", "TEXT NOT NULL DEFAULT 'POINTS'")
    ensure_column(connection, "strategies", "live_trading_enabled", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(connection, "strategies", "execution_mode", "TEXT NOT NULL DEFAULT 'PENDING_BEFORE_SIGNAL'")
    ensure_column(connection, "runtime", "buy_trigger_override", "REAL")
    ensure_column(connection, "runtime", "sell_trigger_override", "REAL")
    ensure_column(connection, "runtime", "trigger_override_day", "TEXT NOT NULL DEFAULT ''")
    connection.execute(
        """
        INSERT OR IGNORE INTO runtime (id, running, algo_status)
        VALUES (1, 0, 'Ready.')
        """
    )
    ensure_runtime_active_strategy(connection)
    prune_event_logs(connection)
    return connection


def ensure_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def prune_event_logs(connection: sqlite3.Connection) -> None:
    today = now_ist().date().isoformat()
    connection.execute("DELETE FROM event_log WHERE day_key != ?", (today,))


def strategy_exists(connection: sqlite3.Connection, strategy_id: str) -> bool:
    return bool(strategy_id and connection.execute("SELECT 1 FROM strategies WHERE id = ?", (strategy_id,)).fetchone())


def legacy_active_strategy_id() -> str:
    if not LEGACY_JSON.exists():
        return ""
    try:
        legacy = json.loads(LEGACY_JSON.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(legacy.get("active_strategy_id") or "")


def ensure_runtime_active_strategy(connection: sqlite3.Connection) -> None:
    runtime = connection.execute("SELECT active_strategy_id FROM runtime WHERE id = 1").fetchone()
    current_id = str(runtime["active_strategy_id"] or "") if runtime else ""
    if strategy_exists(connection, current_id):
        return

    restored_id = legacy_active_strategy_id()
    if not strategy_exists(connection, restored_id):
        row = connection.execute("SELECT id FROM strategies ORDER BY updated_at DESC LIMIT 1").fetchone()
        restored_id = str(row["id"] or "") if row else ""
    if restored_id:
        connection.execute(
            """
            UPDATE runtime
            SET active_strategy_id = ?, last_error = '',
                algo_status = CASE WHEN algo_status = '' OR active_strategy_id = '' THEN 'Saved strategy restored.' ELSE algo_status END
            WHERE id = 1
            """,
            (restored_id,),
        )


def add_event_log(
    connection: sqlite3.Connection,
    level: str,
    event: str,
    message: str,
    strategy: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    created_at = now_ist().isoformat()
    day_key = created_at[:10]
    data = payload or {}
    connection.execute(
        """
        INSERT INTO event_log (created_at, day_key, level, event, strategy_id, symbol, message, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at,
            day_key,
            str(level or "INFO").upper(),
            str(event or "EVENT").upper(),
            str((strategy or {}).get("id") or data.get("strategy_id") or ""),
            str((strategy or {}).get("symbol") or data.get("symbol") or ""),
            str(message or ""),
            json.dumps(data, separators=(",", ":"), default=str),
        ),
    )


def row_to_strategy(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "data_source": row["data_source"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "trail_timeframe": row["trail_timeframe"],
        "entry_pattern": row["entry_pattern"],
        "from_date": row["from_date"],
        "to_date": row["to_date"],
        "range_start": row["range_start"],
        "range_end": row["range_end"],
        "session_start": row["session_start"],
        "entry_cutoff": row["entry_cutoff"],
        "session_end": row["session_end"],
        "entry_buffer_pct": row["entry_buffer_pct"],
        "entry_buffer_points": row["entry_buffer_points"],
        "stop_points": row["stop_points"],
        "stop_points_unit": row["stop_points_unit"],
        "first_trail_profit": row["first_trail_profit"],
        "first_trail_profit_unit": row["first_trail_profit_unit"],
        "first_trail_lock_loss": row["first_trail_lock_loss"],
        "first_trail_lock_loss_unit": row["first_trail_lock_loss_unit"],
        "second_trail_profit": row["second_trail_profit"],
        "second_trail_profit_unit": row["second_trail_profit_unit"],
        "volume": row["volume"],
        "target_points": row["target_points"],
        "max_trades_per_day": row["max_trades_per_day"],
        "max_open_positions": row["max_open_positions"],
        "live_trading_enabled": bool(row["live_trading_enabled"]),
        "execution_mode": row["execution_mode"] or "PENDING_BEFORE_SIGNAL",
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
    execution_mode = str(merged.get("execution_mode") or "PENDING_BEFORE_SIGNAL").upper()
    if execution_mode not in {"PENDING_BEFORE_SIGNAL", "MARKET_AFTER_SIGNAL", "REARM_PENDING"}:
        raise ValueError("Execution mode is invalid.")
    from_date = datetime.strptime(str(merged.get("from_date")), "%Y-%m-%d").date()
    to_date = datetime.strptime(str(merged.get("to_date")), "%Y-%m-%d").date()
    if from_date > to_date:
        raise ValueError("From date cannot be after To date.")
    for key in ("range_start", "range_end", "session_start", "entry_cutoff", "session_end"):
        parse_time(str(merged[key]))
    volume = float(merged.get("volume", 0.01))
    if volume <= 0:
        raise ValueError("Qty / lot size must be greater than zero.")
    return {
        "id": strategy_id,
        "name": str(merged.get("name") or f"{symbol} {timeframe} breakout").strip(),
        "data_source": "MT5",
        "symbol": symbol,
        "timeframe": timeframe,
        "trail_timeframe": trail_timeframe,
        "entry_pattern": entry_pattern,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "range_start": str(merged["range_start"]),
        "range_end": str(merged["range_end"]),
        "session_start": str(merged["session_start"]),
        "entry_cutoff": str(merged["entry_cutoff"]),
        "session_end": str(merged["session_end"]),
        "entry_buffer_pct": float(merged.get("entry_buffer_pct", 0.25)),
        "entry_buffer_points": float(merged.get("entry_buffer_points", 0.0)),
        "stop_points": float(merged.get("stop_points", 500.0)),
        "stop_points_unit": normalize_distance_unit(merged.get("stop_points_unit")),
        "first_trail_profit": float(merged.get("first_trail_profit", 700.0)),
        "first_trail_profit_unit": normalize_distance_unit(merged.get("first_trail_profit_unit")),
        "first_trail_lock_loss": float(merged.get("first_trail_lock_loss", 200.0)),
        "first_trail_lock_loss_unit": normalize_distance_unit(merged.get("first_trail_lock_loss_unit")),
        "second_trail_profit": float(merged.get("second_trail_profit", 700.0)),
        "second_trail_profit_unit": normalize_distance_unit(merged.get("second_trail_profit_unit")),
        "volume": volume,
        "target_points": float(merged.get("target_points", 0.0)),
        "max_trades_per_day": int(merged.get("max_trades_per_day", 1)),
        "max_open_positions": int(merged.get("max_open_positions", 1)),
        "live_trading_enabled": 1 if merged.get("live_trading_enabled") else 0,
        "execution_mode": execution_mode,
        "updated_at": str(merged.get("updated_at") or now_utc()),
    }


def upsert_strategy(connection: sqlite3.Connection, strategy: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO strategies (
            id, name, data_source, symbol, timeframe, trail_timeframe, entry_pattern,
            from_date, to_date, range_start, range_end, session_start, entry_cutoff, session_end,
            entry_buffer_pct, entry_buffer_points, stop_points, stop_points_unit,
            first_trail_profit, first_trail_profit_unit, first_trail_lock_loss,
            first_trail_lock_loss_unit, second_trail_profit, second_trail_profit_unit, volume, target_points,
            max_trades_per_day, max_open_positions, live_trading_enabled, execution_mode, updated_at
        )
        VALUES (
            :id, :name, :data_source, :symbol, :timeframe, :trail_timeframe, :entry_pattern,
            :from_date, :to_date, :range_start, :range_end, :session_start, :entry_cutoff, :session_end,
            :entry_buffer_pct, :entry_buffer_points, :stop_points, :stop_points_unit,
            :first_trail_profit, :first_trail_profit_unit, :first_trail_lock_loss,
            :first_trail_lock_loss_unit, :second_trail_profit, :second_trail_profit_unit, :volume, :target_points,
            :max_trades_per_day, :max_open_positions, :live_trading_enabled, :execution_mode, :updated_at
        )
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            data_source=excluded.data_source,
            symbol=excluded.symbol,
            timeframe=excluded.timeframe,
            trail_timeframe=excluded.trail_timeframe,
            entry_pattern=excluded.entry_pattern,
            from_date=excluded.from_date,
            to_date=excluded.to_date,
            range_start=excluded.range_start,
            range_end=excluded.range_end,
            session_start=excluded.session_start,
            entry_cutoff=excluded.entry_cutoff,
            session_end=excluded.session_end,
            entry_buffer_pct=excluded.entry_buffer_pct,
            entry_buffer_points=excluded.entry_buffer_points,
            stop_points=excluded.stop_points,
            stop_points_unit=excluded.stop_points_unit,
            first_trail_profit=excluded.first_trail_profit,
            first_trail_profit_unit=excluded.first_trail_profit_unit,
            first_trail_lock_loss=excluded.first_trail_lock_loss,
            first_trail_lock_loss_unit=excluded.first_trail_lock_loss_unit,
            second_trail_profit=excluded.second_trail_profit,
            second_trail_profit_unit=excluded.second_trail_profit_unit,
            volume=excluded.volume,
            target_points=excluded.target_points,
            max_trades_per_day=excluded.max_trades_per_day,
            max_open_positions=excluded.max_open_positions,
            live_trading_enabled=excluded.live_trading_enabled,
            execution_mode=excluded.execution_mode,
            updated_at=excluded.updated_at
        """,
        strategy,
    )


def migrate_legacy(connection: sqlite3.Connection) -> None:
    existing_count = connection.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    if LEGACY_JSON.exists():
        try:
            legacy = json.loads(LEGACY_JSON.read_text(encoding="utf-8"))
        except Exception:
            legacy = {}
    else:
        legacy = {}
    strategies = legacy.get("strategies") or [DEFAULT_STRATEGY]

    # A database may already contain a newly-created default strategy even
    # though older saved strategies still exist in algo.json. Merge only
    # missing legacy records so later SQLite edits are never overwritten.
    if existing_count:
        with connection:
            for item in strategies:
                strategy_id = str(item.get("id") or "")
                if not strategy_id or connection.execute(
                    "SELECT 1 FROM strategies WHERE id = ?", (strategy_id,)
                ).fetchone():
                    continue
                imported = dict(item)
                imported["name"] = f"{imported.get('symbol') or DEFAULT_STRATEGY['symbol']} {str(imported.get('timeframe') or 'M5').upper()} breakout"
                upsert_strategy(connection, normalize_strategy(imported, strategy_id))

            runtime = connection.execute(
                "SELECT running, active_strategy_id FROM runtime WHERE id = 1"
            ).fetchone()
            saved_active = str(legacy.get("active_strategy_id") or "")
            if (
                runtime
                and not runtime["running"]
                and not runtime["active_strategy_id"]
                and saved_active
                and connection.execute("SELECT 1 FROM strategies WHERE id = ?", (saved_active,)).fetchone()
            ):
                connection.execute(
                    "UPDATE runtime SET active_strategy_id = ?, last_error = '', algo_status = 'Saved strategy restored.' WHERE id = 1",
                    (saved_active,),
                )
        return

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
    # SQLite is now the only source of truth. Do not recreate deleted/default
    # strategies from the legacy JSON file; an empty database must stay empty
    # until the user explicitly saves settings.
    return connect()


def list_strategies(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute("SELECT * FROM strategies ORDER BY updated_at DESC").fetchall()
    return [row_to_strategy(row) for row in rows]


def runtime_state(connection: sqlite3.Connection) -> dict[str, Any]:
    prune_event_logs(connection)
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
    broker_state = fetch_mt5_trade_state(active["symbol"]) if active and active.get("data_source") == "MT5" else {"pending_orders": [], "open_positions": []}
    signals = [
        {**dict(item), "payload": json.loads(item["payload_json"] or "{}")}
        for item in connection.execute("SELECT * FROM signal_log ORDER BY created_at DESC LIMIT 50").fetchall()
    ]
    trades = [
        {**dict(item), "payload": json.loads(item["payload_json"] or "{}")}
        for item in connection.execute("SELECT * FROM trade_log ORDER BY created_at DESC LIMIT 50").fetchall()
    ]
    events = [
        {**dict(item), "payload": json.loads(item["payload_json"] or "{}")}
        for item in connection.execute("SELECT * FROM event_log ORDER BY created_at DESC LIMIT 300").fetchall()
    ]
    for item in signals:
        item.pop("payload_json", None)
    for item in trades:
        item.pop("payload_json", None)
    for item in events:
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
        "buy_trigger_override": row["buy_trigger_override"],
        "sell_trigger_override": row["sell_trigger_override"],
        "trigger_override_day": row["trigger_override_day"],
        "last_signal": last_signal,
        "live_quote": live_quote,
        "pending_orders": broker_state["pending_orders"],
        "open_positions": broker_state["open_positions"],
        "strategies": list_strategies(connection),
        "signal_log": signals,
        "trade_log": trades,
        "event_log": events,
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


def fetch_mt5_trade_state(symbol: str) -> dict[str, Any]:
    if mt5 is None or not symbol:
        return {"pending_orders": [], "open_positions": []}
    if not mt5.initialize():
        return {"pending_orders": [], "open_positions": [], "error": f"MT5 initialize failed: {mt5.last_error()}"}
    try:
        orders = mt5.orders_get(symbol=symbol)
        positions = mt5.positions_get(symbol=symbol)
        pending_orders = []
        for order in orders or []:
            if int(getattr(order, "magic", 0) or 0) != TRADE_MAGIC:
                continue
            order_type = int(getattr(order, "type", -1))
            side = "BUY" if order_type in {getattr(mt5, "ORDER_TYPE_BUY_STOP", 4), getattr(mt5, "ORDER_TYPE_BUY_LIMIT", 2)} else "SELL"
            pending_orders.append({
                "ticket": int(getattr(order, "ticket", 0) or 0),
                "side": side,
                "price": float(getattr(order, "price_open", 0.0) or 0.0),
                "stop_loss": float(getattr(order, "sl", 0.0) or 0.0),
                "take_profit": float(getattr(order, "tp", 0.0) or 0.0),
                "volume": float(getattr(order, "volume_current", 0.0) or 0.0),
                "state": int(getattr(order, "state", 0) or 0),
                "comment": str(getattr(order, "comment", "") or ""),
            })
        open_positions = []
        for position in positions or []:
            if int(getattr(position, "magic", 0) or 0) != TRADE_MAGIC:
                continue
            open_positions.append({
                "ticket": int(getattr(position, "ticket", 0) or 0),
                "side": "BUY" if int(getattr(position, "type", 0) or 0) == getattr(mt5, "POSITION_TYPE_BUY", 0) else "SELL",
                "price": float(getattr(position, "price_open", 0.0) or 0.0),
                "current_price": float(getattr(position, "price_current", 0.0) or 0.0),
                "stop_loss": float(getattr(position, "sl", 0.0) or 0.0),
                "take_profit": float(getattr(position, "tp", 0.0) or 0.0),
                "volume": float(getattr(position, "volume", 0.0) or 0.0),
                "profit": float(getattr(position, "profit", 0.0) or 0.0),
            })
        return {"pending_orders": pending_orders, "open_positions": open_positions}
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


def signal_outcome_reached(
    candles: list[dict[str, Any]],
    trigger_time: datetime,
    side: str,
    first_target: float,
    stop_loss: float,
) -> str | None:
    """Return a completed safety outcome that makes a later re-entry unsafe."""
    for candle in candles:
        if candle["time_ist"] < trigger_time:
            continue
        if side == "BUY":
            if float(candle["low"]) <= stop_loss + PRICE_EPSILON:
                return "STOP_LOSS_REACHED"
            if float(candle["high"]) + PRICE_EPSILON >= first_target:
                return "FIRST_TARGET_REACHED"
        else:
            if float(candle["high"]) + PRICE_EPSILON >= stop_loss:
                return "STOP_LOSS_REACHED"
            if float(candle["low"]) <= first_target + PRICE_EPSILON:
                return "FIRST_TARGET_REACHED"
    return None


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
    runtime = connection.execute("SELECT running, started_at FROM runtime WHERE id = 1").fetchone()
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
    calculated_buy_trigger = range_high * (1 + strategy["entry_buffer_pct"] / 100) + strategy["entry_buffer_points"]
    calculated_sell_trigger = range_low * (1 - strategy["entry_buffer_pct"] / 100) - strategy["entry_buffer_points"]
    override = connection.execute(
        "SELECT buy_trigger_override, sell_trigger_override, trigger_override_day FROM runtime WHERE id = 1"
    ).fetchone()
    override_active = bool(override and override["trigger_override_day"] == checked_at.date().isoformat())
    buy_trigger = float(override["buy_trigger_override"]) if override_active and override["buy_trigger_override"] is not None else calculated_buy_trigger
    sell_trigger = float(override["sell_trigger_override"]) if override_active and override["sell_trigger_override"] is not None else calculated_sell_trigger
    result.update(
        {
            "last_candle_time": candles[-1]["time_ist"].isoformat() if candles else "",
            "last_close": candles[-1]["close"] if candles else None,
            "range_high": range_high,
            "range_low": range_low,
            "buy_trigger": buy_trigger,
            "sell_trigger": sell_trigger,
            "calculated_buy_trigger": calculated_buy_trigger,
            "calculated_sell_trigger": calculated_sell_trigger,
            "trigger_override_active": override_active,
            "buffer": f"{strategy['entry_buffer_pct']}%",
        }
    )
    result["position_action"] = manage_live_positions(connection, strategy, running, checked_at, session_right)
    if checked_at < range_right:
        result.update(
            {
                "message": "Range window is still forming.",
                "buy_trigger": None,
                "sell_trigger": None,
                "calculated_buy_trigger": None,
                "calculated_sell_trigger": None,
                "trigger_override_active": False,
            }
        )
        save_signal_result(connection, result)
        return result
    if checked_at < session_left:
        result.update({"phase": "WAIT_SESSION", "message": "Waiting for session start."})
        save_signal_result(connection, result)
        return result
    if checked_at > session_right:
        cancel_algo_pending_orders(strategy["symbol"], "Session ended")
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
        stop_distance = distance_to_points(strategy["stop_points"], strategy["stop_points_unit"], entry_reference)
        first_trail_distance = distance_to_points(
            strategy["first_trail_profit"], strategy["first_trail_profit_unit"], entry_reference
        )
        first_lock_distance = distance_to_points(
            strategy["first_trail_lock_loss"], strategy["first_trail_lock_loss_unit"], entry_reference
        )
        second_trail_distance = distance_to_points(
            strategy["second_trail_profit"], strategy["second_trail_profit_unit"], entry_reference
        )
        stop_loss = entry_reference - stop_distance if side == "BUY" else entry_reference + stop_distance
        first_trail_trigger = (
            entry_reference + first_trail_distance
            if side == "BUY"
            else entry_reference - first_trail_distance
        )
        first_trail_stop = first_trail_lock_stop(entry_reference, first_lock_distance, side)
        second_trail_trigger = (
            entry_reference + second_trail_distance
            if side == "BUY"
            else entry_reference - second_trail_distance
        )
        result.update(
            {
                "status": f"{side} signal",
                "message": f"{side} trigger crossed.",
                "side": side,
                "entry_reference": entry_reference,
                "stop_loss": stop_loss,
                "first_trail_trigger": first_trail_trigger,
                "first_trail_stop": first_trail_stop,
                "second_trail_trigger": second_trail_trigger,
                "trigger_candle_time": candle["time_ist"].isoformat(),
            }
        )
        break
    mode = str(strategy.get("execution_mode") or "PENDING_BEFORE_SIGNAL")
    placement_start = max(range_right, session_left)
    if not result.get("side"):
        if mode == "MARKET_AFTER_SIGNAL":
            result["pending_action"] = {"status": "waiting_signal", "message": "Waiting for a signal; this mode does not place pending orders."}
        else:
            result["pending_action"] = manage_pending_orders(
                connection, strategy, running, checked_at, placement_start, cutoff_right, buy_trigger, sell_trigger
            )
    else:
        result["live_quote"] = fetch_live_quote(strategy["symbol"])
        trigger_time = datetime.fromisoformat(str(result["trigger_candle_time"]))
        started_at = str(runtime["started_at"] or "") if runtime else ""
        started_after_signal = False
        if started_at:
            started_time = datetime.fromisoformat(started_at)
            if started_time.tzinfo is None:
                started_time = started_time.replace(tzinfo=UTC)
            started_after_signal = started_time.astimezone(IST) > trigger_time
        result["started_after_signal"] = started_after_signal
        cancellation = cancel_algo_pending_orders(strategy["symbol"], "Signal crossed - stale pending orders removed")
        if mode == "MARKET_AFTER_SIGNAL" and started_after_signal:
            result["trade_action"] = maybe_execute_live_trade(connection, strategy, result, running)
        elif mode == "REARM_PENDING" and started_after_signal:
            outcome = signal_outcome_reached(
                eligible,
                trigger_time,
                str(result["side"]),
                float(result["first_trail_trigger"]),
                float(result["stop_loss"]),
            )
            if outcome:
                result["trade_action"] = {
                    "status": "REARM_BLOCKED",
                    "message": f"Pending re-entry blocked: {outcome.replace('_', ' ').lower()} today.",
                }
            else:
                result["trade_action"] = manage_pending_orders(
                    connection,
                    strategy,
                    running,
                    checked_at,
                    placement_start,
                    cutoff_right,
                    buy_trigger,
                    sell_trigger,
                    {str(result["side"])},
                )
        else:
            result["trade_action"] = {
                "status": "SIGNAL_ALREADY_CROSSED",
                "message": "Signal is already crossed; no new order was placed for this execution mode.",
                "cancellation": cancellation,
            }
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
        add_event_log(
            connection,
            "SIGNAL",
            "SIGNAL_CROSSED",
            f"{result.get('side')} trigger crossed at {result.get('entry_reference')}",
            {"id": result.get("strategy_id") or "", "symbol": result.get("symbol") or ""},
            {
                "side": result.get("side"),
                "entry_reference": result.get("entry_reference"),
                "stop_loss": result.get("stop_loss"),
                "trigger_candle_time": result.get("trigger_candle_time"),
            },
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


def cancel_algo_pending_orders(symbol: str, reason: str) -> dict[str, Any]:
    if mt5 is None or not symbol or not mt5.initialize():
        return {"status": "cancel_unavailable", "message": "MT5 is unavailable."}
    cancelled: list[int] = []
    errors: list[str] = []
    try:
        for order in mt5.orders_get(symbol=symbol) or []:
            if int(getattr(order, "magic", 0) or 0) != TRADE_MAGIC:
                continue
            ticket = int(getattr(order, "ticket", 0) or 0)
            response = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket, "comment": "AlgoCancel"})
            data = response._asdict() if response is not None and hasattr(response, "_asdict") else {}
            if int(data.get("retcode", 0)) == getattr(mt5, "TRADE_RETCODE_DONE", 10009):
                cancelled.append(ticket)
            else:
                errors.append(str(data.get("comment") or mt5.last_error()))
        return {"status": "cancelled" if cancelled else "none", "message": reason, "tickets": cancelled, "errors": errors}
    finally:
        mt5.shutdown()


def position_ticket_logged(connection: sqlite3.Connection, ticket: int) -> bool:
    for row in connection.execute("SELECT payload_json FROM trade_log ORDER BY id DESC LIMIT 200").fetchall():
        try:
            if int(json.loads(row["payload_json"] or "{}").get("position_ticket") or 0) == ticket:
                return True
        except Exception:
            continue
    return False


def log_open_position(connection: sqlite3.Connection, strategy: dict[str, Any], position: Any) -> None:
    ticket = int(getattr(position, "ticket", 0) or 0)
    if not ticket or position_ticket_logged(connection, ticket):
        return
    side = "BUY" if int(getattr(position, "type", 0) or 0) == getattr(mt5, "POSITION_TYPE_BUY", 0) else "SELL"
    payload = {
        "position_ticket": ticket,
        "strategy_id": strategy["id"],
        "symbol": strategy["symbol"],
        "side": side,
        "volume": float(getattr(position, "volume", 0.0) or 0.0),
        "entry_price": float(getattr(position, "price_open", 0.0) or 0.0),
        "stop_loss": float(getattr(position, "sl", 0.0) or 0.0),
        "status": "POSITION_OPEN",
    }
    with connection:
        connection.execute(
            """
            INSERT INTO trade_log (created_at, strategy_id, symbol, side, entry_price, stop_loss, status, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now_ist().isoformat(), strategy["id"], strategy["symbol"], side, payload["entry_price"], payload["stop_loss"], "POSITION_OPEN", json.dumps(payload, separators=(",", ":"))),
        )
        add_event_log(
            connection,
            "TRADE",
            "POSITION_OPEN",
            f"{side} position open #{ticket} entry {payload['entry_price']} SL {payload['stop_loss']}",
            strategy,
            payload,
        )


def modify_position_stop(position: Any, stop_loss: float, reason: str, digits: int) -> dict[str, Any]:
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": str(getattr(position, "symbol", "")),
        "position": int(getattr(position, "ticket", 0) or 0),
        "sl": round(float(stop_loss), digits),
        "tp": float(getattr(position, "tp", 0.0) or 0.0),
        "magic": TRADE_MAGIC,
        "comment": reason[:31],
    }
    response = mt5.order_send(request)
    data = response._asdict() if response is not None and hasattr(response, "_asdict") else {}
    ok = int(data.get("retcode", 0)) == getattr(mt5, "TRADE_RETCODE_DONE", 10009)
    return {"status": "SL_MODIFIED" if ok else "SL_MODIFY_FAILED", "message": str(data.get("comment") or mt5.last_error()), "stop_loss": request["sl"], "retcode": data.get("retcode"), "ticket": request["position"], "reason": reason}


def close_position(position: Any, reason: str) -> dict[str, Any]:
    symbol = str(getattr(position, "symbol", ""))
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return {"status": "CLOSE_FAILED", "message": f"No live tick for {symbol}."}
    is_buy = int(getattr(position, "type", 0) or 0) == getattr(mt5, "POSITION_TYPE_BUY", 0)
    base = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "position": int(getattr(position, "ticket", 0) or 0),
        "volume": float(getattr(position, "volume", 0.0) or 0.0),
        "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
        "price": float(tick.bid if is_buy else tick.ask),
        "deviation": ORDER_DEVIATION,
        "magic": TRADE_MAGIC,
        "comment": reason[:31],
        "type_time": mt5.ORDER_TIME_GTC,
    }
    last: dict[str, Any] = {}
    for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
        request = {**base, "type_filling": filling}
        check = mt5.order_check(request)
        check_data = check._asdict() if check is not None and hasattr(check, "_asdict") else {}
        if int(check_data.get("retcode", -1)) != 0:
            last = check_data
            continue
        response = mt5.order_send(request)
        data = response._asdict() if response is not None and hasattr(response, "_asdict") else {}
        last = data
        if int(data.get("retcode", 0)) in {getattr(mt5, "TRADE_RETCODE_DONE", 10009), getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)}:
            return {"status": "POSITION_CLOSED", "message": reason, "ticket": data.get("deal") or data.get("order"), "retcode": data.get("retcode")}
    return {"status": "CLOSE_FAILED", "message": str(last.get("comment") or mt5.last_error()), "retcode": last.get("retcode")}


def last_completed_trail_level(symbol: str, timeframe_name: str, side: str) -> float | None:
    rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe(timeframe_name), 0, 3)
    if rates is None or len(rates) < 2:
        return None
    candle = rates[-2]
    return float(candle["low"] if side == "BUY" else candle["high"])


def manage_live_positions(
    connection: sqlite3.Connection,
    strategy: dict[str, Any],
    running: bool,
    checked_at: datetime,
    session_end: datetime,
) -> dict[str, Any]:
    if not running or mt5 is None:
        return {"status": "disabled", "message": "Live position management is disabled."}
    if not mt5.initialize():
        return {"status": "error", "message": f"MT5 initialize failed: {mt5.last_error()}"}
    try:
        symbol = strategy["symbol"]
        positions = [p for p in (mt5.positions_get(symbol=symbol) or []) if int(getattr(p, "magic", 0) or 0) == TRADE_MAGIC]
        if not positions:
            return {"status": "no_position", "message": "No live algo position."}
        cancelled_orders: list[int] = []
        cancel_errors: list[str] = []
        for order in mt5.orders_get(symbol=symbol) or []:
            if int(getattr(order, "magic", 0) or 0) == TRADE_MAGIC:
                ticket = int(order.ticket)
                response = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket, "comment": "AlgoCancel"})
                data = response._asdict() if response is not None and hasattr(response, "_asdict") else {}
                if int(data.get("retcode", 0)) == getattr(mt5, "TRADE_RETCODE_DONE", 10009):
                    cancelled_orders.append(ticket)
                else:
                    cancel_errors.append(str(data.get("comment") or mt5.last_error()))
        position = positions[0]
        log_open_position(connection, strategy, position)
        if cancelled_orders or cancel_errors:
            add_event_log(
                connection,
                "ORDER" if cancelled_orders and not cancel_errors else "ERROR",
                "OCO_PENDING_CANCEL",
                f"Position open; cancelled {len(cancelled_orders)} opposite/stale pending order(s).",
                strategy,
                {"tickets": cancelled_orders, "errors": cancel_errors},
            )
        if checked_at >= session_end:
            action = close_position(position, "Algo force exit")
            add_event_log(
                connection,
                "TRADE" if action.get("status") == "POSITION_CLOSED" else "ERROR",
                str(action.get("status") or "FORCE_EXIT"),
                str(action.get("message") or "Session force exit attempted."),
                strategy,
                action,
            )
            return action
        info = mt5.symbol_info(symbol)
        if info is None:
            return {"status": "error", "message": "Symbol information unavailable."}
        digits = int(getattr(info, "digits", 2) or 2)
        side = "BUY" if int(getattr(position, "type", 0) or 0) == getattr(mt5, "POSITION_TYPE_BUY", 0) else "SELL"
        entry = float(getattr(position, "price_open", 0.0) or 0.0)
        current = float(getattr(position, "price_current", 0.0) or 0.0)
        current_sl = float(getattr(position, "sl", 0.0) or 0.0)
        move = current - entry if side == "BUY" else entry - current
        first_distance = distance_to_points(strategy["first_trail_profit"], strategy["first_trail_profit_unit"], entry)
        lock_distance = distance_to_points(strategy["first_trail_lock_loss"], strategy["first_trail_lock_loss_unit"], entry)
        second_distance = distance_to_points(strategy["second_trail_profit"], strategy["second_trail_profit_unit"], entry)
        first_lock = entry + lock_distance if side == "BUY" else entry - lock_distance
        if move >= second_distance:
            candle_stop = last_completed_trail_level(symbol, strategy["trail_timeframe"], side)
            if candle_stop is not None:
                candidate = max(first_lock, candle_stop) if side == "BUY" else min(first_lock, candle_stop)
                improves = current_sl <= 0 or (candidate > current_sl if side == "BUY" else candidate < current_sl)
                valid_side = candidate < current if side == "BUY" else candidate > current
                if improves and valid_side:
                    action = {**modify_position_stop(position, candidate, "Second target trail", digits), "stage": "SECOND_TRAIL", "move": move}
                    add_event_log(
                        connection,
                        "TRADE" if action["status"] == "SL_MODIFIED" else "ERROR",
                        action["status"],
                        f"Second trail SL {'modified' if action['status'] == 'SL_MODIFIED' else 'modify failed'} to {action.get('stop_loss')}",
                        strategy,
                        {**action, "side": side, "entry": entry, "current": current, "previous_sl": current_sl},
                    )
                    return action
            return {"status": "SECOND_TRAIL_ACTIVE", "message": "Waiting for a better completed trail candle.", "stage": "SECOND_TRAIL", "move": move, "stop_loss": current_sl}
        if move >= first_distance:
            improves = current_sl <= 0 or (first_lock > current_sl if side == "BUY" else first_lock < current_sl)
            valid_side = first_lock < current if side == "BUY" else first_lock > current
            if improves and valid_side:
                action = {**modify_position_stop(position, first_lock, "First target SL lock", digits), "stage": "FIRST_LOCK", "move": move}
                add_event_log(
                    connection,
                    "TRADE" if action["status"] == "SL_MODIFIED" else "ERROR",
                    action["status"],
                    f"First target SL {'modified' if action['status'] == 'SL_MODIFIED' else 'modify failed'} to {action.get('stop_loss')}",
                    strategy,
                    {**action, "side": side, "entry": entry, "current": current, "previous_sl": current_sl},
                )
                return action
            return {"status": "FIRST_LOCK_ACTIVE", "message": "First target reached; SL lock is active.", "stage": "FIRST_LOCK", "move": move, "stop_loss": current_sl}
        return {"status": "POSITION_MONITORED", "message": "Live position is being monitored.", "stage": "INITIAL", "move": move, "stop_loss": current_sl}
    finally:
        mt5.shutdown()


def manage_pending_orders(
    connection: sqlite3.Connection,
    strategy: dict[str, Any],
    running: bool,
    checked_at: datetime,
    placement_start: datetime,
    placement_end: datetime,
    buy_trigger: float,
    sell_trigger: float,
    allowed_sides: set[str] | None = None,
) -> dict[str, Any]:
    if not running:
        return {"status": "not_running", "message": "Algo is not running."}
    if not strategy.get("live_trading_enabled"):
        cancelled = cancel_algo_pending_orders(strategy["symbol"], "Live orders disabled")
        return {"status": "disabled", "message": "Auto Trade is disabled; pending orders were cancelled.", "cancellation": cancelled}
    if checked_at < placement_start:
        return {"status": "waiting_range", "message": "Pending orders will be sent after the range is complete."}
    if checked_at > placement_end:
        cancelled = cancel_algo_pending_orders(strategy["symbol"], "Entry cutoff reached")
        return {"status": "entry_closed", "message": "Entry cutoff reached; remaining pending orders were cancelled.", "cancellation": cancelled}
    if mt5 is None:
        return {"status": "error", "message": "MetaTrader5 Python package is not installed."}
    symbol = strategy["symbol"]
    if not mt5.initialize():
        return {"status": "error", "message": f"MT5 initialize failed: {mt5.last_error()}"}
    try:
        if not mt5.symbol_select(symbol, True):
            return {"status": "error", "message": f"Symbol not available in MT5 Market Watch: {symbol}"}
        positions = [p for p in (mt5.positions_get(symbol=symbol) or []) if int(getattr(p, "magic", 0) or 0) == TRADE_MAGIC]
        orders = [o for o in (mt5.orders_get(symbol=symbol) or []) if int(getattr(o, "magic", 0) or 0) == TRADE_MAGIC]
        if positions:
            for order in orders:
                mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": int(order.ticket), "comment": "AlgoCancel"})
            return {"status": "position_open", "message": "Position is open; opposite pending order removed."}

        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        if tick is None or info is None:
            return {"status": "error", "message": f"Live symbol data unavailable for {symbol}."}
        ask = float(tick.ask)
        bid = float(tick.bid)
        point = float(getattr(info, "point", 0.0) or 0.0)
        digits = int(getattr(info, "digits", 2) or 2)
        minimum_gap = float(getattr(info, "trade_stops_level", 0) or 0) * point
        sides = set(allowed_sides or {"BUY", "SELL"})
        if strategy.get("entry_pattern") == "BUY_ONLY":
            sides.discard("SELL")
        if strategy.get("entry_pattern") == "SELL_ONLY":
            sides.discard("BUY")
        existing_sides = {
            "BUY" if int(getattr(order, "type", -1)) == getattr(mt5, "ORDER_TYPE_BUY_STOP", 4) else "SELL"
            for order in orders
        }
        sides_to_place = sides - existing_sides
        if not sides_to_place:
            return {"status": "pending_live", "message": f"{len(orders)} pending order(s) live in MT5.", "tickets": [int(o.ticket) for o in orders]}

        volume = float(strategy.get("volume") or 0.01)
        volume_min = float(getattr(info, "volume_min", 0.0) or 0.0)
        volume_max = float(getattr(info, "volume_max", 0.0) or 0.0)
        volume_step = float(getattr(info, "volume_step", 0.0) or 0.0)
        if volume < volume_min or (volume_max > 0 and volume > volume_max):
            return {"status": "invalid_volume", "message": f"Lot {volume} is outside broker range {volume_min}-{volume_max}."}
        if volume_step > 0 and abs(round(volume / volume_step) * volume_step - volume) > 1e-9:
            return {"status": "invalid_volume", "message": f"Lot {volume} does not match broker step {volume_step}."}
        stop_distance_buy = distance_to_points(strategy["stop_points"], strategy["stop_points_unit"], buy_trigger)
        stop_distance_sell = distance_to_points(strategy["stop_points"], strategy["stop_points_unit"], sell_trigger)
        target_points = float(strategy.get("target_points") or 0.0)
        requests = [
            ("BUY", mt5.ORDER_TYPE_BUY_STOP, buy_trigger, buy_trigger - stop_distance_buy, buy_trigger + target_points if target_points > 0 else 0.0),
            ("SELL", mt5.ORDER_TYPE_SELL_STOP, sell_trigger, sell_trigger + stop_distance_sell, sell_trigger - target_points if target_points > 0 else 0.0),
        ]
        requests = [item for item in requests if item[0] in sides_to_place]
        placed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        success_codes = {getattr(mt5, "TRADE_RETCODE_DONE", 10009), getattr(mt5, "TRADE_RETCODE_PLACED", 10008)}
        for side, order_type, price, stop_loss, take_profit in requests:
            price = round(float(price), digits)
            stop_loss = round(float(stop_loss), digits)
            take_profit = round(float(take_profit), digits) if take_profit else 0.0
            if side == "BUY" and price <= ask + minimum_gap:
                failed.append({"side": side, "ticket": None, "retcode": "level_passed", "message": "BUY trigger is already at/past market price; pending order was not sent.", "price": price, "stop_loss": stop_loss})
                continue
            if side == "SELL" and price >= bid - minimum_gap:
                failed.append({"side": side, "ticket": None, "retcode": "level_passed", "message": "SELL trigger is already at/past market price; pending order was not sent.", "price": price, "stop_loss": stop_loss})
                continue
            request: dict[str, Any] = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": symbol,
                "volume": volume,
                "type": order_type,
                "price": float(price),
                "sl": float(stop_loss),
                "deviation": ORDER_DEVIATION,
                "magic": TRADE_MAGIC,
                "comment": f"AlgoDesk {side} {strategy['id'][:6]}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_RETURN,
            }
            if take_profit:
                request["tp"] = float(take_profit)
            check = mt5.order_check(request)
            check_data = check._asdict() if check is not None and hasattr(check, "_asdict") else {}
            if check is None or int(check_data.get("retcode", -1)) != 0:
                failed.append({"side": side, "ticket": None, "retcode": check_data.get("retcode"), "message": check_data.get("comment") or str(mt5.last_error()), "price": price, "stop_loss": stop_loss})
                continue
            response = mt5.order_send(request)
            data = response._asdict() if response is not None and hasattr(response, "_asdict") else {}
            item = {"side": side, "ticket": data.get("order"), "retcode": data.get("retcode"), "message": data.get("comment"), "price": price, "stop_loss": stop_loss}
            (placed if int(data.get("retcode", 0)) in success_codes else failed).append(item)
        if placed:
            with connection:
                connection.execute("UPDATE runtime SET pending_order_day = ? WHERE id = 1", (checked_at.date().isoformat(),))
        pending_result = {
            "status": "pending_live" if len(placed) + len(existing_sides & sides) == len(sides) and not failed else "pending_partial" if placed or existing_sides else "pending_failed",
            "message": f"{len(placed)} pending order(s) placed; {len(existing_sides & sides)} already live; {len(failed)} failed.",
            "orders": placed,
            "existing_tickets": [int(o.ticket) for o in orders],
            "errors": failed,
        }
        if placed:
            sides_text = ", ".join(f"{item['side']} #{item.get('ticket')} @ {item.get('price')} SL {item.get('stop_loss')}" for item in placed)
            add_event_log(
                connection,
                "ORDER",
                "PENDING_ORDER_CREATED",
                f"Pending order created: {sides_text}",
                strategy,
                {**pending_result, "requested_sides": sorted(sides), "existing_sides": sorted(existing_sides)},
            )
        if failed:
            add_event_log(
                connection,
                "ERROR",
                "PENDING_ORDER_ISSUE",
                f"Pending order issue: {len(failed)} failed/skipped",
                strategy,
                {**pending_result, "requested_sides": sorted(sides), "existing_sides": sorted(existing_sides)},
            )
        return pending_result
    finally:
        mt5.shutdown()


def update_pending_trigger(
    connection: sqlite3.Connection,
    strategy: dict[str, Any],
    side: str,
    requested_price: float,
) -> dict[str, Any]:
    side = side.upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("Side must be BUY or SELL.")
    if requested_price <= 0:
        raise ValueError("Trigger price must be greater than zero.")
    if strategy.get("entry_pattern") == "BUY_ONLY" and side == "SELL":
        raise ValueError("SELL entries are disabled for this strategy.")
    if strategy.get("entry_pattern") == "SELL_ONLY" and side == "BUY":
        raise ValueError("BUY entries are disabled for this strategy.")
    if mt5 is None or not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error() if mt5 else 'package unavailable'}")
    try:
        symbol = strategy["symbol"]
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Symbol not available in MT5 Market Watch: {symbol}")
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            raise RuntimeError(f"Live symbol data unavailable for {symbol}.")
        digits = int(getattr(info, "digits", 2) or 2)
        point = float(getattr(info, "point", 0.0) or 0.0)
        minimum_gap = float(getattr(info, "trade_stops_level", 0) or 0) * point
        price = round(float(requested_price), digits)
        if side == "BUY" and price <= float(tick.ask) + minimum_gap:
            raise ValueError(f"BUY STOP must be above current Ask {float(tick.ask):.{digits}f}.")
        if side == "SELL" and price >= float(tick.bid) - minimum_gap:
            raise ValueError(f"SELL STOP must be below current Bid {float(tick.bid):.{digits}f}.")
        stop_distance = distance_to_points(strategy["stop_points"], strategy["stop_points_unit"], price)
        stop_loss = round(price - stop_distance if side == "BUY" else price + stop_distance, digits)
        expected_type = mt5.ORDER_TYPE_BUY_STOP if side == "BUY" else mt5.ORDER_TYPE_SELL_STOP
        matching = [
            order
            for order in (mt5.orders_get(symbol=symbol) or [])
            if int(getattr(order, "magic", 0) or 0) == TRADE_MAGIC and int(getattr(order, "type", -1)) == expected_type
        ]
        ticket = None
        broker_status = "LEVEL_SAVED"
        broker_message = "Trigger saved. Pending order will use this level when placed."
        if matching:
            order = matching[0]
            ticket = int(getattr(order, "ticket", 0) or 0)
            request = {
                "action": mt5.TRADE_ACTION_MODIFY,
                "order": ticket,
                "price": price,
                "sl": stop_loss,
                "tp": float(getattr(order, "tp", 0.0) or 0.0),
                "type_time": int(getattr(order, "type_time", mt5.ORDER_TIME_GTC)),
                "expiration": int(getattr(order, "time_expiration", 0) or 0),
            }
            response = mt5.order_send(request)
            data = response._asdict() if response is not None and hasattr(response, "_asdict") else {}
            if int(data.get("retcode", 0)) != getattr(mt5, "TRADE_RETCODE_DONE", 10009):
                raise RuntimeError(str(data.get("comment") or mt5.last_error()))
            broker_status = "ORDER_MODIFIED"
            broker_message = f"MT5 {side} STOP #{ticket} updated."
        column = "buy_trigger_override" if side == "BUY" else "sell_trigger_override"
        today = now_ist().date().isoformat()
        runtime = connection.execute("SELECT last_signal_json FROM runtime WHERE id = 1").fetchone()
        try:
            last_signal = json.loads(runtime["last_signal_json"] or "{}")
        except Exception:
            last_signal = {}
        last_signal["buy_trigger" if side == "BUY" else "sell_trigger"] = price
        last_signal["trigger_override_active"] = True
        last_signal["level_update"] = {"side": side, "status": broker_status, "message": broker_message, "ticket": ticket, "price": price, "stop_loss": stop_loss}
        with connection:
            connection.execute(
                f"UPDATE runtime SET {column} = ?, trigger_override_day = ?, last_signal_json = ? WHERE id = 1",
                (price, today, json.dumps(last_signal, separators=(",", ":"))),
            )
        first_distance = distance_to_points(strategy["first_trail_profit"], strategy["first_trail_profit_unit"], price)
        second_distance = distance_to_points(strategy["second_trail_profit"], strategy["second_trail_profit_unit"], price)
        lock_distance = distance_to_points(strategy["first_trail_lock_loss"], strategy["first_trail_lock_loss_unit"], price)
        return {
            "status": broker_status,
            "message": broker_message,
            "side": side,
            "ticket": ticket,
            "price": price,
            "stop_loss": stop_loss,
            "first_target": price + first_distance if side == "BUY" else price - first_distance,
            "first_lock": price + lock_distance if side == "BUY" else price - lock_distance,
            "second_target": price + second_distance if side == "BUY" else price - second_distance,
        }
    finally:
        mt5.shutdown()


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
        planned_stop = float(signal.get("stop_loss") or 0.0)
        stop_distance = distance_to_points(strategy["stop_points"], strategy["stop_points_unit"], price)
        fallback_stop = price - stop_distance if side == "BUY" else price + stop_distance
        stop_loss = planned_stop if planned_stop > 0 and (planned_stop < price if side == "BUY" else planned_stop > price) else fallback_stop
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
            "volume": request["volume"],
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
    add_event_log(
        connection,
        "TRADE" if str(action.get("status") or "").upper() in {"ORDER_PLACED", "ORDER_DONE", "DONE", "PLACED"} else "ERROR",
        str(action.get("status") or "TRADE_LOG"),
        f"{signal.get('side') or ''} order {action.get('status') or ''} entry {entry_price} SL {payload.get('stop_loss')}",
        strategy,
        payload,
    )


def command_status(connection: sqlite3.Connection, _: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **runtime_state(connection)}


def command_chart(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    strategy_id = str((payload or {}).get("strategy_id") or "")
    if strategy_id:
        row = connection.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    else:
        runtime = connection.execute("SELECT active_strategy_id FROM runtime WHERE id = 1").fetchone()
        row = connection.execute("SELECT * FROM strategies WHERE id = ?", (runtime["active_strategy_id"],)).fetchone()
    if row is None:
        raise ValueError("Active strategy not found.")
    strategy = row_to_strategy(row)
    if mt5 is None or not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error() if mt5 else 'package unavailable'}")
    try:
        symbol = strategy["symbol"]
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Symbol not available in MT5 Market Watch: {symbol}")
        requested = max(30, min(int((payload or {}).get("count") or 90), 180))
        rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe(strategy["timeframe"]), 0, requested)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No live chart candles returned for {symbol}.")
        candles = []
        for item in rates:
            candle_time = datetime.fromtimestamp(int(item["time"]), tz=UTC).astimezone(IST)
            candles.append({
                "time": candle_time.isoformat(),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "tick_volume": int(item["tick_volume"]),
            })
        tick = mt5.symbol_info_tick(symbol)
        return {
            "ok": True,
            "symbol": symbol,
            "timeframe": strategy["timeframe"],
            "candles": candles,
            "live_price": float((getattr(tick, "last", 0.0) or getattr(tick, "bid", 0.0)) if tick else candles[-1]["close"]),
            "server_time": now_ist().isoformat(),
        }
    finally:
        mt5.shutdown()


def command_strategies(connection: sqlite3.Connection, _: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "strategies": list_strategies(connection)}


def command_strategy_create(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    strategy = normalize_strategy(payload)
    strategy["updated_at"] = now_utc()
    with connection:
        upsert_strategy(connection, strategy)
        connection.execute(
            "UPDATE runtime SET active_strategy_id = CASE WHEN running = 0 THEN ? ELSE active_strategy_id END WHERE id = 1",
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
    strategy["updated_at"] = now_utc()
    with connection:
        upsert_strategy(connection, strategy)
        connection.execute(
            "UPDATE runtime SET active_strategy_id = CASE WHEN running = 0 THEN ? ELSE active_strategy_id END WHERE id = 1",
            (strategy_id,),
        )
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


def command_clear_all(connection: sqlite3.Connection, _: dict[str, Any]) -> dict[str, Any]:
    active_symbols = [
        str(row["symbol"] or "")
        for row in connection.execute(
            """
            SELECT DISTINCT s.symbol
            FROM runtime r
            LEFT JOIN strategies s ON s.id = r.active_strategy_id
            WHERE s.symbol IS NOT NULL AND s.symbol != ''
            """
        ).fetchall()
    ]
    cancellations = [cancel_algo_pending_orders(symbol, "All saved algo data cleared") for symbol in active_symbols]
    with connection:
        connection.execute("DELETE FROM signal_log")
        connection.execute("DELETE FROM trade_log")
        connection.execute("DELETE FROM event_log")
        connection.execute("DELETE FROM strategies")
        connection.execute(
            """
            UPDATE runtime
            SET running = 0,
                active_strategy_id = '',
                started_at = '',
                stopped_at = ?,
                last_error = '',
                algo_status = 'All saved settings cleared.',
                pending_order_day = '',
                last_signal_json = '{}',
                buy_trigger_override = NULL,
                sell_trigger_override = NULL,
                trigger_override_day = ''
            WHERE id = 1
            """,
            (now_utc(),),
        )
    state = runtime_state(connection)
    return {
        "ok": True,
        "cleared": {
            "status": "ALL_SETTINGS_CLEARED",
            "message": "All saved strategies, runtime state, and logs were cleared.",
            "cancellations": cancellations,
        },
        **state,
    }


def command_control(connection: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").lower()
    strategy_id = str(payload.get("strategy_id") or payload.get("id") or "")
    if action == "clear_all":
        return command_clear_all(connection, payload)
    if action == "select":
        if not strategy_id:
            raise ValueError("Strategy id is required.")
        if connection.execute("SELECT 1 FROM strategies WHERE id = ?", (strategy_id,)).fetchone() is None:
            raise ValueError("Strategy not found.")
        runtime = connection.execute("SELECT running FROM runtime WHERE id = 1").fetchone()
        if runtime and runtime["running"]:
            return {
                "ok": True,
                "selection": {
                    "status": "RUNNING_NOT_SWITCHED",
                    "message": "Algo is running; stop it or press Start with this strategy to switch.",
                    "strategy_id": strategy_id,
                },
                **runtime_state(connection),
            }
        with connection:
            connection.execute(
                "UPDATE runtime SET active_strategy_id = ?, last_error = '', algo_status = 'Strategy selected.' WHERE id = 1",
                (strategy_id,),
            )
        return {
            "ok": True,
            "selection": {"status": "STRATEGY_SELECTED", "message": "Strategy selected.", "strategy_id": strategy_id},
            **runtime_state(connection),
        }
    if action == "update_level":
        if not strategy_id:
            strategy_id = str(connection.execute("SELECT active_strategy_id FROM runtime WHERE id = 1").fetchone()["active_strategy_id"] or "")
        row = connection.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        if row is None:
            raise ValueError("Strategy not found.")
        update = update_pending_trigger(connection, row_to_strategy(row), str(payload.get("side") or ""), float(payload.get("price") or 0))
        return {"ok": True, "level_update": update, **runtime_state(connection)}
    if action == "reset_levels":
        if not strategy_id:
            strategy_id = str(connection.execute("SELECT active_strategy_id FROM runtime WHERE id = 1").fetchone()["active_strategy_id"] or "")
        row = connection.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        if row is None:
            raise ValueError("Strategy not found.")
        strategy = row_to_strategy(row)
        with connection:
            connection.execute(
                """
                UPDATE runtime
                SET buy_trigger_override = NULL, sell_trigger_override = NULL, trigger_override_day = ''
                WHERE id = 1
                """
            )
        cancellation = cancel_algo_pending_orders(strategy["symbol"], "Reset strategy levels")
        result = scan_signal(connection, strategy_id)
        return {
            "ok": True,
            "level_reset": {
                "status": "STRATEGY_LEVELS_RESTORED",
                "message": "Manual levels cleared. Strategy-calculated levels are active.",
                "cancellation": cancellation,
            },
            "last_signal": result,
            **runtime_state(connection),
        }
    if action == "start":
        if not strategy_id:
            runtime = connection.execute("SELECT active_strategy_id FROM runtime WHERE id = 1").fetchone()
            strategy_id = str(runtime["active_strategy_id"] or "") if runtime else ""
        if not strategy_id:
            row = connection.execute("SELECT id FROM strategies ORDER BY updated_at DESC LIMIT 1").fetchone()
            if row is None:
                raise ValueError("No strategy available to start.")
            strategy_id = row["id"]
        if connection.execute("SELECT 1 FROM strategies WHERE id = ?", (strategy_id,)).fetchone() is None:
            raise ValueError("Strategy not found.")
        previous = connection.execute(
            "SELECT s.symbol FROM runtime r LEFT JOIN strategies s ON s.id = r.active_strategy_id WHERE r.id = 1"
        ).fetchone()
        next_symbol = connection.execute("SELECT symbol FROM strategies WHERE id = ?", (strategy_id,)).fetchone()["symbol"]
        if previous and previous["symbol"] and previous["symbol"] != next_symbol:
            cancel_algo_pending_orders(previous["symbol"], "Strategy changed")
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
        active = connection.execute(
            "SELECT s.symbol FROM runtime r LEFT JOIN strategies s ON s.id = r.active_strategy_id WHERE r.id = 1"
        ).fetchone()
        if active and active["symbol"]:
            cancel_algo_pending_orders(active["symbol"], "Algo stopped")
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
    "chart": command_chart,
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
        try:
            with connection:
                add_event_log(connection, "ERROR", f"{command.upper()}_FAILED", str(exc), None, {"command": command, "payload": payload})
        except Exception:
            pass
        output({"ok": False, "error": str(exc), "trace": traceback.format_exc()})
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
