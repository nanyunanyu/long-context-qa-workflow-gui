<template>
  <div>
    <div class="bar">
      <h2>材料目录</h2>
      <el-button @click="load">刷新</el-button>
      <el-dropdown trigger="click" :disabled="jobBusy" @command="onActionSelect">
        <el-button :disabled="jobBusy" title="搜寻材料、审核或入队">
          {{ jobBusy ? jobBusyLabel : "操作" }}
          <el-icon class="el-icon--right"><ArrowDown /></el-icon>
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item
              v-for="opt in actionOptions"
              :key="opt.key"
              :command="opt.key"
              :disabled="opt.disabled"
              :title="opt.title"
              :divided="opt.danger"
            >
              {{ opt.label }}
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button :disabled="!auditBusy" @click="stopAudit">
        {{ auditStopping ? "停止中…" : "停止审核" }}
      </el-button>
      <el-button :disabled="!ingestBusy" @click="stopIngest">
        {{ ingestStopping ? "停止中…" : "停止搜寻" }}
      </el-button>
      <el-alert
        v-if="statusMessage"
        :type="statusError ? 'error' : 'info'"
        :title="statusMessage"
        show-icon
        :closable="false"
        class="audit-alert"
      />
      <span v-if="selectedPackPaths.length" class="hint">
        已选 {{ selectedPackPaths.length }}
        <template v-if="hiddenSelectedCount">（另有 {{ hiddenSelectedCount }} 个被当前筛选隐藏）</template>
      </span>
      <el-input
        v-model="packSearch"
        class="search-input"
        clearable
        placeholder="包名 / 领域 / 路径"
        :prefix-icon="Search"
      />
      <el-popover v-model:visible="filterOpen" trigger="click" placement="bottom-start" :width="300">
        <template #reference>
          <el-button :type="filterOpen ? 'primary' : 'default'">筛选</el-button>
        </template>
        <div class="filter-panel-head">
          <strong>材料筛选</strong>
        </div>
        <h4>领域</h4>
        <div class="drawer-actions">
          <el-button type="primary" link @click="selectAllDomains">全选</el-button>
          <el-button type="primary" link @click="selectedDomains = []">清空</el-button>
        </div>
        <el-checkbox-group v-model="selectedDomains">
          <div v-for="d in domainOptions" :key="d.value" class="check-row">
            <el-checkbox :value="d.value">{{ d.label }}</el-checkbox>
          </div>
        </el-checkbox-group>

        <h4 class="mt">状态</h4>
        <div class="drawer-actions">
          <el-button type="primary" link @click="selectAllStatuses">全选</el-button>
          <el-button type="primary" link @click="selectedStatuses = []">清空</el-button>
        </div>
        <el-checkbox-group v-model="selectedStatuses">
          <div v-for="s in MATERIAL_STATUS_OPTIONS" :key="s.value" class="check-row">
            <el-checkbox :value="s.value">{{ s.label }}</el-checkbox>
          </div>
        </el-checkbox-group>

        <h4 class="mt">审核</h4>
        <div class="drawer-actions">
          <el-button type="primary" link @click="selectAllAudits">全选</el-button>
          <el-button type="primary" link @click="selectedAudits = []">清空</el-button>
        </div>
        <el-checkbox-group v-model="selectedAudits">
          <div v-for="a in MATERIAL_AUDIT_OPTIONS" :key="a.value" class="check-row">
            <el-checkbox :value="a.value">{{ a.label }}</el-checkbox>
          </div>
        </el-checkbox-group>
      </el-popover>
      <span class="hint">已显示 {{ visiblePackCount }} / {{ totalPackCount }} 包</span>
    </div>
    <p class="note">
      「搜寻材料」会先跳过已有种子包，再从 Gutenberg 全文、GNU 手册、IETF RFC 等写死的公开源补货（arXiv API 不通时会跳过）。落盘后请先勾选或删除不需要的包，再点「审核并入队」。审核
      pass/warn 会写入队列但不启动出题，请到看板点「继续」。结构检查在本页完成；单独「审核」只写回 CATALOG.llm_audit，不入队。
    </p>
    <pre v-if="data.readme" class="readme">{{ data.readme }}</pre>

    <FilterSummaryCard
      title="当前筛选汇总"
      unit="包"
      :total="visiblePackCount"
      :groups="materialSummaryGroups"
    />

    <el-card v-for="domain in visibleDomains" :key="domain.domain_key" class="domain" shadow="never">
      <h3>{{ domain.domain }} <small>{{ domain.domain_key }}</small></h3>
      <el-table :data="domain.packs" size="small" :row-class-name="packRowClass">
        <el-table-column min-width="280">
          <template #header>
            <div class="pack-head">
              <el-checkbox
                :model-value="domainAllSelected(domain)"
                :indeterminate="domainSomeSelected(domain)"
                :disabled="jobBusy || !domain.packs?.length"
                @change="toggleDomainPacks(domain)"
              />
              包
            </div>
          </template>
          <template #default="{ row }">
            <div class="pack-cell">
              <el-checkbox
                :model-value="selectedPackSet.has(packPath(row))"
                :disabled="jobBusy"
                @change="togglePack(row)"
              />
              <div class="pack-body">
                <div class="slug-cell">
                  <div class="slug-tags">
                    <el-tag
                      size="small"
                      class="domain-tag"
                      :class="'tone-' + domainTagTone(String(row.domain_key || domain.domain_key || ''))"
                      :title="String(row.domain_key || domain.domain_key || '')"
                    >
                      {{ domain.domain || row.domain_key || domain.domain_key || "—" }}
                    </el-tag>
                    <template v-for="kind in [docKindPresentation(row.doc_count, row.doc_kind)]" :key="row.path + '-dk'">
                      <el-tag
                        v-if="kind.show"
                        size="small"
                        :class="'tone-' + kind.tone"
                        :type="chipTagType(kind.tone)"
                        effect="light"
                        :title="kind.title"
                      >
                        {{ kind.label }}
                      </el-tag>
                    </template>
                  </div>
                  <span class="pack-name" :title="row.pack">{{ row.pack }}</span>
                </div>
                <div class="muted pack-path">{{ row.path }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="140">
          <template #default="{ row }">
            <template v-for="st in [materialStatusPresentation(row.status)]" :key="row.path + '-st'">
              <el-tag size="small" :class="'tone-' + st.bucket" :type="chipTagType(st.bucket)" effect="light" :title="st.title">
                {{ st.label }}
              </el-tag>
              <div v-if="st.detail" class="cell-note">{{ st.detail }}</div>
            </template>
          </template>
        </el-table-column>
        <el-table-column label="审核" width="140">
          <template #default="{ row }">
            <template v-for="au in [auditPresentation(row.llm_audit, isPackAuditing(row, domain))]" :key="row.path + '-au'">
              <el-tag
                size="small"
                class="clickable-tag"
                :class="'tone-' + au.tone"
                :type="chipTagType(au.tone)"
                effect="light"
                :title="au.title"
                @click="openAuditDialog(row, isPackAuditing(row, domain))"
              >
                {{ au.label }}
              </el-tag>
            </template>
          </template>
        </el-table-column>
        <el-table-column label="提示" min-width="160">
          <template #default="{ row }">
            <template v-for="hn in [hintPresentation(row)]" :key="row.path + '-hn'">
              <el-tag size="small" :class="'tone-' + hn.tone" :type="chipTagType(hn.tone)" effect="light" :title="hn.title">
                {{ hn.label }}
              </el-tag>
              <div v-if="hn.detail" class="cell-note">{{ hn.detail }}</div>
            </template>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="center">
          <template #default="{ row }">
            <el-button
              size="small"
              :type="isPackAuditing(row, domain) ? 'primary' : 'default'"
              :loading="isPackAuditing(row, domain)"
              :disabled="jobBusy && !isPackAuditing(row, domain)"
              @click.stop="auditOne(row, domain.domain_key)"
            >
              {{ isPackAuditing(row, domain) ? "审核中" : "审核" }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
    <el-empty
      v-if="!visibleDomains.length"
      :description="packSearch.trim() ? '没有匹配当前搜索的材料包。' : '当前筛选无材料包。'"
    />

    <el-dialog v-model="auditDialog.open" title="材料审核结果" width="640px">
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
      <template #footer>
        <el-button type="primary" @click="closeAuditDialog">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onDeactivated, onMounted, onUnmounted, ref, watch } from "vue";
import { ArrowDown, Search } from "@element-plus/icons-vue";
import { apiGet, apiPost, apiPut, domainTagTone, docKindPresentation, docKindSearchText, matchesMaterialQuery } from "../api";
import { chipTagType, confirmAction, type MenuAction } from "../ui";
import FilterSummaryCard, { type SummaryGroup } from "../components/FilterSummaryCard.vue";

const props = defineProps<{ initialPrefs?: any }>();
const emit = defineEmits(["prefsSaved"]);

const MATERIAL_STATUS_OPTIONS = [
  { value: "READY", label: "READY（就绪）" },
  { value: "IN_PROGRESS", label: "IN_PROGRESS（生产中）" },
  { value: "USED", label: "USED_*（已用于合格样例）" },
  { value: "GATE_FAILED", label: "GATE_FAILED_*（门禁失败）" },
  { value: "OTHER", label: "其他" },
] as const;

const MATERIAL_AUDIT_OPTIONS = [
  { value: "pass", label: "审核通过" },
  { value: "fail", label: "审核未通过" },
  { value: "none", label: "未审核" },
] as const;

const ALL_STATUS_KEYS = MATERIAL_STATUS_OPTIONS.map((o) => o.value);
const ALL_AUDIT_KEYS = MATERIAL_AUDIT_OPTIONS.map((o) => o.value);

function statusesFromPrefs(prefs: any): string[] | null {
  const saved = prefs?.materials?.selected_statuses;
  if (!Array.isArray(saved)) return null;
  return saved.filter((s: string) => ALL_STATUS_KEYS.includes(s as any));
}

function auditsFromPrefs(prefs: any): string[] | null {
  const saved = prefs?.materials?.selected_audits;
  if (!Array.isArray(saved)) return null;
  return saved.filter((s: string) => ALL_AUDIT_KEYS.includes(s as any));
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
const ingestBusy = ref(false);
const auditStopping = ref(false);
const ingestStopping = ref(false);
const auditingPackKeys = ref<string[]>([]);
const ingestingPackKeys = ref<string[]>([]);
const newPackKeys = ref<string[]>([]);
let auditEpoch = 0;
let ingestEpoch = 0;
const selectedPackPaths = ref<string[]>([]);
const statusMessage = ref("");
const statusError = ref(false);
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
const statusHydrated = statusesFromPrefs(props.initialPrefs);
const auditHydrated = auditsFromPrefs(props.initialPrefs);
const selectedDomains = ref<string[]>([]);
const selectedStatuses = ref<string[]>(statusHydrated ?? [...ALL_STATUS_KEYS]);
const selectedAudits = ref<string[]>(auditHydrated ?? [...ALL_AUDIT_KEYS]);
const domainsInitialized = ref(false);
const prefsReady = ref(false);
const savedDomains = ref<string[] | null>(domainsFromPrefs(props.initialPrefs));
let saveTimer: number | undefined;

function auditFilterBucket(audit: any): "pass" | "fail" | "none" {
  const status = String(audit?.status || "").trim().toLowerCase();
  if (status === "pass") return "pass";
  if (status === "fail" || status === "warn") return "fail";
  return "none";
}

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
  const auditSet = new Set(selectedAudits.value);
  const q = packSearch.value;
  return (data.value.domains || [])
    .filter((d: any) => domainSet.has(String(d.domain_key || "")))
    .map((d: any) => ({
      ...d,
      packs: (d.packs || []).filter((p: any) => {
        if (!statusSet.has(materialStatusBucket(p.status))) return false;
        if (!auditSet.has(auditFilterBucket(p.llm_audit))) return false;
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

const materialSummaryGroups = computed<SummaryGroup[]>(() => {
  const domainItems: SummaryGroup["items"] = visibleDomains.value.map((d: any) => ({
    key: String(d.domain_key || ""),
    count: (d.packs || []).length,
    domainKey: String(d.domain_key || ""),
    domainLabel: String(d.domain || d.domain_key || "—"),
  }));
  const statusMap = new Map<string, number>();
  const auditMap = new Map<string, number>();
  for (const domain of visibleDomains.value) {
    for (const pack of domain.packs || []) {
      const st = materialStatusPresentation(pack.status);
      statusMap.set(st.bucket, (statusMap.get(st.bucket) || 0) + 1);
      const audit = auditFilterBucket(pack.llm_audit);
      auditMap.set(audit, (auditMap.get(audit) || 0) + 1);
    }
  }
  const statusItems = MATERIAL_STATUS_OPTIONS.flatMap((opt) => {
    const count = statusMap.get(opt.value) || 0;
    if (!count) return [];
    const pres = materialStatusPresentation(opt.value);
    return [
      {
        key: opt.value,
        count,
        label: pres.label,
        chipTone: pres.bucket,
      },
    ];
  });
  const auditLabel: Record<string, { label: string; tone: string }> = {
    pass: { label: "审核通过", tone: "pass" },
    fail: { label: "审核未通过", tone: "fail" },
    none: { label: "未审核", tone: "pending" },
  };
  const auditItems = MATERIAL_AUDIT_OPTIONS.flatMap((opt) => {
    const count = auditMap.get(opt.value) || 0;
    if (!count) return [];
    const meta = auditLabel[opt.value];
    return [{ key: opt.value, count, label: meta.label, chipTone: meta.tone }];
  });
  return [
    { name: "领域", items: domainItems },
    { name: "状态", items: statusItems },
    { name: "审核", items: auditItems },
  ];
});

const jobBusy = computed(() => auditBusy.value || ingestBusy.value);
const jobBusyLabel = computed(() => {
  if (ingestBusy.value) return ingestStopping.value ? "停止搜寻中…" : "搜寻中…";
  if (auditBusy.value) return auditStopping.value ? "停止审核中…" : "审核中…";
  return "操作";
});

function packPath(pack: any): string {
  return String(pack?.path || `${pack?.domain_key || ""}::${pack?.pack || ""}`);
}

function packAuditKey(domainKey: string, pack: string): string {
  return `${domainKey}::${pack}`;
}

const selectedPackSet = computed(() => new Set(selectedPackPaths.value));
const auditingPackSet = computed(() => new Set(auditingPackKeys.value));

function isPackAuditing(pack: any, domain: any): boolean {
  const domainKey = String(pack?.domain_key || domain?.domain_key || "");
  const name = String(pack?.pack || "");
  return Boolean(domainKey && name && auditingPackSet.value.has(packAuditKey(domainKey, name)));
}

const visiblePackPathSet = computed(() => {
  const paths = new Set<string>();
  for (const domain of visibleDomains.value) {
    for (const pack of domain.packs || []) paths.add(packPath(pack));
  }
  return paths;
});

const hiddenSelectedCount = computed(
  () => selectedPackPaths.value.filter((path) => !visiblePackPathSet.value.has(path)).length
);

function domainSelectedCount(domain: any): number {
  return (domain?.packs || []).filter((pack: any) => selectedPackSet.value.has(packPath(pack))).length;
}

function domainAllSelected(domain: any): boolean {
  const packs = domain?.packs || [];
  return packs.length > 0 && domainSelectedCount(domain) === packs.length;
}

function domainSomeSelected(domain: any): boolean {
  const n = domainSelectedCount(domain);
  return n > 0 && n < (domain?.packs || []).length;
}

function togglePack(pack: any) {
  const key = packPath(pack);
  const next = new Set(selectedPackPaths.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  selectedPackPaths.value = [...next];
}

function toggleDomainPacks(domain: any) {
  const keys = (domain?.packs || []).map((pack: any) => packPath(pack));
  const next = new Set(selectedPackPaths.value);
  if (keys.length && keys.every((key: string) => next.has(key))) {
    keys.forEach((key: string) => next.delete(key));
  } else {
    keys.forEach((key: string) => next.add(key));
  }
  selectedPackPaths.value = [...next];
}

function pruneSelection() {
  const alive = new Set<string>();
  for (const domain of data.value.domains || []) {
    for (const pack of domain.packs || []) alive.add(packPath(pack));
  }
  selectedPackPaths.value = selectedPackPaths.value.filter((path) => alive.has(path));
}

function withCount(label: string, count: number) {
  return count ? `${label}（${count}）` : label;
}

const auditActionOptions = computed<MenuAction[]>(() => [
  {
    label: "搜寻材料",
    key: "ingest",
    disabled: jobBusy.value,
    title: "跳过已有种子后，从 Gutenberg / GNU 手册 / RFC 等可达公开源补货",
  },
  {
    label: withCount("审核当前列表", visiblePackCount.value),
    key: "visible",
    disabled: jobBusy.value || !visiblePackCount.value,
    title: visiblePackCount.value ? "审核当前筛选可见的全部材料包" : "当前列表没有可审核的材料包",
  },
  {
    label: withCount("审核勾选项", selectedPackPaths.value.length),
    key: "selected",
    disabled: jobBusy.value || !selectedPackPaths.value.length,
    title: selectedPackPaths.value.length ? "审核已勾选的材料包" : "请先勾选材料包",
  },
  {
    label: withCount("审核并入队", selectedPackPaths.value.length),
    key: "audit-enqueue",
    disabled: jobBusy.value || !selectedPackPaths.value.length,
    title: selectedPackPaths.value.length
      ? "审核勾选包，将 pass/warn 写入队列（不启动出题）"
      : "请先勾选材料包",
  },
  {
    label: withCount("移除选中包", selectedPackPaths.value.length),
    key: "delete",
    disabled: jobBusy.value || !selectedPackPaths.value.length,
    danger: true,
    title: selectedPackPaths.value.length ? "从工作区删除已勾选材料包目录" : "请先勾选材料包",
  },
]);

const actionOptions = auditActionOptions;

function onActionSelect(key: string | number) {
  if (key === "ingest") return startIngest();
  if (key === "visible") return auditVisible();
  if (key === "selected") return auditSelected();
  if (key === "audit-enqueue") return auditEnqueueSelected();
  if (key === "delete") return deleteSelected();
}

function selectAllDomains() {
  selectedDomains.value = domainOptions.value.map((d: { value: string }) => d.value);
}
function selectAllStatuses() {
  selectedStatuses.value = [...ALL_STATUS_KEYS];
}
function selectAllAudits() {
  selectedAudits.value = [...ALL_AUDIT_KEYS];
}

function persistFilters(immediate = false) {
  if (!prefsReady.value) return;
  const payload = {
    materials: {
      selected_domains: [...selectedDomains.value],
      selected_statuses: [...selectedStatuses.value],
      selected_audits: [...selectedAudits.value],
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

watch([selectedDomains, selectedStatuses, selectedAudits], () => persistFilters(false), { deep: true });
onDeactivated(() => persistFilters(true));
onUnmounted(() => persistFilters(true));

async function load(opts: { clearHighlight?: boolean } = {}) {
  data.value = await apiGet("/api/materials");
  pruneSelection();
  if (opts.clearHighlight !== false) {
    newPackKeys.value = [];
  }
}

function auditLabel(status: string) {
  if (status === "pass") return "通过";
  if (status === "warn") return "警告";
  if (status === "fail") return "不通过";
  if (status === "running") return "审核中";
  return status || "未审核";
}

function auditPresentation(
  audit: any,
  auditing = false
): {
  tone: string;
  label: string;
  summary: string;
  title: string;
} {
  if (auditing) {
    return { tone: "running", label: "审核中", summary: "", title: "正在进行 LLM 选材审核" };
  }
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
  if (status === "running") return "正在审核";
  return "尚未进行 LLM 选材审核";
}

function openAuditDialog(pack: any, auditing = false) {
  const audit = pack?.llm_audit;
  const status = auditing ? "running" : String(audit?.status || "").trim().toLowerCase();
  const pres = auditPresentation(audit, auditing);
  const checks = audit?.checks && typeof audit.checks === "object" ? audit.checks : {};
  const hasAudit = Boolean(status) && !auditing;
  auditDialog.value = {
    open: true,
    pack: String(pack?.pack || ""),
    tone: pres.tone,
    headline: auditHeadline(status),
    summary: auditing
      ? "该材料包正在进行 LLM 选材审核，完成后会写回结果。"
      : hasAudit
        ? String(audit?.summary || "").trim()
        : "尚未进行 LLM 选材审核",
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

function applyAuditProgress(state: any) {
  if (!state?.audit_busy) {
    auditingPackKeys.value = [];
    return;
  }
  const pending = Array.isArray(state.audit_pending) ? state.audit_pending : [];
  if (pending.length) {
    auditingPackKeys.value = pending.map((item: any) =>
      packAuditKey(String(item?.domain_key || ""), String(item?.pack || ""))
    );
  }
}

function packRowClass({ row }: { row: any }) {
  const domainKey = String(row?.domain_key || "");
  const name = String(row?.pack || "");
  if (domainKey && name && newPackKeys.value.includes(packAuditKey(domainKey, name))) {
    return "pack-row-new";
  }
  return "";
}

function selectedPackSelectors(): Array<{ domain_key: string; pack: string }> {
  const want = new Set(selectedPackPaths.value);
  const packs: Array<{ domain_key: string; pack: string }> = [];
  for (const domain of data.value.domains || []) {
    for (const pack of domain.packs || []) {
      if (!want.has(packPath(pack))) continue;
      packs.push({
        domain_key: String(pack.domain_key || domain.domain_key || ""),
        pack: String(pack.pack || ""),
      });
    }
  }
  return packs;
}

function applyIngestProgress(state: any) {
  if (!state?.ingest_busy) {
    ingestingPackKeys.value = [];
    return;
  }
  const current = state.ingest_current;
  const theme = String(current?.theme || "").trim();
  if (theme) statusMessage.value = theme;
  const pending = Array.isArray(state.ingest_pending) ? state.ingest_pending : [];
  const keys = pending.map((item: any) =>
    packAuditKey(String(item?.domain_key || ""), String(item?.pack || ""))
  );
  if (current?.domain_key && current?.pack) {
    keys.unshift(packAuditKey(String(current.domain_key || ""), String(current.pack || "")));
  }
  ingestingPackKeys.value = keys.filter(Boolean);
}

async function reloadMaterialsQuietly() {
  try {
    await load({ clearHighlight: false });
  } catch {
    /* keep waiting for the in-flight job */
  }
}

async function waitAuditDone() {
  await new Promise((resolve) => window.setTimeout(resolve, 400));
  for (let i = 0; i < 20000; i++) {
    const state = await apiGet("/api/run/state");
    if (state?.audit_stopping) {
      auditStopping.value = true;
    }
    applyAuditProgress(state);
    if (!state?.audit_busy) {
      auditingPackKeys.value = [];
      await reloadMaterialsQuietly();
      return state?.last_audit;
    }
    if (i % 3 === 0) await reloadMaterialsQuietly();
    await new Promise((resolve) => window.setTimeout(resolve, 800));
  }
  auditingPackKeys.value = [];
  await reloadMaterialsQuietly();
  return null;
}

async function waitIngestDone(runId: string) {
  await new Promise((resolve) => window.setTimeout(resolve, 400));
  for (let i = 0; i < 20000; i++) {
    const state = await apiGet("/api/run/state");
    if (state?.ingest_stopping) {
      ingestStopping.value = true;
    }
    applyIngestProgress(state);
    const last = state?.last_ingest;
    const matched = Boolean(runId) && String(last?.ingest_run_id || "") === String(runId);
    if (matched && !state?.ingest_busy) {
      ingestingPackKeys.value = [];
      await reloadMaterialsQuietly();
      return last;
    }
    if (i % 3 === 0) await reloadMaterialsQuietly();
    await new Promise((resolve) => window.setTimeout(resolve, 800));
  }
  ingestingPackKeys.value = [];
  await reloadMaterialsQuietly();
  return null;
}

function summarizeAudit(result: any, verb: string) {
  if (result?.error && !Array.isArray(result?.results)) {
    return { error: true, message: `${verb}失败：${result.error}` };
  }
  const rows = result?.results || [];
  const ok = Number(result?.count || 0);
  const skipped = Number(result?.skipped || rows.filter((r: any) => r.stopped).length);
  const fail = rows.filter((r: any) => !r.ok && !r.stopped).length;
  if (result?.stopped) {
    return {
      error: Boolean(fail),
      message: fail
        ? `${verb}已停止：成功 ${ok}，失败 ${fail}，跳过 ${skipped}`
        : `${verb}已停止：成功 ${ok}，跳过 ${skipped}`,
    };
  }
  if (!result) {
    return { error: true, message: `${verb}超时，请刷新后重试。` };
  }
  return {
    error: Boolean(fail),
    message: fail ? `${verb}结束：成功 ${ok}，失败 ${fail}` : `${verb}结束：成功 ${ok}`,
  };
}

async function startAudit(packs: Array<{ domain_key: string; pack: string }>) {
  if (!packs.length) return;
  if (jobBusy.value) {
    const already = packs.every((p) =>
      auditingPackSet.value.has(packAuditKey(p.domain_key, p.pack))
    );
    if (!already) {
      statusError.value = true;
      statusMessage.value = "已有材料任务在运行，请等待结束或点停止。";
    }
    return;
  }
  const epoch = ++auditEpoch;
  auditBusy.value = true;
  auditStopping.value = false;
  statusError.value = false;
  auditingPackKeys.value = packs.map((p) => packAuditKey(p.domain_key, p.pack));
  statusMessage.value = `正在审核 ${packs.length} 个材料包…`;
  try {
    await apiPost("/api/materials/audit", { packs });
    const result = await waitAuditDone();
    if (epoch !== auditEpoch) return;
    const summary = summarizeAudit(result, "审核");
    statusError.value = summary.error;
    statusMessage.value = summary.message;
  } catch (err: any) {
    if (epoch !== auditEpoch) return;
    statusError.value = true;
    statusMessage.value = err?.message || String(err);
  } finally {
    if (epoch === auditEpoch) {
      auditBusy.value = false;
      auditStopping.value = false;
      auditingPackKeys.value = [];
    }
  }
}

async function startIngest() {
  if (jobBusy.value) {
    statusError.value = true;
    statusMessage.value = "已有材料任务在运行，请等待结束或点停止。";
    return;
  }
  const epoch = ++ingestEpoch;
  ingestBusy.value = true;
  ingestStopping.value = false;
  statusError.value = false;
  statusMessage.value = "正在搜寻 Gutenberg / GNU 手册 / RFC…";
  try {
    const started = await apiPost("/api/materials/ingest", {});
    const result = await waitIngestDone(String(started?.ingest_run_id || ""));
    if (epoch !== ingestEpoch) return;
    if (result?.error && !Array.isArray(result?.results)) {
      statusError.value = true;
      statusMessage.value = `搜寻失败：${result.error}`;
      return;
    }
    const collected = Array.isArray(result?.collected) ? result.collected : [];
    newPackKeys.value = collected.map((item: any) =>
      packAuditKey(String(item?.domain_key || ""), String(item?.pack || ""))
    );
    await load({ clearHighlight: false });
    const skipped = Number(result?.skipped || 0);
    const failed = Number(result?.failed || 0);
    const discovered = Number(result?.discovered || 0);
    const discoverFailed = Number(result?.discover_failed || 0);
    const ok = Number(result?.count || collected.length);
    const discoveredBit = discovered ? `（站点搜寻 ${discovered}）` : "";
    if (result?.stopped) {
      statusMessage.value = `搜寻已停止：新入库 ${ok}${discoveredBit}，跳过已有种子 ${skipped}` + (failed ? `，失败 ${failed}` : "");
      statusError.value = Boolean(failed);
    } else if (!result) {
      statusError.value = true;
      statusMessage.value = "搜寻超时，请刷新后重试。";
    } else if (!ok) {
      statusError.value = Boolean(failed || discoverFailed);
      const why = String(result?.last_error || "").trim();
      statusMessage.value = why
        ? `搜寻结束：没有新的达标材料，跳过已有种子 ${skipped}。最近一次失败：${why}`
        : discoverFailed
          ? `搜寻结束：预设站点没有新的达标材料（候选未通过 ${discoverFailed}），跳过已有种子 ${skipped}。可稍后重试或勾选已有包「审核并入队」。`
          : `搜寻结束：预设站点没有新的达标材料，跳过已有种子 ${skipped}。可稍后重试或勾选已有包「审核并入队」。`;
    } else {
      statusError.value = Boolean(failed);
      statusMessage.value = failed
        ? `搜寻结束：新入库 ${ok}${discoveredBit}，跳过已有种子 ${skipped}，失败 ${failed}。请勾选或删除后再「审核并入队」。`
        : `搜寻结束：新入库 ${ok}${discoveredBit}，跳过已有种子 ${skipped}。请勾选或删除后再「审核并入队」。`;
    }
  } catch (err: any) {
    if (epoch !== ingestEpoch) return;
    statusError.value = true;
    statusMessage.value = err?.message || String(err);
  } finally {
    if (epoch === ingestEpoch) {
      ingestBusy.value = false;
      ingestStopping.value = false;
      ingestingPackKeys.value = [];
    }
  }
}

async function auditEnqueueSelected() {
  const packs = selectedPackSelectors();
  if (!packs.length) return;
  if (jobBusy.value) {
    statusError.value = true;
    statusMessage.value = "已有材料任务在运行，请等待结束或点停止。";
    return;
  }
  const epoch = ++auditEpoch;
  auditBusy.value = true;
  auditStopping.value = false;
  statusError.value = false;
  auditingPackKeys.value = packs.map((p) => packAuditKey(p.domain_key, p.pack));
  statusMessage.value = `正在审核并入队 ${packs.length} 个材料包…`;
  try {
    await apiPost("/api/materials/audit-enqueue", { packs });
    const result = await waitAuditDone();
    if (epoch !== auditEpoch) return;
    if (result?.error && !Array.isArray(result?.results)) {
      statusError.value = true;
      statusMessage.value = `审核并入队失败：${result.error}`;
      return;
    }
    const summary = summarizeAudit(result, "审核");
    const enqueued = Number(result?.enqueue?.enqueued || 0);
    const rejected = Array.isArray(result?.rejected) ? result.rejected.length : 0;
    if (result?.stopped || result?.enqueue?.stopped) {
      statusError.value = summary.error;
      statusMessage.value = `${summary.message}；已入队 ${enqueued}。`;
    } else if (!result) {
      statusError.value = true;
      statusMessage.value = "审核并入队超时，请刷新后重试。";
    } else {
      statusError.value = summary.error;
      statusMessage.value = `审核结束：pass/warn 已入队 ${enqueued} 条` +
        (rejected ? `，未入队 ${rejected}` : "") +
        "。请到看板点「继续」开始出题（不要再 stage 无关包）。";
    }
  } catch (err: any) {
    if (epoch !== auditEpoch) return;
    statusError.value = true;
    statusMessage.value = err?.message || String(err);
  } finally {
    if (epoch === auditEpoch) {
      auditBusy.value = false;
      auditStopping.value = false;
      auditingPackKeys.value = [];
    }
  }
}

async function deleteSelected() {
  const packs = selectedPackSelectors();
  if (!packs.length) return;
  const ok = await confirmAction(
    `将从工作区删除 ${packs.length} 个材料包目录，且无法从本页恢复。确认移除？`,
    "移除材料包"
  );
  if (!ok) return;
  try {
    const result = await apiPost("/api/materials/packs/delete", { packs });
    await load();
    const failed = Number(result?.failed || 0);
    statusError.value = Boolean(failed);
    statusMessage.value = failed
      ? `已移除 ${result?.count || 0} 个包，失败 ${failed}`
      : `已移除 ${result?.count || packs.length} 个材料包`;
  } catch (err: any) {
    statusError.value = true;
    statusMessage.value = err?.message || String(err);
  }
}

async function stopAudit() {
  if (!auditBusy.value || auditStopping.value) return;
  auditStopping.value = true;
  try {
    await apiPost("/api/materials/audit/stop");
    statusMessage.value = "正在停止审核…";
  } catch (err: any) {
    auditStopping.value = false;
    statusError.value = true;
    statusMessage.value = err?.message || String(err);
  }
}

async function stopIngest() {
  if (!ingestBusy.value || ingestStopping.value) return;
  ingestStopping.value = true;
  try {
    await apiPost("/api/materials/ingest/stop");
    statusMessage.value = "正在停止搜寻…";
  } catch (err: any) {
    ingestStopping.value = false;
    statusError.value = true;
    statusMessage.value = err?.message || String(err);
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

function auditSelected() {
  return startAudit(selectedPackSelectors());
}

onMounted(async () => {
  try {
    const prefs = await apiGet("/api/ui-prefs");
    const domainsSaved = domainsFromPrefs(prefs);
    const statusesSaved = statusesFromPrefs(prefs);
    const auditsSaved = auditsFromPrefs(prefs);
    if (domainsSaved) savedDomains.value = domainsSaved;
    if (statusesSaved) selectedStatuses.value = statusesSaved;
    if (auditsSaved) selectedAudits.value = auditsSaved;
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
}
.search-input {
  width: min(260px, 48vw);
}
.audit-alert {
  width: min(420px, 100%);
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
}
.drawer-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.check-row {
  margin-bottom: 8px;
}
.pack-head {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.pack-cell {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}
.pack-body {
  min-width: 0;
  flex: 1;
}
.slug-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
}
.slug-tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}
.pack-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.35;
  color: var(--primary-dark);
  word-break: break-word;
}
.pack-path {
  font-size: 12px;
}
.cell-note {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.45;
  color: var(--muted);
}
.clickable-tag {
  cursor: pointer;
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
.review-verdict.tone-running {
  color: #1d4ed8;
}
:deep(.pack-row-new > td) {
  background: #eff6ff;
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
</style>
