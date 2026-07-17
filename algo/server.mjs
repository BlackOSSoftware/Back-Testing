import { createServer } from "node:http";
import path from "node:path";
import { spawn } from "node:child_process";
import { parse } from "node:url";
import next from "next";
import { WebSocket, WebSocketServer } from "ws";
import { resolvePythonBin } from "./lib/resolve-python.mjs";

const dev = process.env.NODE_ENV !== "production";
const hostname =
  process.env.HOST || (dev ? "127.0.0.1" : "0.0.0.0");
const port = Number(process.env.PORT || 3000);
const app = next({ dev, hostname, port });
const handle = app.getRequestHandler();
const workerPath = path.join(process.cwd(), "python", "algo_worker.py");
const pythonBin = resolvePythonBin();

console.log(`> Algo Desk starting (${dev ? "development" : "production"})`);
console.log(`> Python: ${pythonBin}`);
console.log(`> Preparing Next.js (first start on VPS can take 2-5 minutes, please wait)...`);

function runWorker(command, payload) {
  return new Promise((resolve, reject) => {
    const args = [workerPath, command];
    if (payload !== undefined) args.push(JSON.stringify(payload));
    const child = spawn(pythonBin, args, {
      cwd: process.cwd(),
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", (code) => {
      const text = stdout.trim();
      if (!text) {
        reject(new Error(stderr.trim() || `Python worker exited with code ${code}`));
        return;
      }
      try {
        const data = JSON.parse(text);
        if (code !== 0 || data.error) {
          reject(new Error(data.error || stderr.trim() || `Python worker exited with code ${code}`));
          return;
        }
        resolve(data);
      } catch {
        reject(new Error(stderr.trim() || "Python worker returned invalid JSON."));
      }
    });
  });
}

function send(socket, payload) {
  if (socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(payload));
  }
}

function broadcast(wss, payload) {
  for (const client of wss.clients) {
    send(client, payload);
  }
}

async function loadLiveState({ check = false } = {}) {
  if (!check) return runWorker("status");
  try {
    return await runWorker("control", {
      action: "check",
    });
  } catch {
    return await runWorker("status");
  }
}

await app.prepare();
console.log(`> Next.js ready. Binding http://${hostname}:${port}`);

const server = createServer((req, res) => {
  const parsedUrl = parse(req.url || "", true);
  void handle(req, res, parsedUrl);
});

const wss = new WebSocketServer({ noServer: true });
const FAST_ACTION_INTERVAL_MS = 100;
const IDLE_REFRESH_INTERVAL_MS = 2000;
let liveTimer = null;
let liveCheckRunning = false;
let lastKnownRunning = false;

async function publishState(options) {
  if (liveCheckRunning) return;
  liveCheckRunning = true;
  try {
    const state = await loadLiveState(options);
    lastKnownRunning = Boolean(state?.running);
    broadcast(wss, { type: "state", state });
  } catch (error) {
    broadcast(wss, {
      type: "error",
      message: error instanceof Error ? error.message : "Live update failed.",
    });
  } finally {
    liveCheckRunning = false;
  }
}

function scheduleLiveTick(delay = 0) {
  if (liveTimer || !wss.clients.size) return;
  liveTimer = setTimeout(async () => {
    liveTimer = null;
    await publishState({ check: lastKnownRunning });
    scheduleLiveTick(lastKnownRunning ? FAST_ACTION_INTERVAL_MS : IDLE_REFRESH_INTERVAL_MS);
  }, delay);
}

function startLiveTimer() {
  scheduleLiveTick();
}

function stopLiveTimerIfIdle() {
  if (wss.clients.size) return;
  if (liveTimer) {
    clearTimeout(liveTimer);
    liveTimer = null;
  }
}

function runImmediateSignalCheck() {
  lastKnownRunning = true;
  if (liveTimer) {
    clearTimeout(liveTimer);
    liveTimer = null;
  }
  scheduleLiveTick();
}

wss.on("connection", (socket) => {
  startLiveTimer();
  void loadLiveState()
    .then((state) => send(socket, { type: "state", state }))
    .catch((error) =>
      send(socket, {
        type: "error",
        message: error instanceof Error ? error.message : "Live connection failed.",
      }),
    );

  socket.on("message", (raw) => {
    try {
      const message = JSON.parse(String(raw));
      if (message.type === "refresh") {
        void publishState();
      }
      if (message.type === "check") {
        void publishState({ check: true });
      }
      if (message.type === "start") {
        runImmediateSignalCheck();
      }
    } catch {
      send(socket, { type: "error", message: "Invalid WebSocket message." });
    }
  });

  socket.on("close", stopLiveTimerIfIdle);
});

server.on("upgrade", (request, socket, head) => {
  const { pathname } = parse(request.url || "");
  if (pathname !== "/ws/algo") {
    socket.destroy();
    return;
  }
  wss.handleUpgrade(request, socket, head, (client) => {
    wss.emit("connection", client, request);
  });
});

server.listen(port, hostname, () => {
  console.log(`> Ready on http://${hostname}:${port}`);
  console.log(`> WebSocket ready on ws://${hostname}:${port}/ws/algo`);
});
