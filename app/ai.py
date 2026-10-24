"""Optional AI over OpenAI-compatible APIs: primary provider, automatic fallback to secondary."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Optional

import httpx

from app import config

log = logging.getLogger("teams.ai")


def providers() -> list[dict]:
    out = []
    for p in ("PRIMARY", "SECONDARY"):
        base = getattr(config, f"AI_{p}_BASE_URL")
        model = getattr(config, f"AI_{p}_MODEL")
        if base and model:
            out.append({"name": p.lower(), "base": base, "key": getattr(config, f"AI_{p}_API_KEY"), "model": model})
    return out


def enabled() -> bool:
    return bool(providers())


_throttle_lock = threading.Lock()
_last_call = 0.0


def _throttle():
    """Gentle global spacing between AI calls (AI_MIN_INTERVAL_MS) so bursts of uploads or the backfill don't spike credits."""
    global _last_call
    gap = max(0, config.AI_MIN_INTERVAL_MS) / 1000.0
    with _throttle_lock:
        wait = _last_call + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def chat(messages: list[dict], max_tokens: int = 400, temperature: float = 0.3) -> Optional[str]:
    _throttle()
    for p in providers():
        try:
            headers = {"Content-Type": "application/json"}
            if p["key"]:
                headers["Authorization"] = f"Bearer {p['key']}"
            r = httpx.post(f"{p['base']}/chat/completions", headers=headers, timeout=config.AI_TIMEOUT_SECONDS,
                           json={"model": p["model"], "messages": messages, "max_tokens": max_tokens,
                                 "temperature": temperature})
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"]
                if content:
                    return content.strip()
            log.warning("AI %s returned %s: %s", p["name"], r.status_code, r.text[:200])
        except Exception as e:
            log.warning("AI %s failed: %s", p["name"], e)
    return None


def _clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip().strip('"\'“”‘’`*#').strip()
    t = re.sub(r"\.(pdf|docx?|pptx?|xlsx?|csv|txt|md)$", "", t, flags=re.I)
    return t[:120].rstrip(" -–:,.")


def analyze(title: str, text: str, want_meta: bool = False, filename: str = "") -> Optional[dict]:
    """ONE call that returns summary + topics, and (when want_meta) a title + description too.
    Result: {"summary": str|None, "topics": [...], "title": str|None, "description": str|None} or None on failure."""
    if not enabled() or not (text or title):
        return None
    if want_meta:
        shape = ('{"title": "<clear, specific document title, max 80 chars, Title Case, no file extension>", '
                 '"description": "<1-2 plain sentences, max 240 chars, saying what the document is and who it helps>", '
                 '"summary": "<2 short sentences>", "topics": ["<1-3 word topic>", ...up to 5]}')
        intro = ("You name and index documents for a file-sharing site. Ignore placeholder titles like "
                 "'PptxGenJS Presentation', 'Microsoft Word - ...', 'Untitled' or 'Presentation1'.")
    else:
        shape = '{"summary": "<2 short sentences>", "topics": ["<1-3 word topic>", ...up to 5]}'
        intro = "You index documents for a file-sharing site."
    prompt = (f"{intro} Reply ONLY with JSON: {shape}.\n\n"
              + (f"File name: {filename}\n" if filename else "")
              + f"Current title: {title}\n\nContent:\n{(text or '')[:config.AI_MAX_INPUT_CHARS]}")
    out = chat([{"role": "user", "content": prompt}], max_tokens=420 if want_meta else 300)
    if not out:
        return None
    m = re.search(r"\{.*\}", out, re.S)
    try:
        data = json.loads(m.group(0) if m else out)
    except Exception:
        return {"summary": out[:600], "topics": [], "title": None, "description": None}
    topics = [str(t).strip()[:40] for t in (data.get("topics") or []) if str(t).strip()][:5]
    res = {"summary": (str(data.get("summary") or "").strip()[:600] or None), "topics": topics, "title": None, "description": None}
    if want_meta:
        t = _clean_title(data.get("title") or "")
        res["title"] = t if len(t) >= 3 else None
        d = re.sub(r"\s+", " ", str(data.get("description") or "")).strip()
        res["description"] = d[: config.MAX_DESCRIPTION_LENGTH] or None
    return res


def summarize_and_tag(title: str, text: str) -> tuple[Optional[str], list[str]]:
    r = analyze(title, text)
    return (r["summary"], r["topics"]) if r else (None, [])
