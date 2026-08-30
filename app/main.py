import os
import shutil
import uuid
from pathlib import Path
from typing import Optional, List
from datetime import timedelta

from fastapi import FastAPI, Request, Depends, HTTPException, status, UploadFile, File, Form, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db, get_db
from app.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, require_user, get_user_by_email, get_user_by_phone,
    get_user_by_username, get_user_by_id
)
from app.models import UserCreate, UserLogin, ForgotPassword, ResetPassword, ProfileUpdate
from app.utils import generate_username, get_initials, relative_date, extract_text_from_file, score_relevance
from app.config import UPLOAD_DIR, MAX_DESCRIPTION_LENGTH, MIN_PASSWORD_LENGTH, ALLOWED_EXTENSIONS, ACCESS_TOKEN_EXPIRE_MINUTES

app = FastAPI(title="Teams", description="Sleek file sharing & discovery", version="1.0.0")

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

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_PATH)), name="uploads")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

@app.on_event("startup")
def startup():
    init_db()

# ---------- Helpers ----------
def make_file_card(row, author=None):
    if author is None:
        author = get_user_by_id(row["user_id"]) or {}
    return {
        "id": row["id"],
        "filename": row["filename"],
        "original_name": row["original_name"],
        "file_type": row["file_type"],
        "description": (row["description"] or "")[:MAX_DESCRIPTION_LENGTH],
        "url": row["url"],
        "created_at": row["created_at"],
        "view_count": row["view_count"],
        "download_count": row["download_count"],
        "author_name": author.get("full_name", "Unknown"),
        "author_username": author.get("username", ""),
        "author_initials": get_initials(author.get("full_name", "U")),
        "relative_date": relative_date(row["created_at"]),
        "storage_path": row["storage_path"],
    }

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
            text = f"{row['filename']} {row['description']} {row['extracted_text'] or ''}"
            score = score_relevance(q, text)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)

        # Group by type, keep highest score order of groups
        type_scores = {}
        type_items = {}
        for score, row in scored:
            ft = row["file_type"]
            if ft not in type_items:
                type_items[ft] = []
                type_scores[ft] = 0
            type_items[ft].append(make_file_card(row))
            type_scores[ft] = max(type_scores[ft], score)

        # Sort groups by max score
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
    files = [make_file_card(dict(r), user) for r in rows]
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
        rows = conn.execute(
            "SELECT * FROM files WHERE user_id = ? ORDER BY created_at DESC",
            (profile["id"],)
        ).fetchall()
    files = [make_file_card(dict(r), profile) for r in rows]
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": user,
        "profile": profile,
        "files": files,
        "initials": get_initials(profile["full_name"])
    })

# ---------- Auth API ----------
@app.post("/api/signup")
async def api_signup(data: UserCreate, response: Response):
    if len(data.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters")

    if get_user_by_email(data.email):
        raise HTTPException(400, "Email already registered")
    if get_user_by_phone(data.phone):
        raise HTTPException(400, "Phone already registered")

    def exists(u):
        return get_user_by_username(u) is not None

    username = data.username or generate_username(data.full_name, exists)
    if exists(username):
        username = generate_username(data.full_name + str(uuid.uuid4())[:4], exists)

    pw_hash = hash_password(data.password)
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, full_name, email, phone, password_hash) VALUES (?, ?, ?, ?, ?)",
            (username, data.full_name.strip(), data.email, data.phone, pw_hash)
        )
        user_id = cur.lastrowid

    token = create_access_token({"sub": str(user_id)})
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax"
    )
    return {"ok": True, "username": username, "token": token}

@app.post("/api/login")
async def api_login(data: UserLogin, response: Response):
    ident = data.identifier.strip().lower()
    user = get_user_by_email(ident) or get_user_by_phone(data.identifier.strip())
    if not user or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    token = create_access_token({"sub": str(user["id"])})
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax"
    )
    return {"ok": True, "username": user["username"], "token": token}

@app.post("/api/forgot")
async def api_forgot(data: ForgotPassword):
    user = get_user_by_email(data.email.lower().strip())
    if not user or user["phone"] != data.phone.strip().replace(" ", "").replace("-", ""):
        raise HTTPException(400, "Email and phone do not match any account")
    return {"ok": True, "message": "Verified. You may now set a new password."}

@app.post("/api/reset")
async def api_reset(data: ResetPassword, response: Response):
    user = get_user_by_email(data.email.lower().strip())
    if not user or user["phone"] != data.phone.strip().replace(" ", "").replace("-", ""):
        raise HTTPException(400, "Email and phone do not match")
    if len(data.new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    pw_hash = hash_password(data.new_password)
    with get_db() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user["id"]))
    token = create_access_token({"sub": str(user["id"])})
    response.set_cookie(key="access_token", value=token, httponly=True, max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60, samesite="lax")
    return {"ok": True, "token": token}

@app.post("/api/logout")
async def api_logout(response: Response):
    response.delete_cookie("access_token")
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
    with get_db() as conn:
        if data.full_name is not None:
            conn.execute("UPDATE users SET full_name = ? WHERE id = ?", (data.full_name.strip(), user["id"]))
        if data.bio is not None:
            conn.execute("UPDATE users SET bio = ? WHERE id = ?", (data.bio.strip()[:300], user["id"]))
    return {"ok": True}

# ---------- File API ----------
@app.post("/api/upload")
async def api_upload(
    description: str = Form(...),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    user=Depends(require_user)
):
    description = (description or "")[:MAX_DESCRIPTION_LENGTH].strip()
    if not description:
        raise HTTPException(400, "Description is required (max 50 chars)")

    if url and url.strip():
        # URL upload
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO files (user_id, filename, original_name, file_type, url, description, extracted_text)
                   VALUES (?, ?, ?, 'url', ?, ?, ?)""",
                (user["id"], url[:200], url[:200], url, description, url)
            )
            file_id = cur.lastrowid
        return {"ok": True, "id": file_id, "type": "url"}

    if not file:
        raise HTTPException(400, "Provide a file or a URL")

    original = file.filename or "file"
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

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
            """INSERT INTO files (user_id, filename, original_name, file_type, storage_path, description, extracted_text)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user["id"], safe_name, original, file_type, str(dest), description, extracted)
        )
        file_id = cur.lastrowid

    return {"ok": True, "id": file_id, "type": file_type}

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
    return FileResponse(path, filename=row["original_name"])

@app.get("/api/view/{file_id}")
async def api_view(file_id: int):
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
    # For PDF we can serve inline; others force download or open
    media = "application/pdf" if row["file_type"] == "pdf" else "application/octet-stream"
    return FileResponse(path, media_type=media, filename=row["original_name"], content_disposition_type="inline")

@app.delete("/api/file/{file_id}")
async def api_delete_file(file_id: int, user=Depends(require_user)):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM files WHERE id = ? AND user_id = ?", (file_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Not found or not yours")
        row = dict(row)
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
