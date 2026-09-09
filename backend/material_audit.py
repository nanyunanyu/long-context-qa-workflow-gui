"""LLM material-pack audit; writes CATALOG.json llm_audit only."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .llm_client import chat_json, load_prompt
from .materials import scan_materials
from .run_control import utcnow
from .settings import get_settings_public

MAX_DOC_CHARS = 48_000
AUDIT_STATUSES = frozenset({"pass", "warn", "fail"})


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_md(root: Path, pack_dir: Path, rel: str | None) -> Path | None:
    if not rel:
        return None
    path = Path(rel)
    candidates = [
        path if path.is_absolute() else root / path,
        pack_dir / path,
        pack_dir / "md" / path.name,
    ]
    for item in candidates:
        if item.is_file():
            return item
    return None


def _clip(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    head = max(budget // 2, 1)
    tail = max(budget - head - 80, 0)
    return text[:head] + "\n\n[... truncated ...]\n\n" + (text[-tail:] if tail else "")


def find_pack_dir(workspace: Path, domain_key: str, pack: str) -> Path:
    materials = scan_materials(workspace)
    for domain in materials.get("domains") or []:
        if str(domain.get("domain_key") or "") != domain_key:
            continue
        for row in domain.get("packs") or []:
            if str(row.get("pack") or "") == pack:
                rel = row.get("path") or f"materials/{domain_key}/{pack}"
                path = Path(rel)
                return path if path.is_absolute() else workspace / path
    guess = workspace / "materials" / domain_key / pack
    if guess.is_dir():
        return guess
    raise FileNotFoundError(f"pack not found: {domain_key}/{pack}")


def build_audit_user_message(workspace: Path, pack_dir: Path, catalog: dict[str, Any]) -> str:
    docs = catalog.get("docs") if isinstance(catalog.get("docs"), list) else []
    n = max(len(docs), 1)
    per = max(MAX_DOC_CHARS // n, 2000)
    excerpts: list[dict[str, Any]] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        md_path = _resolve_md(workspace, pack_dir, doc.get("file_md") or doc.get("markdown"))
        body = md_path.read_text(encoding="utf-8") if md_path else ""
        excerpts.append(
            {
                "doc_id": doc.get("doc_id"),
                "title": doc.get("title"),
                "url": doc.get("url"),
                "license_note": doc.get("license_note"),
                "approx_tokens": doc.get("approx_tokens"),
                "full_approx_tokens": doc.get("full_approx_tokens"),
                "file_md": doc.get("file_md"),
                "excerpt": _clip(body, per),
            }
        )
    meta = {
        "pack": catalog.get("pack") or pack_dir.name,
        "domain": catalog.get("domain"),
        "domain_key": catalog.get("domain_key"),
        "theme": catalog.get("theme"),
        "coldness": catalog.get("coldness"),
        "note": catalog.get("note"),
        "status": catalog.get("status"),
        "total_approx_tokens": catalog.get("total_approx_tokens"),
        "full_approx_tokens": catalog.get("full_approx_tokens"),
        "enough_for_16k": catalog.get("enough_for_16k"),
    }
    return (
        "PACK META (JSON):\n"
        f"{json.dumps(meta, ensure_ascii=False, indent=2)}\n\n"
        "DOC EXCERPTS (JSON):\n"
        f"{json.dumps(excerpts, ensure_ascii=False, indent=2)}\n"
    )


def _normalize_audit(raw: dict[str, Any], model: str) -> dict[str, Any]:
    status = str(raw.get("status") or "").strip().lower()
    if status not in AUDIT_STATUSES:
        raise ValueError(f"invalid audit status: {raw.get('status')!r}")
    checks_in = raw.get("checks") if isinstance(raw.get("checks"), dict) else {}
    checks = {
        "license_ok": bool(checks_in.get("license_ok")),
        "enough_length": bool(checks_in.get("enough_length")),
        "long_context_potential": bool(checks_in.get("long_context_potential")),
        "cold_enough": bool(checks_in.get("cold_enough")),
    }
    return {
        "status": status,
        "model": model,
        "reviewed_at": utcnow(),
        "summary": str(raw.get("summary") or "").strip() or status,
        "checks": checks,
        "notes": str(raw.get("notes") or "").strip(),
    }


def write_llm_audit(catalog_path: Path, audit: dict[str, Any]) -> dict[str, Any]:
    data = _load_json(catalog_path)
    if not data:
        data = {}
    data["llm_audit"] = audit
    catalog_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def audit_pack(workspace: Path, domain_key: str, pack: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    pack_dir = find_pack_dir(workspace, domain_key, pack)
    catalog_path = pack_dir / "CATALOG.json"
    if not catalog_path.is_file():
        raise FileNotFoundError(f"CATALOG.json missing: {pack_dir}")
    catalog = _load_json(catalog_path)
    settings = get_settings_public()
    model = ((settings.get("roles") or {}).get("material_audit") or {}).get("model") or ""
    raw = chat_json(
        "material_audit",
        system=load_prompt("material_audit.txt"),
        user=build_audit_user_message(workspace, pack_dir, catalog),
        timeout=300,
        max_tokens=2048,
    )
    audit = _normalize_audit(raw, model)
    write_llm_audit(catalog_path, audit)
    return {
        "ok": True,
        "domain_key": domain_key,
        "pack": pack,
        "path": str(pack_dir.relative_to(workspace) if pack_dir.is_relative_to(workspace) else pack_dir),
        "llm_audit": audit,
        "status": catalog.get("status"),
    }


def audit_packs(workspace: Path, packs: list[dict[str, str]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for item in packs:
        domain_key = str(item.get("domain_key") or "").strip()
        pack = str(item.get("pack") or "").strip()
        if not domain_key or not pack:
            results.append({"ok": False, "error": "domain_key and pack required", "domain_key": domain_key, "pack": pack})
            continue
        try:
            results.append(audit_pack(workspace, domain_key, pack))
        except Exception as exc:  # noqa: BLE001 — collect per-pack errors
            results.append({"ok": False, "domain_key": domain_key, "pack": pack, "error": str(exc)})
    return {
        "ok": all(bool(r.get("ok")) for r in results) if results else False,
        "results": results,
        "count": sum(1 for r in results if r.get("ok")),
    }
