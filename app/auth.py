"""Auth. Production: Collab Accounts SSO via the shared cookie on COOKIE_DOMAIN.
Local mode (AUTH_MODE=local) signs people in by email only - for demos and tests."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, Request, status
from jose import jwt

from app import config
from app.db import q1, tx

_me_cache: dict[str, tuple[float, dict]] = {}
_refresh_cache: dict[str, tuple[float, dict, list[str]]] = {}
_refresh_lock = threading.Lock()
_uid_cache: dict[str, tuple[float, int]] = {}
_CACHE_MAX = 2000

USER_COLS = "id, accounts_id, username, full_name, email, phone, bio, avatar_url, avatar_color, is_admin, created_at"


def avatar_src(u: dict | None) -> Optional[str]:
    if not u:
        return None
    url = u.get("avatar_url")
    if not url:
        return None
    if url.startswith("/") and config.USE_ACCOUNTS:
        return config.ACCOUNTS_URL + url
    return url


def get_user_by_id(uid: int) -> Optional[dict]:
    return q1(f"SELECT {USER_COLS} FROM users WHERE id=%s", (uid,))


def get_user_by_username(username: str) -> Optional[dict]:
    return q1(f"SELECT {USER_COLS} FROM users WHERE lower(username)=lower(%s)", (username,))


def get_user_by_email(email: str) -> Optional[dict]:
    return q1(f"SELECT {USER_COLS} FROM users WHERE email=%s", (email.lower().strip(),))


def _claim_pending(c, uid: int, email: str):
    """Attach collaborator rows + org email invites addressed to this email."""
    c.execute("UPDATE file_collaborators SET user_id=%s WHERE email=%s AND user_id IS NULL", (uid, email))
    rows = c.execute("SELECT org_id, role, invited_by FROM org_email_invites WHERE email=%s", (email,)).fetchall()
    for r in rows:
        c.execute("""INSERT INTO org_members(org_id,user_id,role,status,invited_by) VALUES (%s,%s,%s,'invited',%s)
                     ON CONFLICT DO NOTHING""", (r["org_id"], uid, r["role"], r["invited_by"]))
        c.execute("""INSERT INTO notifications(user_id,kind,actor_id,org_id) VALUES (%s,'org_invite',%s,%s)""",
                  (uid, r["invited_by"], r["org_id"]))
    c.execute("DELETE FROM org_email_invites WHERE email=%s", (email,))


def upsert_user(profile: dict) -> dict:
    """Create/refresh the local mirror of a Collab Accounts user."""
    accounts_id = str(profile.get("id") or "")
    email = (profile.get("email") or "").lower().strip()
    name = profile.get("display_name") or profile.get("full_name") or email.split("@")[0]
    username = (profile.get("username") or email.split("@")[0]).lower()
    with tx() as c:
        existing = c.execute("SELECT id FROM users WHERE (accounts_id=%s AND %s<>'') OR email=%s ORDER BY id LIMIT 1",
                             (accounts_id, accounts_id, email)).fetchone()
        if existing:
            uid = existing["id"]
            # keep username unique if Accounts changed it
            clash = c.execute("SELECT id FROM users WHERE lower(username)=%s AND id<>%s", (username, uid)).fetchone()
            c.execute(f"""UPDATE users SET accounts_id=COALESCE(NULLIF(%s,''),accounts_id), full_name=%s, email=%s,
                         username=CASE WHEN %s THEN username ELSE %s END,
                         phone=COALESCE(NULLIF(%s,''),phone), bio=COALESCE(NULLIF(%s,''),bio),
                         avatar_url=%s, avatar_color=COALESCE(%s,avatar_color), last_seen_at=now()
                         WHERE id=%s""",
                      (accounts_id, name, email, bool(clash), username, profile.get("phone") or "",
                       profile.get("bio"), profile.get("avatar_url"), profile.get("avatar_color"), uid))
        else:
            cand, n = username, 0
            while c.execute("SELECT 1 FROM users WHERE lower(username)=%s", (cand,)).fetchone():
                n += 1
                cand = f"{username}{n}"
            uid = c.execute("""INSERT INTO users(accounts_id,username,full_name,email,phone,bio,avatar_url,avatar_color,last_seen_at)
                               VALUES (NULLIF(%s,''),%s,%s,%s,%s,%s,%s,%s,now()) RETURNING id""",
                            (accounts_id, cand, name, email, profile.get("phone"), profile.get("bio") or "",
                             profile.get("avatar_url"), profile.get("avatar_color") or "#5C5DE2")).fetchone()["id"]
        _claim_pending(c, uid, email)
        if config.ADMIN_USERNAMES:
            c.execute("UPDATE users SET is_admin = (lower(username) = ANY(%s)) OR is_admin WHERE id=%s",
                      (config.ADMIN_USERNAMES, uid))
    return get_user_by_id(uid)


# ---------- Accounts HTTP ----------
def _trim(cache: dict):
    if len(cache) > _CACHE_MAX:
        for k in list(cache.keys())[: len(cache) // 2]:
            cache.pop(k, None)


async def fetch_accounts_me(token: str) -> Optional[dict]:
    now = time.time()
    hit = _me_cache.get(token)
    if hit and hit[0] > now:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{config.ACCOUNTS_URL}/auth/me", headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            data = r.json()
            _me_cache[token] = (now + config.ACCOUNTS_ME_CACHE_SECONDS, data)
            _trim(_me_cache)
            return data
    except Exception:
        return None
    return None


async def refresh_via_accounts(refresh_token: str) -> tuple[Optional[dict], list[str]]:
    """Renew an expired session with the shared refresh cookie. Returns (profile, set-cookie headers)."""
    now = time.time()
    with _refresh_lock:
        hit = _refresh_cache.get(refresh_token)
        if hit and hit[0] > now:
            return hit[1], hit[2]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{config.ACCOUNTS_URL}/auth/me", headers={"Cookie": f"refresh_token={refresh_token}"})
        if r.status_code == 200:
            cookies = r.headers.get_list("set-cookie")
            data = r.json()
            with _refresh_lock:
                _refresh_cache[refresh_token] = (now + 30, data, cookies)
                _trim(_refresh_cache)
            return data, cookies
    except Exception:
        pass
    return None, []


async def _accounts_post(path: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{config.ACCOUNTS_URL}{path}", json=payload)
        data = r.json() if r.content else {}
        if r.status_code in (200, 201):
            return data, None, r.headers.get_list("set-cookie")
        detail = data.get("detail") or "Request failed"
        if isinstance(detail, list):
            detail = "; ".join(str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in detail)
        return None, str(detail), []
    except Exception as e:
        return None, f"Accounts unreachable: {e}", []


async def accounts_login(email: str, password: str):
    return await _accounts_post("/auth/login", {"login": email, "email": email, "password": password})


async def accounts_signup(payload: dict):
    return await _accounts_post("/auth/signup", payload)


async def accounts_update_me(token: str, patch: dict):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.patch(f"{config.ACCOUNTS_URL}/auth/me", headers={"Authorization": f"Bearer {token}"}, json=patch)
        data = r.json() if r.content else {}
        if r.status_code == 200:
            _me_cache.pop(token, None)
            return data, None
        return None, str(data.get("detail") or "Update failed")
    except Exception as e:
        return None, str(e)


# ---------- local tokens ----------
def make_local_token(uid: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": str(uid), "exp": exp, "iss": "teams-local"}, config.SECRET_KEY, algorithm=config.ALGORITHM)


def _token_from(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get("access_token")


async def get_current_user(request: Request) -> Optional[dict]:
    if hasattr(request.state, "user"):
        return request.state.user
    user = None
    token = _token_from(request)
    if config.USE_ACCOUNTS:
        hit = _uid_cache.get(token) if token else None
        if hit and hit[0] > time.time():
            user = get_user_by_id(hit[1])
            if user:
                user["_access_token"] = token
                request.state.user = user
                return user
        profile = await fetch_accounts_me(token) if token else None
        if not profile and request.cookies.get("refresh_token"):
            profile, cookies = await refresh_via_accounts(request.cookies["refresh_token"])
            if cookies:
                request.state.relay_cookies = cookies  # relayed to the browser by middleware
        if profile:
            user = upsert_user(profile)
            if token and not getattr(request.state, "relay_cookies", None):
                _uid_cache[token] = (time.time() + config.ACCOUNTS_ME_CACHE_SECONDS, user["id"])
                _trim(_uid_cache)
            user["_access_token"] = token
    elif token:
        try:
            payload = jwt.decode(token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
            user = get_user_by_id(int(payload["sub"]))
        except Exception:
            user = None
    request.state.user = user
    return user


async def require_user(user=Depends(get_current_user)) -> dict:
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    return user
