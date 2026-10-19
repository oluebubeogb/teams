import os
import shutil
import uuid
import re
import mimetypes
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, List
from datetime import timedelta

from fastapi import FastAPI, Request, Depends, HTTPException, status, UploadFile, File, Form, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.background import BackgroundTask

from app.database import init_db, get_db
from app.auth import (
    get_current_user, require_user, get_user_by_email, get_user_by_phone,
    get_user_by_username, get_user_by_id,
    accounts_signup, accounts_login, accounts_update_me, ensure_local_user_from_accounts,
)
from app.config import (
    USE_ACCOUNTS, ACCOUNTS_URL, COOKIE_DOMAIN, COOKIE_SECURE, COOKIE_SAMESITE,
    UPLOAD_DIR, MAX_DESCRIPTION_LENGTH, MAX_TITLE_LENGTH,
    MIN_PASSWORD_LENGTH, ALLOWED_EXTENSIONS, ACCESS_TOKEN_EXPIRE_MINUTES,
)
from app.models import UserCreate, UserLogin, ForgotPassword, ResetPassword, ProfileUpdate
from app.utils import generate_username, get_initials, relative_date, extract_text_from_file, score_relevance

app = FastAPI(title="Teams", description="Sleek file sharing & discovery", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent
UPLOAD_PATH = BASE_DIR / "uploads"
UPLOAD_PATH.mkdir(exist_ok=True)

# Converted Office previews are cached here. The original upload is never modified.
PREVIEW_CACHE_PATH = Path(os.getenv("PREVIEW_CACHE_DIR", "/tmp/teams-previews"))
PREVIEW_CACHE_PATH.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_PATH)), name="uploads")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

MIME_MAP = {
    "pdf": "application/pdf",
    "csv": "text/csv; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "js": "application/javascript",
    "json": "application/json",
    "xml": "application/xml",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@app.on_event("startup")
def startup():
    init_db()


# ---------- Helpers ----------
def parse_collaborator_emails(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    emails = []
    for part in raw.split(","):
        e = part.strip().lower()
        if e and "@" in e and re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e):
            emails.append(e)
    # dedupe preserve order
    seen = set()
    out = []
    for e in emails:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def set_collaborators(conn, file_id: int, emails: List[str]):
    conn.execute("DELETE FROM file_collaborators WHERE file_id = ?", (file_id,))
    for email in emails:
        user = get_user_by_email(email)
        uid = user["id"] if user else None
        conn.execute(
            "INSERT OR IGNORE INTO file_collaborators (file_id, email, user_id) VALUES (?, ?, ?)",
            (file_id, email, uid)
        )


def get_collaborators_for_file(file_id: int, for_owner: bool = False):
    """Return list of collab dicts. for_owner=True includes all emails; else only registered users with names."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT fc.email, fc.user_id, u.full_name, u.username
               FROM file_collaborators fc
               LEFT JOIN users u ON u.id = fc.user_id
               WHERE fc.file_id = ?
               ORDER BY fc.id""",
            (file_id,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if for_owner:
            result.append({
                "email": d["email"],
                "user_id": d["user_id"],
                "full_name": d["full_name"],
                "username": d["username"],
                "registered": bool(d["user_id"]),
            })
        elif d["user_id"] and d["full_name"]:
            result.append({
                "full_name": d["full_name"],
                "username": d["username"],
                "initials": get_initials(d["full_name"]),
            })
    return result


def make_file_card(row, author=None, include_owner_collabs=False):
    if author is None:
        author = get_user_by_id(row["user_id"]) or {}
    title = (row.get("title") or "").strip() or (row.get("original_name") or "")
    card = {
        "id": row["id"],
        "filename": row["filename"],
        "original_name": row["original_name"],
        "file_type": row["file_type"],
        "title": title[:MAX_TITLE_LENGTH],
        "description": (row["description"] or "")[:MAX_DESCRIPTION_LENGTH],
        "url": row["url"],
        "created_at": row["created_at"],
        "view_count": row["view_count"],
        "download_count": row["download_count"],
        "author_name": author.get("full_name", "Unknown"),
        "author_username": author.get("username", ""),
        "author_initials": get_initials(author.get("full_name", "U")),
        "relative_date": relative_date(row["created_at"]),
        "storage_path": row.get("storage_path"),
        "collaborators": get_collaborators_for_file(row["id"], for_owner=False),
    }
    if include_owner_collabs:
        card["owner_collaborators"] = get_collaborators_for_file(row["id"], for_owner=True)
        card["collaborators_csv"] = ", ".join(
            c["email"] for c in card["owner_collaborators"]
        )
    return card


# ---------- Pages ----------
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, user=Depends(get_current_user)):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT f.* FROM files f
            ORDER BY f.created_at DESC LIMIT 24
        """).fetchall()
    files = [make_file_card(dict(r)) for r in rows]
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "files": files,
        "query": ""
    })


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = "", user=Depends(get_current_user)):
    groups = {}
    if q.strip():
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM files").fetchall()
        scored = []
        for r in rows:
            row = dict(r)
            text = f"{row.get('title') or ''} {row['filename']} {row['description']} {row['extracted_text'] or ''}"
            score = score_relevance(q, text)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)

        type_scores = {}
        type_items = {}
        for score, row in scored:
            ft = row["file_type"]
            if ft not in type_items:
                type_items[ft] = []
                type_scores[ft] = 0
            type_items[ft].append(make_file_card(row))
            type_scores[ft] = max(type_scores[ft], score)

        sorted_types = sorted(type_scores.keys(), key=lambda t: type_scores[t], reverse=True)
        for t in sorted_types:
            groups[t] = type_items[t]

    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "files": [],
        "groups": groups,
        "query": q
    })


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user=Depends(get_current_user)):
    if user:
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "user": None})


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, user=Depends(get_current_user)):
    if user:
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("signup.html", {"request": request, "user": None})


@app.get("/forgot", response_class=HTMLResponse)
async def forgot_page(request: Request):
    return templates.TemplateResponse("forgot.html", {"request": request, "user": None})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user=Depends(require_user)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM files WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],)
        ).fetchall()
    files = [make_file_card(dict(r), user, include_owner_collabs=True) for r in rows]
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "files": files,
        "initials": get_initials(user["full_name"])
    })


@app.get("/u/{username}", response_class=HTMLResponse)
async def public_profile(request: Request, username: str, user=Depends(get_current_user)):
    profile = get_user_by_username(username)
    if not profile:
        raise HTTPException(404, "User not found")
    with get_db() as conn:
        # Own files
        own_rows = conn.execute(
            "SELECT * FROM files WHERE user_id = ? ORDER BY created_at DESC",
            (profile["id"],)
        ).fetchall()
        # Files where this user is a collaborator
        collab_rows = conn.execute(
            """SELECT f.* FROM files f
               INNER JOIN file_collaborators fc ON fc.file_id = f.id
               WHERE fc.user_id = ? AND f.user_id != ?
               ORDER BY f.created_at DESC""",
            (profile["id"], profile["id"])
        ).fetchall()
    own_files = [make_file_card(dict(r), profile) for r in own_rows]
    collab_files = [make_file_card(dict(r)) for r in collab_rows]
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": user,
        "profile": profile,
        "files": own_files,
        "collab_files": collab_files,
        "initials": get_initials(profile["full_name"])
    })


# ---------- Auth API ----------
def _set_access_cookie(response: Response, token: str):
    kwargs = dict(
        key="access_token",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite=COOKIE_SAMESITE,
        secure=COOKIE_SECURE,
        path="/",
    )
    if COOKIE_DOMAIN:
        kwargs["domain"] = COOKIE_DOMAIN
    response.set_cookie(**kwargs)


def _clear_access_cookie(response: Response):
    kwargs = dict(key="access_token", path="/")
    if COOKIE_DOMAIN:
        kwargs["domain"] = COOKIE_DOMAIN
    response.delete_cookie(**kwargs)


@app.post("/api/signup")
async def api_signup(data: UserCreate, response: Response):
    """Signup via central Accounts, then ensure local Teams user row."""
    if len(data.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters")

    if not USE_ACCOUNTS:
        raise HTTPException(503, "Local signup disabled. Set USE_ACCOUNTS=true and configure ACCOUNTS_URL.")

    # Username is validated/normalized by UserCreate; check local DB first for a clear message
    username = data.username  # already lowercased + format-checked
    if get_user_by_username(username):
        raise HTTPException(400, "Username already registered")

    # Map Teams fields → Accounts signup (Accounts is source of truth for uniqueness)
    payload = {
        "email": data.email.lower().strip(),
        "password": data.password,
        "display_name": data.full_name.strip(),
        "username": username,
        "phone": data.phone.strip() if data.phone else None,
    }
    result, err = await accounts_signup(payload)
    if err:
        err_lower = err.lower()
        if "username" in err_lower and ("taken" in err_lower or "already" in err_lower or "exists" in err_lower):
            raise HTTPException(400, "Username already registered")
        if "email" in err_lower and ("registered" in err_lower or "already" in err_lower or "exists" in err_lower):
            raise HTTPException(400, "Email already registered")
        raise HTTPException(400, err)

    token = result.get("access_token")
    profile = result.get("user") or {}
    if not token or not profile:
        raise HTTPException(500, "Accounts returned incomplete response")

    local = ensure_local_user_from_accounts(profile)
    _set_access_cookie(response, token)
    return {"ok": True, "username": local.get("username"), "token": token}


@app.post("/api/login")
async def api_login(data: UserLogin, response: Response):
    """Login via central Accounts (email). Phone-only login is no longer supported."""
    if not USE_ACCOUNTS:
        raise HTTPException(503, "Local login disabled. Configure ACCOUNTS_URL.")

    ident = data.identifier.strip()
    # Prefer email; if phone was entered, try local lookup then accounts email
    email = ident.lower() if "@" in ident else None
    if not email:
        local = get_user_by_phone(ident)
        if local:
            email = local["email"]
        else:
            raise HTTPException(401, "Please log in with your email address")

    result, err = await accounts_login(email, data.password)
    if err:
        raise HTTPException(401, err)

    token = result.get("access_token")
    profile = result.get("user") or {}
    if not token or not profile:
        raise HTTPException(500, "Accounts returned incomplete response")

    local = ensure_local_user_from_accounts(profile)
    with get_db() as conn:
        conn.execute(
            "UPDATE file_collaborators SET user_id = ? WHERE email = ? AND (user_id IS NULL OR user_id != ?)",
            (local["id"], local["email"], local["id"]),
        )
    _set_access_cookie(response, token)
    return {"ok": True, "username": local.get("username"), "token": token}


@app.post("/api/forgot")
async def api_forgot(data: ForgotPassword):
    """Password reset is handled by Accounts."""
    return {
        "ok": True,
        "message": "Use Collab Accounts password reset.",
        "accounts_url": f"{ACCOUNTS_URL}/docs",
        "hint": "POST /auth/password-reset/request on Accounts with your email",
    }


@app.post("/api/reset")
async def api_reset(data: ResetPassword, response: Response):
    raise HTTPException(
        400,
        f"Password reset moved to Accounts. Open {ACCOUNTS_URL}/docs and use /auth/password-reset/*",
    )


@app.post("/api/logout")
async def api_logout(response: Response):
    _clear_access_cookie(response)
    # Also clear Accounts cookies if shared domain
    for key in ("access_token", "refresh_token"):
        kwargs = dict(key=key, path="/")
        if COOKIE_DOMAIN:
            kwargs["domain"] = COOKIE_DOMAIN
        response.delete_cookie(**kwargs)
    return {"ok": True}


@app.get("/api/me")
async def api_me(user=Depends(get_current_user)):
    if not user:
        return {"user": None}
    return {
        "user": {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "email": user["email"],
            "phone": user["phone"],
            "bio": user["bio"] or "",
            "initials": get_initials(user["full_name"])
        }
    }


@app.put("/api/profile")
async def api_update_profile(data: ProfileUpdate, user=Depends(require_user)):
    """Profile writes go to Accounts (source of truth), then local cache is updated."""
    token = user.get("_access_token")
    patch = {}
    if data.full_name is not None:
        patch["display_name"] = data.full_name.strip()
    if data.bio is not None:
        patch["bio"] = data.bio.strip()[:300]

    if USE_ACCOUNTS and token and patch:
        profile, err = await accounts_update_me(token, patch)
        if err:
            raise HTTPException(400, err)
        if profile:
            ensure_local_user_from_accounts(profile)
            return {"ok": True}

    # Fallback local-only
    with get_db() as conn:
        if data.full_name is not None:
            conn.execute("UPDATE users SET full_name = ? WHERE id = ?", (data.full_name.strip(), user["id"]))
        if data.bio is not None:
            conn.execute("UPDATE users SET bio = ? WHERE id = ?", (data.bio.strip()[:300], user["id"]))
    return {"ok": True}


# ---------- File API ----------
@app.post("/api/upload")
async def api_upload(
    title: str = Form(...),
    description: str = Form(...),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    collaborators: Optional[str] = Form(None),
    user=Depends(require_user)
):
    title = (title or "")[:MAX_TITLE_LENGTH].strip()
    description = (description or "")[:MAX_DESCRIPTION_LENGTH].strip()
    if not title:
        raise HTTPException(400, f"Title is required (max {MAX_TITLE_LENGTH} chars)")
    if not description:
        raise HTTPException(400, f"Description is required (max {MAX_DESCRIPTION_LENGTH} chars)")

    emails = parse_collaborator_emails(collaborators)

    if url and url.strip():
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO files (user_id, filename, original_name, file_type, url, title, description, extracted_text)
                   VALUES (?, ?, ?, 'url', ?, ?, ?, ?)""",
                (user["id"], url[:200], url[:200], url, title, description, url)
            )
            file_id = cur.lastrowid
            set_collaborators(conn, file_id, emails)
        return {"ok": True, "id": file_id, "type": "url"}

    if not file:
        raise HTTPException(400, "Provide a file or a URL")

    original = file.filename or "file"
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    file_type = ext.lstrip(".")
    if file_type == "xls":
        file_type = "xlsx"

    safe_name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_PATH / safe_name

    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)

    extracted = extract_text_from_file(dest, file_type)

    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO files (user_id, filename, original_name, file_type, storage_path, title, description, extracted_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user["id"], safe_name, original, file_type, str(dest), title, description, extracted)
        )
        file_id = cur.lastrowid
        set_collaborators(conn, file_id, emails)

    return {"ok": True, "id": file_id, "type": file_type}


@app.put("/api/file/{file_id}")
async def api_edit_file(
    file_id: int,
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    collaborators: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    user=Depends(require_user)
):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM files WHERE id = ? AND user_id = ?",
            (file_id, user["id"])
        ).fetchone()
        if not row:
            raise HTTPException(404, "Not found or not yours")
        row = dict(row)

        updates = []
        params = []

        if title is not None:
            t = title.strip()[:MAX_TITLE_LENGTH]
            if not t:
                raise HTTPException(400, "Title cannot be empty")
            updates.append("title = ?")
            params.append(t)

        if description is not None:
            d = description.strip()[:MAX_DESCRIPTION_LENGTH]
            if not d:
                raise HTTPException(400, "Description cannot be empty")
            updates.append("description = ?")
            params.append(d)

        if file is not None and file.filename:
            if row["file_type"] == "url":
                raise HTTPException(400, "Cannot replace a URL entry with a file")
            original = file.filename or "file"
            ext = Path(original).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(400, f"Unsupported type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
            file_type = ext.lstrip(".")
            if file_type == "xls":
                file_type = "xlsx"
            safe_name = f"{uuid.uuid4().hex}{ext}"
            dest = UPLOAD_PATH / safe_name
            content = await file.read()
            with open(dest, "wb") as f:
                f.write(content)
            extracted = extract_text_from_file(dest, file_type)
            # remove old file
            if row.get("storage_path"):
                old = Path(row["storage_path"])
                if old.exists():
                    old.unlink(missing_ok=True)
            updates.extend([
                "filename = ?", "original_name = ?", "file_type = ?",
                "storage_path = ?", "extracted_text = ?"
            ])
            params.extend([safe_name, original, file_type, str(dest), extracted])

        if updates:
            params.append(file_id)
            conn.execute(
                f"UPDATE files SET {', '.join(updates)} WHERE id = ?",
                params
            )

        if collaborators is not None:
            emails = parse_collaborator_emails(collaborators)
            set_collaborators(conn, file_id, emails)

    return {"ok": True}


@app.get("/api/download/{file_id}")
async def api_download(file_id: int, user=Depends(get_current_user)):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            raise HTTPException(404, "File not found")
        conn.execute("UPDATE files SET download_count = download_count + 1 WHERE id = ?", (file_id,))
    row = dict(row)
    if row["file_type"] == "url":
        return RedirectResponse(row["url"])
    path = Path(row["storage_path"])
    if not path.exists():
        raise HTTPException(404, "File missing on disk")
    return FileResponse(
        path,
        filename=row["original_name"],
        media_type=MIME_MAP.get(row["file_type"], "application/octet-stream"),
        content_disposition_type="attachment",
    )


OFFICE_PREVIEW_TYPES = {"pptx", "docx", "xlsx", "xls"}

def get_office_preview_path(source_path: Path, file_id: int, file_type: str) -> Path:
    """Convert an Office document to PDF for browser preview and cache the result.

    LibreOffice keeps PowerPoint slide boundaries when exporting to PDF, so each
    slide remains a separate PDF page. The source Office file is never changed.
    """
    cache_name = f"{file_id}-{source_path.stat().st_mtime_ns}-{source_path.stat().st_size}.pdf"
    cached = PREVIEW_CACHE_PATH / cache_name
    if cached.exists() and cached.stat().st_size > 0:
        return cached

    with tempfile.TemporaryDirectory(prefix="teams-office-") as tmp: 
        tmp_path = Path(tmp)
        source_copy = tmp_path / source_path.name
        source_copy.write_bytes(source_path.read_bytes())

        # Give every conversion its own LibreOffice profile so concurrent
        # previews do not fight over the default profile lock.
        profile_dir = tmp_path / "profile"
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        profile_dir.mkdir()
        profile_uri = profile_dir.as_uri()

        command = [
            "libreoffice",
            "--headless",
            f"-env:UserInstallation={profile_uri}",
            "--convert-to", "pdf",
            "--outdir", str(output_dir),
            str(source_copy),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except FileNotFoundError:
            raise HTTPException(
                503,
                "Office preview is unavailable because LibreOffice is not installed on the server."
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "Office preview conversion timed out")

        converted = output_dir / f"{source_copy.stem}.pdf"
        if result.returncode != 0 or not converted.exists() or converted.stat().st_size == 0:
            detail = (result.stderr or result.stdout or "Unknown LibreOffice conversion error").strip()
            raise HTTPException(500, f"Could not convert {file_type.upper()} to PDF for preview: {detail[:500]}")

        cached.write_bytes(converted.read_bytes())

    return cached


@app.get("/view/{file_id}", response_class=HTMLResponse)
async def view_file(request: Request, file_id: int):
    """HTML wrapper so the browser tab title shows the Teams platform title
    (not the embedded PDF/Office document title such as 'PptxGenJS Presentation').
    """
    with get_db() as conn:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            raise HTTPException(404, "File not found")
    row = dict(row)

    if row["file_type"] == "url":
        return RedirectResponse(row["url"])

    # Prefer the user-provided title on the platform; fall back to original filename
    page_title = (row.get("title") or "").strip() or (row.get("original_name") or "File")
    page_title = page_title[:MAX_TITLE_LENGTH]

    # Types that browsers can display inside an iframe reliably
    embeddable = {
        "pdf", "png", "jpg", "jpeg", "gif", "webp", "svg", "bmp",
        "mp4", "webm", "ogg", "mp3", "wav", "txt", "csv",
        "pptx", "docx", "xlsx", "xls",  # these are converted to PDF by /api/view
    }
    extension = ""
    if row.get("storage_path"):
        extension = Path(row["storage_path"]).suffix.lower().lstrip(".")
    file_type = (row.get("file_type") or extension or "").lower()
    is_embeddable = file_type in embeddable or extension in embeddable

    return templates.TemplateResponse("view.html", {
        "request": request,
        "page_title": page_title,
        "content_url": f"/api/view/{file_id}",
        "is_embeddable": is_embeddable,
    })


@app.get("/api/view/{file_id}")
async def api_view(file_id: int):
    """Preview supported files in the browser; convert Office files to paged PDF."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            raise HTTPException(404, "File not found")
        conn.execute("UPDATE files SET view_count = view_count + 1 WHERE id = ?", (file_id,))
    row = dict(row)
    if row["file_type"] == "url":
        return RedirectResponse(row["url"])

    path = Path(row["storage_path"])
    if not path.exists():
        raise HTTPException(404, "File missing")

    extension = path.suffix.lower().lstrip(".")

    # Browsers cannot natively render PPTX/DOCX/XLSX. Convert Office files to
    # PDF first. For PPTX, LibreOffice exports one slide per PDF page, preserving
    # the normal slide/page structure and allowing the browser's PDF viewer to
    # handle paging, zoom and fullscreen.
    # Prefer platform title for the suggested filename (sanitized)
    display_name = (row.get("title") or "").strip() or (row.get("original_name") or "file")
    display_name = display_name.replace('"', "").replace("/", "-").replace("\\", "-")[:200]

    if extension in OFFICE_PREVIEW_TYPES or row.get("file_type") in OFFICE_PREVIEW_TYPES:
        preview_path = get_office_preview_path(path, file_id, extension or row.get("file_type"))
        safe_pdf_name = f"{display_name}.pdf" if not display_name.lower().endswith(".pdf") else display_name
        return FileResponse(
            preview_path,
            media_type="application/pdf",
            filename=safe_pdf_name,
            content_disposition_type="inline",
            headers={
                "Content-Disposition": f'inline; filename="{safe_pdf_name}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, max-age=3600",
            },
        )

    media = MIME_MAP.get(row.get("file_type")) or MIME_MAP.get(extension) or mimetypes.guess_type(row["original_name"])[0] or "application/octet-stream"
    # Keep original extension on the display name when possible
    orig_ext = Path(row["original_name"]).suffix
    if orig_ext and not display_name.lower().endswith(orig_ext.lower()):
        display_name = f"{display_name}{orig_ext}"

    return FileResponse(
        path,
        media_type=media,
        filename=display_name,
        content_disposition_type="inline",
        headers={
            "Content-Disposition": f'inline; filename="{display_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )


@app.delete("/api/file/{file_id}")
async def api_delete_file(file_id: int, user=Depends(require_user)):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM files WHERE id = ? AND user_id = ?", (file_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Not found or not yours")
        row = dict(row)
        conn.execute("DELETE FROM file_collaborators WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    if row.get("storage_path"):
        p = Path(row["storage_path"])
        if p.exists():
            p.unlink(missing_ok=True)
    return {"ok": True}


# Health
@app.get("/health")
def health():
    return {"status": "ok", "app": "Teams"}
