<template>
  <div class="stage" :title="label">
    <div v-if="showStrip" class="strip" aria-hidden="true">
      <span
        v-for="item in tones"
        :key="item.step"
        class="dot"
        :class="item.tone"
        :title="item.label"
      />
    </div>
    <div class="label">{{ label }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { pipelineStepTones, stageLabel } from "../api";

const props = defineProps<{ task: any; snapshot: any }>();

const label = computed(() => stageLabel(props.task, props.snapshot));
const tones = computed(() => pipelineStepTones(props.task, props.snapshot));
const showStrip = computed(() => {
  const s = String(props.task?.status || "");
  return ["running", "claimed", "queued"].includes(s) || tones.value.some((t) => t.tone === "active");
});
</script>

<style scoped>
.stage {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
  overflow: visible;
}
.strip {
  display: flex;
  align-items: center;
  gap: 3px;
  flex-wrap: nowrap;
  overflow: hidden;
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #e2e8f0;
  flex-shrink: 0;
}
.dot.done {
  background: #38a169;
}
.dot.active {
  background: #3182ce;
  box-shadow: 0 0 0 2px rgba(49, 130, 206, 0.25);
  animation: pulse 1.2s ease-in-out infinite;
}
.dot.error {
  background: #e53e3e;
}
.dot.idle {
  background: #edf2f7;
}
.dot.todo {
  background: #cbd5e0;
}
.label {
  font-size: 12px;
  line-height: 1.35;
  color: var(--primary-dark, #2c5282);
  white-space: normal;
  overflow: visible;
  word-break: break-word;
}
@keyframes pulse {
  0%,
  100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.15);
  }
}
</style>
