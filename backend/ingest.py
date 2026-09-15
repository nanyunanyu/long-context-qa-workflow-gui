"""Wrap hardcoded collect scripts so the GUI can land packs into a workspace.

Does not run collect_*.py main() (user_guides main() overwrites the domain CATALOG).
Always import collectors from code_root/scripts and bind ROOT/MATERIALS to the
selected workspace.
"""
from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .material_audit import audit_packs, find_pack_dir
from .paths import code_root

LEGAL_PACK = "privacy-data-governance"
LEGAL_DOMAIN = "legal"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _scripts_dir() -> Path:
    return code_root() / "scripts"


def _ensure_scripts_path() -> None:
    scripts = str(_scripts_dir())
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def _load_twelve():
    _ensure_scripts_path()
    import collect_twelve_domain_packs as twelve  # type: ignore

    return twelve


def _load_easy():
    _ensure_scripts_path()
    import collect_easy_domain_packs as easy  # type: ignore

    return easy


def _load_user_guides():
    _ensure_scripts_path()
    import collect_user_guide_materials as user_guides  # type: ignore

    return user_guides


def _load_legal():
    _ensure_scripts_path()
    import collect_legal_pack as legal  # type: ignore

    return legal


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _dump_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def existing_pack_ready(workspace: Path, domain_key: str, pack: str) -> bool:
    """Skip when the pack already has a usable catalog or markdown."""
    pack_dir = workspace / "materials" / domain_key / pack
    catalog_path = pack_dir / "CATALOG.json"
    cat = _load_json(catalog_path)
    if cat.get("enough_for_16k") is True:
        return True
    status = str(cat.get("status") or "").upper()
    docs = cat.get("docs") if isinstance(cat.get("docs"), list) else []
    if docs and status in {"READY", "OK", "SELECTED"}:
        return True
    md_dir = pack_dir / "md"
    if md_dir.is_dir() and any(p.is_file() and p.suffix.lower() == ".md" for p in md_dir.iterdir()):
        return True
    return False


class CollectBind:
    """Point collect script ROOT/MATERIALS/PACK at a workspace, then restore."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self._saved: list[tuple[Any, str, Any]] = []

    def _set(self, module: Any, name: str, value: Any) -> None:
        self._saved.append((module, name, getattr(module, name)))
        setattr(module, name, value)

    def __enter__(self) -> CollectBind:
        twelve = _load_twelve()
        self._set(twelve, "ROOT", self.workspace)
        self._set(twelve, "MATERIALS", self.workspace / "materials")
        easy = _load_easy()
        self._set(easy, "ROOT", self.workspace)
        user_guides = _load_user_guides()
        self._set(user_guides, "ROOT", self.workspace)
        self._set(user_guides, "BASE", self.workspace / "materials" / "user_guides")
        try:
            legal = _load_legal()
        except SystemExit:
            legal = None
        except Exception:
            legal = None
        if legal is not None:
            self._set(legal, "ROOT", self.workspace)
            self._set(legal, "PACK", self.workspace / "materials" / LEGAL_DOMAIN / LEGAL_PACK)
        return self

    def __exit__(self, *exc: object) -> None:
        for module, name, value in reversed(self._saved):
            setattr(module, name, value)


def _job(
    collector: str,
    domain_key: str,
    domain: str,
    pack: str,
    *,
    theme: str | None = None,
    coldness: str | None = None,
    exists: bool = False,
) -> dict[str, Any]:
    return {
        "collector": collector,
        "domain_key": domain_key,
        "domain": domain,
        "pack": pack,
        "theme": theme,
        "coldness": coldness,
        "exists": exists,
        "path": f"materials/{domain_key}/{pack}",
    }


def list_ingest_jobs(workspace: Path | None = None) -> dict[str, Any]:
    """Seed packs from the four collect scripts. First (domain, pack) wins."""
    root = Path(workspace).resolve() if workspace is not None else None
    jobs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(
        collector: str,
        domain_key: str,
        domain: str,
        pack: str,
        theme: str | None = None,
        coldness: str | None = None,
    ) -> None:
        key = (domain_key, pack)
        if not domain_key or not pack or key in seen:
            return
        seen.add(key)
        exists = existing_pack_ready(root, domain_key, pack) if root else False
        jobs.append(
            _job(
                collector,
                domain_key,
                domain,
                pack,
                theme=theme,
                coldness=coldness,
                exists=exists,
            )
        )

    twelve = _load_twelve()
    for spec in twelve.PACKS:
        if not isinstance(spec, dict):
            continue
        add(
            "twelve",
            str(spec.get("domain_key") or ""),
            str(spec.get("domain") or spec.get("domain_key") or ""),
            str(spec.get("pack") or ""),
            theme=spec.get("theme"),
            coldness=spec.get("coldness"),
        )

    easy = _load_easy()
    for spec in easy.PACKS:
        if not isinstance(spec, dict):
            continue
        add(
            "easy",
            str(spec.get("domain_key") or ""),
            str(spec.get("domain") or spec.get("domain_key") or ""),
            str(spec.get("pack") or ""),
            theme=spec.get("theme"),
            coldness=spec.get("coldness"),
        )

    user_guides = _load_user_guides()
    for name, pack in (user_guides.PACKS or {}).items():
        if not isinstance(pack, dict):
            continue
        add(
            "user_guides",
            "user_guides",
            "用户指南",
            str(name),
            theme=pack.get("theme"),
            coldness=pack.get("coldness"),
        )

    add(
        "legal",
        LEGAL_DOMAIN,
        "法律",
        LEGAL_PACK,
        theme="个人信息、数据安全与网络安全规则的跨法适用",
        coldness="cold",
    )

    missing = sum(1 for job in jobs if not job.get("exists"))
    return {
        "jobs": jobs,
        "count": len(jobs),
        "missing": missing,
        "existing": len(jobs) - missing,
    }


def _write_user_guide_catalog(workspace: Path, name: str, catalog: dict[str, Any]) -> dict[str, Any]:
    pack_dir = workspace / "materials" / "user_guides" / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    out = dict(catalog)
    out.setdefault("pack", name)
    out.setdefault("domain", "用户指南")
    out.setdefault("domain_key", "user_guides")
    out.setdefault("format", "markdown")
    if "enough_for_16k" not in out:
        total = int(out.get("total_approx_tokens") or 0)
        out["enough_for_16k"] = total >= 16_000
    if out.get("enough_for_16k") and out.get("docs"):
        out["status"] = "READY"
    _dump_json(pack_dir / "CATALOG.json", out)
    twelve = _load_twelve()
    twelve.upsert_domain_catalog("user_guides", "用户指南", out)
    return out


def _collect_legal(workspace: Path) -> dict[str, Any]:
    legal = _load_legal()
    pack_dir = Path(legal.PACK)
    for subdir in ("pdf", "docx", "md"):
        (pack_dir / subdir).mkdir(parents=True, exist_ok=True)

    docs: list[dict[str, Any]] = []
    for law in legal.LAWS:
        import urllib.parse

        detail = legal.fetch_json(
            legal.NPC_API + "/search/flfgDetails?" + urllib.parse.urlencode({"bbbs": law["bbbs"]})
        )
        if detail.get("code") != 200 or not isinstance(detail.get("data"), dict):
            raise RuntimeError(f"NPC detail request failed for {law['title']}: {detail}")
        data = detail["data"]
        oss = data.get("ossFile") or {}
        if not oss.get("ossPdfPath") or not oss.get("ossWordPath"):
            raise RuntimeError(f"NPC source files missing for {law['title']}")

        links: dict[str, str] = {}
        for fmt in ("pdf", "docx"):
            response = legal.fetch_json(
                legal.NPC_API
                + "/download/pc?"
                + urllib.parse.urlencode({"format": fmt, "bbbs": law["bbbs"], "fileId": ""})
            )
            url = (response.get("data") or {}).get("url")
            if not url:
                raise RuntimeError(f"NPC download URL missing for {law['title']} ({fmt})")
            links[fmt] = url

        pdf_path = pack_dir / "pdf" / f"{law['slug']}.pdf"
        docx_path = pack_dir / "docx" / f"{law['slug']}.docx"
        md_path = pack_dir / "md" / f"{law['slug']}.md"
        if not pdf_path.exists():
            legal.download(links["pdf"], pdf_path)
        if not docx_path.exists():
            legal.download(links["docx"], docx_path)
        if not md_path.exists():
            md_path.write_text(
                legal.docx_to_markdown(docx_path, data.get("title") or law["title"]),
                encoding="utf-8",
            )
        text = md_path.read_text(encoding="utf-8")
        docs.append(
            {
                "doc_id": law["doc_id"],
                "slug": law["slug"],
                "title": data.get("title") or law["title"],
                "url": f"{legal.DETAIL_BASE}?{urllib.parse.urlencode({'id': law['bbbs']})}",
                "organization": data.get("zdjgName") or "全国人民代表大会常务委员会",
                "published_date": data.get("gbrq"),
                "effective_date": data.get("sxrq"),
                "status": data.get("sxx"),
                "file_pdf": str(pdf_path.relative_to(Path(legal.ROOT))),
                "file_md": str(md_path.relative_to(Path(legal.ROOT))),
                "char_count": len(text),
            }
        )

    total_chars = sum(int(d.get("char_count") or 0) for d in docs)
    catalog = {
        "pack": LEGAL_PACK,
        "domain": "法律",
        "domain_key": LEGAL_DOMAIN,
        "theme": "个人信息、数据安全与网络安全规则的跨法适用",
        "coldness": "cold",
        "status": "READY" if docs else "FAILED",
        "format": "markdown",
        "created_at": utcnow(),
        "source_authority": "国家法律法规数据库（全国人大常委会办公厅维护）",
        "source_policy": "公开官方法律文本；使用时应以标准文本为准",
        "docs": docs,
        "total_approx_tokens": total_chars,
        "enough_for_16k": total_chars >= 16_000,
        "cross_doc_question_hooks": [
            "个人信息处理者、数据处理者和网络运营者的义务边界",
            "个人信息跨境提供与数据安全、网络安全规则的叠加适用",
            "2025年网络安全法修正后的时间线和规则适用性判断",
            "重要数据、核心数据与个人信息的分类及保护责任比较",
        ],
    }
    if catalog["enough_for_16k"] and docs:
        catalog["status"] = "READY"
    _dump_json(pack_dir / "CATALOG.json", catalog)
    (pack_dir / "README.md").write_text(
        "# 法律资料包：个人信息、数据安全与网络安全\n\n"
        "来源为国家法律法规数据库，保存官方 PDF 和由官方 DOCX 提取的 Markdown。\n",
        encoding="utf-8",
    )
    twelve = _load_twelve()
    twelve.upsert_domain_catalog(LEGAL_DOMAIN, "法律", catalog)
    return catalog


def _spec_for_job(job: dict[str, Any]) -> dict[str, Any] | None:
    collector = str(job.get("collector") or "")
    pack = str(job.get("pack") or "")
    domain_key = str(job.get("domain_key") or "")
    if collector == "twelve":
        for spec in _load_twelve().PACKS:
            if isinstance(spec, dict) and spec.get("pack") == pack and spec.get("domain_key") == domain_key:
                return spec
    if collector == "easy":
        for spec in _load_easy().PACKS:
            if isinstance(spec, dict) and spec.get("pack") == pack and spec.get("domain_key") == domain_key:
                return spec
    if collector == "user_guides":
        pack_spec = (_load_user_guides().PACKS or {}).get(pack)
        if isinstance(pack_spec, dict):
            return pack_spec
    return None


def collect_one(workspace: Path, job: dict[str, Any]) -> dict[str, Any]:
    """Collect a single seed pack. Caller must hold CollectBind."""
    collector = str(job.get("collector") or "")
    pack = str(job.get("pack") or "")
    domain_key = str(job.get("domain_key") or "")
    if collector == "legal":
        return _collect_legal(workspace)
    spec = _spec_for_job(job)
    if spec is None:
        raise KeyError(f"unknown ingest job: {collector}/{domain_key}/{pack}")
    if collector == "user_guides":
        catalog = _load_user_guides().collect_pack(pack, spec)
        return _write_user_guide_catalog(workspace, pack, catalog)
    return _load_twelve().collect_pack(spec)


def _stopped_row(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "stopped": True,
        "skipped": False,
        "collector": job.get("collector"),
        "domain_key": job.get("domain_key"),
        "pack": job.get("pack"),
        "error": "stopped",
    }


DEFAULT_DISCOVER_LIMIT = 10


def _collect_discovered(workspace: Path, spec: dict[str, Any]) -> dict[str, Any]:
    catalog = _load_twelve().collect_pack(spec)
    if catalog.get("enough_for_16k"):
        return catalog
    pack = str(spec.get("pack") or "")
    domain_key = str(spec.get("domain_key") or "")
    pack_dir = workspace / "materials" / domain_key / pack
    if pack_dir.is_dir():
        shutil.rmtree(pack_dir, ignore_errors=True)
        _remove_domain_pack_entry(workspace, domain_key, pack)
    raise RuntimeError(f"{domain_key}/{pack} too short for 16k after download")


def run_ingest(
    workspace: Path,
    *,
    only: list[dict[str, str]] | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_pack: Callable[[dict[str, Any], int], None] | None = None,
    discover_limit: int | None = DEFAULT_DISCOVER_LIMIT,
    ingest_run_id: str | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace).resolve()
    listed = list_ingest_jobs(workspace)
    jobs = list(listed.get("jobs") or [])
    if only is not None:
        want = {
            (str(item.get("domain_key") or "").strip(), str(item.get("pack") or "").strip())
            for item in only
            if isinstance(item, dict)
        }
        jobs = [job for job in jobs if (str(job.get("domain_key") or ""), str(job.get("pack") or "")) in want]
        missing = want - {(str(j.get("domain_key") or ""), str(j.get("pack") or "")) for j in jobs}
        if missing:
            raise KeyError("unknown ingest jobs: " + ", ".join(f"{d}/{p}" for d, p in sorted(missing)))
        discover_limit = 0

    results: list[dict[str, Any]] = []
    stopped = False
    sites_queried = False
    discover_failed = 0
    last_error: str | None = None
    site_errors: list[str] = []
    with CollectBind(workspace):
        for index, job in enumerate(jobs):
            if should_stop and should_stop():
                stopped = True
                results.extend(_stopped_row(rest) for rest in jobs[index:])
                break
            domain_key = str(job.get("domain_key") or "")
            pack = str(job.get("pack") or "")
            if existing_pack_ready(workspace, domain_key, pack):
                results.append(
                    {
                        "ok": True,
                        "skipped": True,
                        "reason": "existing",
                        "collector": job.get("collector"),
                        "domain_key": domain_key,
                        "pack": pack,
                        "path": job.get("path"),
                    }
                )
                continue
            if on_pack:
                on_pack(job, index)
            try:
                catalog = collect_one(workspace, job)
                results.append(
                    {
                        "ok": True,
                        "skipped": False,
                        "discovered": False,
                        "collector": job.get("collector"),
                        "domain_key": domain_key,
                        "pack": pack,
                        "path": f"materials/{domain_key}/{pack}",
                        "status": catalog.get("status"),
                        "enough_for_16k": catalog.get("enough_for_16k"),
                    }
                )
            except Exception as exc:  # noqa: BLE001 — collect per-pack errors
                results.append(
                    {
                        "ok": False,
                        "skipped": False,
                        "collector": job.get("collector"),
                        "domain_key": domain_key,
                        "pack": pack,
                        "error": str(exc),
                    }
                )

        limit = int(discover_limit or 0)
        if not stopped and only is None and limit > 0:
            from .discover import discover_specs

            already = sum(1 for row in results if row.get("ok") and not row.get("skipped") and not row.get("stopped"))
            need = max(limit - already, 0)
            if need and not (should_stop and should_stop()):
                sites_queried = True
                if on_pack:
                    on_pack(
                        {
                            "collector": "discover",
                            "domain_key": "search",
                            "pack": "preset-sites",
                            "theme": "正在搜寻 Gutenberg / GNU 手册 / RFC",
                        },
                        len(jobs),
                    )
                specs = discover_specs(
                    workspace,
                    want=max(need * 3, 12),
                    should_stop=should_stop,
                    errors=site_errors,
                    on_status=lambda site: on_pack(
                        {
                            "collector": "discover",
                            "domain_key": site,
                            "pack": "searching",
                            "theme": f"正在搜寻 {site}",
                        },
                        len(jobs),
                    )
                    if on_pack
                    else None,
                )
                discovered_ok = 0
                attempts = 0
                max_attempts = max(need * 2, need + 4)
                for spec in specs:
                    if discovered_ok >= need or attempts >= max_attempts:
                        break
                    if should_stop and should_stop():
                        stopped = True
                        break
                    attempts += 1
                    job = {
                        "collector": spec.get("source") or "discover",
                        "domain_key": spec.get("domain_key"),
                        "pack": spec.get("pack"),
                        "theme": spec.get("theme"),
                        "path": f"materials/{spec.get('domain_key')}/{spec.get('pack')}",
                    }
                    if on_pack:
                        on_pack(job, len(jobs) + discovered_ok)
                    try:
                        catalog = _collect_discovered(workspace, spec)
                        results.append(
                            {
                                "ok": True,
                                "skipped": False,
                                "discovered": True,
                                "collector": spec.get("source") or "discover",
                                "domain_key": spec.get("domain_key"),
                                "pack": spec.get("pack"),
                                "path": f"materials/{spec.get('domain_key')}/{spec.get('pack')}",
                                "status": catalog.get("status"),
                                "enough_for_16k": catalog.get("enough_for_16k"),
                            }
                        )
                        discovered_ok += 1
                    except Exception as exc:  # noqa: BLE001 — try next candidate
                        discover_failed += 1
                        last_error = str(exc)
    collected = [row for row in results if row.get("ok") and not row.get("skipped") and not row.get("stopped")]
    skipped = [row for row in results if row.get("skipped")]
    failed = [row for row in results if not row.get("ok") and not row.get("stopped")]
    discovered = [row for row in collected if row.get("discovered")]
    return {
        "ok": (not stopped) and (not failed or bool(collected)),
        "kind": "material_ingest",
        "ingest_run_id": ingest_run_id,
        "results": results,
        "count": len(collected),
        "skipped": len(skipped),
        "failed": len(failed),
        "discovered": len(discovered),
        "discover_failed": discover_failed,
        "sites_queried": sites_queried,
        "site_errors": site_errors,
        "last_error": last_error,
        "stopped": stopped,
        "collected": [
            {"domain_key": row.get("domain_key"), "pack": row.get("pack"), "path": row.get("path")}
            for row in collected
        ],
    }


def _remove_domain_pack_entry(workspace: Path, domain_key: str, pack: str) -> bool:
    catalog_path = workspace / "materials" / domain_key / "CATALOG.json"
    cat = _load_json(catalog_path)
    if not cat:
        return False
    packs = [item for item in (cat.get("packs") or []) if isinstance(item, dict)]
    kept = [item for item in packs if str(item.get("pack") or "") != pack]
    if len(kept) == len(packs):
        return False
    cat["packs"] = kept
    cat["updated_at"] = utcnow()
    _dump_json(catalog_path, cat)
    return True


def delete_pack(workspace: Path, domain_key: str, pack: str) -> dict[str, Any]:
    workspace = Path(workspace).resolve()
    domain_key = str(domain_key or "").strip()
    pack = str(pack or "").strip()
    if not domain_key or not pack:
        raise ValueError("domain_key and pack required")
    try:
        pack_dir = find_pack_dir(workspace, domain_key, pack)
    except FileNotFoundError:
        pack_dir = workspace / "materials" / domain_key / pack
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"pack not found: {domain_key}/{pack}")
    rel = str(pack_dir.relative_to(workspace) if pack_dir.is_relative_to(workspace) else pack_dir)
    shutil.rmtree(pack_dir)
    removed_index = _remove_domain_pack_entry(workspace, domain_key, pack)
    return {
        "ok": True,
        "domain_key": domain_key,
        "pack": pack,
        "path": rel,
        "removed_index": removed_index,
    }


def delete_packs(workspace: Path, packs: list[dict[str, str]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for item in packs:
        domain_key = str(item.get("domain_key") or "").strip()
        pack = str(item.get("pack") or "").strip()
        try:
            results.append(delete_pack(workspace, domain_key, pack))
        except Exception as exc:  # noqa: BLE001 — collect per-pack errors
            results.append({"ok": False, "domain_key": domain_key, "pack": pack, "error": str(exc)})
    return {
        "ok": all(row.get("ok") for row in results) if results else False,
        "results": results,
        "count": sum(1 for row in results if row.get("ok")),
        "failed": sum(1 for row in results if not row.get("ok")),
    }


def audit_status(row: dict[str, Any]) -> str:
    return str((row.get("llm_audit") or {}).get("status") or "").strip().lower()


def audit_and_enqueue(
    workspace: Path,
    packs: list[dict[str, str]],
    *,
    should_stop: Callable[[], bool] | None = None,
    on_pack: Callable[[dict[str, str], int], None] | None = None,
) -> dict[str, Any]:
    """LLM-audit selected packs, then stage+enqueue pass/warn only. Does not start workers."""
    from .runner import stage_and_enqueue_only

    workspace = Path(workspace).resolve()
    audit = audit_packs(workspace, packs, should_stop=should_stop, on_pack=on_pack)
    eligible: list[dict[str, str]] = []
    rejected: list[dict[str, Any]] = []
    for row in audit.get("results") or []:
        if row.get("stopped"):
            continue
        status = audit_status(row)
        selector = {
            "domain_key": str(row.get("domain_key") or ""),
            "pack": str(row.get("pack") or ""),
        }
        if row.get("ok") and status in {"pass", "warn"}:
            eligible.append(selector)
        else:
            rejected.append({**selector, "status": status or "fail", "error": row.get("error")})

    enqueue: dict[str, Any]
    if audit.get("stopped"):
        enqueue = {"ok": False, "stopped": True, "enqueued": 0}
    elif not eligible:
        enqueue = {"ok": True, "enqueued": 0, "skipped": "no pass/warn packs"}
    elif should_stop and should_stop():
        enqueue = {"ok": False, "stopped": True, "enqueued": 0}
    else:
        enqueue = stage_and_enqueue_only(workspace, packs=eligible)

    return {
        "ok": bool(audit.get("ok")) and bool(enqueue.get("ok")),
        "kind": "material_audit_enqueue",
        "results": audit.get("results") or [],
        "count": audit.get("count") or 0,
        "skipped": audit.get("skipped") or 0,
        "stopped": bool(audit.get("stopped") or enqueue.get("stopped")),
        "eligible": eligible,
        "rejected": rejected,
        "enqueue": enqueue,
    }
