"""AI titles & descriptions for files uploaded before the feature existed (or while AI was off).

Safe to rerun: every file carries ai_meta_status, finished ones are skipped, originals are kept in
original_title / original_description (first value only), and rows are claimed with SKIP LOCKED so the
boot thread and the CLI (scripts/ai_backfill_meta.py) never process the same file twice.

Status: NULL = never tried (candidate) | pending/running = in progress | done | skipped (no usable text)
        failed (retried up to MAX_ATTEMPTS) | off (uploader turned AI off) | edited (a person changed it; never touched)
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from app import ai, config
from app.core import AI_MIN_TEXT, ai_enrich, is_default_title, regen_thumbs
from app.extract import THUMB_REV
from app.db import ex, q, q1

log = logging.getLogger("teams.backfill")
MAX_ATTEMPTS = 3


def _where(force: bool) -> str:
    if force:  # redo AI ones too, but never files whose owner opted out or edited by hand
        st = "(ai_meta_status IS NULL OR ai_meta_status IN ('done','failed','skipped'))"
    else:
        st = f"(ai_meta_status IS NULL OR (ai_meta_status='failed' AND ai_meta_attempts < {MAX_ATTEMPTS}))"
    return f"file_type <> 'url' AND {st} AND length(coalesce(extracted_text,'')) >= {AI_MIN_TEXT}"


def candidates(force=False, only_default=False, limit: int = 0) -> list[dict]:
    rows = q(f"""SELECT id, title, original_name, description, ai_meta_status FROM files WHERE {_where(force)}
                 ORDER BY id DESC""")
    if only_default:
        rows = [r for r in rows if is_default_title(r["title"], r["original_name"])]
    return rows[:limit] if limit else rows


def _claim(fid: int, force: bool) -> bool:
    ok_status = "ai_meta_status IS NULL OR ai_meta_status IN ('done','failed','skipped')" if force else \
        f"ai_meta_status IS NULL OR (ai_meta_status='failed' AND ai_meta_attempts < {MAX_ATTEMPTS})"
    r = q1(f"""UPDATE files SET ai_meta_status='running', ai_meta_at=now()
               WHERE id = (SELECT id FROM files WHERE id=%s AND ({ok_status}) FOR UPDATE SKIP LOCKED)
               RETURNING id""", (fid,))
    return bool(r)


def mark_skipped_without_text():
    """Files with no usable text can never get an AI title; mark them so they stop showing up as candidates."""
    ex(f"""UPDATE files SET ai_meta_status='skipped', ai_meta_at=now()
           WHERE ai_meta_status IS NULL AND (file_type='url' OR length(coalesce(extracted_text,'')) < {AI_MIN_TEXT})""")


def run(force=False, only_default=False, limit: int = 0, delay: float = 0.0, dry_run=False,
        say: Callable[[str], None] = log.info, stop: Optional[threading.Event] = None) -> dict:
    stats = {"candidates": 0, "done": 0, "failed": 0, "skipped": 0, "busy": 0}
    if not dry_run:
        mark_skipped_without_text()
    rows = candidates(force, only_default, limit)
    stats["candidates"] = len(rows)
    if dry_run:
        for r in rows:
            say(f"would update #{r['id']}: {r['title']!r} ({r['ai_meta_status'] or 'new'})")
        return stats
    if not ai.enabled():
        say("AI is not configured (AI_PRIMARY_BASE_URL / AI_PRIMARY_MODEL); nothing to do")
        return stats
    for i, r in enumerate(rows):
        if stop is not None and stop.is_set():
            break
        if not _claim(r["id"], force):
            stats["busy"] += 1
            continue
        # A backfill replaces both fields (originals are kept); --only-default-titles keeps hand-written titles
        replace_title = (not only_default) or is_default_title(r["title"], r["original_name"])
        try:
            st = ai_enrich(r["id"], want_meta=True, replace_title=replace_title, replace_desc=True, apply_summary=True)
        except Exception as e:  # never leave a row stuck in 'running'
            log.warning("backfill #%s failed: %s", r["id"], e)
            ex("UPDATE files SET ai_meta_status='failed', ai_meta_attempts=ai_meta_attempts+1 WHERE id=%s AND ai_meta_status='running'",
               (r["id"],))
            st = "failed"
        stats[st if st in stats else "skipped"] += 1
        new = q1("SELECT title FROM files WHERE id=%s", (r["id"],))
        say(f"[{i + 1}/{len(rows)}] #{r['id']} {st}: {r['title']!r} -> {new['title']!r}")
        if delay and i + 1 < len(rows):
            (stop.wait(delay) if stop is not None else time.sleep(delay))
    return stats


def revert(limit: int = 0, say: Callable[[str], None] = log.info) -> int:
    rows = q("""SELECT id, title, original_title FROM files WHERE ai_meta AND original_title IS NOT NULL
                AND ai_meta_status IS DISTINCT FROM 'edited' ORDER BY id DESC""")
    rows = rows[:limit] if limit else rows
    from app.core import index_text
    from app.db import tx
    for r in rows:
        with tx() as c:
            row = c.execute("""UPDATE files SET title=original_title, description=original_description, ai_meta=false,
                                 ai_meta_status='off', original_title=NULL, original_description=NULL
                               WHERE id=%s RETURNING title, description, extracted_text, tags""", (r["id"],)).fetchone()
            index_text(c, r["id"], row["title"], row["description"], row["extracted_text"] or "", list(row["tags"] or []))
        say(f"reverted #{r['id']}: {r['title']!r} -> {r['original_title']!r}")
    return len(rows)


# ---------------------------------------------------------------- on boot
_stop = threading.Event()


def _boot_worker():
    if _stop.wait(max(0, config.AI_BACKFILL_START_DELAY_SECONDS)):
        return
    try:
        # rows left 'running' by a crash/redeploy go back to the queue
        ex("""UPDATE files SET ai_meta_status=NULL WHERE ai_meta_status='running'
              AND ai_meta_at < now() - interval '10 minutes' AND original_title IS NULL""")
        total = {"done": 0, "failed": 0}
        cap = config.AI_BACKFILL_MAX_PER_BOOT
        while not _stop.is_set():
            batch = config.AI_BACKFILL_BATCH if not cap else min(config.AI_BACKFILL_BATCH, cap - total["done"] - total["failed"])
            if batch <= 0:
                break
            s = run(only_default=config.AI_BACKFILL_ONLY_DEFAULT_TITLES, limit=batch,
                    delay=config.AI_BACKFILL_DELAY_SECONDS, stop=_stop, say=log.info)
            total["done"] += s["done"]; total["failed"] += s["failed"]
            if s["candidates"] < batch or (s["done"] == 0 and s["failed"] > 0 and s["candidates"]):
                if s["candidates"] >= batch:   # every call failing: provider down / out of credits; try next boot
                    log.warning("AI backfill paused: the AI provider is failing")
                break
            _stop.wait(config.AI_BACKFILL_DELAY_SECONDS)
        if total["done"] or total["failed"]:
            log.info("AI backfill finished: %s", total)
    except Exception as e:
        log.warning("AI backfill stopped: %s", e)


# ---------------------------------------------------------------- HD thumbnails for older files
def thumb_candidates(limit: int = 0, force: bool = False) -> list[dict]:
    cond = "thumb_key IS NOT NULL" + ("" if force else f" AND thumb_rev < {THUMB_REV}")
    return q(f"SELECT id, title, file_type, thumb_rev FROM files WHERE {cond} ORDER BY id DESC" + (f" LIMIT {int(limit)}" if limit else ""))


def run_thumbs(limit: int = 0, force: bool = False, delay: float = 0, stop: Optional[threading.Event] = None,
               say: Callable[[str], None] = log.info) -> dict:
    """Re-render thumbnails one at a time (newest first). Idempotent: done files get thumb_rev=THUMB_REV and drop out."""
    stats = {"done": 0, "failed": 0, "skipped": 0}
    for r in thumb_candidates(limit, force):
        if stop is not None and stop.is_set():
            break
        try:
            res = regen_thumbs(r["id"])
        except Exception as e:
            res = "failed"
            ex("UPDATE files SET thumb_rev=%s WHERE id=%s", (THUMB_REV, r["id"]))   # don't loop on a broken file
            log.warning("thumbnail backfill #%s failed: %s", r["id"], e)
        stats[res] += 1
        say(f"thumb #{r['id']} {r['file_type']}: {res}")
        if delay:
            (stop.wait(delay) if stop is not None else time.sleep(delay))
    return stats


def _thumb_worker():
    if _stop.wait(max(0, config.AI_BACKFILL_START_DELAY_SECONDS)):
        return
    try:
        s = run_thumbs(delay=config.THUMB_BACKFILL_DELAY_SECONDS, stop=_stop, say=log.debug)
        if s["done"] or s["failed"]:
            log.info("thumbnail backfill finished: %s", s)
    except Exception as e:
        log.warning("thumbnail backfill stopped: %s", e)


def start_on_boot():
    if config.THUMBNAILS and config.THUMB_BACKFILL_ON_BOOT:
        threading.Thread(target=_thumb_worker, name="thumb-backfill", daemon=True).start()
    if not config.AI_BACKFILL_ON_BOOT or not ai.enabled():
        return
    threading.Thread(target=_boot_worker, name="ai-backfill", daemon=True).start()


def stop():
    _stop.set()
