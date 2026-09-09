"""Local FastAPI sidecar for the LCQA desktop app. Binds 127.0.0.1 only."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
DESKTOP_ROOT = BACKEND_DIR.parent
CODE_ROOT = DESKTOP_ROOT.parent
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR.parent))

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from desktop.backend.auto_review import run_auto_review
from desktop.backend.material_audit import audit_packs
from desktop.backend.materials import scan_materials
from desktop.backend.paths import code_root
from desktop.backend.queue_ops import (
    can_cancel,
    can_human_reject,
    can_requeue,
    can_review_pass,
    find_pack_sibling,
    human_reject_many,
    human_reject_passed,
    release_active_tasks,
    set_task_status,
    set_tasks_status,
    task_ended_at,
)
from desktop.backend.run_control import (
    MAX_WORKERS,
    read_progress,
    read_state,
    recent_events,
    request_interrupt,
    request_soft_stop,
    validate_batch_bounds,
)
from desktop.backend.runner import key_status, read_queue, run_pipeline, run_review_pass
from desktop.backend.scaffold import ensure_workspace, inspect_workspace
from desktop.backend.settings import get_settings_public, save_settings
from desktop.backend.ui_prefs import get_recents, get_ui_prefs, push_recent, save_ui_prefs

app = FastAPI(title="LCQA Desktop", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = threading.Lock()
_workspace: Path | None = None
_run_thread: threading.Thread | None = None
_audit_thread: threading.Thread | None = None
_last_run: dict[str, Any] | None = None
_last_audit: dict[str, Any] | None = None


class OpenBody(BaseModel):
    path: str


class PackSelector(BaseModel):
    domain_key: str = Field(min_length=1)
    pack: str = Field(min_length=1)


class RunBody(BaseModel):
    limit: int = Field(default=100, ge=1)
    workers: int = Field(default=1, ge=1, le=MAX_WORKERS)
    prefer_cold: bool = True
    include_hot: bool = False
    domain: str | None = None
    provider: str = "live"
    fixture_root: str | None = None
    skip_stage: bool = False
    retry_technical: bool = False
    include_used: bool = False
    packs: list[PackSelector] | None = None


class UiPrefsBody(BaseModel):
    last_workspace: str | None = None
    auto_open_workspace: bool | None = None
    last_tab: str | None = None
    board: dict[str, Any] | None = None
    materials: dict[str, Any] | None = None


class TaskStatusBody(BaseModel):
    task_id: str = Field(min_length=1)
    status: str = Field(pattern="^(queued|cancelled)$")
    reason: str = ""


class TasksStatusBody(BaseModel):
    task_ids: list[str] = Field(min_length=1)
    status: str = Field(pattern="^(queued|cancelled)$")
    reason: str = ""


class ReviewPassBody(BaseModel):
    task_id: str = Field(min_length=1)
    provider: str = "live"
    fixture_root: str | None = None
    source_type: str = "public technical/government documentation"


class HumanRejectBody(BaseModel):
    task_id: str = Field(min_length=1)
    reason: str = ""
    requeue: bool = False


class ReviewPassBatchBody(BaseModel):
    task_ids: list[str] = Field(min_length=1)
    provider: str = "live"
    fixture_root: str | None = None
    source_type: str = "public technical/government documentation"


class HumanRejectBatchBody(BaseModel):
    task_ids: list[str] = Field(min_length=1)
    reason: str = ""
    requeue: bool = False


class AutoReviewBody(BaseModel):
    task_id: str = Field(min_length=1)
    provider: str = "live"
    fixture_root: str | None = None
    source_type: str = "public technical/government documentation"


class AutoReviewBatchBody(BaseModel):
    task_ids: list[str] = Field(min_length=1)
    provider: str = "live"
    fixture_root: str | None = None
    source_type: str = "public technical/government documentation"


class MaterialsAuditBody(BaseModel):
    packs: list[PackSelector] = Field(min_length=1)


def _require_workspace() -> Path:
    if _workspace is None:
        raise HTTPException(400, "尚未选择工作根目录")
    return _workspace


def _annotate_queue(queue: dict[str, Any]) -> dict[str, Any]:
    tasks = []
    for task in queue.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        row = dict(task)
        sibling = find_pack_sibling(queue, task)
        requeue = can_requeue(task, queue=queue)
        cancel = can_cancel(task)
        # Duplicate losers (incl. extra cancelled): allow cancel when still live.
        if sibling and str(task.get("status") or "") != "cancelled":
            if str(task.get("status") or "") != "passed":
                cancel = True
        review_pass = can_review_pass(task)
        human_reject = can_human_reject(task)
        row["can_requeue"] = requeue
        row["can_cancel"] = cancel
        row["can_review_pass"] = review_pass
        row["can_auto_review"] = review_pass
        row["can_human_reject"] = human_reject
        row["status_editable"] = requeue or cancel  # backward compatible
        row["ended_at"] = task_ended_at(task)
        if sibling:
            row["duplicate_of"] = sibling.get("id")
            row["duplicate_status"] = sibling.get("status")
            # Extra cancelled copies are historical noise — hide from default board.
            if str(task.get("status") or "") == "cancelled":
                row["board_hidden"] = True
        tasks.append(row)
    out = dict(queue)
    out["tasks"] = tasks
    return out


def _snapshot() -> dict[str, Any]:
    root = _workspace
    if root is None:
        return {
            "workspace": None,
            "run": None,
            "queue": {"tasks": []},
            "progress": {},
            "events": [],
            "keys": key_status(),
            "last_run": _last_run,
            "last_audit": _last_audit,
            "audit_busy": False,
        }
    queue = _annotate_queue(read_queue(root))
    progress = read_progress(root)
    return {
        "workspace": str(root),
        "run": read_state(root),
        "queue": queue,
        "progress": progress,
        "events": recent_events(root, 80),
        "keys": key_status(),
        "last_run": _last_run,
        "last_audit": _last_audit,
        "audit_busy": _audit_busy(),
    }


def _maybe_sweep_idle(root: Path) -> dict[str, Any] | None:
    state = read_state(root)
    if str(state.get("status") or "idle") != "idle":
        return None
    if _busy():
        return None
    return release_active_tasks(root, reason="desktop idle sweep")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "code_root": str(code_root()), "features": ["reasoning_effort", "auto_review", "material_audit"]}


@app.get("/api/recents")
def recents() -> dict[str, Any]:
    return {"recents": get_recents()}


@app.get("/api/ui-prefs")
def api_get_ui_prefs() -> dict[str, Any]:
    return get_ui_prefs()


@app.put("/api/ui-prefs")
def api_put_ui_prefs(body: UiPrefsBody) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    return save_ui_prefs(patch)


@app.get("/api/keys")
def keys() -> dict[str, Any]:
    return key_status()


@app.get("/api/settings")
def api_get_settings() -> dict[str, Any]:
    return get_settings_public()


@app.put("/api/settings")
def api_put_settings(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return save_settings(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/workspace/inspect")
def api_inspect(body: OpenBody) -> dict[str, Any]:
    path = Path(body.path).expanduser()
    if not path.exists():
        raise HTTPException(400, "路径不存在")
    return inspect_workspace(path)


@app.post("/api/workspace/open")
def api_open(body: OpenBody) -> dict[str, Any]:
    global _workspace
    path = Path(body.path).expanduser().resolve()
    if not path.exists():
        raise HTTPException(400, "路径不存在")
    if not path.is_dir():
        raise HTTPException(400, "请选择文件夹")
    info = ensure_workspace(path)
    _workspace = Path(info["path"])
    os.environ["LCQA_ROOT"] = str(_workspace)
    os.environ["LCQA_CODE_ROOT"] = str(code_root())
    push_recent(_workspace)
    sweep = _maybe_sweep_idle(_workspace)
    return {**info, "keys": key_status(), "sweep": sweep}


@app.get("/api/workspace")
def api_workspace() -> dict[str, Any]:
    if _workspace is None:
        return {"path": None, "status": "none"}
    return inspect_workspace(_workspace)


@app.get("/api/materials")
def api_materials() -> dict[str, Any]:
    return scan_materials(_require_workspace())


@app.post("/api/materials/audit")
def api_materials_audit(body: MaterialsAuditBody) -> dict[str, Any]:
    global _audit_thread, _last_audit
    root = _require_workspace()
    if _audit_busy():
        raise HTTPException(409, "已有材料审核在运行")
    packs = [{"domain_key": p.domain_key, "pack": p.pack} for p in body.packs]

    def target() -> None:
        global _last_audit
        try:
            _last_audit = {"kind": "material_audit", **audit_packs(root, packs)}
        except Exception as exc:
            _last_audit = {"ok": False, "kind": "material_audit", "error": str(exc)}

    with _lock:
        _audit_thread = threading.Thread(target=target, name="lcqa-material-audit", daemon=True)
        _audit_thread.start()
    return {"ok": True, "started": True, "count": len(packs), "snapshot": _snapshot()}


@app.get("/api/queue")
def api_queue() -> dict[str, Any]:
    return _annotate_queue(read_queue(_require_workspace()))


@app.post("/api/queue/task/status")
def api_task_status(body: TaskStatusBody) -> dict[str, Any]:
    root = _require_workspace()
    try:
        result = set_task_status(root, task_id=body.task_id, status=body.status, reason=body.reason)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"ok": True, **result, "snapshot": _snapshot()}


@app.post("/api/queue/tasks/status")
def api_tasks_status(body: TasksStatusBody) -> dict[str, Any]:
    root = _require_workspace()
    if not body.task_ids:
        raise HTTPException(400, "task_ids required")
    result = set_tasks_status(
        root,
        task_ids=body.task_ids,
        status=body.status,
        reason=body.reason or f"batch → {body.status}",
    )
    return {**result, "snapshot": _snapshot()}


@app.post("/api/queue/task/review-pass")
def api_review_pass(body: ReviewPassBody) -> dict[str, Any]:
    """Human confirmed 0/8 is model-miss → ablation + package --zero-rechecked-pass."""
    global _run_thread, _last_run
    root = _require_workspace()
    if _busy():
        raise HTTPException(409, "已有生产任务在运行")
    queue = read_queue(root)
    task = next((t for t in (queue.get("tasks") or []) if isinstance(t, dict) and t.get("id") == body.task_id), None)
    if not task:
        raise HTTPException(404, f"task not found: {body.task_id}")
    if not can_review_pass(task):
        raise HTTPException(400, "仅 blocked + 复验(0/8) 任务可执行复验通过")

    def target() -> None:
        global _last_run
        try:
            _last_run = run_review_pass(
                root,
                body.task_id,
                provider=body.provider,
                fixture_root=Path(body.fixture_root) if body.fixture_root else None,
                source_type=body.source_type,
            )
        except Exception as exc:
            _last_run = {"ok": False, "error": str(exc), "task_id": body.task_id}

    with _lock:
        _run_thread = threading.Thread(target=target, name="lcqa-review-pass", daemon=True)
        _run_thread.start()
    return {"ok": True, "started": True, "task_id": body.task_id, "snapshot": _snapshot()}


@app.post("/api/queue/tasks/review-pass")
def api_review_pass_batch(body: ReviewPassBatchBody) -> dict[str, Any]:
    """Batch review-pass: sequential background jobs for eligible blocked/0-8 tasks."""
    global _run_thread, _last_run
    root = _require_workspace()
    if _busy():
        raise HTTPException(409, "已有生产任务在运行")
    queue = read_queue(root)
    by_id = {
        str(t.get("id") or ""): t
        for t in (queue.get("tasks") or [])
        if isinstance(t, dict) and t.get("id")
    }
    eligible: list[str] = []
    skipped: list[dict[str, str]] = []
    for raw in body.task_ids:
        tid = str(raw or "").strip()
        if not tid:
            continue
        task = by_id.get(tid)
        if not task:
            skipped.append({"task_id": tid, "error": "task not found"})
            continue
        if not can_review_pass(task):
            skipped.append({"task_id": tid, "error": "not eligible for review-pass"})
            continue
        eligible.append(tid)
    if not eligible:
        raise HTTPException(400, "没有可复检通过的任务（需 blocked + 复验 0/8）")

    fixture = Path(body.fixture_root) if body.fixture_root else None

    def target() -> None:
        global _last_run
        results: list[dict[str, Any]] = []
        for tid in eligible:
            try:
                results.append(
                    run_review_pass(
                        root,
                        tid,
                        provider=body.provider,
                        fixture_root=fixture,
                        source_type=body.source_type,
                    )
                )
            except Exception as exc:
                results.append({"ok": False, "error": str(exc), "task_id": tid})
        _last_run = {
            "ok": all(bool(r.get("ok")) for r in results) if results else False,
            "batch": True,
            "results": results,
            "skipped": skipped,
        }

    with _lock:
        _run_thread = threading.Thread(target=target, name="lcqa-review-pass-batch", daemon=True)
        _run_thread.start()
    return {
        "ok": True,
        "started": True,
        "task_ids": eligible,
        "count": len(eligible),
        "skipped": skipped,
        "snapshot": _snapshot(),
    }


@app.post("/api/queue/task/auto-review")
def api_auto_review(body: AutoReviewBody) -> dict[str, Any]:
    global _run_thread, _last_run
    root = _require_workspace()
    if _busy():
        raise HTTPException(409, "已有生产任务在运行")
    queue = read_queue(root)
    task = next((t for t in (queue.get("tasks") or []) if isinstance(t, dict) and t.get("id") == body.task_id), None)
    if not task:
        raise HTTPException(404, f"task not found: {body.task_id}")
    if not can_review_pass(task):
        raise HTTPException(400, "仅 blocked + 复验(0/8) 任务可执行自动复验")

    def target() -> None:
        global _last_run
        try:
            result = run_auto_review(
                root,
                body.task_id,
                provider=body.provider,
                fixture_root=Path(body.fixture_root) if body.fixture_root else None,
                source_type=body.source_type,
            )
            _last_run = {"kind": "auto_review", "batch": False, **result}
        except Exception as exc:
            _last_run = {"ok": False, "kind": "auto_review", "batch": False, "error": str(exc), "task_id": body.task_id}

    with _lock:
        _run_thread = threading.Thread(target=target, name="lcqa-auto-review", daemon=True)
        _run_thread.start()
    return {"ok": True, "started": True, "task_id": body.task_id, "snapshot": _snapshot()}


@app.post("/api/queue/tasks/auto-review")
def api_auto_review_batch(body: AutoReviewBatchBody) -> dict[str, Any]:
    global _run_thread, _last_run
    root = _require_workspace()
    if _busy():
        raise HTTPException(409, "已有生产任务在运行")
    queue = read_queue(root)
    by_id = {
        str(t.get("id") or ""): t
        for t in (queue.get("tasks") or [])
        if isinstance(t, dict) and t.get("id")
    }
    eligible: list[str] = []
    skipped: list[dict[str, str]] = []
    for raw in body.task_ids:
        tid = str(raw or "").strip()
        if not tid:
            continue
        task = by_id.get(tid)
        if not task:
            skipped.append({"task_id": tid, "error": "task not found"})
            continue
        if not can_review_pass(task):
            skipped.append({"task_id": tid, "error": "not eligible for auto-review"})
            continue
        eligible.append(tid)
    if not eligible:
        raise HTTPException(400, "没有可自动复验的任务（需 blocked + 复验 0/8）")

    fixture = Path(body.fixture_root) if body.fixture_root else None

    def target() -> None:
        global _last_run
        results: list[dict[str, Any]] = []
        for tid in eligible:
            try:
                results.append(
                    run_auto_review(
                        root,
                        tid,
                        provider=body.provider,
                        fixture_root=fixture,
                        source_type=body.source_type,
                    )
                )
            except Exception as exc:
                results.append({"ok": False, "error": str(exc), "task_id": tid})
        _last_run = {
            "ok": all(bool(r.get("ok")) for r in results) if results else False,
            "kind": "auto_review",
            "batch": True,
            "results": results,
            "skipped": skipped,
        }

    with _lock:
        _run_thread = threading.Thread(target=target, name="lcqa-auto-review-batch", daemon=True)
        _run_thread.start()
    return {
        "ok": True,
        "started": True,
        "task_ids": eligible,
        "count": len(eligible),
        "skipped": skipped,
        "snapshot": _snapshot(),
    }


@app.post("/api/queue/task/human-reject")
def api_human_reject(body: HumanRejectBody) -> dict[str, Any]:
    """Post-pass or pending-review human QC reject: move delivery into failed-samples, mark gate_failed."""
    root = _require_workspace()
    if _busy():
        raise HTTPException(409, "已有生产任务在运行")
    try:
        result = human_reject_passed(
            root,
            task_id=body.task_id,
            reason=body.reason,
            requeue=body.requeue,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(500, str(exc)) from exc
    return {**result, "snapshot": _snapshot()}


@app.post("/api/queue/tasks/human-reject")
def api_human_reject_batch(body: HumanRejectBatchBody) -> dict[str, Any]:
    root = _require_workspace()
    if _busy():
        raise HTTPException(409, "已有生产任务在运行")
    if not body.task_ids:
        raise HTTPException(400, "task_ids required")
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(400, "reason required")
    result = human_reject_many(
        root,
        task_ids=body.task_ids,
        reason=reason,
        requeue=body.requeue,
    )
    return {**result, "snapshot": _snapshot()}


@app.get("/api/run/state")
def api_run_state() -> dict[str, Any]:
    return _snapshot()


def _busy() -> bool:
    return _run_thread is not None and _run_thread.is_alive()


def _audit_busy() -> bool:
    return _audit_thread is not None and _audit_thread.is_alive()


@app.post("/api/run/start")
def api_start(body: RunBody) -> dict[str, Any]:
    root = _require_workspace()
    try:
        validate_batch_bounds(body.limit, body.workers)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.packs is not None and len(body.packs) == 0:
        raise HTTPException(400, "自选材料模式下请至少选择一个材料包")
    if _busy():
        raise HTTPException(409, "已有生产任务在运行")
    return _spawn(root, body, skip_stage=body.skip_stage)


@app.post("/api/run/resume")
def api_resume(body: RunBody) -> dict[str, Any]:
    root = _require_workspace()
    try:
        validate_batch_bounds(body.limit, body.workers)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if _busy():
        raise HTTPException(409, "已有生产任务在运行")
    payload = body.model_copy(update={"skip_stage": True})
    return _spawn(root, payload, skip_stage=True)


def _spawn(root: Path, body: RunBody, *, skip_stage: bool) -> dict[str, Any]:
    global _run_thread, _last_run

    def target() -> None:
        global _last_run
        packs = (
            [{"domain_key": p.domain_key, "pack": p.pack} for p in body.packs]
            if body.packs is not None
            else None
        )
        _last_run = run_pipeline(
            root,
            limit=body.limit,
            workers=body.workers,
            prefer_cold=body.prefer_cold,
            include_hot=body.include_hot,
            domain=body.domain,
            provider=body.provider,
            fixture_root=Path(body.fixture_root) if body.fixture_root else None,
            skip_stage=skip_stage,
            retry_technical=body.retry_technical,
            include_used=body.include_used,
            packs=packs,
        )

    with _lock:
        _run_thread = threading.Thread(target=target, name="lcqa-run", daemon=True)
        _run_thread.start()
    return {"ok": True, "started": True, "skip_stage": skip_stage, "limit": body.limit, "workers": body.workers}


def _schedule_sweep_when_idle(root: Path) -> None:
    def wait_and_sweep() -> None:
        thread = _run_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=600)
        try:
            release_active_tasks(root, reason="desktop stop/interrupt sweep")
        except Exception:
            pass

    threading.Thread(target=wait_and_sweep, name="lcqa-sweep", daemon=True).start()


@app.post("/api/run/stop")
def api_stop() -> dict[str, Any]:
    root = _require_workspace()
    state = request_soft_stop(root)
    _schedule_sweep_when_idle(root)
    return state


@app.post("/api/run/interrupt")
def api_interrupt() -> dict[str, Any]:
    root = _require_workspace()
    state = request_interrupt(root)
    _schedule_sweep_when_idle(root)
    return state


@app.websocket("/ws/run")
async def ws_run(ws: WebSocket) -> None:
    await ws.accept()
    try:
        import asyncio

        while True:
            await ws.send_json(_snapshot())
            await asyncio.sleep(0.6)
    except WebSocketDisconnect:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="LCQA desktop API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace", default=None)
    args = parser.parse_args()
    global _workspace
    if args.workspace:
        info = ensure_workspace(args.workspace)
        _workspace = Path(info["path"])
        os.environ["LCQA_ROOT"] = str(_workspace)
    os.environ.setdefault("LCQA_CODE_ROOT", str(code_root()))
    from desktop.backend.llm_client import install_ca_bundle

    install_ca_bundle()
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
