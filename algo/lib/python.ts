import { spawn } from "node:child_process";
import path from "node:path";

export type WorkerResult<T = unknown> = T & {
  ok?: boolean;
  error?: string;
};

const workerPath = path.join(process.cwd(), "python", "algo_worker.py");
const pythonBin =
  process.env.ALGO_PYTHON || (process.platform === "win32" ? "python" : "python3");

export function runWorker<T>(command: string, payload?: unknown): Promise<WorkerResult<T>> {
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
        const data = JSON.parse(text) as WorkerResult<T>;
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
