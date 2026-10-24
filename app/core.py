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
from app.extract import DECK_TYPES, IMAGE_TYPES, THUMB_REV, TEXT_TYPES, keywords, make_thumbs, slugify, snippet
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
  f.original_name, f.url, f.size_bytes, f.page_count, f.thumb_key, f.thumb_status, f.thumb_w, f.thumb_h, f.thumb_rev, f.tags, f.ai_summary, f.version,
  f.hidden, f.allow_comments, f.allow_download, f.view_count, f.download_count, f.share_count, f.like_count,
  f.comment_count, f.save_count, f.created_at, f.updated_at, left(f.extracted_text, 420) AS text_head,
  f.ai_meta, f.ai_meta_status,
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


def thumb_url(f: dict, small: bool = False) -> Optional[str]:
    if f.get("thumb_key"):
        rev = f.get("thumb_rev") or 1
        return f"/thumb/{f['id']}.webp?v={f['version']}-{rev}" + ("&s=1" if small and rev >= 2 else "")
    return None


def small_thumb_key(key: str) -> str:
    return key[:-5] + "-s.webp" if key.endswith(".webp") else key + "-s"


def _small_w(f: dict) -> Optional[int]:
    w, h = f.get("thumb_w"), f.get("thumb_h")
    if not (w and h) or (f.get("thumb_rev") or 1) < 2:
        return None
    return w if max(w, h) <= config.THUMB_SMALL_EDGE else round(w * config.THUMB_SMALL_EDGE / max(w, h))


def thumb_frame(f: dict) -> Optional[float]:
    """Aspect ratio (w/h) the card frame should reserve: 16:9 for slide decks, the real first-page shape for PDFs
    (A4/letter portrait or landscape, 16:9 exports...), None = keep the default card frame (docx, images, ...)."""
    if f["file_type"] in DECK_TYPES:
        return round(16 / 9, 4)
    if f["file_type"] == "pdf" and f.get("thumb_w") and f.get("thumb_h"):
        return round(max(0.5, min(2.4, f["thumb_w"] / f["thumb_h"])), 4)
    return None


def card(f: dict, extra: dict | None = None) -> dict:
    title = (f.get("title") or "").strip() or f.get("original_name") or "Untitled"
    c = {
        "id": f["id"], "title": title, "description": (f.get("description") or "")[:280],
        "type": f["file_type"], "visibility": f["visibility"], "thumb": thumb_url(f),
        "thumb_s": thumb_url(f, small=True), "thumb_w": f.get("thumb_w"), "thumb_h": f.get("thumb_h"),
        "thumb_sw": _small_w(f), "frame": thumb_frame(f),
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
        "ai_meta": bool(f.get("ai_meta")), "ai_pending": f.get("ai_meta_status") in ("pending", "running"),
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


# ---------------------------------------------------------------- AI titles & descriptions
def default_title(original_name: str) -> str:
    """The title the uploader gets when they don't type one (mirrors the upload UI)."""
    return re.sub(r"\s+", " ", re.sub(r"[_-]+", " ", Path(original_name or "").stem)).strip()


def is_default_title(title: Optional[str], original_name: Optional[str]) -> bool:
    norm = lambda x: re.sub(r"\s+", " ", re.sub(r"[_-]+", " ", x or "")).strip().lower()  # noqa: E731
    t = norm(title)
    return not t or t == norm(default_title(original_name or "")) or t == norm(original_name)


AI_MIN_TEXT = 40  # characters of extracted text needed to write a meaningful title


def apply_ai_meta(c, file_id: int, res: dict, replace_title: bool = True, replace_desc: bool = True) -> bool:
    """Write AI title/description, keeping the first originals so it's reversible. Returns True if anything changed."""
    t = (res or {}).get("title") if replace_title else None
    d = (res or {}).get("description") if replace_desc else None
    if not t and not d:
        c.execute("UPDATE files SET ai_meta_status='failed', ai_meta_at=now() WHERE id=%s AND ai_meta_status='running'", (file_id,))
        return False
    row = c.execute("""UPDATE files SET original_title = CASE WHEN ai_meta THEN original_title ELSE title END,
                         original_description = CASE WHEN ai_meta THEN original_description ELSE description END,
                         title = COALESCE(%s, title), description = COALESCE(%s, description),
                         ai_meta = true, ai_meta_status = 'done', ai_meta_at = now()
                       WHERE id=%s AND ai_meta_status='running' RETURNING title, description, tags, extracted_text""",
                    (t[: config.MAX_TITLE_LENGTH] if t else None, d, file_id)).fetchone()
    if not row:  # the uploader edited the title/description meanwhile: their words win
        return False
    index_text(c, file_id, row["title"], row["description"], row["extracted_text"] or "", list(row["tags"] or []))
    return True


def ai_enrich(file_id: int, want_meta: bool, replace_title: bool = True, replace_desc: bool = True,
              apply_summary: Optional[bool] = None) -> str:
    """One AI call -> summary + topics (+ title/description when want_meta). Returns the resulting ai_meta_status."""
    f = q1("SELECT id, title, description, original_name, file_type, extracted_text, ai_meta_status FROM files WHERE id=%s",
           (file_id,))
    if not f:
        return "missing"
    apply_summary = config.AI_AUTO_TAG if apply_summary is None else apply_summary
    text = (f["extracted_text"] or "").strip()
    if want_meta and (f["file_type"] == "url" or len(text) < AI_MIN_TEXT):
        want_meta = False
        with tx() as c:
            c.execute("UPDATE files SET ai_meta_status='skipped', ai_meta_at=now() WHERE id=%s AND ai_meta_status='running'", (file_id,))
    if not ai.enabled() or not (want_meta or apply_summary):
        if want_meta:  # leave it as a backfill candidate for when AI is configured
            with tx() as c:
                c.execute("UPDATE files SET ai_meta_status=NULL WHERE id=%s AND ai_meta_status IN ('pending','running')", (file_id,))
        return "disabled"
    try:
        res = ai.analyze(f["title"], text, want_meta=want_meta, filename=f["original_name"] or "")
    except Exception as e:
        log.warning("ai failed for %s: %s", file_id, e)
        res = None
    with tx() as c:
        if res and apply_summary and (res.get("summary") or res.get("topics")):
            c.execute("UPDATE files SET ai_summary=COALESCE(%s, ai_summary) WHERE id=%s", (res.get("summary"), file_id))
            if res.get("topics"):
                set_topics(c, file_id, ai_topics=res["topics"], replace_user=False)
        if not want_meta:
            return "skipped"
        if not res:
            c.execute("""UPDATE files SET ai_meta_status='failed', ai_meta_attempts=ai_meta_attempts+1, ai_meta_at=now()
                         WHERE id=%s AND ai_meta_status='running'""", (file_id,))
            return "failed"
        ok = apply_ai_meta(c, file_id, res, replace_title, replace_desc)
        if not ok:
            c.execute("UPDATE files SET ai_meta_attempts=ai_meta_attempts+1 WHERE id=%s AND ai_meta_status='failed'", (file_id,))
        return "done" if ok else "failed"


# ---------------------------------------------------------------- background processing
def _process(file_id: int):
    f = q1("""SELECT id, file_type, storage_key, version, title, description, original_name, extracted_text, tags, ai_meta_status
              FROM files WHERE id=%s""", (file_id,))
    if not f:
        return
    # AI first (needs only the extracted text) so the title/description land within seconds of the upload
    want_meta = f["ai_meta_status"] == "pending"
    if want_meta or (ai.enabled() and config.AI_AUTO_TAG):
        try:
            if want_meta:
                with tx() as c:
                    c.execute("UPDATE files SET ai_meta_status='running', ai_meta_at=now() WHERE id=%s", (file_id,))
            ai_enrich(file_id, want_meta, replace_title=is_default_title(f["title"], f["original_name"]),
                      replace_desc=not (f["description"] or "").strip())
        except Exception as e:
            log.warning("ai enrich failed for %s: %s", file_id, e)
    st = store()
    status = "none"
    try:
        if config.THUMBNAILS and f["storage_key"] and (f["file_type"] in IMAGE_TYPES or f["file_type"] in THUMBABLE):
            path = st.local_path(f["storage_key"])
            if path:
                ts, pdf = make_thumbs(Path(path), f["file_type"])
                status = "ready" if ts and save_thumbs(f, ts) else "failed"
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
    if status != "ready":
        with tx() as c:
            c.execute("UPDATE files SET thumb_status=%s WHERE id=%s", (status, file_id))


THUMBABLE = {"pdf", "docx", "pptx", "xlsx", "xls"}


def save_thumbs(f: dict, ts: dict) -> bool:
    """Store the large + small WebP and the page dimensions; drop the image it replaces (keeps storage lean)."""
    st = store()
    key = f"thumbs/{f['id']}-v{f['version']}-r{THUMB_REV}.webp"
    st.put_bytes(key, ts["large"], "image/webp")
    st.put_bytes(small_thumb_key(key), ts["small"], "image/webp")
    with tx() as c:
        old = c.execute("SELECT thumb_key, thumb_rev FROM files WHERE id=%s FOR UPDATE", (f["id"],)).fetchone()
        if not old:
            return False
        c.execute("""UPDATE files SET thumb_key=%s, thumb_w=%s, thumb_h=%s, thumb_rev=%s, thumb_status='ready' WHERE id=%s""",
                  (key, ts["w"], ts["h"], THUMB_REV, f["id"]))
    if old["thumb_key"] and old["thumb_key"] != key:
        for k in {old["thumb_key"], small_thumb_key(old["thumb_key"])} if (old["thumb_rev"] or 1) >= 2 else {old["thumb_key"]}:
            try:
                st.delete(k)
            except Exception:
                pass
    return True


def regen_thumbs(file_id: int) -> str:
    """Re-render one file's thumbnail at the current THUMB_REV. Office files reuse the stored preview PDF, so
    LibreOffice only runs when that preview is missing. Returns 'done' | 'skipped' | 'failed'."""
    f = q1("SELECT id, file_type, storage_key, version FROM files WHERE id=%s", (file_id,))
    if not f or not f["storage_key"] or not (f["file_type"] in IMAGE_TYPES or f["file_type"] in THUMBABLE):
        return "skipped"
    st = store()
    path = st.local_path(f["storage_key"])
    if not path:
        return "failed"
    preview = None
    if f["file_type"] in THUMBABLE and f["file_type"] != "pdf":
        preview = st.local_path(f"previews/{f['id']}-v{f['version']}.pdf")
    ts, pdf = make_thumbs(Path(path), f["file_type"], preview_pdf=Path(preview) if preview else None)
    if pdf:
        st.put_file(f"previews/{f['id']}-v{f['version']}.pdf", pdf, "application/pdf")
        pdf.unlink(missing_ok=True)
    if not ts:
        with tx() as c:   # keep the old image, just stop retrying it
            c.execute("UPDATE files SET thumb_rev=%s WHERE id=%s", (THUMB_REV, file_id))
        return "failed"
    return "done" if save_thumbs(f, ts) else "skipped"


def queue_processing(file_id: int):
    _executor.submit(_safe, file_id)


def _safe(file_id):
    try:
        _process(file_id)
    except Exception as e:
        log.exception("process %s failed: %s", file_id, e)


def queue_pending():
    # uploads interrupted by a restart: put AI work back to 'pending' so _process picks it up again
    from app.db import ex
    ex("UPDATE files SET ai_meta_status='pending' WHERE ai_meta_status='running' AND (ai_meta_at IS NULL OR ai_meta_at < now() - interval '10 minutes')")
    for r in q("""SELECT id FROM files WHERE (thumb_status='pending' AND file_type<>'url') OR ai_meta_status='pending'
                  ORDER BY id DESC LIMIT 500"""):
        queue_processing(r["id"])


def index_text(c, file_id: int, title: str, description: str, text: str, tags: list[str]):
    auto = keywords(title, description, text)
    set_topics(c, file_id, user_tags=tags, auto=auto)
