"""Service-to-service org sync for the Collab Suite (Crede <-> Teams).

Protected by SUITE_SYNC_TOKEN (send `Authorization: Bearer <token>` or `X-Suite-Token`).
When the token is not configured the endpoints answer 503, so nothing is exposed by default.

GET  /api/suite/orgs?since=ISO&limit=200   orgs (any source) with members, newest changes first
POST /api/suite/orgs/upsert                 create/update an org that was created in another app
"""
from __future__ import annotations

import hmac
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app import config
from app.core import org_avatar_url, org_verified
from app.db import q, tx
from app.extract import slugify

router = APIRouter()
SOURCES = {"crede", "teams", "accounts"}
ORG_TYPES = {"company", "school", "issuer"}
ROLE_MAP = {"owner": "owner", "admin": "admin", "recruiter": "editor", "editor": "editor", "member": "member"}
RESERVED = {"new", "edit", "settings", "admin", "api", "static", "me", "teams", "collab"}


def require_suite(authorization: Optional[str] = Header(None), x_suite_token: Optional[str] = Header(None)):
    expected = config.SUITE_SYNC_TOKEN
    if not expected:
        raise HTTPException(503, "Suite sync is not configured")
    got = x_suite_token or ""
    if not got and authorization and authorization.lower().startswith("bearer "):
        got = authorization[7:].strip()
    if not got or not hmac.compare_digest(got.encode(), expected.encode()):
        raise HTTPException(401, "Invalid suite token")
    return True


def _abs(path: Optional[str]) -> Optional[str]:
    return f"{config.APP_URL}{path}" if path else None


@router.get("/api/suite/orgs")
def list_orgs(since: Optional[str] = None, limit: int = Query(200, ge=1, le=1000), _=Depends(require_suite)):
    params: dict = {"lim": limit}
    where = ""
    if since:
        try:
            params["since"] = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "since must be ISO-8601")
        where = "WHERE o.updated_at > %(since)s"
    orgs = q(f"""SELECT o.* FROM orgs o {where} ORDER BY o.updated_at DESC, o.id DESC LIMIT %(lim)s""", params)
    ids = [o["id"] for o in orgs]
    members: dict[int, list] = {i: [] for i in ids}
    if ids:
        for m in q("""SELECT m.org_id, m.role, m.status, m.title, u.accounts_id, u.email, u.username, u.full_name
                      FROM org_members m JOIN users u ON u.id=m.user_id WHERE m.org_id = ANY(%s)""", (ids,)):
            members[m["org_id"]].append({
                "accounts_id": m["accounts_id"], "email": m["email"], "username": m["username"],
                "full_name": m["full_name"], "role": m["role"], "status": m["status"], "title": m["title"],
            })
    out = []
    for o in orgs:
        out.append({
            "id": o["id"], "slug": o["slug"], "name": o["name"], "tagline": o["tagline"], "about": o["about"],
            "website": o["website"], "location": o["location"], "verified": org_verified(o),
            "avatar": _abs(org_avatar_url(o["slug"], o["avatar_key"])),
            "url": f"{config.APP_URL}/o/{o['slug']}",
            "source": o.get("source") or "teams", "external_id": o.get("external_id"),
            "org_type": o.get("org_type") or "company",
            "created_at": o["created_at"].isoformat() if o.get("created_at") else None,
            "updated_at": o["updated_at"].isoformat() if o.get("updated_at") else None,
            "members": members.get(o["id"], []),
        })
    return {"orgs": out, "count": len(out), "server_time": datetime.utcnow().isoformat() + "Z"}


class Person(BaseModel):
    accounts_id: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = ""
    username: Optional[str] = None
    role: str = "member"


class SuiteOrgIn(BaseModel):
    source: str = "crede"
    external_id: str = Field(..., min_length=1, max_length=120)
    name: str = Field(..., min_length=2, max_length=80)
    slug: Optional[str] = None
    tagline: str = Field("", max_length=140)
    about: str = Field("", max_length=4000)
    website: str = Field("", max_length=200)
    location: str = Field("", max_length=100)
    org_type: str = "company"
    verified: Optional[bool] = None
    owner: Person
    members: list[Person] = []


def _find_user(c, p: Person) -> Optional[int]:
    email = (p.email or "").lower().strip()
    acc = (p.accounts_id or "").strip()
    if not email and not acc:
        return None
    row = c.execute("SELECT id FROM users WHERE (accounts_id=%s AND %s<>'') OR (email=%s AND %s<>'') ORDER BY id LIMIT 1",
                    (acc, acc, email, email)).fetchone()
    return row["id"] if row else None


def _ensure_user(c, p: Person) -> int:
    """Owner must exist in Teams: create a lightweight mirror that their first SSO sign-in will adopt."""
    uid = _find_user(c, p)
    if uid:
        return uid
    email = (p.email or "").lower().strip()
    if not email:
        raise HTTPException(400, "owner.email is required when the owner has not used Teams yet")
    base = slugify(p.username or email.split("@")[0]).replace("-", "")[:30] or "member"
    cand, n = base, 0
    while c.execute("SELECT 1 FROM users WHERE lower(username)=%s", (cand,)).fetchone():
        n += 1
        cand = f"{base}{n}"
    return c.execute("""INSERT INTO users(accounts_id,username,full_name,email,avatar_color)
                        VALUES (NULLIF(%s,''),%s,%s,%s,'#5C5DE2') RETURNING id""",
                     ((p.accounts_id or "").strip(), cand, (p.full_name or cand).strip(), email)).fetchone()["id"]


def _free_slug(c, wanted: str, own_id: Optional[int]) -> str:
    base = slugify(wanted)[:40] or "org"
    if base in RESERVED or len(base) < 2:
        base = f"{base}-org"
    cand, n = base, 0
    while True:
        row = c.execute("SELECT id FROM orgs WHERE slug=%s", (cand,)).fetchone()
        if not row or row["id"] == own_id:
            return cand
        n += 1
        cand = f"{base}-{n}"


@router.post("/api/suite/orgs/upsert")
def upsert_org(b: SuiteOrgIn, _=Depends(require_suite)):
    source = b.source.lower().strip()
    if source not in SOURCES or source == "teams":
        raise HTTPException(400, "source must name the app the org came from (e.g. 'crede')")
    org_type = b.org_type if b.org_type in ORG_TYPES else "company"
    with tx() as c:
        owner_id = _ensure_user(c, b.owner)
        row = c.execute("SELECT id, slug FROM orgs WHERE source=%s AND external_id=%s", (source, b.external_id)).fetchone()
        created = False
        if row:
            oid = row["id"]
            c.execute("""UPDATE orgs SET name=%s, tagline=%s, about=%s, website=%s, location=%s, org_type=%s,
                         verified = COALESCE(%s, verified) WHERE id=%s""",
                      (b.name.strip(), b.tagline.strip(), b.about.strip(), b.website.strip(), b.location.strip(),
                       org_type, b.verified, oid))
            slug = row["slug"]
        else:
            slug = _free_slug(c, b.slug or b.name, None)
            oid = c.execute("""INSERT INTO orgs(slug,name,tagline,about,website,location,owner_id,source,external_id,org_type,verified)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                            (slug, b.name.strip(), b.tagline.strip(), b.about.strip(), b.website.strip(), b.location.strip(),
                             owner_id, source, b.external_id, org_type, bool(b.verified))).fetchone()["id"]
            created = True
        c.execute("""INSERT INTO org_members(org_id,user_id,role,status) VALUES (%s,%s,'owner','active')
                     ON CONFLICT (org_id,user_id) DO UPDATE SET role='owner', status='active'""", (oid, owner_id))
        added = invited = 0
        for m in b.members:
            role = ROLE_MAP.get((m.role or "member").lower(), "member")
            if role == "owner":
                role = "admin"
            uid = _find_user(c, m)
            if uid:
                if uid == owner_id:
                    continue
                c.execute("""INSERT INTO org_members(org_id,user_id,role,status) VALUES (%s,%s,%s,'active')
                             ON CONFLICT (org_id,user_id) DO UPDATE SET role=EXCLUDED.role
                             WHERE org_members.role <> 'owner'""", (oid, uid, role))
                added += 1
            elif m.email:
                c.execute("""INSERT INTO org_email_invites(org_id,email,role) VALUES (%s,%s,%s)
                             ON CONFLICT (org_id,email) DO NOTHING""", (oid, m.email.lower().strip(), role))
                invited += 1
    return {"ok": True, "id": oid, "slug": slug, "created": created, "members_added": added, "members_invited": invited,
            "url": f"{config.APP_URL}/o/{slug}"}
