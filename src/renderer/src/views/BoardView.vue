<template>
  <div>
    <section class="card controls">
      <label>
        本批条数
        <input
          v-if="materialMode === 'select'"
          type="number"
          :value="effectiveSelectCount"
          min="0"
          disabled
          title="自选模式下等于将实际入队的材料包数量"
        />
        <input
          v-else
          v-model.number="limit"
          type="number"
          min="1"
        />
      </label>
      <label>
        并发
        <input v-model.number="workers" type="number" min="1" max="10" />
      </label>
      <label>
        出题类型
        <select v-model="questionType" :disabled="running">
          <option value="short_answer">短答案</option>
          <option value="multiple_choice">选择题</option>
          <option value="auto">自动</option>
        </select>
      </label>
      <label class="check">
        <input v-model="preferCold" type="checkbox" :disabled="materialMode === 'select'" />
        优先冷源
      </label>
      <label class="check">
        <input v-model="retryTechnical" type="checkbox" />
        续跑技术失败
      </label>
      <div class="controls-actions">
        <button class="primary" :disabled="running" @click="start">开始</button>
        <button :disabled="!running" @click="stop">停止</button>
        <button class="danger" :disabled="!running" @click="interrupt">立即中断</button>
        <button :disabled="running" @click="resume">继续</button>
        <span
          class="run-status"
          :class="'tone-' + runStatusTone"
          :title="runStatusTitle"
        >
          <span class="run-status-dot" aria-hidden="true" />
          <span class="run-status-text">{{ runStatusLabel }}</span>
        </span>
        <span v-if="runStatusClaimed != null" class="run-status-claimed">已领 {{ runStatusClaimed }}</span>
      </div>
      <p class="muted rule-hint">{{ questionTypeHint }}</p>
    </section>

    <section class="card">
      <div class="mode-bar">
        <h2>材料来源</h2>
        <button type="button" class="collapse-btn" @click="toggleMaterialsCollapsed">
          {{ materialsCollapsed ? "展开" : "收起" }}
        </button>
        <template v-if="!materialsCollapsed">
          <label class="radio">
            <input v-model="materialMode" type="radio" value="auto" :disabled="running" />
            自动挑选
          </label>
          <label class="radio">
            <input v-model="materialMode" type="radio" value="select" :disabled="running" />
            自选材料
          </label>
          <button type="button" :disabled="running" @click="loadMaterials">刷新材料</button>
          <span v-if="materialMode === 'select'" class="hint">
            已选 {{ effectiveSelectCount }} / {{ flatPacks.length }}
            <template v-if="hiddenSelectedCount">（另有 {{ hiddenSelectedCount }} 个被当前筛选隐藏）</template>
          </span>
        </template>
        <span v-else class="hint">
          {{ materialMode === "select" ? `自选 · 已选 ${effectiveSelectCount}` : "自动挑选" }}
        </span>
      </div>

      <template v-if="!materialsCollapsed && materialMode === 'auto'">
        <p class="muted">开始时按冷源优先从 materials 自动 stage 并入队，条数受上方「本批条数」限制。</p>
      </template>

      <template v-else-if="!materialsCollapsed">
        <div class="select-tools">
          <label class="search-field">
            搜索材料
            <input
              v-model="packSearch"
              type="search"
              placeholder="包名 / 领域 / 路径"
              autocomplete="off"
            />
          </label>
          <label>
            领域
            <select v-model="domainFilter">
              <option value="">全部</option>
              <option v-for="d in domains" :key="d.domain_key" :value="d.domain_key">
                {{ d.domain }} ({{ d.domain_key }})
              </option>
            </select>
          </label>
          <label class="check">
            <input v-model="showUsed" type="checkbox" />
            显示已用/失败包
          </label>
          <label class="check">
            <input v-model="allowRerunUsed" type="checkbox" :disabled="running" />
            允许重跑已用/失败包
          </label>
        </div>
        <p v-if="loadError" class="error">{{ loadError }}</p>
        <div class="pack-list">
          <table>
            <thead>
              <tr>
                <th class="col-pack-check">
                  <input
                    type="checkbox"
                    :checked="allSelectablePacksSelected"
                    :indeterminate.prop="someSelectablePacksSelected && !allSelectablePacksSelected"
                    :disabled="running || !selectableVisiblePacks.length"
                    title="全选 / 全部不选当前列表"
                    @change="toggleSelectAllPacks"
                  />
                </th>
                <th>材料包</th>
                <th>状态</th>
                <th>提示</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="pack in visiblePacks" :key="pack.key">
                <td>
                  <input
                    type="checkbox"
                    :checked="isSelected(pack.key)"
                    :disabled="running || (!pack.ready && !showUsed)"
                    @change="togglePack(pack)"
                  />
                </td>
                <td>
                  <div class="pack-title">
                    <strong>{{ pack.pack }}</strong>
                    <template v-for="kind in [docKindPresentation(pack.doc_count, pack.doc_kind)]" :key="pack.key + '-dk'">
                      <span v-if="kind.show" class="meta-chip" :class="'tone-' + kind.tone" :title="kind.title">
                        {{ kind.label }}
                      </span>
                    </template>
                    <template v-for="au in [packAuditChip(pack)]" :key="pack.key + '-au'">
                      <button
                        v-if="au"
                        type="button"
                        class="review-chip"
                        :class="'tone-' + au.tone"
                        :title="au.title"
                        @click="openPackAuditDialog(pack)"
                      >
                        {{ au.label }}
                      </button>
                    </template>
                  </div>
                  <div class="muted">{{ pack.domain_key }} · {{ pack.path }}</div>
                </td>
                <td>
                  <span :class="pack.ready ? 'ok' : 'warn'">{{ pack.status }}</span>
                </td>
                <td>{{ pack.hint }}</td>
              </tr>
              <tr v-if="!visiblePacks.length">
                <td colspan="4" class="muted">
                  {{
                    packSearch.trim()
                      ? "没有匹配当前搜索的材料包。"
                      : "没有可显示的材料包。请先在「材料」页检查目录，或勾选显示已用包。"
                  }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="muted">
          自选模式下本批条数 = 将实际入队的包数量。默认只计 READY；勾选「允许重跑已用/失败包」才会计入已用包并带
          --include-used。表头勾选框可全选 / 清空当前列表中的可选材料包。
        </p>
        <p v-if="hiddenSelectedCount" class="warn-hint">
          当前筛选下有 {{ hiddenSelectedCount }} 个已选包未显示，本批条数仍计入它们。可点表头勾选框清空，或去掉领域/搜索筛选查看。
        </p>
      </template>
    </section>

    <section class="card">
      <div class="status-bar">
        <div class="status-filters">
          <h2>生产状态</h2>
          <span class="hint">已筛 {{ filteredTasks.length }} / {{ tasks.length }}</span>
          <label class="search-field inline-search">
            搜索材料
            <input
              v-model="taskSearch"
              type="search"
              placeholder="slug / 包名 / 领域"
              autocomplete="off"
            />
          </label>
          <div class="filter-wrap" v-click-outside="closeFilter">
            <button type="button" :class="{ active: filterOpen }" @click="filterOpen = !filterOpen">筛选</button>
            <div v-if="filterOpen" class="filter-panel" role="dialog" aria-label="按队列状态筛选">
              <div class="filter-panel-head">
                <strong>按队列状态筛选</strong>
                <div class="drawer-actions">
                  <button type="button" @click="selectAllStatuses">全选</button>
                  <button type="button" @click="clearStatuses">清空</button>
                </div>
              </div>
              <n-checkbox-group v-model:value="selectedStatuses">
                <div v-for="opt in QUEUE_STATUS_OPTIONS" :key="opt.value" class="check-row">
                  <n-checkbox :value="opt.value" :label="opt.label" />
                </div>
              </n-checkbox-group>
            </div>
          </div>
          <div class="date-filter-group">
            <label class="date-filter" title="按结束日筛选已完结任务；排队/运行中不受日期影响">
              起日
              <n-date-picker
                v-model:value="dateFromTs"
                type="date"
                clearable
                size="small"
                placeholder="选择日期"
                :default-calendar-start-time="dateFromCalendarDefault"
              />
            </label>
            <label class="date-filter" title="按结束日筛选已完结任务；排队/运行中不受日期影响">
              止日
              <n-date-picker
                v-model:value="dateToTs"
                type="date"
                clearable
                size="small"
                placeholder="选择日期"
              />
            </label>
            <button
              v-if="dateFrom || dateTo"
              type="button"
              class="link clear-dates"
              @click="clearDateFilter"
            >
              清空日期
            </button>
          </div>
        </div>
        <div class="status-actions">
          <button type="button" :disabled="running || !selectableFiltered.length" @click="selectAllFiltered">
            全选当前筛选
          </button>
          <button type="button" :disabled="!selectedTaskIds.length" @click="clearTaskSelection">清空选择</button>
          <span v-if="selectedTaskIds.length" class="hint">已选 {{ selectedTaskIds.length }}</span>
          <n-dropdown
            trigger="click"
            placement="bottom-end"
            :options="batchActionOptions"
            :disabled="batchMenuDisabled"
            @select="onBatchActionSelect"
          >
            <button
              type="button"
              class="batch-menu-btn"
              :disabled="batchMenuDisabled"
              title="对已选任务执行批量操作"
            >
              批量操作
              <span class="action-caret" aria-hidden="true">▾</span>
            </button>
          </n-dropdown>
        </div>
      </div>
      <table class="board-table">
        <thead>
          <tr>
            <th class="col-check">
              <input
                type="checkbox"
                :checked="allFilteredSelected"
                :disabled="running || !selectableFiltered.length"
                @change="toggleSelectAllFiltered"
              />
            </th>
            <th class="col-status">状态</th>
            <th class="col-material">材料</th>
            <th class="col-stage">阶段</th>
            <th class="col-pass">通过</th>
            <th class="col-ended">结束</th>
            <th class="col-actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="task in filteredTasks" :key="task.id">
            <td class="col-check">
              <input
                type="checkbox"
                :checked="selectedTaskSet.has(task.id)"
                :disabled="running || !taskSelectable(task)"
                @change="toggleTaskSelect(task)"
              />
            </td>
            <td class="col-status">
              <StatusIcon :state="taskVisualState(task, snapshot)" />
            </td>
            <td class="col-material">
              <div v-for="parts in [taskSlugParts(task)]" :key="task.id + '-slug'" class="slug-cell">
                <div class="slug-tags">
                  <span
                    class="domain-tag"
                    :class="'tone-' + domainTagTone(parts.domainKey)"
                    :title="parts.domainKey"
                  >
                    {{ parts.domainLabel }}
                  </span>
                  <template v-for="kind in [taskDocKindPresentation(task)]" :key="task.id + '-dk'">
                    <span v-if="kind.show" class="meta-chip" :class="'tone-' + kind.tone" :title="kind.title">
                      {{ kind.label }}
                    </span>
                  </template>
                </div>
                <span class="pack-name" :title="task.slug">{{ parts.pack }}</span>
              </div>
              <div v-if="task.duplicate_of" class="warn-hint">
                同材料重复 · 请取消本条，保留 {{ task.duplicate_of }}
              </div>
            </td>
            <td class="col-stage"><StageProgress :task="task" :snapshot="snapshot" /></td>
            <td class="col-pass">
              <div class="pass-stack">
                <StatusIcon
                  :state="passVisualState(task)"
                  :label="passColumnLabel(task)"
                  :title="passColumnTitle(task)"
                />
                <button
                  v-if="autoReviewChip(task)"
                  type="button"
                  class="review-chip"
                  :class="'tone-' + autoReviewChip(task).tone"
                  :title="autoReviewChip(task).title"
                  @click="openAutoReviewFromTask(task)"
                >
                  {{ autoReviewChip(task).label }}
                </button>
              </div>
            </td>
            <td class="col-ended ended">{{ formatEndedAt(task) }}</td>
            <td class="col-actions">
              <n-dropdown
                v-if="taskHasActions(task)"
                trigger="click"
                placement="bottom-end"
                :options="taskActionOptions(task)"
                :disabled="running || statusBusy === task.id || batchBusy"
                @select="(key) => onTaskActionSelect(key, task)"
              >
                <button
                  type="button"
                  class="action-menu-btn"
                  :disabled="running || statusBusy === task.id || batchBusy"
                >
                  操作
                  <span class="action-caret" aria-hidden="true">▾</span>
                </button>
              </n-dropdown>
              <span v-else class="muted">—</span>
            </td>
          </tr>
          <tr v-if="!tasks.length">
            <td colspan="7" class="muted">队列为空。请先选择材料并点开始，或放入材料后用自动挑选。</td>
          </tr>
          <tr v-else-if="!filteredTasks.length">
            <td colspan="7" class="muted">
              {{ taskSearch.trim() ? "没有匹配当前材料搜索的任务。" : "当前筛选无任务。" }}
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <div v-if="rejectDialog.open" class="modal-backdrop" @click.self="closeRejectDialog">
      <div class="modal-card" role="dialog" :aria-label="rejectDialog.batch ? '批量复检不通过' : '复检不通过'">
        <h3>{{ rejectDialog.batch ? "批量复检不通过" : "复检不通过" }}</h3>
        <p class="muted">
          <template v-if="rejectDialog.batch">
            将对已选 <strong>{{ selectedHumanRejectIds.length }}</strong> 条任务打回（通过交付迁出 samples，或待复验迁出 pending-review）。
          </template>
          <template v-else>
            任务 <strong>{{ rejectDialog.slug }}</strong>：{{
              rejectDialog.pendingReview
                ? "将从待复验迁入 failed-samples、标为失败并释放材料。"
                : "将迁出 samples 交付、标为失败并释放材料。"
            }}
          </template>
        </p>
        <label class="modal-field">
          原因（必填）
          <textarea v-model="rejectDialog.reason" rows="3" placeholder="例如：题干剧透 / 金标错误 / 证据不在文中" />
        </label>
        <label class="check modal-check">
          <input v-model="rejectDialog.requeue" type="checkbox" />
          同时改回排队以便同材料重跑
        </label>
        <p v-if="rejectDialog.error" class="error">{{ rejectDialog.error }}</p>
        <div class="modal-actions">
          <button type="button" :disabled="Boolean(statusBusy) || batchBusy" @click="closeRejectDialog">取消</button>
          <button
            type="button"
            class="danger"
            :disabled="Boolean(statusBusy) || batchBusy"
            @click="submitHumanReject"
          >
            {{ rejectDialog.batch ? "确认批量打回" : "确认打回" }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="packAuditDialog.open" class="modal-backdrop" @click.self="closePackAuditDialog">
      <div class="modal-card modal-wide" role="dialog" aria-label="材料审核结果">
        <h3>材料审核结果</h3>
        <p class="muted">
          材料包 <strong>{{ packAuditDialog.pack }}</strong>
        </p>
        <p class="review-verdict" :class="'tone-' + packAuditDialog.tone">{{ packAuditDialog.headline }}</p>
        <p class="modal-reason">{{ packAuditDialog.summary || "—" }}</p>
        <p v-if="packAuditDialog.notes" class="modal-reason">{{ packAuditDialog.notes }}</p>
        <ul class="modal-checks">
          <li>许可可用：{{ boolLabel(packAuditDialog.licenseOk) }}</li>
          <li>体量足够：{{ boolLabel(packAuditDialog.enoughLength) }}</li>
          <li>长上下文潜力：{{ boolLabel(packAuditDialog.longContextPotential) }}</li>
          <li>足够冷门：{{ boolLabel(packAuditDialog.coldEnough) }}</li>
          <li>模型：{{ packAuditDialog.model || "—" }}</li>
          <li>时间：{{ packAuditDialog.reviewedAt || "—" }}</li>
        </ul>
        <div class="modal-actions">
          <button type="button" class="primary" @click="closePackAuditDialog">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="autoReviewDialog.open" class="modal-backdrop" @click.self="closeAutoReviewDialog">
      <div class="modal-card modal-wide" role="dialog" aria-label="自动复验结果">
        <h3>自动复验结果</h3>
        <p class="muted">
          任务 <strong>{{ autoReviewDialog.slug }}</strong>
        </p>
        <p class="review-verdict" :class="'tone-' + autoReviewDialog.tone">{{ autoReviewDialog.headline }}</p>
        <p class="modal-reason">{{ autoReviewDialog.reason || "—" }}</p>
        <ul class="modal-checks">
          <li>金标可被原文支持：{{ boolLabel(autoReviewDialog.goldSupported) }}</li>
          <li>题干可判定：{{ boolLabel(autoReviewDialog.questionOk) }}</li>
          <li>模型：{{ autoReviewDialog.model || "—" }}</li>
          <li>时间：{{ autoReviewDialog.reviewedAt || "—" }}</li>
        </ul>
        <div class="modal-actions">
          <button type="button" class="primary" @click="closeAutoReviewDialog">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="autoReviewBatchDialog.open" class="modal-backdrop" @click.self="closeAutoReviewBatchDialog">
      <div class="modal-card modal-wide" role="dialog" aria-label="批量自动复验汇总">
        <h3>批量自动复验汇总</h3>
        <p class="muted">共 {{ autoReviewBatchDialog.rows.length }} 条</p>
        <table class="summary-table">
          <thead>
            <tr>
              <th>材料</th>
              <th>结论</th>
              <th>理由</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in autoReviewBatchDialog.rows"
              :key="row.taskId"
              class="clickable"
              @click="openAutoReviewRecord(row.record, row.slug)"
            >
              <td>{{ row.slug }}</td>
              <td>
                <span class="review-chip" :class="'tone-' + row.tone">{{ row.label }}</span>
              </td>
              <td class="muted">{{ row.reason }}</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button type="button" class="primary" @click="closeAutoReviewBatchDialog">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onDeactivated, onMounted, onUnmounted, ref, watch, type Directive } from "vue";
import { NCheckbox, NCheckboxGroup, NDatePicker, NDropdown, type DropdownOption } from "naive-ui";
import {
  ALL_QUEUE_STATUSES,
  QUEUE_STATUS_OPTIONS,
  apiGet,
  apiPost,
  apiPut,
  compareBoardTasks,
  domainTagTone,
  docKindPresentation,
  docKindSearchText,
  formatEndedAt,
  isActiveQueueStatus,
  matchesMaterialQuery,
  passColumnLabel,
  passColumnTitle,
  passVisualState,
  taskFilterDay,
  taskMatchesMaterialQuery,
  taskSlugParts,
  taskVisualState,
} from "../api";
import StatusIcon from "../components/StatusIcon.vue";
import StageProgress from "../components/StageProgress.vue";

const props = defineProps<{ snapshot: any; initialPrefs?: any }>();
const emit = defineEmits(["refresh", "prefsSaved"]);

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

function boardStatusesFromPrefs(prefs: any): string[] | null {
  const saved = prefs?.board?.selected_statuses;
  if (!Array.isArray(saved)) return null;
  return saved.filter((s: string) => ALL_QUEUE_STATUSES.includes(s));
}

function boardDateFromPrefs(prefs: any, key: "date_from" | "date_to"): string {
  const raw = prefs?.board?.[key];
  if (typeof raw !== "string") return "";
  return /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : "";
}

const limit = ref(10);
const workers = ref(1);
const questionType = ref<"short_answer" | "multiple_choice" | "auto">(
  ["short_answer", "multiple_choice", "auto"].includes(String(props.initialPrefs?.board?.question_type || ""))
    ? (props.initialPrefs.board.question_type as "short_answer" | "multiple_choice" | "auto")
    : "short_answer"
);
const preferCold = ref(true);
const retryTechnical = ref(false);
const materialMode = ref<"auto" | "select">("auto");
const domains = ref<any[]>([]);
const packDocByPath = computed(() => {
  const map = new Map<string, { doc_count: number; doc_kind: string }>();
  for (const domain of domains.value) {
    for (const pack of domain.packs || []) {
      const path = String(pack.path || "").replace(/\/+$/, "");
      if (!path) continue;
      map.set(path, {
        doc_count: Number(pack.doc_count) || 0,
        doc_kind: String(pack.doc_kind || "unknown"),
      });
    }
  }
  return map;
});

function lookupPackDoc(path: unknown) {
  const key = String(path || "").replace(/\/+$/, "");
  return key ? packDocByPath.value.get(key) : undefined;
}

function taskDocKindPresentation(task: any) {
  const found = lookupPackDoc(task?.materials_pack);
  if (!found) return docKindPresentation(0, "unknown");
  return docKindPresentation(found.doc_count, found.doc_kind);
}

function taskDocKindSearchText(task: any) {
  const found = lookupPackDoc(task?.materials_pack);
  if (!found) return "";
  return docKindSearchText(found.doc_count, found.doc_kind);
}

const domainFilter = ref("");
const packSearch = ref("");
const taskSearch = ref("");
const showUsed = ref(false);
const allowRerunUsed = ref(false);
const selectedKeys = ref<string[]>([]);
const loadError = ref("");
const filterOpen = ref(false);
function closeFilter() {
  filterOpen.value = false;
}
const materialsCollapsed = ref(Boolean(props.initialPrefs?.board?.materials_collapsed));
const hydrated = boardStatusesFromPrefs(props.initialPrefs);
const selectedStatuses = ref<string[]>(hydrated ?? [...ALL_QUEUE_STATUSES]);
const dateFrom = ref(boardDateFromPrefs(props.initialPrefs, "date_from"));
const dateTo = ref(boardDateFromPrefs(props.initialPrefs, "date_to"));

/** Calendar panel opens to current year/month; value stays empty until user picks a day. */
const dateFromCalendarDefault = (() => {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), 1).getTime();
})();

function dayToLocalTs(day: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d).getTime();
}

function localTsToDay(ts: number): string {
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const dateFromTs = computed<number | null>({
  get() {
    return dayToLocalTs(dateFrom.value);
  },
  set(value) {
    dateFrom.value = value == null ? "" : localTsToDay(value);
  },
});

const dateToTs = computed<number | null>({
  get() {
    return dayToLocalTs(dateTo.value);
  },
  set(value) {
    dateTo.value = value == null ? "" : localTsToDay(value);
  },
});

const statusBusy = ref<string | null>(null);
const batchBusy = ref(false);
const selectedTaskIds = ref<string[]>([]);
const prefsReady = ref(false);
const prevRunning = ref(false);
const autoReviewDialog = ref({
  open: false,
  slug: "",
  headline: "",
  tone: "abort",
  reason: "",
  goldSupported: null as boolean | null,
  questionOk: null as boolean | null,
  model: "",
  reviewedAt: "",
});
const autoReviewBatchDialog = ref({
  open: false,
  rows: [] as Array<{
    taskId: string;
    slug: string;
    label: string;
    tone: string;
    reason: string;
    record: any;
  }>,
});
const rejectDialog = ref({
  open: false,
  taskId: "",
  slug: "",
  reason: "",
  requeue: false,
  error: "",
  pendingReview: false,
  batch: false,
});
const packAuditDialog = ref({
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
let statusSaveTimer: number | undefined;
let materialsSaveTimer: number | undefined;

type FlatPack = {
  key: string;
  domain_key: string;
  pack: string;
  path: string;
  status: string;
  hint: string;
  ready: boolean;
  slug: string;
  doc_count: number;
  doc_kind: string;
  llm_audit: any;
};

const tasks = computed(() => props.snapshot?.queue?.tasks || []);
const filteredTasks = computed(() => {
  const allowed = new Set(selectedStatuses.value);
  const from = dateFrom.value || "";
  const to = dateTo.value || "";
  const dateActive = Boolean(from || to);
  const q = taskSearch.value;
  const rows = tasks.value.filter((t: any) => {
    if (!allowed.has(String(t.status || ""))) return false;
    // Hide superseded cancelled duplicates (same materials_pack / slug).
    if (t.board_hidden) return false;
    if (dateActive) {
      // Keep live queue visible; date range only filters finished/settled rows.
      if (!isActiveQueueStatus(t.status)) {
        const day = taskFilterDay(t);
        if (!day) return false;
        if (from && day < from) return false;
        if (to && day > to) return false;
      }
    }
    if (!taskMatchesMaterialQuery(t, q, taskDocKindSearchText(t))) return false;
    return true;
  });
  return [...rows].sort(compareBoardTasks);
});

function clearDateFilter() {
  dateFrom.value = "";
  dateTo.value = "";
}

function taskCanStart(task: any): boolean {
  return Boolean(task?.can_start) || String(task?.status || "") === "queued";
}

function taskSelectable(task: any): boolean {
  return Boolean(
    taskCanStart(task) ||
      task?.can_requeue ||
      task?.can_cancel ||
      task?.can_review_pass ||
      task?.can_human_reject
  );
}
const selectableFiltered = computed(() => filteredTasks.value.filter((t: any) => taskSelectable(t)));
const selectedTaskSet = computed(() => new Set(selectedTaskIds.value));
const selectedRequeueIds = computed(() =>
  selectedTaskIds.value.filter((id) => {
    const t = tasks.value.find((x: any) => x.id === id);
    return t?.can_requeue;
  })
);
const selectedCancelIds = computed(() =>
  selectedTaskIds.value.filter((id) => {
    const t = tasks.value.find((x: any) => x.id === id);
    return t?.can_cancel;
  })
);
const selectedReviewPassIds = computed(() =>
  selectedTaskIds.value.filter((id) => {
    const t = tasks.value.find((x: any) => x.id === id);
    return t?.can_review_pass;
  })
);
const selectedHumanRejectIds = computed(() =>
  selectedTaskIds.value.filter((id) => {
    const t = tasks.value.find((x: any) => x.id === id);
    return t?.can_human_reject;
  })
);
const selectedStartIds = computed(() =>
  selectedTaskIds.value.filter((id) => {
    const t = tasks.value.find((x: any) => x.id === id);
    return taskCanStart(t);
  })
);
const allFilteredSelected = computed(
  () =>
    selectableFiltered.value.length > 0 &&
    selectableFiltered.value.every((t: any) => selectedTaskSet.value.has(t.id))
);
const runStatus = computed(() => props.snapshot?.run?.status || "idle");
const running = computed(() => ["running", "stopping"].includes(runStatus.value));
const reviewReady = computed(() => Boolean(props.snapshot?.keys?.review_ready ?? props.snapshot?.keys?.review));
const batchMenuDisabled = computed(
  () =>
    running.value ||
    batchBusy.value ||
    !(
      selectedStartIds.value.length ||
      selectedRequeueIds.value.length ||
      selectedCancelIds.value.length ||
      selectedReviewPassIds.value.length ||
      selectedHumanRejectIds.value.length
    )
);

const RUN_STATUS_UI: Record<string, { label: string; tone: string }> = {
  idle: { label: "空闲", tone: "idle" },
  running: { label: "运行中", tone: "running" },
  stopping: { label: "停止中", tone: "stopping" },
  interrupted: { label: "已中断", tone: "interrupted" },
};

const runStatusLabel = computed(
  () => RUN_STATUS_UI[runStatus.value]?.label || String(runStatus.value)
);
const runStatusTone = computed(
  () => RUN_STATUS_UI[runStatus.value]?.tone || "idle"
);
const runStatusClaimed = computed(() => {
  if (!["running", "stopping"].includes(runStatus.value)) return null;
  const claimed = props.snapshot?.run?.claimed;
  return claimed == null ? null : claimed;
});
const runStatusTitle = computed(() => {
  const run = props.snapshot?.run || {};
  const bits = [`状态码：${runStatus.value}`];
  if (run.run_id) bits.push(`run_id：${run.run_id}`);
  if (run.claimed != null) bits.push(`已领取：${run.claimed}`);
  if (run.message) bits.push(String(run.message));
  return bits.join(" · ");
});
const questionTypeHint = computed(() => {
  const shared =
    "出题语言与材料一致（英文材料用英语题/金标，中文材料用中文）。判分覆盖金标要点且无错误内容即可，不必字字对应。本批选项只作用于新入队任务。";
  if (questionType.value === "multiple_choice") {
    return `${shared} 本批出选择题：单选四选一，或多选（2–3 个正确项，答案如 A,C）。干扰项须是近形，禁止以上都不是/全选。`;
  }
  if (questionType.value === "auto") {
    return `${shared} 本批由出题模型按材料选择短答、单选或多选。`;
  }
  return `${shared} 本批只出短答案题；短答可以是短语或 1–3 个短句；多槽须答案格式和 alias。`;
});
const selectedSet = computed(() => new Set(selectedKeys.value));

const flatPacks = computed<FlatPack[]>(() => {
  const rows: FlatPack[] = [];
  for (const domain of domains.value) {
    for (const pack of domain.packs || []) {
      const domain_key = String(pack.domain_key || domain.domain_key || "");
      const name = String(pack.pack || "");
      if (!domain_key || !name) continue;
      rows.push({
        key: `${domain_key}::${name}`,
        domain_key,
        pack: name,
        path: String(pack.path || ""),
        status: String(pack.status || ""),
        hint: String(pack.hint || ""),
        ready: Boolean(pack.ready),
        slug: String(pack.slug || `${domain_key}-${name}`),
        doc_count: Number(pack.doc_count) || 0,
        doc_kind: String(pack.doc_kind || "unknown"),
        llm_audit: pack.llm_audit || null,
      });
    }
  }
  return rows;
});

const visiblePacks = computed(() =>
  flatPacks.value.filter((p) => {
    if (domainFilter.value && p.domain_key !== domainFilter.value) return false;
    if (!showUsed.value && !p.ready) return false;
    if (!matchesMaterialQuery(packSearch.value, p.pack, p.domain_key, p.path, p.slug, p.status, p.hint, docKindSearchText(p.doc_count, p.doc_kind))) {
      return false;
    }
    return true;
  })
);

const selectableVisiblePacks = computed(() =>
  visiblePacks.value.filter((p) => p.ready || showUsed.value)
);

const allSelectablePacksSelected = computed(() => {
  const rows = selectableVisiblePacks.value;
  return rows.length > 0 && rows.every((p) => selectedSet.value.has(p.key));
});

const someSelectablePacksSelected = computed(() =>
  selectableVisiblePacks.value.some((p) => selectedSet.value.has(p.key))
);

/** Selected rows that still exist in materials catalog. */
const selectedPackRows = computed(() => {
  const set = new Set(selectedKeys.value);
  return flatPacks.value.filter((p) => set.has(p.key));
});

/** Packs that will actually be staged/enqueued on Start. */
const effectiveSelectedPacks = computed(() => {
  let rows = selectedPackRows.value;
  if (!allowRerunUsed.value) rows = rows.filter((p) => p.ready);
  return rows;
});

const effectiveSelectCount = computed(() => effectiveSelectedPacks.value.length);

const selectedVisibleCount = computed(
  () => visiblePacks.value.filter((p) => selectedSet.value.has(p.key)).length
);

const hiddenSelectedCount = computed(() =>
  Math.max(0, selectedPackRows.value.length - selectedVisibleCount.value)
);

watch(effectiveSelectCount, (n) => {
  if (materialMode.value === "select") {
    // Keep limit ref in sync for resume / mode switch; display uses effectiveSelectCount.
    limit.value = Math.max(1, n || 1);
  }
});

watch(materialMode, (mode) => {
  if (mode === "select") {
    limit.value = Math.max(1, effectiveSelectCount.value || 1);
    if (!domains.value.length) loadMaterials();
  }
});

watch(allowRerunUsed, (allow) => {
  if (allow) return;
  const next = selectedPackRows.value.filter((p) => p.ready).map((p) => p.key);
  if (next.length !== selectedKeys.value.length) {
    selectedKeys.value = next;
  }
});

watch(
  () => flatPacks.value.map((p) => p.key).join("\0"),
  () => {
    const allowed = new Set(flatPacks.value.map((p) => p.key));
    const pruned = selectedKeys.value.filter((k) => allowed.has(k));
    if (pruned.length !== selectedKeys.value.length) {
      selectedKeys.value = pruned;
    }
  }
);

function selectAllStatuses() {
  selectedStatuses.value = [...ALL_QUEUE_STATUSES];
}
function clearStatuses() {
  selectedStatuses.value = [];
}

const DANGER_ACTION_STYLE = "color: #c53030";

function withBatchCount(label: string, count: number): string {
  return count ? `${label}（${count}）` : label;
}

const batchActionOptions = computed<DropdownOption[]>(() => [
  {
    label: withBatchCount("批量开始", selectedStartIds.value.length),
    key: "start",
    disabled: running.value || batchBusy.value || !selectedStartIds.value.length,
    props: {
      title: "领取已选排队任务并开始生产，不再挑选新材料",
    },
  },
  {
    label: withBatchCount("批量改回排队", selectedRequeueIds.value.length),
    key: "requeue",
    disabled: running.value || batchBusy.value || !selectedRequeueIds.value.length,
  },
  {
    label: withBatchCount("批量取消", selectedCancelIds.value.length),
    key: "cancel",
    disabled: running.value || batchBusy.value || !selectedCancelIds.value.length,
    props: { style: DANGER_ACTION_STYLE },
  },
  {
    label: withBatchCount("批量复检通过", selectedReviewPassIds.value.length),
    key: "review_pass",
    disabled: running.value || batchBusy.value || !selectedReviewPassIds.value.length,
    props: {
      title: "对已选 blocked + 复验 0/8 任务逐条补跑消融并入库",
    },
  },
  {
    label: withBatchCount("批量自动复验", selectedReviewPassIds.value.length),
    key: "auto_review",
    disabled: running.value || batchBusy.value || !reviewReady.value || !selectedReviewPassIds.value.length,
    props: {
      title: reviewReady.value
        ? "对已选 复验 0/8 任务调用大模型自动判定并执行通过/打回"
        : "请先在设置中配置复验模型或判分密钥",
    },
  },
  {
    label: withBatchCount("批量复检不通过", selectedHumanRejectIds.value.length),
    key: "human_reject",
    disabled: running.value || batchBusy.value || !selectedHumanRejectIds.value.length,
    props: {
      style: DANGER_ACTION_STYLE,
      title: "对已选通过/待复验任务批量打回",
    },
  },
]);

function onBatchActionSelect(key: string | number) {
  const action = String(key);
  if (action === "start") {
    void startQueuedTasks(selectedStartIds.value);
    return;
  }
  if (action === "requeue") {
    void batchChangeStatus("queued");
    return;
  }
  if (action === "cancel") {
    void batchChangeStatus("cancelled");
    return;
  }
  if (action === "review_pass") {
    void batchReviewPass();
    return;
  }
  if (action === "auto_review") {
    void batchAutoReview();
    return;
  }
  if (action === "human_reject") {
    openBatchHumanReject();
  }
}

function taskHasActions(task: any): boolean {
  return Boolean(
    taskCanStart(task) ||
      task?.can_human_reject ||
      task?.can_review_pass ||
      task?.can_requeue ||
      task?.can_cancel
  );
}

function taskActionOptions(task: any): DropdownOption[] {
  const opts: DropdownOption[] = [];
  if (taskCanStart(task)) {
    opts.push({
      label: "开始生产",
      key: "start",
      disabled: running.value || batchBusy.value,
      props: {
        title: "领取这条排队任务并开始生产，不再挑选新材料",
      },
    });
  }
  if (task.can_human_reject) {
    opts.push({
      label: "复检不通过？",
      key: "human_reject",
      props: {
        style: DANGER_ACTION_STYLE,
        title: "确认题/金标有问题：从待复验或 samples 迁入 failed-samples 并释放材料",
      },
    });
  }
  if (task.can_review_pass) {
    opts.push({
      label: "自动复验",
      key: "auto_review",
      disabled: !reviewReady.value,
      props: {
        title: reviewReady.value
          ? "调用复验模型自动判定：通过则入库，不通过则打回"
          : "请先在设置中配置复验模型或判分密钥",
      },
    });
    opts.push({
      label: "复验通过",
      key: "review_pass",
      props: {
        title: "确认题/金标无误、0 分为模型答错后，补跑消融并以零分复验通过入库",
      },
    });
  }
  if (task.can_requeue) {
    opts.push({ label: "改回排队", key: "requeue" });
  }
  if (task.can_cancel) {
    opts.push({
      label: "取消",
      key: "cancel",
      props: { style: DANGER_ACTION_STYLE },
    });
  }
  return opts;
}

function onTaskActionSelect(key: string | number, task: any) {
  const action = String(key);
  if (action === "start") {
    void startQueuedTasks([task.id]);
    return;
  }
  if (action === "human_reject") {
    void humanReject(task);
    return;
  }
  if (action === "auto_review") {
    void autoReview(task);
    return;
  }
  if (action === "review_pass") {
    void reviewPass(task);
    return;
  }
  if (action === "requeue") {
    void changeStatus(task.id, "queued");
    return;
  }
  if (action === "cancel") {
    void changeStatus(task.id, "cancelled");
  }
}

async function changeStatus(taskId: string, status: "queued" | "cancelled") {
  statusBusy.value = taskId;
  try {
    await apiPost("/api/queue/task/status", {
      task_id: taskId,
      status,
      reason: status === "queued" ? "manual requeue" : "manual cancel",
    });
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => id !== taskId);
    emit("refresh");
  } catch (err: any) {
    alert(err?.message || String(err));
  } finally {
    statusBusy.value = null;
  }
}

async function reviewPass(task: any) {
  const ok = window.confirm(
    `确认「${task.slug}」题/金标无误，0/8 为模型答错？\n将补跑消融并以零分复验通过入库（可能数分钟）。`
  );
  if (!ok) return;
  statusBusy.value = task.id;
  try {
    await apiPost("/api/queue/task/review-pass", { task_id: task.id });
    emit("refresh");
  } catch (err: any) {
    alert(err?.message || String(err));
  } finally {
    statusBusy.value = null;
  }
}

async function humanReject(task: any) {
  rejectDialog.value = {
    open: true,
    taskId: String(task.id || ""),
    slug: String(task.slug || task.id || ""),
    reason: "",
    requeue: false,
    error: "",
    pendingReview: Boolean(task.can_review_pass),
    batch: false,
  };
}

function openBatchHumanReject() {
  if (!selectedHumanRejectIds.value.length) return;
  rejectDialog.value = {
    open: true,
    taskId: "",
    slug: "",
    reason: "",
    requeue: false,
    error: "",
    pendingReview: false,
    batch: true,
  };
}

function closeRejectDialog() {
  if (statusBusy.value === rejectDialog.value.taskId || batchBusy.value) return;
  rejectDialog.value.open = false;
}

async function submitHumanReject() {
  const trimmed = String(rejectDialog.value.reason || "").trim();
  if (!trimmed) {
    rejectDialog.value.error = "请填写复检不通过原因";
    return;
  }
  rejectDialog.value.error = "";
  if (rejectDialog.value.batch) {
    const ids = [...selectedHumanRejectIds.value];
    if (!ids.length) {
      rejectDialog.value.error = "没有可打回的已选任务";
      return;
    }
    batchBusy.value = true;
    try {
      const result = await apiPost("/api/queue/tasks/human-reject", {
        task_ids: ids,
        reason: trimmed,
        requeue: rejectDialog.value.requeue,
      });
      const errCount = result?.errors?.length || 0;
      if (errCount) {
        alert(`已打回 ${result.count || 0} 条，失败 ${errCount} 条`);
      }
      const done = new Set(result?.updated || ids);
      selectedTaskIds.value = selectedTaskIds.value.filter((id) => !done.has(id));
      rejectDialog.value.open = false;
      emit("refresh");
    } catch (err: any) {
      rejectDialog.value.error = err?.message || String(err);
    } finally {
      batchBusy.value = false;
    }
    return;
  }
  const taskId = rejectDialog.value.taskId;
  const requeue = rejectDialog.value.requeue;
  statusBusy.value = taskId;
  try {
    await apiPost("/api/queue/task/human-reject", {
      task_id: taskId,
      reason: trimmed,
      requeue,
    });
    rejectDialog.value.open = false;
    emit("refresh");
  } catch (err: any) {
    rejectDialog.value.error = err?.message || String(err);
  } finally {
    statusBusy.value = null;
  }
}

function toggleMaterialsCollapsed() {
  materialsCollapsed.value = !materialsCollapsed.value;
  persistMaterialsCollapsed();
}

function persistMaterialsCollapsed() {
  if (!prefsReady.value) return;
  if (materialsSaveTimer) window.clearTimeout(materialsSaveTimer);
  materialsSaveTimer = window.setTimeout(() => {
    apiPut("/api/ui-prefs", { board: { materials_collapsed: materialsCollapsed.value } })
      .then((prefs) => emit("prefsSaved", prefs))
      .catch(() => undefined);
  }, 200);
}

function toggleTaskSelect(task: any) {
  if (!taskSelectable(task)) return;
  const set = new Set(selectedTaskIds.value);
  if (set.has(task.id)) set.delete(task.id);
  else set.add(task.id);
  selectedTaskIds.value = [...set];
}

function selectAllFiltered() {
  const set = new Set(selectedTaskIds.value);
  for (const t of selectableFiltered.value) set.add(t.id);
  selectedTaskIds.value = [...set];
}

function clearTaskSelection() {
  selectedTaskIds.value = [];
}

function toggleSelectAllFiltered(ev: Event) {
  const checked = (ev.target as HTMLInputElement).checked;
  if (checked) selectAllFiltered();
  else {
    const drop = new Set(selectableFiltered.value.map((t: any) => t.id));
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => !drop.has(id));
  }
}

async function batchChangeStatus(status: "queued" | "cancelled") {
  const ids = status === "queued" ? selectedRequeueIds.value : selectedCancelIds.value;
  if (!ids.length) return;
  batchBusy.value = true;
  try {
    const result = await apiPost("/api/queue/tasks/status", {
      task_ids: ids,
      status,
      reason: status === "queued" ? "batch requeue" : "batch cancel",
    });
    const errCount = result?.errors?.length || 0;
    if (errCount) {
      alert(`已更新 ${result.count || 0} 条，失败 ${errCount} 条`);
    }
    const done = new Set(result?.updated || ids);
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => !done.has(id));
    emit("refresh");
  } catch (err: any) {
    alert(err?.message || String(err));
  } finally {
    batchBusy.value = false;
  }
}

async function batchReviewPass() {
  const ids = [...selectedReviewPassIds.value];
  if (!ids.length) return;
  const ok = window.confirm(
    `确认对已选 ${ids.length} 条「复验 0/8」任务执行复检通过？\n将逐条补跑消融并以零分复验通过入库（可能较久）。`
  );
  if (!ok) return;
  batchBusy.value = true;
  try {
    const result = await apiPost("/api/queue/tasks/review-pass", { task_ids: ids });
    const skipped = result?.skipped?.length || 0;
    if (skipped) {
      alert(`已启动 ${result.count || 0} 条复检通过；跳过 ${skipped} 条`);
    }
    const done = new Set(result?.task_ids || ids);
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => !done.has(id));
    emit("refresh");
  } catch (err: any) {
    alert(err?.message || String(err));
  } finally {
    batchBusy.value = false;
  }
}

function boolLabel(value: boolean | null | undefined) {
  if (value == null) return "—";
  return value ? "是" : "否";
}

function autoReviewPresentation(record: any) {
  const action = String(record?.action || "");
  const verdict = String(record?.verdict || "");
  if (action === "running") {
    return { label: "自动复验中…", tone: "running", headline: "自动复验进行中" };
  }
  if (action === "aborted") {
    return { label: "自动复验中止", tone: "abort", headline: "自动复验中止，队列未改动" };
  }
  if (action === "error") {
    return { label: "自动复验执行失败", tone: "abort", headline: "判定已出，但后续入库/打回失败" };
  }
  if (verdict === "pass" || action === "promoted") {
    return { label: "自动复验通过", tone: "pass", headline: "通过并入库（题/金标无误，0/8 视为模型答错）" };
  }
  if (verdict === "fail" || action === "rejected") {
    return { label: "自动复验不通过", tone: "fail", headline: "不通过，已打回 failed-samples" };
  }
  return { label: "自动复验", tone: "abort", headline: "自动复验" };
}

function autoReviewChip(task: any) {
  const ar = task?.auto_review;
  const runMsg = String(props.snapshot?.run?.message || "");
  if (!ar) {
    if (running.value && runMsg.includes("auto-review") && task?.can_review_pass) {
      return { label: "自动复验中…", tone: "running", title: "正在调用复验模型" };
    }
    return null;
  }
  const pres = autoReviewPresentation(ar);
  return { ...pres, title: String(ar.reason || pres.headline) };
}

function fillAutoReviewDialog(record: any, slug: string) {
  const pres = autoReviewPresentation(record || {});
  autoReviewDialog.value = {
    open: true,
    slug,
    headline: pres.headline,
    tone: pres.tone,
    reason: String(record?.reason || record?.exec_error || ""),
    goldSupported: record?.gold_supported ?? null,
    questionOk: record?.question_ok ?? null,
    model: String(record?.model || ""),
    reviewedAt: String(record?.reviewed_at || ""),
  };
}

function openAutoReviewFromTask(task: any) {
  fillAutoReviewDialog(task?.auto_review, String(task?.slug || task?.id || ""));
}

function openAutoReviewRecord(record: any, slug: string) {
  fillAutoReviewDialog(record, slug);
}

function closeAutoReviewDialog() {
  autoReviewDialog.value.open = false;
}

function closeAutoReviewBatchDialog() {
  autoReviewBatchDialog.value.open = false;
}

function showAutoReviewLastRun(lr: any) {
  if (!lr || lr.kind !== "auto_review") return;
  if (lr.batch) {
    const rows = (lr.results || []).map((item: any) => {
      const record = item?.auto_review || { reason: item?.error || "", action: item?.ok ? "" : "aborted" };
      const pres = autoReviewPresentation(record);
      return {
        taskId: String(item?.task_id || ""),
        slug: String(item?.slug || item?.task_id || ""),
        label: pres.label,
        tone: pres.tone,
        reason: String(record.reason || item?.error || ""),
        record,
      };
    });
    autoReviewBatchDialog.value = { open: true, rows };
    return;
  }
  fillAutoReviewDialog(lr.auto_review || { reason: lr.error || "", action: "aborted" }, String(lr.slug || lr.task_id || ""));
}

async function autoReview(task: any) {
  if (!reviewReady.value) {
    alert("请先在设置中配置复验模型，或确保判分密钥可用以便回落。");
    return;
  }
  const ok = window.confirm(
    `对「${task.slug}」调用大模型自动复验？\n通过则补跑消融入库，不通过则打回 failed-samples。`
  );
  if (!ok) return;
  statusBusy.value = task.id;
  try {
    await apiPost("/api/queue/task/auto-review", { task_id: task.id });
    emit("refresh");
  } catch (err: any) {
    alert(err?.message || String(err));
  } finally {
    statusBusy.value = null;
  }
}

async function batchAutoReview() {
  const ids = [...selectedReviewPassIds.value];
  if (!ids.length) return;
  if (!reviewReady.value) {
    alert("请先在设置中配置复验模型，或确保判分密钥可用以便回落。");
    return;
  }
  const ok = window.confirm(
    `对已选 ${ids.length} 条「复验 0/8」任务执行自动复验？\n将逐条调用模型并自动入库或打回（可能较久）。`
  );
  if (!ok) return;
  batchBusy.value = true;
  try {
    const result = await apiPost("/api/queue/tasks/auto-review", { task_ids: ids });
    const skipped = result?.skipped?.length || 0;
    if (skipped) {
      alert(`已启动 ${result.count || 0} 条自动复验；跳过 ${skipped} 条`);
    }
    const done = new Set(result?.task_ids || ids);
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => !done.has(id));
    emit("refresh");
  } catch (err: any) {
    alert(err?.message || String(err));
  } finally {
    batchBusy.value = false;
  }
}

watch(
  [running, () => props.snapshot?.last_run],
  () => {
    if (prevRunning.value && !running.value) {
      showAutoReviewLastRun(props.snapshot?.last_run);
    }
    prevRunning.value = running.value;
  }
);

function packAuditChip(pack: FlatPack): { label: string; tone: string; title: string } | null {
  const status = String(pack?.llm_audit?.status || "").trim().toLowerCase();
  if (status !== "pass" && status !== "fail" && status !== "warn") return null;
  const summary = String(pack?.llm_audit?.summary || "").trim();
  const label = status === "pass" ? "通过" : status === "warn" ? "警告" : "不通过";
  const tone = status === "pass" ? "pass" : status === "warn" ? "warn" : "fail";
  return {
    label,
    tone,
    title: summary ? `${label}：${summary}` : `${label}（点击查看详情）`,
  };
}

function packAuditHeadline(status: string) {
  if (status === "pass") return "审核通过";
  if (status === "warn") return "审核警告";
  if (status === "fail") return "审核不通过";
  return "尚未进行 LLM 选材审核";
}

function openPackAuditDialog(pack: FlatPack) {
  const audit = pack?.llm_audit;
  const status = String(audit?.status || "").trim().toLowerCase();
  const chip = packAuditChip(pack);
  const checks = audit?.checks && typeof audit.checks === "object" ? audit.checks : {};
  const hasAudit = Boolean(status);
  packAuditDialog.value = {
    open: true,
    pack: String(pack?.pack || ""),
    tone: chip?.tone || "pending",
    headline: packAuditHeadline(status),
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

function closePackAuditDialog() {
  packAuditDialog.value.open = false;
}

function isSelected(key: string) {
  return selectedSet.value.has(key);
}

function togglePack(pack: FlatPack) {
  const set = new Set(selectedKeys.value);
  if (set.has(pack.key)) set.delete(pack.key);
  else set.add(pack.key);
  selectedKeys.value = [...set];
}

function toggleSelectAllPacks() {
  if (allSelectablePacksSelected.value) {
    selectedKeys.value = [];
    return;
  }
  // Replace selection with current list selectable packs (avoids hidden leftovers).
  selectedKeys.value = selectableVisiblePacks.value.map((p) => p.key);
}

async function loadMaterials() {
  loadError.value = "";
  try {
    const data = await apiGet("/api/materials");
    domains.value = data?.domains || [];
  } catch (err: any) {
    loadError.value = err?.message || String(err);
  }
}

async function body(opts: { forStart?: boolean } = {}) {
  const payload: Record<string, unknown> = {
    limit: Math.max(1, Number(limit.value) || 1),
    workers: Math.min(10, Math.max(1, Number(workers.value) || 1)),
    prefer_cold: preferCold.value,
    retry_technical: retryTechnical.value,
    question_type: questionType.value,
  };
  if (opts.forStart && materialMode.value === "select") {
    const packs = effectiveSelectedPacks.value;
    if (!packs.length) {
      if (selectedKeys.value.length && !allowRerunUsed.value) {
        throw new Error("所选材料均已用/失败；请改选 READY 包，或勾选「允许重跑已用/失败包」");
      }
      throw new Error("请至少选择一个材料包");
    }
    payload.packs = packs.map((p) => ({ domain_key: p.domain_key, pack: p.pack }));
    payload.limit = Math.max(1, packs.length);
    if (allowRerunUsed.value) {
      payload.include_used = true;
    }
  }
  return payload;
}

async function start() {
  try {
    await apiPost("/api/run/start", await body({ forStart: true }));
    emit("refresh");
  } catch (err: any) {
    alert(err?.message || String(err));
  }
}
async function resume() {
  await apiPost("/api/run/resume", await body());
  emit("refresh");
}

async function startQueuedTasks(ids: string[]) {
  const taskIds = [...new Set(ids.map((id) => String(id || "").trim()).filter(Boolean))];
  if (!taskIds.length) return;
  if (running.value) {
    alert("已有生产任务在运行");
    return;
  }
  try {
    const payload = await body();
    payload.task_ids = taskIds;
    payload.limit = taskIds.length;
    await apiPost("/api/run/resume", payload);
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => !taskIds.includes(id));
    emit("refresh");
  } catch (err: any) {
    alert(err?.message || String(err));
  }
}
async function stop() {
  await apiPost("/api/run/stop");
  emit("refresh");
}
async function interrupt() {
  await apiPost("/api/run/interrupt");
  emit("refresh");
}

function persistBoardFilters(immediate = false) {
  if (!prefsReady.value) return;
  const payload = {
    board: {
      selected_statuses: [...selectedStatuses.value],
      date_from: dateFrom.value || null,
      date_to: dateTo.value || null,
      question_type: questionType.value,
    },
  };
  const run = () => {
    apiPut("/api/ui-prefs", payload)
      .then((prefs) => emit("prefsSaved", prefs))
      .catch(() => undefined);
  };
  if (statusSaveTimer) window.clearTimeout(statusSaveTimer);
  statusSaveTimer = undefined;
  if (immediate) {
    run();
    return;
  }
  statusSaveTimer = window.setTimeout(run, 300);
}

onMounted(async () => {
  if (props.snapshot?.workspace) loadMaterials();
  try {
    const prefs = await apiGet("/api/ui-prefs");
    const saved = boardStatusesFromPrefs(prefs);
    if (saved) selectedStatuses.value = saved;
    dateFrom.value = boardDateFromPrefs(prefs, "date_from");
    dateTo.value = boardDateFromPrefs(prefs, "date_to");
    const savedType = String(prefs?.board?.question_type || "");
    if (savedType === "short_answer" || savedType === "multiple_choice" || savedType === "auto") {
      questionType.value = savedType;
    }
    if (typeof prefs?.board?.materials_collapsed === "boolean") {
      materialsCollapsed.value = prefs.board.materials_collapsed;
    }
    if (prefs) emit("prefsSaved", prefs);
  } catch {
    /* keep defaults / initialPrefs */
  } finally {
    prefsReady.value = true;
  }
});

watch(selectedStatuses, () => persistBoardFilters(false), { deep: true });
watch([dateFrom, dateTo, questionType], () => persistBoardFilters(false));
onDeactivated(() => persistBoardFilters(true));
onUnmounted(() => persistBoardFilters(true));
</script>

<style scoped>
.card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 18px;
  margin-bottom: 16px;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 16px;
  align-items: flex-end;
}
.controls-actions {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.run-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  box-sizing: border-box;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: #f1f5f9;
  color: #334155;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
  white-space: nowrap;
}
.run-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}
.run-status-text {
  font-weight: 600;
}
.run-status-claimed {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.4;
  white-space: nowrap;
}
.run-status.tone-idle {
  background: #f1f5f9;
  border-color: #e2e8f0;
  color: #475569;
}
.run-status.tone-running {
  background: #ecfdf5;
  border-color: #a7f3d0;
  color: #047857;
}
.run-status.tone-running .run-status-dot {
  animation: run-status-pulse 1.4s ease-in-out infinite;
}
.run-status.tone-stopping {
  background: #fffbeb;
  border-color: #fde68a;
  color: #b45309;
}
.run-status.tone-stopping .run-status-dot {
  animation: run-status-pulse 1s ease-in-out infinite;
}
.run-status.tone-interrupted {
  background: #fff1f2;
  border-color: #fecdd3;
  color: #be123c;
}
@keyframes run-status-pulse {
  0%,
  100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.15);
    opacity: 0.65;
  }
}
.controls .rule-hint {
  flex-basis: 100%;
  margin: 0;
  line-height: 1.5;
}
.status-bar {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 12px;
}
.status-filters,
.status-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 14px;
  align-items: center;
}
.status-actions :deep(.n-dropdown-trigger) {
  display: inline-flex;
  align-items: center;
}
.date-filter-group {
  display: inline-flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 10px;
}
.date-filter-group .clear-dates {
  flex-shrink: 0;
  white-space: nowrap;
}
.filter-wrap {
  position: relative;
  display: inline-flex;
}
.date-filter {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
  padding-bottom: 0;
}
.date-filter :deep(.n-date-picker) {
  width: 148px;
}
.search-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  color: var(--muted);
}
.search-field.inline-search {
  flex-direction: row;
  align-items: center;
  gap: 6px;
  padding-bottom: 0;
}
.search-field input[type="search"] {
  width: min(260px, 48vw);
  min-width: 160px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--primary-dark);
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
  min-width: 260px;
  max-width: min(360px, 80vw);
  max-height: min(420px, 60vh);
  overflow: auto;
  padding: 12px 14px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(26, 54, 93, 0.12);
}
.filter-panel-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}
.filter-panel-head strong {
  font-size: 13px;
  color: var(--primary-dark);
}
.mode-bar,
.select-tools,
.drawer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 16px;
  align-items: center;
  margin-bottom: 12px;
}
.drawer-actions {
  margin-bottom: 0;
  gap: 8px;
}
.mode-bar h2,
.status-filters h2 {
  margin: 0;
  margin-right: 8px;
}
.collapse-btn {
  padding: 4px 10px;
  font-size: 12px;
}
.check-row {
  margin-bottom: 10px;
}
label {
  display: flex;
  flex-direction: column;
  font-size: 13px;
  color: var(--muted);
  gap: 4px;
}
label.check,
label.radio {
  flex-direction: row;
  align-items: center;
  gap: 6px;
  padding-bottom: 0;
}
input[type="number"],
select {
  width: 120px;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
button {
  border: 1px solid var(--border);
  background: #fff;
  color: var(--primary-dark);
  border-radius: 8px;
  padding: 8px 14px;
  cursor: pointer;
}
.primary {
  background: var(--primary);
  color: #fff;
  border-color: var(--primary);
}
.danger {
  color: #c53030;
  border-color: #feb2b2;
}
button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.batch-menu-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.hint,
.muted {
  color: var(--muted);
  font-size: 13px;
}
.error {
  color: #c53030;
  font-size: 13px;
}
.warn-hint {
  color: #c05621;
  font-size: 13px;
  margin: 8px 0 0;
}
h2 {
  margin: 0 0 12px;
  color: var(--primary-dark);
  font-size: 16px;
}
.pack-list {
  max-height: 280px;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.pack-list .col-pack-check {
  width: 36px;
}
.pack-title {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 8px;
  min-width: 0;
}
.pack-title strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}
.board-table {
  table-layout: fixed;
}
.board-table th,
.board-table td {
  overflow: hidden;
  vertical-align: middle;
}
.board-table .col-check {
  width: 36px;
}
.board-table .col-status {
  width: 86px;
}
.board-table .col-material {
  width: 280px;
}
.board-table .col-stage {
  width: 176px;
  overflow: visible;
}
.board-table .col-pass {
  width: 140px;
}
.pass-stack {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
}
.board-table .col-ended {
  width: 124px;
  white-space: nowrap;
}
.board-table .col-actions {
  width: 76px;
  white-space: nowrap;
}
.board-table .col-status :deep(.status),
.board-table .col-pass :deep(.status) {
  max-width: 100%;
}
.board-table .col-status :deep(.text),
.board-table .col-pass :deep(.text) {
  max-width: none;
  flex: 1 1 auto;
  min-width: 0;
}
th,
td {
  text-align: left;
  padding: 8px 6px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
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
  flex-wrap: nowrap;
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
.meta-chip.tone-progress {
  color: #1d4ed8;
  background: #dbeafe;
  border-color: #bfdbfe;
}
.meta-chip.tone-neutral {
  color: #334155;
  background: #e2e8f0;
  border-color: #cbd5e1;
}
.meta-chip.tone-pass {
  color: #166534;
  background: #dcfce7;
  border-color: #bbf7d0;
}
.meta-chip.tone-fail {
  color: #9f1239;
  background: #ffe4e6;
  border-color: #fecdd3;
}
.meta-chip.tone-warn {
  color: #9a3412;
  background: #ffedd5;
  border-color: #fed7aa;
}
.pack-list .meta-chip {
  margin-left: 0;
  flex: 0 0 auto;
  vertical-align: middle;
}
.ok {
  color: #2f855a;
}
.warn {
  color: #c05621;
}
.ended {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}
.action-menu-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--border);
  background: #fff;
  color: var(--primary-dark);
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 12px;
  line-height: 1.3;
  cursor: pointer;
}
.action-menu-btn:hover:not(:disabled) {
  border-color: var(--primary);
  color: var(--primary);
  background: #ebf8ff;
}
.action-menu-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.action-caret {
  font-size: 10px;
  opacity: 0.7;
}
.pass-stack .review-chip {
  margin-left: 22px;
}
.review-chip {
  display: inline-block;
  border: 1px solid transparent;
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  background: #edf2f7;
  color: var(--muted);
}
.review-chip.tone-pass {
  color: #166534;
  background: #dcfce7;
  border-color: #bbf7d0;
}
.review-chip.tone-warn {
  color: #9a3412;
  background: #ffedd5;
  border-color: #fed7aa;
}
.review-chip.tone-fail {
  color: #9f1239;
  background: #ffe4e6;
  border-color: #fecdd3;
}
.review-chip.tone-abort {
  color: #9a3412;
  background: #ffedd5;
  border-color: #fed7aa;
}
.review-chip.tone-running {
  color: #1d4ed8;
  background: #dbeafe;
  border-color: #bfdbfe;
}
.review-verdict {
  font-weight: 600;
  margin: 8px 0;
}
.review-verdict.tone-pass {
  color: #166534;
}
.review-verdict.tone-warn {
  color: #9a3412;
}
.review-verdict.tone-fail {
  color: #9f1239;
}
.review-verdict.tone-abort,
.review-verdict.tone-running,
.review-verdict.tone-pending {
  color: #9a3412;
}
.modal-reason {
  white-space: pre-wrap;
  font-size: 13px;
  color: var(--text);
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
.summary-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  margin: 10px 0;
}
.summary-table th,
.summary-table td {
  text-align: left;
  padding: 6px 4px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
.summary-table tr.clickable {
  cursor: pointer;
}
.summary-table tr.clickable:hover {
  background: #ebf8ff;
}
button.link {
  border: 0;
  background: transparent;
  color: var(--primary);
  padding: 0;
  font-size: 12px;
  cursor: pointer;
}
button.danger-link,
button.link.danger-link {
  color: #c53030;
}
button.link:disabled {
  opacity: 0.45;
  cursor: not-allowed;
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
.modal-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 12px 0;
  font-size: 13px;
  color: var(--muted);
}
.modal-field textarea {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font: inherit;
  color: var(--primary-dark);
  resize: vertical;
}
.modal-check {
  margin-bottom: 8px;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 14px;
}

</style>
