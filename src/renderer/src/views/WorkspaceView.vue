<template>
  <el-card class="card" shadow="never">
    <h2>选择工作根目录</h2>
    <p>
      可打开当前「长上下文QA」仓库，或选择空文件夹。空目录会搭建 materials / queue / samples 脚手架，并复制产线脚本；已有工作区不会覆盖材料。
    </p>
    <div class="row">
      <el-button type="primary" :disabled="busy" @click="pick">选择文件夹</el-button>
      <span v-if="busy" class="hint">正在初始化…</span>
    </div>
    <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" class="error-alert" />
    <div class="path-row">
      <el-input
        v-model="manualPath"
        placeholder="或粘贴绝对路径后回车，例如 /Users/…/长上下文QA"
        :disabled="busy"
        @keydown.enter="openManual"
      />
      <el-button :disabled="busy || !manualPath.trim()" @click="openManual">打开</el-button>
    </div>
    <div v-if="recents.length" class="recents">
      <h3>最近打开</h3>
      <el-button
        v-for="item in recents"
        :key="item"
        class="recent"
        @click="open(item)"
      >
        {{ item }}
      </el-button>
    </div>
  </el-card>
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
.error-alert {
  margin-bottom: 12px;
}
.recents {
  margin-top: 24px;
}
.recent {
  display: block;
  width: 100%;
  margin-top: 8px;
  height: auto;
  white-space: normal;
  text-align: left;
  justify-content: flex-start;
}
</style>
