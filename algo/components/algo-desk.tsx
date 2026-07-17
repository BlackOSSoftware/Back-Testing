"use client";

import {
  Activity,
  AlertTriangle,
  ArrowRightLeft,
  Bot,
  ChartNoAxesCombined,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  Database,
  Info,
  Layers3,
  LockKeyhole,
  Percent,
  Radio,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  ShieldCheck,
  Settings,
  ScrollText,
  Target,
  Terminal,
  TrendingDown,
  TrendingUp,
  XCircle,
} from "lucide-react";
import type { ReactNode } from "react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

type DeskView = "dashboard" | "settings" | "logs";

type Strategy = {
  id: string;
  name: string;
  data_source: string;
  symbol: string;
  timeframe: string;
  trail_timeframe: string;
  entry_pattern: string;
  range_start: string;
  range_end: string;
  session_start: string;
  entry_cutoff: string;
  session_end: string;
  entry_buffer_pct: number;
  entry_buffer_points: number;
  stop_points: number;
  stop_points_unit: string;
  first_trail_profit: number;
  first_trail_profit_unit: string;
  first_trail_lock_loss: number;
  first_trail_lock_loss_unit: string;
  second_trail_profit: number;
  second_trail_profit_unit: string;
  volume: number;
  target_points: number;
  max_trades_per_day: number;
  max_open_positions: number;
  live_trading_enabled: boolean;
  execution_mode: "PENDING_BEFORE_SIGNAL" | "MARKET_AFTER_SIGNAL" | "REARM_PENDING";
  updated_at?: string;
};

type LiveQuote = {
  symbol?: string;
  bid?: number;
  ask?: number;
  last?: number;
  point?: number;
  spread?: number;
  time?: string;
  error?: string;
};

type TradeAction = {
  status?: string;
  message?: string;
  ticket?: number;
  retcode?: number;
  price?: number;
  stop_loss?: number;
  take_profit?: number;
};

type BrokerOrder = {
  ticket: number;
  side: string;
  price: number;
  stop_loss: number;
  take_profit: number;
  volume: number;
  state: number;
  comment?: string;
};

type BrokerPosition = {
  ticket: number;
  side: string;
  price: number;
  current_price: number;
  stop_loss: number;
  take_profit: number;
  volume: number;
  profit: number;
};

type ChartCandle = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  tick_volume: number;
};

type ChartFeed = {
  symbol: string;
  timeframe: string;
  candles: ChartCandle[];
  live_price: number;
  server_time: string;
};

type Signal = {
  checked_at?: string;
  strategy_id?: string;
  symbol?: string;
  timeframe?: string;
  last_candle_time?: string;
  last_close?: number;
  range_high?: number;
  range_low?: number;
  buy_trigger?: number;
  sell_trigger?: number;
  trigger_override_active?: boolean;
  buffer?: string;
  phase?: string;
  status?: string;
  message?: string;
  side?: string;
  entry_reference?: number;
  stop_loss?: number;
  trigger_candle_time?: string;
  live_quote?: LiveQuote;
  trade_action?: TradeAction;
};

type LogRow = {
  id: number;
  created_at: string;
  strategy_id: string;
  symbol: string;
  timeframe?: string;
  side: string;
  status: string;
  message?: string;
  entry_price?: number;
  stop_loss?: number;
  payload?: Record<string, unknown>;
};

type EventLogRow = {
  id: number;
  created_at: string;
  day_key: string;
  level: string;
  event: string;
  strategy_id: string;
  symbol: string;
  message: string;
  payload?: Record<string, unknown>;
};

type AlgoState = {
  running: boolean;
  active_strategy_id: string;
  active_strategy: Strategy | null;
  started_at: string;
  stopped_at: string;
  last_error: string;
  algo_status: string;
  pending_order_day: string;
  buy_trigger_override?: number | null;
  sell_trigger_override?: number | null;
  trigger_override_day?: string;
  last_signal: Signal;
  live_quote: LiveQuote;
  pending_orders: BrokerOrder[];
  open_positions: BrokerPosition[];
  strategies: Strategy[];
  signal_log: LogRow[];
  trade_log: LogRow[];
  event_log: EventLogRow[];
  database: string;
};

const emptyStrategy: Strategy = {
  id: "",
  name: "",
  data_source: "MT5",
  symbol: "",
  timeframe: "",
  trail_timeframe: "",
  entry_pattern: "",
  range_start: "",
  range_end: "",
  session_start: "",
  entry_cutoff: "",
  session_end: "",
  entry_buffer_pct: "" as unknown as number,
  entry_buffer_points: 0,
  stop_points: "" as unknown as number,
  stop_points_unit: "POINTS",
  first_trail_profit: "" as unknown as number,
  first_trail_profit_unit: "POINTS",
  first_trail_lock_loss: "" as unknown as number,
  first_trail_lock_loss_unit: "POINTS",
  second_trail_profit: "" as unknown as number,
  second_trail_profit_unit: "POINTS",
  volume: "" as unknown as number,
  target_points: 0,
  max_trades_per_day: 1,
  max_open_positions: 1,
  live_trading_enabled: false,
  execution_mode: "" as Strategy["execution_mode"],
};

const timeframes = ["M1", "M2", "M3", "M4", "M5", "M10", "M15", "M30", "H1", "H4"];
const patterns = [
  ["BOTH", "Both"],
  ["BUY_ONLY", "Buy only"],
  ["SELL_ONLY", "Sell only"],
];

function toNumber(value: unknown) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function numberText(value: unknown) {
  const number = toNumber(value);
  return number === null ? "-" : number.toLocaleString("en-IN", { maximumFractionDigits: 3 });
}

function distanceToPoints(value: number, unit: string, entry: number) {
  return String(unit).toUpperCase() === "PERCENT" ? (entry * value) / 100 : value;
}

function riskValueText(value: number, unit: string) {
  return String(unit).toUpperCase() === "PERCENT" ? `${numberText(value)}%` : `${numberText(value)} pts`;
}

function compactTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function istDayKey(value?: string | Date) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const year = parts.find((part) => part.type === "year")?.value || "0000";
  const month = parts.find((part) => part.type === "month")?.value || "00";
  const day = parts.find((part) => part.type === "day")?.value || "00";
  return `${year}-${month}-${day}`;
}

function isTodayIst(value?: string) {
  return Boolean(value && istDayKey(value) === istDayKey());
}

function phaseText(value?: string) {
  const labels: Record<string, string> = {
    WAITING: "Waiting",
    WAIT_RANGE: "Waiting for range",
    WAIT_SESSION: "Waiting for session",
    SESSION_DONE: "Session done",
    SIGNAL: "Signal scan",
  };
  return value ? labels[value] || value.replaceAll("_", " ").toLowerCase() : "-";
}

function patternText(value?: string) {
  const match = patterns.find(([key]) => key === value);
  return match?.[1] || value || "-";
}

function levelFrom(entry: number, side: string, distance: number, direction: "profit" | "loss") {
  if (side === "BUY") return direction === "profit" ? entry + distance : entry - distance;
  if (side === "SELL") return direction === "profit" ? entry - distance : entry + distance;
  return null;
}

function firstTrailLockStop(entry: number, side: string, lockDistance: number) {
  const effectiveLock = Math.max(lockDistance, 0);
  if (side === "BUY") return entry + effectiveLock;
  if (side === "SELL") return entry - effectiveLock;
  return null;
}

function firstTrailLockNote(lockDistance: number, unit: string) {
  return lockDistance > 0 ? `SL lock: +${riskValueText(lockDistance, unit)}` : "SL lock: break-even";
}

function hasStopLossText(...values: unknown[]) {
  return values.some((value) => /stop[\s_-]*loss|sl[\s_-]*hit/i.test(String(value || "")));
}

function durationText(start?: string, end?: string) {
  if (!start || !end) return "-";
  const startDate = new Date(start);
  const endDate = new Date(end);
  if (Number.isNaN(startDate.getTime()) || Number.isNaN(endDate.getTime())) return "-";
  const totalSeconds = Math.max(0, Math.floor((endDate.getTime() - startDate.getTime()) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":");
}

function liveExitPrice(side: string, quote?: LiveQuote) {
  if (!quote) return null;
  if (side === "BUY") return toNumber(quote.bid) ?? toNumber(quote.last);
  if (side === "SELL") return toNumber(quote.ask) ?? toNumber(quote.last);
  return toNumber(quote.last) ?? toNumber(quote.bid) ?? toNumber(quote.ask);
}

function buildTrailPlan(strategy: Strategy, signal: Signal, trade?: LogRow, quote?: LiveQuote) {
  const side = trade?.side || signal.side || "";
  const entry = toNumber(trade?.entry_price) ?? toNumber(signal.entry_reference);
  const lastClose = liveExitPrice(side, quote) ?? toNumber(signal.last_close);
  const initialStop = toNumber(trade?.stop_loss) ?? toNumber(signal.stop_loss);

  if (!side || entry === null) {
    return {
      side,
      entry,
      initialStop,
      firstTrigger: null,
      firstStop: null,
      secondTrigger: null,
      move: null,
      firstHit: false,
      firstStopHit: false,
      secondHit: false,
    };
  }

  const firstDistance = distanceToPoints(strategy.first_trail_profit, strategy.first_trail_profit_unit, entry);
  const firstLockDistance = distanceToPoints(strategy.first_trail_lock_loss, strategy.first_trail_lock_loss_unit, entry);
  const secondDistance = distanceToPoints(strategy.second_trail_profit, strategy.second_trail_profit_unit, entry);
  const firstTrigger = levelFrom(entry, side, firstDistance, "profit");
  const firstStop = firstTrailLockStop(entry, side, firstLockDistance);
  const secondTrigger = levelFrom(entry, side, firstDistance + secondDistance, "profit");
  const move = lastClose === null ? null : side === "BUY" ? lastClose - entry : entry - lastClose;
  const firstHit = move !== null && move >= firstDistance;
  const firstStopHit =
    firstHit &&
    firstStop !== null &&
    lastClose !== null &&
    (side === "BUY" ? lastClose <= firstStop : lastClose >= firstStop);
  const secondHit = move !== null && move >= firstDistance + secondDistance;

  return { side, entry, initialStop, firstTrigger, firstStop, secondTrigger, move, firstHit, firstStopHit, secondHit };
}

function tradeLevelPlan(strategy: Strategy, level: number | null, side: "BUY" | "SELL") {
  if (level === null) return { stop: null, first: null, lock: null, second: null };
  const stopDistance = distanceToPoints(strategy.stop_points, strategy.stop_points_unit, level);
  const firstDistance = distanceToPoints(strategy.first_trail_profit, strategy.first_trail_profit_unit, level);
  const lockDistance = distanceToPoints(strategy.first_trail_lock_loss, strategy.first_trail_lock_loss_unit, level);
  const secondDistance = distanceToPoints(strategy.second_trail_profit, strategy.second_trail_profit_unit, level);
  return {
    stop: side === "BUY" ? level - stopDistance : level + stopDistance,
    first: side === "BUY" ? level + firstDistance : level - firstDistance,
    lock: side === "BUY" ? level + lockDistance : level - lockDistance,
    second: side === "BUY" ? level + firstDistance + secondDistance : level - firstDistance - secondDistance,
  };
}

async function apiJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers || {}),
    },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed.");
  return data as T;
}

export function AlgoDesk({ initialView = "dashboard" }: { initialView?: DeskView }) {
  const [view, setView] = useState<DeskView>(initialView);
  const [state, setState] = useState<AlgoState | null>(null);
  const [form, setForm] = useState<Strategy>(emptyStrategy);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [socketConnected, setSocketConnected] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const viewRef = useRef<DeskView>(initialView);

  const activeSignal = state?.last_signal || {};
  const liveQuote = state?.live_quote || activeSignal.live_quote || {};
  const selectedExists = Boolean(form.id);
  const sortedStrategies = useMemo(() => state?.strategies || [], [state]);
  const selectedStrategy = form.id ? form : state?.active_strategy || sortedStrategies[0] || emptyStrategy;
  const todayTrades = useMemo(() => {
    const today = istDayKey();
    return (state?.trade_log || []).filter((row) => {
      const sameDay = istDayKey(row.created_at) === today;
      const sameStrategy = !selectedStrategy.id || row.strategy_id === selectedStrategy.id;
      return sameDay && sameStrategy;
    });
  }, [state?.trade_log, selectedStrategy.id]);
  const latestTodayTrade = todayTrades[0];
  const livePosition = state?.open_positions?.[0];
  const effectiveTrade: LogRow | undefined = livePosition
    ? {
        ...(latestTodayTrade || {}),
        id: latestTodayTrade?.id || -livePosition.ticket,
        created_at: latestTodayTrade?.created_at || state?.started_at || new Date().toISOString(),
        strategy_id: selectedStrategy.id,
        symbol: selectedStrategy.symbol,
        side: livePosition.side,
        status: "POSITION_OPEN",
        entry_price: livePosition.price,
        stop_loss: livePosition.stop_loss,
        payload: { ...(latestTodayTrade?.payload || {}), position_ticket: livePosition.ticket, profit: livePosition.profit },
      }
    : latestTodayTrade?.status === "POSITION_OPEN"
      ? undefined
      : latestTodayTrade;
  const signalToday = isTodayIst(activeSignal.checked_at);
  const trailPlan = buildTrailPlan(selectedStrategy, signalToday ? activeSignal : {}, effectiveTrade, liveQuote);
  const hasSignal = signalToday && ["BUY", "SELL"].includes(String(activeSignal.side || "").toUpperCase());
  const headerSide = effectiveTrade?.side || (signalToday ? activeSignal.side : "") || "";
  const headerStop = effectiveTrade?.stop_loss ?? (signalToday ? activeSignal.stop_loss : undefined);
  const headerLivePrice = headerSide === "BUY" || headerSide === "SELL" ? liveExitPrice(headerSide, liveQuote) : toNumber(liveQuote.last) ?? toNumber(liveQuote.bid);
  const headerStopNumber = toNumber(headerStop);
  const headerLiveNumber = toNumber(headerLivePrice);
  const headerStopLossHit =
    Boolean(effectiveTrade) &&
    (hasStopLossText(
      effectiveTrade?.status,
      effectiveTrade?.message,
      effectiveTrade?.payload?.status,
      effectiveTrade?.payload?.message,
    ) ||
    (hasSignal &&
      headerStopNumber !== null &&
      headerLiveNumber !== null &&
      (headerSide === "BUY" ? headerLiveNumber <= headerStopNumber : headerSide === "SELL" ? headerLiveNumber >= headerStopNumber : false)));
  const isSettings = view === "settings";
  const isLogs = view === "logs";

  const loadSymbols = useCallback(async (query = "") => {
    try {
      const suffix = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : "";
      const data = await apiJson<{ symbols: string[] }>(`/api/algo/symbols${suffix}`);
      setSymbols(data.symbols || []);
    } catch {
      setSymbols([]);
    }
  }, []);

  useEffect(() => {
    viewRef.current = view;
  }, [view]);

  useEffect(() => {
    const viewFromLocation = () => {
      const params = new URLSearchParams(window.location.search);
      return window.location.pathname === "/settings" ? "settings" : params.get("view") === "logs" ? "logs" : "dashboard";
    };
    const onPopState = () => {
      setView(viewFromLocation());
    };
    setView(viewFromLocation());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (isSettings) void loadSymbols();
  }, [isSettings, loadSymbols]);

  useEffect(() => {
    let closed = false;
    let retryTimer = 0;

    function connect() {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(`${protocol}//${window.location.host}/ws/algo`);
      socketRef.current = socket;

      socket.onopen = () => {
        setSocketConnected(true);
        socket.send(JSON.stringify({ type: "refresh" }));
      };

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(String(event.data)) as { type?: string; state?: AlgoState; message?: string };
          if (payload.type === "state" && payload.state) {
            setState(payload.state);
            if (viewRef.current !== "settings" && payload.state.active_strategy) {
              setForm(payload.state.active_strategy);
            }
          }
          if (payload.type === "error" && payload.message) {
            setError(payload.message);
          }
        } catch {
          setError("Live update message was invalid.");
        }
      };

      socket.onclose = () => {
        setSocketConnected(false);
        if (!closed) {
          retryTimer = window.setTimeout(connect, 3000);
        }
      };

      socket.onerror = () => {
        setSocketConnected(false);
      };
    }

    connect();
    return () => {
      closed = true;
      window.clearTimeout(retryTimer);
      socketRef.current?.close();
    };
  }, []);

  async function load() {
    setError("");
    const data = await apiJson<AlgoState>("/api/algo");
    setState(data);
    if (data.active_strategy) {
      setForm(data.active_strategy);
    } else if (data.strategies[0]) {
      setForm(data.strategies[0]);
    }
  }

  function field<K extends keyof Strategy>(key: K, value: Strategy[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function saveStrategy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const url = selectedExists ? `/api/algo/strategies/${encodeURIComponent(form.id)}` : "/api/algo/strategies";
      const method = selectedExists ? "PUT" : "POST";
      const settings = {
        ...form,
        name: `${form.symbol} ${form.timeframe} breakout`,
        data_source: "MT5",
        entry_buffer_points: 0,
        target_points: 0,
        max_trades_per_day: 1,
        max_open_positions: 1,
      };
      const data = await apiJson<{ strategy: Strategy }>(url, {
        method,
        body: JSON.stringify(settings),
      });
      setForm(data.strategy);
      setMessage(selectedExists ? "Strategy settings updated." : "New strategy saved.");
      await load();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function control(action: "start" | "stop" | "check") {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const data = await apiJson<AlgoState>("/api/algo/control", {
        method: "POST",
        body: JSON.stringify({ action, strategy_id: selectedStrategy.id || form.id }),
      });
      setState(data);
      if (data.active_strategy) setForm(data.active_strategy);
      if (socketRef.current?.readyState === WebSocket.OPEN) {
        socketRef.current.send(JSON.stringify({ type: "refresh" }));
        if (action === "start") socketRef.current.send(JSON.stringify({ type: "start" }));
        if (action === "check") socketRef.current.send(JSON.stringify({ type: "check" }));
      }
      setMessage(action === "check" ? "Signal checked." : action === "start" ? "Algo started." : "Algo stopped.");
    } catch (controlError) {
      setError(controlError instanceof Error ? controlError.message : "Action failed.");
      await load().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  async function updateTriggerLevel(side: "BUY" | "SELL", price: number) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const data = await apiJson<AlgoState & { level_update?: { message?: string } }>("/api/algo/control", {
        method: "POST",
        body: JSON.stringify({ action: "update_level", strategy_id: selectedStrategy.id, side, price }),
      });
      setState(data);
      setMessage(data.level_update?.message || `${side} trigger updated.`);
      if (socketRef.current?.readyState === WebSocket.OPEN) socketRef.current.send(JSON.stringify({ type: "refresh" }));
    } catch (levelError) {
      const text = levelError instanceof Error ? levelError.message : "Trigger update failed.";
      setError(text);
      throw levelError;
    } finally {
      setBusy(false);
    }
  }

  async function resetTriggerLevels() {
    if (!window.confirm("Remove manual BUY/SELL levels and restore strategy-calculated levels? Existing pending MT5 orders will be refreshed.")) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const data = await apiJson<AlgoState & { level_reset?: { message?: string } }>("/api/algo/control", {
        method: "POST",
        body: JSON.stringify({ action: "reset_levels", strategy_id: selectedStrategy.id }),
      });
      setState(data);
      setMessage(data.level_reset?.message || "Strategy levels restored.");
      if (socketRef.current?.readyState === WebSocket.OPEN) socketRef.current.send(JSON.stringify({ type: "refresh" }));
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : "Strategy level reset failed.");
      throw resetError;
    } finally {
      setBusy(false);
    }
  }

  function selectStrategy(strategy: Strategy) {
    setForm(strategy);
    setMessage("");
    setError("");
  }

  function navigate(nextView: DeskView) {
    const href = nextView === "settings" ? "/settings" : nextView === "logs" ? "/?view=logs" : "/";
    setView(nextView);
    window.history.pushState({}, "", href);
    window.scrollTo(0, 0);
  }

  return (
    <main className={`min-h-screen text-neutral-950 ${isSettings ? "algo-settings-background" : "bg-[#f3f8fb]"}`}>
      <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[236px_minmax(0,1fr)]">
        <aside className="flex border-b border-slate-900/20 bg-[radial-gradient(circle_at_20%_0%,#164767_0%,#08243d_40%,#04182d_100%)] p-4 text-white shadow-[8px_0_30px_rgba(2,12,27,0.12)] lg:sticky lg:top-0 lg:h-screen lg:flex-col lg:border-b-0">
          <div className="flex items-center gap-3 lg:flex-col lg:text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-300 to-cyan-500 text-white shadow-[0_14px_30px_rgba(20,184,166,0.35)]">
              <Bot size={25} />
            </div>
            <div>
              <div className="text-base font-semibold">Algo Command</div>
              <div className="mt-1 text-sm text-slate-200">Live Signal Desk</div>
            </div>
          </div>

          <nav className="ml-4 grid flex-1 grid-cols-2 gap-2 text-sm lg:ml-0 lg:mt-8 lg:flex-none lg:grid-cols-1">
            <button className={navClass(!isSettings && !isLogs)} onClick={() => navigate("dashboard")} type="button">
              <Activity size={16} /> Dashboard
            </button>
            <button className={navClass(isSettings)} onClick={() => navigate("settings")} type="button">
              <Settings size={16} /> Settings
            </button>
            <button className={navClass(isLogs)} onClick={() => navigate("logs")} type="button">
              <ScrollText size={16} /> Logs
            </button>
            <a className={navClass(false)} href="/chart" rel="noopener noreferrer" target="_blank">
              <ChartNoAxesCombined size={16} /> Chart
            </a>
          </nav>

          <div className="hidden rounded-xl border border-white/10 bg-white/[0.08] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] lg:mt-8 lg:block">
            <div className="text-xs text-slate-300">Algo Status</div>
            <div className="mt-2 flex items-center gap-2 text-sm font-medium">
              <span className={`h-2.5 w-2.5 rounded-full ${state?.running ? "bg-emerald-400 shadow-[0_0_12px_rgba(52,211,153,0.75)]" : "bg-slate-500"}`} />
              {state?.running ? "Running" : "Stopped"}
            </div>
            <div className="mt-6 text-xs text-slate-300">Active Strategy</div>
            <div className="mt-2 break-words text-sm font-semibold">{state?.active_strategy?.name || "-"}</div>
          </div>

        </aside>

        <section className="min-w-0 p-3 lg:p-4">
          <ControlHeader
            busy={busy}
            error={error}
            message={message}
            running={Boolean(state?.running)}
            signalToday={signalToday}
            status={headerStopLossHit ? "Stop loss hit." : state?.algo_status || "Loading..."}
            strategy={selectedStrategy}
            lastSignalAt={activeSignal.checked_at}
            title={isSettings ? "Strategy Settings" : isLogs ? "Technical Logs" : "Trading Dashboard"}
            subtitle={isSettings ? "Manage your strategy configuration." : isLogs ? "Daily technical event logs for order and SL tracking." : "Today signal, entry, stop loss, and trail status in one place."}
            lastError={state?.last_error}
            socketConnected={socketConnected}
            onCheck={() => void control("check")}
            onRefresh={() => void load()}
            onStart={() => void control("start")}
            onStop={() => void control("stop")}
          />

          {isSettings ? (
            <SettingsView
              form={form}
              saving={saving}
              symbols={symbols}
              onField={field}
              onReset={() =>
                setForm((current) => ({
                  ...current,
                  symbol: emptyStrategy.symbol,
                  timeframe: emptyStrategy.timeframe,
                  trail_timeframe: emptyStrategy.trail_timeframe,
                  entry_pattern: emptyStrategy.entry_pattern,
                  volume: emptyStrategy.volume,
                  range_start: emptyStrategy.range_start,
                  range_end: emptyStrategy.range_end,
                  session_start: emptyStrategy.session_start,
                  entry_cutoff: emptyStrategy.entry_cutoff,
                  session_end: emptyStrategy.session_end,
                  entry_buffer_pct: emptyStrategy.entry_buffer_pct,
                  stop_points: emptyStrategy.stop_points,
                  stop_points_unit: emptyStrategy.stop_points_unit,
                  first_trail_profit: emptyStrategy.first_trail_profit,
                  first_trail_profit_unit: emptyStrategy.first_trail_profit_unit,
                  first_trail_lock_loss: emptyStrategy.first_trail_lock_loss,
                  first_trail_lock_loss_unit: emptyStrategy.first_trail_lock_loss_unit,
                  second_trail_profit: emptyStrategy.second_trail_profit,
                  second_trail_profit_unit: emptyStrategy.second_trail_profit_unit,
                  live_trading_enabled: false,
                  execution_mode: emptyStrategy.execution_mode,
                }))
              }
              onSave={saveStrategy}
              onSymbolSearch={loadSymbols}
            />
          ) : isLogs ? (
            <LogsView logs={state?.event_log || []} />
          ) : (
            <DashboardView
              activeSignal={activeSignal}
              hasSignal={hasSignal}
              latestTodayTrade={effectiveTrade}
              liveQuote={liveQuote}
              livePosition={livePosition}
              pendingOrders={state?.pending_orders || []}
              running={Boolean(state?.running)}
              selectedStrategy={selectedStrategy}
              signalToday={signalToday}
              sortedStrategies={sortedStrategies}
              todayTrades={todayTrades}
              trailPlan={trailPlan}
              onNavigateSettings={() => navigate("settings")}
              onSelect={selectStrategy}
              onResetLevels={resetTriggerLevels}
              onUpdateLevel={updateTriggerLevel}
            />
          )}
        </section>
      </div>
      <style jsx global>{`
        .input {
          min-height: 40px;
          width: 100%;
          border-radius: 8px;
          border: 1px solid #cfd8d3;
          background: #ffffff;
          padding: 8px 10px;
          color: #171717;
          outline: none;
        }
        .input:focus {
          border-color: #0f766e;
          box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.16);
        }
        .line-clamp-2 {
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }
      `}</style>
      <style jsx global>{`
        .algo-power-switch {
          display: block;
          background-color: black;
          width: 66px;
          height: 86px;
          box-shadow:
            0 0 8px 1px rgba(0, 0, 0, 0.24),
            0 0 1px 2px black,
            inset 0 2px 2px -2px white,
            inset 0 0 2px 7px #47434c,
            inset 0 0 2px 10px black;
          border-radius: 6px;
          padding: 9px;
          perspective: 700px;
        }
        .algo-power-switch input {
          display: none;
        }
        .algo-power-switch .button {
          display: block;
          height: 100%;
          position: relative;
          cursor: pointer;
          transform: translateZ(12px) rotateX(-25deg);
          transform-origin: center center -12px;
          transform-style: preserve-3d;
          transition: all 0.3s cubic-bezier(1, 0, 1, 1);
          background: linear-gradient(#980000 0%, #6f0000 30%, #6f0000 70%, #980000 100%);
          background-repeat: no-repeat;
        }
        .algo-power-switch input:checked + .button {
          transform: translateZ(12px) rotateX(25deg);
          box-shadow: 0 -8px 16px #ff5d72;
        }
        .algo-power-switch input:checked + .button .light {
          animation: power-flicker 0.2s infinite 0.3s;
        }
        .algo-power-switch input:checked + .button .shine {
          opacity: 1;
        }
        .algo-power-switch input:checked + .button .shadow {
          opacity: 0;
        }
        .algo-power-switch .button::before {
          content: "";
          width: 100%;
          height: 22px;
          position: absolute;
          top: 0;
          transform: rotateX(-90deg);
          transform-origin: top;
          background:
            linear-gradient(rgba(255, 255, 255, 0.8) 10%, rgba(255, 255, 255, 0.3) 30%, #650000 75%, #320000) 50% 50%/97% 97%,
            #b10000;
          background-repeat: no-repeat;
        }
        .algo-power-switch .button::after {
          content: "";
          width: 100%;
          height: 22px;
          position: absolute;
          bottom: 0;
          transform: translateY(22px) rotateX(-90deg);
          transform-origin: top;
          background-image: linear-gradient(#650000, #320000);
          box-shadow: 0 22px 6px 0 black, 0 36px 14px 0 rgba(0, 0, 0, 0.5);
        }
        .algo-power-switch .light {
          opacity: 0;
          animation: power-light-off 1s;
          position: absolute;
          width: 100%;
          height: 100%;
          background-image: radial-gradient(#ffd1dc, #ff3f61 40%, transparent 70%);
        }
        .algo-power-switch .dots {
          position: absolute;
          width: 100%;
          height: 100%;
          background-image: radial-gradient(transparent 30%, rgba(101, 0, 0, 0.72) 70%);
          background-size: 7px 7px;
        }
        .algo-power-switch .characters {
          position: absolute;
          width: 100%;
          height: 100%;
          background:
            linear-gradient(white, white) 50% 20%/6% 20%,
            radial-gradient(circle, transparent 50%, white 52%, white 70%, transparent 72%) 50% 80%/36% 25%;
          background-repeat: no-repeat;
        }
        .algo-power-switch .shine {
          opacity: 0.3;
          position: absolute;
          width: 100%;
          height: 100%;
          transition: all 0.3s cubic-bezier(1, 0, 1, 1);
          background:
            linear-gradient(white, transparent 3%) 50% 50%/97% 97%,
            linear-gradient(rgba(255, 255, 255, 0.5), transparent 50%, transparent 80%, rgba(255, 255, 255, 0.5)) 50% 50%/97% 97%;
          background-repeat: no-repeat;
        }
        .algo-power-switch .shadow {
          opacity: 1;
          position: absolute;
          width: 100%;
          height: 100%;
          transition: all 0.3s cubic-bezier(1, 0, 1, 1);
          background: linear-gradient(transparent 70%, rgba(0, 0, 0, 0.8));
          background-repeat: no-repeat;
        }
        .algo-power-switch-disabled {
          opacity: 0.55;
          pointer-events: none;
        }
        @keyframes power-flicker {
          0% {
            opacity: 1;
          }
          80% {
            opacity: 0.8;
          }
          100% {
            opacity: 1;
          }
        }
        @keyframes power-light-off {
          0% {
            opacity: 1;
          }
          80% {
            opacity: 0;
          }
        }
      `}</style>
    </main>
  );
}

function navClass(active: boolean) {
  return `flex items-center gap-3 rounded-xl px-4 py-3 font-semibold transition ${
    active
      ? "border-l-4 border-emerald-400 bg-white/10 text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]"
      : "border-l-4 border-transparent text-slate-200 hover:bg-white/10 hover:text-white"
  }`;
}

function ControlHeader({
  busy,
  error,
  lastError,
  lastSignalAt,
  message,
  running,
  signalToday,
  socketConnected,
  status,
  strategy,
  subtitle,
  title,
  onCheck,
  onRefresh,
  onStart,
  onStop,
}: {
  busy: boolean;
  error: string;
  lastError?: string;
  lastSignalAt?: string;
  message: string;
  running: boolean;
  signalToday: boolean;
  socketConnected: boolean;
  status: string;
  strategy: Strategy;
  subtitle: string;
  title: string;
  onCheck: () => void;
  onRefresh: () => void;
  onStart: () => void;
  onStop: () => void;
}) {
  const savedStatusMessage = status.includes(" - ") ? status.split(" - ").slice(1).join(" - ") : status;
  const statusMessage = signalToday ? savedStatusMessage : running ? "Waiting for today's signal." : "No check today.";
  const lastCheckText = signalToday ? compactTime(lastSignalAt) : "No check today";
  const statusTone = /stop|loss/i.test(statusMessage)
    ? "text-rose-700"
    : signalToday && /buy|crossed|running/i.test(statusMessage)
      ? "text-emerald-700"
      : "text-slate-600";

  return (
    <header className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-[0_6px_20px_rgba(15,23,42,0.06)]">
      <div className="grid md:grid-cols-[minmax(0,1fr)_128px_150px] xl:grid-cols-[minmax(0,1fr)_150px_172px]">
        <section className="min-w-0 p-3 md:border-r md:border-slate-200 xl:p-4">
          <div className="flex flex-wrap items-center gap-2.5">
            <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${socketConnected ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
              <span className={`h-2 w-2 rounded-full ${socketConnected ? "bg-emerald-500" : "bg-amber-500"}`} />
              {socketConnected ? "LIVE" : "CONNECTING"}
            </span>
            <span className="text-xs text-slate-500">Algo Live Control</span>
          </div>

          <h1 className="mt-2 text-[clamp(1.35rem,2vw,1.65rem)] font-semibold tracking-tight text-[#172036]">{title}</h1>
          <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>

          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-slate-500">
            <span className="inline-flex items-center gap-1.5 font-medium"><ChartNoAxesCombined size={15} /> {strategy.symbol || "-"}</span>
            <span className="h-4 w-px bg-slate-300" />
            <span className="inline-flex items-center gap-1.5"><Clock3 size={15} /> {strategy.timeframe || "-"}</span>
            <span className="h-4 w-px bg-slate-300" />
            <span className="inline-flex items-center gap-1.5"><RefreshCw size={14} /> {lastCheckText}</span>
            <span className="h-4 w-px bg-slate-300" />
            <span className={`inline-flex min-w-0 items-center gap-1.5 font-medium ${statusTone}`}><TrendingUp size={14} /> <span className="truncate">{statusMessage}</span></span>
          </div>
        </section>

        <section className="flex flex-col items-center justify-center border-t border-slate-200 p-3 md:border-r md:border-t-0">
          <PowerSwitch
            checked={running}
            disabled={busy || (!running && !strategy.symbol)}
            onChange={(checked) => {
              if (checked) onStart();
              else onStop();
            }}
          />
          <div className={`mt-2 text-xs font-semibold ${running ? "text-emerald-700" : "text-slate-500"}`}>
            Strategy is {running ? "ON" : "OFF"}
          </div>
        </section>

        <section className="grid content-center gap-2 border-t border-slate-200 p-3 md:border-t-0">
          <button
            className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-emerald-700 to-emerald-600 px-3 py-2 text-sm font-semibold text-white shadow-[0_5px_14px_rgba(5,150,105,0.18)] transition hover:brightness-105"
            disabled={busy}
            onClick={onCheck}
            type="button"
          >
            <Search size={16} /> Check Signal
          </button>
          <button
            className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-600 shadow-sm transition hover:bg-slate-50"
            disabled={busy}
            onClick={onRefresh}
            type="button"
          >
            <RefreshCw size={16} /> Refresh
          </button>
        </section>
      </div>
      {(message || error || lastError) && (
        <div
          className={`mx-3 mb-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
            error || lastError
              ? "border-rose-200 bg-rose-50 text-rose-800"
              : "border-emerald-200 bg-emerald-50 text-emerald-800"
          }`}
        >
          {error || lastError ? <AlertTriangle size={17} /> : <CheckCircle2 size={17} />}
          <span>{error || lastError || message}</span>
        </div>
      )}
    </header>
  );
}

function PowerSwitch({
  checked,
  disabled,
  onChange,
}: {
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label
      aria-label={checked ? "Stop algo" : "Start algo"}
      className={`relative inline-flex cursor-pointer items-center ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
      title={checked ? "Stop Algo" : "Start Algo"}
    >
      <input
        className="peer sr-only"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
      <span className={`relative flex h-9 w-20 items-center rounded-full px-3 text-sm font-semibold text-white shadow-inner transition ${checked ? "justify-start bg-gradient-to-r from-emerald-700 to-emerald-500" : "justify-end bg-slate-400"}`}>
        <span>{checked ? "ON" : "OFF"}</span>
        <span className={`absolute top-1 h-7 w-7 rounded-full bg-white shadow-[0_2px_7px_rgba(15,23,42,0.25)] transition-all ${checked ? "right-1" : "left-1"}`} />
      </span>
    </label>
  );
}

export function ChartOnlyPage() {
  const [state, setState] = useState<AlgoState | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let disposed = false;
    const loadChartContext = async () => {
      try {
        const data = await apiJson<AlgoState>("/api/algo", { cache: "no-store" });
        if (!disposed) {
          setState(data);
          setError("");
        }
      } catch (loadError) {
        if (!disposed) setError(loadError instanceof Error ? loadError.message : "Unable to load chart.");
      }
    };
    void loadChartContext();
    const timer = window.setInterval(loadChartContext, 2000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, []);

  const strategy = state?.active_strategy || state?.strategies?.[0];
  const signal = state?.last_signal || {};
  const signalToday = isTodayIst(signal.checked_at);
  const liveQuote = state?.live_quote || signal.live_quote || {};
  const todayTrades = (state?.trade_log || []).filter(
    (trade) => isTodayIst(trade.created_at) && (!strategy?.id || trade.strategy_id === strategy.id),
  );
  const latestTrade = todayTrades[0];
  const livePosition = state?.open_positions?.[0];
  const effectiveTrade: LogRow | undefined = livePosition && strategy
    ? {
        ...(latestTrade || {}),
        id: latestTrade?.id || -livePosition.ticket,
        created_at: latestTrade?.created_at || state?.started_at || new Date().toISOString(),
        strategy_id: strategy.id,
        symbol: strategy.symbol,
        side: livePosition.side,
        status: "POSITION_OPEN",
        entry_price: livePosition.price,
        stop_loss: livePosition.stop_loss,
      }
    : latestTrade?.status === "POSITION_OPEN"
      ? undefined
      : latestTrade;
  const trailPlan = strategy ? buildTrailPlan(strategy, signalToday ? signal : {}, effectiveTrade, liveQuote) : null;
  const entry = toNumber(effectiveTrade?.entry_price ?? (signalToday ? signal.entry_reference : undefined));
  const stopLoss = toNumber(effectiveTrade?.stop_loss ?? (signalToday ? signal.stop_loss : undefined));

  return (
    <main className="min-h-screen bg-[#f3f8fb] p-3 text-neutral-950 lg:p-5">
      <div className="mx-auto max-w-[1500px]">
        {strategy ? (
          <LiveTradingChart
            entry={entry}
            firstTarget={trailPlan?.firstTrigger ?? null}
            secondTarget={trailPlan?.secondTrigger ?? null}
            signal={signalToday ? signal : {}}
            stopLoss={stopLoss}
            strategy={strategy}
          />
        ) : (
          <div className="grid min-h-[360px] place-items-center rounded-xl border border-slate-200 bg-white text-sm text-slate-600">
            {error || "Loading chart..."}
          </div>
        )}
      </div>
    </main>
  );
}

function LogsView({ logs }: { logs: EventLogRow[] }) {
  const ordered = [...logs].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  const latest = ordered[0];
  const levelClass = (level: string) => {
    const text = level.toUpperCase();
    if (text === "ERROR") return "border-rose-500/40 bg-rose-500/10 text-rose-200";
    if (text === "ORDER") return "border-cyan-400/40 bg-cyan-400/10 text-cyan-100";
    if (text === "TRADE") return "border-emerald-400/40 bg-emerald-400/10 text-emerald-100";
    if (text === "SIGNAL") return "border-amber-300/40 bg-amber-300/10 text-amber-100";
    return "border-slate-500/40 bg-slate-500/10 text-slate-200";
  };

  return (
    <div className="mx-auto mt-3 max-w-[1460px]">
      <section className="overflow-hidden rounded-xl border border-slate-800 bg-[#05070b] shadow-[0_18px_50px_rgba(2,6,23,0.22)]">
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-[#090d14] px-4 py-3 text-slate-200">
          <div className="grid h-10 w-10 place-items-center rounded-lg border border-emerald-400/20 bg-emerald-400/10 text-emerald-200">
            <Terminal size={20} />
          </div>
          <div>
            <h2 className="text-base font-semibold text-white">Daily Technical Logs</h2>
            <p className="text-xs text-slate-400">Auto-clears when the trading day changes. Latest: {latest ? compactTime(latest.created_at) : "-"}</p>
          </div>
          <span className="ml-auto rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1 text-xs font-semibold text-slate-300">
            {ordered.length} events today
          </span>
        </div>

        <div className="max-h-[calc(100vh-230px)] min-h-[520px] overflow-auto p-3 font-mono text-[12px] leading-relaxed">
          {ordered.length ? (
            <div className="grid gap-2">
              {ordered.map((item) => (
                <article key={item.id} className="rounded-lg border border-slate-800 bg-black/45 px-3 py-2 text-slate-300">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-slate-500">{compactTime(item.created_at)}</span>
                    <span className={`rounded-md border px-2 py-0.5 text-[11px] font-bold ${levelClass(item.level)}`}>{item.level}</span>
                    <span className="text-emerald-200">{item.event}</span>
                    {item.symbol && <span className="rounded bg-slate-900 px-1.5 py-0.5 text-slate-400">{item.symbol}</span>}
                  </div>
                  <div className="mt-1 whitespace-pre-wrap text-slate-100">{item.message}</div>
                  {item.payload && Object.keys(item.payload).length > 0 && (
                    <pre className="mt-2 max-h-36 overflow-auto rounded border border-slate-800 bg-[#030508] p-2 text-[11px] text-slate-400">
                      {JSON.stringify(item.payload, null, 2)}
                    </pre>
                  )}
                </article>
              ))}
            </div>
          ) : (
            <div className="grid h-[420px] place-items-center text-center text-slate-500">
              <div>
                <Terminal className="mx-auto mb-3 text-slate-700" size={34} />
                <div className="text-sm text-slate-300">No technical logs yet today.</div>
                <div className="mt-1 text-xs">Order, signal, SL modify, and error events will appear here.</div>
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function DashboardView({
  activeSignal,
  hasSignal,
  latestTodayTrade,
  liveQuote,
  livePosition,
  pendingOrders,
  running,
  selectedStrategy,
  signalToday,
  sortedStrategies,
  todayTrades,
  trailPlan,
  onNavigateSettings,
  onResetLevels,
  onSelect,
  onUpdateLevel,
}: {
  activeSignal: Signal;
  hasSignal: boolean;
  latestTodayTrade?: LogRow;
  liveQuote: LiveQuote;
  livePosition?: BrokerPosition;
  pendingOrders: BrokerOrder[];
  running: boolean;
  selectedStrategy: Strategy;
  signalToday: boolean;
  sortedStrategies: Strategy[];
  todayTrades: LogRow[];
  trailPlan: ReturnType<typeof buildTrailPlan>;
  onNavigateSettings: () => void;
  onResetLevels: () => Promise<void>;
  onSelect: (strategy: Strategy) => void;
  onUpdateLevel: (side: "BUY" | "SELL", price: number) => Promise<void>;
}) {
  const [resettingLevels, setResettingLevels] = useState(false);
  const side = latestTodayTrade?.side || (signalToday ? activeSignal.side : "") || "-";
  const entry = latestTodayTrade?.entry_price ?? (signalToday ? activeSignal.entry_reference : undefined);
  const stop = latestTodayTrade?.stop_loss ?? (signalToday ? activeSignal.stop_loss : undefined);
  const tradeTaken = Boolean(latestTodayTrade);
  const displayLivePrice = side === "BUY" || side === "SELL" ? liveExitPrice(side, liveQuote) : toNumber(liveQuote.last) ?? toNumber(liveQuote.bid);
  const livePoints = trailPlan.move;
  const tradeAction = activeSignal.trade_action;
  const rangeReady = signalToday && activeSignal.phase !== "WAIT_RANGE" && activeSignal.phase !== "WAIT_SESSION";
  const buyLevel = rangeReady ? toNumber(activeSignal.buy_trigger) : null;
  const sellLevel = rangeReady ? toNumber(activeSignal.sell_trigger) : null;
  const marketPrice = toNumber(liveQuote.last) ?? toNumber(liveQuote.bid) ?? toNumber(liveQuote.ask);
  const buyTriggered = hasSignal && side === "BUY";
  const sellTriggered = hasSignal && side === "SELL";
  const buyPending = pendingOrders.find((order) => order.side === "BUY");
  const sellPending = pendingOrders.find((order) => order.side === "SELL");
  const buyClosedByOco = livePosition?.side === "SELL";
  const sellClosedByOco = livePosition?.side === "BUY";
  const buyPlan = tradeLevelPlan(selectedStrategy, buyLevel, "BUY");
  const sellPlan = tradeLevelPlan(selectedStrategy, sellLevel, "SELL");
  const entryNumber = toNumber(entry);
  const stopNumber = toNumber(stop);
  const livePriceNumber = toNumber(displayLivePrice);
  const explicitStopLossHit = hasStopLossText(
    latestTodayTrade?.status,
    latestTodayTrade?.message,
    latestTodayTrade?.payload?.status,
    latestTodayTrade?.payload?.message,
    tradeAction?.status,
    tradeAction?.message,
    activeSignal.status,
    activeSignal.message,
  );
  const priceStopLossHit =
    hasSignal &&
    stopNumber !== null &&
    livePriceNumber !== null &&
    (side === "BUY" ? livePriceNumber <= stopNumber : side === "SELL" ? livePriceNumber >= stopNumber : false);
  const stopLossHit = tradeTaken && (explicitStopLossHit || priceStopLossHit);
  const displayPrice = stopLossHit && stopNumber !== null ? stopNumber : displayLivePrice;
  const stopMove =
    stopLossHit && entryNumber !== null && stopNumber !== null
      ? side === "BUY"
        ? stopNumber - entryNumber
        : entryNumber - stopNumber
      : livePoints;
  const signalLabel = hasSignal ? side : signalToday ? activeSignal.status || "No signal" : "Waiting";
  const signalMessage = stopLossHit
    ? `Stop loss hit at ${numberText(stopNumber)}`
    : signalToday
      ? activeSignal.message || "Signal checked."
      : "Today signal has not been checked yet.";
  const firstTargetNote = `+${riskValueText(selectedStrategy.first_trail_profit, selectedStrategy.first_trail_profit_unit)}`;
  const secondTargetNote = `+${riskValueText(selectedStrategy.second_trail_profit, selectedStrategy.second_trail_profit_unit)}`;
  const progressClass = stopLossHit ? "w-[88%]" : trailPlan.secondHit ? "w-[72%]" : trailPlan.firstHit ? "w-[34%]" : "w-[1%]";
  const progressTone = stopLossHit ? "bg-rose-600" : "bg-emerald-600";
  const signalTone = stopLossHit || side === "SELL" ? "red" : hasSignal ? "green" : "plain";
  const exitTime = latestTodayTrade?.created_at || liveQuote.time || activeSignal.checked_at;
  const tradeDuration = durationText(activeSignal.trigger_candle_time || activeSignal.checked_at, exitTime);

  return (
    <div className="mx-auto mt-3 max-w-[1460px]">
      <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,1fr)_310px]">
        <div className="grid content-start gap-3">
          <section className="order-1 self-start rounded-xl border border-slate-200/80 bg-white p-3 shadow-[0_10px_28px_rgba(15,23,42,0.06)]">
            <div className="flex items-center gap-3">
              <Radio size={17} className={stopLossHit ? "text-rose-600" : "text-emerald-700"} />
              <h2 className="text-lg font-semibold tracking-tight text-slate-950">Today&apos;s Signal</h2>
              {stopLossHit && (
                <span className="ml-auto rounded-lg bg-rose-50 px-3 py-1 text-xs font-semibold text-rose-700">STOP LOSS HIT</span>
              )}
              <span className={`${stopLossHit ? "" : "ml-auto"} text-xs text-slate-500`}>{signalToday ? compactTime(activeSignal.checked_at) : "No check today"}</span>
            </div>

            <div className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-white">
              <div className="grid md:grid-cols-[170px_repeat(4,minmax(0,1fr))]">
                <div className={`relative p-4 ${signalTone === "red" ? "bg-rose-50/85" : signalTone === "green" ? "bg-emerald-50/80" : "bg-slate-50"}`}>
                  <div className={`flex items-center gap-2 text-xs font-semibold ${signalTone === "red" ? "text-rose-700" : signalTone === "green" ? "text-emerald-700" : "text-slate-600"}`}>
                    {side === "SELL" ? <TrendingDown size={17} /> : <TrendingUp size={17} />}
                    Signal
                  </div>
                  <div className={`mt-2 text-2xl font-semibold ${signalTone === "red" ? "text-rose-600" : signalTone === "green" ? "text-emerald-700" : "text-slate-600"}`}>
                    {signalLabel}
                  </div>
                  <span
                    aria-hidden="true"
                    className={`absolute -right-5 top-0 hidden h-full w-10 md:block ${signalTone === "red" ? "bg-rose-50/85" : signalTone === "green" ? "bg-emerald-50/80" : "bg-slate-50"}`}
                    style={{ clipPath: "polygon(0 0, 50% 0, 100% 50%, 50% 100%, 0 100%)" }}
                  />
                </div>
                <SignalStat indent label="Entry Price" value={numberText(entry)} />
                <SignalStat label="Live Price" value={numberText(displayPrice)} note={liveQuote.error || compactTime(liveQuote.time)} />
                <SignalStat
                  label="P/L (Points)"
                  value={numberText(stopMove)}
                  tone={stopMove !== null && stopMove >= 0 ? "green" : stopMove !== null ? "red" : "plain"}
                />
                {stopLossHit ? (
                  <SignalStat label="Result" value="STOP LOSS HIT" tone="red" />
                ) : (
                  <SignalStat label="Stop Loss" value={numberText(stop)} tone="red" />
                )}
              </div>
            </div>

            <div className={`mt-3 grid overflow-hidden rounded-xl border border-slate-200 bg-white ${stopLossHit ? "max-w-none md:grid-cols-4" : "max-w-[640px] md:grid-cols-3"}`}>
              <SignalStat label="First Target" value={numberText(trailPlan.firstTrigger)} note={firstTargetNote} tone="green" compact />
              <SignalStat label="Second Target" value={numberText(trailPlan.secondTrigger)} note={secondTargetNote} tone="green" compact />
              {stopLossHit && <SignalStat label="Stop Loss" value={numberText(stop)} note={entryNumber !== null && stopNumber !== null ? `${numberText(stopMove)} pts` : ""} tone="red" compact />}
              <SignalStat
                label="Trade Taken"
                value={tradeTaken ? "Yes" : "No"}
                note={tradeTaken ? latestTodayTrade?.status || "Trade log found" : tradeAction?.message || "Auto trade is disabled"}
                compact
              />
            </div>

            <div className={`mt-3 flex items-center gap-3 rounded-xl border px-3 py-2 text-sm ${stopLossHit ? "border-rose-200 bg-rose-50/80 text-rose-800" : signalToday ? "border-emerald-100 bg-emerald-50/75 text-emerald-800" : "border-slate-200 bg-slate-50 text-slate-600"}`}>
              {stopLossHit ? <XCircle size={18} className="text-rose-700" /> : <CheckCircle2 size={18} className={signalToday ? "text-emerald-700" : "text-slate-400"} />}
              <div className="min-w-0">
                <div className="font-medium">{signalMessage}</div>
                {stopLossHit && <div className="text-xs text-rose-700">{selectedStrategy.live_trading_enabled ? "Trade closed automatically" : "Stop loss level reached"}</div>}
              </div>
              {stopLossHit && <span className="ml-auto text-xs text-rose-700">{compactTime(exitTime)}</span>}
            </div>
          </section>

          <section className="order-3 self-start rounded-xl border border-slate-200/80 bg-white p-3 shadow-[0_10px_28px_rgba(15,23,42,0.06)]">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold tracking-tight text-slate-950">Live Trade Levels</h2>
              <Info size={17} className="text-slate-400" />
              <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
                {activeSignal.trigger_override_active ? (
                  <>
                    <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-800">EDITED MODE</span>
                    <button
                      className="inline-flex min-h-8 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={resettingLevels}
                      onClick={async () => {
                        setResettingLevels(true);
                        try {
                          await onResetLevels();
                        } finally {
                          setResettingLevels(false);
                        }
                      }}
                      type="button"
                    >
                      <RotateCcw size={14} /> {resettingLevels ? "Resetting..." : "Reset to Strategy"}
                    </button>
                  </>
                ) : (
                  <span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-700">STRATEGY MODE</span>
                )}
              </div>
            </div>
            <p className="mt-1 text-sm text-slate-500">
              {rangeReady ? "Exact prices where the system will detect a BUY or SELL breakout." : "Breakout trigger prices will appear after the range window closes."}
            </p>

            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <TradeLevelCard
                enabled={rangeReady && selectedStrategy.entry_pattern !== "SELL_ONLY"}
                icon={<TrendingUp size={24} />}
                label="BUY above"
                level={buyLevel}
                locked={buyClosedByOco}
                order={buyPending}
                plan={buyPlan}
                onUpdate={(price) => onUpdateLevel("BUY", price)}
                note={
                  !rangeReady
                    ? activeSignal.message || "Range window is still forming."
                    : buyClosedByOco
                    ? buyPending
                      ? "SELL position is open - cancelling opposite BUY order in MT5"
                      : "SELL position is open - opposite BUY order was cancelled by OCO"
                    : buyPending
                    ? `Pending BUY STOP is live in MT5 · Ticket #${buyPending.ticket} · SL ${numberText(buyPending.stop_loss)}`
                    : buyTriggered
                    ? "BUY trigger crossed"
                    : buyLevel === null
                      ? activeSignal.message || "Waiting for range levels"
                      : marketPrice === null
                        ? "Waiting for live price"
                        : `${numberText(Math.max(buyLevel - marketPrice, 0))} pts away from live price`
                }
                tone="green"
                triggered={buyTriggered}
              />
              <TradeLevelCard
                enabled={rangeReady && selectedStrategy.entry_pattern !== "BUY_ONLY"}
                icon={<TrendingDown size={24} />}
                label="SELL below"
                level={sellLevel}
                locked={sellClosedByOco}
                order={sellPending}
                plan={sellPlan}
                onUpdate={(price) => onUpdateLevel("SELL", price)}
                note={
                  !rangeReady
                    ? activeSignal.message || "Range window is still forming."
                    : sellClosedByOco
                    ? sellPending
                      ? "BUY position is open - cancelling opposite SELL order in MT5"
                      : "BUY position is open - opposite SELL order was cancelled by OCO"
                    : sellPending
                    ? `Pending SELL STOP is live in MT5 · Ticket #${sellPending.ticket} · SL ${numberText(sellPending.stop_loss)}`
                    : sellTriggered
                    ? "SELL trigger crossed"
                    : sellLevel === null
                      ? activeSignal.message || "Waiting for range levels"
                      : marketPrice === null
                        ? "Waiting for live price"
                        : `${numberText(Math.max(marketPrice - sellLevel, 0))} pts away from live price`
                }
                tone="red"
                triggered={sellTriggered}
              />
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border border-blue-100 bg-blue-50/70 px-3 py-2 text-sm text-blue-900">
              <span><strong>Live:</strong> {numberText(marketPrice)}</span>
              <span><strong>Range High:</strong> {numberText(signalToday ? activeSignal.range_high : null)}</span>
              <span><strong>Range Low:</strong> {numberText(signalToday ? activeSignal.range_low : null)}</span>
              <span className="ml-auto text-xs text-blue-700">{phaseText(activeSignal.phase)}</span>
            </div>
          </section>

          <section className="order-2 self-start rounded-xl border border-slate-200/80 bg-white p-3 shadow-[0_10px_28px_rgba(15,23,42,0.06)]">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold tracking-tight text-slate-950">{stopLossHit ? "Trade Summary" : "Targets &amp; Trailing"}</h2>
              <Info size={17} className="text-slate-400" />
              <span className={`ml-auto rounded-lg px-2 py-1 text-xs ${running ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>
                {running ? "Algo running" : "Algo stopped"}
              </span>
            </div>

            <div className="relative mt-4 px-4 pb-2">
              <div className="absolute bottom-[13px] left-8 right-8 h-0.5 bg-slate-200" />
              <div className={`absolute bottom-[13px] left-8 h-0.5 transition-all ${progressTone} ${progressClass}`} />
              <div className="relative grid grid-cols-2 gap-4 md:grid-cols-4">
                <TargetStage active={entry !== undefined && entry !== null} label="Entry" value={numberText(entry)} />
                <TargetStage active={!stopLossHit && trailPlan.firstHit} label="First Target" note={firstTargetNote} value={numberText(trailPlan.firstTrigger)} muted={stopLossHit} />
                <TargetStage active={!stopLossHit && trailPlan.secondHit} label="Second Target" note={secondTargetNote} value={numberText(trailPlan.secondTrigger)} muted />
                {stopLossHit ? (
                  <TargetStage active label="Stop Loss" note={stopMove !== null ? `${numberText(stopMove)} pts` : ""} tone="red" value={numberText(stop)} />
                ) : (
                  <TargetStage active={trailPlan.secondHit} label="Trail Active" note={firstTrailLockNote(selectedStrategy.first_trail_lock_loss, selectedStrategy.first_trail_lock_loss_unit)} value="" muted />
                )}
              </div>
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-3">
              {stopLossHit ? (
                <>
                  <DashboardMetricBox label="Total Move" value={stopMove !== null ? `${numberText(stopMove)} pts` : "-"} note="Price movement from entry" tone="red" />
                  <DashboardMetricBox label="Exit Reason" value="Stop Loss Hit" note="Risk management triggered" tone="red" />
                  <DashboardMetricBox label="Trade Duration" value={tradeDuration} note="From entry to exit" />
                </>
              ) : (
                <>
                  <DashboardMetricBox label="Current Move" value={livePoints !== null ? `${numberText(livePoints)} pts` : "-"} note="Price movement from entry" tone={livePoints !== null && livePoints >= 0 ? "green" : livePoints !== null ? "red" : "plain"} />
                  <DashboardMetricBox label="First Target Hit?" value={trailPlan.firstHit ? "Yes" : "Waiting"} note={trailPlan.firstHit ? "Trail SL will activate" : "Trail SL not active yet"} tone={trailPlan.firstHit ? "green" : "plain"} />
                  <DashboardMetricBox label="Second Target Hit?" value={trailPlan.secondHit ? "Yes" : "Waiting"} note={trailPlan.secondHit ? `Following ${selectedStrategy.trail_timeframe} candle trail` : "Not reached yet"} tone={trailPlan.secondHit ? "green" : "amber"} />
                </>
              )}
            </div>
          </section>

        </div>

        <aside className="grid content-start gap-3">
          <section className="rounded-xl border border-slate-200/80 bg-white p-3 shadow-[0_10px_28px_rgba(15,23,42,0.06)]">
            <h2 className="text-lg font-semibold tracking-tight text-slate-950">Active Strategy</h2>
            <div className="mt-3 divide-y divide-slate-100">
              <StrategyDetail icon={<Activity size={18} />} label="Name" value={selectedStrategy.name || "-"} />
              <StrategyDetail icon={<Radio size={18} />} label="Symbol" value={selectedStrategy.symbol || "-"} />
              <StrategyDetail icon={<ArrowRightLeft size={18} />} label="Entry Pattern" value={patternText(selectedStrategy.entry_pattern)} />
              <StrategyDetail icon={<Clock3 size={18} />} label="Session" value={`${selectedStrategy.session_start} - ${selectedStrategy.session_end}`} />
              <StrategyDetail icon={<Database size={18} />} label="Range (IST)" value={`${selectedStrategy.range_start} - ${selectedStrategy.range_end}`} />
              <StrategyDetail icon={<Layers3 size={18} />} label="Lot Size" value={numberText(selectedStrategy.volume)} />
              <StrategyDetail icon={<Target size={18} />} label="Max Trades" value={String(selectedStrategy.max_trades_per_day || "-")} />
              <StrategyDetail icon={<Bot size={18} />} label="Auto Trade" value={selectedStrategy.live_trading_enabled ? "Enabled" : "Disabled"} />
            </div>
          </section>

          <StrategyList
            running={running}
            strategies={sortedStrategies}
            selectedId={selectedStrategy.id}
            onNavigateSettings={onNavigateSettings}
            onSelect={onSelect}
          />
        </aside>
      </div>
    </div>
  );
}

function LiveTradingChart({
  entry,
  firstTarget,
  secondTarget,
  signal,
  stopLoss,
  strategy,
}: {
  entry: number | null;
  firstTarget: number | null;
  secondTarget: number | null;
  signal: Signal;
  stopLoss: number | null;
  strategy: Strategy;
}) {
  const [feed, setFeed] = useState<ChartFeed | null>(null);
  const [error, setError] = useState("");
  const [crosshair, setCrosshair] = useState<{ index: number; x: number; y: number } | null>(null);

  useEffect(() => {
    if (!strategy.id || !strategy.symbol) return;
    let disposed = false;
    let timer = 0;
    const loadChart = async () => {
      try {
        const data = await apiJson<ChartFeed>(`/api/algo/chart?strategy_id=${encodeURIComponent(strategy.id)}&count=90`, { cache: "no-store" });
        if (!disposed) {
          setFeed(data);
          setError("");
        }
      } catch (chartError) {
        if (!disposed) setError(chartError instanceof Error ? chartError.message : "Live chart unavailable.");
      } finally {
        if (!disposed) timer = window.setTimeout(loadChart, 2000);
      }
    };
    void loadChart();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [strategy.id, strategy.symbol]);

  const candles = (feed?.candles || []).slice(-72);
  const buyTrigger = toNumber(signal.buy_trigger);
  const sellTrigger = toNumber(signal.sell_trigger);
  const lineCandidates = [buyTrigger, sellTrigger, entry, stopLoss, firstTarget, secondTarget, toNumber(feed?.live_price)].filter(
    (value): value is number => value !== null && Number.isFinite(value),
  );
  const candleLows = candles.map((candle) => candle.low);
  const candleHighs = candles.map((candle) => candle.high);
  const rawMin = Math.min(...candleLows, ...lineCandidates);
  const rawMax = Math.max(...candleHighs, ...lineCandidates);
  const validRange = Number.isFinite(rawMin) && Number.isFinite(rawMax);
  const spread = validRange ? Math.max(rawMax - rawMin, Math.abs(rawMax || 1) * 0.0005) : 1;
  const minPrice = validRange ? rawMin - spread * 0.08 : 0;
  const maxPrice = validRange ? rawMax + spread * 0.08 : 1;
  const width = 1100;
  const height = 440;
  const plotLeft = 18;
  const plotRight = 986;
  const plotTop = 24;
  const plotBottom = 382;
  const plotWidth = plotRight - plotLeft;
  const plotHeight = plotBottom - plotTop;
  const step = candles.length ? plotWidth / candles.length : plotWidth;
  const bodyWidth = Math.max(3, Math.min(step * 0.62, 11));
  const yFor = (price: number) => plotTop + ((maxPrice - price) / (maxPrice - minPrice)) * plotHeight;
  const priceForY = (y: number) => maxPrice - ((y - plotTop) / plotHeight) * (maxPrice - minPrice);
  const timeText = (value?: string) =>
    value
      ? new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" }).format(new Date(value))
      : "--:--";
  const priceText = (value: number) => value.toLocaleString("en-IN", { maximumFractionDigits: 3 });
  const triggerIndex = signal.trigger_candle_time
    ? candles.reduce((best, candle, index) =>
        Math.abs(new Date(candle.time).getTime() - new Date(signal.trigger_candle_time || "").getTime()) <
        Math.abs(new Date(candles[best]?.time || 0).getTime() - new Date(signal.trigger_candle_time || "").getTime())
          ? index
          : best, 0)
    : -1;
  const levels = [
    { label: "BUY", value: buyTrigger, color: "#6ee7b7", dash: "7 6" },
    { label: "SELL", value: sellTrigger, color: "#fda4af", dash: "7 6" },
    { label: "ENTRY", value: entry, color: "#67e8f9", dash: "" },
    { label: "SL", value: stopLoss, color: "#fb7185", dash: "3 5" },
    { label: "T1", value: firstTarget, color: "#86efac", dash: "3 5" },
    { label: "T2", value: secondTarget, color: "#4ade80", dash: "3 5" },
  ].filter((level): level is { label: string; value: number; color: string; dash: string } => level.value !== null && Number.isFinite(level.value));
  const hoveredCandle = crosshair ? candles[crosshair.index] : null;

  const updateCrosshair = (event: React.MouseEvent<SVGSVGElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - bounds.left) / bounds.width) * width;
    const y = ((event.clientY - bounds.top) / bounds.height) * height;
    if (x < plotLeft || x > plotRight || y < plotTop || y > plotBottom || !candles.length) {
      setCrosshair(null);
      return;
    }
    const index = Math.max(0, Math.min(candles.length - 1, Math.floor((x - plotLeft) / step)));
    setCrosshair({ index, x: plotLeft + (index + 0.5) * step, y });
  };

  return (
    <section className="order-4 overflow-hidden rounded-xl border border-slate-800 bg-[#050809] shadow-[0_18px_45px_rgba(2,8,23,0.22)]">
      <div className="flex flex-wrap items-center gap-3 border-b border-white/10 px-4 py-3 text-white">
        <div>
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-emerald-400 shadow-[0_0_12px_#34d399]" />
            <h2 className="font-semibold tracking-tight">Live Market Chart</h2>
          </div>
          <div className="mt-0.5 text-xs text-slate-400">{feed?.symbol || strategy.symbol} · {feed?.timeframe || strategy.timeframe} · IST</div>
        </div>
        <div className="ml-auto text-right">
          <div className="font-mono text-lg font-semibold text-slate-100">{feed ? priceText(feed.live_price) : "Connecting..."}</div>
          <div className="text-[11px] text-slate-500">{error || `Live · ${timeText(feed?.server_time)}`}</div>
        </div>
      </div>

      <div className="overflow-x-auto">
        {candles.length ? (
          <svg
            aria-label={`${strategy.symbol} live candlestick chart`}
            className="block min-w-[760px] w-full cursor-crosshair"
            onMouseLeave={() => setCrosshair(null)}
            onMouseMove={updateCrosshair}
            role="img"
            viewBox={`0 0 ${width} ${height}`}
          >
            <defs>
              <filter id="live-candle-glow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="2.4" result="blur" />
                <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
              </filter>
              <linearGradient id="chart-fade" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0" stopColor="#0c1817" />
                <stop offset="1" stopColor="#030506" />
              </linearGradient>
            </defs>
            <rect width={width} height={height} fill="url(#chart-fade)" />

            {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
              const y = plotTop + plotHeight * fraction;
              return <line key={`grid-${fraction}`} x1={plotLeft} x2={plotRight} y1={y} y2={y} stroke="#1e3a38" strokeOpacity="0.55" strokeWidth="1" />;
            })}

            {levels.map((level) => {
              const y = yFor(level.value);
              return (
                <g key={`${level.label}-${level.value}`}>
                  <line x1={plotLeft} x2={plotRight} y1={y} y2={y} stroke={level.color} strokeDasharray={level.dash} strokeOpacity="0.7" strokeWidth="1.2" />
                  <rect x={plotRight + 5} y={y - 10} width="106" height="20" rx="4" fill="#0a0f11" stroke={level.color} strokeOpacity="0.65" />
                  <text x={plotRight + 11} y={y + 4} fill={level.color} fontFamily="ui-monospace, monospace" fontSize="11" fontWeight="700">{level.label} {priceText(level.value)}</text>
                </g>
              );
            })}

            {triggerIndex >= 0 && (
              <g>
                <line x1={plotLeft + (triggerIndex + 0.5) * step} x2={plotLeft + (triggerIndex + 0.5) * step} y1={plotTop} y2={plotBottom} stroke="#fbbf24" strokeDasharray="4 7" strokeOpacity="0.8" />
                <rect x={Math.min(plotLeft + (triggerIndex + 0.5) * step + 6, plotRight - 92)} y={plotTop + 5} width="88" height="22" rx="5" fill="#2a1d04" stroke="#fbbf24" strokeOpacity="0.8" />
                <text x={Math.min(plotLeft + (triggerIndex + 0.5) * step + 14, plotRight - 84)} y={plotTop + 20} fill="#fcd34d" fontSize="11" fontWeight="700">{signal.side || "SIGNAL"} CROSS</text>
              </g>
            )}

            {candles.map((candle, index) => {
              const x = plotLeft + (index + 0.5) * step;
              const rising = candle.close >= candle.open;
              const color = rising ? "#72e6b1" : "#ff8f9d";
              const top = yFor(Math.max(candle.open, candle.close));
              const bottom = yFor(Math.min(candle.open, candle.close));
              const candleHeight = Math.max(bottom - top, 1.5);
              const current = index === candles.length - 1;
              return (
                <g key={`${candle.time}-${index}`} filter={current ? "url(#live-candle-glow)" : undefined}>
                  <title>{`${timeText(candle.time)}  O ${priceText(candle.open)}  H ${priceText(candle.high)}  L ${priceText(candle.low)}  C ${priceText(candle.close)}`}</title>
                  <line x1={x} x2={x} y1={yFor(candle.high)} y2={yFor(candle.low)} stroke={color} strokeOpacity="0.9" strokeWidth="1.25" />
                  <rect x={x - bodyWidth / 2} y={top} width={bodyWidth} height={candleHeight} rx="1" fill={color} fillOpacity={rising ? "0.82" : "0.88"} stroke={color} />
                </g>
              );
            })}

            {crosshair && hoveredCandle && (
              <g pointerEvents="none">
                <line x1={crosshair.x} x2={crosshair.x} y1={plotTop} y2={plotBottom} stroke="#cbd5e1" strokeDasharray="4 4" strokeOpacity="0.72" />
                <line x1={plotLeft} x2={plotRight} y1={crosshair.y} y2={crosshair.y} stroke="#cbd5e1" strokeDasharray="4 4" strokeOpacity="0.72" />
                <circle cx={crosshair.x} cy={yFor(hoveredCandle.close)} r="4" fill="#f8fafc" stroke={hoveredCandle.close >= hoveredCandle.open ? "#72e6b1" : "#ff8f9d"} strokeWidth="2" />
                <rect x={plotRight + 5} y={Math.max(plotTop, Math.min(crosshair.y - 10, plotBottom - 20))} width="106" height="20" rx="4" fill="#e2e8f0" />
                <text x={plotRight + 11} y={Math.max(plotTop + 14, Math.min(crosshair.y + 4, plotBottom - 6))} fill="#0f172a" fontFamily="ui-monospace, monospace" fontSize="11" fontWeight="700">{priceText(priceForY(crosshair.y))}</text>
                <rect x={Math.max(plotLeft, Math.min(crosshair.x - 38, plotRight - 76))} y={plotBottom + 8} width="76" height="20" rx="4" fill="#e2e8f0" />
                <text x={Math.max(plotLeft + 38, Math.min(crosshair.x, plotRight - 38))} y={plotBottom + 22} textAnchor="middle" fill="#0f172a" fontSize="11" fontWeight="700">{timeText(hoveredCandle.time)}</text>
                <g transform={`translate(${plotLeft + 10} ${plotTop + 10})`}>
                  <rect width="260" height="48" rx="5" fill="#071110" fillOpacity="0.92" stroke="#28534e" />
                  <text x="10" y="18" fill="#94a3b8" fontFamily="ui-monospace, monospace" fontSize="11">{timeText(hoveredCandle.time)}  O {priceText(hoveredCandle.open)}  H {priceText(hoveredCandle.high)}</text>
                  <text x="10" y="36" fill={hoveredCandle.close >= hoveredCandle.open ? "#72e6b1" : "#ff8f9d"} fontFamily="ui-monospace, monospace" fontSize="11">L {priceText(hoveredCandle.low)}  C {priceText(hoveredCandle.close)}  Vol {hoveredCandle.tick_volume.toLocaleString("en-IN")}</text>
                </g>
              </g>
            )}

            {feed && (
              <g>
                <line x1={plotLeft} x2={plotRight} y1={yFor(feed.live_price)} y2={yFor(feed.live_price)} stroke="#f8fafc" strokeDasharray="2 5" strokeOpacity="0.5" />
                <circle cx={plotRight} cy={yFor(feed.live_price)} r="3.5" fill="#f8fafc" filter="url(#live-candle-glow)" />
              </g>
            )}

            <line x1={plotLeft} x2={plotRight} y1={plotBottom} y2={plotBottom} stroke="#334155" strokeOpacity="0.55" />
            {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
              const index = Math.min(candles.length - 1, Math.round((candles.length - 1) * fraction));
              const x = plotLeft + (index + 0.5) * step;
              return <text key={fraction} x={x} y={plotBottom + 28} textAnchor="middle" fill="#64748b" fontSize="11">{timeText(candles[index]?.time)}</text>;
            })}
            <text x={plotRight} y={height - 14} textAnchor="end" fill="#475569" fontSize="10">Hover candle for OHLC · updates every 2s</text>
          </svg>
        ) : (
          <div className="grid min-h-[360px] place-items-center text-sm text-slate-500">{error || "Loading live candles..."}</div>
        )}
      </div>
    </section>
  );
}

function TradeLevelCard({
  enabled,
  icon,
  label,
  level,
  locked,
  note,
  onUpdate,
  order,
  plan,
  tone,
  triggered,
}: {
  enabled: boolean;
  icon: ReactNode;
  label: string;
  level: number | null;
  locked: boolean;
  note: string;
  onUpdate: (price: number) => Promise<void>;
  order?: BrokerOrder;
  plan: { stop: number | null; first: number | null; lock: number | null; second: number | null };
  tone: "green" | "red";
  triggered: boolean;
}) {
  const green = tone === "green";
  const [draft, setDraft] = useState(level === null ? "" : String(level));
  const [saving, setSaving] = useState(false);
  const draftNumber = toNumber(draft);
  useEffect(() => setDraft(level === null ? "" : String(level)), [level]);
  const colors = green
    ? "border-emerald-200 bg-gradient-to-br from-white to-emerald-50/70 text-emerald-700"
    : "border-rose-200 bg-gradient-to-br from-white to-rose-50/70 text-rose-700";

  return (
    <div className={`rounded-xl border p-4 ${enabled ? colors : "border-slate-200 bg-slate-50 text-slate-400"}`}>
      <div className="flex items-center gap-3">
        <span className={`grid h-11 w-11 place-items-center rounded-full ${enabled ? (green ? "bg-emerald-100" : "bg-rose-100") : "bg-slate-200"}`}>
          {icon}
        </span>
        <div className="min-w-0">
          <div className="text-sm font-semibold uppercase tracking-wide">{label}</div>
          <div className="mt-1 text-3xl font-bold tracking-tight text-slate-950">{enabled ? numberText(level) : "Disabled"}</div>
        </div>
        <span className={`ml-auto rounded-full px-2.5 py-1 text-xs font-semibold ${locked ? "bg-slate-700 text-white" : triggered ? (green ? "bg-emerald-600 text-white" : "bg-rose-600 text-white") : enabled ? "bg-white/90 text-slate-600 shadow-sm" : "bg-slate-200 text-slate-500"}`}>
          {locked ? (order ? "CANCELLING" : "OCO CANCELLED") : order ? "ORDER PLACED" : triggered ? "TRIGGERED" : enabled ? level === null ? "FORMING" : "WAITING" : "OFF"}
        </span>
      </div>
      {enabled && level !== null && !locked && (
        <div className={`mt-4 rounded-xl border p-3 ${green ? "border-emerald-100 bg-white/85" : "border-rose-100 bg-white/85"}`}>
          <div className="text-xs font-medium text-slate-600">Edit {green ? "Buy" : "Sell"} Trigger Price</div>
          <div className="mt-2 flex gap-2">
            <input
              className="input min-w-0 flex-1 text-lg font-semibold"
              inputMode="decimal"
              onChange={(event) => setDraft(event.target.value)}
              step="0.01"
              type="number"
              value={draft}
            />
            <button
              className={`min-w-24 rounded-lg px-4 text-sm font-semibold text-white transition disabled:cursor-not-allowed disabled:opacity-50 ${green ? "bg-emerald-600 hover:bg-emerald-700" : "bg-rose-600 hover:bg-rose-700"}`}
              disabled={saving || draftNumber === null || draftNumber <= 0 || draftNumber === level}
              onClick={async () => {
                if (draftNumber === null) return;
                setSaving(true);
                try {
                  await onUpdate(draftNumber);
                } finally {
                  setSaving(false);
                }
              }}
              type="button"
            >
              {saving ? "Updating..." : "Update MT5"}
            </button>
          </div>
        </div>
      )}
      <div className={`mt-4 rounded-lg border px-3 py-2 text-sm ${enabled ? (green ? "border-emerald-100 bg-emerald-50/80" : "border-rose-100 bg-rose-50/80") : "border-slate-200 bg-white"}`}>
        {enabled ? note : note || `${label.split(" ")[0]} entries are disabled in this strategy.`}
      </div>
      {enabled && level !== null && (
        <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-600 sm:grid-cols-4">
          <div className="rounded-lg bg-white/75 p-2"><span className="block text-slate-400">Initial SL</span><strong className="text-slate-800">{numberText(plan.stop)}</strong></div>
          <div className="rounded-lg bg-white/75 p-2"><span className="block text-slate-400">First Target</span><strong className="text-slate-800">{numberText(plan.first)}</strong></div>
          <div className="rounded-lg bg-white/75 p-2"><span className="block text-slate-400">SL Lock</span><strong className="text-slate-800">{numberText(plan.lock)}</strong></div>
          <div className="rounded-lg bg-white/75 p-2"><span className="block text-slate-400">Second Target</span><strong className="text-slate-800">{numberText(plan.second)}</strong></div>
        </div>
      )}
    </div>
  );
}

function SignalStat({
  compact = false,
  indent = false,
  label,
  note,
  tone = "plain",
  value,
}: {
  compact?: boolean;
  indent?: boolean;
  label: string;
  note?: string;
  tone?: "plain" | "green" | "red";
  value: ReactNode;
}) {
  const toneClass = {
    plain: "text-slate-950",
    green: "text-emerald-700",
    red: "text-rose-600",
  }[tone];
  return (
    <div className={`flex flex-col justify-center border-t border-slate-200 p-3 md:border-l md:border-t-0 ${indent ? "md:pl-8" : ""} ${compact ? "min-h-[82px]" : "min-h-[92px]"}`}>
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`mt-1.5 break-words text-xl font-medium tracking-tight ${toneClass}`}>{value}</div>
      {note && <div className="mt-1 text-xs text-slate-500">{note}</div>}
    </div>
  );
}

function TargetStage({
  active,
  label,
  muted = false,
  note,
  tone = "green",
  value,
}: {
  active: boolean;
  label: string;
  muted?: boolean;
  note?: string;
  tone?: "green" | "red";
  value: ReactNode;
}) {
  const activeClass =
    tone === "red"
      ? "border-rose-600 bg-rose-600 text-white shadow-[0_0_0_4px_rgba(244,63,94,0.14)]"
      : "border-emerald-600 bg-emerald-600 text-white shadow-[0_0_0_4px_rgba(16,185,129,0.14)]";
  return (
    <div className="relative min-h-[78px] pb-7">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-base font-medium text-slate-950">{value || "\u00a0"}</div>
      {note && <div className="text-xs text-slate-500">{note}</div>}
      <span
        className={`absolute bottom-0 flex h-6 w-6 items-center justify-center rounded-full border-2 ${
          active ? activeClass : muted ? "border-slate-300 bg-white text-slate-300" : "border-slate-300 bg-white text-slate-300"
        }`}
      >
        {active && (tone === "red" ? <XCircle size={15} /> : <CheckCircle2 size={15} />)}
      </span>
    </div>
  );
}

function DashboardMetricBox({
  label,
  note,
  tone = "plain",
  value,
}: {
  label: string;
  note: string;
  tone?: "plain" | "green" | "red" | "amber";
  value: ReactNode;
}) {
  const toneClass = {
    plain: "text-slate-950",
    green: "text-emerald-700",
    red: "text-rose-600",
    amber: "text-amber-600",
  }[tone];
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-[0_6px_18px_rgba(15,23,42,0.035)]">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`mt-1.5 break-words text-lg font-medium ${toneClass}`}>{value}</div>
      <div className="mt-1.5 text-xs text-slate-500">{note}</div>
    </div>
  );
}

function StrategyDetail({ icon, label, value }: { icon: ReactNode; label: string; value: ReactNode }) {
  return (
    <div className="grid grid-cols-[26px_minmax(0,1fr)] gap-2 py-2.5">
      <div className="pt-1 text-slate-700 [&>svg]:h-4 [&>svg]:w-4">{icon}</div>
      <div>
        <div className="text-xs text-slate-500">{label}</div>
        <div className="mt-0.5 break-words text-sm font-medium text-slate-950">{value}</div>
      </div>
    </div>
  );
}

function SettingsView({
  form,
  saving,
  symbols,
  onField,
  onReset,
  onSave,
  onSymbolSearch,
}: {
  form: Strategy;
  saving: boolean;
  symbols: string[];
  onField: <K extends keyof Strategy>(key: K, value: Strategy[K]) => void;
  onReset: () => void;
  onSave: (event: FormEvent<HTMLFormElement>) => void;
  onSymbolSearch: (query?: string) => Promise<void>;
}) {
  return (
    <div className="algo-settings-card mx-auto mt-3 max-w-[1220px] rounded-xl border border-white/80 bg-white/75 p-2 shadow-[0_10px_30px_rgba(39,76,119,0.08)] backdrop-blur-xl sm:p-3">
      <header className="flex flex-col gap-2 px-1.5 py-1.5 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-600 to-emerald-400 text-white shadow-[0_5px_14px_rgba(5,150,105,0.18)]">
            <Settings size={18} />
          </div>
          <div>
            <h1 className="text-[clamp(1.15rem,1.8vw,1.35rem)] font-bold tracking-tight text-[#14213d]">Algo Settings</h1>
            <p className="text-xs text-slate-500">Only the settings used by this strategy.</p>
          </div>
        </div>
        <div className="inline-flex w-fit items-center gap-1.5 rounded-lg border border-slate-200 bg-white/70 px-2.5 py-1.5 text-xs text-slate-700 shadow-sm">
          <Info size={14} /> Tip: All times are in IST
        </div>
      </header>

      <form className="algo-settings-panel mt-2 rounded-xl border border-slate-200/80 bg-white/80 p-3 shadow-[0_6px_18px_rgba(15,23,42,0.035)] lg:p-4" onSubmit={onSave}>
        <div className="grid gap-x-4 gap-y-3 md:grid-cols-2 xl:grid-cols-3">
            <Field icon={<ChartNoAxesCombined />} label="Script / Symbol">
              <SymbolPicker
                onChange={(symbol) => onField("symbol", symbol)}
                onSearch={onSymbolSearch}
                symbols={symbols}
                value={form.symbol}
              />
            </Field>
            <Field icon={<Clock3 />} label="Entry Candle Timeframe">
              <select className="input" value={form.timeframe} onChange={(event) => onField("timeframe", event.target.value)}>
                <option value="">Select timeframe</option>
                {timeframes.map((timeframe) => (
                  <option key={timeframe}>{timeframe}</option>
                ))}
              </select>
            </Field>
            <Field icon={<Clock3 />} label="Second Target Exit Trail Candle Timeframe">
              <select className="input" value={form.trail_timeframe} onChange={(event) => onField("trail_timeframe", event.target.value)}>
                <option value="">Select timeframe</option>
                {timeframes.map((timeframe) => (
                  <option key={timeframe}>{timeframe}</option>
                ))}
              </select>
            </Field>
            <Field icon={<ArrowRightLeft />} label="Entry Pattern">
              <select className="input" value={form.entry_pattern} onChange={(event) => onField("entry_pattern", event.target.value)}>
                <option value="">Select pattern</option>
                {patterns.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field icon={<Layers3 />} label="Qty">
              <NumericInput className="input" min={0.01} step={0.01} value={form.volume} onChange={(value) => onField("volume", value)} />
            </Field>
            <Field icon={<Clock3 />} label="Range Start IST">
              <input className="input" type="time" value={form.range_start} onChange={(event) => onField("range_start", event.target.value)} />
            </Field>
            <Field icon={<Clock3 />} label="Range End IST">
              <input className="input" type="time" value={form.range_end} onChange={(event) => onField("range_end", event.target.value)} />
            </Field>
            <Field icon={<Clock3 />} label="Session Start IST">
              <input className="input" type="time" value={form.session_start} onChange={(event) => onField("session_start", event.target.value)} />
            </Field>
            <Field icon={<Clock3 />} label="Last Entry Time IST">
              <input className="input" type="time" value={form.entry_cutoff} onChange={(event) => onField("entry_cutoff", event.target.value)} />
            </Field>
            <Field icon={<Clock3 />} label="Force Exit Time IST">
              <input className="input" type="time" value={form.session_end} onChange={(event) => onField("session_end", event.target.value)} />
            </Field>
            <Field icon={<Percent />} label="Entry Buffer %">
              <div className="relative">
                <NumericInput className="input pr-12" min={0} step={0.01} value={form.entry_buffer_pct} onChange={(value) => onField("entry_buffer_pct", value)} />
                <Percent className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-emerald-600" size={16} />
              </div>
            </Field>
            <Field icon={<ShieldCheck />} label="Stop Loss">
              <RiskInput
                unit={form.stop_points_unit}
                value={form.stop_points}
                onChange={(value) => onField("stop_points", value)}
                onUnitChange={(unit) => onField("stop_points_unit", unit)}
              />
            </Field>
            <Field accent icon={<Target />} label="First Target">
              <RiskInput
                unit={form.first_trail_profit_unit}
                value={form.first_trail_profit}
                onChange={(value) => onField("first_trail_profit", value)}
                onUnitChange={(unit) => onField("first_trail_profit_unit", unit)}
              />
            </Field>
            <Field accent icon={<LockKeyhole />} label="First SL Lock">
              <RiskInput
                unit={form.first_trail_lock_loss_unit}
                value={form.first_trail_lock_loss}
                onChange={(value) => onField("first_trail_lock_loss", value)}
                onUnitChange={(unit) => onField("first_trail_lock_loss_unit", unit)}
              />
            </Field>
            <Field accent icon={<Target />} label="Second Target">
              <RiskInput
                unit={form.second_trail_profit_unit}
                value={form.second_trail_profit}
                onChange={(value) => onField("second_trail_profit", value)}
                onUnitChange={(unit) => onField("second_trail_profit_unit", unit)}
              />
            </Field>
            <Field icon={<Bot />} label="Live Trading">
              <label className={`flex min-h-10 cursor-pointer items-center justify-between rounded-lg border px-3 py-2 transition ${form.live_trading_enabled ? "border-emerald-400 bg-emerald-50 text-emerald-900" : "border-slate-300 bg-white text-slate-700"}`}>
                <span>
                  <span className="block text-sm font-semibold">{form.live_trading_enabled ? "LIVE trading enabled" : "Live trading disabled"}</span>
                  <span className="block text-xs text-slate-500">Orders follow the execution mode selected below.</span>
                </span>
                <input
                  checked={form.live_trading_enabled}
                  className="peer sr-only"
                  onChange={(event) => onField("live_trading_enabled", event.target.checked)}
                  type="checkbox"
                />
                <span className={`relative h-6 w-11 shrink-0 rounded-full transition ${form.live_trading_enabled ? "bg-emerald-600" : "bg-slate-300"}`}>
                  <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition ${form.live_trading_enabled ? "left-[22px]" : "left-0.5"}`} />
                </span>
              </label>
            </Field>
            <div className="md:col-span-2 xl:col-span-3 px-0.5">
              <div className="flex min-h-4 items-center gap-2 text-[13px] font-medium text-[#14213d]">
                <Bot className="text-emerald-600" size={16} /> Trade Execution Mode
              </div>
              <div className="mt-1.5 grid gap-2 lg:grid-cols-3" role="radiogroup" aria-label="Trade execution mode">
                {[
                  {
                    value: "PENDING_BEFORE_SIGNAL",
                    title: "Pre-Signal Pending Orders",
                    note: "Start before the signal: BUY STOP and SELL STOP are placed after the range.",
                  },
                  {
                    value: "MARKET_AFTER_SIGNAL",
                    title: "Late Start Market Entry",
                    note: "Start after a valid signal: enter its side immediately at the current market price.",
                  },
                  {
                    value: "REARM_PENDING",
                    title: "Safe Pending Re-entry",
                    note: "After a late start, re-place only the crossed side at its original trigger; blocked if T1 or SL was reached today.",
                  },
                ].map((mode) => {
                  const selected = form.execution_mode === mode.value;
                  return (
                    <label key={mode.value} className={`cursor-pointer rounded-lg border p-3 transition ${selected ? "border-emerald-500 bg-emerald-50 shadow-sm" : "border-slate-200 bg-white hover:border-emerald-300"}`}>
                      <span className="flex items-start gap-2.5">
                        <input
                          checked={selected}
                          className="mt-0.5 h-4 w-4 accent-emerald-600"
                          name="execution_mode"
                          onChange={() => onField("execution_mode", mode.value as Strategy["execution_mode"])}
                          type="radio"
                          value={mode.value}
                        />
                        <span>
                          <span className="block text-sm font-semibold text-slate-900">{mode.title}</span>
                          <span className="mt-1 block text-xs leading-5 text-slate-600">{mode.note}</span>
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>
        </div>

        <div className="mt-4 flex flex-col gap-3 border-t border-slate-100 pt-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-1 items-start gap-2.5 rounded-lg border border-blue-200 bg-blue-50/75 px-3 py-2 text-blue-900">
            <Info className="mt-0.5 shrink-0 text-blue-600" size={16} />
            <div>
              <div className="text-xs font-semibold">Important</div>
              <div className="mt-0.5 text-xs text-blue-700">
                {form.live_trading_enabled
                  ? "LIVE mode can place real MT5 pending or market orders based on the selected execution mode."
                  : "Live mode is OFF. No broker order will be placed until you enable it and save settings."}
              </div>
            </div>
          </div>
          <div className="flex justify-end gap-2.5">
            <button
              className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50"
              onClick={onReset}
              type="button"
            >
              <RotateCcw size={16} /> Reset
            </button>
            <button
              className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-gradient-to-r from-emerald-700 to-emerald-500 px-4 py-2 text-sm font-semibold text-white shadow-[0_5px_14px_rgba(5,150,105,0.18)] transition hover:brightness-105"
              disabled={saving}
              type="submit"
            >
              <Save size={16} /> {saving ? "Saving..." : "Save Settings"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

function StrategyList({
  onNavigateSettings,
  running,
  strategies,
  selectedId,
  onSelect,
}: {
  onNavigateSettings: () => void;
  running: boolean;
  strategies: Strategy[];
  selectedId?: string;
  onSelect: (strategy: Strategy) => void;
}) {
  return (
    <section className="rounded-xl border border-slate-200/80 bg-white p-3 shadow-[0_10px_28px_rgba(15,23,42,0.06)]">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold tracking-tight text-slate-950">Strategies</h2>
        <button className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50" onClick={onNavigateSettings} type="button">
          <Settings size={14} /> Settings
        </button>
      </div>
      <div className="mt-3 grid max-h-[145px] gap-2 overflow-auto pr-1">
        {strategies.length ? (
          strategies.map((strategy) => (
            <button
              className={`rounded-xl border p-2.5 text-left text-sm transition hover:bg-emerald-50/40 ${
                selectedId === strategy.id ? "border-emerald-300 bg-emerald-50/50 shadow-[0_8px_22px_rgba(16,185,129,0.08)]" : "border-slate-200 bg-white"
              }`}
              key={strategy.id}
              onClick={() => onSelect(strategy)}
              type="button"
            >
              <div className="flex items-center gap-2">
                <span className="font-medium text-slate-950">{strategy.name}</span>
                {selectedId === strategy.id && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-emerald-600" />}
              </div>
              <div className="mt-2 flex items-center gap-2 text-xs text-emerald-700">
                <span className={`h-2 w-2 rounded-full ${running && selectedId === strategy.id ? "bg-emerald-500" : "bg-slate-300"}`} />
                {running && selectedId === strategy.id ? "Running" : `${strategy.symbol} | ${strategy.timeframe}`}
              </div>
            </button>
          ))
        ) : (
          <div className="rounded-xl border border-dashed border-slate-300 p-3 text-sm text-slate-500">No strategy saved.</div>
        )}
      </div>
      <button
        className="mt-2 inline-flex min-h-8 w-full items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white text-sm font-medium text-slate-800 shadow-sm hover:bg-slate-50"
        onClick={onNavigateSettings}
        type="button"
      >
        + Add New Strategy
      </button>
    </section>
  );
}

function SymbolPicker({
  onChange,
  onSearch,
  symbols,
  value,
}: {
  onChange: (symbol: string) => void;
  onSearch: (query?: string) => Promise<void>;
  symbols: string[];
  value: string;
}) {
  const [query, setQuery] = useState(value);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setQuery(value);
  }, [value]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void onSearch(query);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query, onSearch]);

  const filteredSymbols = useMemo(() => {
    const search = query.trim().toUpperCase();
    const matches = search ? symbols.filter((symbol) => symbol.toUpperCase().includes(search)) : symbols;
    return matches.slice(0, 30);
  }, [query, symbols]);

  function updateQuery(nextQuery: string) {
    setQuery(nextQuery);
    onChange(nextQuery);
    setOpen(true);
  }

  function chooseSymbol(symbol: string) {
    setQuery(symbol);
    onChange(symbol);
    setOpen(false);
  }

  return (
    <div className="relative">
      <input
        className="input pr-12"
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onChange={(event) => updateQuery(event.target.value)}
        onFocus={() => {
          setOpen(true);
          void onSearch(query);
        }}
        placeholder="Search MT5 symbol"
        value={query}
      />
      <ChevronDown className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-600" size={16} />
      {open && (
        <div className="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-neutral-200 bg-white shadow-lg">
          {filteredSymbols.length ? (
            filteredSymbols.map((symbol) => (
              <button
                className="block w-full px-3 py-2 text-left text-sm hover:bg-[#f5f7f2]"
                key={symbol}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => chooseSymbol(symbol)}
                type="button"
              >
                {symbol}
              </button>
            ))
          ) : (
            <div className="px-3 py-3 text-sm text-neutral-500">No selectable symbols found.</div>
          )}
        </div>
      )}
    </div>
  );
}

function Field({ accent = false, children, icon, label }: { accent?: boolean; children: ReactNode; icon?: ReactNode; label: string }) {
  return (
    <label className={`grid gap-1.5 text-[13px] ${accent ? "rounded-lg border border-emerald-100 bg-emerald-50/65 p-2" : "px-0.5"}`}>
      <span className="flex min-h-4 items-center gap-2 font-medium text-[#14213d]">
        {icon && <span className="flex text-emerald-600 [&>svg]:h-4 [&>svg]:w-4">{icon}</span>}
        {label}
      </span>
      {children}
    </label>
  );
}

function RiskInput({
  onChange,
  onUnitChange,
  unit,
  value,
}: {
  onChange: (value: number) => void;
  onUnitChange: (unit: string) => void;
  unit: string;
  value: number;
}) {
  return (
    <div className="flex">
      <NumericInput
        className="input rounded-r-none"
        min={0}
        onChange={onChange}
        step={0.01}
        value={value}
      />
      <select
        aria-label="Distance unit"
        className="risk-unit-select min-w-16 rounded-r-lg border border-l-0 border-slate-300 bg-white px-2 text-xs font-medium text-slate-700 outline-none focus:border-emerald-600"
        onChange={(event) => onUnitChange(event.target.value)}
        value={unit || "POINTS"}
      >
        <option value="POINTS">Pts</option>
        <option value="PERCENT">%</option>
      </select>
    </div>
  );
}

function NumericInput({
  className,
  min,
  onChange,
  step,
  value,
}: {
  className?: string;
  min?: number;
  onChange: (value: number) => void;
  step?: number;
  value: number;
}) {
  const [draft, setDraft] = useState(String(value));

  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  return (
    <input
      className={className}
      inputMode="decimal"
      min={min}
      onBlur={() => {
        if (draft.trim() === "") setDraft(String(value));
      }}
      onChange={(event) => {
        const next = event.target.value;
        setDraft(next);
        if (next.trim() === "") return;
        const number = Number(next);
        if (Number.isFinite(number)) onChange(number);
      }}
      onFocus={(event) => event.currentTarget.select()}
      step={step}
      type="number"
      value={draft}
    />
  );
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-[#f8faf8] p-2">
      <div className="text-[11px] uppercase text-neutral-500">{label}</div>
      <div className="mt-1 break-words text-neutral-950">{value}</div>
    </div>
  );
}

function BigMetric({
  icon,
  label,
  note,
  tone = "plain",
  value,
}: {
  icon?: ReactNode;
  label: string;
  note?: string;
  tone?: "plain" | "green" | "red" | "blush";
  value: ReactNode;
}) {
  const toneClass = {
    plain: "border-neutral-200 bg-[#f8faf8]",
    green: "border-emerald-200 bg-emerald-50",
    red: "border-rose-200 bg-rose-50",
    blush: "border-rose-200 bg-rose-50",
  }[tone];
  return (
    <div className={`rounded-lg border p-3 ${toneClass}`}>
      <div className="flex items-center gap-1 text-[11px] uppercase text-neutral-500">
        {icon}
        <span>{label}</span>
      </div>
      <div className="mt-2 break-words text-xl text-neutral-950">{value}</div>
      {note && <div className="mt-1 text-xs text-neutral-500">{note}</div>}
    </div>
  );
}

function GlassMetric({
  icon,
  label,
  note,
  tone = "plain",
  value,
}: {
  icon?: ReactNode;
  label: string;
  note?: string;
  tone?: "plain" | "green" | "red" | "blush";
  value: ReactNode;
}) {
  const toneClass = {
    plain: "border-white/70 bg-white/58",
    green: "border-emerald-100 bg-emerald-50/70",
    red: "border-rose-100 bg-rose-50/75",
    blush: "border-rose-100 bg-rose-50/75",
  }[tone];
  return (
    <div className={`min-h-[116px] cursor-default select-none rounded-[17px] border p-3 shadow-[8px_12px_28px_rgba(0,0,0,0.14)] backdrop-blur-md transition-transform duration-300 hover:scale-[1.02] active:scale-[0.98] ${toneClass}`}>
      <div className="flex items-center gap-2 text-[11px] uppercase text-neutral-500">
        {icon}
        <span>{label}</span>
      </div>
      <div className="mt-2 break-words text-[clamp(1.15rem,2vw,1.55rem)] leading-tight text-neutral-950">{value}</div>
      {note && <div className="mt-2 line-clamp-2 text-xs leading-snug text-neutral-600">{note}</div>}
    </div>
  );
}

function PlanMetric({ label, note, value }: { label: string; note: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-[#f8faf8] p-3">
      <div className="text-[11px] uppercase text-neutral-500">{label}</div>
      <div className="mt-2 break-words text-lg text-neutral-950">{value}</div>
      <div className="mt-1 text-xs text-neutral-500">{note}</div>
    </div>
  );
}

function StatusBadge({ side, status }: { side: string; status: string }) {
  return (
    <span
      className={`rounded-lg px-2 py-1 text-xs ${
        side === "BUY"
          ? "bg-emerald-100 text-emerald-700"
          : side === "SELL"
            ? "bg-rose-100 text-rose-700"
            : "bg-neutral-100 text-neutral-700"
      }`}
    >
      {status}
    </span>
  );
}

function LogPanel({ title, rows }: { title: string; rows: LogRow[] }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-3 shadow-[0_8px_22px_rgba(15,23,42,0.04)]">
      <h2 className="text-base font-semibold text-slate-950">{title}</h2>
      <div className="mt-2 max-h-[170px] overflow-auto">
        <table className="w-full min-w-[620px] border-collapse text-xs">
          <thead>
            <tr className="border-b border-neutral-200 text-left text-xs uppercase text-neutral-500">
              <th className="py-2 pr-3">Time</th>
              <th className="py-2 pr-3">Symbol</th>
              <th className="py-2 pr-3">Side</th>
              <th className="py-2 pr-3">Entry</th>
              <th className="py-2 pr-3">SL</th>
              <th className="py-2 pr-3">Status</th>
              <th className="py-2 pr-3">Message</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? (
              rows.map((row) => (
                <tr className="border-b border-neutral-100" key={row.id}>
                  <td className="py-2 pr-3 text-neutral-600">{compactTime(row.created_at)}</td>
                  <td className="py-2 pr-3">{row.symbol || "-"}</td>
                  <td className={`py-2 pr-3 ${row.side === "BUY" ? "text-emerald-700" : row.side === "SELL" ? "text-rose-700" : "text-neutral-600"}`}>
                    {row.side || "-"}
                  </td>
                  <td className="py-2 pr-3">{numberText(row.entry_price ?? row.payload?.entry_reference)}</td>
                  <td className="py-2 pr-3">{numberText(row.stop_loss ?? row.payload?.stop_loss)}</td>
                  <td className="py-2 pr-3">{row.status || "-"}</td>
                  <td className="py-2 pr-3 text-neutral-600">{row.message || String(row.payload?.message || "-")}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td className="py-6 text-center text-neutral-500" colSpan={7}>
                  No rows
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
