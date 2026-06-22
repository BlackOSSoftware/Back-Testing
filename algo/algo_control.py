from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, Thread

import MetaTrader5 as mt5
from flask import Blueprint, jsonify, request, send_from_directory

from .app_paths import INSTANCE_DIR, RESOURCE_DIR
from .strategy_core import (
    BacktestConfig,
    IST,
    choose_first_trigger,
    clear_rates_cache,
    current_and_previous_high,
    current_and_previous_low,
    ist_datetime,
    parse_time,
)
from .market_data import fetch_source_rates, normalize_source, validate_source_timeframe


ALGO_FILE = INSTANCE_DIR / "algo.json"
ALGO_MAGIC = 260530
ALGO_POLL_SECONDS = 2

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
        "position_state": {},
        "last_error": "",
        "algo_status": "Stopped.",
        "pending_order_day": "",
    }


def load_state() -> dict:
    if not ALGO_FILE.exists():
        return default_state()
    data = json.loads(ALGO_FILE.read_text(encoding="utf-8-sig"))
    state = default_state()
    state.update({key: value for key, value in data.items() if key in state})
    return state


def save_state(state: dict) -> dict:
    ALGO_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = ALGO_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(ALGO_FILE)
    return state


def algo_is_running() -> bool:
    try:
        return bool(load_state().get("running"))
    except Exception:
        return False


def normalize_strategy(data: dict) -> dict:
    source = normalize_source(data.get("data_source", "DELTA"))
    strategy = {
        "id": str(data.get("id") or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(3)}"),
        "name": str(data.get("name") or data.get("strategy_name") or f"{data.get('symbol', 'BTCUSD')} {data.get('timeframe', 'M5')}").strip(),
        "data_source": source,
        "symbol": str(data.get("symbol", "BTCUSD")).strip(),
        "timeframe": str(data.get("timeframe", "M5")).upper(),
        "trail_timeframe": str(data.get("trail_timeframe", "M15")).upper(),
        "entry_pattern": str(data.get("entry_pattern", "BOTH")).upper(),
        "range_start": parse_time(data.get("range_start", "08:30")).strftime("%H:%M"),
        "range_end": parse_time(data.get("range_end", "09:30")).strftime("%H:%M"),
        "session_start": parse_time(data.get("session_start", "09:30")).strftime("%H:%M"),
        "entry_cutoff": parse_time(data.get("entry_cutoff", "18:00")).strftime("%H:%M"),
        "session_end": parse_time(data.get("session_end", "19:30")).strftime("%H:%M"),
        "entry_buffer_pct": float(data.get("entry_buffer_pct", 0.25)),
        "entry_buffer_points": float(data.get("entry_buffer_points", 0) or 0),
        "stop_points": float(data.get("stop_points", 400)),
        "first_trail_profit": float(data.get("first_trail_profit", 400)),
        "first_trail_lock_loss": float(data.get("first_trail_lock_loss", 300)),
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
    if str(strategy.get("data_source", "MT5")).upper() == "MT5":
        clear_rates_cache()
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
    buffer_points = float(strategy.get("entry_buffer_points", 0) or 0)
    if buffer_points > 0:
        buy_trigger = range_high + buffer_points
        sell_trigger = range_low - buffer_points
        buffer_label = f"{buffer_points:g} points"
    else:
        buy_trigger = range_high * (1 + config.entry_buffer_pct)
        sell_trigger = range_low * (1 - config.entry_buffer_pct)
        buffer_label = f"{float(strategy.get('entry_buffer_pct', 0.0)):g}%"
    base.update({"range_high": range_high, "range_low": range_low, "buy_trigger": buy_trigger, "sell_trigger": sell_trigger, "buffer": buffer_label})
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


def pending_order_side(order) -> str:
    order_type = int(getattr(order, "type", -1))
    if order_type == mt5.ORDER_TYPE_BUY_STOP:
        return "BUY"
    if order_type == mt5.ORDER_TYPE_SELL_STOP:
        return "SELL"
    return ""


def mt5_result_payload(result) -> dict:
    data = result._asdict() if hasattr(result, "_asdict") else {"result": str(result)}
    data["ok"] = int(data.get("retcode", 0)) in {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED}
    return data


def set_algo_status(state: dict, message: str) -> None:
    state["algo_status"] = f"{now_ist().strftime('%H:%M:%S')} - {message}"


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


def ensure_pending_orders(strategy: dict, signal: dict, state: dict) -> int:
    if not mt5.initialize():
        state["last_error"] = f"MT5 initialize failed: {mt5.last_error()}"
        set_algo_status(state, state["last_error"])
        return 0
    try:
        symbol = strategy["symbol"]
        if not mt5.symbol_select(symbol, True):
            state["last_error"] = f"symbol_select failed: {mt5.last_error()}"
            set_algo_status(state, state["last_error"])
            return 0
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None or info is None:
            state["last_error"] = "No MT5 tick/symbol info."
            set_algo_status(state, state["last_error"])
            return 0
        if getattr(info, "trade_mode", None) == mt5.SYMBOL_TRADE_MODE_DISABLED:
            state["last_error"] = "MT5 trading is disabled for this symbol."
            set_algo_status(state, state["last_error"])
            return 0
        existing = mt5_pending_orders(strategy)
        any_result = False
        active_count = 0
        for side in pending_sides(strategy):
            order_type = mt5.ORDER_TYPE_BUY_STOP if side == "BUY" else mt5.ORDER_TYPE_SELL_STOP
            request_payload = pending_order_payload(strategy, signal, side, info, tick)
            if request_payload is None:
                continue
            desired_price = float(request_payload["price"])
            desired_sl = float(request_payload["sl"])
            matching_order = None
            for order in existing:
                if int(getattr(order, "type", -1)) != order_type:
                    continue
                same_price = round(float(getattr(order, "price_open", 0.0)), digits) == round(desired_price, digits)
                same_sl = round(float(getattr(order, "sl", 0.0) or 0.0), digits) == round(desired_sl, digits)
                if same_price and same_sl:
                    matching_order = order
                    break
                mt5.order_send(
                    {
                        "action": mt5.TRADE_ACTION_REMOVE,
                        "order": int(getattr(order, "ticket")),
                        "symbol": strategy["symbol"],
                        "magic": ALGO_MAGIC,
                        "comment": "AlgoControl reprice",
                    }
                )
            if matching_order is not None:
                active_count += 1
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
                set_algo_status(state, f"Pending {side} failed.")
                return active_count
            active_count += 1
        if any_result or existing:
            state["last_error"] = ""
        set_algo_status(state, f"Watching pending orders. Active pending: {active_count}.")
        return active_count
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


def cancel_opposite_pending_orders(strategy: dict, keep_side: str, state: dict) -> int:
    keep_side = str(keep_side or "").upper()
    if keep_side not in {"BUY", "SELL"}:
        return 0
    if not mt5.initialize():
        state["last_error"] = f"MT5 initialize failed: {mt5.last_error()}"
        set_algo_status(state, state["last_error"])
        return 0
    try:
        remaining = 0
        cancelled = 0
        for order in mt5_pending_orders(strategy):
            side = pending_order_side(order)
            if side == keep_side:
                remaining += 1
                continue
            result = mt5_result_payload(
                mt5.order_send(
                    {
                        "action": mt5.TRADE_ACTION_REMOVE,
                        "order": int(getattr(order, "ticket")),
                        "symbol": strategy["symbol"],
                        "magic": ALGO_MAGIC,
                        "comment": f"AlgoControl OCO keep {keep_side}",
                    }
                )
            )
            if result.get("ok"):
                cancelled += 1
            else:
                state["last_error"] = str(result)
        if cancelled and not state.get("last_error"):
            set_algo_status(state, f"{keep_side} signal confirmed. Opposite pending order cancelled.")
        return remaining
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
        return current_and_previous_low(trail_df, now_ist(), config.trail_timeframe)
    return current_and_previous_high(trail_df, now_ist(), config.trail_timeframe)


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
            state["position_state"] = {}
            return False
        active_tickets = {str(getattr(position, "ticket")) for position in positions}
        position_state = {
            ticket: value
            for ticket, value in dict(state.get("position_state") or {}).items()
            if ticket in active_tickets
        }
        state["position_state"] = position_state
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
            ticket = str(getattr(position, "ticket"))
            trail_state = position_state.setdefault(ticket, {})
            side = position_side(position)
            entry = float(getattr(position, "price_open"))
            current_price = float(tick.bid if side == "BUY" else tick.ask)
            profit_points = current_price - entry if side == "BUY" else entry - current_price
            current_sl = float(getattr(position, "sl", 0.0) or 0.0)
            candidate_sl = None
            candidate_reason = "TRAIL_SL"

            if profit_points >= float(strategy["first_trail_profit"]):
                first_sl = entry + float(strategy["first_trail_lock_loss"]) if side == "BUY" else entry - float(strategy["first_trail_lock_loss"])
                candidate_sl = first_sl

            second_trail_active = bool(trail_state.get("second_trail_active") or trail_state.get("second_trail_checked"))
            if profit_points >= float(strategy["second_trail_profit"]):
                second_trail_active = True
                trail_state["second_trail_active"] = True
                trail_state["second_trail_checked"] = True
                trail_state.setdefault("second_trail_time", current.isoformat())

            if second_trail_active:
                candle_sl = trail_stop_from_candles(strategy, side)
                if candle_sl is not None:
                    candidate_sl = candle_sl if candidate_sl is None else (max(candidate_sl, candle_sl) if side == "BUY" else min(candidate_sl, candle_sl))
                    candidate_reason = "TWO_CANDLE_TRAIL_SL"

            if candidate_sl is None:
                continue
            is_better = candidate_sl > current_sl if side == "BUY" else (current_sl <= 0 or candidate_sl < current_sl)
            if is_better:
                modify_position_sl(strategy, position, candidate_sl, info, state, candidate_reason)
        return True
    finally:
        mt5.shutdown()


def update_state(state: dict, execute: bool) -> dict:
    strategy = active_strategy(state)
    if not strategy:
        state["last_signal"] = None
        set_algo_status(state, "No active strategy selected.")
        return state
    signal = build_signal(strategy)
    state["last_signal"] = signal
    state["last_error"] = ""
    today = now_ist().date().isoformat()
    if execute and state.get("running"):
        if signal.get("phase") in {"BUILDING_RANGE", "NO_DATA"}:
            set_algo_status(state, signal.get("message", "Waiting for candles."))
            return state
        if state.get("pending_order_day") and state.get("pending_order_day") != today:
            cancel_pending_orders(strategy)
            state["pending_order_day"] = ""
            set_algo_status(state, "New day detected. Old pending orders cancelled.")
        has_position = manage_open_positions(strategy, state)
        if signal.get("phase") in {"ENTRY_CLOSED", "FORCE_EXIT_DUE"}:
            cancel_pending_orders(strategy)
            state["pending_order_day"] = ""
            set_algo_status(state, "Entry window closed. Pending orders cancelled; managing exits only.")
        elif signal.get("phase") == "WATCHING" and not has_position:
            if not mt5.initialize():
                state["last_error"] = f"MT5 initialize failed: {mt5.last_error()}"
                set_algo_status(state, state["last_error"])
            else:
                try:
                    open_positions = len(mt5_positions(strategy))
                finally:
                    mt5.shutdown()
                if open_positions:
                    cancel_pending_orders(strategy)
                    state["last_error"] = ""
                    set_algo_status(state, "Open position found. Pending orders cancelled; managing trade.")
                else:
                    active_count = ensure_pending_orders(strategy, signal, state)
                    if active_count:
                        state["pending_order_day"] = today
                    elif not state.get("last_error"):
                        set_algo_status(state, "Price already crossed a trigger; not chasing market. Waiting for next valid pending setup.")
        elif signal.get("phase") == "SIGNAL" and not has_position:
            remaining_same_side = cancel_opposite_pending_orders(strategy, signal.get("side", ""), state)
            if mt5.initialize():
                try:
                    pending_count = len(mt5_pending_orders(strategy))
                    open_positions = len(mt5_positions(strategy))
                finally:
                    mt5.shutdown()
                if open_positions:
                    cancel_pending_orders(strategy)
                    state["last_error"] = ""
                    set_algo_status(state, "Trigger filled. Open position active; managing trade.")
                elif pending_count:
                    side = signal.get("side", "signal")
                    set_algo_status(state, f"{side} signal confirmed. Opposite pending cancelled. Active pending: {pending_count}. Waiting for MT5 fill.")
                elif remaining_same_side:
                    side = signal.get("side", "signal")
                    set_algo_status(state, f"{side} signal confirmed. Waiting for same-side pending fill.")
                else:
                    set_algo_status(state, "Trigger already crossed but no pending order is active. No market order will be sent; waiting for next valid setup/day.")
            else:
                state["last_error"] = f"MT5 initialize failed: {mt5.last_error()}"
                set_algo_status(state, state["last_error"])
        elif has_position:
            set_algo_status(state, "Open position active. Managing trailing stop and force exit.")
        else:
            set_algo_status(state, signal.get("message", "Checking market."))
    elif state.get("running"):
        if not state.get("algo_status"):
            set_algo_status(state, f"Worker running. Phase: {signal.get('phase', 'WAIT')}.")
    else:
        set_algo_status(state, f"Stopped/check-only. Phase: {signal.get('phase', 'WAIT')}.")
    return state


def public_state(state: dict) -> dict:
    today = now_ist().date().isoformat()
    trades_today = [
        item
        for item in state.get("trade_log", [])
        if str(item.get("time", "")).startswith(today) and (item.get("result") or {}).get("kind") != "PENDING"
    ]
    public = {**state}
    signal_phase = (public.get("last_signal") or {}).get("phase")
    status_text = str(public.get("algo_status", "")).lower()
    if signal_phase in {"BUILDING_RANGE", "NO_DATA"} and "failed" not in status_text and "error" not in status_text:
        public["last_error"] = ""
    return {**public, "active_strategy": active_strategy(state), "trades_today": len(trades_today), "recent_trades": list(reversed(state.get("trade_log", [])[-25:]))}


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

    @bp.post("/api/algo/clear-logs")
    @auth_required
    def clear_logs():
        if not csrf_is_valid():
            return jsonify({"error": "Invalid security token."}), 400
        with _lock:
            state = load_state()
            state["trade_log"] = []
            state["signal_log"] = []
            state["last_error"] = ""
            save_state(state)
            return jsonify({"message": "Trade logs cleared.", "algo": public_state(state)})

    try:
        if load_state().get("running"):
            ensure_worker()
    except Exception:
        pass

    return bp
