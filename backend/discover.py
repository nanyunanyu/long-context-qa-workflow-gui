"""Discover new materials packs from hardcoded public sources.

Live arXiv / gutendex APIs are optional: on many networks they time out, so 搜寻
first uses Gutenberg plaintext, GNU html_mono manuals, and IETF RFCs that the
collect scripts can actually download.
"""
from __future__ import annotations

import json
import re
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

UA = "LCQA-ingest/1.0 (research; long-context QA materials)"
ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?$", re.I)
GUTENBERG_ID_RE = re.compile(r"gutenberg\.org/(?:cache/epub|ebooks|files)/(\d+)", re.I)
PACK_ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}$")
HOT_TITLE_RE = re.compile(
    r"\b(sherlock holmes|dracula|pride and prejudice|alice'?s adventures|harry potter|"
    r"father brown|the bible|hamlet|romeo and juliet)\b",
    re.I,
)
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
ARXIV_CATS = (
    "q-bio.PE",
    "q-bio.TO",
    "astro-ph.EP",
    "astro-ph.IM",
    "math.HO",
    "cs.GL",
)
GUTENDEX_TOPICS = ("detective", "folklore", "mythology", "natural history", "travel")
GNU_MANUAL_INDEX = "https://www.gnu.org/manual/manual.html"
LICENSE_ARXIV = "arXiv e-print; no training-prohibition notice found"
LICENSE_PG = "Public domain (USA) via Project Gutenberg; keep PG header in source/raw for provenance"
LICENSE_GNU = "Official public technical documentation; no training-prohibition notice found"
LICENSE_RFC = "IETF RFC; no training-prohibition notice found"

# Titles must match Project Gutenberg's Title: header (see gutenberg_source_check).
FALLBACK_GUTENBERG: tuple[tuple[int, str, str, str], ...] = (
    (8492, "The King in Yellow", "literature", "文学"),
    (2276, "The Private Memoirs and Confessions of a Justified Sinner", "literature", "文学"),
    (3285, "The Deerslayer", "literature", "文学"),
    (175, "The Phantom of the Opera", "literature", "文学"),
    (408, "The Souls of Black Folk", "literature", "文学"),
    (1059, "The World Set Free", "literature", "文学"),
    (12163, "The Sleeper Awakes", "literature", "文学"),
    (62, "A princess of Mars", "literature", "文学"),
    (551, "The Land That Time Forgot", "literature", "文学"),
    (21700, "Don Juan", "literature", "文学"),
    (7927, "The Celibates", "literature", "文学"),
    (103, "Around the World in Eighty Days", "literature", "文学"),
    (6130, "The Iliad", "literature", "文学"),
    (1727, "The Odyssey", "literature", "文学"),
    (1998, "Thus Spake Zarathustra: A Book for All and None", "literature", "文学"),
    (28054, "The Brothers Karamazov", "literature", "文学"),
    (2554, "Crime and Punishment", "literature", "文学"),
    (1399, "Anna Karenina", "literature", "文学"),
    (24737, "The Children of Odin: The Book of Northern Myths", "literature", "文学"),
    (394, "Cranford", "literature", "文学"),
    (4212, "Culture and Anarchy", "literature", "文学"),
    (10008, "The Mystery", "detective", "侦探小说"),
    (10009, "Wild Northern Scenes; Or, Sporting Adventures with the Rifle and the Rod", "literature", "文学"),
    (2130, "Utopia", "literature", "文学"),
)

FALLBACK_GNU: tuple[tuple[str, str, str], ...] = (
    ("gnu-coreutils", "GNU Coreutils Manual", "https://www.gnu.org/software/coreutils/manual/coreutils.html"),
    ("gnu-bash", "GNU Bash Manual", "https://www.gnu.org/software/bash/manual/bash.html"),
    ("gnu-tar", "GNU tar Manual", "https://www.gnu.org/software/tar/manual/tar.html"),
    ("gnu-grep", "GNU Grep Manual", "https://www.gnu.org/software/grep/manual/grep.html"),
    ("gnu-sed", "GNU Sed Manual", "https://www.gnu.org/software/sed/manual/sed.html"),
    ("gnu-findutils", "GNU Findutils Manual", "https://www.gnu.org/software/findutils/manual/html_mono/find.html"),
    ("gnu-guile", "GNU Guile Reference Manual", "https://www.gnu.org/software/guile/manual/html_mono/guile.html"),
    ("gnu-elisp", "GNU Emacs Lisp Reference Manual", "https://www.gnu.org/software/emacs/manual/html_mono/elisp.html"),
    ("gnu-libc", "GNU C Library Manual", "https://www.gnu.org/software/libc/manual/html_mono/libc.html"),
)

FALLBACK_RFC: tuple[tuple[str, str, str], ...] = (
    ("rfc9110", "HTTP Semantics (RFC 9110)", "https://www.rfc-editor.org/rfc/rfc9110.txt"),
    ("rfc9112", "HTTP/1.1 (RFC 9112)", "https://www.rfc-editor.org/rfc/rfc9112.txt"),
    ("rfc7540", "HTTP/2 (RFC 7540)", "https://www.rfc-editor.org/rfc/rfc7540.txt"),
    ("rfc8446", "The Transport Layer Security (TLS) Protocol Version 1.3", "https://www.rfc-editor.org/rfc/rfc8446.txt"),
    ("rfc3261", "SIP: Session Initiation Protocol", "https://www.rfc-editor.org/rfc/rfc3261.txt"),
    ("rfc5321", "Simple Mail Transfer Protocol", "https://www.rfc-editor.org/rfc/rfc5321.txt"),
    ("rfc4253", "The Secure Shell (SSH) Transport Layer Protocol", "https://www.rfc-editor.org/rfc/rfc4253.txt"),
    ("rfc4511", "Lightweight Directory Access Protocol (LDAP): The Protocol", "https://www.rfc-editor.org/rfc/rfc4511.txt"),
)


class KnownSources:
    def __init__(self) -> None:
        self.packs: set[str] = set()
        self.pairs: set[tuple[str, str]] = set()
        self.arxiv: set[str] = set()
        self.gutenberg: set[str] = set()
        self.urls: set[str] = set()

    def has_pack(self, domain_key: str, pack: str) -> bool:
        return (domain_key, pack) in self.pairs or pack in self.packs

    def remember(self, spec: dict[str, Any]) -> None:
        domain_key = str(spec.get("domain_key") or "")
        pack = str(spec.get("pack") or "")
        self.packs.add(pack)
        if domain_key:
            self.pairs.add((domain_key, pack))
        if PACK_ARXIV_RE.match(pack):
            self.arxiv.add(pack)
        for doc in spec.get("docs") or []:
            if not isinstance(doc, dict):
                continue
            url = str(doc.get("url") or "")
            if url:
                self.urls.add(url)


def _ssl_context() -> ssl.SSLContext:
    try:
        from .llm_client import install_ca_bundle

        bundle = install_ca_bundle()
        if bundle:
            return ssl.create_default_context(cafile=bundle)
    except Exception:
        pass
    return ssl.create_default_context()


def http_get(url: str, timeout: int = 8) -> bytes:
    """Fetch URL via curl (same as collect scripts), then urllib+certifi."""
    tmp = Path(tempfile.gettempdir()) / f"lcqa-disc-{abs(hash(url)) % 10**12}.bin"
    proc = subprocess.run(
        [
            "curl",
            "-L",
            "--max-time",
            str(max(int(timeout), 5)),
            "-A",
            UA,
            "-sS",
            "-o",
            str(tmp),
            url,
        ],
        capture_output=True,
        check=False,
    )
    data = tmp.read_bytes() if tmp.exists() else b""
    tmp.unlink(missing_ok=True)
    if proc.returncode == 0 and len(data) > 40:
        return data
    curl_err = proc.stderr.decode(errors="replace")[-200:]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as exc:
        raise RuntimeError(f"GET failed {url}: curl={proc.returncode} {curl_err}; urllib={exc}") from exc


def scan_known(workspace: Path) -> KnownSources:
    known = KnownSources()
    materials = Path(workspace) / "materials"
    if not materials.is_dir():
        return known
    for catalog_path in materials.glob("*/*/CATALOG.json"):
        try:
            data = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            continue
        domain_key = str(data.get("domain_key") or catalog_path.parent.parent.name)
        pack = str(data.get("pack") or catalog_path.parent.name)
        known.packs.add(pack)
        known.pairs.add((domain_key, pack))
        if PACK_ARXIV_RE.match(pack):
            known.arxiv.add(pack)
        arxiv_id = str(data.get("arxiv_id") or "")
        if PACK_ARXIV_RE.match(arxiv_id):
            known.arxiv.add(arxiv_id)
        for doc in data.get("docs") or []:
            if not isinstance(doc, dict):
                continue
            url = str(doc.get("url") or "")
            if url:
                known.urls.add(url)
            aid = str(doc.get("arxiv_id") or "")
            if PACK_ARXIV_RE.match(aid):
                known.arxiv.add(aid)
            match = ARXIV_ID_RE.search(url.replace("/pdf/", "/").rstrip(".pdf"))
            if match:
                known.arxiv.add(match.group(1))
            gmatch = GUTENBERG_ID_RE.search(url)
            if gmatch:
                known.gutenberg.add(gmatch.group(1))
            slug = str(doc.get("slug") or "")
            if slug.isdigit():
                known.gutenberg.add(slug)
            if slug.startswith("pg") and slug[2:].isdigit():
                known.gutenberg.add(slug[2:])
            if pack.startswith("pg") and pack[2:].isdigit():
                known.gutenberg.add(pack[2:])
            if pack.startswith("rfc"):
                known.packs.add(pack)
    return known


def _pg_spec(ebook_id: int, title: str, domain_key: str, domain: str) -> dict[str, Any]:
    slug = f"pg{ebook_id}"
    url = f"https://www.gutenberg.org/cache/epub/{ebook_id}/pg{ebook_id}.txt"
    return {
        "source": "gutenberg",
        "domain_key": domain_key,
        "domain": domain,
        "pack": slug,
        "theme": title,
        "coldness": "cold",
        "note": f"Project Gutenberg #{ebook_id}",
        "docs": [
            {
                "doc_id": "DOC1",
                "slug": slug,
                "title": title,
                "url": url,
                "kind": "txt",
                "license_note": LICENSE_PG,
            }
        ],
    }


def _html_spec(pack: str, title: str, url: str, *, domain_key: str, domain: str, source: str, license_note: str) -> dict[str, Any]:
    kind = "txt" if url.lower().endswith(".txt") else "html"
    return {
        "source": source,
        "domain_key": domain_key,
        "domain": domain,
        "pack": pack,
        "theme": title,
        "coldness": "cold",
        "note": f"{source} 写死源",
        "docs": [
            {
                "doc_id": "DOC1",
                "slug": pack,
                "title": title,
                "url": url,
                "kind": kind,
                "license_note": license_note,
            }
        ],
    }


def hardcoded_specs(known: KnownSources) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for ebook_id, title, domain_key, domain in FALLBACK_GUTENBERG:
        if str(ebook_id) in known.gutenberg or known.has_pack(domain_key, f"pg{ebook_id}"):
            continue
        spec = _pg_spec(ebook_id, title, domain_key, domain)
        specs.append(spec)
    for pack, title, url in FALLBACK_GNU:
        if known.has_pack("user_guides", pack) or known.has_pack("software_engineering", pack):
            continue
        specs.append(
            _html_spec(
                pack,
                title,
                url,
                domain_key="user_guides",
                domain="用户指南",
                source="gnu",
                license_note=LICENSE_GNU,
            )
        )
    for pack, title, url in FALLBACK_RFC:
        if known.has_pack("software_engineering", pack) or pack in known.packs:
            continue
        specs.append(
            _html_spec(
                pack,
                title,
                url,
                domain_key="software_engineering",
                domain="软件与工程",
                source="rfc",
                license_note=LICENSE_RFC,
            )
        )
    return specs


def _arxiv_id_from_atom(entry: ET.Element) -> str | None:
    raw = (entry.findtext("a:id", default="", namespaces=ATOM_NS) or "").strip()
    match = ARXIV_ID_RE.search(raw.rsplit("/", 1)[-1])
    return match.group(1) if match else None


def search_arxiv(known: KnownSources, *, per_cat: int = 25) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    failures = 0
    for cat in ARXIV_CATS:
        query = urllib.parse.urlencode(
            {
                "search_query": f"cat:{cat}",
                "start": 0,
                "max_results": per_cat,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
        )
        url = f"https://export.arxiv.org/api/query?{query}"
        try:
            raw = http_get(url, timeout=5)
        except Exception:
            failures += 1
            if failures >= 2:
                break
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        for entry in root.findall("a:entry", ATOM_NS):
            arxiv_id = _arxiv_id_from_atom(entry)
            title = re.sub(r"\s+", " ", entry.findtext("a:title", default="", namespaces=ATOM_NS) or "").strip()
            if not arxiv_id or not title:
                continue
            if arxiv_id in known.arxiv or known.has_pack("academic", arxiv_id):
                continue
            pdf = f"https://export.arxiv.org/pdf/{arxiv_id}"
            spec = {
                "source": "arxiv",
                "domain_key": "academic",
                "domain": "学术",
                "pack": arxiv_id,
                "theme": title,
                "coldness": "cold",
                "note": f"arXiv {cat} 自动搜寻",
                "docs": [
                    {
                        "doc_id": "DOC1",
                        "slug": arxiv_id,
                        "title": title,
                        "url": pdf,
                        "kind": "pdf",
                        "arxiv_id": arxiv_id,
                        "license_note": LICENSE_ARXIV,
                    }
                ],
            }
            specs.append(spec)
            known.arxiv.add(arxiv_id)
            known.urls.add(pdf)
    return specs


def _gutenberg_plain_url(formats: dict[str, Any], ebook_id: int) -> str | None:
    for key, value in formats.items():
        if not isinstance(value, str):
            continue
        key_l = key.lower()
        if "text/plain" in key_l and "utf-8" in key_l:
            return value
    for key, value in formats.items():
        if isinstance(value, str) and "text/plain" in key.lower():
            return value
    return f"https://www.gutenberg.org/cache/epub/{ebook_id}/pg{ebook_id}.txt"


def _gutenberg_domain(subjects: list[str]) -> tuple[str, str]:
    blob = " ".join(subjects).lower()
    if any(token in blob for token in ("detective", "mystery", "crime", "espionage")):
        return "detective", "侦探小说"
    return "literature", "文学"


def search_gutenberg(known: KnownSources, *, per_topic: int = 20) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    failures = 0
    for topic in GUTENDEX_TOPICS:
        query = urllib.parse.urlencode(
            {
                "languages": "en",
                "copyright": "false",
                "topic": topic,
                "sort": "popular",
            }
        )
        url = f"https://gutendex.com/books?{query}"
        try:
            payload = json.loads(http_get(url, timeout=5).decode("utf-8", errors="replace"))
        except Exception:
            failures += 1
            if failures >= 2:
                break
            continue
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            continue
        for item in results[:per_topic]:
            if not isinstance(item, dict):
                continue
            try:
                ebook_id = int(item.get("id") or 0)
            except (TypeError, ValueError):
                continue
            title = str(item.get("title") or "").strip()
            if not ebook_id or not title or HOT_TITLE_RE.search(title):
                continue
            if str(ebook_id) in known.gutenberg:
                continue
            download_count = int(item.get("download_count") or 0)
            if download_count > 80_000:
                continue
            formats = item.get("formats") if isinstance(item.get("formats"), dict) else {}
            txt_url = _gutenberg_plain_url(formats, ebook_id)
            if not txt_url:
                continue
            if txt_url in known.urls:
                continue
            subjects = [str(s) for s in (item.get("subjects") or []) if isinstance(s, str)]
            subjects.extend(str(s) for s in (item.get("bookshelves") or []) if isinstance(s, str))
            domain_key, domain = _gutenberg_domain(subjects + [topic])
            slug = f"pg{ebook_id}"
            if known.has_pack(domain_key, slug):
                continue
            spec = {
                "source": "gutenberg",
                "domain_key": domain_key,
                "domain": domain,
                "pack": slug,
                "theme": title,
                "coldness": "cold",
                "note": f"Project Gutenberg #{ebook_id} · {topic}",
                "docs": [
                    {
                        "doc_id": "DOC1",
                        "slug": slug,
                        "title": title,
                        "url": txt_url,
                        "kind": "txt",
                        "license_note": LICENSE_PG,
                    }
                ],
            }
            specs.append(spec)
            known.gutenberg.add(str(ebook_id))
            known.urls.add(txt_url)
    return specs


class _GnuLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href") or ""
        if "/software/" in href and "manual" in href.lower():
            self.hrefs.append(href)


def _gnu_pack_name(href: str) -> str:
    parts = [p for p in urllib.parse.urlparse(href).path.split("/") if p]
    if "software" in parts:
        idx = parts.index("software")
        if idx + 1 < len(parts):
            return f"gnu-{parts[idx + 1]}"
    return re.sub(r"[^a-z0-9-]+", "-", href.lower())[:40].strip("-") or "gnu-manual"


def search_gnu(known: KnownSources) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    try:
        html = http_get(GNU_MANUAL_INDEX, timeout=8).decode("utf-8", errors="replace")
    except Exception:
        return specs
    parser = _GnuLinkParser()
    parser.feed(html)
    seen: set[str] = set()
    for href in parser.hrefs:
        if not href.lower().endswith(".html"):
            continue
        abs_url = urllib.parse.urljoin(GNU_MANUAL_INDEX, href)
        pack = _gnu_pack_name(href)
        if pack in seen or known.has_pack("user_guides", pack) or known.has_pack("software_engineering", pack):
            continue
        seen.add(pack)
        title = pack.replace("gnu-", "GNU ").replace("-", " ").title()
        spec = {
            "source": "gnu",
            "domain_key": "user_guides",
            "domain": "用户指南",
            "pack": pack,
            "theme": title,
            "coldness": "cold",
            "note": "GNU manuals 索引自动搜寻",
            "docs": [
                {
                    "doc_id": "DOC1",
                    "slug": pack,
                    "title": title,
                    "url": abs_url,
                    "kind": "html",
                    "license_note": LICENSE_GNU,
                }
            ],
        }
        specs.append(spec)
    return specs


def discover_specs(
    workspace: Path,
    *,
    want: int = 30,
    should_stop: Callable[[], bool] | None = None,
    on_status: Callable[[str], None] | None = None,
    errors: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return new collect_pack specs not already in the workspace."""
    known = scan_known(workspace)
    out: list[dict[str, Any]] = []

    def note(message: str) -> None:
        if errors is not None:
            errors.append(message)

    def take(_source: str, rows: list[dict[str, Any]]) -> None:
        for spec in rows:
            if should_stop and should_stop():
                return
            if known.has_pack(str(spec.get("domain_key") or ""), str(spec.get("pack") or "")):
                continue
            out.append(spec)
            known.remember(spec)
            if len(out) >= want:
                return

    if on_status:
        on_status("gutenberg")
    if not (should_stop and should_stop()):
        take("hardcoded", hardcoded_specs(known))
    if len(out) >= want:
        return out[:want]

    if on_status:
        on_status("gutenberg-api")
    if not (should_stop and should_stop()):
        try:
            take("gutenberg", search_gutenberg(known))
        except Exception as exc:
            note(f"gutenberg: {exc}")
    if len(out) >= want:
        return out[:want]

    if on_status:
        on_status("arxiv")
    if not (should_stop and should_stop()):
        try:
            take("arxiv", search_arxiv(known))
        except Exception as exc:
            note(f"arxiv: {exc}")
    if len(out) >= want:
        return out[:want]
    if on_status:
        on_status("gnu")
    if not (should_stop and should_stop()):
        try:
            take("gnu", search_gnu(known))
        except Exception as exc:
            note(f"gnu: {exc}")
    return out[:want]
