<template>
  <el-config-provider class="app-root" :locale="zhCn">
    <el-container class="shell">
      <el-aside width="220px" class="sidebar">
        <div class="brand">
          <div class="brand-mark" aria-hidden="true">QA</div>
          <div class="brand-text">
            <h1>长上下文 QA</h1>
            <p>本地产线控制台</p>
          </div>
        </div>
        <el-menu :default-active="tab" class="nav-menu" @select="onMenuSelect">
          <el-menu-item index="workspace">
            <el-icon><House /></el-icon>
            <span>工作区</span>
          </el-menu-item>
          <el-menu-item index="board">
            <el-icon><Grid /></el-icon>
            <span>生产看板</span>
          </el-menu-item>
          <el-menu-item index="materials">
            <el-icon><Document /></el-icon>
            <span>材料</span>
          </el-menu-item>
          <el-menu-item index="settings">
            <el-icon><Setting /></el-icon>
            <span>设置</span>
          </el-menu-item>
        </el-menu>
        <div class="sidebar-footer">
          <el-button v-if="workspace" class="reset" @click="resetWorkspace">更换目录</el-button>
        </div>
      </el-aside>

      <el-container class="content">
        <el-header class="top" height="auto">
          <div>
            <h2>{{ titles[tab] }}</h2>
            <p v-if="workspace" class="path">{{ workspace }}</p>
            <p v-else class="path">尚未选择工作根目录</p>
          </div>
          <div class="top-pills">
            <el-tag size="small" :type="keysReady ? 'success' : 'warning'" effect="light">
              密钥 {{ keysReady ? "已就绪" : keys.file_exists ? "不完整" : "未配置" }}
            </el-tag>
            <el-tag
              v-if="workspace"
              size="small"
              class="run-status"
              :class="'tone-' + runStatusTone"
              :type="runStatusTagType"
              effect="light"
              :title="runStatusTitle"
            >
              <span class="run-status-dot" aria-hidden="true" />
              <span>{{ runStatusLabel }}</span>
              <span v-if="runStatusClaimed != null" class="run-status-claimed"> 已领 {{ runStatusClaimed }}</span>
            </el-tag>
          </div>
        </el-header>

        <el-main class="main">
          <WorkspaceView
            v-show="tab === 'workspace'"
            :recents="recents"
            @opened="onOpened"
          />
          <SettingsView v-show="tab === 'settings'" @saved="onSettingsSaved" />
          <el-empty
            v-show="!workspace && (tab === 'board' || tab === 'materials')"
            :description="tab === 'materials' ? '请先在「工作区」选择目录后再查看材料。' : '请先在「工作区」选择目录后再生产。'"
          />
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
        </el-main>
      </el-container>
    </el-container>
  </el-config-provider>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import { Document, Grid, House, Setting } from "@element-plus/icons-vue";
import { apiGet, apiPost, apiPut } from "./api";
import WorkspaceView from "./views/WorkspaceView.vue";
import MaterialsView from "./views/MaterialsView.vue";
import BoardView from "./views/BoardView.vue";
import SettingsView from "./views/SettingsView.vue";

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

const RUN_STATUS_UI: Record<string, { label: string; tone: string; tag: "info" | "success" | "warning" | "danger" }> = {
  idle: { label: "空闲", tone: "idle", tag: "info" },
  running: { label: "运行中", tone: "running", tag: "success" },
  stopping: { label: "停止中", tone: "stopping", tag: "warning" },
  interrupted: { label: "已中断", tone: "interrupted", tag: "danger" },
};

function isQuotaStopMessage(message: unknown) {
  return /额度耗尽|insufficient_quota|credit_balance_exhausted/i.test(String(message || ""));
}

const runStatus = computed(() => snapshot.value?.run?.status || "idle");
const quotaStopped = computed(() => isQuotaStopMessage(snapshot.value?.run?.message));
const runStatusLabel = computed(() => {
  if (runStatus.value === "idle" && quotaStopped.value) return "额度耗尽";
  return RUN_STATUS_UI[runStatus.value]?.label || String(runStatus.value);
});
const runStatusTone = computed(() => {
  if (runStatus.value === "idle" && quotaStopped.value) return "interrupted";
  return RUN_STATUS_UI[runStatus.value]?.tone || "idle";
});
const runStatusTagType = computed(() => {
  if (runStatus.value === "idle" && quotaStopped.value) return "danger";
  return RUN_STATUS_UI[runStatus.value]?.tag || "info";
});
const runStatusClaimed = computed(() => {
  if (!["running", "stopping"].includes(runStatus.value)) return null;
  const claimed = snapshot.value?.run?.claimed;
  return claimed == null ? null : claimed;
});
const runStatusTitle = computed(() => {
  const run = snapshot.value?.run || {};
  const bits = [`状态码：${runStatus.value}`];
  if (run.run_id) bits.push(`run_id：${run.run_id}`);
  if (run.claimed != null) bits.push(`已领取：${run.claimed}`);
  if (run.message) bits.push(String(run.message));
  return bits.join(" · ");
});

function onMenuSelect(index: string) {
  if (index === "workspace" || index === "board" || index === "materials" || index === "settings") {
    tab.value = index;
  }
}

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
  height: 100%;
  min-height: 0;
  width: 100%;
  flex-direction: row;
  overflow: hidden;
  background: var(--bg);
}
.sidebar {
  width: 220px;
  flex: 0 0 220px;
  height: 100%;
  background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 20px 14px 16px;
  overflow: auto;
  box-sizing: border-box;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 4px 16px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--border);
}
.brand-mark {
  flex: 0 0 auto;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(145deg, #3182ce 0%, #2c5282 100%);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
  box-shadow: 0 4px 10px rgba(44, 82, 130, 0.22);
}
.brand-text {
  min-width: 0;
}
.brand h1 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  line-height: 1.25;
  color: var(--primary-dark);
}
.brand p {
  margin: 3px 0 0;
  font-size: 12px;
  color: var(--muted);
}
.nav-menu {
  border-right: 0;
  background: transparent;
}
.sidebar-footer {
  margin-top: auto;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.reset {
  width: 100%;
}
.content {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
}
.top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  padding: 18px 24px 12px;
  border-bottom: 1px solid var(--border);
  background: #fff;
  height: auto;
}
.top h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: var(--primary-dark);
  letter-spacing: 0.01em;
}
.path {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--muted);
  word-break: break-all;
  max-width: 640px;
  letter-spacing: 0.01em;
}
.top-pills {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
  flex-shrink: 0;
}
.run-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.run-status-dot {
  position: relative;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}
.run-status-dot::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: currentColor;
  pointer-events: none;
  opacity: 0;
}
.run-status-claimed {
  color: inherit;
  opacity: 0.72;
  font-weight: 500;
}
.run-status.tone-running .run-status-dot {
  animation: run-status-core 1.4s ease-in-out infinite;
}
.run-status.tone-running .run-status-dot::after {
  animation: run-status-ring 1.5s ease-out infinite;
}
@keyframes run-status-core {
  0%,
  100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(0.85);
    opacity: 0.72;
  }
}
@keyframes run-status-ring {
  0% {
    transform: scale(1);
    opacity: 0.55;
  }
  100% {
    transform: scale(2.8);
    opacity: 0;
  }
}
.main {
  flex: 1;
  min-height: 0;
  padding: 20px 24px 28px;
  overflow: auto;
  background: var(--bg);
}
</style>
