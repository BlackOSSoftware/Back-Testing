#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-3000}"
export NODE_ENV="${NODE_ENV:-production}"
export NEXT_TELEMETRY_DISABLED=1

if command -v python3 >/dev/null 2>&1; then
  export ALGO_PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  export ALGO_PYTHON=python
else
  echo "[Algo Desk] Warning: Python not found. API calls may fail."
fi

if [[ ! -d node_modules ]]; then
  echo "[Algo Desk] Installing npm dependencies..."
  npm install
fi

if [[ ! -d .next ]]; then
  echo "[Algo Desk] No production build found. Building (use build:vps on small VPS)..."
  if [[ "${LOW_MEMORY_BUILD:-0}" == "1" ]]; then
    npm run build:vps
  else
    npm run build
  fi
fi

echo "[Algo Desk] Starting on http://${HOST}:${PORT}"
exec node server.mjs
