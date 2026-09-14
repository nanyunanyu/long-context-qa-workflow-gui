<template>
  <div>
    <el-card class="controls-card" shadow="never">
      <el-form :inline="true" class="controls" @submit.prevent>
        <el-form-item label="本批条数">
          <el-input-number
            v-if="materialMode === 'select'"
            :model-value="effectiveSelectCount"
            :min="0"
            disabled
            title="自选模式下等于将实际入队的材料包数量"
          />
          <el-input-number v-else v-model="limit" :min="1" />
        </el-form-item>
        <el-form-item label="并发">
          <el-input-number v-model="workers" :min="1" :max="10" />
        </el-form-item>
        <el-form-item label="出题类型">
          <el-select v-model="questionType" :disabled="running" style="width: 140px">
            <el-option label="短答案" value="short_answer" />
            <el-option label="选择题" value="multiple_choice" />
            <el-option label="自动" value="auto" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="preferCold" :disabled="materialMode === 'select'">优先冷源</el-checkbox>
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="retryTechnical">续跑技术失败</el-checkbox>
        </el-form-item>
        <el-form-item class="controls-actions">
          <el-button type="primary" :disabled="running" @click="start">开始</el-button>
          <el-button :disabled="!running" @click="stop">停止</el-button>
          <el-button type="danger" :disabled="!running" @click="interrupt">立即中断</el-button>
          <el-button :disabled="running" @click="resume">继续</el-button>
        </el-form-item>
      </el-form>
      <p class="muted rule-hint">{{ questionTypeHint }}</p>
    </el-card>

    <el-card class="card" shadow="never">
      <div class="mode-bar">
        <h2>材料来源</h2>
        <el-button type="primary" link @click="toggleMaterialsCollapsed">
          {{ materialsCollapsed ? "展开" : "收起" }}
        </el-button>
        <template v-if="!materialsCollapsed">
          <el-radio-group v-model="materialMode" :disabled="running">
            <el-radio value="auto">自动挑选</el-radio>
            <el-radio value="select">自选材料</el-radio>
          </el-radio-group>
          <el-button :disabled="running" @click="loadMaterials">刷新材料</el-button>
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
          <el-input
            v-model="packSearch"
            class="search-input"
            clearable
            placeholder="包名 / 领域 / 路径"
            :prefix-icon="Search"
          />
          <el-select v-model="domainFilter" clearable placeholder="全部领域" style="width: 220px">
            <el-option label="全部" value="" />
            <el-option
              v-for="d in domains"
              :key="d.domain_key"
              :label="`${d.domain} (${d.domain_key})`"
              :value="d.domain_key"
            />
          </el-select>
          <el-checkbox v-model="showUsed">显示已用/失败包</el-checkbox>
          <el-checkbox v-model="allowRerunUsed" :disabled="running">允许重跑已用/失败包</el-checkbox>
        </div>
        <el-alert v-if="loadError" type="error" :title="loadError" show-icon :closable="false" />
        <div class="pack-list">
          <el-table :data="visiblePacks" size="small" empty-text="没有可显示的材料包。">
            <el-table-column width="48">
              <template #header>
                <el-checkbox
                  :model-value="allSelectablePacksSelected"
                  :indeterminate="someSelectablePacksSelected && !allSelectablePacksSelected"
                  :disabled="running || !selectableVisiblePacks.length"
                  @change="toggleSelectAllPacks"
                />
              </template>
              <template #default="{ row }">
                <el-checkbox
                  :model-value="isSelected(row.key)"
                  :disabled="running || (!row.ready && !showUsed)"
                  @change="togglePack(row)"
                />
              </template>
            </el-table-column>
            <el-table-column label="材料包" min-width="280">
              <template #default="{ row }">
                <div class="pack-title">
                  <strong>{{ row.pack }}</strong>
                  <template v-for="kind in [docKindPresentation(row.doc_count, row.doc_kind)]" :key="row.key + '-dk'">
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
                  <template v-for="au in [packAuditChip(row)]" :key="row.key + '-au'">
                    <el-tag
                      size="small"
                      class="clickable-tag"
                      :class="'tone-' + au.tone"
                      :type="chipTagType(au.tone)"
                      effect="light"
                      :title="au.title"
                      @click="openPackAuditDialog(row)"
                    >
                      {{ au.label }}
                    </el-tag>
                  </template>
                </div>
                <div class="muted">{{ row.domain_key }} · {{ row.path }}</div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="120">
              <template #default="{ row }">
                <el-tag size="small" :type="row.ready ? 'success' : 'warning'" effect="light">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="提示" min-width="160" prop="hint" show-overflow-tooltip />
            <template #empty>
              <el-empty
                :description="
                  packSearch.trim()
                    ? '没有匹配当前搜索的材料包。'
                    : '没有可显示的材料包。请先在「材料」页检查目录，或勾选显示已用包。'
                "
                :image-size="64"
              />
            </template>
          </el-table>
        </div>
        <p class="muted">
          自选模式下本批条数 = 将实际入队的包数量。默认只计 READY；勾选「允许重跑已用/失败包」才会计入已用包并带
          --include-used。表头勾选框可全选 / 清空当前列表中的可选材料包。
        </p>
        <p v-if="hiddenSelectedCount" class="warn-hint">
          当前筛选下有 {{ hiddenSelectedCount }} 个已选包未显示，本批条数仍计入它们。可点表头勾选框清空，或去掉领域/搜索筛选查看。
        </p>
      </template>
    </el-card>

    <el-card class="card" shadow="never">
      <div class="status-bar">
        <div class="status-row status-lookup">
          <h2>生产状态</h2>
          <span class="hint">已筛 {{ filteredTasks.length }} / {{ tasks.length }}</span>
          <el-input
            v-model="taskSearch"
            class="search-input"
            clearable
            placeholder="slug / 包名 / 领域"
            :prefix-icon="Search"
          />
          <el-popover v-model:visible="filterOpen" trigger="click" placement="bottom-start" :width="280">
            <template #reference>
              <el-button :type="filterOpen ? 'primary' : 'default'">筛选</el-button>
            </template>
            <div class="filter-panel-head">
              <strong>按队列状态筛选</strong>
              <div class="drawer-actions">
                <el-button type="primary" link @click="selectAllStatuses">全选</el-button>
                <el-button type="primary" link @click="clearStatuses">清空</el-button>
              </div>
            </div>
            <el-checkbox-group v-model="selectedStatuses">
              <div v-for="opt in QUEUE_STATUS_OPTIONS" :key="opt.value" class="check-row">
                <el-checkbox :value="opt.value">{{ opt.label }}</el-checkbox>
              </div>
            </el-checkbox-group>
          </el-popover>
        </div>
        <div class="status-row status-tools">
          <div class="date-filter-group">
            <span class="date-filter" title="按结束日筛选已完结任务；排队/运行中不受日期影响">
              起日
              <el-date-picker
                v-model="dateFromModel"
                type="date"
                value-format="YYYY-MM-DD"
                clearable
                size="small"
                placeholder="选择日期"
                style="width: 148px"
              />
            </span>
            <span class="date-filter" title="按结束日筛选已完结任务；排队/运行中不受日期影响">
              止日
              <el-date-picker
                v-model="dateToModel"
                type="date"
                value-format="YYYY-MM-DD"
                clearable
                size="small"
                placeholder="选择日期"
                style="width: 148px"
              />
            </span>
            <el-button v-if="dateFrom || dateTo" type="primary" link @click="clearDateFilter">清空日期</el-button>
          </div>
          <div class="status-actions">
            <el-button :disabled="running || !selectableFiltered.length" @click="selectAllFiltered">
              全选当前筛选
            </el-button>
            <el-button :disabled="!selectedTaskIds.length" @click="clearTaskSelection">清空选择</el-button>
            <span v-if="selectedTaskIds.length" class="hint">已选 {{ selectedTaskIds.length }}</span>
            <el-dropdown trigger="click" :disabled="batchMenuDisabled" @command="onBatchActionSelect">
              <el-button :disabled="batchMenuDisabled">
                批量操作
                <el-icon class="el-icon--right"><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item
                    v-for="opt in batchActionOptions"
                    :key="opt.key"
                    :command="opt.key"
                    :disabled="opt.disabled"
                    :title="opt.title"
                    :style="opt.danger ? { color: 'var(--el-color-danger)' } : undefined"
                  >
                    {{ opt.label }}
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>
      </div>
      <FilterSummaryCard
        title="当前筛选汇总"
        unit="条"
        :total="filteredTasks.length"
        :groups="boardSummaryGroups"
      />
      <el-table :data="filteredTasks" class="board-table" size="small" row-key="id">
        <el-table-column width="48">
          <template #header>
            <el-checkbox
              :model-value="allFilteredSelected"
              :disabled="running || !selectableFiltered.length"
              @change="toggleSelectAllFiltered"
            />
          </template>
          <template #default="{ row }">
            <el-checkbox
              :model-value="selectedTaskSet.has(row.id)"
              :disabled="running || !taskSelectable(row)"
              @change="toggleTaskSelect(row)"
            />
          </template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <StatusIcon :state="taskVisualState(row, snapshot)" />
          </template>
        </el-table-column>
        <el-table-column label="材料" min-width="220">
          <template #default="{ row }">
            <div v-for="parts in [taskSlugParts(row)]" :key="row.id + '-slug'" class="slug-cell">
              <div class="slug-tags">
                <el-tag
                  size="small"
                  class="domain-tag"
                  :class="'tone-' + domainTagTone(parts.domainKey)"
                  :title="parts.domainKey"
                >
                  {{ parts.domainLabel }}
                </el-tag>
                <template v-for="kind in [taskDocKindPresentation(row)]" :key="row.id + '-dk'">
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
              <span class="pack-name" :title="row.slug">{{ parts.pack }}</span>
            </div>
            <div v-if="row.duplicate_of" class="warn-hint">
              同材料重复 · 请取消本条，保留 {{ row.duplicate_of }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="阶段" min-width="160">
          <template #default="{ row }">
            <StageProgress :task="row" :snapshot="snapshot" />
          </template>
        </el-table-column>
        <el-table-column label="通过" width="140">
          <template #default="{ row }">
            <div class="pass-stack">
              <StatusIcon
                :state="passVisualState(row)"
                :label="passColumnLabel(row)"
                :title="passColumnTitle(row)"
              />
              <el-tag
                v-if="autoReviewChip(row)"
                size="small"
                class="clickable-tag"
                :class="'tone-' + autoReviewChip(row).tone"
                :type="chipTagType(autoReviewChip(row).tone)"
                effect="light"
                :title="autoReviewChip(row).title"
                @click="openAutoReviewFromTask(row)"
              >
                {{ autoReviewChip(row).label }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="结束" width="124">
          <template #default="{ row }">
            <span class="ended">{{ formatEndedAt(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-dropdown
              v-if="taskHasActions(row)"
              trigger="click"
              :disabled="running || statusBusy === row.id || batchBusy"
              @command="(key) => onTaskActionSelect(key, row)"
            >
              <el-button
                size="small"
                :disabled="running || statusBusy === row.id || batchBusy"
              >
                操作
                <el-icon class="el-icon--right"><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item
                    v-for="opt in taskActionOptions(row)"
                    :key="opt.key"
                    :command="opt.key"
                    :disabled="opt.disabled"
                    :title="opt.title"
                    :style="opt.danger ? { color: 'var(--el-color-danger)' } : undefined"
                  >
                    {{ opt.label }}
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty
            :description="
              !tasks.length
                ? '队列为空。请先选择材料并点开始，或放入材料后用自动挑选。'
                : taskSearch.trim()
                  ? '没有匹配当前材料搜索的任务。'
                  : '当前筛选无任务。'
            "
            :image-size="72"
          />
        </template>
      </el-table>
    </el-card>

    <el-dialog
      v-model="rejectDialog.open"
      :title="rejectDialog.batch ? '批量复检不通过' : '复检不通过'"
      width="480px"
      :close-on-click-modal="!(statusBusy || batchBusy)"
      :before-close="onRejectDialogBeforeClose"
    >
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
      <el-form label-position="top">
        <el-form-item label="原因（必填）">
          <el-input
            v-model="rejectDialog.reason"
            type="textarea"
            :rows="3"
            placeholder="例如：题干剧透 / 金标错误 / 证据不在文中"
          />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="rejectDialog.requeue">同时改回排队以便同材料重跑</el-checkbox>
        </el-form-item>
      </el-form>
      <el-alert v-if="rejectDialog.error" type="error" :title="rejectDialog.error" show-icon :closable="false" />
      <template #footer>
        <el-button :disabled="Boolean(statusBusy) || batchBusy" @click="closeRejectDialog">取消</el-button>
        <el-button type="danger" :disabled="Boolean(statusBusy) || batchBusy" @click="submitHumanReject">
          {{ rejectDialog.batch ? "确认批量打回" : "确认打回" }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="packAuditDialog.open" title="材料审核结果" width="640px">
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
      <template #footer>
        <el-button type="primary" @click="closePackAuditDialog">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="autoReviewDialog.open" title="自动复验结果" width="640px">
      <p class="muted">
        任务 <strong>{{ autoReviewDialog.slug }}</strong>
      </p>
      <p class="review-verdict" :class="'tone-' + autoReviewDialog.tone">{{ autoReviewDialog.headline }}</p>
      <p class="modal-reason">{{ autoReviewDialog.reason || "—" }}</p>
      <ul class="modal-checks">
        <li>金标可被原文支持：{{ boolLabel(autoReviewDialog.goldSupported) }}</li>
        <li>题干可判定：{{ boolLabel(autoReviewDialog.questionOk) }}</li>
        <li v-if="autoReviewDialog.rescoredCount != null">假阴性改判正确数：{{ autoReviewDialog.rescoredCount }}</li>
        <li v-if="autoReviewDialog.rescoredAvg != null">假阴性改判正确率：{{ autoReviewDialog.rescoredAvg }}</li>
        <li>模型：{{ autoReviewDialog.model || "—" }}</li>
        <li>时间：{{ autoReviewDialog.reviewedAt || "—" }}</li>
      </ul>
      <template #footer>
        <el-button type="primary" @click="closeAutoReviewDialog">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="autoReviewBatchDialog.open" title="批量自动复验汇总" width="720px">
      <p class="muted">共 {{ autoReviewBatchDialog.rows.length }} 条</p>
      <el-table :data="autoReviewBatchDialog.rows" size="small" @row-click="onBatchReviewRowClick">
        <el-table-column label="材料" prop="slug" min-width="180" />
        <el-table-column label="结论" width="160">
          <template #default="{ row }">
            <el-tag size="small" :class="'tone-' + row.tone" :type="chipTagType(row.tone)" effect="light">
              {{ row.label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="理由" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="muted">{{ row.reason }}</span>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button type="primary" @click="closeAutoReviewBatchDialog">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from "vue";
import { ArrowDown, Search } from "@element-plus/icons-vue";
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
  countByDomain,
  countByVisualStatus,
  passVisualState,
  taskFilterDay,
  taskMatchesMaterialQuery,
  taskSlugParts,
  taskVisualState,
} from "../api";
import { chipTagType, confirmAction, toastError, toastWarning, type MenuAction } from "../ui";
import FilterSummaryCard, { type SummaryGroup } from "../components/FilterSummaryCard.vue";
import StatusIcon from "../components/StatusIcon.vue";
import StageProgress from "../components/StageProgress.vue";

const props = defineProps<{ snapshot: any; initialPrefs?: any }>();
const emit = defineEmits(["refresh", "prefsSaved"]);

function boardStatusesFromPrefs(prefs: any): string[] | null {
  const saved = prefs?.board?.selected_statuses;
  if (!Array.isArray(saved)) return null;
  const known = saved.filter((s: string) => ALL_QUEUE_STATUSES.includes(s));
  const missing = ALL_QUEUE_STATUSES.filter((s) => !known.includes(s));
  return [...known, ...missing];
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
const materialsCollapsed = ref(Boolean(props.initialPrefs?.board?.materials_collapsed));
const hydrated = boardStatusesFromPrefs(props.initialPrefs);
const selectedStatuses = ref<string[]>(hydrated ?? [...ALL_QUEUE_STATUSES]);
const dateFrom = ref(boardDateFromPrefs(props.initialPrefs, "date_from"));
const dateTo = ref(boardDateFromPrefs(props.initialPrefs, "date_to"));

const dateFromModel = computed<string | null>({
  get() {
    return dateFrom.value || null;
  },
  set(value) {
    dateFrom.value = value || "";
  },
});
const dateToModel = computed<string | null>({
  get() {
    return dateTo.value || null;
  },
  set(value) {
    dateTo.value = value || "";
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
  rescoredCount: null as number | null,
  rescoredAvg: null as string | null,
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

const boardSummaryGroups = computed<SummaryGroup[]>(() => [
  {
    name: "状态",
    items: countByVisualStatus(filteredTasks.value, props.snapshot).map((row) => ({
      key: row.state,
      count: row.count,
      state: row.state,
    })),
  },
  {
    name: "领域",
    items: countByDomain(filteredTasks.value).map((row) => ({
      key: row.key,
      count: row.count,
      domainKey: row.key,
      domainLabel: row.label,
    })),
  },
]);

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
    ensureMaterialsLoaded();
  }
});

watch(
  () => props.snapshot?.workspace,
  (ws) => {
    if (ws) ensureMaterialsLoaded();
  }
);

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

function withBatchCount(label: string, count: number): string {
  return count ? `${label}（${count}）` : label;
}

const batchActionOptions = computed<MenuAction[]>(() => [
  {
    label: withBatchCount("批量开始", selectedStartIds.value.length),
    key: "start",
    disabled: running.value || batchBusy.value || !selectedStartIds.value.length,
    title: "领取已选排队任务并开始生产，不再挑选新材料",
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
    danger: true,
  },
  {
    label: withBatchCount("批量复检通过", selectedReviewPassIds.value.length),
    key: "review_pass",
    disabled: running.value || batchBusy.value || !selectedReviewPassIds.value.length,
    title: "对已选 blocked + 复验 0/8 任务逐条补跑消融并入库",
  },
  {
    label: withBatchCount("批量自动复验", selectedReviewPassIds.value.length),
    key: "auto_review",
    disabled: running.value || batchBusy.value || !reviewReady.value || !selectedReviewPassIds.value.length,
    title: reviewReady.value
      ? "对已选 复验 0/8 任务调用大模型自动判定并执行通过/打回"
      : "请先在设置中配置复验模型或判分密钥",
  },
  {
    label: withBatchCount("批量复检不通过", selectedHumanRejectIds.value.length),
    key: "human_reject",
    disabled: running.value || batchBusy.value || !selectedHumanRejectIds.value.length,
    danger: true,
    title: "对已选通过/待复验任务批量打回",
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

function taskActionOptions(task: any): MenuAction[] {
  const opts: MenuAction[] = [];
  if (taskCanStart(task)) {
    opts.push({
      label: "开始生产",
      key: "start",
      disabled: running.value || batchBusy.value,
      title: "领取这条排队任务并开始生产，不再挑选新材料",
    });
  }
  if (task.can_human_reject) {
    opts.push({
      label: "复检不通过？",
      key: "human_reject",
      danger: true,
      title: "确认题/金标有问题：从待复验或 samples 迁入 failed-samples 并释放材料",
    });
  }
  if (task.can_review_pass) {
    opts.push({
      label: "自动复验",
      key: "auto_review",
      disabled: !reviewReady.value,
      title: reviewReady.value
        ? "调用复验模型自动判定：通过则入库，不通过则打回"
        : "请先在设置中配置复验模型或判分密钥",
    });
    opts.push({
      label: "复验通过",
      key: "review_pass",
      title: "确认题/金标无误、0 分为模型答错后，补跑消融并以零分复验通过入库",
    });
  }
  if (task.can_requeue) {
    opts.push({ label: "改回排队", key: "requeue" });
  }
  if (task.can_cancel) {
    opts.push({ label: "取消", key: "cancel", danger: true });
  }
  return opts;
}

function onBatchReviewRowClick(row: any) {
  openAutoReviewRecord(row.record, row.slug);
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
    toastError(err);
  } finally {
    statusBusy.value = null;
  }
}

async function reviewPass(task: any) {
  const ok = await confirmAction(
    `确认「${task.slug}」题/金标无误，0/8 为模型答错？\n将补跑消融并以零分复验通过入库（可能数分钟）。`
  );
  if (!ok) return;
  statusBusy.value = task.id;
  try {
    await apiPost("/api/queue/task/review-pass", { task_id: task.id });
    emit("refresh");
  } catch (err: any) {
    toastError(err);
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

function rejectDialogBusy() {
  return statusBusy.value === rejectDialog.value.taskId || batchBusy.value;
}

function closeRejectDialog() {
  if (rejectDialogBusy()) return;
  rejectDialog.value.open = false;
}

function onRejectDialogBeforeClose(done: () => void) {
  if (rejectDialogBusy()) return;
  done();
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
        toastWarning(`已打回 ${result.count || 0} 条，失败 ${errCount} 条`);
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

function toggleSelectAllFiltered(checked: string | number | boolean) {
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
      toastWarning(`已更新 ${result.count || 0} 条，失败 ${errCount} 条`);
    }
    const done = new Set(result?.updated || ids);
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => !done.has(id));
    emit("refresh");
  } catch (err: any) {
    toastError(err);
  } finally {
    batchBusy.value = false;
  }
}

async function batchReviewPass() {
  const ids = [...selectedReviewPassIds.value];
  if (!ids.length) return;
  const ok = await confirmAction(
    `确认对已选 ${ids.length} 条「复验 0/8」任务执行复检通过？\n将逐条补跑消融并以零分复验通过入库（可能较久）。`
  );
  if (!ok) return;
  batchBusy.value = true;
  try {
    const result = await apiPost("/api/queue/tasks/review-pass", { task_ids: ids });
    const skipped = result?.skipped?.length || 0;
    if (skipped) {
      toastWarning(`已启动 ${result.count || 0} 条复检通过；跳过 ${skipped} 条`);
    }
    const done = new Set(result?.task_ids || ids);
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => !done.has(id));
    emit("refresh");
  } catch (err: any) {
    toastError(err);
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
  if (action === "borderline_50") {
    return {
      label: "自动复验临界 4/8",
      tone: "abort",
      headline: "假阴性改判后恰好 4/8，已进临界归档（不算通过，不入 failed-samples）",
    };
  }
  if (action === "rejected" || verdict === "fail") {
    const rescored = record?.rescored_avg_accuracy != null;
    return {
      label: "自动复验未通过",
      tone: "fail",
      headline: rescored
        ? "假阴性改判后未过门禁，已打回 failed-samples"
        : "不通过，已打回 failed-samples",
    };
  }
  if (verdict === "pass" || action === "promoted" || action === "rescored") {
    return { label: "自动复验通过", tone: "pass", headline: "通过并入库（题/金标无误，0/8 视为模型答错）" };
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
  const rescoredAvg =
    record?.rescored_avg_accuracy == null || record?.rescored_avg_accuracy === ""
      ? null
      : String(record.rescored_avg_accuracy);
  const rescoredCount =
    record?.rescored_correct_count == null || record?.rescored_correct_count === ""
      ? null
      : Number(record.rescored_correct_count);
  autoReviewDialog.value = {
    open: true,
    slug,
    headline: pres.headline,
    tone: pres.tone,
    reason: String(record?.reason || record?.exec_error || ""),
    goldSupported: record?.gold_supported ?? null,
    questionOk: record?.question_ok ?? null,
    rescoredCount: Number.isFinite(rescoredCount) ? rescoredCount : null,
    rescoredAvg,
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
    toastWarning("请先在设置中配置复验模型，或确保判分密钥可用以便回落。");
    return;
  }
  const ok = await confirmAction(
    `对「${task.slug}」调用大模型自动复验？\n通过则补跑消融入库；恰好 4/8 进临界归档；过易则打回 failed-samples。`
  );
  if (!ok) return;
  statusBusy.value = task.id;
  try {
    await apiPost("/api/queue/task/auto-review", { task_id: task.id });
    emit("refresh");
  } catch (err: any) {
    toastError(err);
  } finally {
    statusBusy.value = null;
  }
}

async function batchAutoReview() {
  const ids = [...selectedReviewPassIds.value];
  if (!ids.length) return;
  if (!reviewReady.value) {
    toastWarning("请先在设置中配置复验模型，或确保判分密钥可用以便回落。");
    return;
  }
  const ok = await confirmAction(
    `对已选 ${ids.length} 条「复验 0/8」任务执行自动复验？\n将逐条调用模型并自动入库或打回（可能较久）。`
  );
  if (!ok) return;
  batchBusy.value = true;
  try {
    const result = await apiPost("/api/queue/tasks/auto-review", { task_ids: ids });
    const skipped = result?.skipped?.length || 0;
    if (skipped) {
      toastWarning(`已启动 ${result.count || 0} 条自动复验；跳过 ${skipped} 条`);
    }
    const done = new Set(result?.task_ids || ids);
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => !done.has(id));
    emit("refresh");
  } catch (err: any) {
    toastError(err);
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

function packAuditChip(pack: FlatPack): { label: string; tone: string; title: string } {
  const status = String(pack?.llm_audit?.status || "").trim().toLowerCase();
  if (status !== "pass" && status !== "fail" && status !== "warn") {
    return { label: "未审核", tone: "pending", title: "尚未进行 LLM 选材审核（点击查看）" };
  }
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

let materialsInflight: Promise<void> | null = null;

async function loadMaterials() {
  if (materialsInflight) return materialsInflight;
  loadError.value = "";
  const pending = (async () => {
    try {
      const data = await apiGet("/api/materials");
      domains.value = data?.domains || [];
    } catch (err: any) {
      loadError.value = err?.message || String(err);
    }
  })();
  materialsInflight = pending;
  try {
    await pending;
  } finally {
    if (materialsInflight === pending) materialsInflight = null;
  }
}

function ensureMaterialsLoaded() {
  if (domains.value.length) return;
  void loadMaterials();
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
    toastError(err);
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
    toastWarning("已有生产任务在运行");
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
    toastError(err);
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
  // Task 单文档/多文档 tags join against /api/materials. Do not wait for
  // snapshot.workspace: first board paint often still has the empty placeholder
  // snapshot, while the backend workspace is already open.
  ensureMaterialsLoaded();
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
onActivated(() => ensureMaterialsLoaded());
onDeactivated(() => persistBoardFilters(true));
onUnmounted(() => persistBoardFilters(true));
</script>

<style scoped>
.controls-card,
.card {
  margin-bottom: 16px;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
}
.controls :deep(.el-form-item) {
  margin-bottom: 8px;
}
.controls-actions :deep(.el-form-item__content) {
  flex-wrap: wrap;
  gap: 8px;
}
.rule-hint {
  margin: 0;
  line-height: 1.5;
}
.status-bar {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 12px;
}
.status-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
}
.status-tools {
  justify-content: space-between;
}
.status-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-left: auto;
}
.date-filter-group {
  display: inline-flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 10px;
}
.date-filter {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}
.search-input {
  width: min(260px, 48vw);
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
.status-row h2 {
  margin: 0;
}
.check-row {
  margin-bottom: 8px;
}
.hint,
.muted {
  color: var(--muted);
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
}
.pack-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}
.pack-title strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pass-stack {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
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
  min-width: 0;
}
.pack-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.35;
  color: var(--primary-dark);
  word-break: break-word;
}
.clickable-tag {
  cursor: pointer;
}
.ended {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
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
</style>
