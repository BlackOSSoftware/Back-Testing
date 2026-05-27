from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Iterable
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5
import pandas as pd


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
_rates_cache: dict[tuple[str, str, date, date], tuple[float, pd.DataFrame]] = {}


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
    exit_time: str
    exit_price: float
    exit_reason: str
    pnl_points: float
    mfe_points: float
    mae_points: float


@dataclass
class BacktestConfig:
    symbol: str
    from_date: date
    to_date: date
    data_source: str = "MT5"
    timeframe: str = "M5"
    trail_timeframe: str = "M5"
    range_start: time = time(8, 30)
    range_end: time = time(9, 30)
    session_start: time = time(9, 30)
    entry_cutoff: time = time(18, 0)
    session_end: time = time(19, 30)
    entry_pattern: str = "BOTH"
    entry_buffer_pct: float = 0.0005
    stop_points: float = 400.0
    first_trail_profit: float = 400.0
    first_trail_lock_loss: float = 300.0
    second_trail_profit: float = 700.0
    data_from_date: date | None = None
    data_to_date: date | None = None


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
        end_ist = ist_datetime(config.to_date + timedelta(days=1), time(0, 0))
        rates = None
        probe_date = config.from_date
        while probe_date <= config.to_date:
            start_ist = ist_datetime(probe_date, time(0, 0))
            rates = mt5.copy_rates_range(
                config.symbol,
                TIMEFRAMES[timeframe],
                start_ist.astimezone(UTC),
                end_ist.astimezone(UTC),
            )
            if rates is not None and len(rates) > 0:
                break
            probe_date += timedelta(days=30)
    finally:
        mt5.shutdown()

    if rates is None or len(rates) == 0:
        raise RuntimeError("No MT5 candle data returned. Check symbol name, date range, and broker history.")

    df = pd.DataFrame(rates)
    df["time_utc"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df["time_ist"] = df["time_utc"].dt.tz_convert(IST)
    df["time_ist"] = df["time_ist"] - pd.Timedelta(hours=time_shift_hours)
    df["trade_date"] = df["time_ist"].dt.date
    df = df[(df["trade_date"] >= config.from_date) & (df["trade_date"] <= config.to_date)]
    df = df[["time_ist", "trade_date", "open", "high", "low", "close", "tick_volume"]].copy()
    _rates_cache[cache_key] = (monotonic(), df)
    return df.copy()


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


def previous_two_completed_position(candles: pd.DataFrame, candle_time: datetime, timeframe: str) -> int:
    duration = pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe.upper()])
    cache_key = f"completed_times_{timeframe.upper()}"
    completed_times = candles.attrs.get(cache_key)
    if completed_times is None:
        completed_times = (candles["time_ist"] + duration).reset_index(drop=True)
        candles.attrs[cache_key] = completed_times
    return int(completed_times.searchsorted(pd.Timestamp(candle_time), side="right"))


def previous_two_completed_low(candles: pd.DataFrame, candle_time: datetime, timeframe: str) -> float | None:
    position = previous_two_completed_position(candles, candle_time, timeframe)
    if position < 2:
        return None
    return float(candles.iloc[position - 2 : position]["low"].min())


def previous_two_completed_high(candles: pd.DataFrame, candle_time: datetime, timeframe: str) -> float | None:
    position = previous_two_completed_position(candles, candle_time, timeframe)
    if position < 2:
        return None
    return float(candles.iloc[position - 2 : position]["high"].max())


def choose_first_trigger(row: pd.Series, buy_trigger: float, sell_trigger: float, entry_pattern: str = "BOTH") -> str | None:
    buy_hit = float(row["high"]) >= buy_trigger
    sell_hit = float(row["low"]) <= sell_trigger
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


def manage_buy_trade(
    candles: pd.DataFrame,
    trail_candles: pd.DataFrame,
    start_idx: int,
    entry_price: float,
    config: BacktestConfig,
) -> tuple[str, float, str, float, float]:
    stop = entry_price - config.stop_points
    stop_reason = "INITIAL_SL"
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

        if low <= stop:
            return candle_time.isoformat(), stop, stop_reason, mfe, mae

        if high >= entry_price + config.first_trail_profit:
            first_trail_stop = entry_price - config.first_trail_lock_loss
            if first_trail_stop > stop:
                stop = first_trail_stop
                stop_reason = "FIRST_TRAIL_SL"

        if high >= entry_price + config.second_trail_profit:
            trail = previous_two_completed_low(trail_candles, candle_time, config.trail_timeframe)
            if trail is not None and trail > stop:
                stop = trail
                stop_reason = "TWO_CANDLE_TRAIL_SL"

        if candle_time.time() >= config.session_end:
            return candle_time.isoformat(), close, "FORCE_EXIT", mfe, mae

    last = candles.iloc[-1]
    return last["time_ist"].isoformat(), float(last["close"]), "DATA_END", mfe, mae


def manage_sell_trade(
    candles: pd.DataFrame,
    trail_candles: pd.DataFrame,
    start_idx: int,
    entry_price: float,
    config: BacktestConfig,
) -> tuple[str, float, str, float, float]:
    stop = entry_price + config.stop_points
    stop_reason = "INITIAL_SL"
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

        if high >= stop:
            return candle_time.isoformat(), stop, stop_reason, mfe, mae

        if low <= entry_price - config.first_trail_profit:
            first_trail_stop = entry_price + config.first_trail_lock_loss
            if first_trail_stop < stop:
                stop = first_trail_stop
                stop_reason = "FIRST_TRAIL_SL"

        if low <= entry_price - config.second_trail_profit:
            trail = previous_two_completed_high(trail_candles, candle_time, config.trail_timeframe)
            if trail is not None and trail < stop:
                stop = trail
                stop_reason = "TWO_CANDLE_TRAIL_SL"

        if candle_time.time() >= config.session_end:
            return candle_time.isoformat(), close, "FORCE_EXIT", mfe, mae

    last = candles.iloc[-1]
    return last["time_ist"].isoformat(), float(last["close"]), "DATA_END", mfe, mae


def backtest(df: pd.DataFrame, config: BacktestConfig, trail_df: pd.DataFrame | None = None) -> list[Trade]:
    trades: list[Trade] = []
    trail_df = df if trail_df is None else trail_df

    for trade_date, day_df in df.groupby("trade_date", sort=True):
        range_start = ist_datetime(trade_date, config.range_start)
        range_end = ist_datetime(trade_date, config.range_end)
        session_start = ist_datetime(trade_date, config.session_start)
        session_end = ist_datetime(trade_date, config.session_end)

        range_df = day_df[(day_df["time_ist"] >= range_start) & (day_df["time_ist"] < range_end)]
        session_df = day_df[(day_df["time_ist"] >= session_start) & (day_df["time_ist"] <= session_end)].reset_index(drop=True)

        if range_df.empty or session_df.empty:
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

            entry_price = buy_trigger if side == "BUY" else sell_trigger
            initial_sl = entry_price - config.stop_points if side == "BUY" else entry_price + config.stop_points

            if side == "BUY":
                exit_time, exit_price, reason, mfe, mae = manage_buy_trade(session_df, trail_df, idx, entry_price, config)
                pnl = exit_price - entry_price
                trigger = buy_trigger
            else:
                exit_time, exit_price, reason, mfe, mae = manage_sell_trade(session_df, trail_df, idx, entry_price, config)
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
                    entry_time=row["time_ist"].isoformat(),
                    entry_price=round(entry_price, 2),
                    initial_sl=round(initial_sl, 2),
                    exit_time=exit_time,
                    exit_price=round(exit_price, 2),
                    exit_reason=reason,
                    pnl_points=round(pnl, 2),
                    mfe_points=round(mfe, 2),
                    mae_points=round(mae, 2),
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
        "initial_sl_exits": sum(row["exit_reason"] in {"INITIAL_SL", "SL"} and row["pnl_points"] <= -config.stop_points for row in rows),
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
            "first_trail_profit": config.first_trail_profit,
            "first_trail_lock_loss": config.first_trail_lock_loss,
            "second_trail_profit": config.second_trail_profit,
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
    output_dir = Path("results")
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
        Path("report_data.js").write_text(
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
    parser.add_argument("--trail-timeframe", default="M5", choices=sorted(TIMEFRAMES), help="Timeframe for the two-candle trail after second profit trigger.")
    parser.add_argument("--range-start", default="08:30", type=parse_time, help="Range start HH:MM IST.")
    parser.add_argument("--range-end", default="09:30", type=parse_time, help="Range end HH:MM IST.")
    parser.add_argument("--session-start", default="09:30", type=parse_time, help="Session start HH:MM IST.")
    parser.add_argument("--entry-cutoff", default="18:00", type=parse_time, help="Last time a new entry may trigger, HH:MM IST.")
    parser.add_argument("--session-end", default="19:30", type=parse_time, help="Forced exit time for running trades, HH:MM IST.")
    parser.add_argument("--buffer-pct", default=0.05, type=float, help="Entry buffer percent. 0.05 means 0.05 percent.")
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
    trades = backtest(df, config, trail_df)
    summary = build_summary(trades, config)
    write_outputs(summary)

    stats = summary["stats"]
    print(f"Backtest complete for {config.symbol}")
    print(f"Trades: {stats['total_trades']} | Win rate: {stats['win_rate_pct']}% | Net: {stats['net_points']} points")
    print("Open index.html to view the dashboard.")


if __name__ == "__main__":
    main()
