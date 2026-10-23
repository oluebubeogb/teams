"""Shared domain logic: visibility, cards, topics, events, notifications, background jobs."""
from __future__ import annotations

import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from fastapi import HTTPException, Request
from psycopg.types.json import Jsonb

from app import ai, config
from app.auth import avatar_src
from app.db import q, q1, tx
from app.extract import IMAGE_TYPES, TEXT_TYPES, keywords, make_thumbnail, slugify, snippet
from app.storage import store

log = logging.getLogger("teams.core")
_executor = ThreadPoolExecutor(max_workers=max(1, config.WORKER_THREADS), thread_name_prefix="teams-worker")

# ---------------------------------------------------------------- visibility
# Use with params dict containing "uid" (0 for anonymous).
VIS = """((f.hidden = false OR f.user_id = %(uid)s) AND (
    f.visibility = 'public' OR f.user_id = %(uid)s
    OR EXISTS (SELECT 1 FROM file_collaborators fc WHERE fc.file_id = f.id AND fc.user_id = %(uid)s)
    OR (f.visibility = 'org' AND EXISTS (SELECT 1 FROM org_members m WHERE m.org_id = f.org_id
                                          AND m.user_id = %(uid)s AND m.status = 'active'))))"""

FILE_SELECT = """SELECT f.id, f.user_id, f.org_id, f.visibility, f.link_token, f.title, f.description, f.file_type,
  f.original_name, f.url, f.size_bytes, f.page_count, f.thumb_key, f.thumb_status, f.tags, f.ai_summary, f.version,
  f.hidden, f.allow_comments, f.allow_download, f.view_count, f.download_count, f.share_count, f.like_count,
  f.comment_count, f.save_count, f.created_at, f.updated_at, left(f.extracted_text, 420) AS text_head,
  u.username AS a_username, u.full_name AS a_name, u.avatar_url AS a_avatar, u.avatar_color AS a_color,
  o.slug AS o_slug, o.name AS o_name, o.avatar_key AS o_avatar, o.verified AS o_verified, o.accent AS o_accent
FROM files f JOIN users u ON u.id = f.user_id LEFT JOIN orgs o ON o.id = f.org_id"""


def uid_of(user: Optional[dict]) -> int:
    return int(user["id"]) if user else 0


def is_site_admin(user: Optional[dict]) -> bool:
    return bool(user and (user.get("is_admin") or user["username"].lower() in config.ADMIN_USERNAMES))


def org_role(org_id: Optional[int], user: Optional[dict]) -> Optional[str]:
    if not org_id or not user:
        return None
    r = q1("SELECT role FROM org_members WHERE org_id=%s AND user_id=%s AND status='active'", (org_id, user["id"]))
    return r["role"] if r else None


ROLE_RANK = {"member": 1, "editor": 2, "admin": 3, "owner": 4}


def has_role(org_id, user, minimum: str) -> bool:
    r = org_role(org_id, user)
    return bool(r and ROLE_RANK[r] >= ROLE_RANK[minimum])


def can_view(f: dict, user: Optional[dict], key: Optional[str] = None) -> bool:
    uid = uid_of(user)
    if f["user_id"] == uid or is_site_admin(user):
        return True
    if f["hidden"]:
        return False
    if f["visibility"] == "public":
        return True
    if f["visibility"] == "link" and key and key == f["link_token"]:
        return True
    if uid and q1("SELECT 1 FROM file_collaborators WHERE file_id=%s AND user_id=%s", (f["id"], uid)):
        return True
    if f["visibility"] == "org" and uid and org_role(f["org_id"], user):
        return True
    return False


def can_edit(f: dict, user: Optional[dict]) -> bool:
    if not user:
        return False
    if f["user_id"] == user["id"]:
        return True
    # org editors can edit files posted directly to their org
    return bool(f.get("org_id") and has_role(f["org_id"], user, "editor"))


def get_file(file_id: int) -> Optional[dict]:
    return q1(FILE_SELECT + " WHERE f.id=%s", (file_id,))


def file_or_404(file_id: int, user, key: Optional[str] = None) -> dict:
    f = get_file(file_id)
    if not f or not can_view(f, user, key):
        raise HTTPException(404, "File not found")
    return f


# ---------------------------------------------------------------- serialization
def initials(name: str) -> str:
    parts = (name or "").strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return (name or "U")[:2].upper()


def iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def rel(dt) -> str:
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    s = (now - dt).total_seconds()
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)}m"
    if s < 86400:
        return f"{int(s // 3600)}h"
    if s < 86400 * 7:
        return f"{int(s // 86400)}d"
    if dt.year == now.year:
        return dt.strftime("%b %-d")
    return dt.strftime("%b %-d, %Y")


def user_mini(row: dict, prefix: str = "") -> dict:
    g = lambda k: row.get(prefix + k)  # noqa: E731
    name = g("full_name") if g("full_name") is not None else g("name")
    return {
        "username": g("username"), "name": name or g("username"),
        "avatar": avatar_src({"avatar_url": g("avatar_url") if g("avatar_url") is not None else g("avatar")}),
        "color": g("avatar_color") or g("color") or "#5C5DE2", "initials": initials(name or g("username") or "U"),
    }


def org_avatar_url(slug: str, key: Optional[str]) -> Optional[str]:
    return f"/media/org/{slug}/avatar.webp?v={hashlib.md5(key.encode()).hexdigest()[:6]}" if key else None


def org_cover_url(slug: str, key: Optional[str]) -> Optional[str]:
    return f"/media/org/{slug}/cover.webp?v={hashlib.md5(key.encode()).hexdigest()[:6]}" if key else None


def org_verified(row: dict, slug_key="slug", flag_key="verified") -> bool:
    return bool(row.get(flag_key)) or (row.get(slug_key) or "").lower() in config.VERIFIED_ORGS


def thumb_url(f: dict) -> Optional[str]:
    if f.get("thumb_key"):
        return f"/thumb/{f['id']}.webp?v={f['version']}"
    return None


def card(f: dict, extra: dict | None = None) -> dict:
    title = (f.get("title") or "").strip() or f.get("original_name") or "Untitled"
    c = {
        "id": f["id"], "title": title, "description": (f.get("description") or "")[:280],
        "type": f["file_type"], "visibility": f["visibility"], "thumb": thumb_url(f),
        "thumb_status": f.get("thumb_status"),
        "text": snippet(f.get("text_head") or "", 300) if (f["file_type"] in TEXT_TYPES or not f.get("thumb_key")) else "",
        "pages": f.get("page_count"), "size": f.get("size_bytes") or 0,
        "created_at": iso(f["created_at"]), "ago": rel(f["created_at"]),
        "counts": {k: f.get(f"{k}_count") or 0 for k in ("view", "download", "share", "like", "comment", "save")},
        "author": {"username": f["a_username"], "name": f["a_name"] or f["a_username"],
                   "avatar": avatar_src({"avatar_url": f.get("a_avatar")}), "color": f.get("a_color") or "#5C5DE2",
                   "initials": initials(f["a_name"] or f["a_username"])},
        "org": ({"slug": f["o_slug"], "name": f["o_name"], "avatar": org_avatar_url(f["o_slug"], f.get("o_avatar")),
                 "verified": org_verified({"slug": f["o_slug"], "verified": f.get("o_verified")}),
                 "initials": initials(f["o_name"])} if f.get("o_slug") else None),
        "tags": list(f.get("tags") or [])[:6], "summary": f.get("ai_summary"),
        "hidden": f.get("hidden", False), "url": f.get("url") if f["file_type"] == "url" else None,
    }
    if extra:
        c.update(extra)
    return c


def cards(rows: list[dict], user: Optional[dict]) -> list[dict]:
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    topics = {}
    for t in q("""SELECT ft.file_id, t.slug, t.name FROM file_topics ft JOIN topics t ON t.id=ft.topic_id
                  WHERE ft.file_id = ANY(%s) ORDER BY ft.weight DESC, t.name""", (ids,)):
        topics.setdefault(t["file_id"], []).append({"slug": t["slug"], "name": t["name"]})
    liked, saved = set(), set()
    if user:
        liked = {r["file_id"] for r in q("SELECT file_id FROM likes WHERE user_id=%s AND file_id = ANY(%s)", (user["id"], ids))}
        saved = {r["file_id"] for r in q("SELECT file_id FROM saves WHERE user_id=%s AND file_id = ANY(%s)", (user["id"], ids))}
    out = []
    for r in rows:
        c = card(r)
        c["topics"] = topics.get(r["id"], [])[:3]
        c["liked"] = r["id"] in liked
        c["saved"] = r["id"] in saved
        c["mine"] = bool(user and r["user_id"] == user["id"])
        if r.get("headline"):
            c["headline"] = r["headline"]
        if r.get("score") is not None and isinstance(r.get("score"), (int, float)):
            c["score"] = round(float(r["score"]), 3)
        out.append(c)
    return out


# ---------------------------------------------------------------- topics
def set_topics(c, file_id: int, user_tags: Iterable[str] = (), auto: Iterable[str] = (), ai_topics: Iterable[str] = (),
               replace_user: bool = True):
    def upsert(name: str) -> Optional[int]:
        name = re.sub(r"\s+", " ", name.strip())[:40]
        slug = slugify(name)
        if len(slug) < 2:
            return None
        row = c.execute("""INSERT INTO topics(slug,name) VALUES (%s,%s) ON CONFLICT (slug) DO UPDATE SET slug=EXCLUDED.slug
                           RETURNING id""", (slug, name.title() if name.islower() else name)).fetchone()
        return row["id"]

    sources = []
    if replace_user:
        sources.append(("user", list(user_tags), 3.0))
    if auto:
        sources.append(("auto", list(auto), 1.0))
    if ai_topics:
        sources.append(("ai", list(ai_topics), 2.0))
    for src, names, w in sources:
        c.execute("DELETE FROM file_topics WHERE file_id=%s AND source=%s", (file_id, src))
        for i, n in enumerate(names[:8]):
            tid = upsert(n)
            if tid:
                c.execute("""INSERT INTO file_topics(file_id,topic_id,source,weight) VALUES (%s,%s,%s,%s)
                             ON CONFLICT (file_id,topic_id) DO UPDATE SET weight=GREATEST(file_topics.weight, EXCLUDED.weight)""",
                          (file_id, tid, src, w - i * 0.05))


def parse_tags(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items = raw
    else:
        items = re.split(r"[,#\n]", str(raw))
    out, seen = [], set()
    for t in items:
        t = re.sub(r"\s+", " ", str(t)).strip().lower()[:40]
        if len(t) >= 2 and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:12]


# ---------------------------------------------------------------- events & notifications
SOURCES = {"feed", "search", "direct", "org", "profile", "topic", "top", "embed", "collection", "notification",
           "library", "related", "viewer"}
SHARE_CHANNELS = {"whatsapp", "x", "linkedin", "telegram", "facebook", "copy", "native", "email", "embed"}


def norm_source(src: Optional[str]) -> str:
    s = (src or "direct").lower().strip()[:40]
    if s.startswith("share:") and s[6:] in SHARE_CHANNELS:
        return s
    if s in SHARE_CHANNELS:
        return "share:" + s
    return s if s in SOURCES else "direct"


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "0.0.0.0")


def visitor_id(request: Request, user: Optional[dict]) -> str:
    if user:
        return f"u:{user['id']}"
    raw = f"{client_ip(request)}|{request.headers.get('user-agent','')[:120]}|{config.SECRET_KEY}"
    return "ip:" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def record_event(file_id: int, kind: str, request: Request, user: Optional[dict], source: Optional[str] = None,
                 channel: Optional[str] = None) -> bool:
    """Insert a deduped event (one per visitor per file per kind per day). Returns True if it counted."""
    visitor = visitor_id(request, user)
    src = norm_source(source)
    if kind == "share":
        ch = (channel or "copy").lower()
        ch = ch if ch in SHARE_CHANNELS else "copy"
        visitor += ":" + ch
        src = "share:" + ch
    with tx() as c:
        row = c.execute("""INSERT INTO events(file_id,kind,user_id,visitor,source) VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT (file_id,kind,day,visitor) DO NOTHING RETURNING id""",
                        (file_id, kind, user["id"] if user else None, visitor, src)).fetchone()
        if row:
            col = {"view": "view_count", "download": "download_count", "share": "share_count"}[kind]
            c.execute(f"UPDATE files SET {col} = {col} + 1 WHERE id=%s", (file_id,))
            return True
    return False


def notify(c, user_id: int, kind: str, actor_id: Optional[int], file_id=None, org_id=None, comment_id=None, data=None):
    if not user_id or user_id == actor_id:
        return
    # collapse repeated likes from the same actor
    if kind in ("like", "follow") and c.execute(
            """SELECT 1 FROM notifications WHERE user_id=%s AND kind=%s AND actor_id=%s AND file_id IS NOT DISTINCT FROM %s
               AND created_at > now() - interval '1 day'""", (user_id, kind, actor_id, file_id)).fetchone():
        return
    c.execute("""INSERT INTO notifications(user_id,kind,actor_id,file_id,org_id,comment_id,data)
                 VALUES (%s,%s,%s,%s,%s,%s,%s)""", (user_id, kind, actor_id, file_id, org_id, comment_id, Jsonb(data or {})))


# ---------------------------------------------------------------- pagination
import base64


def enc_cursor(*parts) -> str:
    return base64.urlsafe_b64encode("|".join(str(p) for p in parts).encode()).decode().rstrip("=")


def dec_cursor(cur: Optional[str]) -> list[str]:
    if not cur:
        return []
    try:
        pad = "=" * (-len(cur) % 4)
        return base64.urlsafe_b64decode(cur + pad).decode().split("|")
    except Exception:
        return []


# ---------------------------------------------------------------- background processing
def _process(file_id: int):
    f = q1("SELECT id, file_type, storage_key, version, title, description, extracted_text, tags FROM files WHERE id=%s",
           (file_id,))
    if not f:
        return
    st = store()
    thumb_key, preview_key = None, None
    status = "none"
    try:
        if config.THUMBNAILS and f["storage_key"] and (f["file_type"] in IMAGE_TYPES or f["file_type"] in ("pdf", "docx", "pptx", "xlsx", "xls")):
            path = st.local_path(f["storage_key"])
            if path:
                data, pdf = make_thumbnail(Path(path), f["file_type"])
                if data:
                    thumb_key = f"thumbs/{f['id']}-v{f['version']}.webp"
                    st.put_bytes(thumb_key, data, "image/webp")
                    status = "ready"
                else:
                    status = "failed"
                if pdf and f["file_type"] != "pdf":
                    try:
                        from pypdf import PdfReader
                        pages = len(PdfReader(str(pdf)).pages)
                        with tx() as c:
                            c.execute("UPDATE files SET page_count=COALESCE(page_count, %s) WHERE id=%s", (pages, file_id))
                    except Exception:
                        pass
                if pdf:
                    preview_key = f"previews/{f['id']}-v{f['version']}.pdf"
                    st.put_file(preview_key, pdf, "application/pdf")
                    pdf.unlink(missing_ok=True)
    except Exception as e:
        log.warning("thumbnail failed for %s: %s", file_id, e)
        status = "failed"
    summary, ai_topics = None, []
    if ai.enabled() and config.AI_AUTO_TAG:
        try:
            summary, ai_topics = ai.summarize_and_tag(f["title"], f["extracted_text"])
        except Exception as e:
            log.warning("ai failed: %s", e)
    with tx() as c:
        c.execute("""UPDATE files SET thumb_key=COALESCE(%s,thumb_key), thumb_status=%s,
                     ai_summary=COALESCE(%s, ai_summary) WHERE id=%s""", (thumb_key, status, summary, file_id))
        if ai_topics:
            set_topics(c, file_id, ai_topics=ai_topics, replace_user=False)


def queue_processing(file_id: int):
    _executor.submit(_safe, file_id)


def _safe(file_id):
    try:
        _process(file_id)
    except Exception as e:
        log.exception("process %s failed: %s", file_id, e)


def queue_pending():
    for r in q("SELECT id FROM files WHERE thumb_status='pending' AND file_type<>'url' ORDER BY id DESC LIMIT 500"):
        queue_processing(r["id"])


def index_text(c, file_id: int, title: str, description: str, text: str, tags: list[str]):
    auto = keywords(title, description, text)
    set_topics(c, file_id, user_tags=tags, auto=auto)
