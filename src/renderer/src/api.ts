const BASE = "http://127.0.0.1:8765";

async function parse(res: Response) {
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const detail = data?.detail || data?.error || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

export function apiGet(path: string) {
  return fetch(`${BASE}${path}`).then(parse);
}

export function apiPost(path: string, body: unknown = {}) {
  return fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(parse);
}

export function apiPut(path: string, body: unknown = {}) {
  return fetch(`${BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(parse);
}

export type TaskVisualState =
  | "passed"
  | "gate_failed"
  | "cancelled"
  | "manual_review"
  | "blocked"
  | "running"
  | "paused"
  | "queued"
  | "unknown";

export const QUEUE_STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "passed", label: "通过" },
  { value: "claimed", label: "已领取" },
  { value: "running", label: "运行中" },
  { value: "queued", label: "排队" },
  { value: "blocked", label: "复检" },
  { value: "cancelled", label: "已取消" },
  { value: "gate_failed", label: "门禁失败" },
];

export const ALL_QUEUE_STATUSES = QUEUE_STATUS_OPTIONS.map((o) => o.value);

/** Board list group order: 通过 → 进行中 → 排队 → 复检 → 取消 → 门禁失败 */
export const BOARD_STATUS_RANK: Record<string, number> = {
  passed: 0,
  claimed: 1,
  running: 1,
  queued: 2,
  blocked: 3,
  cancelled: 4,
  gate_failed: 5,
};

export function boardStatusRank(task: any): number {
  const status = String(task?.status || "");
  return BOARD_STATUS_RANK[status] ?? 9;
}

function toLocalDay(isoOrDate: string | Date | null | undefined): string | null {
  if (!isoOrDate) return null;
  const d = isoOrDate instanceof Date ? isoOrDate : new Date(String(isoOrDate));
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Calendar day for board date filter: ended_at, else latest history.at. */
export function taskFilterDay(task: any): string | null {
  const ended = toLocalDay(task?.ended_at);
  if (ended) return ended;
  const history = Array.isArray(task?.history) ? task.history : [];
  let latest: Date | null = null;
  for (const entry of history) {
    if (!entry || typeof entry !== "object") continue;
    const at = entry.at;
    if (!at) continue;
    const d = new Date(String(at));
    if (Number.isNaN(d.getTime())) continue;
    if (!latest || d > latest) latest = d;
  }
  return toLocalDay(latest);
}

/** In-flight queue rows stay visible under date filter (no ended_at yet). */
export function isActiveQueueStatus(status: unknown): boolean {
  return ["queued", "claimed", "running"].includes(String(status || ""));
}

/** Sort key ms for within-group ordering (newer first). */
export function taskSortTimeMs(task: any): number {
  const ended = task?.ended_at ? Date.parse(String(task.ended_at)) : NaN;
  if (!Number.isNaN(ended)) return ended;
  const history = Array.isArray(task?.history) ? task.history : [];
  let earliest = NaN;
  for (const entry of history) {
    if (!entry || typeof entry !== "object" || !entry.at) continue;
    const t = Date.parse(String(entry.at));
    if (Number.isNaN(t)) continue;
    if (Number.isNaN(earliest) || t < earliest) earliest = t;
  }
  return Number.isNaN(earliest) ? 0 : earliest;
}

export function compareBoardTasks(a: any, b: any): number {
  const rank = boardStatusRank(a) - boardStatusRank(b);
  if (rank !== 0) return rank;
  const time = taskSortTimeMs(b) - taskSortTimeMs(a);
  if (time !== 0) return time;
  return String(a?.id || "").localeCompare(String(b?.id || ""));
}

/** Split queue slug / materials_pack into domain tag + pack name (no hyphen join). */
export function taskSlugParts(task: any): { domainKey: string; domainLabel: string; pack: string } {
  const packPath = String(task?.materials_pack || "").trim();
  let domainKey = "";
  let pack = "";
  if (packPath.startsWith("materials/")) {
    const parts = packPath.split("/").filter(Boolean);
    if (parts.length >= 3) {
      domainKey = parts[1];
      pack = parts.slice(2).join("/");
    }
  }
  const slug = String(task?.slug || "").trim();
  if ((!domainKey || !pack) && slug) {
    // Fallback: first segment before '-' is usually domain_key (may be wrong for multi-underscore keys).
    const i = slug.indexOf("-");
    if (i > 0) {
      domainKey = domainKey || slug.slice(0, i);
      pack = pack || slug.slice(i + 1);
    } else {
      pack = pack || slug;
    }
  }
  const domainLabel = String(task?.domain || "").trim() || domainKey || "—";
  return { domainKey: domainKey || "unknown", domainLabel, pack: pack || slug || "—" };
}

const DOMAIN_TAG_TONES = [
  "teal",
  "blue",
  "green",
  "amber",
  "rose",
  "slate",
  "cyan",
  "orange",
  "indigo",
  "lime",
] as const;

export type DomainTagTone = (typeof DOMAIN_TAG_TONES)[number];

export function domainTagTone(domainKey: string): DomainTagTone {
  const key = String(domainKey || "unknown");
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  return DOMAIN_TAG_TONES[h % DOMAIN_TAG_TONES.length];
}

export type DocKind = "single" | "multi" | "unknown" | string;

export type DocKindPresentation = {
  show: boolean;
  kind: DocKind;
  count: number;
  label: string;
  title: string;
  tone: "neutral" | "progress";
};

export function docKindPresentation(
  count: unknown,
  kind?: unknown
): DocKindPresentation {
  const n = Number(count);
  let resolved: DocKind = String(kind || "").trim().toLowerCase();
  if (resolved !== "single" && resolved !== "multi" && resolved !== "unknown") {
    if (!Number.isFinite(n) || n <= 0) resolved = "unknown";
    else if (n === 1) resolved = "single";
    else resolved = "multi";
  }
  if (resolved === "single") {
    return { show: true, kind: "single", count: 1, label: "单文档", title: "1 篇文档", tone: "neutral" };
  }
  if (resolved === "multi") {
    const docs = Number.isFinite(n) && n >= 2 ? n : 2;
    return {
      show: true,
      kind: "multi",
      count: docs,
      label: `多文档·${docs}`,
      title: `${docs} 篇文档`,
      tone: "progress",
    };
  }
  return { show: false, kind: "unknown", count: Number.isFinite(n) ? n : 0, label: "", title: "", tone: "neutral" };
}

export function docKindSearchText(count: unknown, kind?: unknown): string {
  const p = docKindPresentation(count, kind);
  if (!p.show) return "";
  return `${p.label} ${p.title} ${p.kind}`;
}

/** Case-insensitive multi-token AND match against material-related text fields. */
export function matchesMaterialQuery(
  query: string,
  ...parts: Array<string | null | undefined>
): boolean {
  const q = String(query || "")
    .trim()
    .toLowerCase();
  if (!q) return true;
  const hay = parts.map((p) => String(p || "").toLowerCase()).join("\n");
  return q.split(/\s+/).filter(Boolean).every((token) => hay.includes(token));
}

export function taskMatchesMaterialQuery(
  task: any,
  query: string,
  ...extra: Array<string | null | undefined>
): boolean {
  const parts = taskSlugParts(task);
  return matchesMaterialQuery(
    query,
    task?.id,
    task?.slug,
    task?.materials_pack,
    task?.domain,
    parts.domainKey,
    parts.domainLabel,
    parts.pack,
    ...extra
  );
}

export const STAGE_NAMES: Record<string, string> = {
  stage: "准备材料",
  enqueue: "入队",
  prepare: "准备样本",
  generate: "题目生产",
  precheck: "出题门禁",
  rollout: "8× rollout",
  judge: "判分",
  ablation: "消融对照",
  package: "打包交付",
  "gate-failed": "门禁失败归档",
  complete: "结束",
  paused: "已暂停",
  "auto-review": "自动复验",
};

/** Main per-task pipeline shown in the board stage strip. */
export const PIPELINE_STEPS = [
  "prepare",
  "generate",
  "precheck",
  "rollout",
  "judge",
  "ablation",
  "package",
  "complete",
] as const;

export type PipelineStep = (typeof PIPELINE_STEPS)[number];

export function progressRow(task: any, progress: any) {
  return progress?.progress?.tasks?.[task?.id] || progress?.progress?.tasks?.[task?.slug] || null;
}

export function taskVisualState(task: any, progress: any): TaskVisualState {
  const status = String(task?.status || "");
  const review = String(task?.review_status || "");
  if (status === "passed") return "passed";
  if (status === "gate_failed") {
    if (review === "human_reject") return "gate_failed"; // same icon; label via passVisual
    return "gate_failed";
  }
  if (status === "cancelled") return "cancelled";
  if (status === "blocked" && (review === "manual_review" || task?.avg_accuracy === 0)) return "manual_review";
  if (status === "blocked") return "blocked";
  if (status === "running" || status === "claimed") return "running";
  if (status === "queued") {
    const runStatus = progress?.run?.status;
    const step = progressRow(task, progress)?.step;
    if (step === "paused" || runStatus === "stopping" || runStatus === "interrupted") return "paused";
    return "queued";
  }
  return "unknown";
}

export function queueStatusLabel(status: string, task?: any): string {
  if (task && String(task.status) === "gate_failed" && String(task.review_status || "") === "human_reject") {
    return "复检打回";
  }
  return QUEUE_STATUS_OPTIONS.find((o) => o.value === status)?.label || status || "—";
}

export function passVisualState(task: any): TaskVisualState | "pending" {
  if (task.status === "passed") return "passed";
  if (task.status === "gate_failed") {
    if (String(task.review_status || "") === "human_reject") return "gate_failed";
    return "gate_failed";
  }
  if (task.status === "blocked") {
    return task.review_status === "manual_review" || task?.avg_accuracy === 0 ? "manual_review" : "blocked";
  }
  if (task.status === "cancelled") return "cancelled";
  return "pending";
}

/** Raw failure text from task or last candidate row. */
export function taskFailureReason(task: any): string {
  const top = String(task?.failure_reason || "").trim();
  if (top && top !== "gate_failed" && top !== "queued") return top;
  const rows = Array.isArray(task?.candidate_results) ? task.candidate_results : [];
  for (let i = rows.length - 1; i >= 0; i--) {
    const row = rows[i];
    if (!row || typeof row !== "object") continue;
    const reason = String(row.failure_reason || "").trim();
    if (reason) return reason;
  }
  return top;
}

function formatAvg(avg: unknown): string | null {
  if (avg == null || avg === "") return null;
  const n = Number(avg);
  if (Number.isNaN(n)) return null;
  return String(Math.round(n * 1000) / 1000);
}

/** Best-effort avg_accuracy from task or latest candidate row. */
export function taskAvgAccuracy(task: any): number | null {
  if (task?.avg_accuracy != null && task.avg_accuracy !== "") {
    const n = Number(task.avg_accuracy);
    if (!Number.isNaN(n)) return n;
  }
  const rows = Array.isArray(task?.candidate_results) ? task.candidate_results : [];
  for (let i = rows.length - 1; i >= 0; i--) {
    const row = rows[i];
    if (row?.avg_accuracy == null || row.avg_accuracy === "") continue;
    const n = Number(row.avg_accuracy);
    if (!Number.isNaN(n)) return n;
  }
  return null;
}

/** Display gate accuracy as n/8 when it maps cleanly to 8 rollouts. */
export function formatAccuracyRate(avg: number | null | undefined): string | null {
  if (avg == null || Number.isNaN(Number(avg))) return null;
  const n = Number(avg);
  const eighths = Math.round(n * 8);
  if (Math.abs(n * 8 - eighths) < 1e-6) return `${eighths}/8`;
  return `${Math.round(n * 1000) / 10}%`;
}

/** Short Chinese label for the board「通过」column (failures get concrete cause). */
export function passColumnLabel(task: any): string {
  const state = passVisualState(task);
  if (state === "passed") {
    const rate = formatAccuracyRate(taskAvgAccuracy(task));
    return rate || "通过";
  }
  if (state === "pending") return "—";
  if (state === "cancelled") return "已取消";
  if (state === "manual_review") return "复验 0/8";

  const review = String(task?.review_status || "").toLowerCase();
  if (review === "human_reject") return "复检打回";

  const reason = taskFailureReason(task).toLowerCase();
  const avg = taskAvgAccuracy(task);
  const avgTxt = formatAvg(avg);

  if (review === "manual_review" || reason.includes("avg_accuracy == 0") || reason.includes("requires human")) {
    return "复验 0/8";
  }
  if (reason.includes("human reject") || reason.includes("human_reject")) {
    return "复检打回";
  }
  if (
    reason.includes("avg_accuracy == 1") ||
    reason.includes("reject_perfect") ||
    (avg != null && avg === 1)
  ) {
    return "全对 8/8";
  }
  if (
    reason.includes("not in (0, 0.5") ||
    reason.includes("reject_too_easy") ||
    reason.includes("too easy") ||
    (avg != null && avg > 0.5)
  ) {
    return avgTxt ? `偏易 ${avgTxt}` : "偏易";
  }
  if (
    reason.includes("precheck") ||
    reason.includes("quote") ||
    reason.includes("evidence") ||
    reason.includes("search-kill") ||
    reason.includes("confusing") ||
    reason.includes("overlaps")
  ) {
    // Prefer a short slice of the original English reason when informative
    const raw = taskFailureReason(task);
    if (raw.length <= 28) return raw;
    if (reason.includes("search-kill") || reason.includes("overlaps")) return "出题门禁·易搜杀";
    if (reason.includes("not in context") || reason.includes("cites D")) return "出题门禁·虚构文档";
    if (reason.includes("quote") || reason.includes("substring")) return "出题门禁·证据引用";
    return "出题门禁失败";
  }
  if (reason.includes("technical") || review === "technical_failed" || state === "blocked") {
    return "技术失败";
  }
  if (avg != null && avg === 0) return "复验 0/8";
  if (avgTxt) return `未过门禁 ${avgTxt}`;
  const raw = taskFailureReason(task);
  if (raw) return raw.length > 24 ? `${raw.slice(0, 22)}…` : raw;
  return "门禁失败";
}

/** Full tooltip for pass column. */
export function passColumnTitle(task: any): string {
  const label = passColumnLabel(task);
  if (passVisualState(task) === "passed") {
    const rate = formatAccuracyRate(taskAvgAccuracy(task));
    return rate ? `正确率 ${rate}` : label;
  }
  const raw = taskFailureReason(task);
  if (raw && raw !== label) return `${label}\n${raw}`;
  return label;
}

/** Format task.ended_at (ISO) for board display. */
export function formatEndedAt(task: any): string {
  const raw = task?.ended_at;
  if (!raw) return "—";
  const d = new Date(String(raw));
  if (Number.isNaN(d.getTime())) return "—";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function terminalStageLabel(task: any): string {
  if (task.status === "passed") return "结束 · 通过";
  if (task.status === "gate_failed" && task.review_status === "human_reject") return "结束 · 复检打回";
  if (task.status === "gate_failed") return "结束 · 门禁失败";
  if (task.status === "blocked") return "结束 · 未通过/待处理";
  if (task.status === "cancelled") return "已取消";
  if (task.status === "running" || task.status === "claimed") return "运行中";
  if (task.status === "queued") return "排队";
  return task.status || "—";
}

export function stageLabel(task: any, progress: any): string {
  const terminal = ["passed", "gate_failed", "blocked", "cancelled"].includes(String(task?.status || ""));
  if (terminal) return terminalStageLabel(task);

  const row = progressRow(task, progress);
  if (!row) return terminalStageLabel(task);

  const stage = STAGE_NAMES[String(row.step || "")] || String(row.step || "阶段");
  let verb = String(row.status || "");
  if (row.status === "started") verb = "进行中";
  else if (row.status === "finished") verb = "已完成";
  else if (row.status === "error") verb = "出错";

  const bits: string[] = [stage];
  const detail = String(row.detail || "").trim();
  if (detail) {
    // Avoid duplicating attempt if detail already starts with aN
    bits.push(detail);
  } else if (row.attempt != null && row.step === "generate") {
    bits.push(`a${row.attempt}`);
  }
  bits.push(verb);
  return bits.join(" · ");
}

export type StepTone = "done" | "active" | "todo" | "error" | "idle";

export function pipelineStepTones(task: any, progress: any): { step: string; label: string; tone: StepTone }[] {
  const row = progressRow(task, progress);
  const status = String(task?.status || "");
  const terminalFail = ["gate_failed", "blocked", "cancelled"].includes(status);
  const terminalPass = status === "passed";
  const done = new Set<string>(Array.isArray(row?.done_steps) ? row.done_steps.map(String) : []);
  const current = row ? String(row.step || "") : "";
  const currentIdx = PIPELINE_STEPS.indexOf(current as PipelineStep);
  const err = row?.status === "error";

  return PIPELINE_STEPS.map((step, idx) => {
    let tone: StepTone = "todo";
    if (terminalPass) {
      tone = "done";
    } else if (terminalFail) {
      if (done.has(step) || (currentIdx >= 0 && idx < currentIdx)) tone = "done";
      else if (step === current || (current === "gate-failed" && step === "complete")) tone = "error";
      else if (currentIdx >= 0 && idx === currentIdx) tone = "error";
      else tone = "idle";
    } else if (!row) {
      tone = status === "queued" || status === "claimed" || status === "running" ? "todo" : "idle";
    } else if (done.has(step) || (currentIdx >= 0 && idx < currentIdx)) {
      tone = "done";
    } else if (step === current) {
      tone = err ? "error" : "active";
    }
    return { step, label: STAGE_NAMES[step] || step, tone };
  });
}

/** @deprecated use stageLabel */
export function stepLabel(task: any, progress: any) {
  return stageLabel(task, progress);
}

/** @deprecated use taskVisualState */
export function emojiForTask(task: any, progress: any) {
  return taskVisualState(task, progress);
}
