"""OpenAI-compatible chat client for desktop auto-review and material audit."""
from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from typing import Any

from .paths import code_root
from .settings import apply_to_environ, resolve_secrets, resolve_settings

try:
    from lcqa_reasoning import openai_chat_kwargs, openai_responses_kwargs
except ImportError:
    import sys

    scripts = code_root() / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from lcqa_reasoning import openai_chat_kwargs, openai_responses_kwargs  # type: ignore


JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def load_prompt(name: str) -> str:
    path = code_root() / "scripts" / "prompts" / name
    if not path.is_file():
        raise FileNotFoundError(f"prompt missing: {path}")
    return path.read_text(encoding="utf-8")


def extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty model output")
    fenced = JSON_FENCE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model output is not a JSON object")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model JSON is not an object")
    return data


def role_config(role: str) -> dict[str, str]:
    settings = resolve_settings()
    secrets = resolve_secrets()
    if role not in settings:
        raise KeyError(f"unknown role: {role}")
    model = settings[role]["model"]
    base_url = (settings[role]["base_url"] or "").rstrip("/")
    if not base_url:
        base_url = "https://api.openai.com/v1"
    key = secrets.get(role) or ""
    if not key:
        raise RuntimeError(f"{role} API key is not configured")
    return {
        "model": model,
        "base_url": base_url,
        "api_key": key,
        "reasoning_effort": settings[role]["reasoning_effort"],
    }


def install_ca_bundle(*, force: bool = False) -> str:
    """Point OpenSSL at certifi. python.org macOS builds ship with no cert.pem."""
    existing = (os.environ.get("SSL_CERT_FILE") or "").strip()
    if not force and existing and os.path.isfile(existing):
        return existing
    try:
        import certifi

        bundle = certifi.where()
    except Exception:
        return existing
    if not bundle or not os.path.isfile(bundle):
        return existing
    os.environ["SSL_CERT_FILE"] = bundle
    os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)
    os.environ.setdefault("CURL_CA_BUNDLE", bundle)
    return bundle


def _ssl_context() -> ssl.SSLContext:
    bundle = install_ca_bundle()
    if bundle:
        return ssl.create_default_context(cafile=bundle)
    return ssl.create_default_context()


def _is_ssl_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "certificate_verify_failed" in text or "certificate verify failed" in text


def _wrap_llm_error(exc: BaseException) -> RuntimeError:
    if _is_ssl_error(exc):
        return RuntimeError(
            "HTTPS 证书校验失败（不是 API Key）。请完全退出并重新打开桌面端后再试。"
        )
    return RuntimeError(f"LLM request failed: {exc}")


def _http_json(url: str, payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail[:800]}") from exc
    except urllib.error.URLError as exc:
        raise _wrap_llm_error(exc.reason if exc.reason else exc) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LLM returned non-JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("LLM returned non-object JSON")
    return data


def _content_from_responses(data: dict[str, Any]) -> str:
    text = data.get("output_text")
    if isinstance(text, str) and text.strip():
        return text
    parts: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for chunk in item.get("content") or []:
            if isinstance(chunk, dict) and chunk.get("text"):
                parts.append(str(chunk["text"]))
    return "\n".join(parts).strip()


def _content_from_chat(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
    return ""


def _openai_client(cfg: dict[str, str], timeout: int):
    from openai import OpenAI

    return OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=float(timeout))


def _chat_via_openai(cfg: dict[str, str], messages: list[dict[str, str]], timeout: int, max_tokens: int) -> str:
    client = _openai_client(cfg, timeout)
    effort = cfg["reasoning_effort"]
    last_error: Exception | None = None
    try:
        kwargs: dict[str, Any] = {
            "model": cfg["model"],
            "input": messages,
            "max_output_tokens": max_tokens,
        }
        if effort and effort != "none":
            kwargs.update(openai_responses_kwargs(effort))
        resp = client.responses.create(**kwargs)
        text = (getattr(resp, "output_text", None) or "").strip()
        if not text:
            dumped = resp.model_dump() if hasattr(resp, "model_dump") else {}
            text = _content_from_responses(dumped if isinstance(dumped, dict) else {})
        if text:
            return text
    except Exception as exc:  # noqa: BLE001 — fall back to chat
        last_error = exc

    try:
        kwargs = {
            "model": cfg["model"],
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }
        if effort and effort != "none":
            kwargs.update(openai_chat_kwargs(effort))
        chat = client.chat.completions.create(**kwargs)
        message = chat.choices[0].message if chat.choices else None
        text = (getattr(message, "content", None) or "").strip()
    except Exception as exc:  # noqa: BLE001
        if last_error:
            raise RuntimeError(f"Responses failed ({_wrap_llm_error(last_error)}); chat failed ({_wrap_llm_error(exc)})") from exc
        raise _wrap_llm_error(exc) from exc
    if not text:
        raise RuntimeError("LLM returned empty content")
    return text


def _chat_via_urllib(cfg: dict[str, str], messages: list[dict[str, str]], timeout: int, max_tokens: int) -> str:
    effort = cfg["reasoning_effort"]
    responses_payload: dict[str, Any] = {
        "model": cfg["model"],
        "input": messages,
        "max_output_tokens": max_tokens,
    }
    if effort and effort != "none":
        responses_payload.update(openai_responses_kwargs(effort))
    last_error: Exception | None = None
    try:
        data = _http_json(
            f"{cfg['base_url']}/responses",
            responses_payload,
            cfg["api_key"],
            timeout,
        )
        text = _content_from_responses(data)
        if text:
            return text
    except Exception as exc:  # noqa: BLE001 — fall back to chat
        last_error = exc

    chat_payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    if effort and effort != "none":
        chat_payload.update(openai_chat_kwargs(effort))
    try:
        data = _http_json(
            f"{cfg['base_url']}/chat/completions",
            chat_payload,
            cfg["api_key"],
            timeout,
        )
    except Exception as exc:  # noqa: BLE001
        if last_error:
            raise RuntimeError(f"Responses failed ({last_error}); chat failed ({exc})") from exc
        raise
    text = _content_from_chat(data)
    if not text:
        raise RuntimeError("LLM returned empty content")
    return text


def chat_text(role: str, *, system: str, user: str, timeout: int = 600, max_tokens: int = 4096) -> str:
    """Call the configured role. Prefer Responses API, fall back to chat completions."""
    apply_to_environ()
    install_ca_bundle()
    cfg = role_config(role)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        return _chat_via_openai(cfg, messages, timeout, max_tokens)
    except ImportError:
        return _chat_via_urllib(cfg, messages, timeout, max_tokens)


def chat_json(role: str, *, system: str, user: str, timeout: int = 600, max_tokens: int = 4096) -> dict[str, Any]:
    text = chat_text(role, system=system, user=user, timeout=timeout, max_tokens=max_tokens)
    return extract_json_object(text)
