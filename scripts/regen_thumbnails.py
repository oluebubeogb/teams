#!/usr/bin/env python3
"""Re-render thumbnails as sharp, full first pages (WebP, long edge THUMB_EDGE + a small variant) and record
their dimensions so cards reserve the right aspect ratio. Runs automatically on boot (THUMB_BACKFILL_ON_BOOT);
use this to run it by hand. Safe to rerun: finished files are skipped unless --force.

    docker compose exec app python scripts/regen_thumbnails.py --dry-run
    docker compose exec app python scripts/regen_thumbnails.py --limit 50 --delay 1
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.backfill import run_thumbs, thumb_candidates  # noqa: E402
from app.db import close_pool, run_migrations  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="list the files that would be regenerated")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="also redo files already at the current version")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between files")
    a = ap.parse_args()
    run_migrations()
    try:
        if a.dry_run:
            rows = thumb_candidates(a.limit, a.force)
            for r in rows:
                print(f"#{r['id']:<6} {r['file_type']:<5} rev {r['thumb_rev']}  {r['title']}")
            print(f"{len(rows)} file(s) would be regenerated")
            return
        print(run_thumbs(limit=a.limit, force=a.force, delay=a.delay, say=print))
    finally:
        close_pool()


if __name__ == "__main__":
    main()
