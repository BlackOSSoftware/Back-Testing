import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { resolvePythonBin } from "../lib/resolve-python.mjs";

const root = process.cwd();
const totalMemGb = (os.totalmem() / 1024 ** 3).toFixed(1);
const freeMemGb = (os.freemem() / 1024 ** 3).toFixed(1);

function ok(message) {
  console.log(`  OK   ${message}`);
}

function warn(message) {
  console.log(`  WARN ${message}`);
}

function fail(message) {
  console.log(`  FAIL ${message}`);
}

function run(command, args) {
  return spawnSync(command, args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
}

console.log("Algo Desk VPS doctor");
console.log(`Platform: ${process.platform} ${process.arch}`);
console.log(`Node: ${process.version}`);
console.log(`Memory: ${freeMemGb} GB free / ${totalMemGb} GB total`);
console.log("");

const pythonBin = resolvePythonBin();
const pythonCheck = run(pythonBin, ["--version"]);
if (pythonCheck.status === 0) {
  ok(`Python found (${pythonBin}): ${pythonCheck.stdout.trim() || pythonCheck.stderr.trim()}`);
} else {
  fail(`Python not found. Install python3 or set ALGO_PYTHON. Tried: ${pythonBin}`);
}

const worker = path.join(root, "python", "algo_worker.py");
if (existsSync(worker)) {
  ok("Python worker file exists");
  const statusCheck = run(pythonBin, [worker, "status"]);
  if (statusCheck.status === 0 && statusCheck.stdout.trim()) {
    ok("Python worker responds to status");
  } else {
    warn("Python worker status check failed (MetaTrader5 may be missing on Linux VPS)");
  }
} else {
  fail(`Missing worker: ${worker}`);
}

if (existsSync(path.join(root, "node_modules"))) {
  ok("node_modules present");
} else {
  fail("node_modules missing. Run: npm install");
}

if (existsSync(path.join(root, ".next"))) {
  ok("Production build (.next) present");
} else {
  warn("No .next build yet. Run: npm run build (or npm run build:vps on 1-2 GB RAM VPS)");
}

if (Number(totalMemGb) < 2) {
  warn("Less than 2 GB RAM detected. Use: npm run build:vps and add 2 GB swap on VPS.");
}

const pkg = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
const startScript = pkg.scripts?.start || "";
if (startScript.includes("cross-env")) {
  ok("package.json start script is Linux-compatible");
} else if (startScript.includes("set NODE_ENV")) {
  fail('package.json still uses Windows-only "set NODE_ENV". Pull latest code or run: npm install');
} else {
  warn("Could not verify start script compatibility");
}

console.log("");
console.log("Suggested VPS commands:");
console.log("  export HOST=0.0.0.0 PORT=3000");
console.log("  npm install");
console.log("  npm run build:vps    # use on small VPS");
console.log("  npm run start        # production");
console.log("  # or: chmod +x start.sh && LOW_MEMORY_BUILD=1 ./start.sh");
