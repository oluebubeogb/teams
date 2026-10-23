"""Files: upload, edit, versions, preview, download, thumbnails, views, shares, bulk actions."""
from __future__ import annotations

import mimetypes
import re
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app import config
from app.auth import get_current_user, get_user_by_email, require_user
from app.core import (FILE_SELECT, VIS, can_edit, can_view, card, cards, file_or_404, get_file, has_role, index_text, notify,
                      parse_tags, queue_processing, record_event, uid_of)
from app.db import q, q1, tx
from app.extract import extract_text, office_to_pdf
from app.storage import store

router = APIRouter()

MIME = {
    "pdf": "application/pdf", "csv": "text/csv; charset=utf-8", "txt": "text/plain; charset=utf-8",
    "md": "text/plain; charset=utf-8", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xls": "application/vnd.ms-excel",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
OFFICE = {"pptx", "docx", "xlsx", "xls"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\r\n]+', "-", s).strip()[:180] or "file"


def resolve_org_for_post(slug: Optional[str], user: dict) -> Optional[int]:
    if not slug:
        return None
    org = q1("SELECT id FROM orgs WHERE slug=%s", (slug.strip().lower(),))
    if not org:
        raise HTTPException(400, "Organization not found")
    if not has_role(org["id"], user, "member"):
        raise HTTPException(403, "You are not a member of that organization")
    return org["id"]


def set_collaborators(c, file_id: int, raw: Optional[str], actor: dict, file_title: str = ""):
    entries = [e.strip() for e in re.split(r"[,\s;]+", raw or "") if e.strip()]
    emails = []
    for e in entries:
        e = e.lower().lstrip("@")
        if EMAIL_RE.match(e):
            emails.append(e)
        else:
            u = c.execute("SELECT email FROM users WHERE lower(username)=%s", (e,)).fetchone()
            if u:
                emails.append(u["email"])
    emails = list(dict.fromkeys(emails))[:50]
    before = {r["email"] for r in c.execute("SELECT email FROM file_collaborators WHERE file_id=%s", (file_id,)).fetchall()}
    c.execute("DELETE FROM file_collaborators WHERE file_id=%s AND NOT (email = ANY(%s))", (file_id, emails))
    for e in emails:
        u = c.execute("SELECT id FROM users WHERE email=%s", (e,)).fetchone()
        c.execute("""INSERT INTO file_collaborators(file_id,email,user_id) VALUES (%s,%s,%s)
                     ON CONFLICT (file_id,email) DO UPDATE SET user_id=COALESCE(EXCLUDED.user_id, file_collaborators.user_id)""",
                  (file_id, e, u["id"] if u else None))
        if u and e not in before:
            notify(c, u["id"], "collab_added", actor["id"], file_id=file_id)


async def _save_upload(file: UploadFile) -> tuple[Path, str, str, int]:
    original = file.filename or "file"
    ext = Path(original).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported type {ext or '?'}. Allowed: {', '.join(sorted(config.ALLOWED_EXTENSIONS))}")
    ftype = "jpg" if ext == ".jpeg" else ext.lstrip(".")
    tmp = Path(tempfile.mkstemp(prefix="teams-up-", suffix=ext)[1])
    size, limit = 0, config.MAX_UPLOAD_MB * 1024 * 1024
    with open(tmp, "wb") as out:
        while chunk := await file.read(1024 * 512):
            size += len(chunk)
            if size > limit:
                out.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, f"File too large (max {config.MAX_UPLOAD_MB} MB)")
            out.write(chunk)
    return tmp, original, ftype, size


@router.post("/api/files")
async def upload(request: Request, file: Optional[UploadFile] = File(None), url: Optional[str] = Form(None),
                 title: str = Form(""), description: str = Form(""), tags: str = Form(""),
                 visibility: str = Form("public"), org: str = Form(""), collaborators: str = Form(""),
                 allow_comments: bool = Form(True), allow_download: bool = Form(True), user=Depends(require_user)):
    if visibility not in ("public", "org", "private", "link"):
        raise HTTPException(400, "Bad visibility")
    org_id = resolve_org_for_post(org, user)
    if visibility == "org" and not org_id:
        raise HTTPException(400, "Pick an organization for org-only files")
    tag_list = parse_tags(tags)
    description = (description or "").strip()[: config.MAX_DESCRIPTION_LENGTH]

    if url and url.strip() and not file:
        u = url.strip()
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        t = (title or u).strip()[: config.MAX_TITLE_LENGTH]
        with tx() as c:
            fid = c.execute("""INSERT INTO files(user_id,org_id,visibility,filename,original_name,file_type,url,title,description,
                               tags,extracted_text,thumb_status,allow_comments,allow_download)
                               VALUES (%s,%s,%s,%s,%s,'url',%s,%s,%s,%s,%s,'none',%s,%s) RETURNING id""",
                            (user["id"], org_id, visibility, u[:200], u[:200], u, t, description, tag_list, u,
                             allow_comments, allow_download)).fetchone()["id"]
            set_collaborators(c, fid, collaborators, user)
            index_text(c, fid, t, description, "", tag_list)
        return {"ok": True, "file": cards([get_file(fid)], user)[0]}

    if not file:
        raise HTTPException(400, "Choose a file or paste a link")
    tmp, original, ftype, size = await _save_upload(file)
    try:
        text, pages = extract_text(tmp, ftype)
        key = f"files/{uuid.uuid4().hex}.{ftype}"
        store().put_file(key, tmp, MIME.get(ftype, "application/octet-stream"))
    finally:
        tmp.unlink(missing_ok=True)
    t = (title or Path(original).stem.replace("_", " ").replace("-", " ")).strip()[: config.MAX_TITLE_LENGTH]
    with tx() as c:
        fid = c.execute("""INSERT INTO files(user_id,org_id,visibility,filename,original_name,file_type,storage_key,size_bytes,
                           page_count,title,description,tags,extracted_text,allow_comments,allow_download)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (user["id"], org_id, visibility, Path(key).name, original, ftype, key, size, pages, t, description,
                         tag_list, text, allow_comments, allow_download)).fetchone()["id"]
        c.execute("""INSERT INTO file_versions(file_id,version,storage_key,original_name,file_type,size_bytes,created_by,note)
                     VALUES (%s,1,%s,%s,%s,%s,%s,'Original upload')""", (fid, key, original, ftype, size, user["id"]))
        set_collaborators(c, fid, collaborators, user)
        index_text(c, fid, t, description, text, tag_list)
        if org_id and visibility in ("public", "org"):
            # posting straight to an org = the uploader's consent; notify org admins
            for a in c.execute("SELECT user_id FROM org_members WHERE org_id=%s AND role IN ('owner','admin') AND status='active'",
                               (org_id,)).fetchall():
                notify(c, a["user_id"], "org_post", user["id"], file_id=fid, org_id=org_id)
    queue_processing(fid)
    return {"ok": True, "file": cards([get_file(fid)], user)[0]}


class FilePatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str] | str] = None
    visibility: Optional[str] = None
    org: Optional[str] = None  # slug, "" to remove
    collaborators: Optional[str] = None
    allow_comments: Optional[bool] = None
    allow_download: Optional[bool] = None


def apply_patch(c, f: dict, p: FilePatch, user: dict):
    sets, params = [], []
    if p.title is not None:
        t = p.title.strip()[: config.MAX_TITLE_LENGTH]
        if not t:
            raise HTTPException(400, "Title cannot be empty")
        sets.append("title=%s"); params.append(t)
    if p.description is not None:
        sets.append("description=%s"); params.append(p.description.strip()[: config.MAX_DESCRIPTION_LENGTH])
    tags = None
    if p.tags is not None:
        tags = parse_tags(p.tags)
        sets.append("tags=%s"); params.append(tags)
    org_id = f["org_id"]
    if p.org is not None:
        org_id = resolve_org_for_post(p.org, user) if p.org else None
        sets.append("org_id=%s"); params.append(org_id)
    if p.visibility is not None:
        if p.visibility not in ("public", "org", "private", "link"):
            raise HTTPException(400, "Bad visibility")
        if p.visibility == "org" and not org_id:
            raise HTTPException(400, "Org-only needs an organization")
        sets.append("visibility=%s"); params.append(p.visibility)
    for k in ("allow_comments", "allow_download"):
        v = getattr(p, k)
        if v is not None:
            sets.append(f"{k}=%s"); params.append(v)
    if sets:
        sets.append("updated_at=now()")
        c.execute(f"UPDATE files SET {', '.join(sets)} WHERE id=%s", (*params, f["id"]))
    if p.collaborators is not None and f["user_id"] == user["id"]:
        set_collaborators(c, f["id"], p.collaborators, user)
    if tags is not None or p.title is not None:
        row = c.execute("SELECT title, description, tags, extracted_text FROM files WHERE id=%s", (f["id"],)).fetchone()
        index_text(c, f["id"], row["title"], row["description"], row["extracted_text"], list(row["tags"]))


@router.patch("/api/files/{file_id}")
def edit(file_id: int, p: FilePatch, user=Depends(require_user)):
    f = file_or_404(file_id, user)
    if not can_edit(f, user):
        raise HTTPException(403, "Only the owner can edit this file")
    with tx() as c:
        apply_patch(c, f, p, user)
    return {"ok": True, "file": cards([get_file(file_id)], user)[0]}


@router.get("/api/files/{file_id}")
def detail(file_id: int, k: Optional[str] = None, user=Depends(get_current_user)):
    f = file_or_404(file_id, user, k)
    c = cards([f], user)[0]
    owner = f["user_id"] == uid_of(user)
    c["can_edit"] = can_edit(f, user)
    c["is_owner"] = owner
    c["allow_comments"] = f["allow_comments"]
    c["allow_download"] = f["allow_download"] or owner
    c["original_name"] = f["original_name"]
    c["version"] = f["version"]
    c["link"] = f"{config.APP_URL}/f/{f['id']}" + (f"?k={f['link_token']}" if f["visibility"] == "link" else "")
    c["collaborators"] = [
        {"email": r["email"] if owner else None, "username": r["username"], "name": r["full_name"], "registered": bool(r["username"])}
        for r in q("""SELECT fc.email, u.username, u.full_name FROM file_collaborators fc LEFT JOIN users u ON u.id=fc.user_id
                      WHERE fc.file_id=%s ORDER BY fc.id""", (file_id,)) if owner or r["username"]]
    c["versions"] = [dict(v, created_at=v["created_at"].isoformat()) for v in q(
        "SELECT version, original_name, file_type, size_bytes, note, created_at FROM file_versions WHERE file_id=%s ORDER BY version DESC",
        (file_id,))]
    c["featured_in"] = [dict(r) for r in q("""SELECT o.slug, o.name, of.status FROM org_features of JOIN orgs o ON o.id=of.org_id
                                              WHERE of.file_id=%s AND (of.status='approved' OR %s)""", (file_id, owner))]
    return c


@router.post("/api/files/{file_id}/version")
async def new_version(file_id: int, file: UploadFile = File(...), note: str = Form(""), user=Depends(require_user)):
    f = file_or_404(file_id, user)
    if not can_edit(f, user) or f["file_type"] == "url":
        raise HTTPException(403, "Cannot add a version")
    tmp, original, ftype, size = await _save_upload(file)
    try:
        text, pages = extract_text(tmp, ftype)
        key = f"files/{uuid.uuid4().hex}.{ftype}"
        store().put_file(key, tmp, MIME.get(ftype, "application/octet-stream"))
    finally:
        tmp.unlink(missing_ok=True)
    with tx() as c:
        v = f["version"] + 1
        c.execute("""INSERT INTO file_versions(file_id,version,storage_key,original_name,file_type,size_bytes,created_by,note)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (file_id, v, key, original, ftype, size, user["id"], note[:200]))
        c.execute("""UPDATE files SET version=%s, storage_key=%s, original_name=%s, file_type=%s, size_bytes=%s, page_count=%s,
                     extracted_text=%s, thumb_status='pending', updated_at=now() WHERE id=%s""",
                  (v, key, original, ftype, size, pages, text, file_id))
        index_text(c, file_id, f["title"], f["description"], text, list(f["tags"]))
        for s in c.execute("SELECT user_id FROM saves WHERE file_id=%s", (file_id,)).fetchall():
            notify(c, s["user_id"], "new_version", user["id"], file_id=file_id, data={"version": v})
    queue_processing(file_id)
    return {"ok": True, "version": v}


@router.delete("/api/files/{file_id}")
def delete(file_id: int, user=Depends(require_user)):
    f = get_file(file_id)
    if not f or f["user_id"] != user["id"]:
        raise HTTPException(404, "Not found or not yours")
    _delete_files([file_id])
    return {"ok": True}


def _delete_files(ids: list[int]):
    keys = [r["storage_key"] for r in q("SELECT storage_key FROM file_versions WHERE file_id = ANY(%s)", (ids,)) if r["storage_key"]]
    keys += [r["thumb_key"] for r in q("SELECT thumb_key FROM files WHERE id = ANY(%s)", (ids,)) if r["thumb_key"]]
    with tx() as c:
        c.execute("DELETE FROM files WHERE id = ANY(%s)", (ids,))
    st = store()
    for k in keys:
        st.delete(k)


# ---------------------------------------------------------------- serving
def _serve(key: str, filename: str, ftype: str, inline: bool):
    st = store()
    if st.kind == "s3":
        return RedirectResponse(st.presigned(key, filename, inline=inline), status_code=302)
    path = st.local_path(key)
    if not path:
        raise HTTPException(404, "File missing from storage")
    disp = "inline" if inline else "attachment"
    return FileResponse(path, media_type=MIME.get(ftype, mimetypes.guess_type(filename)[0] or "application/octet-stream"),
                        filename=filename, content_disposition_type=disp,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=600"})


def _display_name(f: dict, ftype: str) -> str:
    base = safe_name((f["title"] or "").strip() or Path(f["original_name"]).stem)
    ext = "." + ("jpg" if ftype == "jpeg" else ftype)
    return base if base.lower().endswith(ext) else base + ext


@router.get("/api/files/{file_id}/download")
def download(file_id: int, request: Request, k: Optional[str] = None, src: Optional[str] = None, v: Optional[int] = None,
             user=Depends(get_current_user)):
    f = file_or_404(file_id, user, k)
    if f["file_type"] == "url":
        record_event(file_id, "download", request, user, src)
        return RedirectResponse(f["url"])
    if not f["allow_download"] and f["user_id"] != uid_of(user):
        raise HTTPException(403, "The owner turned off downloads for this file")
    key, ftype = None, f["file_type"]
    if v:
        row = q1("SELECT storage_key, file_type FROM file_versions WHERE file_id=%s AND version=%s", (file_id, v))
        if not row:
            raise HTTPException(404, "Version not found")
        key, ftype = row["storage_key"], row["file_type"]
    else:
        key = q1("SELECT storage_key FROM files WHERE id=%s", (file_id,))["storage_key"]
    record_event(file_id, "download", request, user, src)
    return _serve(key, _display_name(f, ftype), ftype, inline=False)


def _pdf_key(f: dict, key: str) -> str:
    """Storage key of a PDF rendition (the file itself, or the cached LibreOffice preview)."""
    if f["file_type"] == "pdf":
        return key
    pkey = f"previews/{f['id']}-v{f['version']}.pdf"
    st = store()
    if not st.exists(pkey):
        src = st.local_path(key)
        if not src:
            raise HTTPException(404, "File missing")
        with tempfile.TemporaryDirectory(prefix="teams-prev-") as tmp:
            s2 = Path(tmp) / f"doc.{f['file_type']}"
            s2.write_bytes(Path(src).read_bytes())
            pdf = office_to_pdf(s2, Path(tmp))
            if not pdf:
                raise HTTPException(503, "Preview unavailable (LibreOffice could not convert this file)")
            st.put_file(pkey, pdf, "application/pdf")
    return pkey


@router.get("/page/{file_id}/{n}.webp")
def page_image(file_id: int, n: int, k: Optional[str] = None, w: int = 1100, user=Depends(get_current_user)):
    """One rendered page as WebP - a viewer that works the same on every phone (no PDF plugin needed)."""
    f = file_or_404(file_id, user, k)
    if f["file_type"] != "pdf" and f["file_type"] not in OFFICE:
        raise HTTPException(404, "No pages")
    w = 700 if w <= 700 else 1100 if w <= 1100 else 1600
    n = max(1, n)
    cache = Path(config.CACHE_DIR) / "pages"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"{file_id}-v{f['version']}-{n}-{w}.webp"
    if not out.exists():
        key = q1("SELECT storage_key FROM files WHERE id=%s", (file_id,))["storage_key"]
        src = store().local_path(_pdf_key(f, key))
        if not src:
            raise HTTPException(404, "File missing")
        with tempfile.TemporaryDirectory(prefix="teams-page-") as tmp:
            base = Path(tmp) / "p"
            subprocess.run(["pdftoppm", "-f", str(n), "-l", str(n), "-png", "-scale-to", str(w), "-singlefile", src, str(base)],
                           capture_output=True, timeout=60, check=False)
            png = base.with_suffix(".png")
            if not png.exists():
                raise HTTPException(404, "Page not found")
            from PIL import Image
            with Image.open(png) as im:
                im.convert("RGB").save(out, "WEBP", quality=80, method=4)
    return FileResponse(out, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/raw/{file_id}")
def raw(file_id: int, k: Optional[str] = None, user=Depends(get_current_user)):
    """Inline preview. Office files are converted to PDF once (at upload) and cached in storage."""
    f = file_or_404(file_id, user, k)
    if f["file_type"] == "url":
        return RedirectResponse(f["url"])
    key = q1("SELECT storage_key FROM files WHERE id=%s", (file_id,))["storage_key"]
    if f["file_type"] in OFFICE:
        return _serve(_pdf_key(f, key), _display_name(f, "pdf"), "pdf", inline=True)
    return _serve(key, _display_name(f, f["file_type"]), f["file_type"], inline=True)


@router.get("/thumb/{file_id}.webp")
def thumb(file_id: int, k: Optional[str] = None, user=Depends(get_current_user)):
    f = file_or_404(file_id, user, k)
    if not f["thumb_key"]:
        raise HTTPException(404, "No thumbnail")
    st = store()
    pub = st.public_url(f["thumb_key"]) if f["visibility"] == "public" else None
    if pub:
        return RedirectResponse(pub, status_code=302)
    path = st.local_path(f["thumb_key"])
    if not path:
        raise HTTPException(404, "No thumbnail")
    cache = "public, max-age=86400" if f["visibility"] == "public" else "private, max-age=3600"
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": cache})


class ViewIn(BaseModel):
    src: Optional[str] = None
    k: Optional[str] = None


@router.post("/api/files/{file_id}/view")
def view(file_id: int, body: ViewIn, request: Request, user=Depends(get_current_user)):
    file_or_404(file_id, user, body.k)
    return {"counted": record_event(file_id, "view", request, user, body.src)}


class ShareIn(BaseModel):
    channel: str = "copy"
    k: Optional[str] = None


@router.post("/api/files/{file_id}/share")
def share(file_id: int, body: ShareIn, request: Request, user=Depends(get_current_user)):
    file_or_404(file_id, user, body.k)
    return {"counted": record_event(file_id, "share", request, user, channel=body.channel)}


@router.get("/api/files/{file_id}/related")
def related(file_id: int, user=Depends(get_current_user)):
    f = file_or_404(file_id, user)
    params = {"uid": uid_of(user), "fid": file_id, "uid2": f["user_id"]}
    rows = q(FILE_SELECT + f""" JOIN (
        SELECT ft2.file_id, sum(ft1.weight + ft2.weight) AS s FROM file_topics ft1
        JOIN file_topics ft2 ON ft2.topic_id = ft1.topic_id AND ft2.file_id <> ft1.file_id
        WHERE ft1.file_id = %(fid)s GROUP BY ft2.file_id) r ON r.file_id = f.id
        WHERE {VIS} ORDER BY r.s DESC, f.like_count DESC LIMIT 8""", params)
    if len(rows) < 4:
        more = q(FILE_SELECT + f" WHERE f.user_id=%(uid2)s AND f.id<>%(fid)s AND {VIS} ORDER BY f.created_at DESC LIMIT 6", params)
        seen = {r["id"] for r in rows}
        rows += [r for r in more if r["id"] not in seen]
    return {"items": cards(rows[:8], user)}


# ---------------------------------------------------------------- bulk
class BulkIn(BaseModel):
    ids: list[int]
    action: str
    value: Optional[str | list[str] | bool] = None


@router.post("/api/bulk")
def bulk(b: BulkIn, user=Depends(require_user)):
    ids = list(dict.fromkeys(int(i) for i in b.ids))[:500]
    if not ids:
        raise HTTPException(400, "Select at least one file")
    rows = q("SELECT f.id, f.user_id, f.org_id, f.tags FROM files f WHERE f.id = ANY(%s)", (ids,))
    mine = [r for r in rows if r["user_id"] == user["id"] or (r["org_id"] and has_role(r["org_id"], user, "editor"))]
    if not mine:
        raise HTTPException(403, "None of the selected files can be edited by you")
    mids = [r["id"] for r in mine]
    a, v = b.action, b.value
    if a == "delete":
        own = [r["id"] for r in mine if r["user_id"] == user["id"]]
        _delete_files(own)
        return {"ok": True, "affected": len(own), "skipped": len(ids) - len(own)}
    with tx() as c:
        for r in mine:
            f = dict(r)
            if a == "visibility":
                apply_patch(c, f, FilePatch(visibility=str(v)), user)
            elif a == "org":
                p = FilePatch(org=str(v or ""))
                if not v:
                    c.execute("UPDATE files SET visibility='public' WHERE id=%s AND visibility='org'", (f["id"],))
                apply_patch(c, f, p, user)
            elif a in ("tags_add", "tags_remove", "tags_set"):
                new = parse_tags(v)
                cur = list(f["tags"] or [])
                if a == "tags_add":
                    cur = list(dict.fromkeys(cur + new))
                elif a == "tags_remove":
                    cur = [t for t in cur if t not in new]
                else:
                    cur = new
                apply_patch(c, f, FilePatch(tags=cur), user)
            elif a == "collaborators_add":
                if f["user_id"] != user["id"]:
                    continue
                existing = [x["email"] for x in c.execute("SELECT email FROM file_collaborators WHERE file_id=%s", (f["id"],)).fetchall()]
                set_collaborators(c, f["id"], ",".join(existing) + "," + str(v or ""), user)
            elif a in ("allow_comments", "allow_download"):
                c.execute(f"UPDATE files SET {a}=%s WHERE id=%s", (bool(v), f["id"]))
            else:
                raise HTTPException(400, "Unknown action")
    return {"ok": True, "affected": len(mids), "skipped": len(ids) - len(mids)}


@router.get("/api/bulk/zip")
def bulk_zip(ids: str, request: Request, user=Depends(get_current_user)):
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()][:200]
    tmp = Path(tempfile.mkstemp(prefix="teams-zip-", suffix=".zip")[1])
    names = set()
    count = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for fid in id_list:
            f = get_file(fid)
            if not f or f["file_type"] == "url" or not can_view(f, user):
                continue
            if not f["allow_download"] and f["user_id"] != uid_of(user):
                continue
            key = q1("SELECT storage_key FROM files WHERE id=%s", (fid,))["storage_key"]
            p = store().local_path(key) if key else None
            if not p:
                continue
            n = _display_name(f, f["file_type"])
            base, i = n, 1
            while n in names:
                i += 1
                n = f"{Path(base).stem} ({i}){Path(base).suffix}"
            names.add(n)
            z.write(p, n)
            record_event(fid, "download", request, user, "library")
            count += 1
    if not count:
        tmp.unlink(missing_ok=True)
        raise HTTPException(404, "Nothing to download")
    return FileResponse(tmp, media_type="application/zip", filename="teams-files.zip",
                        background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))
