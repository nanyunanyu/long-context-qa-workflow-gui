"""Persist LCQA desktop model / base_url / api_key settings under ~/.config/lcqa-desktop/."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".config" / "lcqa-desktop"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
KEYS_PATH = CONFIG_DIR / "keys.env"

REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
PIPELINE_ROLES = ("generation", "evaluation", "judge")
AUX_ROLES = ("review", "material_audit")
DEFAULT_REASONING = {
    "generation": "medium",
    "evaluation": "high",
    "judge": "medium",
    "review": "medium",
    "material_audit": "medium",
}
DEFAULTS = {
    "generation": {
        "model": "gpt-5.6-sol",
        "base_url": "",
        "reasoning_effort": DEFAULT_REASONING["generation"],
    },
    "evaluation": {
        "model": "qwen3.5-35b-a3b",
        "base_url": "",
        "reasoning_effort": DEFAULT_REASONING["evaluation"],
    },
    "judge": {
        "model": "gpt-5.6-luna",
        "base_url": "https://api.openai.com/v1",
        "reasoning_effort": DEFAULT_REASONING["judge"],
    },
    "review": {
        "model": "gpt-5.6-luna",
        "base_url": "",
        "reasoning_effort": DEFAULT_REASONING["review"],
    },
    "material_audit": {
        "model": "gpt-5.6-luna",
        "base_url": "",
        "reasoning_effort": DEFAULT_REASONING["material_audit"],
    },
}

KEY_NAMES = {
    "generation": "LCQA_GEN_API_KEY",
    "evaluation": "LCQA_EVAL_API_KEY",
    "judge": "LCQA_JUDGE_API_KEY",
    "review": "LCQA_REVIEW_API_KEY",
    "material_audit": "LCQA_AUDIT_API_KEY",
}

LEGACY_KEY_FILE = Path.home() / ".config" / "ai-keys.env"
LEGACY_VANKIT = Path("/Users/a1215/Desktop/vankit.txt")


def normalize_reasoning_effort(value: object, default: str = "medium") -> str:
    text = str(value or "").strip().lower()
    if text in REASONING_EFFORTS:
        return text
    fallback = str(default or "medium").strip().lower()
    return fallback if fallback in REASONING_EFFORTS else "medium"


def _mask(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}…{text[-4:]}"


def _parse_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    pairs: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        pairs[key.strip()] = value.strip().strip("'\"")
    return pairs


def _write_env(path: Path, pairs: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}\n" for k, v in pairs.items() if v]
    path.write_text("".join(lines), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_settings_file() -> dict[str, Any]:
    if not SETTINGS_PATH.is_file():
        return {role: dict(cfg) for role, cfg in DEFAULTS.items()}
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {role: dict(cfg) for role, cfg in DEFAULTS.items()}
    out: dict[str, Any] = {}
    for role, defaults in DEFAULTS.items():
        row = data.get(role) if isinstance(data.get(role), dict) else {}
        out[role] = {
            "model": str(row.get("model") or defaults["model"]).strip() or defaults["model"],
            "base_url": str(row.get("base_url") if row.get("base_url") is not None else defaults["base_url"]).strip(),
            "reasoning_effort": normalize_reasoning_effort(
                row.get("reasoning_effort"), defaults["reasoning_effort"]
            ),
        }
    return out


def _legacy_vankit() -> dict[str, str]:
    return _parse_env(LEGACY_VANKIT)


def _legacy_ai_keys() -> dict[str, str]:
    return _parse_env(LEGACY_KEY_FILE)


def resolve_secrets() -> dict[str, str]:
    """Effective api keys: GUI keys.env wins, then legacy files. Review/audit fall back to judge."""
    gui = _parse_env(KEYS_PATH)
    legacy = _legacy_ai_keys()
    vankit = _legacy_vankit()
    judge = gui.get("LCQA_JUDGE_API_KEY") or legacy.get("OPENAI_API_KEY") or ""
    return {
        "generation": gui.get("LCQA_GEN_API_KEY") or vankit.get("api_key") or "",
        "evaluation": gui.get("LCQA_EVAL_API_KEY") or legacy.get("DASHSCOPE_API_KEY") or "",
        "judge": judge,
        "review": gui.get("LCQA_REVIEW_API_KEY") or judge,
        "material_audit": gui.get("LCQA_AUDIT_API_KEY") or judge,
    }


def own_secrets() -> dict[str, str]:
    """Keys stored for each role without judge fallback."""
    gui = _parse_env(KEYS_PATH)
    legacy = _legacy_ai_keys()
    vankit = _legacy_vankit()
    return {
        "generation": gui.get("LCQA_GEN_API_KEY") or vankit.get("api_key") or "",
        "evaluation": gui.get("LCQA_EVAL_API_KEY") or legacy.get("DASHSCOPE_API_KEY") or "",
        "judge": gui.get("LCQA_JUDGE_API_KEY") or legacy.get("OPENAI_API_KEY") or "",
        "review": gui.get("LCQA_REVIEW_API_KEY") or "",
        "material_audit": gui.get("LCQA_AUDIT_API_KEY") or "",
    }


def resolve_settings() -> dict[str, dict[str, str]]:
    settings = _read_settings_file()
    vankit = _legacy_vankit()
    legacy = _legacy_ai_keys()
    if not settings["generation"]["base_url"] and vankit.get("base_url"):
        settings["generation"]["base_url"] = vankit["base_url"]
    if not settings["evaluation"]["base_url"] and legacy.get("DASHSCOPE_BASE_URL"):
        settings["evaluation"]["base_url"] = legacy["DASHSCOPE_BASE_URL"]
    judge_base = settings["judge"]["base_url"] or "https://api.openai.com/v1"
    if not settings["review"]["base_url"]:
        settings["review"]["base_url"] = judge_base
    if not settings["material_audit"]["base_url"]:
        settings["material_audit"]["base_url"] = judge_base
    return settings


def get_settings_public() -> dict[str, Any]:
    settings = resolve_settings()
    secrets = resolve_secrets()
    owned = own_secrets()
    roles = {}
    for role in DEFAULTS:
        key = secrets.get(role) or ""
        own = owned.get(role) or ""
        if own:
            source = "own"
        elif role in AUX_ROLES and secrets.get("judge"):
            source = "judge"
        else:
            source = "none"
        roles[role] = {
            "model": settings[role]["model"],
            "base_url": settings[role]["base_url"],
            "reasoning_effort": settings[role]["reasoning_effort"],
            "api_key_set": bool(key),
            "api_key_mask": _mask(key),
            "api_key_source": source,
        }
    ready = all(roles[r]["api_key_set"] for r in PIPELINE_ROLES)
    return {
        "roles": roles,
        "ready": ready,
        "review_ready": bool(roles["review"]["api_key_set"]),
        "material_audit_ready": bool(roles["material_audit"]["api_key_set"]),
        "config_dir": str(CONFIG_DIR),
        "settings_path": str(SETTINGS_PATH),
        "keys_path": str(KEYS_PATH),
    }


def key_status() -> dict[str, Any]:
    public = get_settings_public()
    roles = public["roles"]
    return {
        "file_exists": KEYS_PATH.is_file() or LEGACY_KEY_FILE.is_file() or LEGACY_VANKIT.is_file(),
        "ready": public["ready"],
        "generation": roles["generation"]["api_key_set"],
        "evaluation": roles["evaluation"]["api_key_set"],
        "judge": roles["judge"]["api_key_set"],
        "review": roles["review"]["api_key_set"],
        "material_audit": roles["material_audit"]["api_key_set"],
        "review_ready": public["review_ready"],
        "material_audit_ready": public["material_audit_ready"],
        # backward-compatible aliases used by existing UI
        "openai": roles["judge"]["api_key_set"],
        "dashscope": roles["evaluation"]["api_key_set"],
        "path": str(KEYS_PATH if KEYS_PATH.is_file() else LEGACY_KEY_FILE),
        "config_dir": str(CONFIG_DIR),
    }


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Update settings.json and keys.env. Empty api_key means keep existing."""
    current = _read_settings_file()
    gui_keys = _parse_env(KEYS_PATH)
    roles_in = payload.get("roles") if isinstance(payload.get("roles"), dict) else payload

    for role in DEFAULTS:
        row = roles_in.get(role) if isinstance(roles_in.get(role), dict) else {}
        model = str(row.get("model") or "").strip()
        base_url = str(row.get("base_url") if row.get("base_url") is not None else current[role]["base_url"]).strip()
        if model:
            current[role]["model"] = model
        current[role]["base_url"] = base_url
        if "reasoning_effort" in row:
            raw_effort = str(row.get("reasoning_effort") or "").strip().lower()
            if raw_effort and raw_effort not in REASONING_EFFORTS:
                raise ValueError(f"{role} reasoning_effort must be one of: {', '.join(REASONING_EFFORTS)}")
            if raw_effort:
                current[role]["reasoning_effort"] = raw_effort
        # Always persist a canonical value so settings.json is not left without the key.
        current[role]["reasoning_effort"] = normalize_reasoning_effort(
            current[role].get("reasoning_effort"), DEFAULT_REASONING[role]
        )
        api_key = row.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            gui_keys[KEY_NAMES[role]] = api_key.strip()

    # Shell wrappers source this file; keep classic aliases in sync.
    if gui_keys.get("LCQA_EVAL_API_KEY"):
        gui_keys["DASHSCOPE_API_KEY"] = gui_keys["LCQA_EVAL_API_KEY"]
    if gui_keys.get("LCQA_JUDGE_API_KEY"):
        gui_keys["OPENAI_API_KEY"] = gui_keys["LCQA_JUDGE_API_KEY"]
    if current["evaluation"]["base_url"]:
        gui_keys["DASHSCOPE_BASE_URL"] = current["evaluation"]["base_url"]
    if current["judge"]["base_url"]:
        gui_keys["OPENAI_BASE_URL"] = current["judge"]["base_url"]

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_env(KEYS_PATH, gui_keys)
    return get_settings_public()


def apply_to_environ(env: dict[str, str] | None = None) -> dict[str, str]:
    """Inject resolved settings into a subprocess environment."""
    out = dict(env or os.environ)
    settings = resolve_settings()
    secrets = resolve_secrets()

    gen_model = settings["generation"]["model"]
    gen_base = settings["generation"]["base_url"]
    gen_effort = settings["generation"]["reasoning_effort"]
    gen_key = secrets["generation"]
    eval_model = settings["evaluation"]["model"]
    eval_base = settings["evaluation"]["base_url"]
    eval_effort = settings["evaluation"]["reasoning_effort"]
    eval_key = secrets["evaluation"]
    judge_model = settings["judge"]["model"]
    judge_base = settings["judge"]["base_url"] or "https://api.openai.com/v1"
    judge_effort = settings["judge"]["reasoning_effort"]
    judge_key = secrets["judge"]
    review_model = settings["review"]["model"]
    review_base = settings["review"]["base_url"] or judge_base
    review_effort = settings["review"]["reasoning_effort"]
    review_key = secrets["review"]
    audit_model = settings["material_audit"]["model"]
    audit_base = settings["material_audit"]["base_url"] or judge_base
    audit_effort = settings["material_audit"]["reasoning_effort"]
    audit_key = secrets["material_audit"]

    if gen_model:
        out["LCQA_GEN_MODEL"] = gen_model
    if gen_base:
        out["LCQA_GEN_BASE_URL"] = gen_base
    if gen_effort:
        out["LCQA_GEN_REASONING_EFFORT"] = gen_effort
    if gen_key:
        out["LCQA_GEN_API_KEY"] = gen_key

    if eval_model:
        out["LCQA_EVAL_MODEL"] = eval_model
    if eval_base:
        out["LCQA_EVAL_BASE_URL"] = eval_base
        out["DASHSCOPE_BASE_URL"] = eval_base
    if eval_effort:
        out["LCQA_EVAL_REASONING_EFFORT"] = eval_effort
    if eval_key:
        out["LCQA_EVAL_API_KEY"] = eval_key
        out["DASHSCOPE_API_KEY"] = eval_key

    if judge_model:
        out["LCQA_JUDGE_MODEL"] = judge_model
    if judge_base:
        out["LCQA_JUDGE_BASE_URL"] = judge_base
        out["OPENAI_BASE_URL"] = judge_base
    if judge_effort:
        out["LCQA_JUDGE_REASONING_EFFORT"] = judge_effort
    if judge_key:
        out["LCQA_JUDGE_API_KEY"] = judge_key
        out["OPENAI_API_KEY"] = judge_key

    if review_model:
        out["LCQA_REVIEW_MODEL"] = review_model
    if review_base:
        out["LCQA_REVIEW_BASE_URL"] = review_base
    if review_effort:
        out["LCQA_REVIEW_REASONING_EFFORT"] = review_effort
    if review_key:
        out["LCQA_REVIEW_API_KEY"] = review_key

    if audit_model:
        out["LCQA_AUDIT_MODEL"] = audit_model
    if audit_base:
        out["LCQA_AUDIT_BASE_URL"] = audit_base
    if audit_effort:
        out["LCQA_AUDIT_REASONING_EFFORT"] = audit_effort
    if audit_key:
        out["LCQA_AUDIT_API_KEY"] = audit_key

    # Prefer GUI keys.env when present so shell wrappers source it.
    if KEYS_PATH.is_file():
        out["AI_KEYS_ENV"] = str(KEYS_PATH)

    return out
