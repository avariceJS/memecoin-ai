import { type ChildProcess, spawn, execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const webRoot = path.resolve(here, "..");
export const appRoot = path.resolve(webRoot, "..");
export const repoRoot = path.resolve(webRoot, "../../..");
const adapterScript = path.join(appRoot, "adapter/server.py");
const viteBin = path.join(webRoot, "node_modules/vite/bin/vite.js");
const runtimeDir = path.join(appRoot, ".local/tasks/CC02.0");
const pidFile = path.join(runtimeDir, "servers.json");
const logFile = path.join(runtimeDir, "servers.log");

type Pids = { adapter: number; vite: number };

function commandOf(pid: number): string {
  try {
    return execFileSync("ps", ["-ww", "-p", String(pid), "-o", "command="], { encoding: "utf8" });
  } catch {
    return "";
  }
}

function killOwned(pid: number, marker: string): void {
  const command = commandOf(pid);
  if (!command.includes(marker)) {
    return;
  }
  try {
    process.kill(-pid, "SIGTERM");
  } catch {
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      /* already gone */
    }
  }
}

export function stopServices(): void {
  if (!exists(pidFile)) {
    return;
  }
  try {
    const pids = JSON.parse(readFileSync(pidFile, "utf8")) as Pids;
    killOwned(pids.vite, viteBin);
    killOwned(pids.adapter, adapterScript);
  } catch {
    /* unreadable pid file */
  }
  rmSync(pidFile, { force: true });
}

function exists(file: string): boolean {
  try {
    readFileSync(file);
    return true;
  } catch {
    return false;
  }
}

async function ready(url: string, headers?: Record<string, string>): Promise<boolean> {
  try {
    const response = await fetch(url, { headers });
    return response.ok;
  } catch {
    return false;
  }
}

async function waitFor(check: () => Promise<boolean>, timeoutMs: number): Promise<boolean> {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await check()) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return false;
}

function launch(command: string, args: string[], cwd: string): ChildProcess {
  mkdirSync(runtimeDir, { recursive: true });
  const child = spawn(command, args, {
    cwd,
    detached: true,
    stdio: ["ignore", "ignore", "ignore"],
  });
  child.on("error", (error) => {
    writeFileSync(logFile, `${command} ${error.message}\n`, { flag: "a" });
  });
  return child;
}

export async function startServices(): Promise<void> {
  stopServices();
  const adapterBusy = await ready("http://127.0.0.1:8787/api/v1/capabilities", {
    "X-Control-Center": "1",
  });
  const viteBusy = await ready("http://127.0.0.1:5173/");
  if (adapterBusy || viteBusy) {
    throw new Error(
      "127.0.0.1:8787 или :5173 уже заняты неизвестным процессом. Harness не подключается к чужому серверу.",
    );
  }
  const adapter = launch("python3", [adapterScript, "--port", "8787"], appRoot);
  const vite = launch(
    "node",
    [viteBin, "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
    webRoot,
  );
  if (!adapter.pid || !vite.pid) {
    throw new Error("Не удалось запустить adapter или Vite");
  }
  adapter.unref();
  vite.unref();
  const pids: Pids = { adapter: adapter.pid, vite: vite.pid };
  writeFileSync(pidFile, JSON.stringify(pids));
  writeFileSync(logFile, `adapter ${pids.adapter}\nvite ${pids.vite}\n`);
  const adapterUp = await waitFor(
    () =>
      ready("http://127.0.0.1:8787/api/v1/capabilities", {
        "X-Control-Center": "1",
      }),
    20_000,
  );
  const viteUp = await waitFor(() => ready("http://127.0.0.1:5173/"), 40_000);
  if (!adapterUp || !viteUp) {
    stopServices();
    throw new Error("UI adapter или Vite не стали доступны на loopback");
  }
}
