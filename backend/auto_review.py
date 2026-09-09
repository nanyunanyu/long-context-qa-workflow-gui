"""LLM auto-review for blocked 0/8 pending-review tasks."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .llm_client import chat_json, load_prompt
from .queue_ops import (
    can_review_pass,
    find_manual_review_candidate,
    find_pending_delivery_dir,
    human_reject_passed,
    patch_task,
    read_queue,
)
from .run_control import clear_events, emit_event, mark_idle, mark_running, update_state, utcnow
from .runner import load_keys, run_review_pass
from .settings import get_settings_public


class AutoReviewAborted(ValueError):
    """Model output could not be parsed into pass/fail; queue must stay unchanged."""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _first_file(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def _gold_excerpt(gold: dict[str, Any]) -> dict[str, Any]:
    evidence = gold.get("evidence") if isinstance(gold.get("evidence"), list) else []
    slim_ev = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        slim_ev.append(
            {
                "id": item.get("id"),
                "doc": item.get("doc"),
                "position": item.get("position"),
                "text": item.get("text") or item.get("quote") or "",
            }
        )
    return {
        "question": gold.get("question") or "",
        "answer": gold.get("answer") or "",
        "answer_aliases": gold.get("answer_aliases") or gold.get("accepted_answers") or [],
        "evidence": slim_ev,
        "evidence_reasoning": gold.get("evidence_reasoning") or gold.get("reasoning") or "",
        "domain": gold.get("domain"),
        "question_type": gold.get("question_type"),
    }


def _rollout_excerpts(rollout_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    files = sorted(rollout_dir.glob("rollout_*.json")) if rollout_dir.is_dir() else []
    for path in files:
        data = _read_json(path)
        rows.append(
            {
                "rollout_id": data.get("rollout_id") or path.stem,
                "predicted_answer": data.get("predicted_answer") or "",
                "raw_output": (data.get("raw_output") or "")[:400],
                "score": data.get("score"),
                "judge_note": data.get("judge_note") or "",
            }
        )
    return rows


def _candidate_raw(workspace: Path, cand: dict[str, Any]) -> Path | None:
    rel = str(cand.get("candidate_dir") or "")
    if not rel:
        return None
    path = Path(rel)
    if not path.is_absolute():
        path = workspace / path
    raw = path / "work" / "raw"
    return raw if raw.is_dir() else None


def collect_review_bundle(workspace: Path, task: dict[str, Any]) -> dict[str, Any]:
    cand = find_manual_review_candidate(task) or {}
    pending = find_pending_delivery_dir(workspace, task)
    cand_root: Path | None = None
    rel = str(cand.get("candidate_dir") or "")
    if rel:
        cand_root = Path(rel) if Path(rel).is_absolute() else workspace / rel

    gold_path = _first_file(
        [
            *(
                [
                    pending / "questions" / "q01" / "data" / "gold.json",
                ]
                if pending
                else []
            ),
            *(
                [
                    cand_root / "questions" / "q01" / "data" / "gold.json",
                    cand_root / "source" / "gold.json",
                    cand_root / "work" / "raw" / "gold.json",
                ]
                if cand_root
                else []
            ),
        ]
    )
    if not gold_path:
        raise FileNotFoundError("gold.json not found for auto-review")
    gold = _read_json(gold_path)

    context_path = _first_file(
        [
            *(
                [
                    pending / "main_file" / "context_000001.txt",
                    pending / "main_file" / "context_000001.md",
                ]
                if pending
                else []
            ),
            *(
                [
                    cand_root / "source" / "context.md",
                    cand_root / "source" / "context.txt",
                    cand_root / "main_file" / "context_000001.txt",
                ]
                if cand_root
                else []
            ),
        ]
    )
    context = context_path.read_text(encoding="utf-8") if context_path else ""

    rollout_dir = None
    if pending and (pending / "questions" / "q01" / "rollouts").is_dir():
        rollout_dir = pending / "questions" / "q01" / "rollouts"
    elif cand_root and (cand_root / "questions" / "q01" / "rollouts").is_dir():
        rollout_dir = cand_root / "questions" / "q01" / "rollouts"
    rollouts = _rollout_excerpts(rollout_dir) if rollout_dir else []

    raw = _candidate_raw(workspace, cand)
    judge = {}
    if raw:
        judge = _read_json(raw / "judge_summary.json")
    if not judge and pending:
        judge = _read_json(pending / "questions" / "q01" / "data" / "difficulty_result.json")

    return {
        "gold_path": gold_path,
        "pending": pending,
        "candidate_root": cand_root,
        "gold": gold,
        "context": context,
        "rollouts": rollouts,
        "judge": judge,
        "candidate": cand,
    }


def build_review_user_message(bundle: dict[str, Any], task: dict[str, Any]) -> str:
    judge = bundle.get("judge") if isinstance(bundle.get("judge"), dict) else {}
    payload = {
        "task_id": task.get("id"),
        "slug": task.get("slug"),
        "avg_accuracy": judge.get("avg_accuracy", task.get("avg_accuracy")),
        "gold": _gold_excerpt(bundle.get("gold") or {}),
        "rollout_answers": bundle.get("rollouts") or [],
        "judge_summary": {
            "avg_accuracy": judge.get("avg_accuracy"),
            "n": judge.get("n") or judge.get("n_scored"),
            "correct": judge.get("correct"),
        },
    }
    gold_json = json.dumps(payload, ensure_ascii=False, indent=2)
    context = bundle.get("context") or ""
    return (
        "TASK META AND GOLD (JSON):\n"
        f"{gold_json}\n\n"
        "LONG CONTEXT START\n"
        f"{context}\n"
        "LONG CONTEXT END\n"
    )


def _normalize_verdict(raw: dict[str, Any]) -> dict[str, Any]:
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in {"pass", "fail"}:
        raise AutoReviewAborted(f"invalid verdict: {raw.get('verdict')!r}")
    reason = str(raw.get("reason") or "").strip() or (
        "题/金标无误，0/8 视为模型答错" if verdict == "pass" else "题/金标存在问题"
    )
    return {
        "verdict": verdict,
        "reason": reason,
        "gold_supported": bool(raw.get("gold_supported")),
        "question_ok": bool(raw.get("question_ok")),
    }


def _write_review_file(bundle: dict[str, Any], record: dict[str, Any]) -> None:
    targets: list[Path] = []
    pending = bundle.get("pending")
    if isinstance(pending, Path) and pending.is_dir():
        targets.append(pending / "questions" / "q01" / "data" / "auto_review.json")
    cand_root = bundle.get("candidate_root")
    if isinstance(cand_root, Path) and cand_root.is_dir():
        targets.append(cand_root / "work" / "raw" / "auto_review.json")
        targets.append(cand_root / "questions" / "q01" / "data" / "auto_review.json")
    text = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def run_auto_review(
    workspace: Path,
    task_id: str,
    *,
    provider: str = "live",
    fixture_root: Path | None = None,
    source_type: str = "public technical/government documentation",
) -> dict[str, Any]:
    workspace = workspace.resolve()
    queue = read_queue(workspace)
    task = next((t for t in (queue.get("tasks") or []) if isinstance(t, dict) and t.get("id") == task_id), None)
    if not task:
        raise KeyError(f"task not found: {task_id}")
    if not can_review_pass(task):
        raise PermissionError("task is not eligible for auto-review (need blocked + manual_review 0/8)")

    settings = get_settings_public()
    model = ((settings.get("roles") or {}).get("review") or {}).get("model") or ""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    running_record = {
        "verdict": None,
        "reason": "",
        "gold_supported": None,
        "question_ok": None,
        "model": model,
        "reviewed_at": now,
        "action": "running",
    }
    patch_task(workspace, task_id, {"auto_review": running_record})
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    clear_events(workspace)
    mark_running(
        workspace,
        limit=1,
        workers=1,
        run_id=run_id,
        prefer_cold=False,
        provider=provider,
        skip_stage=True,
    )
    update_state(workspace, message="auto-review")
    emit_event(workspace, step="auto-review", status="started", task_id=task_id, slug=task.get("slug"))
    if provider == "live":
        load_keys()

    try:
        try:
            bundle = collect_review_bundle(workspace, task)
        except (OSError, FileNotFoundError, json.JSONDecodeError) as exc:
            record = {
                **running_record,
                "verdict": None,
                "reason": f"missing review inputs: {exc}",
                "action": "aborted",
                "reviewed_at": utcnow(),
            }
            patch_task(workspace, task_id, {"auto_review": record})
            emit_event(
                workspace,
                step="auto-review",
                status="error",
                task_id=task_id,
                slug=task.get("slug"),
                detail="aborted",
            )
            mark_idle(workspace, f"auto-review aborted: {exc}")
            return {"ok": False, "task_id": task_id, "slug": task.get("slug"), "auto_review": record, "aborted": True}

        try:
            raw = chat_json(
                "review",
                system=load_prompt("auto_review.txt"),
                user=build_review_user_message(bundle, task),
                timeout=600,
                max_tokens=2048,
            )
            parsed = _normalize_verdict(raw)
        except AutoReviewAborted as exc:
            record = {
                **running_record,
                "verdict": None,
                "reason": str(exc),
                "action": "aborted",
                "reviewed_at": utcnow(),
            }
            _write_review_file(bundle, record)
            patch_task(workspace, task_id, {"auto_review": record})
            emit_event(
                workspace,
                step="auto-review",
                status="error",
                task_id=task_id,
                slug=task.get("slug"),
                detail="aborted",
            )
            mark_idle(workspace, f"auto-review aborted: {exc}")
            return {"ok": False, "task_id": task_id, "slug": task.get("slug"), "auto_review": record, "aborted": True}
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            record = {
                **running_record,
                "verdict": None,
                "reason": f"parse/call failed: {exc}",
                "action": "aborted",
                "reviewed_at": utcnow(),
            }
            try:
                _write_review_file(bundle, record)
            except Exception:
                pass
            patch_task(workspace, task_id, {"auto_review": record})
            emit_event(
                workspace,
                step="auto-review",
                status="error",
                task_id=task_id,
                slug=task.get("slug"),
                detail="aborted",
            )
            mark_idle(workspace, f"auto-review aborted: {exc}")
            return {"ok": False, "task_id": task_id, "slug": task.get("slug"), "auto_review": record, "aborted": True}

        action = "promoted" if parsed["verdict"] == "pass" else "rejected"
        record = {
            **parsed,
            "model": model,
            "reviewed_at": utcnow(),
            "action": action,
        }
        _write_review_file(bundle, record)

        try:
            if parsed["verdict"] == "pass":
                follow = run_review_pass(
                    workspace,
                    task_id,
                    provider=provider,
                    fixture_root=fixture_root,
                    source_type=source_type,
                )
            else:
                follow = human_reject_passed(
                    workspace,
                    task_id=task_id,
                    reason=f"auto-review: {parsed['reason']}",
                    requeue=False,
                )
        except Exception as exc:
            record = {**record, "action": "error", "exec_error": str(exc)}
            _write_review_file(bundle, record)
            patch_task(workspace, task_id, {"auto_review": record})
            emit_event(
                workspace,
                step="auto-review",
                status="error",
                task_id=task_id,
                slug=task.get("slug"),
                detail="exec_failed",
            )
            raise
        patched = patch_task(workspace, task_id, {"auto_review": record})
        emit_event(
            workspace,
            step="auto-review",
            status="finished",
            task_id=task_id,
            slug=task.get("slug"),
            detail=action,
        )
        mark_idle(workspace, f"auto-review {action}")
        return {
            "ok": True,
            "task_id": task_id,
            "slug": patched.get("slug") or task.get("slug"),
            "auto_review": record,
            "follow": follow,
        }
    except Exception as exc:
        mark_idle(workspace, f"auto-review failed: {exc}")
        raise
