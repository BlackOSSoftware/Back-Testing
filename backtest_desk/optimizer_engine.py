from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import numpy as np
import pandas as pd

from .backtest_mt5 import BacktestConfig, TIMEFRAME_MINUTES

try:
    from numba import njit, prange
except ImportError:
    def njit(*args, **kwargs):
        def decorator(function):
            return function
        return decorator

    prange = range


@dataclass
class EntryLayout:
    day_starts: np.ndarray
    day_ends: np.ndarray
    cutoff_ends: np.ndarray
    range_highs: np.ndarray
    range_lows: np.ndarray
    times_ns: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    at_session_end: np.ndarray


@dataclass
class ScanContext:
    layout: EntryLayout
    trail_lows: np.ndarray
    trail_highs: np.ndarray


def minute_of_day(value: time) -> int:
    return value.hour * 60 + value.minute


def layout_source(frame: pd.DataFrame):
    cached = frame.attrs.get("_optimizer_layout_source")
    if cached is not None:
        return cached
    sorted_frame = frame.sort_values("time_ist")
    times = sorted_frame["time_ist"].array.asi8.astype(np.int64)
    dates = sorted_frame["trade_date"].to_numpy()
    minutes = (sorted_frame["time_ist"].dt.hour.to_numpy(dtype=np.int64) * 60) + sorted_frame["time_ist"].dt.minute.to_numpy(dtype=np.int64)
    source = {
        "times": times,
        "dates": dates,
        "minutes": minutes,
        "opens": sorted_frame["open"].to_numpy(dtype=np.float64),
        "highs": sorted_frame["high"].to_numpy(dtype=np.float64),
        "lows": sorted_frame["low"].to_numpy(dtype=np.float64),
        "closes": sorted_frame["close"].to_numpy(dtype=np.float64),
        "boundaries": np.r_[0, np.flatnonzero(dates[1:] != dates[:-1]) + 1, len(dates)] if len(dates) else np.asarray([0], dtype=np.int64),
    }
    frame.attrs["_optimizer_layout_source"] = source
    return source


def window_timestamp(day, value: time, tz) -> pd.Timestamp:
    return pd.Timestamp.combine(day, value).tz_localize(tz)


def build_overnight_entry_layout(df: pd.DataFrame, config: BacktestConfig) -> EntryLayout:
    empty_i = np.array([], dtype=np.int64)
    empty_f = np.array([], dtype=np.float64)
    empty_b = np.array([], dtype=np.bool_)
    if df.empty:
        return EntryLayout(empty_i, empty_i, empty_i, empty_f, empty_f, empty_i, empty_f, empty_f, empty_f, empty_f, empty_b)

    frame = df.sort_values("time_ist")
    source = layout_source(df)
    tz = frame["time_ist"].dt.tz
    dates = source["dates"]
    times_ns_all = source["times"]
    opens_all = source["opens"]
    highs_all = source["highs"]
    lows_all = source["lows"]
    closes_all = source["closes"]
    starts = []
    ends = []
    cutoff_ends = []
    range_highs = []
    range_lows = []
    index_blocks = []
    end_flags = []
    offset = 0

    for trade_date in sorted(set(dates)):
        if trade_date < config.from_date or trade_date > config.to_date:
            continue
        range_start = window_timestamp(trade_date, config.range_start, tz)
        range_end = window_timestamp(trade_date, config.range_end, tz)
        while range_end <= range_start:
            range_end += pd.Timedelta(days=1)
        session_start = window_timestamp(trade_date, config.session_start, tz)
        while session_start < range_end:
            session_start += pd.Timedelta(days=1)
        entry_cutoff = window_timestamp(trade_date, config.entry_cutoff, tz)
        while entry_cutoff < session_start:
            entry_cutoff += pd.Timedelta(days=1)
        session_end = window_timestamp(trade_date, config.session_end, tz)
        while session_end <= session_start:
            session_end += pd.Timedelta(days=1)

        range_left = int(np.searchsorted(times_ns_all, range_start.value, side="left"))
        range_right = int(np.searchsorted(times_ns_all, range_end.value, side="left"))
        session_left = int(np.searchsorted(times_ns_all, session_start.value, side="left"))
        session_right = int(np.searchsorted(times_ns_all, session_end.value, side="right"))
        if range_left >= range_right or session_left >= session_right:
            continue
        range_indices = np.arange(range_left, range_right, dtype=np.int64)
        session_indices = np.arange(session_left, session_right, dtype=np.int64)
        size = len(session_indices)
        starts.append(offset)
        ends.append(offset + size)
        cutoff_right = int(np.searchsorted(times_ns_all, entry_cutoff.value, side="right"))
        cutoff_ends.append(offset + max(0, min(size, cutoff_right - session_left)))
        range_highs.append(float(np.max(highs_all[range_indices])))
        range_lows.append(float(np.min(lows_all[range_indices])))
        index_blocks.append(session_indices)
        flags = np.zeros(size, dtype=np.bool_)
        flags[-1] = True
        end_flags.append(flags)
        offset += size

    if not index_blocks:
        return EntryLayout(empty_i, empty_i, empty_i, empty_f, empty_f, empty_i, empty_f, empty_f, empty_f, empty_f, empty_b)

    session_indices = np.concatenate(index_blocks)
    return EntryLayout(
        day_starts=np.asarray(starts, dtype=np.int64),
        day_ends=np.asarray(ends, dtype=np.int64),
        cutoff_ends=np.asarray(cutoff_ends, dtype=np.int64),
        range_highs=np.asarray(range_highs, dtype=np.float64),
        range_lows=np.asarray(range_lows, dtype=np.float64),
        times_ns=times_ns_all[session_indices],
        opens=opens_all[session_indices],
        highs=highs_all[session_indices],
        lows=lows_all[session_indices],
        closes=closes_all[session_indices],
        at_session_end=np.concatenate(end_flags),
    )


def build_entry_layout(df: pd.DataFrame, config: BacktestConfig) -> EntryLayout:
    empty_i = np.array([], dtype=np.int64)
    empty_f = np.array([], dtype=np.float64)
    empty_b = np.array([], dtype=np.bool_)
    if df.empty:
        return EntryLayout(empty_i, empty_i, empty_i, empty_f, empty_f, empty_i, empty_f, empty_f, empty_f, empty_f, empty_b)

    if config.range_end <= config.range_start or config.session_end <= config.session_start or config.entry_cutoff < config.session_start:
        return build_overnight_entry_layout(df, config)

    source = layout_source(df)
    times = source["times"]
    dates = source["dates"]
    minutes = source["minutes"]
    opens_all = source["opens"]
    highs_all = source["highs"]
    lows_all = source["lows"]
    closes_all = source["closes"]
    range_start = minute_of_day(config.range_start)
    range_end = minute_of_day(config.range_end)
    session_start = minute_of_day(config.session_start)
    session_end = minute_of_day(config.session_end)
    cutoff = minute_of_day(config.entry_cutoff)

    starts = []
    ends = []
    cutoff_ends = []
    range_highs = []
    range_lows = []
    index_blocks = []
    offset = 0

    if len(dates) == 0:
        return EntryLayout(empty_i, empty_i, empty_i, empty_f, empty_f, empty_i, empty_f, empty_f, empty_f, empty_f, empty_b)
    boundaries = source["boundaries"]
    for start_pos, end_pos in zip(boundaries[:-1], boundaries[1:]):
        day_minutes = minutes[start_pos:end_pos]
        range_local = np.flatnonzero((day_minutes >= range_start) & (day_minutes < range_end))
        session_local = np.flatnonzero((day_minutes >= session_start) & (day_minutes <= session_end))
        if len(range_local) == 0 or len(session_local) == 0:
            continue
        range_indices = range_local + start_pos
        session_indices = session_local + start_pos
        size = len(session_indices)
        starts.append(offset)
        ends.append(offset + size)
        cutoff_ends.append(offset + int(np.count_nonzero(minutes[session_indices] <= cutoff)))
        range_highs.append(float(np.max(highs_all[range_indices])))
        range_lows.append(float(np.min(lows_all[range_indices])))
        index_blocks.append(session_indices)
        offset += size
    if not index_blocks:
        return EntryLayout(empty_i, empty_i, empty_i, empty_f, empty_f, empty_i, empty_f, empty_f, empty_f, empty_f, empty_b)

    session_indices = np.concatenate(index_blocks)
    return EntryLayout(
        day_starts=np.asarray(starts, dtype=np.int64),
        day_ends=np.asarray(ends, dtype=np.int64),
        cutoff_ends=np.asarray(cutoff_ends, dtype=np.int64),
        range_highs=np.asarray(range_highs, dtype=np.float64),
        range_lows=np.asarray(range_lows, dtype=np.float64),
        times_ns=times[session_indices],
        opens=opens_all[session_indices],
        highs=highs_all[session_indices],
        lows=lows_all[session_indices],
        closes=closes_all[session_indices],
        at_session_end=(minutes[session_indices] >= session_end),
    )


def build_scan_context(layout: EntryLayout, trail_df: pd.DataFrame, trail_timeframe: str) -> ScanContext:
    count = len(layout.times_ns)
    trailing_lows = np.full(count, np.nan, dtype=np.float64)
    trailing_highs = np.full(count, np.nan, dtype=np.float64)
    if count and not trail_df.empty:
        duration_ns = TIMEFRAME_MINUTES[trail_timeframe.upper()] * 60 * 1_000_000_000
        cache_key = f"_optimizer_trail_source_{trail_timeframe.upper()}"
        cached = trail_df.attrs.get(cache_key)
        if cached is None:
            trail = trail_df.sort_values("time_ist")
            cached = {
                "completed_times": trail["time_ist"].array.asi8.astype(np.int64) + duration_ns,
                "lows": trail["low"].to_numpy(dtype=np.float64),
                "highs": trail["high"].to_numpy(dtype=np.float64),
            }
            trail_df.attrs[cache_key] = cached
        completed_times = cached["completed_times"]
        positions = np.searchsorted(completed_times, layout.times_ns, side="right")
        eligible = positions >= 2
        indices = positions[eligible]
        lows = cached["lows"]
        highs = cached["highs"]
        trailing_lows[eligible] = np.minimum(lows[indices - 2], lows[indices - 1])
        trailing_highs[eligible] = np.maximum(highs[indices - 2], highs[indices - 1])
    return ScanContext(layout=layout, trail_lows=trailing_lows, trail_highs=trailing_highs)


@njit(cache=True)
def _evaluate_kernel(
    day_starts,
    day_ends,
    cutoff_ends,
    range_highs,
    range_lows,
    opens,
    highs,
    lows,
    closes,
    at_session_end,
    trail_lows,
    trail_highs,
    entry_buffer_pct,
    stop_points,
    first_trail_profit,
    first_trail_lock_loss,
    second_trail_profit,
    side_filter,
):
    total = 0
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for day in range(len(day_starts)):
        start = day_starts[day]
        end = day_ends[day]
        buy_trigger = range_highs[day] * (1.0 + entry_buffer_pct)
        sell_trigger = range_lows[day] * (1.0 - entry_buffer_pct)
        side = 0
        entry_idx = -1
        for idx in range(start, cutoff_ends[day]):
            buy_hit = highs[idx] >= buy_trigger
            sell_hit = lows[idx] <= sell_trigger
            if side_filter == 1:
                if buy_hit:
                    side = 1
            elif side_filter == -1:
                if sell_hit:
                    side = -1
            elif buy_hit and not sell_hit:
                side = 1
            elif sell_hit and not buy_hit:
                side = -1
            elif buy_hit and sell_hit:
                side = 1 if abs(buy_trigger - opens[idx]) <= abs(opens[idx] - sell_trigger) else -1
            if side != 0:
                entry_idx = idx
                break
        if side == 0:
            continue
        entry_price = buy_trigger if side == 1 else sell_trigger
        stop = entry_price - stop_points if side == 1 else entry_price + stop_points
        exit_price = closes[end - 1]
        for idx in range(entry_idx, end):
            if side == 1:
                if lows[idx] <= stop:
                    exit_price = stop
                    break
                if highs[idx] >= entry_price + first_trail_profit:
                    candidate = entry_price + first_trail_lock_loss
                    if candidate > stop:
                        stop = candidate
                if highs[idx] >= entry_price + second_trail_profit:
                    candidate = trail_lows[idx]
                    if not np.isnan(candidate) and candidate > stop:
                        stop = candidate
            else:
                if highs[idx] >= stop:
                    exit_price = stop
                    break
                if lows[idx] <= entry_price - first_trail_profit:
                    candidate = entry_price - first_trail_lock_loss
                    if candidate < stop:
                        stop = candidate
                if lows[idx] <= entry_price - second_trail_profit:
                    candidate = trail_highs[idx]
                    if not np.isnan(candidate) and candidate < stop:
                        stop = candidate
            if at_session_end[idx]:
                exit_price = closes[idx]
                break
        pnl = round(exit_price - entry_price, 2) if side == 1 else round(entry_price - exit_price, 2)
        total += 1
        running += pnl
        if running > peak:
            peak = running
        drawdown = running - peak
        if drawdown < max_drawdown:
            max_drawdown = drawdown
        if pnl > 0:
            wins += 1
            gross_profit += pnl
        elif pnl < 0:
            losses += 1
            gross_loss += pnl
    return total, wins, losses, gross_profit, gross_loss, running, max_drawdown


def evaluate_scan(context: ScanContext, entry_buffer_pct: float, stop_points: float, first_trail_profit: float, first_trail_lock_loss: float, second_trail_profit: float, side_filter: int = 0) -> dict:
    layout = context.layout
    total, wins, losses, gross_profit, gross_loss, net, max_drawdown = _evaluate_kernel(
        layout.day_starts,
        layout.day_ends,
        layout.cutoff_ends,
        layout.range_highs,
        layout.range_lows,
        layout.opens,
        layout.highs,
        layout.lows,
        layout.closes,
        layout.at_session_end,
        context.trail_lows,
        context.trail_highs,
        entry_buffer_pct,
        stop_points,
        first_trail_profit,
        first_trail_lock_loss,
        second_trail_profit,
        side_filter,
    )
    return {
        "total_trades": int(total),
        "wins": int(wins),
        "losses": int(losses),
        "win_rate_pct": round((wins / total) * 100, 2) if total else 0.0,
        "net_points": round(float(net), 2),
        "profit_factor": round(float(gross_profit / abs(gross_loss)), 2) if gross_loss else None,
        "max_drawdown_points": round(float(max_drawdown), 2),
    }


@njit(cache=True, parallel=True)
def _evaluate_batch_kernel(
    day_starts,
    day_ends,
    cutoff_ends,
    range_highs,
    range_lows,
    opens,
    highs,
    lows,
    closes,
    at_session_end,
    trail_lows,
    trail_highs,
    parameters,
    side_filter,
):
    metrics = np.empty((len(parameters), 7), dtype=np.float64)
    for index in prange(len(parameters)):
        metric = _evaluate_kernel(
            day_starts,
            day_ends,
            cutoff_ends,
            range_highs,
            range_lows,
            opens,
            highs,
            lows,
            closes,
            at_session_end,
            trail_lows,
            trail_highs,
            parameters[index, 0],
            parameters[index, 1],
            parameters[index, 2],
            parameters[index, 3],
            parameters[index, 4],
            side_filter,
        )
        metrics[index, 0] = metric[0]
        metrics[index, 1] = metric[1]
        metrics[index, 2] = metric[2]
        metrics[index, 3] = metric[3]
        metrics[index, 4] = metric[4]
        metrics[index, 5] = metric[5]
        metrics[index, 6] = metric[6]
    return metrics


def evaluate_scan_batch(context: ScanContext, parameters: list[tuple[float, float, float, float, float]], side_filter: int = 0) -> list[dict]:
    if not parameters:
        return []
    layout = context.layout
    values = np.asarray(parameters, dtype=np.float64)
    if len(parameters) < 32:
        return [evaluate_scan(context, *row, side_filter=side_filter) for row in parameters]
    kernel = _evaluate_batch_kernel
    metrics = kernel(
        layout.day_starts,
        layout.day_ends,
        layout.cutoff_ends,
        layout.range_highs,
        layout.range_lows,
        layout.opens,
        layout.highs,
        layout.lows,
        layout.closes,
        layout.at_session_end,
        context.trail_lows,
        context.trail_highs,
        values,
        side_filter,
    )
    rows = []
    for total, wins, losses, gross_profit, gross_loss, net, max_drawdown in metrics:
        rows.append(
            {
                "total_trades": int(total),
                "wins": int(wins),
                "losses": int(losses),
                "win_rate_pct": round((wins / total) * 100, 2) if total else 0.0,
                "net_points": round(float(net), 2),
                "profit_factor": round(float(gross_profit / abs(gross_loss)), 2) if gross_loss else None,
                "max_drawdown_points": round(float(max_drawdown), 2),
            }
        )
    return rows


def evaluate_scan_batch_metrics(context: ScanContext, parameters: np.ndarray, side_filter: int = 0) -> np.ndarray:
    layout = context.layout
    if parameters.size == 0:
        return np.empty((0, 7), dtype=np.float64)
    return _evaluate_batch_kernel(
        layout.day_starts,
        layout.day_ends,
        layout.cutoff_ends,
        layout.range_highs,
        layout.range_lows,
        layout.opens,
        layout.highs,
        layout.lows,
        layout.closes,
        layout.at_session_end,
        context.trail_lows,
        context.trail_highs,
        parameters,
        side_filter,
    )


def warm_optimizer_engine() -> None:
    params = np.asarray([(0.0005, 400.0, 700.0, 300.0, 700.0)], dtype=np.float64)
    _evaluate_batch_kernel(
        np.asarray([0], dtype=np.int64),
        np.asarray([2], dtype=np.int64),
        np.asarray([2], dtype=np.int64),
        np.asarray([101.0], dtype=np.float64),
        np.asarray([99.0], dtype=np.float64),
        np.asarray([100.0, 101.0], dtype=np.float64),
        np.asarray([102.0, 103.0], dtype=np.float64),
        np.asarray([98.0, 99.0], dtype=np.float64),
        np.asarray([101.0, 102.0], dtype=np.float64),
        np.asarray([False, True], dtype=np.bool_),
        np.asarray([99.0, 100.0], dtype=np.float64),
        np.asarray([102.0, 103.0], dtype=np.float64),
        params,
        0,
    )
