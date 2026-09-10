<template>
  <div class="settings">
    <div class="bar">
      <h2>密钥与模型</h2>
      <div class="actions">
        <button :disabled="busy" @click="reload">重新加载</button>
        <button class="primary" :disabled="busy" @click="save">保存</button>
      </div>
    </div>
    <p class="note">
      产线三路（出题 / Qwen / 判分）各自配置 model、base_url、api_key 和推理强度。下方复验与材料审核可单独配，也可留空密钥以复用判分密钥。密钥保存在
      <code>~/.config/lcqa-desktop/</code>，不会写入仓库。留空 api_key 表示不改动已有密钥。
      判分按覆盖式：金标要点不漏、且无错误内容即可得分，不必字字对应。
    </p>
    <p v-if="message" class="msg" :class="{ err: isError }">{{ message }}</p>

    <h3 class="group">产线</h3>
    <section v-for="role in pipelineRoles" :key="role" class="card">
      <h3>{{ labels[role] }}</h3>
      <label>
        模型 ID
        <input v-model="form[role].model" type="text" :disabled="busy" />
      </label>
      <label>
        Base URL
        <input v-model="form[role].base_url" type="text" :disabled="busy" :placeholder="placeholders[role]" />
      </label>
      <label>
        推理强度
        <select v-model="form[role].reasoning_effort" :disabled="busy">
          <option v-for="item in effortOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </option>
        </select>
      </label>
      <label>
        API Key
        <div class="key-row">
          <input
            v-model="form[role].api_key"
            :type="show[role] ? 'text' : 'password'"
            :disabled="busy"
            :placeholder="form[role].api_key_set ? `已配置 ${form[role].api_key_mask}` : '未配置'"
          />
          <button type="button" class="ghost" @click="show[role] = !show[role]">
            {{ show[role] ? "隐藏" : "显示" }}
          </button>
        </div>
      </label>
      <p class="hint">
        状态：{{ form[role].api_key_set ? "密钥已就绪" : "缺少密钥" }}
        <template v-if="roleNotes[role]"> · {{ roleNotes[role] }}</template>
      </p>
    </section>

    <h3 class="group">复验与材料审核</h3>
    <section v-for="role in auxRoles" :key="role" class="card">
      <h3>{{ labels[role] }}</h3>
      <label>
        模型 ID
        <input v-model="form[role].model" type="text" :disabled="busy" />
      </label>
      <label>
        Base URL
        <input v-model="form[role].base_url" type="text" :disabled="busy" :placeholder="placeholders[role]" />
      </label>
      <label>
        推理强度
        <select v-model="form[role].reasoning_effort" :disabled="busy">
          <option v-for="item in effortOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </option>
        </select>
      </label>
      <label>
        API Key
        <div class="key-row">
          <input
            v-model="form[role].api_key"
            :type="show[role] ? 'text' : 'password'"
            :disabled="busy"
            :placeholder="auxKeyPlaceholder(role)"
          />
          <button type="button" class="ghost" @click="show[role] = !show[role]">
            {{ show[role] ? "隐藏" : "显示" }}
          </button>
        </div>
      </label>
      <p class="hint">
        状态：{{ auxKeyStatus(role) }}
        <template v-if="roleNotes[role]"> · {{ roleNotes[role] }}</template>
      </p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { apiGet, apiPut } from "../api";

const emit = defineEmits<{ saved: [] }>();

const pipelineRoles = ["generation", "evaluation", "judge"] as const;
const auxRoles = ["review", "material_audit"] as const;
const roleOrder = [...pipelineRoles, ...auxRoles] as const;
const labels: Record<string, string> = {
  generation: "出题模型",
  evaluation: "Qwen 评测",
  judge: "判分模型",
  review: "复验模型",
  material_audit: "材料审核模型",
};
const placeholders: Record<string, string> = {
  generation: "例如 https://relay.example.com/v1",
  evaluation: "例如 https://dashscope.aliyuncs.com/compatible-mode/v1",
  judge: "默认 https://api.openai.com/v1",
  review: "留空则复用判分 Base URL",
  material_audit: "留空则复用判分 Base URL",
};
const roleNotes: Record<string, string> = {
  generation: "默认短答案；看板可选选择题或自动。题干与金标语言跟材料走；短答可以是短语或 1–3 个短句；多槽须答案格式和 alias。选择题为四选一或 2–3 项多选。默认推理强度「中」。",
  evaluation: "默认「高」（开启 thinking，thinking_budget=16384）。关闭会关掉 thinking。",
  judge: "覆盖金标要点、无错误内容即可得分，不必字字对应。默认推理强度「中」。",
  review: "用于 0/8 自动复验。可单独配密钥，未填则复用判分密钥。",
  material_audit: "用于材料页内容审核。可单独配密钥，未填则复用判分密钥。",
};
const effortOptions = [
  { value: "none", label: "关闭" },
  { value: "low", label: "低" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
  { value: "xhigh", label: "极高" },
  { value: "max", label: "最大" },
];
const defaultEffort: Record<string, string> = {
  generation: "medium",
  evaluation: "high",
  judge: "medium",
  review: "medium",
  material_audit: "medium",
};

type RoleForm = {
  model: string;
  base_url: string;
  reasoning_effort: string;
  api_key: string;
  api_key_set: boolean;
  api_key_mask: string;
  api_key_source: string;
};

const form = reactive<Record<string, RoleForm>>({
  generation: emptyRole("gpt-5.6-sol", "", "medium"),
  evaluation: emptyRole("qwen3.5-35b-a3b", "", "high"),
  judge: emptyRole("gpt-5.6-luna", "https://api.openai.com/v1", "medium"),
  review: emptyRole("gpt-5.6-luna", "", "medium"),
  material_audit: emptyRole("gpt-5.6-luna", "", "medium"),
});
const show = reactive<Record<string, boolean>>({
  generation: false,
  evaluation: false,
  judge: false,
  review: false,
  material_audit: false,
});
const busy = ref(false);
const message = ref("");
const isError = ref(false);

function emptyRole(model: string, base_url = "", reasoning_effort = "medium"): RoleForm {
  return {
    model,
    base_url,
    reasoning_effort,
    api_key: "",
    api_key_set: false,
    api_key_mask: "",
    api_key_source: "none",
  };
}

function applyRoleRow(role: (typeof roleOrder)[number], row: any, keepEffort: boolean) {
  form[role].model = row.model || form[role].model;
  form[role].base_url = row.base_url || "";
  form[role].api_key = "";
  form[role].api_key_set = !!row.api_key_set;
  form[role].api_key_mask = row.api_key_mask || "";
  form[role].api_key_source = String(row.api_key_source || (row.api_key_set ? "own" : "none"));
  const effort = String(row.reasoning_effort || "").trim();
  if (effort) {
    form[role].reasoning_effort = effort;
  } else if (!keepEffort) {
    form[role].reasoning_effort = defaultEffort[role];
  }
}

async function reload() {
  busy.value = true;
  message.value = "";
  try {
    const data = await apiGet("/api/settings");
    for (const role of roleOrder) {
      applyRoleRow(role, data.roles?.[role] || {}, false);
    }
  } catch (err: any) {
    isError.value = true;
    message.value = err.message || String(err);
  } finally {
    busy.value = false;
  }
}

async function save() {
  busy.value = true;
  message.value = "";
  isError.value = false;
  try {
    const roles: Record<string, any> = {};
    for (const role of roleOrder) {
      roles[role] = {
        model: form[role].model.trim(),
        base_url: form[role].base_url.trim(),
        reasoning_effort: form[role].reasoning_effort,
        api_key: form[role].api_key,
      };
    }
    const data = await apiPut("/api/settings", { roles });
    for (const role of roleOrder) {
      applyRoleRow(role, data.roles?.[role] || {}, true);
    }
    const bits: string[] = [];
    bits.push(data.ready ? "已保存，产线三路密钥就绪" : "已保存，但产线仍有角色缺少密钥");
    if (data.review_ready) bits.push("复验可用");
    if (data.material_audit_ready) bits.push("材料审核可用");
    message.value = bits.join("；");
    emit("saved");
  } catch (err: any) {
    isError.value = true;
    message.value = err.message || String(err);
  } finally {
    busy.value = false;
  }
}

function auxKeyPlaceholder(role: (typeof auxRoles)[number]) {
  const row = form[role];
  if (row.api_key_source === "judge") return `复用判分密钥 ${row.api_key_mask || ""}`.trim();
  if (row.api_key_set) return `已配置 ${row.api_key_mask}`;
  return "未配置（将复用判分密钥）";
}

function auxKeyStatus(role: (typeof auxRoles)[number]) {
  const row = form[role];
  if (row.api_key_source === "judge") return "复用判分密钥";
  if (row.api_key_set) return "密钥已就绪";
  return "缺少密钥（保存后仍可回落到判分密钥）";
}

onMounted(reload);
</script>

<style scoped>
.settings {
  max-width: 720px;
}
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
h2,
h3 {
  margin: 0;
  color: var(--primary-dark);
}
h3.group {
  margin: 22px 0 0;
  font-size: 14px;
}
.actions {
  display: flex;
  gap: 8px;
}
.note,
.hint {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}
.msg {
  color: var(--primary);
  font-size: 13px;
}
.msg.err {
  color: #c53030;
}
.card {
  margin-top: 14px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  color: var(--muted);
}
input,
select {
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 14px;
  color: var(--text);
  background: #fff;
}
.key-row {
  display: flex;
  gap: 8px;
}
.key-row input {
  flex: 1;
}
button {
  border: 1px solid var(--border);
  background: #fff;
  color: var(--primary-dark);
  border-radius: 8px;
  padding: 8px 14px;
  cursor: pointer;
}
button.primary {
  background: var(--primary);
  color: #fff;
  border-color: var(--primary);
}
button.ghost {
  white-space: nowrap;
}
button:disabled {
  opacity: 0.5;
}
code {
  font-size: 12px;
}
</style>
