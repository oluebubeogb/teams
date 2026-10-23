"""Optional AI over OpenAI-compatible APIs: primary provider, automatic fallback to secondary."""
from __future__ import annotations

import json
import logging
import re
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


def chat(messages: list[dict], max_tokens: int = 400, temperature: float = 0.3) -> Optional[str]:
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


def summarize_and_tag(title: str, text: str) -> tuple[Optional[str], list[str]]:
    if not enabled() or not (text or title):
        return None, []
    prompt = (
        "You index documents for a file-sharing site. Reply ONLY with JSON: "
        '{"summary": "<2 short sentences>", "topics": ["<1-3 word topic>", ...up to 5]}.\n\n'
        f"Title: {title}\n\nContent:\n{(text or '')[:6000]}"
    )
    out = chat([{"role": "user", "content": prompt}], max_tokens=300)
    if not out:
        return None, []
    m = re.search(r"\{.*\}", out, re.S)
    try:
        data = json.loads(m.group(0) if m else out)
        topics = [str(t).strip()[:40] for t in data.get("topics", []) if str(t).strip()][:5]
        return (str(data.get("summary") or "").strip()[:600] or None), topics
    except Exception:
        return out[:600], []
