"""Read-only scan of materials/**/CATALOG.json for the GUI tree."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _doc_issues(doc: dict[str, Any], pack_dir: Path, root: Path) -> list[str]:
    issues = []
    file_md = doc.get("file_md") or doc.get("markdown")
    file_pdf = doc.get("file_pdf")
    file_html = doc.get("file_html") or doc.get("file_htm") or doc.get("file_raw") or doc.get("raw")
    if not file_md:
        issues.append("缺 file_md")
    else:
        md = Path(file_md)
        md_path = md if md.is_absolute() else root / md
        if not md_path.is_file():
            alt = pack_dir / "md" / Path(file_md).name
            if not alt.is_file():
                # also allow pack-relative paths like md/foo.md
                alt2 = pack_dir / file_md
                if not alt2.is_file():
                    issues.append("md 文件不存在")
    if not file_pdf and not file_html:
        issues.append("缺 file_pdf/file_html")
    else:
        for rel in (file_pdf, file_html):
            if not rel:
                continue
            path = Path(rel)
            full = path if path.is_absolute() else root / path
            if full.is_file():
                continue
            alt = pack_dir / Path(rel).name
            alt2 = pack_dir / rel
            if not alt.is_file() and not alt2.is_file():
                issues.append(f"源文件不存在: {rel}")
    return issues


def scan_materials(root: Path) -> dict[str, Any]:
    materials = root / "materials"
    domains: list[dict[str, Any]] = []
    if not materials.is_dir():
        return {"root": str(root), "readme": None, "domains": [], "pack_count": 0}
    readme = materials / "README.md"
    for domain_dir in sorted(p for p in materials.iterdir() if p.is_dir()):
        domain_cat = _load(domain_dir / "CATALOG.json")
        packs: list[dict[str, Any]] = []
        domain_key = str(domain_cat.get("domain_key") or domain_dir.name)
        listed = domain_cat.get("packs") if isinstance(domain_cat.get("packs"), list) else []
        seen = set()
        for item in listed:
            if not isinstance(item, dict):
                continue
            name = str(item.get("pack") or "")
            rel = item.get("path") or f"materials/{domain_dir.name}/{name}"
            pack_dir = root / rel if not Path(rel).is_absolute() else Path(rel)
            seen.add(pack_dir.resolve())
            packs.append(_pack_entry(root, domain_key, name, pack_dir, item))
        for pack_dir in sorted(p for p in domain_dir.iterdir() if p.is_dir()):
            if pack_dir.resolve() in seen:
                continue
            if not (pack_dir / "CATALOG.json").is_file() and not any(
                (pack_dir / sub).is_dir() for sub in ("pdf", "html", "md")
            ):
                continue
            packs.append(_pack_entry(root, domain_key, pack_dir.name, pack_dir, {}))
        domains.append(
            {
                "domain": domain_cat.get("domain") or domain_dir.name,
                "domain_key": domain_key,
                "path": str(domain_dir.relative_to(root)),
                "status": domain_cat.get("status"),
                "packs": packs,
            }
        )
    return {
        "root": str(root),
        "readme": readme.read_text(encoding="utf-8") if readme.is_file() else None,
        "domains": domains,
        "pack_count": sum(len(d["packs"]) for d in domains),
    }


def _pack_entry(root: Path, domain_key: str, name: str, pack_dir: Path, listed: dict[str, Any]) -> dict[str, Any]:
    pack_cat = _load(pack_dir / "CATALOG.json") if pack_dir.is_dir() else {}
    docs = pack_cat.get("docs") if isinstance(pack_cat.get("docs"), list) else []
    doc_rows = []
    issues: list[str] = []
    if not (pack_dir / "CATALOG.json").is_file():
        issues.append("缺 CATALOG.json")
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        doc_issues = _doc_issues(doc, pack_dir, root)
        doc_rows.append(
            {
                "doc_id": doc.get("doc_id"),
                "title": doc.get("title"),
                "file_md": doc.get("file_md"),
                "file_pdf": doc.get("file_pdf"),
                "file_html": doc.get("file_html"),
                "issues": doc_issues,
            }
        )
        issues.extend(doc_issues)
    doc_count = len(doc_rows)
    if doc_count <= 0:
        doc_kind = "unknown"
    elif doc_count == 1:
        doc_kind = "single"
    else:
        doc_kind = "multi"
    has_md = (pack_dir / "md").is_dir() and any((pack_dir / "md").iterdir()) if pack_dir.is_dir() else False
    if not docs and not has_md:
        issues.append("尚未放入文档")
    status = pack_cat.get("status") or listed.get("status") or "READY"
    audit = pack_cat.get("llm_audit") if isinstance(pack_cat.get("llm_audit"), dict) else None
    hint = "READY"
    if issues:
        hint = "；".join(dict.fromkeys(issues))
    elif str(status).startswith("USED_"):
        hint = "已用于合格样例"
    elif str(status).startswith("GATE_FAILED"):
        hint = "已用于门禁失败样例"
    elif str(status).upper() == "IN_PROGRESS":
        hint = "生产中"
    return {
        "pack": pack_cat.get("pack") or listed.get("pack") or name,
        "domain_key": domain_key,
        "path": str(pack_dir.relative_to(root)) if pack_dir.is_relative_to(root) else str(pack_dir),
        "status": status,
        "coldness": pack_cat.get("coldness") or listed.get("coldness"),
        "theme": pack_cat.get("theme") or listed.get("theme"),
        "docs": doc_rows,
        "doc_count": doc_count,
        "doc_kind": doc_kind,
        "issues": list(dict.fromkeys(issues)),
        "hint": hint,
        "llm_audit": audit,
        "ready": not issues and str(status).upper() in {"", "READY", "OK", "SELECTED"},
        "slug": f"{domain_key}-{pack_cat.get('pack') or listed.get('pack') or name}",
    }
