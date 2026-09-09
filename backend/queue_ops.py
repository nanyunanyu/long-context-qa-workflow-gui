"""Desktop-side queue helpers (wrap queue_worker; do not edit workflow/)."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTENT_FAIL_MARKERS = (
    "precheck_failed",
    "technical_failed",
    "precheck",
    "technical",
    "human_reject",
)


def read_queue(workspace: Path) -> dict[str, Any]:
    path = workspace / "queue" / "queue.json"
    if not path.is_file():
        return {"version": 2, "tasks": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 2, "tasks": []}


def write_queue(workspace: Path, queue: dict[str, Any]) -> None:
    path = workspace / "queue" / "queue.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_task(workspace: Path, task_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Merge fields onto a queue task and persist. Used after review-pass/reject rewrite the queue."""
    queue = read_queue(workspace)
    found: dict[str, Any] | None = None
    for task in queue.get("tasks") or []:
        if isinstance(task, dict) and task.get("id") == task_id:
            task.update(fields)
            found = task
            break
    if found is None:
        raise KeyError(f"task not found: {task_id}")
    write_queue(workspace, queue)
    return found


def _qw(workspace: Path, args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        ["python3", str(workspace / "workflow" / "queue_worker.py"), *args],
        cwd=str(workspace),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"queue_worker {' '.join(args)} failed:\n{(proc.stderr or '')[-2000:]}\n{(proc.stdout or '')[-2000:]}")
    text = (proc.stdout or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < 0:
            raise
        return json.loads(text[start : end + 1])


def release_active_tasks(workspace: Path, *, reason: str = "desktop sweep") -> dict[str, Any]:
    """Release all claimed/running tasks back to queued."""
    queue = read_queue(workspace)
    released: list[str] = []
    errors: list[str] = []
    for task in queue.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        if task.get("status") not in {"claimed", "running"}:
            continue
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        try:
            _qw(
                workspace,
                ["release", "--task-id", task_id, "--by", "desktop", "--reason", reason],
            )
            released.append(task_id)
        except Exception as exc:  # noqa: BLE001 — collect and continue sweep
            errors.append(f"{task_id}: {exc}")
    return {"released": released, "errors": errors, "count": len(released)}


def is_non_accuracy_failure(task: dict[str, Any]) -> bool:
    """True when failure is precheck/technical (not avg_accuracy gate band)."""
    status = str(task.get("status") or "")
    if status not in {"gate_failed", "blocked"}:
        return False
    avg = task.get("avg_accuracy")
    if avg is None:
        return True
    review = str(task.get("review_status") or "").lower()
    if any(m in review for m in CONTENT_FAIL_MARKERS):
        return True
    reason = str(task.get("failure_reason") or "").lower()
    if any(m in reason for m in ("precheck", "technical", "confusing_facts", "quote is not")):
        return True
    results = task.get("candidate_results") if isinstance(task.get("candidate_results"), list) else []
    if results:
        last = results[-1] if isinstance(results[-1], dict) else {}
        last_status = str(last.get("status") or last.get("review_status") or "").lower()
        if any(m in last_status for m in CONTENT_FAIL_MARKERS):
            return True
        if last.get("avg_accuracy") is None and last_status in {"precheck_failed", "technical_failed", "gate_failed"}:
            # gate_failed with null avg from content path
            if last_status != "gate_failed" or any(
                m in str(last.get("failure_reason") or "").lower() for m in ("precheck", "technical", "confusing")
            ):
                return True
    # Numeric avg means accuracy-band failure — not editable here.
    try:
        float(avg)
        return False
    except (TypeError, ValueError):
        return True


def can_requeue(task: dict[str, Any], *, queue: dict[str, Any] | None = None) -> bool:
    """cancelled, non-accuracy gate_failed/blocked, or human_reject after pass revoke.

    When queue is provided: only the preferred task for that pack/slug may requeue
    (so multiple cancelled rows do not each expose「改回排队」).
    """
    if str(task.get("status") or "") == "cancelled":
        base = True
    elif str(task.get("review_status") or "").lower() == "human_reject":
        base = str(task.get("status") or "") in {"gate_failed", "blocked"}
    else:
        base = is_non_accuracy_failure(task)
    if not base:
        return False
    if queue is None:
        return True
    pref = preferred_task_for_pack_row(queue, task)
    if not pref:
        return True
    return str(pref.get("id") or "") == str(task.get("id") or "")


def preferred_task_for_pack_row(queue: dict[str, Any], task: dict[str, Any]) -> dict[str, Any] | None:
    from workflow.queue_worker import preferred_task_for_pack

    return preferred_task_for_pack(
        queue,
        slug=str(task.get("slug") or ""),
        materials_pack=task.get("materials_pack"),
    )


def find_pack_sibling(queue: dict[str, Any], task: dict[str, Any]) -> dict[str, Any] | None:
    """Other task that is the canonical owner of this pack/slug (includes cancelled dups)."""
    pref = preferred_task_for_pack_row(queue, task)
    if not pref:
        return None
    if str(pref.get("id") or "") == str(task.get("id") or ""):
        return None
    return pref


def can_cancel(task: dict[str, Any]) -> bool:
    """Only non-accuracy failures (not already cancelled)."""
    return is_non_accuracy_failure(task)


def can_human_reject(task: dict[str, Any]) -> bool:
    """Passed delivery eligible for post-QC revoke, or 0/8 pending-review reject."""
    status = str(task.get("status") or "")
    if status == "passed":
        return True
    if status == "blocked" and find_manual_review_candidate(task) is not None:
        return True
    return False


def _candidate_rows(task: dict[str, Any]) -> list[dict[str, Any]]:
    rows = task.get("candidate_results") if isinstance(task.get("candidate_results"), list) else []
    return [r for r in rows if isinstance(r, dict)]


def find_manual_review_candidate(task: dict[str, Any]) -> dict[str, Any] | None:
    """Return the candidate row waiting on 0/8 human recheck."""
    for row in _candidate_rows(task):
        status = str(row.get("status") or row.get("review_status") or "").lower()
        if status == "manual_review":
            return row
        try:
            if float(row.get("avg_accuracy")) == 0.0 and "manual" in status:
                return row
        except (TypeError, ValueError):
            continue
    return None


def can_review_pass(task: dict[str, Any]) -> bool:
    """blocked + manual_review candidate (0/8) — eligible for human-confirmed promote."""
    if str(task.get("status") or "") != "blocked":
        return False
    return find_manual_review_candidate(task) is not None


_TERMINAL_STATUSES = frozenset({"passed", "gate_failed", "blocked", "cancelled"})


def task_ended_at(task: dict[str, Any]) -> str | None:
    """Derive end timestamp from history for terminal tasks."""
    status = str(task.get("status") or "")
    if status not in _TERMINAL_STATUSES:
        return None
    history = task.get("history") if isinstance(task.get("history"), list) else []
    # Prefer last matching terminal event (scan from end).
    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue
        at = entry.get("at")
        if not at:
            continue
        event = str(entry.get("event") or "")
        detail = str(entry.get("detail") or "")
        if event == "complete":
            return str(at)
        if event == "status":
            # e.g. gate_failed → cancelled; … → gate_failed
            if "→ cancelled" in detail or "→ gate_failed" in detail or "→ blocked" in detail or "→ passed" in detail:
                return str(at)
            if detail.endswith("cancelled") or "cancelled" in detail.lower():
                return str(at)
    # Fallback: last history stamp
    for entry in reversed(history):
        if isinstance(entry, dict) and entry.get("at"):
            return str(entry["at"])
    return None


def _reset_pack_ready(workspace: Path, materials_pack: str | None) -> dict[str, Any]:
    """Force GATE_FAILED_* / USED_* pack back to READY for retry (desktop-local patch)."""
    if not materials_pack:
        return {"ok": False, "reason": "no materials_pack"}
    pack_path = workspace / materials_pack
    if not pack_path.is_dir():
        return {"ok": False, "reason": f"missing {materials_pack}"}
    pack_name = pack_path.name
    domain_dir = pack_path.parent
    updated: list[str] = []
    for path in (pack_path / "CATALOG.json", domain_dir / "CATALOG.json"):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        changed = False
        status = str(data.get("status") or "")
        if status.startswith("GATE_FAILED") or status.startswith("USED_") or status == "IN_PROGRESS":
            data["status"] = "READY"
            data.pop("used_at", None)
            data.pop("used_sample", None)
            data.pop("used_result", None)
            changed = True
        for item in data.get("packs") or []:
            if not isinstance(item, dict):
                continue
            if item.get("pack") != pack_name and not str(item.get("path") or "").rstrip("/").endswith(f"/{pack_name}"):
                continue
            st = str(item.get("status") or "")
            if st.startswith("GATE_FAILED") or st.startswith("USED_") or st == "IN_PROGRESS":
                item["status"] = "READY"
                item.pop("used_at", None)
                item.pop("used_sample", None)
                item.pop("used_result", None)
                changed = True
        if changed:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            try:
                updated.append(str(path.relative_to(workspace)))
            except ValueError:
                updated.append(str(path))
    return {"ok": True, "updated": updated, "materials_pack": materials_pack}


def _reset_sample_work_for_retry(workspace: Path, sample_dir: str | None) -> dict[str, Any]:
    """Clear terminal candidate state so the next run regenerates instead of short-circuiting.

    workflow/batch_run.run_candidate returns immediately when candidate_state.json
    status is in {precheck_failed, ...}. Manual requeue must wipe raw + state
    (keep source/ and archived attempts/).
    """
    if not sample_dir:
        return {"ok": False, "reason": "no sample_dir"}
    base = Path(sample_dir)
    if not base.is_absolute():
        base = workspace / base
    if not base.is_dir():
        return {"ok": False, "reason": f"missing {sample_dir}"}

    def _rel(path: Path) -> str:
        try:
            return str(path.relative_to(workspace))
        except ValueError:
            return str(path)

    cleared: list[str] = []
    results_path = base / "work" / "candidate_results.json"
    if results_path.is_file():
        results_path.unlink()
        cleared.append(_rel(results_path))

    candidates_root = base / "work" / "candidates"
    if candidates_root.is_dir():
        for candidate in sorted(candidates_root.iterdir()):
            if not candidate.is_dir():
                continue
            work = candidate / "work"
            raw = work / "raw"
            state_path = work / "candidate_state.json"
            if raw.exists():
                shutil.rmtree(raw)
                cleared.append(_rel(raw))
            if state_path.is_file():
                state_path.unlink()
                cleared.append(_rel(state_path))

    return {"ok": True, "sample_dir": sample_dir, "cleared": cleared}


def set_task_status(
    workspace: Path,
    *,
    task_id: str,
    status: str,
    reason: str = "",
) -> dict[str, Any]:
    if status not in {"queued", "cancelled"}:
        raise ValueError("status must be queued or cancelled")
    queue = read_queue(workspace)
    task = next((t for t in (queue.get("tasks") or []) if isinstance(t, dict) and t.get("id") == task_id), None)
    if not task:
        raise KeyError(f"task not found: {task_id}")
    if status == "queued":
        if not can_requeue(task, queue=queue):
            sibling = find_pack_sibling(queue, task)
            if sibling:
                raise PermissionError(
                    f"task cannot be requeued: pack/slug already owned by "
                    f"{sibling.get('id')} ({sibling.get('status')})"
                )
            raise PermissionError("task cannot be requeued")
    elif status == "cancelled":
        if not can_cancel(task):
            raise PermissionError("only non-accuracy gate failures can be cancelled")
        # Cancelling a duplicate loser is always allowed when can_cancel is true.
        # Also allow cancel of accuracy failures? unchanged.
    row = _qw(
        workspace,
        [
            "set-status",
            "--task-id",
            task_id,
            "--status",
            status,
            "--by",
            "desktop",
            "--reason",
            reason or f"manual → {status}",
        ],
    )
    # Ensure lease fields cleared when re-queuing.
    if status == "queued":
        sample_reset = _reset_sample_work_for_retry(workspace, task.get("sample_dir"))
        queue2 = read_queue(workspace)
        for t in queue2.get("tasks") or []:
            if isinstance(t, dict) and t.get("id") == task_id:
                t["status"] = "queued"
                t["worker_id"] = None
                t["claimed_at"] = None
                t["review_status"] = "queued"
                t["failure_reason"] = None
                t["avg_accuracy"] = None
                t["candidate_results"] = []
                t["rewrite_used"] = 0
                t["candidate_id"] = None
                t["candidate_index"] = 0
                t["attempt"] = 0
                break
        path = workspace / "queue" / "queue.json"
        path.write_text(json.dumps(queue2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        materials = _reset_pack_ready(workspace, task.get("materials_pack"))
        row = next((t for t in (queue2.get("tasks") or []) if t.get("id") == task_id), row)
        return {"task": row, "materials": materials, "sample_reset": sample_reset}
    return {"task": row, "materials": None}


def set_tasks_status(
    workspace: Path,
    *,
    task_ids: list[str],
    status: str,
    reason: str = "",
) -> dict[str, Any]:
    updated: list[str] = []
    errors: list[dict[str, str]] = []
    for task_id in task_ids:
        tid = str(task_id or "").strip()
        if not tid:
            continue
        try:
            set_task_status(workspace, task_id=tid, status=status, reason=reason)
            updated.append(tid)
        except (KeyError, PermissionError, ValueError, RuntimeError) as exc:
            errors.append({"task_id": tid, "error": str(exc)})
    return {
        "ok": not errors or bool(updated),
        "updated": updated,
        "errors": errors,
        "count": len(updated),
    }


def human_reject_many(
    workspace: Path,
    *,
    task_ids: list[str],
    reason: str = "",
    requeue: bool = False,
) -> dict[str, Any]:
    """Batch human reject; continues on per-task errors."""
    updated: list[str] = []
    errors: list[dict[str, str]] = []
    for task_id in task_ids:
        tid = str(task_id or "").strip()
        if not tid:
            continue
        try:
            human_reject_passed(workspace, task_id=tid, reason=reason, requeue=requeue)
            updated.append(tid)
        except (KeyError, PermissionError, ValueError, OSError, RuntimeError) as exc:
            errors.append({"task_id": tid, "error": str(exc)})
    return {
        "ok": not errors or bool(updated),
        "updated": updated,
        "errors": errors,
        "count": len(updated),
    }


def _is_delivery_dir(path: Path) -> bool:
    return (path / "sample_qc.md").is_file() and (path / "questions" / "q01" / "data" / "gold.json").is_file()


def find_passed_delivery_dir(workspace: Path, task: dict[str, Any]) -> Path | None:
    """Locate samples/ delivery package for a passed task."""
    candidates: list[Path] = []
    for row in _candidate_rows(task):
        sd = str(row.get("sample_dir") or "")
        if not sd:
            continue
        p = Path(sd)
        if not p.is_absolute():
            p = workspace / p
        if "samples" in p.parts and "work" not in p.parts and _is_delivery_dir(p):
            candidates.append(p)
    if candidates:
        return candidates[0]
    work = str(task.get("sample_dir") or "")
    if work:
        base = Path(work).name
        guess = workspace / "samples" / f"{base}-c01"
        if _is_delivery_dir(guess):
            return guess
        # legacy delivery without -c01
        legacy = workspace / "samples" / base
        if _is_delivery_dir(legacy):
            return legacy
    return None


def find_pending_delivery_dir(workspace: Path, task: dict[str, Any]) -> Path | None:
    """Locate 0/8 holding package in archive/pending-review (or legacy failed-samples)."""
    for row in _candidate_rows(task):
        sd = str(row.get("sample_dir") or "")
        if not sd:
            continue
        p = Path(sd)
        if not p.is_absolute():
            p = workspace / p
        if _is_delivery_dir(p) and ("pending-review" in p.parts or "failed-samples" in p.parts):
            return p
    work = str(task.get("sample_dir") or "")
    if work:
        base = Path(work).name
        for parent in ("archive/pending-review", "archive/failed-samples"):
            guess = workspace / parent / f"{base}-c01"
            if _is_delivery_dir(guess):
                return guess
    return None


def human_reject_passed(
    workspace: Path,
    *,
    task_id: str,
    reason: str = "",
    requeue: bool = False,
) -> dict[str, Any]:
    """Human QC reject: move out of samples/ or pending-review into failed-samples, mark gate_failed."""
    workspace = workspace.resolve()
    queue_path = workspace / "queue" / "queue.json"
    queue = read_queue(workspace)
    task = next((t for t in (queue.get("tasks") or []) if isinstance(t, dict) and t.get("id") == task_id), None)
    if not task:
        raise KeyError(f"task not found: {task_id}")
    if not can_human_reject(task):
        raise PermissionError("only passed or pending-review (0/8) tasks can be human-rejected")

    pending_cand = find_manual_review_candidate(task)
    is_pending = str(task.get("status") or "") == "blocked" and pending_cand is not None
    reason_text = (reason or "").strip() or "human QC reject after pass"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    delivery = find_pending_delivery_dir(workspace, task) if is_pending else find_passed_delivery_dir(workspace, task)
    moved: dict[str, Any] | None = None
    if delivery is not None and delivery.is_dir():
        failed_root = workspace / "archive" / "failed-samples"
        failed_root.mkdir(parents=True, exist_ok=True)
        already_failed = "failed-samples" in delivery.parts
        dest = delivery if already_failed else failed_root / delivery.name
        if not already_failed:
            if dest.exists():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                dest = failed_root / f"{delivery.name}-revoked-{stamp}"
            shutil.move(str(delivery), str(dest))
        try:
            from_rel = str(delivery.relative_to(workspace))
        except ValueError:
            from_rel = str(delivery)
        try:
            rel = str(dest.relative_to(workspace))
        except ValueError:
            rel = str(dest)
        origin = "archive/pending-review" if is_pending else f"samples/{delivery.name}"
        if already_failed and is_pending:
            origin = from_rel
        note = (
            f"# Human reject\n\n"
            f"- task_id: `{task_id}`\n"
            f"- revoked_at: `{now}`\n"
            f"- from: `{origin}`\n"
            f"- reason: {reason_text}\n"
        )
        (dest / "HUMAN_REJECT.md").write_text(note, encoding="utf-8")
        pending_note = dest / "PENDING_REVIEW.md"
        if pending_note.is_file():
            pending_note.unlink()
        qc = dest / "sample_qc.md"
        if qc.is_file():
            qc.write_text(
                qc.read_text(encoding="utf-8").rstrip()
                + f"\n\n## Human reject\n\n- at: `{now}`\n- reason: {reason_text}\n",
                encoding="utf-8",
            )
        moved = {"from": from_rel, "to": rel}
        for row in _candidate_rows(task):
            row_sd = str(row.get("sample_dir") or "")
            if (
                row_sd.endswith(delivery.name)
                or row_sd.endswith(f"samples/{delivery.name}")
                or "pending-review" in row_sd.replace("\\", "/")
                or (is_pending and "failed-samples" in row_sd.replace("\\", "/") and row_sd.endswith(delivery.name))
            ):
                row["sample_dir"] = rel
                row["status"] = "gate_failed"
                row["review_status"] = "human_reject"

    prev = str(task.get("status") or "passed")
    task["status"] = "gate_failed"
    task["review_status"] = "human_reject"
    task["failure_reason"] = reason_text
    task["worker_id"] = None
    hist = task.setdefault("history", [])
    if isinstance(hist, list):
        hist.append(
            {
                "at": now,
                "event": "status",
                "by": "desktop",
                "detail": f"{prev} → gate_failed; human reject: {reason_text}",
            }
        )
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    materials = _reset_pack_ready(workspace, task.get("materials_pack"))
    requeue_result = None
    if requeue:
        requeue_result = set_task_status(
            workspace,
            task_id=task_id,
            status="queued",
            reason=f"human reject then requeue: {reason_text}",
        )

    return {
        "ok": True,
        "task_id": task_id,
        "moved": moved,
        "materials": materials,
        "requeue": requeue_result,
        "task": (requeue_result or {}).get("task")
        or next((t for t in (read_queue(workspace).get("tasks") or []) if t.get("id") == task_id), task),
    }
