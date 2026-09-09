"""Detect and initialize an LCQA workspace without touching the original workflow/ tree.

New empty folders get a data scaffold plus a *copy* of workflow/ and scripts/
so the unmodified pipeline can resolve ROOT = parents[1] inside that folder.
Existing LCQA repos (this one included) only get a .lcqa marker.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import code_root

MARKER = Path(".lcqa") / "workspace.json"

MATERIALS_README = """# 材料存放约定

请按领域 → 主题包放置文档，软件不会代替你写入原文。

```
materials/<domain>/<pack>/
  pdf/          # 原始 PDF（优先）
  html/         # 无 PDF 时的 HTML
  md/           # 与源文件同 stem 的 Markdown（必填，供裁剪）
  CATALOG.json  # 本包索引
```

领域索引：`materials/<domain>/CATALOG.json`，`packs[]` 列出子包。

每个文档在 pack 的 `CATALOG.json` 的 `docs[]` 中应包含：

- `file_md`（必填）
- `file_pdf` 或 `file_html`（源文件）
- `title` / `url` / `doc_id`

`status` 由产线回写（READY / IN_PROGRESS / USED_IN_SAMPLE_NNN / GATE_FAILED_NNN），请勿手改源文件名。

示例包：`materials/example/sample-pack/`。把真实材料放进你自己的 domain/pack 后即可在 GUI 里开始生产。
"""

EXAMPLE_DOMAIN_CATALOG = {
    "domain": "示例",
    "domain_key": "example",
    "layout": "materials/example/<pack>/{pdf,html,md}/",
    "packs": [
        {
            "pack": "sample-pack",
            "path": "materials/example/sample-pack",
            "coldness": "cold",
            "status": "READY",
            "theme": "示例空包：请替换为真实材料后再生产",
        }
    ],
    "note": "删除或忽略本示例，改用自己的 domain/pack。",
}

EXAMPLE_PACK_CATALOG = {
    "pack": "sample-pack",
    "domain": "示例",
    "theme": "脚手架示例，请放入 pdf/ 或 html/ 以及对应 md/",
    "coldness": "cold",
    "status": "READY",
    "format": "markdown",
    "docs": [],
    "enough_for_16k": False,
    "note": "每个文档需要 file_md，以及 file_pdf 或 file_html。",
}

EMPTY_QUEUE = {
    "version": 2,
    "updated_at": None,
    "defaults": {
        "lease_seconds": 14400,
        "question_type": "short_answer",
        "min_context_tokens": 16000,
        "sample_root": "work/samples",
    },
    "tasks": [],
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def marker_path(root: Path) -> Path:
    return root / MARKER


def inspect_workspace(root: Path | str) -> dict[str, Any]:
    path = Path(root).expanduser().resolve()
    marker = marker_path(path)
    has_materials = (path / "materials").is_dir()
    has_produce = (path / "scripts" / "produce_one.py").is_file()
    has_queue = (path / "queue" / "queue.json").is_file()
    existing = has_materials and (has_produce or has_queue)
    if marker.is_file():
        try:
            meta = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        return {
            "path": str(path),
            "status": "ready",
            "kind": meta.get("kind") or ("existing" if existing else "scaffolded"),
            "needs_init": False,
            "existing_repo": existing,
            "marker": str(marker),
        }
    if existing:
        return {
            "path": str(path),
            "status": "legacy_ready",
            "kind": "existing",
            "needs_init": False,
            "existing_repo": True,
            "marker": str(marker),
        }
    return {
        "path": str(path),
        "status": "needs_init",
        "kind": "new",
        "needs_init": True,
        "existing_repo": False,
        "marker": str(marker),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _copy_pipeline(dest: Path, source: Path) -> None:
    """Copy unmodified workflow/ + required scripts/ into the workspace."""
    wf_src = source / "workflow"
    wf_dst = dest / "workflow"
    wf_dst.mkdir(parents=True, exist_ok=True)
    for item in wf_src.iterdir():
        if item.suffix == ".py":
            shutil.copy2(item, wf_dst / item.name)
    scripts_src = source / "scripts"
    scripts_dst = dest / "scripts"
    scripts_dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(scripts_src / "produce_one.py", scripts_dst / "produce_one.py")
    for name in ("call_gpt_luna.sh", "call_qwen35.sh", "call_gpt_sol_vankit.sh", "lcqa_reasoning.py"):
        src = scripts_src / name
        if src.is_file():
            shutil.copy2(src, scripts_dst / name)
            (scripts_dst / name).chmod(src.stat().st_mode)
    for dirname in ("prompts", "sample_bundle"):
        src_dir = scripts_src / dirname
        if src_dir.is_dir():
            if (scripts_dst / dirname).exists():
                shutil.rmtree(scripts_dst / dirname)
            shutil.copytree(src_dir, scripts_dst / dirname)


def _write_marker(root: Path, *, kind: str, initialized: bool) -> None:
    _write_json(
        marker_path(root),
        {
            "version": 1,
            "kind": kind,
            "initialized": initialized,
            "initialized_at": utcnow(),
            "code_root": str(code_root()),
        },
    )


def _make_data_dirs(root: Path) -> None:
    for rel in (
        "materials/example/sample-pack/pdf",
        "materials/example/sample-pack/html",
        "materials/example/sample-pack/md",
        "data/staging",
        "samples",
        "archive/failed-samples",
        "archive/pending-review",
        "archive/runs",
        "archive/exports",
        "queue/locks",
        "queue/batch_reports",
    ):
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "materials" / "README.md").write_text(MATERIALS_README, encoding="utf-8")
    catalog = dict(EXAMPLE_DOMAIN_CATALOG)
    catalog["created_at"] = utcnow()
    _write_json(root / "materials" / "example" / "CATALOG.json", catalog)
    pack = dict(EXAMPLE_PACK_CATALOG)
    pack["created_at"] = utcnow()
    _write_json(root / "materials" / "example" / "sample-pack" / "CATALOG.json", pack)
    queue = dict(EMPTY_QUEUE)
    queue["updated_at"] = utcnow()
    _write_json(root / "queue" / "queue.json", queue)


def ensure_workspace(root: Path | str) -> dict[str, Any]:
    path = Path(root).expanduser().resolve()
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError(f"not a directory: {path}")
    info = inspect_workspace(path)
    if info["status"] == "ready":
        return {**info, "initialized": False, "copied_pipeline": False}
    if info["status"] == "legacy_ready":
        _write_marker(path, kind="existing", initialized=True)
        return {**inspect_workspace(path), "initialized": True, "copied_pipeline": False, "skipped_overwrite": True}
    _make_data_dirs(path)
    _copy_pipeline(path, code_root())
    _write_marker(path, kind="scaffolded", initialized=True)
    return {**inspect_workspace(path), "initialized": True, "copied_pipeline": True}
