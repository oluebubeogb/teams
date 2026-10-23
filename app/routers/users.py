"""Me / profile / avatar / sign-in. Accounts is the source of truth in production; local mode is for demos & tests."""
from __future__ import annotations

import re
import uuid
from http.cookies import SimpleCookie
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import config
from app.auth import (accounts_login, accounts_signup, accounts_update_me, avatar_src, get_current_user,
                      get_user_by_email, get_user_by_id, get_user_by_username, make_local_token, require_user,
                      upsert_user, _me_cache, _uid_cache)
from app.core import initials, is_site_admin
from app.db import q, q1, tx
from app.extract import resize_square
from app.storage import store

router = APIRouter()
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")


def me_out(u: dict) -> dict:
    name = u["full_name"] or u["username"]
    orgs = q("""SELECT o.slug, o.name, o.avatar_key, m.role FROM org_members m JOIN orgs o ON o.id=m.org_id
                WHERE m.user_id=%s AND m.status='active' ORDER BY o.name""", (u["id"],))
    from app.core import org_avatar_url
    return {"id": u["id"], "username": u["username"], "full_name": name, "name": name, "email": u["email"],
            "phone": u.get("phone"), "bio": u.get("bio") or "", "avatar": avatar_src(u), "avatar_url": avatar_src(u),
            "color": u.get("avatar_color") or "#5C5DE2", "initials": initials(name), "is_admin": is_site_admin(u),
            "orgs": [{"slug": o["slug"], "name": o["name"], "role": o["role"], "avatar": org_avatar_url(o["slug"], o["avatar_key"]),
                      "initials": initials(o["name"])} for o in orgs],
            "auth_mode": config.AUTH_MODE}


@router.get("/api/me")
def api_me(user=Depends(get_current_user)):
    if not user:
        return {"user": None, "auth_mode": config.AUTH_MODE}
    return {"user": me_out(user)}


def set_access_cookie(response: Response, token: str):
    kw = dict(key="access_token", value=token, httponly=True, max_age=config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
              samesite=config.COOKIE_SAMESITE, secure=config.COOKIE_SECURE, path="/")
    if config.COOKIE_DOMAIN:
        kw["domain"] = config.COOKIE_DOMAIN
    response.set_cookie(**kw)


def relay(response: Response, cookies: list[str]):
    for c in cookies or []:
        response.headers.append("set-cookie", c)


def fresh_token(request: Request, user: dict) -> Optional[str]:
    """Access token to call Accounts with - prefer one just minted by a refresh."""
    for raw in getattr(request.state, "relay_cookies", None) or []:
        sc = SimpleCookie()
        try:
            sc.load(raw)
        except Exception:
            continue
        if "access_token" in sc:
            return sc["access_token"].value
    return user.get("_access_token")


class LoginIn(BaseModel):
    identifier: Optional[str] = None
    email: Optional[str] = None
    password: str = ""


@router.post("/api/login")
async def api_login(data: LoginIn, response: Response):
    ident = (data.identifier or data.email or "").strip().lower()
    if not ident:
        raise HTTPException(400, "Enter your email")
    if not config.USE_ACCOUNTS:
        # Local/demo mode: email-only sign-in (creates the user on first use)
        if "@" not in ident:
            u = get_user_by_username(ident)
            if not u:
                raise HTTPException(401, "No account with that username")
        else:
            u = get_user_by_email(ident) or upsert_user({"email": ident, "username": re.sub(r"[^a-z0-9_-]", "", ident.split("@")[0])[:30] or "user"})
        token = make_local_token(u["id"])
        set_access_cookie(response, token)
        return {"ok": True, "username": u["username"], "token": token}
    email = ident
    if "@" not in ident:
        local = get_user_by_username(ident) or q1("SELECT email FROM users WHERE phone=%s", (ident,))
        if not local:
            raise HTTPException(401, "Please sign in with your email address")
        email = local["email"]
    result, err, cookies = await accounts_login(email, data.password)
    if err:
        raise HTTPException(401, err)
    token, profile = result.get("access_token"), result.get("user") or {}
    if not token or not profile:
        raise HTTPException(502, "Accounts returned an incomplete response")
    local = upsert_user(profile)
    if any(c.lower().startswith("access_token=") for c in cookies):
        relay(response, cookies)
    else:
        relay(response, cookies)
        set_access_cookie(response, token)
    return {"ok": True, "username": local["username"], "token": token}


class SignupIn(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., min_length=5, max_length=200)
    phone: Optional[str] = None
    password: str = ""
    username: str = Field(..., min_length=3, max_length=64)


@router.post("/api/signup")
async def api_signup(data: SignupIn, response: Response):
    username = data.username.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Username must be 3–63 chars: a-z, 0-9, _ or -, starting with a letter or number")
    if get_user_by_username(username):
        raise HTTPException(400, "Username already registered")
    email = data.email.strip().lower()
    if not config.USE_ACCOUNTS:
        if get_user_by_email(email):
            raise HTTPException(400, "Email already registered")
        u = upsert_user({"email": email, "username": username, "display_name": data.full_name.strip(), "phone": data.phone})
        token = make_local_token(u["id"])
        set_access_cookie(response, token)
        return {"ok": True, "username": u["username"], "token": token}
    if len(data.password) < config.MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Password must be at least {config.MIN_PASSWORD_LENGTH} characters")
    result, err, cookies = await accounts_signup({"email": email, "password": data.password, "display_name": data.full_name.strip(),
                                                  "username": username, "phone": (data.phone or "").strip() or None})
    if err:
        low = err.lower()
        if "username" in low and any(w in low for w in ("taken", "already", "exists")):
            raise HTTPException(400, "Username already registered")
        if "email" in low and any(w in low for w in ("registered", "already", "exists")):
            raise HTTPException(400, "Email already registered")
        raise HTTPException(400, err)
    token, profile = result.get("access_token"), result.get("user") or {}
    if not token or not profile:
        raise HTTPException(502, "Accounts returned an incomplete response")
    local = upsert_user(profile)
    relay(response, cookies)
    if not any(c.lower().startswith("access_token=") for c in cookies):
        set_access_cookie(response, token)
    return {"ok": True, "username": local["username"], "token": token}


@router.post("/api/logout")
def api_logout(request: Request, response: Response):
    tok = request.cookies.get("access_token")
    if tok:
        _me_cache.pop(tok, None)
        _uid_cache.pop(tok, None)
    for key in ("access_token", "refresh_token"):
        response.delete_cookie(key=key, path="/")
        if config.COOKIE_DOMAIN:
            response.delete_cookie(key=key, path="/", domain=config.COOKIE_DOMAIN)
    return {"ok": True, "redirect": f"{config.ACCOUNTS_URL}/logout?next={config.APP_URL}/" if config.USE_ACCOUNTS else "/"}


class ProfileIn(BaseModel):
    full_name: Optional[str] = Field(None, max_length=120)
    bio: Optional[str] = Field(None, max_length=500)
    avatar_color: Optional[str] = Field(None, max_length=9)


@router.api_route("/api/profile", methods=["PUT", "PATCH"])
@router.patch("/api/me")
async def update_profile(data: ProfileIn, request: Request, user=Depends(require_user)):
    patch = {}
    if data.full_name is not None and data.full_name.strip():
        patch["display_name"] = data.full_name.strip()
    if data.bio is not None:
        patch["bio"] = data.bio.strip()
    if data.avatar_color is not None:
        if not re.match(r"^#[0-9a-fA-F]{6}$", data.avatar_color):
            raise HTTPException(400, "Colour must look like #5C5DE2")
        patch["avatar_color"] = data.avatar_color
    token = fresh_token(request, user)
    if config.USE_ACCOUNTS and token and patch:
        profile, err = await accounts_update_me(token, patch)
        if err:
            raise HTTPException(400, err)
        if profile:
            u = upsert_user(profile)
            if "bio" in patch:  # keep Teams' copy even if Accounts doesn't echo it
                with tx() as c:
                    c.execute("UPDATE users SET bio=%s WHERE id=%s", (patch["bio"], u["id"]))
            return {"ok": True, "user": me_out(get_user_by_id(u["id"]))}
    with tx() as c:
        if "display_name" in patch:
            c.execute("UPDATE users SET full_name=%s WHERE id=%s", (patch["display_name"], user["id"]))
        if "bio" in patch:
            c.execute("UPDATE users SET bio=%s WHERE id=%s", (patch["bio"], user["id"]))
        if "avatar_color" in patch:
            c.execute("UPDATE users SET avatar_color=%s WHERE id=%s", (patch["avatar_color"], user["id"]))
    return {"ok": True, "user": me_out(get_user_by_id(user["id"]))}


@router.post("/api/me/avatar")
async def upload_avatar(request: Request, file: UploadFile = File(...), user=Depends(require_user)):
    """Profile pictures live in Collab Accounts (shared across the suite). Local mode stores them in Teams."""
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Image too large (max 8 MB)")
    if config.USE_ACCOUNTS:
        token = fresh_token(request, user)
        if not token:
            raise HTTPException(401, "Sign in again to change your photo")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(f"{config.ACCOUNTS_URL}/auth/me/avatar", headers={"Authorization": f"Bearer {token}"},
                                      files={"file": (file.filename or "avatar", data, file.content_type or "application/octet-stream")})
        except Exception as e:
            raise HTTPException(502, f"Accounts unreachable: {e}")
        if r.status_code != 200:
            try:
                detail = r.json().get("detail")
            except Exception:
                detail = None
            raise HTTPException(r.status_code if r.status_code < 500 else 502, detail or "Accounts rejected the image")
        _me_cache.pop(token, None)
        u = upsert_user(r.json())
        return {"ok": True, "avatar": avatar_src(u), "user": me_out(u)}
    try:
        out = resize_square(data, 256)
    except Exception:
        raise HTTPException(400, "That doesn't look like an image")
    key = f"avatars/{user['id']}-{uuid.uuid4().hex[:8]}.webp"
    store().put_bytes(key, out, "image/webp")
    old = user.get("avatar_url") or ""
    with tx() as c:
        c.execute("UPDATE users SET avatar_url=%s WHERE id=%s", (f"/media/{key}", user["id"]))
    if old.startswith("/media/avatars/"):
        store().delete(old[len("/media/"):])
    u = get_user_by_id(user["id"])
    return {"ok": True, "avatar": avatar_src(u), "user": me_out(u)}


@router.delete("/api/me/avatar")
async def delete_avatar(request: Request, user=Depends(require_user)):
    if config.USE_ACCOUNTS:
        token = fresh_token(request, user)
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.delete(f"{config.ACCOUNTS_URL}/auth/me/avatar", headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            raise HTTPException(502, "Accounts couldn't remove the photo")
        _me_cache.pop(token, None)
        u = upsert_user(r.json())
        return {"ok": True, "user": me_out(u)}
    with tx() as c:
        c.execute("UPDATE users SET avatar_url=NULL WHERE id=%s", (user["id"],))
    return {"ok": True, "user": me_out(get_user_by_id(user["id"]))}


@router.get("/media/avatars/{name}")
def local_avatar(name: str):
    if not re.match(r"^[0-9]+-[0-9a-f]{8}\.webp$", name):
        raise HTTPException(404, "Not found")
    pub = store().public_url(f"avatars/{name}")
    if pub:
        return RedirectResponse(pub)
    p = store().local_path(f"avatars/{name}")
    if not p:
        raise HTTPException(404, "Not found")
    return FileResponse(p, media_type="image/webp", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/api/me/library")
def library(tab: str = "files", user=Depends(require_user)):
    from app.core import FILE_SELECT, VIS, cards
    if tab == "files":
        rows = q(FILE_SELECT + " WHERE f.user_id=%(u)s ORDER BY f.created_at DESC LIMIT 500", {"u": user["id"]})
    elif tab == "shared":
        rows = q(FILE_SELECT + " WHERE f.id IN (SELECT file_id FROM file_collaborators WHERE user_id=%(u)s) ORDER BY f.updated_at DESC LIMIT 300",
                 {"u": user["id"]})
    elif tab == "saved":
        rows = q(FILE_SELECT + """ JOIN saves s ON s.file_id=f.id AND s.user_id=%(u)s
                 WHERE {VIS} ORDER BY s.created_at DESC LIMIT 300""".replace("{VIS}", VIS), {"u": user["id"], "uid": user["id"]})
    elif tab == "liked":
        rows = q(FILE_SELECT + """ JOIN likes l ON l.file_id=f.id AND l.user_id=%(u)s
                 WHERE {VIS} ORDER BY l.created_at DESC LIMIT 300""".replace("{VIS}", VIS), {"u": user["id"], "uid": user["id"]})
    else:
        raise HTTPException(400, "Bad tab")
    return {"items": cards(rows, user)}
