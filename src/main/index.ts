import { app, BrowserWindow, dialog, ipcMain, nativeImage } from "electron";
// fetch is available in Electron 35+
import { execSync, spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

const API_PORT = 8765;
let mainWindow: BrowserWindow | null = null;
let apiProc: ChildProcess | null = null;

function resolveAppIcon(): string | undefined {
  const root = join(__dirname, "../../resources");
  const packaged = process.resourcesPath || "";
  const candidates =
    process.platform === "darwin"
      ? [
          join(root, "icon.icns"),
          join(root, "icon.png"),
          join(packaged, "icon.icns"),
          join(packaged, "icon.png"),
        ]
      : [
          join(root, "icon.png"),
          join(packaged, "icon.png"),
          join(root, "icon.icns"),
          join(packaged, "icon.icns"),
        ];
  return candidates.find((p) => p && existsSync(p));
}

function codeRoot(): string {
  const desktopRoot = app.getAppPath();
  return join(desktopRoot, "..");
}

function listenerPids(port: number): number[] {
  if (process.platform === "win32") return [];
  try {
    const out = execSync(`lsof -nP -iTCP:${port} -sTCP:LISTEN -t`, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    if (!out) return [];
    return out
      .split(/\s+/)
      .map((value) => Number(value))
      .filter((pid) => Number.isFinite(pid) && pid > 0);
  } catch {
    return [];
  }
}

async function stopStaleApi(): Promise<void> {
  for (const pid of listenerPids(API_PORT)) {
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      /* already gone */
    }
  }
  const start = Date.now();
  while (Date.now() - start < 3000 && listenerPids(API_PORT).length) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  for (const pid of listenerPids(API_PORT)) {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      /* already gone */
    }
  }
}

function startApi(): void {
  const root = codeRoot();
  const server = join(root, "desktop", "backend", "server.py");
  apiProc = spawn("python3", [server, "--host", "127.0.0.1", "--port", String(API_PORT)], {
    cwd: root,
    env: { ...process.env, LCQA_CODE_ROOT: root },
    stdio: "inherit",
  });
}

async function waitForApi(timeoutMs = 20000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(`http://127.0.0.1:${API_PORT}/api/health`);
      if (res.ok) {
        const data = (await res.json().catch(() => null)) as { features?: string[] } | null;
        if (Array.isArray(data?.features) && data.features.includes("reasoning_effort")) {
          return;
        }
      }
    } catch {
      /* retry */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("无法启动本地 API（127.0.0.1:8765）");
}

function createWindow(): void {
  const iconPath = resolveAppIcon();
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    backgroundColor: "#F7FAFC",
    title: "长上下文 QA",
    ...(iconPath ? { icon: iconPath } : {}),
    webPreferences: {
      preload: existsSync(join(__dirname, "../preload/index.mjs"))
        ? join(__dirname, "../preload/index.mjs")
        : join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  if (process.env.ELECTRON_RENDERER_URL) {
    mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL);
  } else {
    mainWindow.loadFile(join(__dirname, "../renderer/index.html"));
  }
}

ipcMain.handle("pick-folder", async () => {
  const opts = {
    title: "选择工作根目录",
    properties: ["openDirectory", "createDirectory"] as ("openDirectory" | "createDirectory")[],
  };
  const result = mainWindow
    ? await dialog.showOpenDialog(mainWindow, opts)
    : await dialog.showOpenDialog(opts);
  if (result.canceled || !result.filePaths[0]) return null;
  return result.filePaths[0];
});

app.whenReady().then(async () => {
  const iconPath = resolveAppIcon();
  if (iconPath && process.platform === "darwin" && app.dock) {
    const image = nativeImage.createFromPath(iconPath);
    if (!image.isEmpty()) app.dock.setIcon(image);
  }
  await stopStaleApi();
  startApi();
  try {
    await waitForApi();
  } catch (err) {
    dialog.showErrorBox("启动失败", String(err));
  }
  createWindow();
});

app.on("window-all-closed", () => {
  if (apiProc && !apiProc.killed) apiProc.kill();
  app.quit();
});
