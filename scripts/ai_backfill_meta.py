#!/usr/bin/env python3
"""AI titles & descriptions for older files (same env as the app: DATABASE_URL + AI_* vars).

    python scripts/ai_backfill_meta.py --dry-run              # list what would change
    python scripts/ai_backfill_meta.py                        # do it (resumable; finished files are skipped)
    python scripts/ai_backfill_meta.py --limit 50             # at most 50 files this run
    python scripts/ai_backfill_meta.py --only-default-titles  # only files still titled after their filename
    python scripts/ai_backfill_meta.py --force                # redo files AI already handled (never hand-edited/opted-out ones)
    python scripts/ai_backfill_meta.py --revert               # restore the saved original titles/descriptions

In Docker:  docker compose exec app python scripts/ai_backfill_meta.py --dry-run
The app also runs this automatically in the background on boot (AI_BACKFILL_ON_BOOT, throttled).
Original title/description are stored once in original_title / original_description, so reruns are safe.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ai, backfill, config  # noqa: E402
from app.db import close_pool, run_migrations  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show candidates, change nothing, no AI calls")
    ap.add_argument("--limit", type=int, default=0, help="max files to process (0 = all)")
    ap.add_argument("--force", action="store_true", help="also redo files that already have AI titles")
    ap.add_argument("--only-default-titles", action="store_true", help="only touch titles still derived from the filename")
    ap.add_argument("--delay", type=float, default=None, help=f"seconds between files (default AI_BACKFILL_DELAY_SECONDS={config.AI_BACKFILL_DELAY_SECONDS})")
    ap.add_argument("--revert", action="store_true", help="restore original titles/descriptions saved by earlier runs")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    run_migrations()
    try:
        if a.revert:
            n = backfill.revert(limit=a.limit, say=print)
            print(f"reverted {n} file(s)")
            return 0
        if not a.dry_run and not ai.enabled():
            print("AI is not configured: set AI_PRIMARY_BASE_URL and AI_PRIMARY_MODEL (and AI_PRIMARY_API_KEY).", file=sys.stderr)
            return 2
        s = backfill.run(force=a.force, only_default=a.only_default_titles, limit=a.limit, dry_run=a.dry_run,
                         delay=config.AI_BACKFILL_DELAY_SECONDS if a.delay is None else a.delay, say=print)
        if a.dry_run:
            print(f"{s['candidates']} file(s) would be updated (dry run, nothing changed)")
        else:
            print(f"done={s['done']} failed={s['failed']} skipped={s['skipped']} busy={s['busy']} of {s['candidates']}")
        return 0
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
