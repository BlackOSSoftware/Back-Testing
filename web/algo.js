let csrfToken = "";
let currentStrategies = [];
let availableSymbols = [];

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Please sign in again.");
  }
  if (!response.ok) throw new Error(data.error || "Request failed.");
  return data;
}

function fmt(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : String(value);
}

function shortTime(value) {
  if (!value) return "-";
  try { return new Date(value).toLocaleString("en-IN"); } catch { return value; }
}

function item(label, value) {
  return `<div><span>${label}</span><strong>${value ?? "-"}</strong></div>`;
}

function currentSource() {
  return document.getElementById("strategyForm").elements.data_source.value || "MT5";
}

function selectedStrategy() {
  const selectedId = document.getElementById("strategySelect").value;
  return currentStrategies.find((strategy) => strategy.id === selectedId) || currentStrategies[0] || null;
}

const strategyFields = [
  "name",
  "data_source",
  "symbol",
  "timeframe",
  "trail_timeframe",
  "entry_pattern",
  "range_start",
  "range_end",
  "session_start",
  "entry_cutoff",
  "session_end",
  "entry_buffer_pct",
  "stop_points",
  "first_trail_profit",
  "first_trail_lock_loss",
  "second_trail_profit",
  "volume",
];

function fillStrategyForm(strategy) {
  const form = document.getElementById("strategyForm");
  const defaults = {
    name: "",
    data_source: "MT5",
    symbol: "BTCUSD#",
    timeframe: "M5",
    trail_timeframe: "M5",
    entry_pattern: "BOTH",
    range_start: "08:30",
    range_end: "09:30",
    session_start: "09:30",
    entry_cutoff: "18:00",
    session_end: "19:30",
    entry_buffer_pct: 0.25,
    stop_points: 500,
    first_trail_profit: 700,
    first_trail_lock_loss: 200,
    second_trail_profit: 700,
    volume: 0.01,
  };
  const values = { ...defaults, ...(strategy || {}) };
  strategyFields.forEach((name) => {
    if (form.elements[name]) form.elements[name].value = values[name] ?? "";
  });
}

function collectStrategyForm() {
  const form = document.getElementById("strategyForm");
  const data = {};
  strategyFields.forEach((name) => {
    if (form.elements[name]) data[name] = form.elements[name].value;
  });
  ["entry_buffer_pct", "stop_points", "first_trail_profit", "first_trail_lock_loss", "second_trail_profit", "volume"].forEach((name) => {
    data[name] = Number(data[name]);
  });
  const activeId = document.getElementById("strategySelect").value;
  if (activeId) data.id = activeId;
  return data;
}

function renderStrategyDetails(strategy) {
  document.getElementById("strategyDetails").innerHTML = strategy ? [
    item("Symbol", strategy.symbol),
    item("Timeframe", `${strategy.timeframe} / ${strategy.trail_timeframe}`),
    item("Pattern", strategy.entry_pattern),
    item("Range", `${strategy.range_start}-${strategy.range_end}`),
    item("Session", `${strategy.session_start}-${strategy.session_end}`),
    item("Stop / Trail", `${fmt(strategy.stop_points)} / ${fmt(strategy.first_trail_profit)}-${fmt(strategy.first_trail_lock_loss)}`),
  ].join("") : item("Status", "No saved strategy");
}

function editSelectedStrategy() {
  const strategy = selectedStrategy();
  fillStrategyForm(strategy);
  renderStrategyDetails(strategy);
  loadSymbols();
}

function renderSymbolMenu() {
  const input = document.getElementById("algoSymbolInput");
  const menu = document.getElementById("algoSymbolMenu");
  const query = input.value.trim().toLowerCase();
  const matches = availableSymbols
    .filter((symbol) => !query || symbol.toLowerCase().includes(query))
    .slice(0, 35);
  menu.innerHTML = matches.length
    ? matches.map((symbol) => `<button type="button" data-symbol="${symbol}">${symbol}</button>`).join("")
    : `<div class="symbol-empty">No symbols found</div>`;
}

async function loadSymbols() {
  try {
    const symbols = await api(`/api/symbols?${new URLSearchParams({ source: currentSource() }).toString()}`);
    availableSymbols = Array.isArray(symbols) ? symbols : [];
  } catch (error) {
    availableSymbols = ["BTCUSD#", "BTCUSD", "XAUUSD", "ETHUSD", "US30", "NAS100"];
  }
  renderSymbolMenu();
}

function render(algo) {
  const running = Boolean(algo.running);
  const pill = document.getElementById("modePill");
  pill.textContent = running ? "RUNNING" : "STOPPED";
  pill.classList.toggle("running", running);
  document.getElementById("runningValue").textContent = running ? "YES" : "NO";
  document.getElementById("tradesToday").textContent = algo.trades_today ?? 0;
  document.getElementById("lastError").textContent = algo.last_error || "-";
  document.getElementById("startBtn").disabled = running;
  document.getElementById("stopBtn").disabled = !running;

  const strategies = algo.strategies || [];
  currentStrategies = strategies;
  const select = document.getElementById("strategySelect");
  select.innerHTML = strategies.length
    ? strategies.map((strategy) => `<option value="${strategy.id}">${strategy.name} | ${strategy.symbol} | ${strategy.timeframe}/${strategy.trail_timeframe}</option>`).join("")
    : `<option value="">No saved strategies</option>`;
  select.value = algo.active_strategy_id || strategies[0]?.id || "";

  const strategy = algo.active_strategy;
  fillStrategyForm(strategy);
  renderStrategyDetails(strategy);

  const signal = algo.last_signal || {};
  document.getElementById("signalPhase").textContent = signal.phase || "NO_STRATEGY";
  document.getElementById("signalStatus").textContent = signal.status || "No active strategy";
  document.getElementById("signalMessage").textContent = signal.message || "Save or select a strategy first.";
  document.getElementById("lastCheck").textContent = shortTime(signal.checked_at);
  document.getElementById("signalDetails").innerHTML = [
    item("Range High", fmt(signal.range_high)),
    item("Range Low", fmt(signal.range_low)),
    item("Buy Trigger", fmt(signal.buy_trigger)),
    item("Sell Trigger", fmt(signal.sell_trigger)),
    item("Side", signal.side || "WAIT"),
    item("Entry Ref", fmt(signal.entry_reference)),
    item("Stop Loss", fmt(signal.stop_loss)),
  ].join("");

  document.getElementById("tradeRows").innerHTML = algo.recent_trades?.length
    ? algo.recent_trades.map((trade) => {
        const result = trade.result || {};
        const label = result.ok
          ? result.kind === "PENDING" ? "PENDING SET"
            : result.kind === "FORCE_EXIT" ? "FORCE EXIT"
            : result.kind === "TRAIL_SL" ? "TRAIL SL UPDATED"
            : "ORDER SENT"
          : `FAILED: ${result.comment || result.error || result.retcode || "-"}`;
        return `<tr><td>${shortTime(trade.time)}</td><td>${trade.symbol}</td><td><b>${trade.side}</b></td><td class="num">${fmt(trade.entry_reference)}</td><td class="num">${fmt(trade.stop_loss)}</td><td>${label}</td></tr>`;
      }).join("")
    : `<tr><td class="empty" colspan="6">No trade attempts yet.</td></tr>`;
  if (window.lucide) lucide.createIcons();
}

async function refresh() {
  render(await api("/api/algo/status"));
  await loadSymbols();
}

async function postAction(button, path, body = {}) {
  button.disabled = true;
  try {
    const result = await api(path, { method: "POST", body: JSON.stringify(body) });
    render(result.algo);
  } catch (error) {
    document.getElementById("signalMessage").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

document.getElementById("refreshBtn").addEventListener("click", refresh);
document.getElementById("startBtn").addEventListener("click", (event) => postAction(event.currentTarget, "/api/algo/start"));
document.getElementById("stopBtn").addEventListener("click", (event) => postAction(event.currentTarget, "/api/algo/stop"));
document.getElementById("checkBtn").addEventListener("click", (event) => postAction(event.currentTarget, "/api/algo/check"));
document.getElementById("applyBtn").addEventListener("click", (event) => postAction(event.currentTarget, "/api/algo/apply", { strategy_id: document.getElementById("strategySelect").value }));
document.getElementById("saveStrategyBtn").addEventListener("click", (event) => postAction(event.currentTarget, "/api/algo/strategies", collectStrategyForm()));
document.getElementById("strategySelect").addEventListener("change", editSelectedStrategy);
document.getElementById("algoSymbolInput").addEventListener("focus", () => {
  renderSymbolMenu();
  document.getElementById("algoSymbolMenu").classList.add("open");
});
document.getElementById("algoSymbolInput").addEventListener("input", () => {
  renderSymbolMenu();
  document.getElementById("algoSymbolMenu").classList.add("open");
});
document.getElementById("algoSymbolMenu").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-symbol]");
  if (!button) return;
  document.getElementById("algoSymbolInput").value = button.dataset.symbol;
  document.getElementById("algoSymbolMenu").classList.remove("open");
});
document.getElementById("strategyForm").elements.data_source.addEventListener("change", loadSymbols);
document.addEventListener("click", (event) => {
  if (!event.target.closest(".symbol-combo")) {
    document.getElementById("algoSymbolMenu").classList.remove("open");
  }
});

async function boot() {
  const session = await api("/api/session");
  csrfToken = session.csrf_token;
  await refresh();
}

boot();
