<template>
  <span class="status" :class="tone" :title="tip">
    <svg class="icon" viewBox="0 0 16 16" aria-hidden="true">
      <!-- check -->
      <path
        v-if="kind === 'passed'"
        d="M3.5 8.5 6.5 11.5 12.5 4.5"
        fill="none"
        stroke="currentColor"
        stroke-width="1.6"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <!-- x -->
      <g v-else-if="kind === 'gate_failed' || kind === 'cancelled'">
        <path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
      </g>
      <!-- search / review -->
      <g v-else-if="kind === 'manual_review'">
        <circle cx="7" cy="7" r="3.2" fill="none" stroke="currentColor" stroke-width="1.5" />
        <path d="M9.5 9.5 13 13" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      </g>
      <!-- alert -->
      <g v-else-if="kind === 'blocked'">
        <path
          d="M8 2.5 14 13.5H2Z"
          fill="none"
          stroke="currentColor"
          stroke-width="1.4"
          stroke-linejoin="round"
        />
        <path d="M8 6.2v3.2M8 11.2h.01" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      </g>
      <!-- play -->
      <path
        v-else-if="kind === 'running'"
        d="M5 3.5v9l8-4.5z"
        fill="none"
        stroke="currentColor"
        stroke-width="1.4"
        stroke-linejoin="round"
      />
      <!-- pause -->
      <g v-else-if="kind === 'paused'">
        <path d="M5 3.5v9M11 3.5v9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
      </g>
      <!-- clock / pending -->
      <g v-else>
        <circle cx="8" cy="8" r="5.2" fill="none" stroke="currentColor" stroke-width="1.4" />
        <path d="M8 5v3.2l2 1.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
      </g>
    </svg>
    <span v-if="showLabel" class="text">{{ label }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { TaskVisualState } from "../api";

const props = withDefaults(
  defineProps<{
    state: TaskVisualState | "pending";
    showLabel?: boolean;
    /** Override default label text (e.g. concrete gate-fail reason). */
    label?: string;
    /** Override hover title; defaults to label. */
    title?: string;
  }>(),
  { showLabel: true }
);

const kind = computed(() => props.state);

const label = computed(() => {
  if (props.label != null && props.label !== "") return props.label;
  switch (props.state) {
    case "passed":
      return "通过";
    case "gate_failed":
      return "门禁失败";
    case "cancelled":
      return "已取消";
    case "manual_review":
      return "复验";
    case "blocked":
      return "技术失败";
    case "running":
      return "运行中";
    case "paused":
      return "已暂停";
    case "queued":
      return "排队";
    case "pending":
      return "—";
    default:
      return "未知";
  }
});

const tip = computed(() => props.title || label.value);

const tone = computed(() => {
  switch (props.state) {
    case "passed":
      return "ok";
    case "gate_failed":
    case "cancelled":
      return "bad";
    case "manual_review":
    case "blocked":
    case "paused":
      return "warn";
    case "running":
      return "run";
    default:
      return "muted";
  }
});
</script>

<style scoped>
.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  white-space: nowrap;
  max-width: 100%;
  min-width: 0;
}
.icon {
  width: 16px;
  height: 16px;
  flex: 0 0 16px;
}
.text {
  line-height: 1.2;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ok {
  color: #2f855a;
}
.bad {
  color: #c53030;
}
.warn {
  color: #c05621;
}
.run {
  color: #2b6cb0;
}
.muted {
  color: #718096;
}
</style>
