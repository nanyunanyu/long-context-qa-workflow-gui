"""GUI batch runner: wraps workflow scripts via subprocess.

Claim-one workers, soft stop, hard interrupt, and JSONL progress live here.
queue_worker / produce_one stay unmodified; archive/runs slim-snapshot lives in
workflow.batch_run.archive_run (this runner binds ROOT/RUNS_ROOT and calls it).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .paths import code_root
from .settings import apply_to_environ, key_status as settings_key_status
from .queue_ops import can_review_pass, find_manual_review_candidate, release_active_tasks
from .run_control import (
    Interrupted,
    StopRequested,
    bump_claimed,
    clear_events,
    emit_event,
    kill_pid,
    current_status,
    mark_idle,
    mark_running,
    raise_if_paused,
    register_child,
    should_interrupt,
    should_pause,
    unregister_child,
    update_state,
    utcnow,
    validate_batch_bounds,
)

_CODE = code_root()
if str(_CODE) not in sys.path:
    sys.path.insert(0, str(_CODE))

from workflow import batch_run  # noqa: E402


class BatchBind:
    """Temporarily point the in-process batch_run helpers at a workspace copy."""

    FIELDS = (
        "ROOT",
        "QUEUE_WORKER",
        "PRODUCE_ONE",
        "DEFAULT_SYSTEM",
        "RUNS_ROOT",
        "FAILED_ROOT",
        "PENDING_REVIEW_ROOT",
        "run",
        "produce",
    )

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self._saved: dict[str, Any] = {}

    def __enter__(self):
        for name in self.FIELDS:
            self._saved[name] = getattr(batch_run, name)
        batch_run.ROOT = self.workspace
        batch_run.QUEUE_WORKER = self.workspace / "workflow" / "queue_worker.py"
        batch_run.PRODUCE_ONE = self.workspace / "scripts" / "produce_one.py"
        batch_run.DEFAULT_SYSTEM = self.workspace / "scripts" / "prompts" / "generate_qa.txt"
        batch_run.RUNS_ROOT = self.workspace / "archive" / "runs"
        batch_run.FAILED_ROOT = self.workspace / "archive" / "failed-samples"
        batch_run.PENDING_REVIEW_ROOT = self.workspace / "archive" / "pending-review"
        batch_run.run = lambda cmd, env=None, timeout=None: popen_run(cmd, self.workspace, env=env, timeout=timeout)
        batch_run.produce = lambda *args, **kwargs: produce_step(self.workspace, *args, **kwargs)
        return batch_run

    def __exit__(self, *exc):
        for name, value in self._saved.items():
            setattr(batch_run, name, value)


def load_keys() -> None:
    """Load legacy ai-keys.env then overlay GUI desktop settings."""
    key_file = Path.home() / ".config" / "ai-keys.env"
    if key_file.exists():
        for line in key_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            line = line[7:] if line.startswith("export ") else line
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    applied = apply_to_environ(os.environ)
    for key, value in applied.items():
        os.environ[key] = value


def key_status() -> dict[str, Any]:
    return settings_key_status()


def popen_run(
    cmd: list[str],
    workspace: Path,
    *,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    on_tick=None,
) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    merged = apply_to_environ(env or os.environ)
    merged.setdefault("LCQA_ROOT", str(workspace))
    merged.setdefault("LCQA_CODE_ROOT", str(code_root()))
    proc = subprocess.Popen(
        cmd,
        cwd=str(workspace),
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    register_child(proc.pid, workspace)
    try:
        deadline = time.time() + timeout if timeout else None
        while True:
            if should_interrupt(workspace):
                kill_pid(proc.pid)
                stdout, stderr = proc.communicate(timeout=8)
                raise Interrupted(f"killed {' '.join(cmd[:4])}")
            remaining = None
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    kill_pid(proc.pid)
                    stdout, stderr = proc.communicate(timeout=8)
                    raise TimeoutError(f"timeout: {' '.join(cmd[:4])}")
            if on_tick is not None:
                try:
                    on_tick()
                except Exception:
                    pass
            slice_timeout = 0.4 if remaining is None else min(0.4, max(0.05, remaining))
            try:
                stdout, stderr = proc.communicate(timeout=slice_timeout)
                break
            except subprocess.TimeoutExpired:
                continue
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    finally:
        unregister_child(proc.pid, workspace)


_GEN_CALL_COUNTS: dict[str, int] = {}


def _gen_call_key(task: dict[str, Any], candidate_id: str, attempt: int) -> str:
    return f"{task.get('id') or task.get('slug')}:{candidate_id}:a{attempt}"


def _next_gen_call(task: dict[str, Any], candidate_id: str, attempt: int) -> int:
    key = _gen_call_key(task, candidate_id, attempt)
    _GEN_CALL_COUNTS[key] = _GEN_CALL_COUNTS.get(key, 0) + 1
    return _GEN_CALL_COUNTS[key]


def _judge_progress_detail(raw: Path) -> str:
    done = 0
    correct = 0
    for i in range(1, 9):
        path = raw / f"judge_{i}.json"
        if not path.is_file():
            continue
        done += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        verdict = payload
        if isinstance(payload.get("correct"), bool):
            if payload["correct"]:
                correct += 1
        elif isinstance(payload.get("content"), str):
            # some dumps wrap the model response
            text = payload["content"]
            if '"correct": true' in text.lower() or '"correct":true' in text.lower():
                correct += 1
    detail = f"{done}/8"
    if done:
        detail = f"{detail} · 已对 {correct}"
    return detail


def _generate_progress_detail(raw: Path, *, attempt: int, call_n: int) -> str:
    parts: list[str] = [f"a{attempt}"]
    if call_n > 1:
        parts.append(f"重试{call_n}")
    if (raw / "qa_validate.json").is_file():
        parts.append("证据校验")
    elif (raw / "generate_qa.json").is_file() or (raw / "qa_parsed.json").is_file():
        parts.append("解析题目")
    else:
        parts.append("调用出题模型")
    return " · ".join(parts)


def _ablation_progress_detail(raw: Path) -> str:
    modes = ("none", "truncated", "full")
    done = [m for m in modes if (raw / f"ablation_{m}.json").is_file()]
    if not done:
        return "准备中"
    return f"{done[-1]}（{len(done)}/{len(modes)}）"


def _parse_rollout_range(extra: list[str] | None) -> tuple[int, int]:
    start, n = 1, 8
    if not extra:
        return start, n
    args = list(extra)
    i = 0
    while i < len(args):
        if args[i] == "--start" and i + 1 < len(args):
            start = int(args[i + 1])
            i += 2
            continue
        if args[i] == "--n" and i + 1 < len(args):
            n = int(args[i + 1])
            i += 2
            continue
        i += 1
    return start, n


def produce_step(
    workspace: Path,
    step: str,
    sample_workspace: Path,
    task: dict[str, Any],
    *,
    candidate_id: str,
    candidate_index: int,
    attempt: int,
    provider: str,
    fixture_root: Path | None,
    system: Path | None = None,
    extra: list[str] | None = None,
    timeout: int = 3600,
) -> None:
    raise_if_paused(workspace)
    if step == "rollout":
        start, n = _parse_rollout_range(extra)
        for i in range(start, n + 1):
            raise_if_paused(workspace)
            _produce_once(
                workspace,
                step,
                sample_workspace,
                task,
                candidate_id=candidate_id,
                candidate_index=candidate_index,
                attempt=attempt,
                provider=provider,
                fixture_root=fixture_root,
                system=system,
                extra=["--start", str(i), "--n", str(i)],
                timeout=timeout,
                detail=f"{i}/8",
            )
            raise_if_paused(workspace)
        return
    gen_call = _next_gen_call(task, candidate_id, attempt) if step == "generate" else 1
    _produce_once(
        workspace,
        step,
        sample_workspace,
        task,
        candidate_id=candidate_id,
        candidate_index=candidate_index,
        attempt=attempt,
        provider=provider,
        fixture_root=fixture_root,
        system=system,
        extra=extra,
        timeout=timeout,
        gen_call=gen_call,
    )
    raise_if_paused(workspace)


def _produce_once(
    workspace: Path,
    step: str,
    sample_workspace: Path,
    task: dict[str, Any],
    *,
    candidate_id: str,
    candidate_index: int,
    attempt: int,
    provider: str,
    fixture_root: Path | None,
    system: Path | None,
    extra: list[str] | None,
    timeout: int,
    detail: str = "",
    gen_call: int = 1,
) -> None:
    cmd = [
        "python3",
        str(workspace / "scripts" / "produce_one.py"),
        step,
        "--sample-dir",
        str(sample_workspace),
        "--sample-id",
        f"{task.get('sample_id') or task['slug']}-{candidate_id}-a{attempt}",
        "--provider",
        provider,
        "--candidate-id",
        candidate_id,
        "--candidate-index",
        str(candidate_index),
        "--attempt",
        str(attempt),
        "--context-unit-id",
        str(task.get("context_unit_id") or task["slug"]),
    ]
    if fixture_root:
        cmd += ["--fixture-root", str(fixture_root)]
    if system:
        cmd += ["--system", str(system)]
    if extra:
        cmd += extra
    env = batch_run.child_env(provider, step)
    env["LCQA_TASK_ID"] = str(task.get("id") or "")
    env["LCQA_TASK_SLUG"] = str(task.get("slug") or "")
    env["LCQA_ROOT"] = str(workspace)
    env["LCQA_CODE_ROOT"] = str(code_root())

    raw = Path(sample_workspace) / "work" / "raw"
    meta = {"attempt": attempt, "candidate_id": candidate_id}

    def current_detail() -> str:
        if detail:
            return detail
        if step == "generate":
            return _generate_progress_detail(raw, attempt=attempt, call_n=gen_call)
        if step == "judge":
            return _judge_progress_detail(raw)
        if step == "ablation":
            return _ablation_progress_detail(raw)
        return ""

    last_detail = {"value": ""}

    def tick() -> None:
        d = current_detail()
        if d == last_detail["value"]:
            return
        last_detail["value"] = d
        emit_event(
            workspace,
            step=step,
            status="started",
            task_id=task.get("id"),
            slug=task.get("slug"),
            detail=d,
            extra=meta,
        )

    initial = current_detail()
    last_detail["value"] = initial
    emit_event(
        workspace,
        step=step,
        status="started",
        task_id=task.get("id"),
        slug=task.get("slug"),
        detail=initial,
        extra=meta,
    )
    proc = popen_run(
        cmd,
        workspace,
        env=env,
        timeout=timeout,
        on_tick=tick if step in {"generate", "judge", "ablation"} else None,
    )
    final_detail = current_detail() or detail
    if proc.returncode:
        emit_event(
            workspace,
            step=step,
            status="error",
            task_id=task.get("id"),
            slug=task.get("slug"),
            detail=final_detail,
            extra=meta,
        )
        raise RuntimeError(
            f"produce_one {step} failed ({proc.returncode}):\n{(proc.stderr or '')[-3000:]}\n{(proc.stdout or '')[-2000:]}"
        )
    emit_event(
        workspace,
        step=step,
        status="finished",
        task_id=task.get("id"),
        slug=task.get("slug"),
        detail=final_detail,
        extra=meta,
    )


def qw_json(workspace: Path, args: list[str]) -> dict[str, Any]:
    proc = popen_run(["python3", str(workspace / "workflow" / "queue_worker.py"), *args], workspace)
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


def claim_one(workspace: Path, worker_id: str, *, task_id: str | None = None) -> dict[str, Any] | None:
    args = ["claim", "--worker-id", worker_id]
    if task_id:
        args += ["--task-id", task_id]
    try:
        return qw_json(workspace, args)
    except RuntimeError as exc:
        if "no queued task" in str(exc):
            return None
        raise


def release_task(workspace: Path, task_id: str, reason: str) -> None:
    try:
        qw_json(workspace, ["release", "--task-id", task_id, "--by", "desktop", "--reason", reason])
    except Exception:
        pass


def run_one_controlled(
    workspace: Path,
    task: dict[str, Any],
    *,
    system: Path,
    gen_retries: int,
    provider: str,
    fixture_root: Path | None,
    source_type: str,
    retry_technical: bool,
) -> dict[str, Any]:
    worker = task.get("worker_id") or f"desktop-{os.getpid()}"
    result: dict[str, Any] = {"task_id": task["id"], "slug": task["slug"], "status": "error", "candidates": []}
    try:
        emit_event(workspace, step="prepare", status="started", task_id=task["id"], slug=task["slug"])
        qw_json(workspace, ["prepare", "--task-id", task["id"], "--by", worker])
        emit_event(workspace, step="prepare", status="finished", task_id=task["id"], slug=task["slug"])
        raise_if_paused(workspace)
        result["candidates"] = [
            batch_run.run_candidate(
                task,
                index=i,
                system=system,
                gen_retries=gen_retries,
                provider=provider,
                fixture_root=fixture_root,
                source_type=source_type,
                retry_technical=retry_technical,
            )
            for i in range(1, batch_run.CANDIDATES_PER_CONTEXT + 1)
        ]
        statuses = {row.get("status") for row in result["candidates"]}
        if "passed" in statuses:
            result["status"] = "passed"
            queue_result = "passed"
        elif "manual_review" in statuses:
            result["status"] = "manual_review"
            queue_result = "blocked"
        elif "technical_failed" in statuses:
            result["status"] = "technical_failed"
            queue_result = "blocked"
        else:
            result["status"] = "gate_failed"
            queue_result = "gate_failed"
        result["provider"] = provider
        result["review_status"] = result["status"]
        summary_path = batch_run.resolved(task["sample_dir"]) / "work" / "candidate_results.json"
        batch_run.write_json(summary_path, result)
        emit_event(workspace, step="complete", status="finished", task_id=task["id"], slug=task["slug"], detail=result["status"])
        qw_json(
            workspace,
            [
                "complete",
                "--task-id",
                task["id"],
                "--result",
                queue_result,
                "--sample-dir",
                task["sample_dir"],
                "--candidate-results",
                str(summary_path),
                "--by",
                worker,
            ],
        )
        return result
    except (StopRequested, Interrupted):
        release_task(workspace, task["id"], "paused by desktop runner")
        emit_event(workspace, step="paused", status="finished", task_id=task["id"], slug=task["slug"])
        result["status"] = "paused"
        return result
    except Exception as exc:
        result["error"] = str(exc)
        result["traceback"] = traceback.format_exc()[-3000:]
        try:
            qw_json(workspace, ["complete", "--task-id", task["id"], "--result", "blocked", "--by", worker])
        except Exception:
            pass
        emit_event(workspace, step="complete", status="error", task_id=task["id"], slug=task["slug"], detail=str(exc))
        return result


def _selected_stage_error(domain_key: str, pack_name: str, proc: subprocess.CompletedProcess) -> str:
    prefix = f"{domain_key}/{pack_name}"
    try:
        payload = json.loads(proc.stdout or "")
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict):
        bits: list[str] = []
        for row in payload.get("errors") or []:
            if isinstance(row, dict):
                bits.append(str(row.get("error") or row.get("reason") or row))
            elif row:
                bits.append(str(row))
        if bits:
            return f"{prefix}: " + "; ".join(bits)
        if payload.get("staged") == 0:
            return f"{prefix}: staged 0 packs"
    tail = (proc.stderr or proc.stdout or "").strip()[-800:]
    if tail:
        return f"{prefix}: {tail}"
    return f"{prefix}: staged 0 packs"


def _stage_and_enqueue(
    workspace: Path,
    *,
    limit: int,
    prefer_cold: bool,
    include_hot: bool,
    domain: str | None,
    provider: str,
    include_used: bool,
    packs: list[dict[str, str]] | None = None,
    question_type: str = "short_answer",
) -> None:
    emit_event(workspace, step="stage", status="started", detail=f"limit={limit}")
    manifest_path = workspace / "queue" / "pending_packs.json"
    stage_bin = str(workspace / "workflow" / "stage_from_materials.py")

    if packs:
        # User-selected packs: stage one-by-one, then merge into a single manifest.
        merged: list[dict[str, Any]] = []
        errors: list[str] = []
        for item in packs[:limit]:
            domain_key = str(item.get("domain_key") or "").strip()
            pack_name = str(item.get("pack") or "").strip()
            if not domain_key or not pack_name:
                errors.append(f"invalid pack selector: {item}")
                continue
            raise_if_paused(workspace)
            # Explicit selection: no coldness filters; --include-used only when requested.
            stage_cmd = [
                "python3",
                stage_bin,
                "--domain",
                domain_key,
                "--pack",
                pack_name,
                "--limit",
                "1",
                "--force",
                "--write-manifest",
                str(manifest_path),
            ]
            if include_used:
                stage_cmd.insert(-2, "--include-used")
            proc = popen_run(stage_cmd, workspace, timeout=3600)
            added = 0
            if manifest_path.is_file() and not proc.returncode:
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    for row in data.get("packs") or []:
                        if isinstance(row, dict) and row.get("slug"):
                            if not any(m.get("slug") == row["slug"] for m in merged):
                                merged.append(row)
                                added += 1
                except (OSError, json.JSONDecodeError) as exc:
                    errors.append(f"{domain_key}/{pack_name} manifest: {exc}")
                    emit_event(
                        workspace,
                        step="stage",
                        status="error",
                        detail=f"{domain_key}/{pack_name} manifest: {exc}",
                    )
                    continue
            if proc.returncode or added == 0:
                detail = _selected_stage_error(domain_key, pack_name, proc)
                errors.append(detail)
                emit_event(workspace, step="stage", status="error", detail=detail)
                continue
        if not merged:
            detail = "; ".join(errors) if errors else "no packs staged"
            raise RuntimeError(f"selected materials staging failed: {detail}")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "packs": merged,
                    "selected": True,
                    "include_used": include_used,
                    "count": len(merged),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        emit_event(
            workspace,
            step="stage",
            status="finished",
            detail=f"selected={len(merged)}" + (f" errors={len(errors)}" if errors else ""),
        )
        effective_limit = min(limit, len(merged))
    else:
        stage_cmd = [
            "python3",
            stage_bin,
            "--limit",
            str(limit),
            "--write-manifest",
            str(manifest_path),
        ]
        if prefer_cold:
            stage_cmd.append("--prefer-cold")
        if include_hot:
            stage_cmd.append("--include-hot")
        if domain:
            stage_cmd += ["--domain", domain]
        if include_used:
            stage_cmd.append("--include-used")
        proc = popen_run(stage_cmd, workspace, timeout=3600)
        if proc.returncode:
            raise RuntimeError(f"stage failed:\n{(proc.stderr or '')[-2000:]}\n{(proc.stdout or '')[-2000:]}")
        emit_event(workspace, step="stage", status="finished")
        effective_limit = limit

    raise_if_paused(workspace)
    emit_event(workspace, step="enqueue", status="started")
    if manifest_path.is_file():
        enq = [
            "python3",
            str(workspace / "workflow" / "queue_worker.py"),
            "enqueue-bulk",
            "--manifest",
            str(manifest_path),
            "--limit",
            str(effective_limit),
            "--provider-mode",
            provider,
        ]
    else:
        enq = [
            "python3",
            str(workspace / "workflow" / "queue_worker.py"),
            "enqueue-staging-scan",
            "--limit",
            str(effective_limit),
            "--provider-mode",
            provider,
        ]
    if include_used:
        enq.append("--include-used")
    qtype = str(question_type or "short_answer").strip().lower() or "short_answer"
    if qtype not in {"short_answer", "multiple_choice", "auto"}:
        qtype = "short_answer"
    enq += ["--question-type", qtype]
    proc = popen_run(enq, workspace, timeout=600)
    if proc.returncode:
        raise RuntimeError(f"enqueue failed:\n{(proc.stderr or '')[-2000:]}\n{(proc.stdout or '')[-2000:]}")
    emit_event(workspace, step="enqueue", status="finished")


def produce_batch(
    workspace: Path,
    *,
    limit: int,
    workers: int,
    provider: str = "live",
    fixture_root: Path | None = None,
    gen_retries: int = 3,
    source_type: str = "public documentation",
    retry_technical: bool = False,
    task_ids: list[str] | None = None,
) -> dict[str, Any]:
    system = workspace / "scripts" / "prompts" / "generate_qa.txt"
    fixture = fixture_root.resolve() if fixture_root else None
    if current_status(workspace) != "running":
        mark_running(
            workspace,
            limit=limit,
            workers=workers,
            run_id=time.strftime("%Y%m%d-%H%M%S"),
            provider=provider,
            skip_stage=True,
        )
    claimed_lock = threading.Lock()
    claimed_count = 0
    results: list[dict[str, Any]] = []
    results_lock = threading.Lock()
    pinned = [str(tid).strip() for tid in (task_ids or []) if str(tid).strip()] if task_ids is not None else None
    pinned_lock = threading.Lock()

    def next_pinned_id() -> str | None:
        if pinned is None:
            return None
        with pinned_lock:
            if not pinned:
                return None
            return pinned.pop(0)

    def worker(index: int) -> None:
        nonlocal claimed_count
        while True:
            if should_pause(workspace):
                return
            with claimed_lock:
                if claimed_count >= limit:
                    return
                claimed_count += 1
            specific_id = None
            if pinned is not None:
                specific_id = next_pinned_id()
                if specific_id is None:
                    with claimed_lock:
                        claimed_count -= 1
                    return
            task = claim_one(workspace, f"desktop-{os.getpid()}-{index}", task_id=specific_id)
            if not task:
                with claimed_lock:
                    claimed_count -= 1
                if pinned is None:
                    return
                continue
            if should_pause(workspace):
                release_task(workspace, task["id"], "paused before start")
                with claimed_lock:
                    claimed_count -= 1
                return
            bump_claimed(workspace)
            row = run_one_controlled(
                workspace,
                task,
                system=system,
                gen_retries=gen_retries,
                provider=provider,
                fixture_root=fixture,
                source_type=source_type,
                retry_technical=retry_technical,
            )
            with results_lock:
                results.append(row)

    n_workers = max(1, min(workers, limit))
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = [pool.submit(worker, i) for i in range(n_workers)]
        for future in as_completed(futures):
            future.result()
    summary = {
        "finished_at": utcnow(),
        "provider": provider,
        "workers": n_workers,
        "claimed": claimed_count,
        "paused": should_pause(workspace),
        "passed_tasks": sum(row.get("status") == "passed" for row in results),
        "results": results,
    }
    out = workspace / "queue" / "batch_reports" / f"batch-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def run_pipeline(
    workspace: Path,
    *,
    limit: int = 100,
    workers: int = 1,
    prefer_cold: bool = True,
    include_hot: bool = False,
    domain: str | None = None,
    provider: str = "live",
    fixture_root: Path | None = None,
    skip_stage: bool = False,
    retry_technical: bool = False,
    include_used: bool = False,
    gen_retries: int = 3,
    packs: list[dict[str, str]] | None = None,
    question_type: str = "short_answer",
    task_ids: list[str] | None = None,
) -> dict[str, Any]:
    if packs:
        packs = [
            {"domain_key": str(p.get("domain_key") or "").strip(), "pack": str(p.get("pack") or "").strip()}
            for p in packs
            if isinstance(p, dict) and str(p.get("domain_key") or "").strip() and str(p.get("pack") or "").strip()
        ]
        if not packs:
            raise ValueError("已选择自选材料模式，但未提供有效 pack")
        limit = min(limit, len(packs))
    pinned = [str(tid).strip() for tid in (task_ids or []) if str(tid).strip()]
    if pinned:
        skip_stage = True
        limit = len(pinned)
    limit, workers = validate_batch_bounds(limit, workers)
    workspace = workspace.resolve()
    run_id = time.strftime("%Y%m%d-%H%M%S")
    clear_events(workspace)
    _GEN_CALL_COUNTS.clear()
    mark_running(
        workspace,
        limit=limit,
        workers=workers,
        run_id=run_id,
        prefer_cold=prefer_cold,
        provider=provider,
        skip_stage=skip_stage,
    )
    if provider == "live":
        load_keys()
    try:
        with BatchBind(workspace):
            if not skip_stage:
                _stage_and_enqueue(
                    workspace,
                    limit=limit,
                    prefer_cold=prefer_cold and not include_hot,
                    include_hot=include_hot,
                    domain=domain,
                    provider=provider,
                    include_used=include_used,
                    packs=packs,
                    question_type=question_type,
                )
                raise_if_paused(workspace)
            if pinned:
                claim_limit = len(pinned)
            else:
                queued = queued_task_count(workspace)
                claim_limit = claim_limit_for_run(limit, queued)
            if claim_limit != limit:
                update_state(workspace, limit=claim_limit)
            summary = produce_batch(
                workspace,
                limit=claim_limit,
                workers=workers,
                provider=provider,
                fixture_root=fixture_root,
                gen_retries=gen_retries,
                retry_technical=retry_technical,
                task_ids=pinned or None,
            )
        status = current_status_message(workspace, summary)
        mark_idle(workspace, status)
        return {"ok": True, "summary": summary, "run_id": run_id}
    except (StopRequested, Interrupted) as exc:
        mark_idle(workspace, str(exc))
        return {"ok": True, "paused": True, "run_id": run_id, "reason": str(exc)}
    except Exception as exc:
        mark_idle(workspace, str(exc))
        return {"ok": False, "error": str(exc), "traceback": traceback.format_exc()[-3000:], "run_id": run_id}
    finally:
        try:
            release_active_tasks(workspace, reason="desktop run end sweep")
        except Exception:
            pass


def current_status_message(workspace: Path, summary: dict[str, Any]) -> str:
    if summary.get("paused"):
        return "paused"
    return f"claimed={summary.get('claimed')} passed={summary.get('passed_tasks')}"


def queued_task_count(workspace: Path) -> int:
    queue = read_queue(workspace)
    return sum(
        1
        for t in (queue.get("tasks") or [])
        if isinstance(t, dict) and str(t.get("status") or "") == "queued"
    )


def claim_limit_for_run(requested: int, queued: int) -> int:
    """Process leftover 排队 in the same run."""
    return max(int(requested), int(queued))


def _remove_holding_delivery(
    workspace: Path,
    task: dict[str, Any],
    cand: dict[str, Any],
    index: int,
) -> None:
    """Drop the 0/8 holding package after promoting to samples/."""
    names: set[str] = set()
    pending = batch_run.final_dir(task, index, "pending_review")
    names.add(pending.name)
    sd = str(cand.get("sample_dir") or "")
    if sd:
        names.add(Path(sd).name)
    pending_root = workspace / "archive" / "pending-review"
    failed_root = workspace / "archive" / "failed-samples"
    for name in names:
        hold = pending_root / name
        if hold.is_dir():
            shutil.rmtree(hold)
        # Legacy: 0/8 used to be written into failed-samples.
        if "failed-samples" in sd.replace("\\", "/"):
            leftover = failed_root / Path(sd).name
            if leftover.is_dir():
                shutil.rmtree(leftover)


def run_review_pass(
    workspace: Path,
    task_id: str,
    *,
    provider: str = "live",
    fixture_root: Path | None = None,
    source_type: str = "public technical/government documentation",
    require_zero: bool = True,
    zero_rechecked: bool = True,
) -> dict[str, Any]:
    """Promote a pending-review candidate.

    Default is human/auto-confirmed 0/8 (package --zero-rechecked-pass).
    After false-negative rescore, call with require_zero=False and
    zero_rechecked=False so the patched avg_accuracy is packaged as a normal pass.
    """
    workspace = workspace.resolve()
    queue = read_queue(workspace)
    task = next((t for t in (queue.get("tasks") or []) if isinstance(t, dict) and t.get("id") == task_id), None)
    if not task:
        raise KeyError(f"task not found: {task_id}")
    if not can_review_pass(task):
        raise PermissionError("task is not eligible for review-pass (need blocked + manual_review 0/8)")
    cand = find_manual_review_candidate(task)
    if not cand:
        raise ValueError("no manual_review candidate on task")

    candidate_rel = str(cand.get("candidate_dir") or "")
    if not candidate_rel:
        raise ValueError("candidate_dir missing on manual_review row")
    candidate = (workspace / candidate_rel).resolve()
    if not candidate.is_dir():
        raise FileNotFoundError(f"candidate workspace missing: {candidate_rel}")
    if not batch_run.can_package_failed(candidate):
        raise ValueError("candidate raw incomplete (need qa + 8 rollouts + judge)")

    judge = {}
    judge_path = candidate / "work" / "raw" / "judge_summary.json"
    if judge_path.is_file():
        try:
            judge = json.loads(judge_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            judge = {}
    try:
        avg = float(judge.get("avg_accuracy") if judge.get("avg_accuracy") is not None else cand.get("avg_accuracy"))
    except (TypeError, ValueError) as exc:
        raise ValueError("avg_accuracy unavailable") from exc
    if require_zero and avg != 0.0:
        raise ValueError(f"review-pass requires avg_accuracy == 0.0 (got {avg})")
    if not require_zero:
        from workflow.qa_checks import gate_decision

        mapped = gate_decision(avg)
        if mapped != "ablation":
            raise ValueError(f"rescored pass requires avg_accuracy in (0, 0.5], got {avg} ({mapped})")

    index = int(cand.get("candidate_index") or int(str(cand.get("candidate_id") or "candidate-01").rsplit("-", 1)[-1]))
    attempt = int(cand.get("attempt") or 1)
    candidate_id = str(cand.get("candidate_id") or f"candidate-{index:02d}")
    run_id = time.strftime("%Y%m%d-%H%M%S")
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
    if provider == "live":
        load_keys()
    try:
        with BatchBind(workspace):
            produce_step(
                workspace,
                "ablation",
                candidate,
                task,
                candidate_id=candidate_id,
                candidate_index=index,
                attempt=attempt,
                provider=provider,
                fixture_root=fixture_root,
                timeout=5400,
            )
            final = batch_run.final_dir(task, index, "passed")
            package_extra = ["--delivery-dir", str(final)]
            if zero_rechecked:
                package_extra.append("--zero-rechecked-pass")
            package_extra.extend(["--source-type", source_type])
            produce_step(
                workspace,
                "package",
                candidate,
                task,
                candidate_id=candidate_id,
                candidate_index=index,
                attempt=attempt,
                provider=provider,
                fixture_root=fixture_root,
                extra=package_extra,
                timeout=900,
            )
            archive_label = "zero-rechecked-pass" if zero_rechecked else "passed"
            reported_avg = 0.0 if zero_rechecked else float(avg)
            archive = batch_run.archive_run(candidate, task, candidate_id, attempt, archive_label)
            _remove_holding_delivery(workspace, task, cand, index)
            row = batch_run.candidate_result(
                task,
                candidate_id,
                index,
                candidate,
                status="passed",
                attempt=attempt,
                provider=provider,
                avg=reported_avg,
                sample=str(final.relative_to(workspace)),
                review="passed",
                archive=str(archive),
            )
            row["failure_reason"] = None
            row["zero_rechecked"] = zero_rechecked
            prior = batch_run.candidate_state(candidate)
            attempts = prior.get("attempts") if isinstance(prior.get("attempts"), list) else []
            batch_run.save_state(candidate, {**row, "attempts": attempts})
            summary = {
                "task_id": task["id"],
                "slug": task.get("slug"),
                "status": "passed",
                "candidates": [row],
                "provider": provider,
                "review_status": "passed",
                "zero_rechecked": zero_rechecked,
            }
            summary_path = batch_run.resolved(task["sample_dir"]) / "work" / "candidate_results.json"
            batch_run.write_json(summary_path, summary)
            emit_event(
                workspace,
                step="complete",
                status="finished",
                task_id=task["id"],
                slug=task.get("slug"),
                detail="zero_rechecked_pass" if zero_rechecked else "rescored_pass",
            )
            completed = qw_json(
                workspace,
                [
                    "complete",
                    "--task-id",
                    task["id"],
                    "--result",
                    "passed",
                    "--sample-dir",
                    task["sample_dir"],
                    "--avg-accuracy",
                    str(reported_avg),
                    "--candidate-results",
                    str(summary_path),
                    "--by",
                    "desktop-review-pass" if zero_rechecked else "desktop-auto-review-rescore",
                ],
            )
        mark_idle(workspace, f"review-pass ok → {final.name}")
        return {
            "ok": True,
            "task_id": task_id,
            "delivery_dir": str(final.relative_to(workspace)),
            "archive": str(archive),
            "task": completed,
            "run_id": run_id,
        }
    except Exception as exc:
        mark_idle(workspace, f"review-pass failed: {exc}")
        raise
    finally:
        try:
            release_active_tasks(workspace, reason="desktop review-pass end sweep")
        except Exception:
            pass


def read_queue(workspace: Path) -> dict[str, Any]:
    path = workspace / "queue" / "queue.json"
    if not path.is_file():
        return {"version": 2, "tasks": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 2, "tasks": []}
