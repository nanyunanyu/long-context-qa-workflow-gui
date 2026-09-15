<template>
  <el-card class="filter-summary" shadow="never" role="region" :aria-label="title">
    <div class="filter-summary-head" :class="{ 'has-body': expanded }">
      <div class="filter-summary-head-main">
        <strong>{{ title }}</strong>
        <span class="hint">共 {{ total }} {{ unit }}</span>
      </div>
      <el-button type="primary" link :aria-expanded="expanded" @click="expanded = !expanded">
        {{ expanded ? "收起" : "展开" }}
        <el-icon class="el-icon--right">
          <ArrowUp v-if="expanded" />
          <ArrowDown v-else />
        </el-icon>
      </el-button>
    </div>
    <template v-if="expanded">
      <el-empty v-if="!total" :description="'当前筛选无' + unit + '。'" :image-size="48" />
      <template v-else>
        <el-checkbox-group v-model="selectedNames" size="small" class="filter-summary-cats">
          <el-checkbox-button v-for="name in groupNames" :key="name" :value="name">
            {{ name }}
          </el-checkbox-button>
        </el-checkbox-group>
        <el-empty
          v-if="!visibleGroups.length"
          description="请选择要显示的分类"
          :image-size="48"
        />
        <div v-else class="filter-summary-body">
          <div v-for="group in visibleGroups" :key="group.name" class="summary-group">
            <span class="summary-label">{{ group.name }}</span>
            <div class="summary-pills">
              <template v-for="item in group.items" :key="item.key">
                <template v-for="sel in [chipSelectState(item)]" :key="item.key + '-sel'">
                  <el-check-tag
                    v-if="sel"
                    class="summary-pill"
                    :class="{ 'is-indeterminate': sel.indeterminate }"
                    :checked="sel.checked"
                    :disabled="sel.disabled"
                    :title="chipTitle(sel)"
                    @change="(checked) => onChipChange(item, checked)"
                  >
                    <StatusIcon v-if="item.state" :state="item.state" />
                    <strong>{{ item.count }}</strong>
                  </el-check-tag>
                  <span v-else class="summary-pill">
                    <StatusIcon v-if="item.state" :state="item.state" />
                    <el-tag
                      v-else-if="item.domainKey"
                      size="small"
                      class="domain-tag"
                      :class="'tone-' + domainTagTone(item.domainKey)"
                      :title="item.domainKey"
                    >
                      {{ item.domainLabel || item.label || item.domainKey }}
                    </el-tag>
                    <el-tag
                      v-else-if="item.chipTone"
                      size="small"
                      :class="'tone-' + item.chipTone"
                      :type="chipTagType(item.chipTone)"
                      effect="light"
                    >
                      {{ item.label }}
                    </el-tag>
                    <span v-else>{{ item.label }}</span>
                    <strong>{{ item.count }}</strong>
                  </span>
                </template>
              </template>
            </div>
          </div>
        </div>
      </template>
    </template>
  </el-card>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ArrowDown, ArrowUp } from "@element-plus/icons-vue";
import { domainTagTone, type TaskVisualState } from "../api";
import { chipTagType } from "../ui";
import StatusIcon from "./StatusIcon.vue";

export type SummaryChip = {
  key: string;
  count: number;
  label?: string;
  state?: TaskVisualState | "pending";
  domainKey?: string;
  domainLabel?: string;
  chipTone?: string;
};

export type SummaryGroup = {
  name: string;
  items: SummaryChip[];
};

export type SummaryChipSelectState = {
  checked: boolean;
  indeterminate: boolean;
  disabled: boolean;
};

const props = withDefaults(
  defineProps<{
    title?: string;
    total: number;
    unit?: string;
    groups: SummaryGroup[];
    itemSelectStates?: Record<string, SummaryChipSelectState>;
  }>(),
  { title: "当前筛选汇总", unit: "条" }
);

const emit = defineEmits<{
  toggleItem: [item: SummaryChip, checked: boolean];
}>();

function chipSelectState(item: SummaryChip): SummaryChipSelectState | null {
  if (!item.state) return null;
  return props.itemSelectStates?.[item.key] ?? null;
}

function chipTitle(sel: SummaryChipSelectState): string {
  if (sel.disabled) return "当前状态没有可勾选任务";
  if (sel.indeterminate) return "部分已选，点击全选该类";
  if (sel.checked) return "点击取消勾选该类";
  return "点击勾选该类";
}

function onChipChange(item: SummaryChip, checked: string | number | boolean) {
  emit("toggleItem", item, Boolean(checked));
}

const expanded = ref(true);
const selectedNames = ref<string[]>([]);

const groupNames = computed(() => props.groups.map((g) => g.name));

watch(
  () => groupNames.value.join("\0"),
  () => {
    const names = groupNames.value;
    const prev = new Set(selectedNames.value);
    selectedNames.value = [...names.filter((n) => prev.has(n)), ...names.filter((n) => !prev.has(n))];
  },
  { immediate: true }
);

const visibleGroups = computed(() => {
  const selected = new Set(selectedNames.value);
  return props.groups.filter((g) => selected.has(g.name) && g.items.length > 0);
});
</script>

<style scoped>
.filter-summary {
  margin: 0 0 12px;
}
.filter-summary :deep(.el-card__body) {
  padding: 10px 12px;
}
.filter-summary-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.filter-summary-head.has-body {
  margin-bottom: 8px;
}
.filter-summary-head-main {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.filter-summary-head strong {
  font-size: 13px;
  color: var(--primary-dark);
}
.hint {
  font-size: 12px;
  color: var(--muted);
}
.filter-summary-cats {
  margin-bottom: 8px;
}
.filter-summary-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.summary-group {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
}
.summary-label {
  flex: 0 0 36px;
  padding-top: 4px;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.4;
}
.summary-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}
.summary-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px 3px 6px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 12px;
  color: var(--primary-dark);
  line-height: 1.3;
}
.summary-pill.el-check-tag {
  height: auto;
  font-weight: inherit;
}
.summary-pill.el-check-tag:not(.is-disabled) {
  cursor: pointer;
}
.summary-pill.el-check-tag:hover:not(.is-disabled):not(.is-checked):not(.is-indeterminate) {
  background: #fff;
  color: var(--primary-dark);
  border-color: var(--el-color-primary-light-5);
}
.summary-pill.el-check-tag.is-checked,
.summary-pill.el-check-tag.is-checked:hover {
  background: var(--el-color-primary-light-9);
  color: var(--primary-dark);
  border-color: var(--el-color-primary);
}
.summary-pill.el-check-tag.is-indeterminate,
.summary-pill.el-check-tag.is-indeterminate:hover {
  background: var(--el-color-primary-light-9);
  color: var(--primary-dark);
  border-color: var(--el-color-primary);
  border-style: dashed;
}
.summary-pill.el-check-tag.is-disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.summary-pill strong {
  font-variant-numeric: tabular-nums;
  font-weight: 650;
}
.summary-pill :deep(.status) {
  gap: 4px;
  font-size: 12px;
}
.summary-pill :deep(.icon) {
  width: 14px;
  height: 16px;
  flex-basis: 14px;
}
</style>
