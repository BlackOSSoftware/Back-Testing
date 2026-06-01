from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, Thread

import MetaTrader5 as mt5
from flask import Blueprint, jsonify, request, send_from_directory

from .app_paths import INSTANCE_DIR, RESOURCE_DIR
from .backtest_mt5 import (
    BacktestConfig,
    IST,
    choose_first_trigger,
    ist_datetime,
    parse_time,
    previous_two_completed_high,
    previous_two_completed_low,
)
from .market_data import fetch_source_rates, normalize_source, validate_source_timeframe


ALGO_FILE = INSTANCE_DIR / "algo.json"
ALGO_MAGIC = 260530
ALGO_POLL_SECONDS = 10

_lock = Lock()
_stop_event = Event()
_worker: Thread | None = None


def now_ist() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


def default_state() -> dict:
    return {
        "running": False,
        "active_strategy_id": "",
        "started_at": "",
        "stopped_at": "",
        "last_signal": None,
        "strategies": [],
        "signal_log": [],
        "trade_log": [],
        "last_error": "",
    }


def load_state() -> dict:
    if not ALGO_FILE.exists():
        return default_state()
    data = json.loads(ALGO_FILE.read_text(encoding="utf-8"))
    state = default_state()
    state.update({key: value for key, value in data.items() if key in state})
    return state


def save_state(state: dict) -> dict:
    ALGO_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = ALGO_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(ALGO_FILE)
    return state


def normalize_strategy(data: dict) -> dict:
    source = normalize_source(data.get("data_source", "MT5"))
    strategy = {
        "id": str(data.get("id") or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(3)}"),
        "name": str(data.get("name") or data.get("strategy_name") or f"{data.get('symbol', 'BTCUSD')} {data.get('timeframe', 'M5')}").strip(),
        "data_source": source,
        "symbol": str(data.get("symbol", "BTCUSD")).strip(),
        "timeframe": str(data.get("timeframe", "M5")).upper(),
        "trail_timeframe": str(data.get("trail_timeframe", data.get("timeframe", "M5"))).upper(),
        "entry_pattern": str(data.get("entry_pattern", "BOTH")).upper(),
        "range_start": parse_time(data.get("range_start", "08:30")).strftime("%H:%M"),
        "range_end": parse_time(data.get("range_end", "09:30")).strftime("%H:%M"),
        "session_start": parse_time(data.get("session_start", "09:30")).strftime("%H:%M"),
        "entry_cutoff": parse_time(data.get("entry_cutoff", "18:00")).strftime("%H:%M"),
        "session_end": parse_time(data.get("session_end", "19:30")).strftime("%H:%M"),
        "entry_buffer_pct": float(data.get("entry_buffer_pct", 0.25)),
        "stop_points": float(data.get("stop_points", 500)),
        "first_trail_profit": float(data.get("first_trail_profit", 700)),
        "first_trail_lock_loss": float(data.get("first_trail_lock_loss", 200)),
        "second_trail_profit": float(data.get("second_trail_profit", 700)),
        "volume": float(data.get("volume", 0.01) or 0.01),
        "target_points": 0.0,
        "max_trades_per_day": 1,
        "max_open_positions": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not strategy["symbol"]:
        raise ValueError("Symbol is required.")
    validate_source_timeframe(source, strategy["timeframe"])
    validate_source_timeframe(source, strategy["trail_timeframe"])
    return strategy


def active_strategy(state: dict) -> dict | None:
    active_id = state.get("active_strategy_id")
    strategies = state.get("strategies") or []
    return next((item for item in strategies if item.get("id") == active_id), strategies[0] if strategies else None)


def config_for(strategy: dict) -> BacktestConfig:
    current = now_ist().date()
    return BacktestConfig(
        symbol=strategy["symbol"],
        from_date=current - timedelta(days=2),
        to_date=current,
        data_source=strategy["data_source"],
        timeframe=strategy["timeframe"],
        trail_timeframe=strategy["trail_timeframe"],
        entry_pattern=strategy["entry_pattern"],
        range_start=parse_time(strategy["range_start"]),
        range_end=parse_time(strategy["range_end"]),
        session_start=parse_time(strategy["session_start"]),
        entry_cutoff=parse_time(strategy["entry_cutoff"]),
        session_end=parse_time(strategy["session_end"]),
        entry_buffer_pct=float(strategy["entry_buffer_pct"]) / 100,
        stop_points=float(strategy["stop_points"]),
        first_trail_profit=float(strategy["first_trail_profit"]),
        first_trail_lock_loss=float(strategy["first_trail_lock_loss"]),
        second_trail_profit=float(strategy["second_trail_profit"]),
    )


def build_signal(strategy: dict) -> dict:
    current = now_ist()
    config = config_for(strategy)
    df = fetch_source_rates(config)
    today_df = df[df["trade_date"] == current.date()].copy()
    if today_df.empty:
        return {"phase": "NO_DATA", "status": "Waiting for candles", "message": "No candle data for today.", "checked_at": current.isoformat()}
    range_df = today_df[
        (today_df["time_ist"] >= ist_datetime(current.date(), config.range_start))
        & (today_df["time_ist"] < ist_datetime(current.date(), config.range_end))
    ]
    latest = today_df.iloc[-1]
    base = {
        "checked_at": current.isoformat(),
        "strategy_id": strategy["id"],
        "symbol": strategy["symbol"],
        "timeframe": strategy["timeframe"],
        "last_candle_time": latest["time_ist"].isoformat(),
        "last_close": float(latest["close"]),
    }
    if range_df.empty or current.time() < config.range_end:
        return {**base, "phase": "BUILDING_RANGE", "status": "Building range", "message": f"Range completes at {config.range_end.strftime('%H:%M')}."}
    range_high = float(range_df["high"].max())
    range_low = float(range_df["low"].min())
    buy_trigger = range_high * (1 + config.entry_buffer_pct)
    sell_trigger = range_low * (1 - config.entry_buffer_pct)
    base.update({"range_high": range_high, "range_low": range_low, "buy_trigger": buy_trigger, "sell_trigger": sell_trigger})
    if current.time() >= config.session_end:
        return {**base, "phase": "FORCE_EXIT_DUE", "status": "Force exit due", "message": "Force exit time passed."}
    if current.time() > config.entry_cutoff:
        return {**base, "phase": "ENTRY_CLOSED", "status": "Entry closed", "message": "Last entry time passed."}
    session_df = today_df[
        (today_df["time_ist"] >= ist_datetime(current.date(), config.session_start))
        & (today_df["time_ist"] <= current)
        & (today_df["time_ist"].dt.time <= config.entry_cutoff)
    ].reset_index(drop=True)
    side = None
    trigger_row = None
    for _, row in session_df.iterrows():
        side = choose_first_trigger(row, buy_trigger, sell_trigger, config.entry_pattern)
        if side:
            trigger_row = row
            break
    if not side or trigger_row is None:
        return {**base, "phase": "WATCHING", "status": "Watching breakout", "message": "No trigger yet."}
    entry_reference = buy_trigger if side == "BUY" else sell_trigger
    stop_loss = entry_reference - config.stop_points if side == "BUY" else entry_reference + config.stop_points
    return {
        **base,
        "phase": "SIGNAL",
        "status": f"{side} signal",
        "message": f"{side} trigger crossed.",
        "side": side,
        "entry_reference": entry_reference,
        "stop_loss": stop_loss,
        "trigger_candle_time": trigger_row["time_ist"].isoformat(),
    }


def mt5_positions(strategy: dict) -> list:
    positions = mt5.positions_get(symbol=strategy["symbol"])
    return [] if positions is None else [item for item in positions if int(getattr(item, "magic", 0)) == ALGO_MAGIC]


def mt5_pending_orders(strategy: dict) -> list:
    orders = mt5.orders_get(symbol=strategy["symbol"])
    return [] if orders is None else [item for item in orders if int(getattr(item, "magic", 0)) == ALGO_MAGIC]


def pending_sides(strategy: dict) -> list[str]:
    pattern = str(strategy.get("entry_pattern", "BOTH")).upper()
    if pattern == "BUY_ONLY":
        return ["BUY"]
    if pattern == "SELL_ONLY":
        return ["SELL"]
    return ["BUY", "SELL"]


def mt5_result_payload(result) -> dict:
    data = result._asdict() if hasattr(result, "_asdict") else {"result": str(result)}
    data["ok"] = int(data.get("retcode", 0)) in {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED}
    return data


def pending_order_payload(strategy: dict, signal: dict, side: str, info, tick) -> dict | None:
    digits = int(info.digits or 2)
    stop = float(strategy["stop_points"])
    if side == "BUY":
        price = float(signal["buy_trigger"])
        if price <= float(tick.ask):
            return None
        sl = price - stop
        order_type = mt5.ORDER_TYPE_BUY_STOP
    else:
        price = float(signal["sell_trigger"])
        if price >= float(tick.bid):
            return None
        sl = price + stop
        order_type = mt5.ORDER_TYPE_SELL_STOP
    return {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": strategy["symbol"],
        "volume": float(strategy["volume"]),
        "type": order_type,
        "price": round(price, digits),
        "sl": round(sl, digits),
        "tp": 0.0,
        "magic": ALGO_MAGIC,
        "comment": f"AlgoControl {side}",
        "type_time": mt5.ORDER_TIME_GTC,
    }


def ensure_pending_orders(strategy: dict, signal: dict, state: dict) -> None:
    if not mt5.initialize():
        state["last_error"] = f"MT5 initialize failed: {mt5.last_error()}"
        return
    try:
        symbol = strategy["symbol"]
        if not mt5.symbol_select(symbol, True):
            state["last_error"] = f"symbol_select failed: {mt5.last_error()}"
            return
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None or info is None:
            state["last_error"] = "No MT5 tick/symbol info."
            return
        if getattr(info, "trade_mode", None) == mt5.SYMBOL_TRADE_MODE_DISABLED:
            state["last_error"] = "MT5 trading is disabled for this symbol."
            return
        existing = mt5_pending_orders(strategy)
        existing_types = {int(getattr(order, "type", -1)) for order in existing}
        any_result = False
        for side in pending_sides(strategy):
            order_type = mt5.ORDER_TYPE_BUY_STOP if side == "BUY" else mt5.ORDER_TYPE_SELL_STOP
            if order_type in existing_types:
                continue
            request_payload = pending_order_payload(strategy, signal, side, info, tick)
            if request_payload is None:
                continue
            result = mt5_result_payload(mt5.order_send(request_payload))
            result["request"] = request_payload
            result["kind"] = "PENDING"
            key = f"{now_ist().date().isoformat()}:{strategy['id']}:PENDING:{side}"
            if not any(item.get("key") == key for item in state.get("trade_log", [])):
                state.setdefault("trade_log", []).append(
                    {
                        "key": key,
                        "time": now_ist().isoformat(),
                        "strategy_id": strategy["id"],
                        "symbol": strategy["symbol"],
                        "side": side,
                        "entry_reference": request_payload["price"],
                        "stop_loss": request_payload["sl"],
                        "result": result,
                    }
                )
                state["trade_log"] = state["trade_log"][-200:]
            any_result = True
            if not result.get("ok"):
                state["last_error"] = str(result)
                return
        if any_result or existing:
            state["last_error"] = ""
    finally:
        mt5.shutdown()


def cancel_pending_orders(strategy: dict) -> None:
    if not mt5.initialize():
        return
    try:
        for order in mt5_pending_orders(strategy):
            mt5.order_send(
                {
                    "action": mt5.TRADE_ACTION_REMOVE,
                    "order": int(getattr(order, "ticket")),
                    "symbol": strategy["symbol"],
                    "magic": ALGO_MAGIC,
                    "comment": "AlgoControl cancel",
                }
            )
    finally:
        mt5.shutdown()


def position_side(position) -> str:
    return "BUY" if int(getattr(position, "type", 0)) == mt5.POSITION_TYPE_BUY else "SELL"


def log_trade_event(state: dict, event: dict) -> None:
    if any(item.get("key") == event.get("key") for item in state.get("trade_log", [])):
        return
    state.setdefault("trade_log", []).append(event)
    state["trade_log"] = state["trade_log"][-200:]


def close_position(strategy: dict, position, tick, info, state: dict, reason: str) -> None:
    side = position_side(position)
    close_type = mt5.ORDER_TYPE_SELL if side == "BUY" else mt5.ORDER_TYPE_BUY
    price = float(tick.bid if side == "BUY" else tick.ask)
    digits = int(info.digits or 2)
    request_payload = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": int(getattr(position, "ticket")),
        "symbol": strategy["symbol"],
        "volume": float(getattr(position, "volume")),
        "type": close_type,
        "price": round(price, digits),
        "deviation": 50,
        "magic": ALGO_MAGIC,
        "comment": f"AlgoControl {reason}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5_result_payload(mt5.order_send(request_payload))
    result["request"] = request_payload
    result["kind"] = reason
    log_trade_event(
        state,
        {
            "key": f"{now_ist().date().isoformat()}:{strategy['id']}:{reason}:{getattr(position, 'ticket')}",
            "time": now_ist().isoformat(),
            "strategy_id": strategy["id"],
            "symbol": strategy["symbol"],
            "side": f"CLOSE {side}",
            "entry_reference": price,
            "stop_loss": getattr(position, "sl", 0.0),
            "result": result,
        },
    )
    state["last_error"] = "" if result.get("ok") else str(result)


def modify_position_sl(strategy: dict, position, new_sl: float, info, state: dict, reason: str) -> None:
    digits = int(info.digits or 2)
    request_payload = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": int(getattr(position, "ticket")),
        "symbol": strategy["symbol"],
        "sl": round(new_sl, digits),
        "tp": float(getattr(position, "tp", 0.0) or 0.0),
        "magic": ALGO_MAGIC,
        "comment": f"AlgoControl {reason}",
    }
    result = mt5_result_payload(mt5.order_send(request_payload))
    result["request"] = request_payload
    result["kind"] = reason
    log_trade_event(
        state,
        {
            "key": f"{now_ist().date().isoformat()}:{strategy['id']}:{reason}:{getattr(position, 'ticket')}:{round(new_sl, digits)}",
            "time": now_ist().isoformat(),
            "strategy_id": strategy["id"],
            "symbol": strategy["symbol"],
            "side": f"SL {position_side(position)}",
            "entry_reference": getattr(position, "price_open", 0.0),
            "stop_loss": round(new_sl, digits),
            "result": result,
        },
    )
    state["last_error"] = "" if result.get("ok") else str(result)


def trail_stop_from_candles(strategy: dict, side: str) -> float | None:
    config = config_for(strategy)
    trail_config = BacktestConfig(
        symbol=strategy["symbol"],
        from_date=now_ist().date() - timedelta(days=2),
        to_date=now_ist().date(),
        data_source=strategy["data_source"],
        timeframe=strategy["trail_timeframe"],
    )
    trail_df = fetch_source_rates(trail_config)
    if side == "BUY":
        return previous_two_completed_low(trail_df, now_ist(), config.trail_timeframe)
    return previous_two_completed_high(trail_df, now_ist(), config.trail_timeframe)


def manage_open_positions(strategy: dict, state: dict) -> bool:
    if not mt5.initialize():
        state["last_error"] = f"MT5 initialize failed: {mt5.last_error()}"
        return False
    try:
        symbol = strategy["symbol"]
        if not mt5.symbol_select(symbol, True):
            state["last_error"] = f"symbol_select failed: {mt5.last_error()}"
            return False
        positions = mt5_positions(strategy)
        if not positions:
            return False
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        if tick is None or info is None:
            state["last_error"] = "No MT5 tick/symbol info."
            return True

        config = config_for(strategy)
        current = now_ist()
        if current.time() >= config.session_end:
            for position in positions:
                close_position(strategy, position, tick, info, state, "FORCE_EXIT")
            return True

        for position in positions:
            side = position_side(position)
            entry = float(getattr(position, "price_open"))
            current_price = float(tick.bid if side == "BUY" else tick.ask)
            profit_points = current_price - entry if side == "BUY" else entry - current_price
            current_sl = float(getattr(position, "sl", 0.0) or 0.0)
            candidate_sl = None

            if profit_points >= float(strategy["first_trail_profit"]):
                first_sl = entry - float(strategy["first_trail_lock_loss"]) if side == "BUY" else entry + float(strategy["first_trail_lock_loss"])
                candidate_sl = first_sl

            if profit_points >= float(strategy["second_trail_profit"]):
                candle_sl = trail_stop_from_candles(strategy, side)
                if candle_sl is not None:
                    candidate_sl = candle_sl if candidate_sl is None else (max(candidate_sl, candle_sl) if side == "BUY" else min(candidate_sl, candle_sl))

            if candidate_sl is None:
                continue
            is_better = candidate_sl > current_sl if side == "BUY" else (current_sl <= 0 or candidate_sl < current_sl)
            if is_better:
                modify_position_sl(strategy, position, candidate_sl, info, state, "TRAIL_SL")
        return True
    finally:
        mt5.shutdown()


def send_order(strategy: dict, signal: dict) -> dict:
    if not mt5.initialize():
        return {"ok": False, "error": f"MT5 initialize failed: {mt5.last_error()}"}
    try:
        symbol = strategy["symbol"]
        if not mt5.symbol_select(symbol, True):
            return {"ok": False, "error": f"symbol_select failed: {mt5.last_error()}"}
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        if tick is None or info is None:
            return {"ok": False, "error": "No MT5 tick/symbol info."}
        if getattr(info, "trade_mode", None) == mt5.SYMBOL_TRADE_MODE_DISABLED:
            return {"ok": False, "error": "MT5 trading is disabled for this symbol."}
        side = signal.get("side")
        price = float(tick.ask if side == "BUY" else tick.bid)
        digits = int(info.digits or 2)
        stop = float(strategy["stop_points"])
        target = 0.0
        sl = price - stop if side == "BUY" else price + stop
        tp = 0.0 if target <= 0 else (price + target if side == "BUY" else price - target)
        request_payload = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(strategy["volume"]),
            "type": mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": round(price, digits),
            "sl": round(sl, digits),
            "tp": round(tp, digits) if tp else 0.0,
            "deviation": 50,
            "magic": ALGO_MAGIC,
            "comment": "AlgoControl",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request_payload)
        data = result._asdict() if hasattr(result, "_asdict") else {"result": str(result)}
        data["ok"] = int(data.get("retcode", 0)) in {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED}
        data["request"] = request_payload
        return data
    finally:
        mt5.shutdown()


def today_count(state: dict, strategy_id: str) -> int:
    today = now_ist().date().isoformat()
    return sum(1 for item in state.get("trade_log", []) if item.get("strategy_id") == strategy_id and str(item.get("time", "")).startswith(today))


def update_state(state: dict, execute: bool) -> dict:
    strategy = active_strategy(state)
    if not strategy:
        state["last_signal"] = None
        return state
    signal = build_signal(strategy)
    state["last_signal"] = signal
    if execute and state.get("running"):
        has_position = manage_open_positions(strategy, state)
        if signal.get("phase") in {"ENTRY_CLOSED", "FORCE_EXIT_DUE"}:
            cancel_pending_orders(strategy)
        elif signal.get("phase") in {"WATCHING", "SIGNAL"} and not has_position:
            if not mt5.initialize():
                state["last_error"] = f"MT5 initialize failed: {mt5.last_error()}"
            else:
                try:
                    open_positions = len(mt5_positions(strategy))
                finally:
                    mt5.shutdown()
                if open_positions:
                    cancel_pending_orders(strategy)
                    state["last_error"] = ""
                else:
                    ensure_pending_orders(strategy, signal, state)
    return state


def public_state(state: dict) -> dict:
    today = now_ist().date().isoformat()
    trades_today = [item for item in state.get("trade_log", []) if str(item.get("time", "")).startswith(today)]
    return {**state, "active_strategy": active_strategy(state), "trades_today": len(trades_today), "recent_trades": list(reversed(state.get("trade_log", [])[-25:]))}


def worker_loop() -> None:
    while not _stop_event.is_set():
        with _lock:
            state = load_state()
            if not state.get("running"):
                break
            try:
                state = update_state(state, execute=True)
            except Exception as exc:
                state["last_error"] = str(exc)
            save_state(state)
        if _stop_event.wait(ALGO_POLL_SECONDS):
            break


def ensure_worker() -> None:
    global _worker
    if _worker and _worker.is_alive():
        return
    _stop_event.clear()
    _worker = Thread(target=worker_loop, name="algo-control-worker", daemon=True)
    _worker.start()


def create_algo_blueprint(auth_required, csrf_is_valid) -> Blueprint:
    bp = Blueprint("algo_control", __name__)

    @bp.get("/algo")
    @auth_required
    def page():
        return send_from_directory(RESOURCE_DIR, "algo.html")

    @bp.get("/algo.css")
    @auth_required
    def css():
        return send_from_directory(RESOURCE_DIR, "algo.css")

    @bp.get("/algo.js")
    @auth_required
    def js():
        return send_from_directory(RESOURCE_DIR, "algo.js")

    @bp.get("/api/algo/status")
    @auth_required
    def status():
        with _lock:
            state = update_state(load_state(), execute=False)
            save_state(state)
            should_run = bool(state.get("running"))
        if should_run:
            ensure_worker()
        with _lock:
            state = load_state()
            return jsonify(public_state(state))

    @bp.post("/api/algo/strategies")
    @auth_required
    def save_strategy():
        if not csrf_is_valid():
            return jsonify({"error": "Invalid security token."}), 400
        try:
            strategy = normalize_strategy(request.get_json(force=True))
            with _lock:
                state = load_state()
                strategies = [item for item in state.get("strategies", []) if item.get("id") != strategy["id"]]
                strategies.insert(0, strategy)
                state["strategies"] = strategies[:50]
                state["active_strategy_id"] = strategy["id"]
                state = update_state(state, execute=False)
                save_state(state)
            return jsonify({"message": "Strategy saved.", "algo": public_state(state)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @bp.post("/api/algo/apply")
    @auth_required
    def apply_strategy():
        if not csrf_is_valid():
            return jsonify({"error": "Invalid security token."}), 400
        strategy_id = str(request.get_json(force=True).get("strategy_id", ""))
        with _lock:
            state = load_state()
            if not any(item.get("id") == strategy_id for item in state.get("strategies", [])):
                return jsonify({"error": "Saved strategy not found."}), 400
            state["active_strategy_id"] = strategy_id
            state = update_state(state, execute=False)
            save_state(state)
            return jsonify({"message": "Strategy applied.", "algo": public_state(state)})

    @bp.post("/api/algo/start")
    @auth_required
    def start():
        if not csrf_is_valid():
            return jsonify({"error": "Invalid security token."}), 400
        with _lock:
            state = load_state()
            if not active_strategy(state):
                return jsonify({"error": "Save/select a strategy first."}), 400
            state["running"] = True
            state["started_at"] = datetime.now(timezone.utc).isoformat()
            state["stopped_at"] = ""
            state = update_state(state, execute=True)
            save_state(state)
            ensure_worker()
            return jsonify({"message": "Algo started.", "algo": public_state(state)})

    @bp.post("/api/algo/stop")
    @auth_required
    def stop():
        if not csrf_is_valid():
            return jsonify({"error": "Invalid security token."}), 400
        with _lock:
            state = load_state()
            strategy = active_strategy(state)
            if strategy:
                cancel_pending_orders(strategy)
            state["running"] = False
            state["stopped_at"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
            _stop_event.set()
            return jsonify({"message": "Algo stopped.", "algo": public_state(state)})

    @bp.post("/api/algo/check")
    @auth_required
    def check():
        if not csrf_is_valid():
            return jsonify({"error": "Invalid security token."}), 400
        with _lock:
            state = update_state(load_state(), execute=True)
            save_state(state)
            return jsonify({"message": "Checked.", "algo": public_state(state)})

    return bp
