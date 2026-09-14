<template>
  <div class="settings">
    <el-card class="page-card" shadow="never">
      <template #header>
        <div class="page-header">
          <span class="page-title">密钥与模型</span>
          <el-space>
            <el-button :loading="busy" @click="reload">
              <el-icon class="el-icon--left"><Refresh /></el-icon>
              重新加载
            </el-button>
            <el-button type="primary" :loading="busy" @click="save">
              <el-icon class="el-icon--left"><CircleCheck /></el-icon>
              保存
            </el-button>
          </el-space>
        </div>
      </template>

      <el-alert type="info" show-icon :closable="false">
        产线三路（出题 / Qwen / 判分）各自配置 model、base_url、api_key 和推理强度。下方复验与材料审核可单独配，也可留空密钥以复用判分密钥。密钥保存在
        <code>~/.config/lcqa-desktop/</code>
        ，不会写入仓库。留空 api_key 表示不改动已有密钥。判分按覆盖式：金标要点不漏、且无错误内容即可得分，不必字字对应。
      </el-alert>
    </el-card>

    <section v-for="group in groups" :key="group.id" class="group">
      <el-divider content-position="left">{{ group.title }}</el-divider>
      <el-card v-for="role in group.roles" :key="role" class="role-card" shadow="never">
        <template #header>
          <div class="role-header">
            <span>{{ labels[role] }}</span>
            <el-tag :type="keyTagType(role)" effect="light" size="small">
              {{ keyStatus(role) }}
            </el-tag>
          </div>
        </template>
        <el-form :disabled="busy" label-width="90px" @submit.prevent>
          <el-row :gutter="16">
            <el-col :xs="24" :sm="14">
              <el-form-item label="模型 ID">
                <el-input v-model="form[role].model">
                  <template #prefix>
                    <el-icon><Cpu /></el-icon>
                  </template>
                </el-input>
              </el-form-item>
            </el-col>
            <el-col :xs="24" :sm="10">
              <el-form-item label="推理强度">
                <el-select v-model="form[role].reasoning_effort" style="width: 100%">
                  <el-option
                    v-for="item in effortOptions"
                    :key="item.value"
                    :label="item.label"
                    :value="item.value"
                  />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="Base URL">
            <el-input v-model="form[role].base_url" :placeholder="placeholders[role]">
              <template #prefix>
                <el-icon><Link /></el-icon>
              </template>
            </el-input>
          </el-form-item>
          <el-form-item label="API Key">
            <el-input
              v-model="form[role].api_key"
              type="password"
              show-password
              :placeholder="keyPlaceholder(role)"
            >
              <template #prefix>
                <el-icon><Key /></el-icon>
              </template>
            </el-input>
          </el-form-item>
        </el-form>
        <el-text v-if="roleNotes[role]" type="info" size="small" class="role-note">
          {{ roleNotes[role] }}
        </el-text>
      </el-card>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { apiGet, apiPut } from "../api";
import { toastError, toastSuccess } from "../ui";

const emit = defineEmits<{ saved: [] }>();

const pipelineRoles = ["generation", "evaluation", "judge"] as const;
const auxRoles = ["review", "material_audit"] as const;
const roleOrder = [...pipelineRoles, ...auxRoles] as const;
type RoleId = (typeof roleOrder)[number];

const groups = [
  { id: "pipeline", title: "产线", roles: pipelineRoles },
  { id: "aux", title: "复验与材料审核", roles: auxRoles },
] as const;

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
const busy = ref(false);

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

function applyRoleRow(role: RoleId, row: any, keepEffort: boolean) {
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

function isAux(role: RoleId): role is (typeof auxRoles)[number] {
  return (auxRoles as readonly string[]).includes(role);
}

function keyStatus(role: RoleId) {
  const row = form[role];
  if (isAux(role) && row.api_key_source === "judge") return "复用判分密钥";
  if (row.api_key_set) return "密钥已就绪";
  return isAux(role) ? "缺少密钥（保存后仍可回落到判分密钥）" : "缺少密钥";
}

function keyTagType(role: RoleId): "success" | "warning" | "info" {
  const row = form[role];
  if (isAux(role) && row.api_key_source === "judge") return "info";
  return row.api_key_set ? "success" : "warning";
}

function keyPlaceholder(role: RoleId) {
  const row = form[role];
  if (isAux(role) && row.api_key_source === "judge") {
    return `复用判分密钥 ${row.api_key_mask || ""}`.trim();
  }
  if (row.api_key_set) return `已配置 ${row.api_key_mask}`;
  return isAux(role) ? "未配置（将复用判分密钥）" : "未配置";
}

async function reload() {
  busy.value = true;
  try {
    const data = await apiGet("/api/settings");
    for (const role of roleOrder) {
      applyRoleRow(role, data.roles?.[role] || {}, false);
    }
  } catch (err: any) {
    toastError(err);
  } finally {
    busy.value = false;
  }
}

async function save() {
  busy.value = true;
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
    toastSuccess(bits.join("；"));
    emit("saved");
  } catch (err: any) {
    toastError(err);
  } finally {
    busy.value = false;
  }
}

onMounted(reload);
</script>

<style scoped>
.settings {
  max-width: 840px;
}
.page-card {
  margin-bottom: 4px;
}
.page-header,
.role-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.page-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--primary-dark);
}
.role-header span {
  font-weight: 600;
  color: var(--primary-dark);
}
.group {
  margin-top: 4px;
}
.role-card {
  margin-bottom: 14px;
}
.role-card :deep(.el-form-item) {
  margin-bottom: 14px;
}
.role-card :deep(.el-form-item:last-of-type) {
  margin-bottom: 0;
}
.role-note {
  display: block;
  margin-top: 12px;
  line-height: 1.6;
}
code {
  font-size: 12px;
}
</style>
