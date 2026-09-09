<template>
  <div>
    <div class="bar">
      <h2>材料目录</h2>
      <button @click="load">刷新</button>
      <button type="button" :disabled="auditBusy || !visiblePackCount" @click="auditVisible">
        {{ auditBusy ? "审核中…" : "审核当前列表" }}
      </button>
      <label class="search-field">
        搜索材料
        <input
          v-model="packSearch"
          type="search"
          placeholder="包名 / 领域 / 路径"
          autocomplete="off"
        />
      </label>
      <div class="filter-wrap" v-click-outside="closeFilter">
        <button type="button" :class="{ active: filterOpen }" @click="filterOpen = !filterOpen">筛选</button>
        <div v-if="filterOpen" class="filter-panel" role="dialog" aria-label="材料筛选">
          <div class="filter-panel-head">
            <strong>材料筛选</strong>
          </div>
          <h4>领域</h4>
          <div class="drawer-actions">
            <button type="button" @click="selectAllDomains">全选</button>
            <button type="button" @click="selectedDomains = []">清空</button>
          </div>
          <n-checkbox-group v-model:value="selectedDomains">
            <div v-for="d in domainOptions" :key="d.value" class="check-row">
              <n-checkbox :value="d.value" :label="d.label" />
            </div>
          </n-checkbox-group>

          <h4 class="mt">状态</h4>
          <div class="drawer-actions">
            <button type="button" @click="selectAllStatuses">全选</button>
            <button type="button" @click="selectedStatuses = []">清空</button>
          </div>
          <n-checkbox-group v-model:value="selectedStatuses">
            <div v-for="s in MATERIAL_STATUS_OPTIONS" :key="s.value" class="check-row">
              <n-checkbox :value="s.value" :label="s.label" />
            </div>
          </n-checkbox-group>
        </div>
      </div>
      <span class="hint">已显示 {{ visiblePackCount }} / {{ totalPackCount }} 包</span>
    </div>
    <p class="note">
      请按领域 → 主题包放入 pdf/html 与对应 md，并维护 CATALOG.json。结构检查在本页完成；「审核」会调用大模型按选材标准写回
      CATALOG.llm_audit，不拦入队。
    </p>
    <p v-if="auditMessage" class="note" :class="{ err: auditError }">{{ auditMessage }}</p>
    <pre v-if="data.readme" class="readme">{{ data.readme }}</pre>

    <div v-for="domain in visibleDomains" :key="domain.domain_key" class="domain">
      <h3>{{ domain.domain }} <small>{{ domain.domain_key }}</small></h3>
      <table>
        <thead>
          <tr>
            <th>包</th>
            <th>状态</th>
            <th>审核</th>
            <th>提示</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="pack in domain.packs" :key="pack.path">
            <td>
              <strong>{{ pack.pack }}</strong>
              <div class="muted">{{ pack.path }}</div>
            </td>
            <td>{{ pack.status }}</td>
            <td>
              <span v-if="pack.llm_audit?.status" class="audit-chip" :class="'tone-' + pack.llm_audit.status">
                {{ auditLabel(pack.llm_audit.status) }}
              </span>
              <span v-else class="muted">未审核</span>
              <div v-if="pack.llm_audit?.summary" class="muted audit-summary">{{ pack.llm_audit.summary }}</div>
            </td>
            <td>{{ pack.hint }}</td>
            <td>
              <button type="button" class="link" :disabled="auditBusy" @click="auditOne(pack, domain.domain_key)">
                审核
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="!visibleDomains.length" class="muted empty">
      {{ packSearch.trim() ? "没有匹配当前搜索的材料包。" : "当前筛选无材料包。" }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onDeactivated, onMounted, onUnmounted, ref, watch, type Directive } from "vue";
import { NCheckbox, NCheckboxGroup } from "naive-ui";
import { apiGet, apiPost, apiPut, matchesMaterialQuery } from "../api";

const props = defineProps<{ initialPrefs?: any }>();
const emit = defineEmits(["prefsSaved"]);

const vClickOutside: Directive = {
  mounted(el, binding) {
    (el as any).__clickOutside = (ev: MouseEvent) => {
      if (!el.contains(ev.target as Node)) binding.value?.(ev);
    };
    document.addEventListener("mousedown", (el as any).__clickOutside);
  },
  unmounted(el) {
    document.removeEventListener("mousedown", (el as any).__clickOutside);
  },
};

const MATERIAL_STATUS_OPTIONS = [
  { value: "READY", label: "READY（就绪）" },
  { value: "IN_PROGRESS", label: "IN_PROGRESS（生产中）" },
  { value: "USED", label: "USED_*（已用于合格样例）" },
  { value: "GATE_FAILED", label: "GATE_FAILED_*（门禁失败）" },
  { value: "OTHER", label: "其他" },
] as const;

const ALL_STATUS_KEYS = MATERIAL_STATUS_OPTIONS.map((o) => o.value);

function statusesFromPrefs(prefs: any): string[] | null {
  const saved = prefs?.materials?.selected_statuses;
  if (!Array.isArray(saved)) return null;
  return saved.filter((s: string) => ALL_STATUS_KEYS.includes(s as any));
}

function domainsFromPrefs(prefs: any): string[] | null {
  const saved = prefs?.materials?.selected_domains;
  if (!Array.isArray(saved)) return null;
  return saved.map(String);
}

const data = ref<any>({ domains: [] });
const filterOpen = ref(false);
const packSearch = ref("");
const auditBusy = ref(false);
const auditMessage = ref("");
const auditError = ref(false);
function closeFilter() {
  filterOpen.value = false;
}
const statusHydrated = statusesFromPrefs(props.initialPrefs);
const selectedDomains = ref<string[]>([]);
const selectedStatuses = ref<string[]>(statusHydrated ?? [...ALL_STATUS_KEYS]);
const domainsInitialized = ref(false);
const prefsReady = ref(false);
const savedDomains = ref<string[] | null>(domainsFromPrefs(props.initialPrefs));
let saveTimer: number | undefined;

function materialStatusBucket(status: string): string {
  const s = String(status || "").toUpperCase();
  if (!s || s === "READY" || s === "OK" || s === "SELECTED") return "READY";
  if (s === "IN_PROGRESS") return "IN_PROGRESS";
  if (s.startsWith("USED_")) return "USED";
  if (s.startsWith("GATE_FAILED")) return "GATE_FAILED";
  return "OTHER";
}

const domainOptions = computed(() =>
  (data.value.domains || []).map((d: any) => ({
    value: String(d.domain_key || ""),
    label: `${d.domain || d.domain_key} (${d.domain_key})`,
  }))
);

const totalPackCount = computed(() =>
  (data.value.domains || []).reduce((n: number, d: any) => n + (d.packs?.length || 0), 0)
);

const visibleDomains = computed(() => {
  const domainSet = new Set(selectedDomains.value);
  const statusSet = new Set(selectedStatuses.value);
  const q = packSearch.value;
  return (data.value.domains || [])
    .filter((d: any) => domainSet.has(String(d.domain_key || "")))
    .map((d: any) => ({
      ...d,
      packs: (d.packs || []).filter((p: any) => {
        if (!statusSet.has(materialStatusBucket(p.status))) return false;
        return matchesMaterialQuery(
          q,
          p.pack,
          p.path,
          p.slug,
          p.status,
          p.hint,
          d.domain,
          d.domain_key,
          p.domain_key
        );
      }),
    }))
    .filter((d: any) => d.packs.length > 0);
});

const visiblePackCount = computed(() =>
  visibleDomains.value.reduce((n: number, d: any) => n + (d.packs?.length || 0), 0)
);

function selectAllDomains() {
  selectedDomains.value = domainOptions.value.map((d: { value: string }) => d.value);
}
function selectAllStatuses() {
  selectedStatuses.value = [...ALL_STATUS_KEYS];
}

function persistFilters(immediate = false) {
  if (!prefsReady.value) return;
  const payload = {
    materials: {
      selected_domains: [...selectedDomains.value],
      selected_statuses: [...selectedStatuses.value],
    },
  };
  const run = () => {
    apiPut("/api/ui-prefs", payload)
      .then((prefs) => emit("prefsSaved", prefs))
      .catch(() => undefined);
  };
  if (saveTimer) window.clearTimeout(saveTimer);
  saveTimer = undefined;
  if (immediate) {
    run();
    return;
  }
  saveTimer = window.setTimeout(run, 300);
}

watch(domainOptions, (opts) => {
  if (domainsInitialized.value || !opts.length) return;
  if (savedDomains.value && savedDomains.value.length) {
    const allowed = new Set(opts.map((o: { value: string }) => o.value));
    selectedDomains.value = savedDomains.value.filter((d) => allowed.has(d));
    if (!selectedDomains.value.length) {
      selectedDomains.value = opts.map((o: { value: string }) => o.value);
    }
  } else if (savedDomains.value && savedDomains.value.length === 0) {
    selectedDomains.value = [];
  } else {
    selectedDomains.value = opts.map((o: { value: string }) => o.value);
  }
  domainsInitialized.value = true;
});

watch([selectedDomains, selectedStatuses], () => persistFilters(false), { deep: true });
onDeactivated(() => persistFilters(true));
onUnmounted(() => persistFilters(true));

async function load() {
  data.value = await apiGet("/api/materials");
}

function auditLabel(status: string) {
  if (status === "pass") return "通过";
  if (status === "warn") return "警告";
  if (status === "fail") return "不通过";
  return status;
}

async function waitAuditDone() {
  await new Promise((resolve) => window.setTimeout(resolve, 400));
  for (let i = 0; i < 180; i++) {
    const state = await apiGet("/api/run/state");
    if (!state?.audit_busy) {
      await load();
      return state?.last_audit;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 800));
  }
  await load();
  return null;
}

async function startAudit(packs: Array<{ domain_key: string; pack: string }>) {
  if (!packs.length) return;
  auditBusy.value = true;
  auditError.value = false;
  auditMessage.value = `正在审核 ${packs.length} 个材料包…`;
  try {
    await apiPost("/api/materials/audit", { packs });
    const result = await waitAuditDone();
    const ok = Number(result?.count || 0);
    const fail = (result?.results || []).filter((r: any) => !r.ok).length;
    auditMessage.value = fail ? `审核结束：成功 ${ok}，失败 ${fail}` : `审核结束：成功 ${ok}`;
    auditError.value = Boolean(fail);
  } catch (err: any) {
    auditError.value = true;
    auditMessage.value = err?.message || String(err);
  } finally {
    auditBusy.value = false;
  }
}

function auditOne(pack: any, domainKey: string) {
  return startAudit([{ domain_key: String(pack.domain_key || domainKey), pack: String(pack.pack || "") }]);
}

function auditVisible() {
  const packs: Array<{ domain_key: string; pack: string }> = [];
  for (const domain of visibleDomains.value) {
    for (const pack of domain.packs || []) {
      packs.push({
        domain_key: String(pack.domain_key || domain.domain_key || ""),
        pack: String(pack.pack || ""),
      });
    }
  }
  return startAudit(packs);
}

onMounted(async () => {
  try {
    const prefs = await apiGet("/api/ui-prefs");
    const domainsSaved = domainsFromPrefs(prefs);
    const statusesSaved = statusesFromPrefs(prefs);
    if (domainsSaved) savedDomains.value = domainsSaved;
    if (statusesSaved) selectedStatuses.value = statusesSaved;
    if (prefs) emit("prefsSaved", prefs);
  } catch {
    /* defaults */
  } finally {
    prefsReady.value = true;
  }
  await load();
});
</script>

<style scoped>
.bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  position: relative;
  z-index: 2;
}
.filter-wrap {
  position: relative;
  display: inline-flex;
}
.search-field {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}
.search-field input[type="search"] {
  width: min(260px, 48vw);
  min-width: 160px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--primary);
  background: #fff;
}
.filter-wrap > button.active {
  border-color: var(--primary);
  color: var(--primary);
  background: #ebf8ff;
}
.filter-panel {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 20;
  min-width: 280px;
  max-width: min(380px, 80vw);
  max-height: min(480px, 65vh);
  overflow: auto;
  padding: 12px 14px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(26, 54, 93, 0.12);
}
.filter-panel-head {
  margin-bottom: 8px;
}
.filter-panel-head strong {
  font-size: 13px;
  color: var(--primary-dark);
}
h2,
h3,
h4 {
  color: var(--primary-dark);
}
h4 {
  margin: 0 0 8px;
  font-size: 14px;
}
h4.mt {
  margin-top: 20px;
}
.note,
.muted,
.hint,
small {
  color: var(--muted);
}
.hint {
  font-size: 13px;
}
.empty {
  margin-top: 16px;
}
.readme {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 16px;
  white-space: pre-wrap;
  font-size: 13px;
}
.domain {
  margin-top: 20px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 16px;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}
th,
td {
  text-align: left;
  padding: 8px 6px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
button {
  border: 1px solid var(--border);
  background: #fff;
  color: var(--primary);
  border-radius: 8px;
  padding: 6px 12px;
  cursor: pointer;
}
.drawer-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}
.check-row {
  margin-bottom: 8px;
}
.note.err {
  color: #c53030;
}
.audit-chip {
  display: inline-block;
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 600;
}
.audit-chip.tone-pass {
  color: #166534;
  background: #dcfce7;
}
.audit-chip.tone-warn {
  color: #9a3412;
  background: #ffedd5;
}
.audit-chip.tone-fail {
  color: #9f1239;
  background: #ffe4e6;
}
.audit-summary {
  margin-top: 4px;
  font-size: 12px;
  max-width: 280px;
}
button.link {
  border: 0;
  background: transparent;
  color: var(--primary);
  padding: 0;
  font-size: 12px;
  cursor: pointer;
}
button.link:disabled,
button:disabled {
  opacity: 0.45;
}
</style>
