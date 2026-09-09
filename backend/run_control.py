"""Run state, JSONL progress, and interruptible child PIDs — lives only under desktop/."""
from __future__ import annotations

import fcntl
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_WORKERS = 10
MIN_LIMIT = 1
MIN_WORKERS = 1
RUN_STATUSES = frozenset({"idle", "running", "stopping", "interrupted"})


class StopRequested(BaseException):
    """Soft stop: finish the current produce_one step, then pause."""


class Interrupted(BaseException):
    """Hard interrupt: current produce_one process was killed."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def queue_dir(root: Path) -> Path:
    return root / "queue"


def state_path(root: Path) -> Path:
    return queue_dir(root) / "run_state.json"


def events_path(root: Path) -> Path:
    return queue_dir(root) / "run_events.jsonl"


def progress_path(root: Path) -> Path:
    return queue_dir(root) / "progress.json"


def lock_path(root: Path) -> Path:
    return queue_dir(root) / "locks" / "run_control.lock"


class ControlLock:
    def __init__(self, root: Path):
        self.path = lock_path(root)
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self._fh:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


def validate_batch_bounds(limit: int, workers: int) -> tuple[int, int]:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < MIN_LIMIT:
        raise ValueError(f"limit must be >= {MIN_LIMIT}, got {limit!r}")
    if not isinstance(workers, int) or isinstance(workers, bool) or not (MIN_WORKERS <= workers <= MAX_WORKERS):
        raise ValueError(f"workers must be {MIN_WORKERS}-{MAX_WORKERS}, got {workers!r}")
    return limit, workers


def default_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "run_id": None,
        "limit": None,
        "workers": None,
        "prefer_cold": True,
        "provider": "live",
        "started_at": None,
        "updated_at": utcnow(),
        "runner_pid": None,
        "child_pids": [],
        "claimed": 0,
        "message": None,
        "skip_stage": False,
    }


def read_state(root: Path) -> dict[str, Any]:
    path = state_path(root)
    if not path.exists():
        return default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_state()
    if not isinstance(data, dict):
        return default_state()
    merged = default_state()
    merged.update(data)
    return merged


def write_state(state: dict[str, Any], root: Path) -> dict[str, Any]:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["updated_at"] = utcnow()
    tmp = path.with_name(".run_state.json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return state


def update_state(root: Path, **fields: Any) -> dict[str, Any]:
    with ControlLock(root):
        state = read_state(root)
        state.update(fields)
        return write_state(state, root)


def mark_running(
    root: Path,
    *,
    limit: int,
    workers: int,
    run_id: str,
    prefer_cold: bool = True,
    provider: str = "live",
    skip_stage: bool = False,
) -> dict[str, Any]:
    return update_state(
        root,
        status="running",
        run_id=run_id,
        limit=limit,
        workers=workers,
        prefer_cold=prefer_cold,
        provider=provider,
        started_at=utcnow(),
        runner_pid=os.getpid(),
        child_pids=[],
        claimed=0,
        message=None,
        skip_stage=skip_stage,
    )


def mark_idle(root: Path, message: str | None = None) -> dict[str, Any]:
    return update_state(root, status="idle", runner_pid=None, child_pids=[], message=message)


def request_soft_stop(root: Path) -> dict[str, Any]:
    with ControlLock(root):
        state = read_state(root)
        if state.get("status") == "running":
            state["status"] = "stopping"
            state["message"] = "soft stop requested"
            write_state(state, root)
        return state


def request_interrupt(root: Path) -> dict[str, Any]:
    with ControlLock(root):
        state = read_state(root)
        if state.get("status") in {"running", "stopping"}:
            state["status"] = "interrupted"
            state["message"] = "hard interrupt requested"
            write_state(state, root)
        pids = list(state.get("child_pids") or [])
    for pid in pids:
        kill_pid(pid)
    return read_state(root)


def current_status(root: Path) -> str:
    status = str(read_state(root).get("status") or "idle")
    return status if status in RUN_STATUSES else "idle"


def should_interrupt(root: Path) -> bool:
    return current_status(root) == "interrupted"


def should_pause(root: Path) -> bool:
    return current_status(root) in {"stopping", "interrupted"}


def raise_if_paused(root: Path) -> None:
    status = current_status(root)
    if status == "interrupted":
        raise Interrupted("run interrupted")
    if status == "stopping":
        raise StopRequested("run soft-stopped")


def register_child(pid: int, root: Path) -> None:
    with ControlLock(root):
        state = read_state(root)
        pids = [p for p in (state.get("child_pids") or []) if isinstance(p, int)]
        if pid not in pids:
            pids.append(pid)
        state["child_pids"] = pids
        write_state(state, root)


def unregister_child(pid: int, root: Path) -> None:
    with ControlLock(root):
        state = read_state(root)
        state["child_pids"] = [p for p in (state.get("child_pids") or []) if p != pid]
        write_state(state, root)


def bump_claimed(root: Path) -> int:
    with ControlLock(root):
        state = read_state(root)
        claimed = int(state.get("claimed") or 0) + 1
        state["claimed"] = claimed
        write_state(state, root)
        return claimed


def kill_pid(pid: int) -> None:
    if not pid:
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            return


def emit_event(
    root: Path,
    *,
    step: str,
    status: str,
    task_id: str | None = None,
    slug: str | None = None,
    detail: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "at": utcnow(),
        "step": step,
        "status": status,
        "task_id": task_id or os.environ.get("LCQA_TASK_ID") or None,
        "slug": slug or os.environ.get("LCQA_TASK_SLUG") or None,
        "detail": detail,
    }
    if extra:
        # Keep progress-only keys out of the JSONL noise if desired; still merge for progress row.
        event.update({k: v for k, v in extra.items() if k not in {"_progress_only"}})
    path = events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False)
    with ControlLock(root):
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        progress = {"updated_at": event["at"], "tasks": {}}
        ppath = progress_path(root)
        if ppath.exists():
            try:
                loaded = json.loads(ppath.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("tasks"), dict):
                    progress["tasks"] = loaded["tasks"]
            except (OSError, json.JSONDecodeError):
                pass
        key = event["task_id"] or event["slug"] or "_pipeline"
        prev = progress["tasks"].get(str(key)) if isinstance(progress["tasks"].get(str(key)), dict) else {}
        done_steps = list(prev.get("done_steps") or [])
        if status == "finished" and step and step not in done_steps:
            # Keep pipeline order; allow repeats only as single entry.
            done_steps.append(step)
        attempt = (extra or {}).get("attempt", prev.get("attempt"))
        candidate_id = (extra or {}).get("candidate_id", prev.get("candidate_id"))
        row = {
            "task_id": event["task_id"],
            "slug": event["slug"],
            "step": step,
            "status": status,
            "detail": detail,
            "at": event["at"],
            "done_steps": done_steps,
        }
        if attempt is not None:
            row["attempt"] = attempt
        if candidate_id is not None:
            row["candidate_id"] = candidate_id
        progress["tasks"][str(key)] = row
        progress["latest"] = event
        tmp = ppath.with_name(".progress.json.tmp")
        tmp.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(ppath)
    return event


def recent_events(root: Path, limit: int = 200) -> list[dict[str, Any]]:
    path = events_path(root)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines()[-max(1, limit) :]:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def read_progress(root: Path) -> dict[str, Any]:
    path = progress_path(root)
    if not path.exists():
        return {"updated_at": None, "tasks": {}, "latest": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"updated_at": None, "tasks": {}, "latest": None}
    return data if isinstance(data, dict) else {"updated_at": None, "tasks": {}, "latest": None}


def clear_events(root: Path) -> None:
    for path in (events_path(root), progress_path(root)):
        if path.exists():
            path.unlink()
