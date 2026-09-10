<template>
  <div>
    <div class="bar">
      <h2>材料目录</h2>
      <button @click="load">刷新</button>
      <button type="button" :disabled="auditBusy || !visiblePackCount" @click="auditVisible">
        {{ auditBusy ? "审核中…" : "审核当前列表" }}
      </button>
      <button type="button" :disabled="!auditBusy" @click="stopAudit">
        {{ auditStopping ? "停止中…" : "停止审核" }}
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
      <table class="materials-table">
        <colgroup>
          <col class="col-pack" />
          <col class="col-status" />
          <col class="col-audit" />
          <col class="col-hint" />
          <col class="col-action" />
        </colgroup>
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
              <div class="slug-cell">
                <div class="slug-tags">
                  <span
                    class="domain-tag"
                    :class="'tone-' + domainTagTone(String(pack.domain_key || domain.domain_key || ''))"
                    :title="String(pack.domain_key || domain.domain_key || '')"
                  >
                    {{ domain.domain || pack.domain_key || domain.domain_key || "—" }}
                  </span>
                  <template v-for="kind in [docKindPresentation(pack.doc_count, pack.doc_kind)]" :key="pack.path + '-dk'">
                    <span v-if="kind.show" class="meta-chip" :class="'tone-' + kind.tone" :title="kind.title">
                      {{ kind.label }}
                    </span>
                  </template>
                </div>
                <span class="pack-name" :title="pack.pack">{{ pack.pack }}</span>
              </div>
              <div class="muted pack-path">{{ pack.path }}</div>
            </td>
            <td>
              <template v-for="st in [materialStatusPresentation(pack.status)]" :key="pack.path + '-st'">
                <span class="meta-chip" :class="'tone-' + st.bucket" :title="st.title">
                  {{ st.label }}
                </span>
                <div v-if="st.detail" class="cell-note">{{ st.detail }}</div>
              </template>
            </td>
            <td>
              <template v-for="au in [auditPresentation(pack.llm_audit)]" :key="pack.path + '-au'">
                <button
                  type="button"
                  class="review-chip"
                  :class="'tone-' + au.tone"
                  :title="au.title"
                  @click="openAuditDialog(pack)"
                >
                  {{ au.label }}
                </button>
              </template>
            </td>
            <td>
              <template v-for="hn in [hintPresentation(pack)]" :key="pack.path + '-hn'">
                <span class="meta-chip" :class="'tone-' + hn.tone" :title="hn.title">
                  {{ hn.label }}
                </span>
                <div v-if="hn.detail" class="cell-note">{{ hn.detail }}</div>
              </template>
            </td>
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

    <div v-if="auditDialog.open" class="modal-backdrop" @click.self="closeAuditDialog">
      <div class="modal-card modal-wide" role="dialog" aria-label="材料审核结果">
        <h3>材料审核结果</h3>
        <p class="muted">
          材料包 <strong>{{ auditDialog.pack }}</strong>
        </p>
        <p class="review-verdict" :class="'tone-' + auditDialog.tone">{{ auditDialog.headline }}</p>
        <p class="modal-reason">{{ auditDialog.summary || "—" }}</p>
        <p v-if="auditDialog.notes" class="modal-reason">{{ auditDialog.notes }}</p>
        <ul class="modal-checks">
          <li>许可可用：{{ boolLabel(auditDialog.licenseOk) }}</li>
          <li>体量足够：{{ boolLabel(auditDialog.enoughLength) }}</li>
          <li>长上下文潜力：{{ boolLabel(auditDialog.longContextPotential) }}</li>
          <li>足够冷门：{{ boolLabel(auditDialog.coldEnough) }}</li>
          <li>模型：{{ auditDialog.model || "—" }}</li>
          <li>时间：{{ auditDialog.reviewedAt || "—" }}</li>
        </ul>
        <div class="modal-actions">
          <button type="button" class="primary" @click="closeAuditDialog">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onDeactivated, onMounted, onUnmounted, ref, watch, type Directive } from "vue";
import { NCheckbox, NCheckboxGroup } from "naive-ui";
import { apiGet, apiPost, apiPut, domainTagTone, docKindPresentation, docKindSearchText, matchesMaterialQuery } from "../api";

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
const auditStopping = ref(false);
const auditMessage = ref("");
const auditError = ref(false);
const auditDialog = ref({
  open: false,
  pack: "",
  tone: "pending",
  headline: "尚未进行 LLM 选材审核",
  summary: "",
  notes: "",
  licenseOk: null as boolean | null,
  enoughLength: null as boolean | null,
  longContextPotential: null as boolean | null,
  coldEnough: null as boolean | null,
  model: "",
  reviewedAt: "",
});
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

function materialStatusPresentation(status: unknown): {
  bucket: string;
  label: string;
  title: string;
  detail: string;
} {
  const raw = String(status || "").trim() || "READY";
  const bucket = materialStatusBucket(raw);
  const labels: Record<string, string> = {
    READY: "就绪",
    IN_PROGRESS: "生产中",
    USED: "已用",
    GATE_FAILED: "门禁失败",
    OTHER: "其他",
  };
  let detail = "";
  if (bucket === "USED" || bucket === "GATE_FAILED") {
    const m = raw.match(/(\d+)\s*$/);
    if (m) detail = `#${m[1]}`;
    else if (raw.toUpperCase() !== bucket) detail = raw;
  } else if (bucket === "OTHER" && raw.toUpperCase() !== "OTHER") {
    detail = raw;
  }
  return {
    bucket,
    label: labels[bucket] || raw,
    title: raw,
    detail,
  };
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
          p.domain_key,
          docKindSearchText(p.doc_count, p.doc_kind)
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
  return status || "未审核";
}

function auditPresentation(audit: any): {
  tone: string;
  label: string;
  summary: string;
  title: string;
} {
  const status = String(audit?.status || "").trim().toLowerCase();
  if (!status) {
    return { tone: "pending", label: "未审核", summary: "", title: "尚未进行 LLM 选材审核" };
  }
  const summary = String(audit?.summary || "").trim();
  const label = auditLabel(status);
  const tone = ["pass", "warn", "fail"].includes(status) ? status : "pending";
  return {
    tone,
    label,
    summary,
    title: summary ? `${label}：${summary}` : label,
  };
}

function hintPresentation(pack: any): {
  tone: string;
  label: string;
  detail: string;
  title: string;
} {
  const issues = Array.isArray(pack?.issues)
    ? pack.issues.map((x: unknown) => String(x || "").trim()).filter(Boolean)
    : [];
  if (issues.length) {
    const detail = issues.join("；");
    return {
      tone: "issue",
      label: issues.length === 1 ? "待补齐" : `待补齐 · ${issues.length}`,
      detail,
      title: detail,
    };
  }
  const bucket = materialStatusBucket(String(pack?.status || ""));
  if (bucket === "USED") {
    return { tone: "used", label: "已用于合格样例", detail: "", title: String(pack?.hint || "已用于合格样例") };
  }
  if (bucket === "GATE_FAILED") {
    return {
      tone: "fail",
      label: "已用于门禁失败样例",
      detail: "",
      title: String(pack?.hint || "已用于门禁失败样例"),
    };
  }
  if (bucket === "IN_PROGRESS") {
    return { tone: "progress", label: "生产中", detail: "", title: String(pack?.hint || "生产中") };
  }
  if (pack?.ready || bucket === "READY") {
    return { tone: "ok", label: "结构就绪", detail: "", title: String(pack?.hint || "READY") };
  }
  const hint = String(pack?.hint || "").trim();
  if (!hint || hint === "READY") {
    return { tone: "ok", label: "结构就绪", detail: "", title: "READY" };
  }
  return { tone: "neutral", label: hint.length > 18 ? `${hint.slice(0, 16)}…` : hint, detail: "", title: hint };
}

function boolLabel(value: boolean | null | undefined) {
  if (value == null) return "—";
  return value ? "是" : "否";
}

function auditHeadline(status: string) {
  if (status === "pass") return "审核通过";
  if (status === "warn") return "审核警告";
  if (status === "fail") return "审核不通过";
  return "尚未进行 LLM 选材审核";
}

function openAuditDialog(pack: any) {
  const audit = pack?.llm_audit;
  const status = String(audit?.status || "").trim().toLowerCase();
  const pres = auditPresentation(audit);
  const checks = audit?.checks && typeof audit.checks === "object" ? audit.checks : {};
  const hasAudit = Boolean(status);
  auditDialog.value = {
    open: true,
    pack: String(pack?.pack || ""),
    tone: pres.tone,
    headline: auditHeadline(status),
    summary: hasAudit ? String(audit?.summary || "").trim() : "尚未进行 LLM 选材审核",
    notes: hasAudit ? String(audit?.notes || "").trim() : "",
    licenseOk: hasAudit ? Boolean(checks.license_ok) : null,
    enoughLength: hasAudit ? Boolean(checks.enough_length) : null,
    longContextPotential: hasAudit ? Boolean(checks.long_context_potential) : null,
    coldEnough: hasAudit ? Boolean(checks.cold_enough) : null,
    model: hasAudit ? String(audit?.model || "") : "",
    reviewedAt: hasAudit ? String(audit?.reviewed_at || "") : "",
  };
}

function closeAuditDialog() {
  auditDialog.value.open = false;
}

async function waitAuditDone() {
  await new Promise((resolve) => window.setTimeout(resolve, 400));
  for (let i = 0; i < 180; i++) {
    const state = await apiGet("/api/run/state");
    if (state?.audit_stopping) {
      auditStopping.value = true;
    }
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
  auditStopping.value = false;
  auditError.value = false;
  auditMessage.value = `正在审核 ${packs.length} 个材料包…`;
  try {
    await apiPost("/api/materials/audit", { packs });
    const result = await waitAuditDone();
    const rows = result?.results || [];
    const ok = Number(result?.count || 0);
    const skipped = Number(result?.skipped || rows.filter((r: any) => r.stopped).length);
    const fail = rows.filter((r: any) => !r.ok && !r.stopped).length;
    if (result?.stopped) {
      auditMessage.value = fail
        ? `审核已停止：成功 ${ok}，失败 ${fail}，跳过 ${skipped}`
        : `审核已停止：成功 ${ok}，跳过 ${skipped}`;
      auditError.value = Boolean(fail);
    } else {
      auditMessage.value = fail ? `审核结束：成功 ${ok}，失败 ${fail}` : `审核结束：成功 ${ok}`;
      auditError.value = Boolean(fail);
    }
  } catch (err: any) {
    auditError.value = true;
    auditMessage.value = err?.message || String(err);
  } finally {
    auditBusy.value = false;
    auditStopping.value = false;
  }
}

async function stopAudit() {
  if (!auditBusy.value || auditStopping.value) return;
  auditStopping.value = true;
  try {
    await apiPost("/api/materials/audit/stop");
    auditMessage.value = "正在停止审核…";
  } catch (err: any) {
    auditStopping.value = false;
    auditError.value = true;
    auditMessage.value = err?.message || String(err);
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
table.materials-table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 14px;
}
.materials-table .col-pack {
  width: 36%;
}
.materials-table .col-status {
  width: 14%;
}
.materials-table .col-audit {
  width: 22%;
}
.materials-table .col-hint {
  width: 22%;
}
.materials-table .col-action {
  width: 6%;
}
.materials-table th,
.materials-table td {
  text-align: left;
  padding: 8px 6px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
  overflow: hidden;
}
.materials-table th:last-child,
.materials-table td:last-child {
  text-align: right;
  white-space: nowrap;
}
.slug-cell {
  display: flex;
  flex-direction: column;
  flex-wrap: nowrap;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
}
.slug-tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
  max-width: 100%;
}
.domain-tag {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
  letter-spacing: 0.02em;
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
.pack-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.35;
  color: var(--primary-dark);
  min-width: 0;
  max-width: 100%;
  white-space: normal;
  overflow: hidden;
  word-break: break-word;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
}
.pack-path {
  margin-top: 4px;
  font-size: 12px;
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
.meta-chip {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
  letter-spacing: 0.02em;
  white-space: nowrap;
  border: 1px solid transparent;
}
.meta-chip.tone-READY,
.meta-chip.tone-ok,
.meta-chip.tone-pass {
  color: #166534;
  background: #dcfce7;
  border-color: #bbf7d0;
}
.meta-chip.tone-IN_PROGRESS,
.meta-chip.tone-progress {
  color: #1d4ed8;
  background: #dbeafe;
  border-color: #bfdbfe;
}
.meta-chip.tone-USED,
.meta-chip.tone-used,
.meta-chip.tone-neutral,
.meta-chip.tone-pending {
  color: #334155;
  background: #e2e8f0;
  border-color: #cbd5e1;
}
.meta-chip.tone-GATE_FAILED,
.meta-chip.tone-fail,
.meta-chip.tone-issue {
  color: #9f1239;
  background: #ffe4e6;
  border-color: #fecdd3;
}
.meta-chip.tone-OTHER,
.meta-chip.tone-warn {
  color: #9a3412;
  background: #ffedd5;
  border-color: #fed7aa;
}
.cell-note {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.45;
  color: var(--muted);
  max-width: 100%;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  overflow: hidden;
  word-break: break-word;
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
button.primary {
  background: var(--primary);
  color: #fff;
  border-color: var(--primary);
}
.review-chip {
  display: inline-flex;
  align-items: center;
  border: 1px solid transparent;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
  letter-spacing: 0.02em;
  white-space: nowrap;
  cursor: pointer;
  background: #e2e8f0;
  color: #334155;
}
.review-chip.tone-pass {
  color: #166534;
  background: #dcfce7;
  border-color: #bbf7d0;
}
.review-chip.tone-fail {
  color: #9f1239;
  background: #ffe4e6;
  border-color: #fecdd3;
}
.review-chip.tone-warn {
  color: #9a3412;
  background: #ffedd5;
  border-color: #fed7aa;
}
.review-chip.tone-pending {
  color: #334155;
  background: #e2e8f0;
  border-color: #cbd5e1;
}
.review-verdict {
  font-weight: 600;
  margin: 8px 0;
}
.review-verdict.tone-pass {
  color: #166534;
}
.review-verdict.tone-fail {
  color: #9f1239;
}
.review-verdict.tone-warn {
  color: #9a3412;
}
.review-verdict.tone-pending {
  color: #334155;
}
.modal-reason {
  white-space: pre-wrap;
  font-size: 13px;
  color: var(--text, #2d3748);
  margin: 8px 0;
}
.modal-checks {
  margin: 0 0 12px;
  padding-left: 18px;
  color: var(--muted);
  font-size: 13px;
}
.modal-wide {
  width: min(640px, 100%);
  max-height: min(80vh, 720px);
  overflow: auto;
}
.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(26, 54, 93, 0.35);
  padding: 16px;
}
.modal-card {
  width: min(420px, 100%);
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 18px 20px;
  box-shadow: 0 12px 32px rgba(26, 54, 93, 0.18);
}
.modal-card h3 {
  margin: 0 0 8px;
  color: var(--primary-dark);
  font-size: 16px;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 14px;
}
</style>
