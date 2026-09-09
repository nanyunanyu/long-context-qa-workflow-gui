<template>
  <n-config-provider class="app-root" :theme-overrides="theme">
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <h1>长上下文 QA</h1>
          <p>本地产线控制台</p>
        </div>
        <nav class="menu">
          <button :class="{ active: tab === 'workspace' }" @click="tab = 'workspace'">工作区</button>
          <button :class="{ active: tab === 'board' }" @click="tab = 'board'">生产看板</button>
          <button :class="{ active: tab === 'materials' }" @click="tab = 'materials'">材料</button>
          <button :class="{ active: tab === 'settings' }" @click="tab = 'settings'">设置</button>
        </nav>
        <button v-if="workspace" class="reset" @click="resetWorkspace">更换目录</button>
      </aside>

      <div class="content">
        <header class="top">
          <div>
            <h2>{{ titles[tab] }}</h2>
            <p v-if="workspace" class="path">{{ workspace }}</p>
            <p v-else class="path">尚未选择工作根目录</p>
          </div>
          <span class="pill" :class="keysReady ? 'ok' : 'warn'">
            密钥 {{ keysReady ? "已就绪" : keys.file_exists ? "不完整" : "未配置" }}
          </span>
        </header>

        <main class="main">
          <WorkspaceView
            v-show="tab === 'workspace'"
            :recents="recents"
            @opened="onOpened"
          />
          <SettingsView v-show="tab === 'settings'" @saved="onSettingsSaved" />
          <div
            v-show="!workspace && (tab === 'board' || tab === 'materials')"
            class="empty"
          >
            请先在「工作区」选择目录后再{{ tab === "materials" ? "查看材料" : "生产" }}。
          </div>
          <KeepAlive>
            <BoardView
              v-if="workspace && tab === 'board'"
              :key="'board:' + workspace"
              :snapshot="snapshot"
              :initial-prefs="uiPrefs"
              @refresh="refresh"
              @prefs-saved="onPrefsSaved"
            />
            <MaterialsView
              v-else-if="workspace && tab === 'materials'"
              :key="'materials:' + workspace"
              :initial-prefs="uiPrefs"
              @prefs-saved="onPrefsSaved"
            />
          </KeepAlive>
        </main>
      </div>
    </div>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { NConfigProvider } from "naive-ui";
import { apiGet, apiPost, apiPut } from "./api";
import WorkspaceView from "./views/WorkspaceView.vue";
import MaterialsView from "./views/MaterialsView.vue";
import BoardView from "./views/BoardView.vue";
import SettingsView from "./views/SettingsView.vue";

const theme = {
  common: {
    primaryColor: "#2B6CB0",
    primaryColorHover: "#3182CE",
    primaryColorPressed: "#2C5282",
    borderRadius: "8px",
  },
};

const titles: Record<string, string> = {
  workspace: "工作区",
  board: "生产看板",
  materials: "材料",
  settings: "设置",
};

const workspace = ref<string | null>(null);
const recents = ref<string[]>([]);
const keys = ref<any>({ file_exists: false, ready: false, openai: false, dashscope: false });
const snapshot = ref<any>({ queue: { tasks: [] }, run: { status: "idle" }, progress: { tasks: {} } });
const tab = ref<"workspace" | "board" | "materials" | "settings">("workspace");
const uiPrefs = ref<any>(null);
let timer: number | undefined;
let ws: WebSocket | null = null;
let tabSaveTimer: number | undefined;

const keysReady = computed(() => !!keys.value.ready);

async function loadRecents() {
  const data = await apiGet("/api/recents");
  recents.value = data.recents || [];
  keys.value = (await apiGet("/api/keys")) || keys.value;
}

async function onOpened(info: any) {
  workspace.value = info.path;
  keys.value = info.keys || (await apiGet("/api/keys"));
  tab.value = "board";
  connect();
}

async function onSettingsSaved() {
  keys.value = await apiGet("/api/keys");
}

function onPrefsSaved(prefs: any) {
  if (prefs) uiPrefs.value = prefs;
}

function resetWorkspace() {
  workspace.value = null;
  ws?.close();
  if (timer) window.clearInterval(timer);
  tab.value = "workspace";
  loadRecents();
}

async function refresh() {
  snapshot.value = await apiGet("/api/run/state");
  if (snapshot.value.keys) keys.value = snapshot.value.keys;
}

function connect() {
  ws?.close();
  ws = new WebSocket("ws://127.0.0.1:8765/ws/run");
  ws.onmessage = (ev) => {
    snapshot.value = JSON.parse(ev.data);
    if (snapshot.value.keys) keys.value = snapshot.value.keys;
  };
  ws.onerror = () => {
    if (timer) window.clearInterval(timer);
    timer = window.setInterval(() => refresh().catch(() => undefined), 800);
  };
  refresh().catch(() => undefined);
}

async function bootstrap() {
  await loadRecents();
  try {
    uiPrefs.value = await apiGet("/api/ui-prefs");
  } catch {
    uiPrefs.value = null;
  }
  const prefs = uiPrefs.value;
  if (prefs?.last_tab && ["workspace", "board", "materials", "settings"].includes(prefs.last_tab)) {
    // only apply tab after workspace open; keep workspace first if no auto-open
  }
  if (prefs?.auto_open_workspace && prefs?.last_workspace) {
    try {
      const info = await apiPost("/api/workspace/open", { path: prefs.last_workspace });
      await onOpened(info);
      if (prefs.last_tab && prefs.last_tab !== "workspace") {
        tab.value = prefs.last_tab;
      }
    } catch {
      // fall back to picker if last workspace missing
    }
  }
}

watch(tab, (value) => {
  if (tabSaveTimer) window.clearTimeout(tabSaveTimer);
  tabSaveTimer = window.setTimeout(() => {
    apiPut("/api/ui-prefs", { last_tab: value }).catch(() => undefined);
  }, 300);
});

onMounted(bootstrap);
onUnmounted(() => {
  ws?.close();
  if (timer) window.clearInterval(timer);
  if (tabSaveTimer) window.clearTimeout(tabSaveTimer);
});
</script>

<style scoped>
:deep(.app-root) {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  flex: 1;
}
.shell {
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
  display: flex;
  align-items: stretch;
  overflow: hidden;
  background: var(--bg);
}
.sidebar {
  width: 200px;
  flex: 0 0 200px;
  align-self: stretch;
  background: #fff;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 20px 14px;
  overflow: auto;
  box-sizing: border-box;
}
.brand h1 {
  margin: 0;
  font-size: 18px;
  color: var(--primary-dark);
}
.brand p {
  margin: 4px 0 18px;
  font-size: 12px;
  color: var(--muted);
}
.menu {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.menu button {
  text-align: left;
  border: 0;
  background: transparent;
  color: var(--primary-dark);
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
}
.menu button.active {
  background: #ebf8ff;
  color: var(--primary);
  font-weight: 600;
}
.reset {
  margin-top: auto;
  border: 1px solid var(--border);
  background: #fff;
  color: var(--muted);
  border-radius: 8px;
  padding: 8px 12px;
  cursor: pointer;
}
.content {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  align-self: stretch;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 18px 24px 12px;
  border-bottom: 1px solid var(--border);
  background: #fff;
  flex-shrink: 0;
}
.top h2 {
  margin: 0;
  font-size: 18px;
  color: var(--primary-dark);
}
.path {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--muted);
  word-break: break-all;
  max-width: 640px;
}
.pill {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  background: #fffaf0;
  color: #c05621;
}
.pill.ok {
  background: #ebf8ff;
  color: var(--primary);
}
.main {
  flex: 1;
  min-height: 0;
  padding: 20px 24px 28px;
  overflow: auto;
}
.empty {
  background: #fff;
  border: 1px dashed var(--border);
  border-radius: 12px;
  padding: 28px;
  color: var(--muted);
}
</style>
