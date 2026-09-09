"""Locate the original pipeline tree and the user workspace."""
from __future__ import annotations

import os
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent
DESKTOP_ROOT = _BACKEND_DIR.parent
CODE_ROOT = Path(os.environ.get("LCQA_CODE_ROOT") or DESKTOP_ROOT.parent).expanduser().resolve()


def code_root() -> Path:
    env = (os.environ.get("LCQA_CODE_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return CODE_ROOT


def data_root(explicit: Path | str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = (os.environ.get("LCQA_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return code_root()
