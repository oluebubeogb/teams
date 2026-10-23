#!/usr/bin/env python3
"""Migrate a Teams v1 SQLite database (teams.db) + uploads folder into Teams v2 (PostgreSQL + storage backend).

    DATABASE_URL=postgresql://... STORAGE_BACKEND=local DATA_DIR=/app/data \
    python scripts/migrate_sqlite.py --sqlite /old/data/teams.db --uploads /old/app/uploads [--dry-run]

Safe to re-run: users are matched by username/email, files by (owner, original_name, created_at).
All v1 files become visibility=public (v1 had no privacy), keep their view/download counts,
and are queued for thumbnails/topics the next time the app boots.
Run with the app stopped (or at least before users start uploading).
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", required=True, help="path to v1 teams.db")
    ap.add_argument("--uploads", action="append", default=[], help="v1 uploads dir (repeatable); used when storage_path is stale")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    from app import db
    from app.core import index_text
    from app.storage import store

    src = sqlite3.connect(a.sqlite)
    src.row_factory = sqlite3.Row
    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    db.run_migrations()

    def find(row) -> Path | None:
        cands = []
        if row["storage_path"]:
            cands.append(Path(row["storage_path"]))
        for d in a.uploads:
            for name in filter(None, [row["filename"], Path(row["storage_path"] or "").name]):
                cands.append(Path(d) / name)
        return next((p for p in cands if p.is_file()), None)

    umap: dict[int, int] = {}
    stats = dict(users_new=0, users_matched=0, files_new=0, files_skipped=0, missing_blobs=0, collaborators=0)
    with db.tx() as c:
        for u in src.execute("SELECT * FROM users ORDER BY id"):
            keys = u.keys()
            ex = c.execute("SELECT id FROM users WHERE lower(username)=lower(%s) OR lower(email)=lower(%s)", (u["username"], u["email"])).fetchone()
            if ex:
                umap[u["id"]] = ex["id"]; stats["users_matched"] += 1
                continue
            r = c.execute("""INSERT INTO users(accounts_id,username,full_name,email,phone,bio,password_hash,created_at)
                             VALUES (%s,%s,%s,%s,%s,%s,%s,COALESCE(%s::timestamptz, now())) RETURNING id""",
                          (u["accounts_id"] if "accounts_id" in keys else None, u["username"], u["full_name"] or u["username"],
                           u["email"].lower(), u["phone"] or None, u["bio"] or "", u["password_hash"] or "", u["created_at"])).fetchone()
            umap[u["id"]] = r["id"]; stats["users_new"] += 1

        fmap: dict[int, int] = {}
        for f in src.execute("SELECT * FROM files ORDER BY id"):
            uid = umap.get(f["user_id"])
            if not uid:
                continue
            ex = c.execute("SELECT id FROM files WHERE user_id=%s AND original_name=%s AND created_at=COALESCE(%s::timestamptz, created_at)",
                           (uid, f["original_name"], f["created_at"])).fetchone()
            if ex:
                fmap[f["id"]] = ex["id"]; stats["files_skipped"] += 1
                continue
            ftype = (f["file_type"] or "file").lower()
            key, size = None, 0
            if ftype != "url":
                p = find(f)
                if p:
                    size = p.stat().st_size
                    key = f"files/{uuid.uuid4().hex}{p.suffix.lower() or '.' + ftype}"
                    if not a.dry_run:
                        store().put_file(key, p, mimetypes.guess_type(p.name)[0] or "application/octet-stream")
                else:
                    stats["missing_blobs"] += 1
                    print(f"  ! blob missing for v1 file {f['id']} ({f['original_name']})", file=sys.stderr)
            title = (f["title"] if "title" in f.keys() else "") or Path(f["original_name"]).stem.replace("_", " ")
            text = f["extracted_text"] or ""
            r = c.execute("""INSERT INTO files(user_id,visibility,filename,original_name,file_type,storage_key,url,size_bytes,title,
                             description,extracted_text,view_count,download_count,thumb_status,created_at,updated_at)
                             VALUES (%s,'public',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s::timestamptz, now()),now()) RETURNING id""",
                          (uid, Path(key).name if key else f["filename"], f["original_name"], ftype, key, f["url"], size, title[:200],
                           f["description"] or "", text, f["view_count"] or 0, f["download_count"] or 0,
                           "none" if ftype == "url" or not key else "pending", f["created_at"])).fetchone()
            fid = r["id"]; fmap[f["id"]] = fid; stats["files_new"] += 1
            if key:
                c.execute("""INSERT INTO file_versions(file_id,version,storage_key,original_name,file_type,size_bytes,created_by,note)
                             VALUES (%s,1,%s,%s,%s,%s,%s,'Migrated from Teams v1')""", (fid, key, f["original_name"], ftype, size, uid))
            index_text(c, fid, title, f["description"] or "", text, [])

        if "file_collaborators" in tables:
            for fc in src.execute("SELECT * FROM file_collaborators"):
                if fc["file_id"] in fmap:
                    c.execute("""INSERT INTO file_collaborators(file_id,email,user_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                              (fmap[fc["file_id"]], fc["email"].lower(), umap.get(fc["user_id"])))
                    stats["collaborators"] += 1
        for t in ("users", "files", "file_collaborators"):
            c.execute(f"SELECT setval(pg_get_serial_sequence('{t}','id'), GREATEST((SELECT COALESCE(max(id),1) FROM {t}),1))")
        if a.dry_run:
            c.rollback()
            print("dry run — rolled back:", stats)
            return
    print(stats)
    print("Done. Start the app: pending thumbnails and page previews are generated in the background on boot.")


if __name__ == "__main__":
    try:
        main()
    finally:
        from app import db as _db
        _db.close_pool()
