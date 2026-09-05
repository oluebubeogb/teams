"""Teams auth – uses central Collab Accounts when USE_ACCOUNTS=true."""

from __future__ import annotations
from typing import Optional
import httpx
from fastapi import Depends, HTTPException, status, Request, Cookie
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.config import ACCOUNTS_URL, USE_ACCOUNTS, SECRET_KEY, ALGORITHM
from app.database import get_db

security = HTTPBearer(auto_error=False)


def get_user_by_id(user_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
        return dict(row) if row else None


def get_user_by_phone(phone: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def get_user_by_accounts_id(accounts_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE accounts_id = ?", (accounts_id,)).fetchone()
        return dict(row) if row else None


def ensure_local_user_from_accounts(profile: dict) -> dict:
    accounts_id = profile["id"]
    email = profile["email"].lower()
    display_name = profile.get("display_name") or email.split("@")[0]
    username = profile.get("username") or email.split("@")[0]
    phone = profile.get("phone") or ""
    bio = profile.get("bio") or ""

    existing = get_user_by_accounts_id(accounts_id) or get_user_by_email(email)

    with get_db() as conn:
        if existing:
            conn.execute(
                """UPDATE users SET accounts_id = ?, full_name = ?,
                   username = COALESCE(?, username),
                   phone = CASE WHEN ? != '' THEN ? ELSE phone END,
                   bio = CASE WHEN ? != '' THEN ? ELSE bio END,
                   email = ? WHERE id = ?""",
                (accounts_id, display_name, username, phone, phone, bio, bio, email, existing["id"]),
            )
            local_id = existing["id"]
        else:
            base = username
            candidate = base
            n = 0
            while conn.execute("SELECT id FROM users WHERE username = ?", (candidate,)).fetchone():
                n += 1
                candidate = f"{base}{n}"
            phone_val = phone or f"acc-{accounts_id[:12]}"
            cur = conn.execute(
                """INSERT INTO users (username, full_name, email, phone, password_hash, bio, accounts_id)
                   VALUES (?, ?, ?, ?, '', ?, ?)""",
                (candidate, display_name, email, phone_val, bio, accounts_id),
            )
            local_id = cur.lastrowid
            conn.execute(
                "UPDATE file_collaborators SET user_id = ? WHERE email = ? AND user_id IS NULL",
                (local_id, email),
            )

    user = get_user_by_id(local_id)
    if user:
        user["display_name"] = display_name
        user["accounts_id"] = accounts_id
        user["country"] = profile.get("country")
        user["school"] = profile.get("school")
        user["date_of_birth"] = profile.get("date_of_birth")
        user["avatar_color"] = profile.get("avatar_color") or "#5C5DE2"
    return user


async def fetch_accounts_me(token: str) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{ACCOUNTS_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 200:
                return r.json()
    except Exception:
        return None
    return None


async def accounts_signup(payload: dict):
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{ACCOUNTS_URL}/auth/signup", json=payload)
            data = r.json() if r.content else {}
            if r.status_code in (200, 201):
                return data, None
            detail = data.get("detail") or "Signup failed"
            if isinstance(detail, list):
                detail = "; ".join(str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in detail)
            return None, str(detail)
    except Exception as e:
        return None, f"Accounts unreachable: {e}"


async def accounts_login(email: str, password: str):
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{ACCOUNTS_URL}/auth/login",
                json={"email": email, "password": password},
            )
            data = r.json() if r.content else {}
            if r.status_code == 200:
                return data, None
            return None, str(data.get("detail") or "Invalid credentials")
    except Exception as e:
        return None, f"Accounts unreachable: {e}"


async def accounts_update_me(token: str, patch: dict):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.patch(
                f"{ACCOUNTS_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"},
                json=patch,
            )
            data = r.json() if r.content else {}
            if r.status_code == 200:
                return data, None
            return None, str(data.get("detail") or "Update failed")
    except Exception as e:
        return None, str(e)


def _extract_token(request, credentials, cookie_token):
    if credentials:
        return credentials.credentials
    if cookie_token:
        return cookie_token
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth[7:]
    return None


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    token: Optional[str] = Cookie(None, alias="access_token"),
):
    token_str = _extract_token(request, credentials, token)
    if not token_str:
        return None

    if USE_ACCOUNTS:
        profile = await fetch_accounts_me(token_str)
        if not profile:
            return None
        user = ensure_local_user_from_accounts(profile)
        if user:
            user["_access_token"] = token_str
        return user

    try:
        from jose import jwt
        payload = jwt.decode(token_str, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            return None
        return get_user_by_id(int(user_id))
    except Exception:
        return None


async def require_user(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
