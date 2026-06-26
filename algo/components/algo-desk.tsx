"use client";

import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleDollarSign,
  Database,
  Radio,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Settings,
  TrendingDown,
  TrendingUp,
  Trash2,
} from "lucide-react";
import type { ReactNode } from "react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

type DeskView = "dashboard" | "settings";

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
  first_trail_profit: number;
  first_trail_lock_loss: number;
  second_trail_profit: number;
  volume: number;
  target_points: number;
  max_trades_per_day: number;
  max_open_positions: number;
  live_trading_enabled: boolean;
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

type AlgoState = {
  running: boolean;
  active_strategy_id: string;
  active_strategy: Strategy | null;
  started_at: string;
  stopped_at: string;
  last_error: string;
  algo_status: string;
  pending_order_day: string;
  last_signal: Signal;
  live_quote: LiveQuote;
  strategies: Strategy[];
  signal_log: LogRow[];
  trade_log: LogRow[];
  database: string;
};

const emptyStrategy: Strategy = {
  id: "",
  name: "XAUUSD M5 breakout",
  data_source: "MT5",
  symbol: "XAUUSD",
  timeframe: "M5",
  trail_timeframe: "M5",
  entry_pattern: "BOTH",
  range_start: "08:30",
  range_end: "09:30",
  session_start: "09:30",
  entry_cutoff: "18:00",
  session_end: "19:30",
  entry_buffer_pct: 0.25,
  entry_buffer_points: 0,
  stop_points: 500,
  first_trail_profit: 700,
  first_trail_lock_loss: 200,
  second_trail_profit: 700,
  volume: 0.01,
  target_points: 0,
  max_trades_per_day: 1,
  max_open_positions: 1,
  live_trading_enabled: false,
};

const timeframes = ["M1", "M2", "M3", "M4", "M5", "M10", "M15", "M30", "H1", "H4"];
const patterns = [
  ["BOTH", "Both sides"],
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

function compactTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-IN", {
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

function firstTrailLockNote(lockDistance: number) {
  return lockDistance > 0 ? `SL lock: +${numberText(lockDistance)} points` : "SL lock: break-even";
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

  const firstTrigger = levelFrom(entry, side, strategy.first_trail_profit, "profit");
  const firstStop = firstTrailLockStop(entry, side, strategy.first_trail_lock_loss);
  const secondTrigger = levelFrom(entry, side, strategy.first_trail_profit + strategy.second_trail_profit, "profit");
  const move = lastClose === null ? null : side === "BUY" ? lastClose - entry : entry - lastClose;
  const firstHit = move !== null && move >= strategy.first_trail_profit;
  const firstStopHit =
    firstHit &&
    firstStop !== null &&
    lastClose !== null &&
    (side === "BUY" ? lastClose <= firstStop : lastClose >= firstStop);
  const secondHit = move !== null && move >= strategy.first_trail_profit + strategy.second_trail_profit;

  return { side, entry, initialStop, firstTrigger, firstStop, secondTrigger, move, firstHit, firstStopHit, secondHit };
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
  const signalToday = isTodayIst(activeSignal.checked_at);
  const trailPlan = buildTrailPlan(selectedStrategy, signalToday ? activeSignal : {}, latestTodayTrade, liveQuote);
  const hasSignal = signalToday && Boolean(activeSignal.status || activeSignal.side);
  const isSettings = view === "settings";

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
    const onPopState = () => {
      setView(window.location.pathname === "/settings" ? "settings" : "dashboard");
    };
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
      const data = await apiJson<{ strategy: Strategy }>(url, {
        method,
        body: JSON.stringify(form),
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

  async function deleteStrategy() {
    if (!form.id) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await apiJson(`/api/algo/strategies/${encodeURIComponent(form.id)}`, { method: "DELETE" });
      setForm(emptyStrategy);
      setMessage("Strategy deleted.");
      await load();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Delete failed.");
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
    const href = nextView === "settings" ? "/settings" : "/";
    setView(nextView);
    window.history.pushState({}, "", href);
    window.scrollTo(0, 0);
  }

  return (
    <main className="min-h-screen bg-[#eef4f1] text-neutral-950">
      <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[248px_minmax(0,1fr)]">
        <aside className="border-b border-neutral-200 bg-[#121615] p-4 text-white lg:sticky lg:top-0 lg:h-screen lg:border-b-0">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-rose-300 text-neutral-950">
              <Bot size={20} />
            </div>
            <div>
              <div className="text-sm">Algo Command</div>
              <div className="text-xs text-neutral-400">Live signal desk</div>
            </div>
          </div>

          <nav className="mt-6 grid gap-2 text-sm">
            <button className={navClass(!isSettings)} onClick={() => navigate("dashboard")} type="button">
              <Activity size={16} /> Dashboard
            </button>
            <button className={navClass(isSettings)} onClick={() => navigate("settings")} type="button">
              <Settings size={16} /> Settings
            </button>
            <a className="flex items-center gap-2 rounded-lg px-3 py-2 text-neutral-300 hover:bg-white/10" href="#logs">
              <Database size={16} /> Logs
            </a>
          </nav>

          <div className="mt-6 rounded-lg border border-white/10 bg-white/5 p-3">
            <div className="text-xs text-neutral-400">Runtime</div>
            <div className="mt-1 flex items-center gap-2 text-sm">
              <span className={`h-2.5 w-2.5 rounded-full ${state?.running ? "bg-emerald-400" : "bg-neutral-500"}`} />
              {state?.running ? "Running" : "Stopped"}
            </div>
            <div className="mt-3 text-xs text-neutral-400">Active Strategy</div>
            <div className="mt-1 break-words text-sm">{state?.active_strategy?.name || "-"}</div>
          </div>
        </aside>

        <section className="min-w-0 p-4 lg:p-5">
          <ControlHeader
            busy={busy}
            error={error}
            message={message}
            running={Boolean(state?.running)}
            status={state?.algo_status || "Loading..."}
            strategy={selectedStrategy}
            title={isSettings ? "Strategy Settings" : "Trading Dashboard"}
            subtitle={isSettings ? "Manage strategy setup here." : "Today signal, entry, stop loss, and trail status in one place."}
            lastError={state?.last_error}
            socketConnected={socketConnected}
            onCheck={() => void control("check")}
            onRefresh={() => void load()}
            onStart={() => void control("start")}
            onStop={() => void control("stop")}
          />

          {isSettings ? (
            <SettingsView
              busy={busy}
              form={form}
              saving={saving}
              selectedExists={selectedExists}
              sortedStrategies={sortedStrategies}
              symbols={symbols}
              onDelete={() => void deleteStrategy()}
              onField={field}
              onNew={() => setForm(emptyStrategy)}
              onSave={saveStrategy}
              onSelect={selectStrategy}
              onNavigateSettings={() => navigate("settings")}
              onSymbolSearch={loadSymbols}
            />
          ) : (
            <DashboardView
              activeSignal={activeSignal}
              hasSignal={hasSignal}
              latestTodayTrade={latestTodayTrade}
              liveQuote={liveQuote}
              running={Boolean(state?.running)}
              selectedStrategy={selectedStrategy}
              signalToday={signalToday}
              sortedStrategies={sortedStrategies}
              todayTrades={todayTrades}
              trailPlan={trailPlan}
              onNavigateSettings={() => navigate("settings")}
              onSelect={selectStrategy}
            />
          )}

          <section id="logs" className="mt-4 grid gap-4 xl:grid-cols-2">
            <LogPanel title="Signal Log" rows={state?.signal_log || []} />
            <LogPanel title="Trade Log" rows={state?.trade_log || []} />
          </section>

          <footer className="mt-4 rounded-lg border border-neutral-200 bg-white p-3 text-xs text-neutral-500">
            SQL DB: {state?.database || "Loading..."}
          </footer>
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
  return `flex items-center gap-2 rounded-lg px-3 py-2 ${
    active ? "bg-white text-neutral-950" : "text-neutral-300 hover:bg-white/10"
  }`;
}

function ControlHeader({
  busy,
  error,
  lastError,
  message,
  running,
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
  message: string;
  running: boolean;
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
  return (
    <header className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
        <div>
          <p className="text-xs uppercase tracking-wide text-teal-700">Algo Live Control</p>
          <h1 className="mt-1 text-2xl tracking-normal text-neutral-950">{title}</h1>
          <p className="mt-1 text-sm text-neutral-600">{subtitle}</p>
          <p className="mt-2 text-xs text-neutral-500">
            {strategy.symbol || "-"} | {strategy.timeframe || "-"} | {status}
          </p>
          <p className="mt-1 text-xs text-neutral-500">
            {socketConnected ? "WebSocket live" : "WebSocket reconnecting"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <PowerSwitch
            checked={running}
            disabled={busy || (!running && !strategy.symbol)}
            onChange={(checked) => {
              if (checked) onStart();
              else onStop();
            }}
          />
          <button
            className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-neutral-950 px-4 py-2 text-sm text-white shadow-sm hover:bg-black"
            disabled={busy}
            onClick={onCheck}
            type="button"
          >
            <Search size={17} /> Check Signal
          </button>
          <button
            className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-neutral-300 bg-white px-4 py-2 text-sm text-neutral-900 hover:bg-neutral-50"
            disabled={busy}
            onClick={onRefresh}
            type="button"
          >
            <RefreshCw size={17} /> Refresh
          </button>
        </div>
      </div>
      {(message || error || lastError) && (
        <div
          className={`mt-4 flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
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
      className={`algo-power-switch ${disabled ? "algo-power-switch-disabled" : ""}`}
      title={checked ? "Stop Algo" : "Start Algo"}
    >
      <input
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
      <span className="button">
        <span className="light" />
        <span className="dots" />
        <span className="characters" />
        <span className="shine" />
        <span className="shadow" />
      </span>
    </label>
  );
}

function DashboardView({
  activeSignal,
  hasSignal,
  latestTodayTrade,
  liveQuote,
  running,
  selectedStrategy,
  signalToday,
  sortedStrategies,
  todayTrades,
  trailPlan,
  onNavigateSettings,
  onSelect,
}: {
  activeSignal: Signal;
  hasSignal: boolean;
  latestTodayTrade?: LogRow;
  liveQuote: LiveQuote;
  running: boolean;
  selectedStrategy: Strategy;
  signalToday: boolean;
  sortedStrategies: Strategy[];
  todayTrades: LogRow[];
  trailPlan: ReturnType<typeof buildTrailPlan>;
  onNavigateSettings: () => void;
  onSelect: (strategy: Strategy) => void;
}) {
  const side = latestTodayTrade?.side || (signalToday ? activeSignal.side : "") || "-";
  const entry = latestTodayTrade?.entry_price ?? (signalToday ? activeSignal.entry_reference : undefined);
  const stop = latestTodayTrade?.stop_loss ?? (signalToday ? activeSignal.stop_loss : undefined);
  const tradeTaken = Boolean(latestTodayTrade);
  const displayLivePrice = side === "BUY" || side === "SELL" ? liveExitPrice(side, liveQuote) : toNumber(liveQuote.last) ?? toNumber(liveQuote.bid);
  const livePoints = trailPlan.move;
  const tradeAction = activeSignal.trade_action;

  return (
    <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="grid gap-4">
        <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="flex items-center gap-2">
                <Radio size={18} className="text-teal-700" />
                <h2 className="text-lg">Today Signal</h2>
              </div>
              <p className="text-sm text-neutral-600">{compactTime(activeSignal.checked_at)}</p>
            </div>
            <StatusBadge side={side} status={signalToday ? activeSignal.status || "Checked" : "Not checked today"} />
          </div>

          <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3">
            <GlassMetric label="Signal Side" value={hasSignal ? side : "No signal"} tone={side === "BUY" ? "green" : side === "SELL" ? "red" : "plain"} icon={side === "BUY" ? <TrendingUp size={16} /> : side === "SELL" ? <TrendingDown size={16} /> : <Radio size={16} />} />
            <GlassMetric label="Entry Price" value={numberText(entry)} note="Entry reference" tone="blush" icon={<CircleDollarSign size={16} />} />
            <GlassMetric label="Live Price" value={numberText(displayLivePrice)} note={liveQuote.error || compactTime(liveQuote.time)} tone="plain" icon={<Radio size={16} />} />
            <GlassMetric label="Live Points" value={numberText(livePoints)} note="P/L from entry" tone={livePoints !== null && livePoints >= 0 ? "green" : livePoints !== null ? "red" : "plain"} icon={livePoints !== null && livePoints >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />} />
            <GlassMetric label="Stop Loss" value={numberText(stop)} note="Initial SL" tone="red" icon={<ShieldCheck size={16} />} />
            <GlassMetric label="First Target" value={numberText(trailPlan.firstTrigger)} note={`+${numberText(selectedStrategy.first_trail_profit)} points`} tone="green" icon={<TrendingUp size={16} />} />
            <GlassMetric label="Second Target" value={numberText(trailPlan.secondTrigger)} note={`+${numberText(selectedStrategy.second_trail_profit)} points`} tone="green" icon={<TrendingUp size={16} />} />
            <GlassMetric label="Trade Taken" value={tradeTaken ? "Yes" : "No"} note={tradeTaken ? latestTodayTrade?.status || "Trade log found" : tradeAction?.message || "No trade record today"} tone={tradeTaken ? "green" : "plain"} icon={<CircleDollarSign size={16} />} />
          </div>

          <p className="mt-3 rounded-lg bg-[#f5f7f2] p-3 text-sm text-neutral-700">
            {signalToday ? activeSignal.message || "Signal checked." : "Today signal has not been checked yet."}
          </p>
        </section>

        <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-lg">SL & Trail Status</h2>
              <p className="text-sm text-neutral-600">Live snapshot for first SL, second trail, and current move.</p>
            </div>
            <span className={`rounded-lg px-2 py-1 text-xs ${running ? "bg-emerald-100 text-emerald-700" : "bg-neutral-100 text-neutral-700"}`}>
              {running ? "Algo running" : "Algo stopped"}
            </span>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <PlanMetric label="Current Move" value={numberText(trailPlan.move)} note="Price movement from entry" />
            <PlanMetric label="First Target Price" value={numberText(trailPlan.firstTrigger)} note={`First target: +${numberText(selectedStrategy.first_trail_profit)} points`} />
            <PlanMetric
              label="First Target Hit?"
              value={trailPlan.firstHit ? "Yes" : "Waiting"}
              note="Trail SL starts after first target"
            />
            <PlanMetric label="Trail SL After First Target" value={numberText(trailPlan.firstStop)} note={firstTrailLockNote(selectedStrategy.first_trail_lock_loss)} />
            <PlanMetric
              label="Second Target Price"
              value={numberText(trailPlan.secondTrigger)}
              note={`Second target: +${numberText(selectedStrategy.second_trail_profit)} points after first target`}
            />
            <PlanMetric
              label="Second Target Hit?"
              value={trailPlan.secondHit ? "Yes" : "Waiting"}
              note={trailPlan.secondHit ? `Now following ${selectedStrategy.trail_timeframe} candle trail` : "Second target not reached yet"}
            />
          </div>
        </section>

        <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
          <h2 className="text-lg">Range & Trigger Info</h2>
          <div className="mt-4 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
            <Metric label="Range High" value={numberText(signalToday ? activeSignal.range_high : undefined)} />
            <Metric label="Range Low" value={numberText(signalToday ? activeSignal.range_low : undefined)} />
            <Metric label="Buy Trigger" value={numberText(signalToday ? activeSignal.buy_trigger : undefined)} />
            <Metric label="Sell Trigger" value={numberText(signalToday ? activeSignal.sell_trigger : undefined)} />
            <Metric label="Last Close" value={numberText(signalToday ? activeSignal.last_close : undefined)} />
            <Metric label="Live Bid" value={numberText(liveQuote.bid)} />
            <Metric label="Live Ask" value={numberText(liveQuote.ask)} />
            <Metric label="Spread" value={numberText(liveQuote.spread)} />
            <Metric label="Quote Time" value={compactTime(liveQuote.time)} />
            <Metric label="Phase" value={signalToday ? phaseText(activeSignal.phase) : "-"} />
            <Metric label="Trigger Candle" value={compactTime(signalToday ? activeSignal.trigger_candle_time : undefined)} />
            <Metric label="Trades Today" value={String(todayTrades.length)} />
            <Metric label="Auto Trade" value={selectedStrategy.live_trading_enabled ? "Enabled" : "Disabled"} />
          </div>
        </section>
      </div>

      <aside className="grid gap-4 content-start">
        <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
          <h2 className="text-lg">Active Strategy</h2>
          <div className="mt-3 grid gap-2 text-sm">
            <Metric label="Name" value={selectedStrategy.name || "-"} />
            <Metric label="Symbol" value={selectedStrategy.symbol || "-"} />
            <Metric label="Entry Pattern" value={patternText(selectedStrategy.entry_pattern)} />
            <Metric label="Session" value={`${selectedStrategy.session_start} - ${selectedStrategy.session_end}`} />
            <Metric label="Range" value={`${selectedStrategy.range_start} - ${selectedStrategy.range_end}`} />
            <Metric label="Max Trades" value={String(selectedStrategy.max_trades_per_day || "-")} />
            <Metric label="Auto Trade" value={selectedStrategy.live_trading_enabled ? "Enabled" : "Disabled"} />
          </div>
        </section>

        <StrategyList strategies={sortedStrategies} selectedId={selectedStrategy.id} onNavigateSettings={onNavigateSettings} onSelect={onSelect} />
      </aside>
    </div>
  );
}

function SettingsView({
  busy,
  form,
  saving,
  selectedExists,
  sortedStrategies,
  symbols,
  onDelete,
  onField,
  onNew,
  onSave,
  onSelect,
  onNavigateSettings,
  onSymbolSearch,
}: {
  busy: boolean;
  form: Strategy;
  saving: boolean;
  selectedExists: boolean;
  sortedStrategies: Strategy[];
  symbols: string[];
  onDelete: () => void;
  onField: <K extends keyof Strategy>(key: K, value: Strategy[K]) => void;
  onNew: () => void;
  onSave: (event: FormEvent<HTMLFormElement>) => void;
  onSelect: (strategy: Strategy) => void;
  onNavigateSettings: () => void;
  onSymbolSearch: (query?: string) => Promise<void>;
}) {
  return (
    <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-lg">Strategy Setup</h2>
            <p className="text-sm text-neutral-600">{selectedExists ? form.id : "New strategy"}</p>
          </div>
          <div className="flex gap-2">
            <button
              className="inline-flex items-center gap-2 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm hover:bg-neutral-50"
              onClick={onNew}
              type="button"
            >
              New
            </button>
            <button
              className="inline-flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 hover:bg-rose-100"
              disabled={!selectedExists || busy}
              onClick={onDelete}
              type="button"
            >
              <Trash2 size={16} /> Delete
            </button>
          </div>
        </div>

        <form className="mt-4 grid gap-3" onSubmit={onSave}>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <Field label="Strategy Name">
              <input className="input" value={form.name} onChange={(event) => onField("name", event.target.value)} />
            </Field>
            <Field label="Data Source">
              <select className="input" value={form.data_source} onChange={(event) => onField("data_source", event.target.value)}>
                <option value="MT5">MT5</option>
                <option value="DELTA">DELTA</option>
              </select>
            </Field>
            <Field label="Symbol Search">
              <SymbolPicker
                onChange={(symbol) => onField("symbol", symbol)}
                onSearch={onSymbolSearch}
                symbols={symbols}
                value={form.symbol}
              />
            </Field>
            <Field label="Entry Timeframe">
              <select className="input" value={form.timeframe} onChange={(event) => onField("timeframe", event.target.value)}>
                {timeframes.map((timeframe) => (
                  <option key={timeframe}>{timeframe}</option>
                ))}
              </select>
            </Field>
            <Field label="Trail Timeframe">
              <select className="input" value={form.trail_timeframe} onChange={(event) => onField("trail_timeframe", event.target.value)}>
                {timeframes.map((timeframe) => (
                  <option key={timeframe}>{timeframe}</option>
                ))}
              </select>
            </Field>
            <Field label="Entry Pattern">
              <select className="input" value={form.entry_pattern} onChange={(event) => onField("entry_pattern", event.target.value)}>
                {patterns.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Range Start">
              <input className="input" type="time" value={form.range_start} onChange={(event) => onField("range_start", event.target.value)} />
            </Field>
            <Field label="Range End">
              <input className="input" type="time" value={form.range_end} onChange={(event) => onField("range_end", event.target.value)} />
            </Field>
            <Field label="Session Start">
              <input className="input" type="time" value={form.session_start} onChange={(event) => onField("session_start", event.target.value)} />
            </Field>
            <Field label="Last Entry Time">
              <input className="input" type="time" value={form.entry_cutoff} onChange={(event) => onField("entry_cutoff", event.target.value)} />
            </Field>
            <Field label="Force Exit Time">
              <input className="input" type="time" value={form.session_end} onChange={(event) => onField("session_end", event.target.value)} />
            </Field>
            <Field label="Volume">
              <input className="input" type="number" step="0.01" value={form.volume} onChange={(event) => onField("volume", Number(event.target.value))} />
            </Field>
            <Field label="Auto Trade">
              <label className="flex min-h-10 items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 text-sm">
                <input
                  checked={Boolean(form.live_trading_enabled)}
                  onChange={(event) => onField("live_trading_enabled", event.target.checked)}
                  type="checkbox"
                />
                Send live MT5 orders
              </label>
            </Field>
            <Field label="Entry Buffer %">
              <input className="input" type="number" step="0.01" value={form.entry_buffer_pct} onChange={(event) => onField("entry_buffer_pct", Number(event.target.value))} />
            </Field>
            <Field label="Entry Buffer Points">
              <input className="input" type="number" step="0.01" value={form.entry_buffer_points} onChange={(event) => onField("entry_buffer_points", Number(event.target.value))} />
            </Field>
            <Field label="Initial Stop Points">
              <input className="input" type="number" step="0.01" value={form.stop_points} onChange={(event) => onField("stop_points", Number(event.target.value))} />
            </Field>
            <Field label="First Target Points">
              <input className="input" type="number" step="0.01" value={form.first_trail_profit} onChange={(event) => onField("first_trail_profit", Number(event.target.value))} />
            </Field>
            <Field label="SL Lock After First Target">
              <input className="input" type="number" step="0.01" value={form.first_trail_lock_loss} onChange={(event) => onField("first_trail_lock_loss", Number(event.target.value))} />
            </Field>
            <Field label="Second Target Points">
              <input className="input" type="number" step="0.01" value={form.second_trail_profit} onChange={(event) => onField("second_trail_profit", Number(event.target.value))} />
            </Field>
            <Field label="Target Points">
              <input className="input" type="number" step="0.01" value={form.target_points} onChange={(event) => onField("target_points", Number(event.target.value))} />
            </Field>
            <Field label="Max Trades / Day">
              <input className="input" type="number" step="1" value={form.max_trades_per_day} onChange={(event) => onField("max_trades_per_day", Number(event.target.value))} />
            </Field>
            <Field label="Max Open Positions">
              <input className="input" type="number" step="1" value={form.max_open_positions} onChange={(event) => onField("max_open_positions", Number(event.target.value))} />
            </Field>
          </div>
          <div className="flex justify-end">
            <button
              className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-teal-700 px-4 py-2 text-sm text-white hover:bg-teal-800"
              disabled={saving}
              type="submit"
            >
              <Save size={17} /> {saving ? "Saving..." : "Save Settings"}
            </button>
          </div>
        </form>
      </section>

      <aside className="grid gap-4 content-start">
        <StrategyList strategies={sortedStrategies} selectedId={form.id} onNavigateSettings={onNavigateSettings} onSelect={onSelect} />
        <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
          <h2 className="text-lg">Selected Setup</h2>
          <div className="mt-3 grid gap-2 text-sm">
            <Metric label="Symbol" value={form.symbol || "-"} />
            <Metric label="Range" value={`${form.range_start} - ${form.range_end}`} />
            <Metric label="Session" value={`${form.session_start} - ${form.session_end}`} />
            <Metric label="Initial SL Points" value={numberText(form.stop_points)} />
            <Metric label="First Target Points" value={numberText(form.first_trail_profit)} />
            <Metric label="Second Target Points" value={numberText(form.second_trail_profit)} />
            <Metric label="Auto Trade" value={form.live_trading_enabled ? "Enabled" : "Disabled"} />
          </div>
        </section>
      </aside>
    </div>
  );
}

function StrategyList({
  onNavigateSettings,
  strategies,
  selectedId,
  onSelect,
}: {
  onNavigateSettings: () => void;
  strategies: Strategy[];
  selectedId?: string;
  onSelect: (strategy: Strategy) => void;
}) {
  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="text-lg">Strategies</h2>
        <button className="inline-flex items-center gap-1 rounded-lg border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-50" onClick={onNavigateSettings} type="button">
          <Settings size={14} /> Settings
        </button>
      </div>
      <div className="mt-3 grid max-h-[360px] gap-2 overflow-auto pr-1">
        {strategies.length ? (
          strategies.map((strategy) => (
            <button
              className={`rounded-lg border p-3 text-left text-sm hover:bg-[#f5f7f2] ${
                selectedId === strategy.id ? "border-rose-300 bg-rose-50" : "border-neutral-200 bg-white"
              }`}
              key={strategy.id}
              onClick={() => onSelect(strategy)}
              type="button"
            >
              <div>{strategy.name}</div>
              <div className="mt-1 text-xs text-neutral-600">
                {strategy.symbol} | {strategy.timeframe} | {patternText(strategy.entry_pattern)}
              </div>
            </button>
          ))
        ) : (
          <div className="rounded-lg border border-dashed border-neutral-300 p-3 text-sm text-neutral-500">No strategy saved.</div>
        )}
      </div>
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
        className="input"
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onChange={(event) => updateQuery(event.target.value)}
        onFocus={() => {
          setOpen(true);
          void onSearch(query);
        }}
        placeholder="Search MT5 symbol"
        value={query}
      />
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

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid gap-1 text-sm">
      <span className="text-neutral-700">{label}</span>
      {children}
    </label>
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
    <section className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
      <h2 className="text-lg">{title}</h2>
      <div className="mt-3 overflow-auto">
        <table className="w-full min-w-[760px] border-collapse text-sm">
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
