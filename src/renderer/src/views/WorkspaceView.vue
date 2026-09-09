<template>
  <div class="card">
    <h2>选择工作根目录</h2>
    <p>
      可打开当前「长上下文QA」仓库，或选择空文件夹。空目录会搭建 materials / queue / samples 脚手架，并复制产线脚本；已有工作区不会覆盖材料。
    </p>
    <div class="row">
      <button class="primary" :disabled="busy" @click="pick">选择文件夹</button>
      <span v-if="busy" class="hint">正在初始化…</span>
      <span v-if="error" class="error">{{ error }}</span>
    </div>
    <div class="path-row">
      <input
        v-model="manualPath"
        type="text"
        placeholder="或粘贴绝对路径后回车，例如 /Users/…/长上下文QA"
        :disabled="busy"
        @keydown.enter="openManual"
      />
      <button :disabled="busy || !manualPath.trim()" @click="openManual">打开</button>
    </div>
    <div v-if="recents.length" class="recents">
      <h3>最近打开</h3>
      <button v-for="item in recents" :key="item" class="recent" @click="open(item)">{{ item }}</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { apiPost } from "../api";

defineProps<{ recents: string[] }>();
const emit = defineEmits<{ opened: [info: any] }>();
const busy = ref(false);
const error = ref("");
const manualPath = ref("");

async function open(path: string) {
  busy.value = true;
  error.value = "";
  try {
    const info = await apiPost("/api/workspace/open", { path });
    emit("opened", info);
  } catch (err: any) {
    error.value = err.message || String(err);
  } finally {
    busy.value = false;
  }
}

async function openManual() {
  const path = manualPath.value.trim();
  if (path) await open(path);
}

async function pick() {
  error.value = "";
  try {
    if (!window.lcqa?.pickFolder) {
      error.value = "系统选目录不可用（请确认用 npm run dev 启动 Electron，或下方粘贴路径）";
      return;
    }
    const path = await window.lcqa.pickFolder();
    if (path) await open(path);
  } catch (err: any) {
    error.value = err?.message || String(err);
  }
}
</script>

<style scoped>
.card {
  margin: 48px auto;
  max-width: 720px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 28px;
  box-shadow: 0 8px 30px rgba(43, 108, 176, 0.06);
}
h2 {
  margin: 0 0 8px;
  color: var(--primary-dark);
}
p,
.hint {
  color: var(--muted);
  line-height: 1.6;
}
.row {
  display: flex;
  gap: 12px;
  align-items: center;
  margin: 18px 0;
}
.path-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.path-row input {
  flex: 1;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
}
.path-row button {
  border: 1px solid var(--border);
  background: #fff;
  color: var(--primary-dark);
  border-radius: 8px;
  padding: 8px 14px;
  cursor: pointer;
}
.primary {
  background: var(--primary);
  color: #fff;
  border: 0;
  padding: 10px 18px;
  border-radius: 8px;
  cursor: pointer;
}
.primary:disabled {
  opacity: 0.6;
}
.error {
  color: #c53030;
}
.recents {
  margin-top: 24px;
}
.recent {
  display: block;
  width: 100%;
  text-align: left;
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #f7fafc;
  color: var(--primary-dark);
  cursor: pointer;
}
</style>
