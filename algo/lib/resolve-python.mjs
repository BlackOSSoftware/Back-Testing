/** Shared Python executable resolution for Node entrypoints. */
export function resolvePythonBin() {
  if (process.env.ALGO_PYTHON) return process.env.ALGO_PYTHON;
  return process.platform === "win32" ? "python" : "python3";
}
