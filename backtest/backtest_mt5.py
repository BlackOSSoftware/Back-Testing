from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Iterable
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5
import pandas as pd

from .app_paths import REPORT_DATA_FILE, RESULTS_DIR


IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M2": mt5.TIMEFRAME_M2,
    "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4,
    "M5": mt5.TIMEFRAME_M5,
    "M10": mt5.TIMEFRAME_M10,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}
TIMEFRAME_MINUTES = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
    "M4": 4,
    "M5": 5,
    "M10": 10,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
}

RATES_CACHE_SECONDS = 60.0
MT5_FETCH_CHUNK_DAYS = max(int(os.getenv("BACKTEST_MT5_CHUNK_DAYS", "30")), 1)
PRICE_EPSILON = 1e-9
_rates_cache: dict[tuple[str, str, date, date], tuple[float, pd.DataFrame]] = {}
RISK_DISTANCE_UNITS = {"POINTS", "PERCENT"}


def normalize_distance_unit(value: str | None) -> str:
    text = str(value or "POINTS").strip().upper()
    if text in {"POINT", "POINTS", "PTS"}:
        return "POINTS"
    if text in {"PERCENT", "PERCENTAGE", "PCT", "%"}:
        return "PERCENT"
    raise ValueError("Risk distance unit must be POINTS or PERCENT.")


def distance_to_points(value: float, unit: str, entry_price: float) -> float:
    amount = float(value)
    if normalize_distance_unit(unit) == "PERCENT":
        return float(entry_price) * amount / 100
    return amount


def points_to_percent(points: float, entry_price: float) -> float:
    entry = float(entry_price)
    return (float(points) / entry) * 100 if entry else 0.0


def clear_rates_cache() -> None:
    _rates_cache.clear()


@dataclass
class Trade:
    trade_date: str
    symbol: str
    side: str
    range_high: float
    range_low: float
    trigger_price: float
    entry_time: str
    entry_price: float
    initial_sl: float
    first_trail_time: str | None
    first_trail_sl: float | None
    two_candle_trail_time: str | None
    two_candle_trail_sl: float | None
    exit_time: str
    exit_price: float
    exit_reason: str
    pnl_points: float
    pnl_pct: float
    mfe_points: float
    mfe_pct: float
    mae_points: float
    mae_pct: float
    stop_distance_points: float
    stop_distance_pct: float
    first_trail_profit_points: float
    first_trail_profit_pct: float
    first_trail_lock_points: float
    first_trail_lock_pct: float
    second_trail_profit_points: float
    second_trail_profit_pct: float


@dataclass
class BacktestConfig:
    symbol: str
    from_date: date
    to_date: date
    data_source: str = "DELTA"
    timeframe: str = "M5"
    trail_timeframe: str = "M15"
    range_start: time = time(8, 30)
    range_end: time = time(9, 30)
    session_start: time = time(9, 30)
    entry_cutoff: time = time(18, 0)
    session_end: time = time(19, 30)
    entry_pattern: str = "BOTH"
    entry_buffer_pct: float = 0.0025
    stop_points: float = 400.0
    first_trail_profit: float = 400.0
    first_trail_lock_loss: float = 300.0
    second_trail_profit: float = 700.0
    stop_points_unit: str = "POINTS"
    first_trail_profit_unit: str = "POINTS"
    first_trail_lock_loss_unit: str = "POINTS"
    second_trail_profit_unit: str = "POINTS"
    data_from_date: date | None = None
    data_to_date: date | None = None

    def __post_init__(self) -> None:
        self.stop_points_unit = normalize_distance_unit(self.stop_points_unit)
        self.first_trail_profit_unit = normalize_distance_unit(self.first_trail_profit_unit)
        self.first_trail_lock_loss_unit = normalize_distance_unit(self.first_trail_lock_loss_unit)
        self.second_trail_profit_unit = normalize_distance_unit(self.second_trail_profit_unit)

    def stop_distance_points(self, entry_price: float) -> float:
        return distance_to_points(self.stop_points, self.stop_points_unit, entry_price)

    def first_trail_profit_points(self, entry_price: float) -> float:
        return distance_to_points(self.first_trail_profit, self.first_trail_profit_unit, entry_price)

    def first_trail_lock_points(self, entry_price: float) -> float:
        return distance_to_points(self.first_trail_lock_loss, self.first_trail_lock_loss_unit, entry_price)

    def second_trail_profit_points(self, entry_price: float) -> float:
        return distance_to_points(self.second_trail_profit, self.second_trail_profit_unit, entry_price)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def ist_datetime(day: date, value: time) -> datetime:
    return datetime.combine(day, value, tzinfo=IST)


def fetch_rates(config: BacktestConfig) -> pd.DataFrame:
    timeframe = config.timeframe.upper()
    if timeframe not in TIMEFRAMES:
        raise RuntimeError(f"Unsupported timeframe: {config.timeframe}")

    cache_key = (config.symbol, timeframe, config.from_date, config.to_date)
    cached = _rates_cache.get(cache_key)
    if cached and monotonic() - cached[0] < RATES_CACHE_SECONDS:
        return cached[1].copy()

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    try:
        if not mt5.symbol_select(config.symbol, True):
            raise RuntimeError(f"Symbol not available in MT5 Market Watch: {config.symbol}")

        time_shift_hours = detect_broker_time_shift_hours(config.symbol)
        rates = copy_rates_range_chunked(config.symbol, TIMEFRAMES[timeframe], config.from_date, config.to_date)
    finally:
        mt5.shutdown()

    if rates is None or len(rates) == 0:
        raise RuntimeError("No MT5 candle data returned. Check symbol name, date range, and broker history.")

    df = pd.DataFrame(rates)
    df = df.drop_duplicates(subset=["time"], keep="last").sort_values("time")
    df["time_utc"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df["time_ist"] = df["time_utc"].dt.tz_convert(IST)
    df["time_ist"] = df["time_ist"] - pd.Timedelta(hours=time_shift_hours)
    df["trade_date"] = df["time_ist"].dt.date
    df = df[(df["trade_date"] >= config.from_date) & (df["trade_date"] <= config.to_date)]
    df = df[["time_ist", "trade_date", "open", "high", "low", "close", "tick_volume"]].copy()
    _rates_cache[cache_key] = (monotonic(), df)
    return df.copy()


def copy_rates_range_chunked(symbol: str, timeframe: int, from_date: date, to_date: date):
    frames = []
    cursor = from_date
    while cursor <= to_date:
        chunk_to = min(cursor + timedelta(days=MT5_FETCH_CHUNK_DAYS - 1), to_date)
        start_ist = ist_datetime(cursor, time(0, 0))
        end_ist = ist_datetime(chunk_to + timedelta(days=1), time(0, 0))
        rates = mt5.copy_rates_range(
            symbol,
            timeframe,
            start_ist.astimezone(UTC),
            end_ist.astimezone(UTC),
        )
        if rates is not None and len(rates) > 0:
            frames.append(pd.DataFrame(rates))
        cursor = chunk_to + timedelta(days=1)

    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def detect_broker_time_shift_hours(symbol: str) -> int:
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or not getattr(tick, "time", None):
        return 0
    now_ist = datetime.now(IST)
    tick_ist = datetime.fromtimestamp(int(tick.time), tz=UTC).astimezone(IST)
    skew = tick_ist - now_ist

    if timedelta(minutes=20) < skew < timedelta(hours=12):
        return max(0, round(skew.total_seconds() / 3600))

    return 0


def current_and_previous_trail_position(candles: pd.DataFrame, candle_time: datetime, timeframe: str) -> int:
    """Return the newest trail candle that is fully closed at candle_time."""
    cache_key = f"start_times_{timeframe.upper()}"
    start_times = candles.attrs.get(cache_key)
    if start_times is None:
        start_times = candles["time_ist"].reset_index(drop=True)
        candles.attrs[cache_key] = start_times
    timeframe_minutes = TIMEFRAME_MINUTES[timeframe.upper()]
    last_closed_start = pd.Timestamp(candle_time) - pd.Timedelta(minutes=timeframe_minutes)
    return int(start_times.searchsorted(last_closed_start, side="right")) - 1


def price_at_or_above(price: float, level: float) -> bool:
    return price + PRICE_EPSILON >= level


def price_at_or_below(price: float, level: float) -> bool:
    return price <= level + PRICE_EPSILON


def higher_stop(candidate: float, current: float) -> bool:
    return candidate > current + PRICE_EPSILON


def lower_stop(candidate: float, current: float) -> bool:
    return candidate < current - PRICE_EPSILON


def first_trail_lock_stop(entry_price: float, lock_distance: float, side: str) -> float:
    effective_lock = max(float(lock_distance), 0.0)
    return entry_price + effective_lock if side == "BUY" else entry_price - effective_lock


def current_and_previous_low(candles: pd.DataFrame, candle_time: datetime, timeframe: str) -> float | None:
    position = current_and_previous_trail_position(candles, candle_time, timeframe)
    if position < 1:
        return None
    return float(candles.iloc[position - 1 : position + 1]["low"].min())


def current_and_previous_high(candles: pd.DataFrame, candle_time: datetime, timeframe: str) -> float | None:
    position = current_and_previous_trail_position(candles, candle_time, timeframe)
    if position < 1:
        return None
    return float(candles.iloc[position - 1 : position + 1]["high"].max())


def choose_first_trigger(row: pd.Series, buy_trigger: float, sell_trigger: float, entry_pattern: str = "BOTH") -> str | None:
    buy_hit = price_at_or_above(float(row["high"]), buy_trigger)
    sell_hit = price_at_or_below(float(row["low"]), sell_trigger)
    pattern = (entry_pattern or "BOTH").upper()

    if pattern == "BUY_ONLY":
        return "BUY" if buy_hit else None
    if pattern == "SELL_ONLY":
        return "SELL" if sell_hit else None

    if buy_hit and not sell_hit:
        return "BUY"
    if sell_hit and not buy_hit:
        return "SELL"
    if buy_hit and sell_hit:
        open_price = float(row["open"])
        buy_distance = abs(buy_trigger - open_price)
        sell_distance = abs(open_price - sell_trigger)
        return "BUY" if buy_distance <= sell_distance else "SELL"
    return None


def candle_end_time(candles: pd.DataFrame, idx: int, config: BacktestConfig) -> datetime:
    if idx + 1 < len(candles):
        return candles.iloc[idx + 1]["time_ist"]
    return candles.iloc[idx]["time_ist"] + timedelta(minutes=TIMEFRAME_MINUTES[config.timeframe.upper()])


def choose_execution_trigger(
    candles: pd.DataFrame,
    buy_trigger: float,
    sell_trigger: float,
    entry_pattern: str = "BOTH",
) -> tuple[str, int, datetime] | None:
    pattern = (entry_pattern or "BOTH").upper()
    for idx, row in candles.iterrows():
        side = choose_first_trigger(row, buy_trigger, sell_trigger, pattern)
        if side is not None:
            return side, int(idx), row["time_ist"]
    return None


def manage_buy_trade(
    candles: pd.DataFrame,
    trail_candles: pd.DataFrame,
    start_idx: int,
    entry_price: float,
    config: BacktestConfig,
) -> tuple[str, float, str, float, float, str | None, float | None, str | None, float | None]:
    stop_distance = config.stop_distance_points(entry_price)
    first_profit_distance = config.first_trail_profit_points(entry_price)
    first_lock_distance = config.first_trail_lock_points(entry_price)
    second_profit_distance = config.second_trail_profit_points(entry_price)
    stop = entry_price - stop_distance
    stop_reason = "INITIAL_SL"
    first_trail_time = None
    first_trail_sl = None
    two_candle_trail_time = None
    two_candle_trail_sl = None
    second_trail_active = False
    mfe = 0.0
    mae = 0.0

    for idx in range(start_idx, len(candles)):
        row = candles.iloc[idx]
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        candle_time = row["time_ist"]

        mfe = max(mfe, high - entry_price)
        mae = min(mae, low - entry_price)

        if second_trail_active:
            trail = current_and_previous_low(trail_candles, candle_time, config.trail_timeframe)
            if trail is not None and higher_stop(trail, stop):
                stop = trail
                stop_reason = "TWO_CANDLE_TRAIL_SL"
                two_candle_trail_time = candle_time.isoformat()
                two_candle_trail_sl = stop

        if price_at_or_below(low, stop):
            return candle_time.isoformat(), stop, stop_reason, mfe, mae, first_trail_time, first_trail_sl, two_candle_trail_time, two_candle_trail_sl

        if price_at_or_above(high, entry_price + first_profit_distance):
            first_trail_stop = first_trail_lock_stop(entry_price, first_lock_distance, "BUY")
            if price_at_or_above(high, first_trail_stop) and higher_stop(first_trail_stop, stop):
                stop = first_trail_stop
                stop_reason = "FIRST_TRAIL_SL"
                first_trail_time = candle_time.isoformat()
                first_trail_sl = stop

        if price_at_or_above(high, entry_price + second_profit_distance) and not second_trail_active:
            second_trail_active = True
            trail = current_and_previous_low(trail_candles, candle_time, config.trail_timeframe)
            if trail is not None and higher_stop(trail, stop):
                stop = trail
                stop_reason = "TWO_CANDLE_TRAIL_SL"
                two_candle_trail_time = candle_time.isoformat()
                two_candle_trail_sl = stop

        if candle_time.time() >= config.session_end:
            return candle_time.isoformat(), close, "FORCE_EXIT", mfe, mae, first_trail_time, first_trail_sl, two_candle_trail_time, two_candle_trail_sl

    last = candles.iloc[-1]
    return last["time_ist"].isoformat(), float(last["close"]), "DATA_END", mfe, mae, first_trail_time, first_trail_sl, two_candle_trail_time, two_candle_trail_sl


def manage_sell_trade(
    candles: pd.DataFrame,
    trail_candles: pd.DataFrame,
    start_idx: int,
    entry_price: float,
    config: BacktestConfig,
) -> tuple[str, float, str, float, float, str | None, float | None, str | None, float | None]:
    stop_distance = config.stop_distance_points(entry_price)
    first_profit_distance = config.first_trail_profit_points(entry_price)
    first_lock_distance = config.first_trail_lock_points(entry_price)
    second_profit_distance = config.second_trail_profit_points(entry_price)
    stop = entry_price + stop_distance
    stop_reason = "INITIAL_SL"
    first_trail_time = None
    first_trail_sl = None
    two_candle_trail_time = None
    two_candle_trail_sl = None
    second_trail_active = False
    mfe = 0.0
    mae = 0.0

    for idx in range(start_idx, len(candles)):
        row = candles.iloc[idx]
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        candle_time = row["time_ist"]

        mfe = max(mfe, entry_price - low)
        mae = min(mae, entry_price - high)

        if second_trail_active:
            trail = current_and_previous_high(trail_candles, candle_time, config.trail_timeframe)
            if trail is not None and lower_stop(trail, stop):
                stop = trail
                stop_reason = "TWO_CANDLE_TRAIL_SL"
                two_candle_trail_time = candle_time.isoformat()
                two_candle_trail_sl = stop

        if price_at_or_above(high, stop):
            return candle_time.isoformat(), stop, stop_reason, mfe, mae, first_trail_time, first_trail_sl, two_candle_trail_time, two_candle_trail_sl

        if price_at_or_below(low, entry_price - first_profit_distance):
            first_trail_stop = first_trail_lock_stop(entry_price, first_lock_distance, "SELL")
            if price_at_or_below(low, first_trail_stop) and lower_stop(first_trail_stop, stop):
                stop = first_trail_stop
                stop_reason = "FIRST_TRAIL_SL"
                first_trail_time = candle_time.isoformat()
                first_trail_sl = stop

        if price_at_or_below(low, entry_price - second_profit_distance) and not second_trail_active:
            second_trail_active = True
            trail = current_and_previous_high(trail_candles, candle_time, config.trail_timeframe)
            if trail is not None and lower_stop(trail, stop):
                stop = trail
                stop_reason = "TWO_CANDLE_TRAIL_SL"
                two_candle_trail_time = candle_time.isoformat()
                two_candle_trail_sl = stop

        if candle_time.time() >= config.session_end:
            return candle_time.isoformat(), close, "FORCE_EXIT", mfe, mae, first_trail_time, first_trail_sl, two_candle_trail_time, two_candle_trail_sl

    last = candles.iloc[-1]
    return last["time_ist"].isoformat(), float(last["close"]), "DATA_END", mfe, mae, first_trail_time, first_trail_sl, two_candle_trail_time, two_candle_trail_sl


def backtest(
    df: pd.DataFrame,
    config: BacktestConfig,
    trail_df: pd.DataFrame | None = None,
    execution_df: pd.DataFrame | None = None,
) -> list[Trade]:
    trades: list[Trade] = []
    trail_df = df if trail_df is None else trail_df
    execution_df = df if execution_df is None else execution_df

    for trade_date, day_df in df.groupby("trade_date", sort=True):
        range_start = ist_datetime(trade_date, config.range_start)
        range_end = ist_datetime(trade_date, config.range_end)
        session_start = ist_datetime(trade_date, config.session_start)
        session_end = ist_datetime(trade_date, config.session_end)

        range_df = day_df[(day_df["time_ist"] >= range_start) & (day_df["time_ist"] < range_end)]
        session_df = day_df[(day_df["time_ist"] >= session_start) & (day_df["time_ist"] <= session_end)].reset_index(drop=True)
        execution_day_df = execution_df[execution_df["trade_date"] == trade_date]
        execution_session_df = execution_day_df[
            (execution_day_df["time_ist"] >= session_start) & (execution_day_df["time_ist"] <= session_end)
        ].reset_index(drop=True)

        if range_df.empty or session_df.empty or execution_session_df.empty:
            continue

        range_high = float(range_df["high"].max())
        range_low = float(range_df["low"].min())
        buy_trigger = range_high * (1 + config.entry_buffer_pct)
        sell_trigger = range_low * (1 - config.entry_buffer_pct)

        for idx, row in session_df.iterrows():
            if row["time_ist"].time() > config.entry_cutoff:
                break

            side = choose_first_trigger(row, buy_trigger, sell_trigger, config.entry_pattern)
            if side is None:
                continue
            signal_start = row["time_ist"]
            signal_end = min(candle_end_time(session_df, idx, config), session_end + timedelta(minutes=1))
            execution_window = execution_session_df[
                (execution_session_df["time_ist"] >= signal_start) & (execution_session_df["time_ist"] < signal_end)
            ].reset_index(drop=True)
            execution_entry = choose_execution_trigger(execution_window, buy_trigger, sell_trigger, config.entry_pattern)
            if execution_entry is None:
                continue
            side, _, entry_time = execution_entry
            execution_start_matches = execution_session_df["time_ist"] >= entry_time
            if not bool(execution_start_matches.any()):
                continue
            execution_start_idx = int(execution_start_matches.idxmax())

            entry_price = buy_trigger if side == "BUY" else sell_trigger
            stop_distance = config.stop_distance_points(entry_price)
            first_profit_distance = config.first_trail_profit_points(entry_price)
            first_lock_distance = config.first_trail_lock_points(entry_price)
            second_profit_distance = config.second_trail_profit_points(entry_price)
            initial_sl = entry_price - stop_distance if side == "BUY" else entry_price + stop_distance

            if side == "BUY":
                exit_time, exit_price, reason, mfe, mae, first_trail_time, first_trail_sl, two_candle_trail_time, two_candle_trail_sl = manage_buy_trade(execution_session_df, trail_df, execution_start_idx, entry_price, config)
                pnl = exit_price - entry_price
                trigger = buy_trigger
            else:
                exit_time, exit_price, reason, mfe, mae, first_trail_time, first_trail_sl, two_candle_trail_time, two_candle_trail_sl = manage_sell_trade(execution_session_df, trail_df, execution_start_idx, entry_price, config)
                pnl = entry_price - exit_price
                trigger = sell_trigger

            trades.append(
                Trade(
                    trade_date=trade_date.isoformat(),
                    symbol=config.symbol,
                    side=side,
                    range_high=round(range_high, 2),
                    range_low=round(range_low, 2),
                    trigger_price=round(trigger, 2),
                    entry_time=entry_time.isoformat(),
                    entry_price=round(entry_price, 2),
                    initial_sl=round(initial_sl, 2),
                    first_trail_time=first_trail_time,
                    first_trail_sl=round(first_trail_sl, 2) if first_trail_sl is not None else None,
                    two_candle_trail_time=two_candle_trail_time,
                    two_candle_trail_sl=round(two_candle_trail_sl, 2) if two_candle_trail_sl is not None else None,
                    exit_time=exit_time,
                    exit_price=round(exit_price, 2),
                    exit_reason=reason,
                    pnl_points=round(pnl, 2),
                    pnl_pct=round(points_to_percent(pnl, entry_price), 4),
                    mfe_points=round(mfe, 2),
                    mfe_pct=round(points_to_percent(mfe, entry_price), 4),
                    mae_points=round(mae, 2),
                    mae_pct=round(points_to_percent(mae, entry_price), 4),
                    stop_distance_points=round(stop_distance, 2),
                    stop_distance_pct=round(points_to_percent(stop_distance, entry_price), 4),
                    first_trail_profit_points=round(first_profit_distance, 2),
                    first_trail_profit_pct=round(points_to_percent(first_profit_distance, entry_price), 4),
                    first_trail_lock_points=round(first_lock_distance, 2),
                    first_trail_lock_pct=round(points_to_percent(first_lock_distance, entry_price), 4),
                    second_trail_profit_points=round(second_profit_distance, 2),
                    second_trail_profit_pct=round(points_to_percent(second_profit_distance, entry_price), 4),
                )
            )
            break

    return trades


def build_summary(trades: Iterable[Trade], config: BacktestConfig) -> dict:
    rows = [asdict(trade) for trade in trades]
    total = len(rows)
    wins = [row for row in rows if row["pnl_points"] > 0]
    losses = [row for row in rows if row["pnl_points"] < 0]
    gross_profit = sum(row["pnl_points"] for row in wins)
    gross_loss = sum(row["pnl_points"] for row in losses)
    net = gross_profit + gross_loss
    buy_rows = [row for row in rows if row["side"] == "BUY"]
    sell_rows = [row for row in rows if row["side"] == "SELL"]
    best_trade = max(rows, key=lambda row: row["pnl_points"], default=None)
    worst_trade = min(rows, key=lambda row: row["pnl_points"], default=None)
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    exit_counts = {
        "initial_sl_exits": sum(
            row["exit_reason"] in {"INITIAL_SL", "SL"}
            and row["pnl_points"] <= -float(row.get("stop_distance_points", config.stop_points))
            for row in rows
        ),
        "first_trail_sl_exits": sum(row["exit_reason"] == "FIRST_TRAIL_SL" for row in rows),
        "two_candle_trail_sl_exits": sum(row["exit_reason"] == "TWO_CANDLE_TRAIL_SL" for row in rows),
        "force_exits": sum(row["exit_reason"] in {"FORCE_EXIT", "SESSION_CLOSE"} for row in rows),
        "data_end_exits": sum(row["exit_reason"] == "DATA_END" for row in rows),
    }

    equity = []
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in rows:
        running += row["pnl_points"]
        peak = max(peak, running)
        max_drawdown = min(max_drawdown, running - peak)
        equity.append({"date": row["trade_date"], "pnl": round(running, 2)})

    return {
        "config": {
            "symbol": config.symbol,
            "data_source": config.data_source.upper(),
            "from_date": config.from_date.isoformat(),
            "to_date": config.to_date.isoformat(),
            "data_from_date": config.data_from_date.isoformat() if config.data_from_date else None,
            "data_to_date": config.data_to_date.isoformat() if config.data_to_date else None,
            "timeframe": config.timeframe.upper(),
            "trail_timeframe": config.trail_timeframe.upper(),
            "execution_timeframe": "M1",
            "timezone": "Asia/Kolkata",
            "range_start": config.range_start.strftime("%H:%M"),
            "range_end": config.range_end.strftime("%H:%M"),
            "session_start": config.session_start.strftime("%H:%M"),
            "entry_cutoff": config.entry_cutoff.strftime("%H:%M"),
            "session_end": config.session_end.strftime("%H:%M"),
            "range": f"{config.range_start.strftime('%H:%M')}-{config.range_end.strftime('%H:%M')} IST",
            "session": (
                f"Entries {config.session_start.strftime('%H:%M')}-{config.entry_cutoff.strftime('%H:%M')} IST"
                f" | Force exit {config.session_end.strftime('%H:%M')} IST"
            ),
            "entry_pattern": config.entry_pattern.upper(),
            "entry_buffer_pct": config.entry_buffer_pct * 100,
            "stop_points": config.stop_points,
            "stop_points_unit": config.stop_points_unit,
            "first_trail_profit": config.first_trail_profit,
            "first_trail_profit_unit": config.first_trail_profit_unit,
            "first_trail_lock_loss": config.first_trail_lock_loss,
            "first_trail_lock_loss_unit": config.first_trail_lock_loss_unit,
            "second_trail_profit": config.second_trail_profit,
            "second_trail_profit_unit": config.second_trail_profit_unit,
        },
        "stats": {
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round((len(wins) / total) * 100, 2) if total else 0.0,
            "gross_profit_points": round(gross_profit, 2),
            "gross_loss_points": round(gross_loss, 2),
            "net_points": round(net, 2),
            "avg_points": round(net / total, 2) if total else 0.0,
            "profit_factor": round(gross_profit / abs(gross_loss), 2) if gross_loss else None,
            "max_drawdown_points": round(max_drawdown, 2),
            "avg_win_points": round(avg_win, 2),
            "avg_loss_points": round(avg_loss, 2),
            "risk_reward_ratio": round(abs(avg_win / avg_loss), 2) if avg_loss else None,
            "buy_trades": len(buy_rows),
            "sell_trades": len(sell_rows),
            "buy_net_points": round(sum(row["pnl_points"] for row in buy_rows), 2),
            "sell_net_points": round(sum(row["pnl_points"] for row in sell_rows), 2),
            "max_profit_points": round(best_trade["pnl_points"], 2) if best_trade else 0.0,
            "max_loss_points": round(worst_trade["pnl_points"], 2) if worst_trade else 0.0,
            **exit_counts,
        },
        "equity": equity,
        "trades": rows,
    }


def write_outputs(summary: dict) -> None:
    source = str(summary.get("config", {}).get("data_source", "MT5")).upper()
    output_dir = RESULTS_DIR
    source_dir = output_dir / source.lower()
    output_dir.mkdir(exist_ok=True)
    source_dir.mkdir(exist_ok=True)
    serialized = json.dumps(summary, indent=2)
    (source_dir / "backtest_summary.json").write_text(serialized, encoding="utf-8")
    trades = summary["trades"]
    if trades:
        with (source_dir / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(trades[0].keys()))
            writer.writeheader()
            writer.writerows(trades)

    # Keep the existing startup report MT5-only, so an online Delta run never
    # replaces a user's locally verified MT5 dashboard after refresh.
    if source == "MT5":
        (output_dir / "backtest_summary.json").write_text(serialized, encoding="utf-8")
        REPORT_DATA_FILE.write_text(
            "window.BACKTEST_REPORT = " + serialized + ";\n",
            encoding="utf-8",
        )
        if trades:
            with (output_dir / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(trades[0].keys()))
                writer.writeheader()
                writer.writerows(trades)


def main() -> None:
    parser = argparse.ArgumentParser(description="MT5 5-minute crypto breakout backtest.")
    parser.add_argument("--symbol", default="BTCUSD", help="Exact MT5 symbol name, for example BTCUSD or BTCUSDm.")
    parser.add_argument("--from", dest="from_date", required=True, type=parse_date, help="Start date YYYY-MM-DD.")
    parser.add_argument("--to", dest="to_date", required=True, type=parse_date, help="End date YYYY-MM-DD.")
    parser.add_argument("--timeframe", default="M5", choices=sorted(TIMEFRAMES), help="MT5 timeframe.")
    parser.add_argument("--trail-timeframe", default="M15", choices=sorted(TIMEFRAMES), help="Timeframe for the two-candle trail after second profit trigger.")
    parser.add_argument("--range-start", default="08:30", type=parse_time, help="Range start HH:MM IST.")
    parser.add_argument("--range-end", default="09:30", type=parse_time, help="Range end HH:MM IST.")
    parser.add_argument("--session-start", default="09:30", type=parse_time, help="Session start HH:MM IST.")
    parser.add_argument("--entry-cutoff", default="18:00", type=parse_time, help="Last time a new entry may trigger, HH:MM IST.")
    parser.add_argument("--session-end", default="19:30", type=parse_time, help="Forced exit time for running trades, HH:MM IST.")
    parser.add_argument("--buffer-pct", default=0.25, type=float, help="Entry buffer percent. 0.25 means 0.25 percent.")
    parser.add_argument("--stop-points", default=400.0, type=float, help="Initial stop-loss points.")
    args = parser.parse_args()

    config = BacktestConfig(
        symbol=args.symbol,
        from_date=args.from_date,
        to_date=args.to_date,
        timeframe=args.timeframe,
        trail_timeframe=args.trail_timeframe,
        range_start=args.range_start,
        range_end=args.range_end,
        session_start=args.session_start,
        entry_cutoff=args.entry_cutoff,
        session_end=args.session_end,
        entry_buffer_pct=args.buffer_pct / 100,
        stop_points=args.stop_points,
    )
    df = fetch_rates(config)
    if not df.empty:
        config.data_from_date = min(df["trade_date"])
        config.data_to_date = max(df["trade_date"])
    if config.trail_timeframe.upper() == config.timeframe.upper():
        trail_df = df
    else:
        trail_config = BacktestConfig(
            symbol=config.symbol,
            from_date=config.from_date - timedelta(days=1),
            to_date=config.to_date,
            timeframe=config.trail_timeframe,
        )
        trail_df = fetch_rates(trail_config)
    if config.timeframe.upper() == "M1":
        execution_df = df
    else:
        execution_config = BacktestConfig(
            symbol=config.symbol,
            from_date=config.from_date,
            to_date=config.to_date,
            timeframe="M1",
        )
        execution_df = fetch_rates(execution_config)
    trades = backtest(df, config, trail_df, execution_df)
    summary = build_summary(trades, config)
    write_outputs(summary)

    stats = summary["stats"]
    print(f"Backtest complete for {config.symbol}")
    print(f"Trades: {stats['total_trades']} | Win rate: {stats['win_rate_pct']}% | Net: {stats['net_points']} points")
    print("Run python server.py and open http://127.0.0.1:5000 to view the dashboard.")


if __name__ == "__main__":
    main()
