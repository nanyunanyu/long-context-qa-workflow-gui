<template>
  <section class="filter-summary" role="region" :aria-label="title">
    <div class="filter-summary-head">
      <strong>{{ title }}</strong>
      <span class="hint">共 {{ total }} {{ unit }}</span>
    </div>
    <p v-if="!total" class="muted empty">当前筛选无{{ unit }}。</p>
    <div v-else class="filter-summary-body">
      <div v-for="group in visibleGroups" :key="group.name" class="summary-group">
        <span class="summary-label">{{ group.name }}</span>
        <div class="summary-pills">
          <span v-for="item in group.items" :key="item.key" class="summary-pill">
            <StatusIcon v-if="item.state" :state="item.state" />
            <span
              v-else-if="item.domainKey"
              class="domain-tag"
              :class="'tone-' + domainTagTone(item.domainKey)"
              :title="item.domainKey"
            >
              {{ item.domainLabel || item.label || item.domainKey }}
            </span>
            <span v-else-if="item.chipTone" class="meta-chip" :class="'tone-' + item.chipTone">
              {{ item.label }}
            </span>
            <span v-else>{{ item.label }}</span>
            <strong>{{ item.count }}</strong>
          </span>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { domainTagTone, type TaskVisualState } from "../api";
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

const props = withDefaults(
  defineProps<{
    title?: string;
    total: number;
    unit?: string;
    groups: SummaryGroup[];
  }>(),
  { title: "当前筛选汇总", unit: "条" }
);

const visibleGroups = computed(() => props.groups.filter((g) => g.items.length > 0));
</script>

<style scoped>
.filter-summary {
  margin: 0 0 12px;
  padding: 10px 12px;
  background: #f7fafc;
  border: 1px solid var(--border);
  border-radius: 10px;
}
.filter-summary-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}
.filter-summary-head strong {
  font-size: 13px;
  color: var(--primary-dark);
}
.hint {
  font-size: 12px;
  color: var(--muted);
}
.empty {
  margin: 0;
  font-size: 13px;
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
.domain-tag,
.meta-chip {
  display: inline-flex;
  align-items: center;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  line-height: 1.5;
  white-space: nowrap;
}
.domain-tag.tone-teal {
  color: #0f766e;
  background: #ccfbf1;
}
.domain-tag.tone-blue {
  color: #1d4ed8;
  background: #dbeafe;
}
.domain-tag.tone-green {
  color: #166534;
  background: #dcfce7;
}
.domain-tag.tone-amber {
  color: #92400e;
  background: #fef3c7;
}
.domain-tag.tone-rose {
  color: #9f1239;
  background: #ffe4e6;
}
.domain-tag.tone-slate {
  color: #334155;
  background: #e2e8f0;
}
.domain-tag.tone-cyan {
  color: #0e7490;
  background: #cffafe;
}
.domain-tag.tone-orange {
  color: #c2410c;
  background: #ffedd5;
}
.domain-tag.tone-indigo {
  color: #3730a3;
  background: #e0e7ff;
}
.domain-tag.tone-lime {
  color: #3f6212;
  background: #ecfccb;
}
.meta-chip.tone-READY,
.meta-chip.tone-pass {
  color: #166534;
  background: #dcfce7;
}
.meta-chip.tone-IN_PROGRESS,
.meta-chip.tone-progress {
  color: #1d4ed8;
  background: #dbeafe;
}
.meta-chip.tone-USED,
.meta-chip.tone-neutral,
.meta-chip.tone-pending {
  color: #334155;
  background: #e2e8f0;
}
.meta-chip.tone-GATE_FAILED,
.meta-chip.tone-fail {
  color: #9f1239;
  background: #ffe4e6;
}
.meta-chip.tone-OTHER,
.meta-chip.tone-warn {
  color: #92400e;
  background: #fef3c7;
}
</style>
