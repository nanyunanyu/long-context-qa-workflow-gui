"""Persist desktop UI prefs in ~/.config/lcqa-desktop.json (alongside recents)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

APP_CONFIG = Path.home() / ".config" / "lcqa-desktop.json"

DEFAULT_UI: dict[str, Any] = {
    "last_workspace": None,
    "auto_open_workspace": True,
    "last_tab": "workspace",
    "board": {
        "selected_statuses": None,  # None → frontend uses all
        "materials_collapsed": False,
        "date_from": None,
        "date_to": None,
        "question_type": "short_answer",
    },
    "materials": {
        "selected_domains": None,
        "selected_statuses": None,
    },
}


def _read_raw() -> dict[str, Any]:
    if not APP_CONFIG.is_file():
        return {"recents": [], "ui": dict(DEFAULT_UI)}
    try:
        data = json.loads(APP_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"recents": [], "ui": dict(DEFAULT_UI)}
    if not isinstance(data, dict):
        return {"recents": [], "ui": dict(DEFAULT_UI)}
    return data


def _write_raw(data: dict[str, Any]) -> None:
    APP_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    APP_CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_recents() -> list[str]:
    data = _read_raw()
    recents = data.get("recents")
    return [str(p) for p in recents] if isinstance(recents, list) else []


def push_recent(path: Path | str) -> list[str]:
    data = _read_raw()
    path_s = str(Path(path).expanduser().resolve())
    recents = [p for p in get_recents() if p != path_s]
    recents.insert(0, path_s)
    data["recents"] = recents[:12]
    ui = data.get("ui") if isinstance(data.get("ui"), dict) else {}
    ui = {**DEFAULT_UI, **ui}
    ui["last_workspace"] = path_s
    data["ui"] = ui
    _write_raw(data)
    return data["recents"]


def _norm_question_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"short_answer", "multiple_choice", "auto"}:
        return text
    return "short_answer"


def _norm_board_day(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def get_ui_prefs() -> dict[str, Any]:
    data = _read_raw()
    ui = data.get("ui") if isinstance(data.get("ui"), dict) else {}
    board = ui.get("board") if isinstance(ui.get("board"), dict) else {}
    materials = ui.get("materials") if isinstance(ui.get("materials"), dict) else {}
    return {
        "last_workspace": ui.get("last_workspace") or (get_recents()[0] if get_recents() else None),
        "auto_open_workspace": bool(ui.get("auto_open_workspace", True)),
        "last_tab": str(ui.get("last_tab") or "workspace"),
        "board": {
            "selected_statuses": board.get("selected_statuses"),
            "materials_collapsed": bool(board.get("materials_collapsed", False)),
            "date_from": _norm_board_day(board.get("date_from")),
            "date_to": _norm_board_day(board.get("date_to")),
            "question_type": _norm_question_type(board.get("question_type")),
        },
        "materials": {
            "selected_domains": materials.get("selected_domains"),
            "selected_statuses": materials.get("selected_statuses"),
        },
    }


def save_ui_prefs(patch: dict[str, Any]) -> dict[str, Any]:
    data = _read_raw()
    ui = data.get("ui") if isinstance(data.get("ui"), dict) else {}
    board_prev = ui.get("board") if isinstance(ui.get("board"), dict) else {}
    materials_prev = ui.get("materials") if isinstance(ui.get("materials"), dict) else {}
    merged = {
        **DEFAULT_UI,
        **ui,
        "board": {**DEFAULT_UI["board"], **board_prev},
        "materials": {**DEFAULT_UI["materials"], **materials_prev},
    }

    if "last_workspace" in patch:
        value = patch.get("last_workspace")
        merged["last_workspace"] = str(value) if value else None
    if "auto_open_workspace" in patch:
        merged["auto_open_workspace"] = bool(patch.get("auto_open_workspace"))
    if "last_tab" in patch and patch.get("last_tab"):
        merged["last_tab"] = str(patch["last_tab"])

    if isinstance(patch.get("board"), dict):
        board_patch = dict(patch["board"])
        if "date_from" in board_patch:
            board_patch["date_from"] = _norm_board_day(board_patch.get("date_from"))
        if "date_to" in board_patch:
            board_patch["date_to"] = _norm_board_day(board_patch.get("date_to"))
        if "question_type" in board_patch:
            board_patch["question_type"] = _norm_question_type(board_patch.get("question_type"))
        merged["board"] = {**merged["board"], **board_patch}
    if isinstance(patch.get("materials"), dict):
        merged["materials"] = {**merged["materials"], **patch["materials"]}

    data["ui"] = merged
    if "recents" not in data:
        data["recents"] = []
    _write_raw(data)
    return get_ui_prefs()
