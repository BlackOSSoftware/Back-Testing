from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

import pandas as pd

from backtest_mt5 import BacktestConfig, IST, TIMEFRAMES, UTC, fetch_rates, ist_datetime


DELTA_BASE_URL = "https://api.india.delta.exchange/v2"
DELTA_TIMEFRAMES = {
    "M1": "1m",
    "M3": "3m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "4h",
}
DELTA_MINUTES = {
    "M1": 1,
    "M3": 3,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
}
SOURCES = [
    {"value": "MT5", "label": "MetaTrader 5 (Local)"},
    {"value": "DELTA", "label": "Delta Exchange India (Online Cache)"},
]
SOURCE_TIMEFRAMES = {
    "MT5": list(TIMEFRAMES),
    "DELTA": list(DELTA_TIMEFRAMES),
}
DELTA_CACHE_DIR = Path("cache") / "delta"
DELTA_MAX_CANDLES_PER_REQUEST = 1800
_delta_launch_cache: dict[str, date | None] = {}
_delta_frame_cache: dict[str, pd.DataFrame] = {}


def normalize_source(value: str | None) -> str:
    source = str(value or "MT5").strip().upper()
    if source not in SOURCE_TIMEFRAMES:
        raise ValueError(f"Unsupported data source: {source}")
    return source


def validate_source_timeframe(source: str, timeframe: str) -> None:
    if timeframe.upper() not in SOURCE_TIMEFRAMES[normalize_source(source)]:
        raise ValueError(f"Unsupported {source} timeframe: {timeframe}")


def fetch_source_rates(config: BacktestConfig) -> pd.DataFrame:
    source = normalize_source(config.data_source)
    validate_source_timeframe(source, config.timeframe)
    if source == "MT5":
        return fetch_rates(config)
    return fetch_delta_rates(config)


def _delta_json(path: str, params: dict | None = None) -> dict:
    suffix = f"?{urlencode(params)}" if params else ""
    request = Request(
        f"{DELTA_BASE_URL}{path}{suffix}",
        headers={"Accept": "application/json", "User-Agent": "MT5-Lab-Backtest/1.0"},
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def delta_symbols() -> list[str]:
    products: list[dict] = []
    after: str | None = None
    while True:
        params = {"page_size": 100, "contract_types": "perpetual_futures,futures"}
        if after:
            params["after"] = after
        payload = _delta_json("/products", params)
        products.extend(payload.get("result") or [])
        after = (payload.get("meta") or {}).get("after")
        if not after:
            break
    names = {
        str(product.get("symbol", "")).strip()
        for product in products
        if product.get("symbol") and product.get("contract_type") in {"perpetual_futures", "futures"}
    }
    priority = ("BTC", "ETH", "XRP", "SOL", "DOGE", "BNB", "ADA")
    return sorted(names, key=lambda name: (not name.startswith(priority), name))


def delta_launch_date(symbol: str) -> date | None:
    key = symbol.upper()
    if key in _delta_launch_cache:
        return _delta_launch_cache[key]
    try:
        payload = _delta_json(f"/products/{quote(symbol, safe='')}")
        launch_time = (payload.get("result") or {}).get("launch_time")
        if not launch_time:
            _delta_launch_cache[key] = None
            return None
        launch_date = datetime.fromisoformat(launch_time.replace("Z", "+00:00")).astimezone(IST).date()
        _delta_launch_cache[key] = launch_date
        return launch_date
    except Exception:
        return None


def delta_history_status(symbol: str, timeframe: str, from_date: date, to_date: date) -> dict:
    validate_source_timeframe("DELTA", timeframe)
    launch_date = delta_launch_date(symbol)
    available = launch_date is None or to_date >= launch_date
    effective_from = max(from_date, launch_date) if launch_date else from_date
    paths = _cache_paths(symbol, timeframe)
    metadata = _load_metadata(paths["meta"])
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "available": available,
        "from_date": effective_from.isoformat(),
        "to_date": to_date.isoformat(),
        "launch_date": launch_date.isoformat() if launch_date else None,
        "cached_ranges": metadata.get("covered_ranges", []),
        "online_chunked": True,
    }


def fetch_delta_rates(config: BacktestConfig) -> pd.DataFrame:
    timeframe = config.timeframe.upper()
    validate_source_timeframe("DELTA", timeframe)
    requested_start = int(ist_datetime(config.from_date, time(0, 0)).astimezone(UTC).timestamp())
    requested_end = int(ist_datetime(config.to_date + timedelta(days=1), time(0, 0)).astimezone(UTC).timestamp()) - 1
    launch_date = delta_launch_date(config.symbol)
    if launch_date and config.to_date < launch_date:
        raise RuntimeError(f"Delta contract {config.symbol} launched on {launch_date.isoformat()}.")
    if launch_date:
        requested_start = max(
            requested_start,
            int(ist_datetime(launch_date, time(0, 0)).astimezone(UTC).timestamp()),
        )

    paths = _cache_paths(config.symbol, timeframe)
    cached_df = _load_cache(paths["csv"])
    metadata = _load_metadata(paths["meta"])
    missing = _missing_ranges(requested_start, requested_end, metadata.get("covered_ranges", []))
    fetched_frames: list[pd.DataFrame] = []

    for range_start, range_end in missing:
        fetched_frames.extend(_download_delta_chunks(config.symbol, timeframe, range_start, range_end))

    if fetched_frames:
        frames = fetched_frames if cached_df.empty else [cached_df, *fetched_frames]
        cached_df = pd.concat(frames, ignore_index=True)
        cached_df = cached_df.drop_duplicates(subset=["time"], keep="last").sort_values("time")

    if missing:
        metadata["covered_ranges"] = _merge_ranges(
            [*metadata.get("covered_ranges", []), *[[start, end] for start, end in missing]]
        )
        if launch_date:
            metadata["launch_date"] = launch_date.isoformat()
        _save_cache(paths, cached_df, metadata)

    result = _to_strategy_frame(cached_df, config.from_date, config.to_date)
    if result.empty:
        raise RuntimeError(
            f"No Delta candle data returned for {config.symbol} {timeframe} in selected date range."
        )
    return result


def _download_delta_chunks(symbol: str, timeframe: str, range_start: int, range_end: int) -> list[pd.DataFrame]:
    interval_seconds = DELTA_MINUTES[timeframe] * 60
    chunk_seconds = interval_seconds * (DELTA_MAX_CANDLES_PER_REQUEST - 1)
    frames: list[pd.DataFrame] = []
    cursor = range_start
    while cursor <= range_end:
        chunk_end = min(range_end, cursor + chunk_seconds)
        payload = _delta_json(
            "/history/candles",
            {
                "resolution": DELTA_TIMEFRAMES[timeframe],
                "symbol": symbol,
                "start": cursor,
                "end": chunk_end,
            },
        )
        rows = payload.get("result") or []
        if rows:
            frame = pd.DataFrame(rows)
            required = ["time", "open", "high", "low", "close", "volume"]
            frame = frame[[column for column in required if column in frame.columns]].copy()
            if "volume" not in frame:
                frame["volume"] = 0
            frames.append(frame[required])
            last_candle_time = int(pd.to_numeric(frame["time"], errors="raise").max())
            next_candle_time = last_candle_time + interval_seconds
            if next_candle_time <= chunk_end:
                cursor = max(cursor + interval_seconds, next_candle_time)
                continue
        cursor = chunk_end + interval_seconds
    return frames


def _cache_paths(symbol: str, timeframe: str) -> dict[str, Path]:
    safe_symbol = re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol.upper())
    prefix = DELTA_CACHE_DIR / f"{safe_symbol}_{timeframe.upper()}"
    return {"csv": prefix.with_suffix(".csv"), "meta": prefix.with_suffix(".json")}


def _load_cache(path: Path) -> pd.DataFrame:
    cache_key = str(path.resolve())
    if cache_key in _delta_frame_cache:
        return _delta_frame_cache[cache_key].copy()
    if not path.exists():
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    dataframe = pd.read_csv(path)
    _delta_frame_cache[cache_key] = dataframe
    return dataframe.copy()


def _load_metadata(path: Path) -> dict:
    if not path.exists():
        return {"covered_ranges": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["covered_ranges"] = _merge_ranges(data.get("covered_ranges", []))
        return data
    except (json.JSONDecodeError, OSError, TypeError):
        return {"covered_ranges": []}


def _save_cache(paths: dict[str, Path], dataframe: pd.DataFrame, metadata: dict) -> None:
    DELTA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dataframe.to_csv(paths["csv"], index=False)
    _delta_frame_cache[str(paths["csv"].resolve())] = dataframe.copy()
    paths["meta"].write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def _merge_ranges(ranges: list[list[int]]) -> list[list[int]]:
    normalized = sorted([int(start), int(end)] for start, end in ranges if int(start) <= int(end))
    merged: list[list[int]] = []
    for start, end in normalized:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged


def _missing_ranges(start: int, end: int, covered_ranges: list[list[int]]) -> list[tuple[int, int]]:
    missing: list[tuple[int, int]] = []
    cursor = start
    for covered_start, covered_end in _merge_ranges(covered_ranges):
        if covered_end < cursor:
            continue
        if covered_start > end:
            break
        if cursor < covered_start:
            missing.append((cursor, min(end, covered_start - 1)))
        cursor = max(cursor, covered_end + 1)
        if cursor > end:
            break
    if cursor <= end:
        missing.append((cursor, end))
    return missing


def _to_strategy_frame(dataframe: pd.DataFrame, from_date: date, to_date: date) -> pd.DataFrame:
    if dataframe.empty:
        return pd.DataFrame(columns=["time_ist", "trade_date", "open", "high", "low", "close", "tick_volume"])
    df = dataframe.copy()
    df["time_utc"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df["time_ist"] = df["time_utc"].dt.tz_convert(IST)
    df["trade_date"] = df["time_ist"].dt.date
    for column in ["open", "high", "low", "close"]:
        df[column] = pd.to_numeric(df[column], errors="raise")
    df["tick_volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    df = df[(df["trade_date"] >= from_date) & (df["trade_date"] <= to_date)]
    return df[["time_ist", "trade_date", "open", "high", "low", "close", "tick_volume"]].copy()
