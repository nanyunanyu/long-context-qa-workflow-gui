<template>
  <span class="status" :class="tone" :title="tip">
    <el-icon class="icon" :size="16">
      <component :is="icon" />
    </el-icon>
    <span v-if="showLabel" class="text">{{ label }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed, type Component } from "vue";
import {
  CircleCheck,
  CircleClose,
  Clock,
  Minus,
  Search,
  VideoPause,
  VideoPlay,
  Warning,
} from "@element-plus/icons-vue";
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

const icon = computed<Component>(() => {
  switch (props.state) {
    case "passed":
      return CircleCheck;
    case "borderline_50":
      return Minus;
    case "gate_failed":
    case "cancelled":
      return CircleClose;
    case "manual_review":
      return Search;
    case "blocked":
      return Warning;
    case "running":
      return VideoPlay;
    case "paused":
      return VideoPause;
    default:
      return Clock;
  }
});

const label = computed(() => {
  if (props.label != null && props.label !== "") return props.label;
  switch (props.state) {
    case "passed":
      return "通过";
    case "borderline_50":
      return "临界 4/8";
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
    case "borderline_50":
    case "manual_review":
    case "blocked":
    case "paused":
      return "warn";
    case "gate_failed":
    case "cancelled":
      return "bad";
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
