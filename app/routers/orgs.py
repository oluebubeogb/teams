"""Organizations (owned by a Collab user; not Collab accounts), members, curation by consent, collections."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import config
from app.auth import avatar_src, get_current_user, get_user_by_email, require_user
from app.core import (FILE_SELECT, ROLE_RANK, VIS, cards, dec_cursor, enc_cursor, file_or_404, has_role, initials, iso,
                      is_site_admin, notify, org_avatar_url, org_cover_url, org_role, org_verified, rel, uid_of)
from app.db import q, q1, tx
from app.extract import resize_cover, resize_square, slugify
from app.storage import store

router = APIRouter()
RESERVED = {"new", "edit", "settings", "admin", "api", "static", "me", "teams", "collab"}


def org_or_404(slug: str) -> dict:
    o = q1("SELECT * FROM orgs WHERE slug=%s", (slug.lower(),))
    if not o:
        raise HTTPException(404, "Organization not found")
    return o


def org_out(o: dict, user: Optional[dict] = None, stats: bool = True) -> dict:
    out = {
        "id": o["id"], "slug": o["slug"], "name": o["name"], "tagline": o["tagline"], "about": o["about"],
        "website": o["website"], "location": o["location"], "accent": o["accent"],
        "avatar": org_avatar_url(o["slug"], o["avatar_key"]), "cover": org_cover_url(o["slug"], o["cover_key"]),
        "verified": org_verified(o), "initials": initials(o["name"]), "created_at": iso(o["created_at"]),
        "source": o.get("source") or "teams", "org_type": o.get("org_type") or "company",
    }
    if stats:
        s = q1("""SELECT (SELECT count(*) FROM org_members WHERE org_id=%(o)s AND status='active') AS members,
                         (SELECT count(*) FROM follows WHERE org_id=%(o)s) AS followers,
                         (SELECT count(*) FROM files f WHERE (f.org_id=%(o)s OR f.id IN (SELECT file_id FROM org_features WHERE org_id=%(o)s AND status='approved'))
                                  AND f.visibility='public' AND NOT f.hidden) AS files,
                         (SELECT coalesce(sum(download_count),0) FROM files f WHERE (f.org_id=%(o)s OR f.id IN (SELECT file_id FROM org_features WHERE org_id=%(o)s AND status='approved'))
                                  AND NOT f.hidden) AS downloads""", {"o": o["id"]})
        out["stats"] = {k: int(v) for k, v in s.items()}
    if user:
        out["my_role"] = org_role(o["id"], user)
        m = q1("SELECT status, role FROM org_members WHERE org_id=%s AND user_id=%s", (o["id"], user["id"]))
        out["invited"] = bool(m and m["status"] == "invited")
        out["following"] = bool(q1("SELECT 1 FROM follows WHERE follower_id=%s AND org_id=%s", (user["id"], o["id"])))
    return out


class OrgIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    slug: Optional[str] = None
    tagline: str = Field("", max_length=140)
    about: str = Field("", max_length=4000)
    website: str = Field("", max_length=200)
    location: str = Field("", max_length=100)


@router.post("/api/orgs")
def create_org(b: OrgIn, user=Depends(require_user)):
    slug = slugify(b.slug or b.name)[:40]
    if len(slug) < 2 or slug in RESERVED:
        raise HTTPException(400, "Pick a different handle")
    if q1("SELECT 1 FROM orgs WHERE slug=%s", (slug,)):
        raise HTTPException(400, "That handle is taken")
    with tx() as c:
        oid = c.execute("""INSERT INTO orgs(slug,name,tagline,about,website,location,owner_id) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (slug, b.name.strip(), b.tagline.strip(), b.about.strip(), b.website.strip(), b.location.strip(), user["id"])).fetchone()["id"]
        c.execute("INSERT INTO org_members(org_id,user_id,role,status) VALUES (%s,%s,'owner','active')", (oid, user["id"]))
        if slug in config.VERIFIED_ORGS:
            c.execute("UPDATE orgs SET verified=true WHERE id=%s", (oid,))
    return {"ok": True, "org": org_out(org_or_404(slug), user)}


class OrgPatch(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    tagline: Optional[str] = Field(None, max_length=140)
    about: Optional[str] = Field(None, max_length=4000)
    website: Optional[str] = Field(None, max_length=200)
    location: Optional[str] = Field(None, max_length=100)
    accent: Optional[str] = Field(None, max_length=9)


@router.patch("/api/orgs/{slug}")
def patch_org(slug: str, b: OrgPatch, user=Depends(require_user)):
    o = org_or_404(slug)
    if not has_role(o["id"], user, "admin"):
        raise HTTPException(403, "Admins only")
    data = {k: v.strip() for k, v in b.model_dump().items() if v is not None}
    if "accent" in data and data["accent"] and not re.match(r"^#[0-9a-fA-F]{6}$", data["accent"]):
        raise HTTPException(400, "Accent must look like #5C5DE2")
    if data:
        sets = ", ".join(f"{k}=%s" for k in data)
        with tx() as c:
            c.execute(f"UPDATE orgs SET {sets} WHERE id=%s", (*data.values(), o["id"]))
    return {"ok": True, "org": org_out(org_or_404(slug), user)}


@router.delete("/api/orgs/{slug}")
def delete_org(slug: str, user=Depends(require_user)):
    o = org_or_404(slug)
    if o["owner_id"] != user["id"]:
        raise HTTPException(403, "Only the owner can delete the organization")
    with tx() as c:
        c.execute("UPDATE files SET visibility='private' WHERE org_id=%s AND visibility='org'", (o["id"],))
        c.execute("DELETE FROM orgs WHERE id=%s", (o["id"],))
    return {"ok": True}


@router.post("/api/orgs/{slug}/avatar")
async def org_avatar(slug: str, file: UploadFile = File(...), user=Depends(require_user)):
    return await org_image(slug, "avatar", file, user)


@router.post("/api/orgs/{slug}/cover")
async def org_cover(slug: str, file: UploadFile = File(...), user=Depends(require_user)):
    return await org_image(slug, "cover", file, user)


async def org_image(slug: str, kind: str, file: UploadFile, user: dict):
    o = org_or_404(slug)
    if not has_role(o["id"], user, "admin"):
        raise HTTPException(403, "Admins only")
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Image too large (max 8 MB)")
    try:
        out = resize_square(data, 256) if kind == "avatar" else resize_cover(data)
    except Exception:
        raise HTTPException(400, "That doesn't look like an image")
    key = f"orgs/{o['id']}/{kind}-{__import__('uuid').uuid4().hex[:8]}.webp"
    store().put_bytes(key, out, "image/webp")
    old = o[f"{kind}_key"]
    with tx() as c:
        c.execute(f"UPDATE orgs SET {kind}_key=%s WHERE id=%s", (key, o["id"]))
    if old:
        store().delete(old)
    return {"ok": True, "url": (org_avatar_url if kind == "avatar" else org_cover_url)(o["slug"], key)}


@router.get("/media/org/{slug}/{kind}.webp")
def org_media(slug: str, kind: str):
    o = org_or_404(slug)
    key = o.get(f"{kind}_key") if kind in ("avatar", "cover") else None
    if not key:
        raise HTTPException(404, "No image")
    st = store()
    pub = st.public_url(key)
    if pub:
        return RedirectResponse(pub)
    p = st.local_path(key)
    if not p:
        raise HTTPException(404, "No image")
    return FileResponse(p, media_type="image/webp", headers={"Cache-Control": "public, max-age=604800"})


@router.get("/api/orgs/{slug}")
def get_org(slug: str, user=Depends(get_current_user)):
    return org_out(org_or_404(slug), user)


@router.get("/api/me/orgs")
def my_orgs(user=Depends(require_user)):
    rows = q("""SELECT o.*, m.role, m.status FROM org_members m JOIN orgs o ON o.id=m.org_id
                WHERE m.user_id=%s ORDER BY m.status, o.name""", (user["id"],))
    return {"items": [dict(org_out(r, None, stats=False), role=r["role"], status=r["status"]) for r in rows]}


# ---------------------------------------------------------------- members
@router.get("/api/orgs/{slug}/members")
def members(slug: str, user=Depends(get_current_user)):
    o = org_or_404(slug)
    admin = has_role(o["id"], user, "admin")
    rows = q("""SELECT m.role, m.status, m.title, m.created_at, u.username, u.full_name, u.avatar_url, u.avatar_color, u.email,
                       (SELECT count(*) FROM files f WHERE f.user_id=u.id AND f.org_id=m.org_id) AS files
                FROM org_members m JOIN users u ON u.id=m.user_id WHERE m.org_id=%s AND (m.status='active' OR %s)
                ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 WHEN 'editor' THEN 2 ELSE 3 END, u.full_name""",
             (o["id"], admin))
    pending_email = q("SELECT email, role, created_at FROM org_email_invites WHERE org_id=%s", (o["id"],)) if admin else []
    return {"items": [{"username": r["username"], "name": r["full_name"] or r["username"], "avatar": avatar_src(r),
                       "color": r["avatar_color"] or "#5C5DE2", "initials": initials(r["full_name"] or r["username"]),
                       "role": r["role"], "status": r["status"], "title": r["title"], "files": r["files"],
                       "email": r["email"] if admin else None, "since": rel(r["created_at"])} for r in rows],
            "email_invites": [{"email": r["email"], "role": r["role"]} for r in pending_email], "can_manage": admin}


class InviteIn(BaseModel):
    who: str = Field(..., min_length=2, max_length=200)  # username or email, comma separated allowed
    role: str = "member"
    title: str = ""


@router.post("/api/orgs/{slug}/invite")
def invite(slug: str, b: InviteIn, user=Depends(require_user)):
    o = org_or_404(slug)
    if not has_role(o["id"], user, "admin"):
        raise HTTPException(403, "Only org admins can invite")
    if b.role not in ("admin", "editor", "member"):
        raise HTTPException(400, "Role must be admin, editor or member")
    results = []
    with tx() as c:
        for who in [w.strip().lstrip("@") for w in re.split(r"[,\s;]+", b.who) if w.strip()][:50]:
            if "@" in who:
                u = c.execute("SELECT id FROM users WHERE email=%s", (who.lower(),)).fetchone()
            else:
                u = c.execute("SELECT id FROM users WHERE lower(username)=%s", (who.lower(),)).fetchone()
            if not u:
                if "@" in who:
                    c.execute("""INSERT INTO org_email_invites(org_id,email,role,invited_by) VALUES (%s,%s,%s,%s)
                                 ON CONFLICT (org_id,email) DO UPDATE SET role=EXCLUDED.role""", (o["id"], who.lower(), b.role, user["id"]))
                    results.append({"who": who, "status": "emailed_pending"})
                else:
                    results.append({"who": who, "status": "not_found"})
                continue
            ex = c.execute("SELECT status FROM org_members WHERE org_id=%s AND user_id=%s", (o["id"], u["id"])).fetchone()
            if ex:
                results.append({"who": who, "status": "already_" + ex["status"]})
                continue
            c.execute("""INSERT INTO org_members(org_id,user_id,role,status,invited_by,title) VALUES (%s,%s,%s,'invited',%s,%s)""",
                      (o["id"], u["id"], b.role, user["id"], b.title[:80]))
            notify(c, u["id"], "org_invite", user["id"], org_id=o["id"], data={"role": b.role})
            results.append({"who": who, "status": "invited"})
    return {"ok": True, "results": results}


class RespondIn(BaseModel):
    accept: bool


@router.post("/api/orgs/{slug}/respond")
def respond(slug: str, b: RespondIn, user=Depends(require_user)):
    o = org_or_404(slug)
    m = q1("SELECT status, invited_by FROM org_members WHERE org_id=%s AND user_id=%s", (o["id"], user["id"]))
    if not m or m["status"] != "invited":
        raise HTTPException(404, "No pending invite")
    with tx() as c:
        if b.accept:
            c.execute("UPDATE org_members SET status='active', created_at=now() WHERE org_id=%s AND user_id=%s", (o["id"], user["id"]))
            c.execute("INSERT INTO follows(follower_id, org_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (user["id"], o["id"]))
            notify(c, m["invited_by"], "invite_accepted", user["id"], org_id=o["id"])
        else:
            c.execute("DELETE FROM org_members WHERE org_id=%s AND user_id=%s", (o["id"], user["id"]))
    return {"ok": True, "joined": b.accept}


class MemberPatch(BaseModel):
    role: Optional[str] = None
    title: Optional[str] = None


@router.patch("/api/orgs/{slug}/members/{username}")
def patch_member(slug: str, username: str, b: MemberPatch, user=Depends(require_user)):
    o = org_or_404(slug)
    if not has_role(o["id"], user, "admin"):
        raise HTTPException(403, "Admins only")
    t = q1("SELECT u.id, m.role FROM users u JOIN org_members m ON m.user_id=u.id AND m.org_id=%s WHERE lower(u.username)=lower(%s)",
           (o["id"], username))
    if not t:
        raise HTTPException(404, "Member not found")
    if t["role"] == "owner":
        raise HTTPException(400, "The owner's role can't be changed")
    with tx() as c:
        if b.role:
            if b.role not in ("admin", "editor", "member"):
                raise HTTPException(400, "Bad role")
            if b.role == "admin" and org_role(o["id"], user) != "owner":
                raise HTTPException(403, "Only the owner can make admins")
            c.execute("UPDATE org_members SET role=%s WHERE org_id=%s AND user_id=%s", (b.role, o["id"], t["id"]))
        if b.title is not None:
            c.execute("UPDATE org_members SET title=%s WHERE org_id=%s AND user_id=%s", (b.title[:80], o["id"], t["id"]))
    return {"ok": True}


@router.delete("/api/orgs/{slug}/members/{username}")
def remove_member(slug: str, username: str, user=Depends(require_user)):
    o = org_or_404(slug)
    t = q1("SELECT u.id, m.role FROM users u JOIN org_members m ON m.user_id=u.id AND m.org_id=%s WHERE lower(u.username)=lower(%s)",
           (o["id"], username))
    if not t:
        raise HTTPException(404, "Member not found")
    leaving = t["id"] == user["id"]
    if t["role"] == "owner":
        raise HTTPException(400, "The owner can't be removed")
    if not leaving and not has_role(o["id"], user, "admin"):
        raise HTTPException(403, "Admins only")
    with tx() as c:
        c.execute("DELETE FROM org_members WHERE org_id=%s AND user_id=%s", (o["id"], t["id"]))
    return {"ok": True}


@router.delete("/api/orgs/{slug}/email-invites/{email}")
def cancel_email_invite(slug: str, email: str, user=Depends(require_user)):
    o = org_or_404(slug)
    if not has_role(o["id"], user, "admin"):
        raise HTTPException(403, "Admins only")
    with tx() as c:
        c.execute("DELETE FROM org_email_invites WHERE org_id=%s AND email=%s", (o["id"], email.lower()))
    return {"ok": True}


# ---------------------------------------------------------------- org files + curation
@router.get("/api/orgs/{slug}/files")
def org_files(slug: str, tab: str = "all", cursor: Optional[str] = None, user=Depends(get_current_user)):
    o = org_or_404(slug)
    p = {"uid": uid_of(user), "o": o["id"]}
    cur = dec_cursor(cursor)
    where = {
        "all": "(f.org_id=%(o)s OR f.id IN (SELECT file_id FROM org_features WHERE org_id=%(o)s AND status='approved'))",
        "posted": "f.org_id=%(o)s",
        "featured": "f.id IN (SELECT file_id FROM org_features WHERE org_id=%(o)s AND status='approved')",
        "top": "(f.org_id=%(o)s OR f.id IN (SELECT file_id FROM org_features WHERE org_id=%(o)s AND status='approved'))",
    }.get(tab)
    if not where:
        raise HTTPException(400, "Bad tab")
    if tab == "top":
        off = int(cur[0]) if cur else 0
        rows = q(FILE_SELECT + f" WHERE {where} AND {VIS} ORDER BY (f.download_count*2+f.like_count*3+f.view_count) DESC, f.id DESC LIMIT 25 OFFSET %(off)s",
                 dict(p, off=off))
        nxt = enc_cursor(off + 24) if len(rows) > 24 else None
    else:
        if cur:
            p.update(ts=cur[0], id=int(cur[1]))
            where += " AND (f.created_at, f.id) < (%(ts)s::timestamptz, %(id)s)"
        rows = q(FILE_SELECT + f""" LEFT JOIN org_features pin ON pin.org_id=%(o)s AND pin.file_id=f.id
                 WHERE {where} AND {VIS} ORDER BY f.created_at DESC, f.id DESC LIMIT 25""", p)
        nxt = enc_cursor(rows[23]["created_at"].isoformat(), rows[23]["id"]) if len(rows) > 24 else None
    return {"items": cards(rows[:24], user), "next": nxt}


class FeatureIn(BaseModel):
    file_id: int
    note: str = Field("", max_length=300)


@router.post("/api/orgs/{slug}/feature")
def request_feature(slug: str, b: FeatureIn, user=Depends(require_user)):
    """Org admin/editor asks to feature a member's file. The uploader must approve (consent)."""
    o = org_or_404(slug)
    if not has_role(o["id"], user, "editor"):
        raise HTTPException(403, "Only org editors and admins can curate")
    f = file_or_404(b.file_id, user)
    if f["visibility"] in ("private",) and f["user_id"] != user["id"]:
        raise HTTPException(400, "Private files can't be featured")
    own = f["user_id"] == user["id"]
    with tx() as c:
        ex = c.execute("SELECT status FROM org_features WHERE org_id=%s AND file_id=%s", (o["id"], f["id"])).fetchone()
        if ex and ex["status"] in ("approved", "pending"):
            return {"ok": True, "status": ex["status"]}
        status = "approved" if own else "pending"
        c.execute("""INSERT INTO org_features(org_id,file_id,status,requested_by,note,decided_at) VALUES (%s,%s,%s,%s,%s,%s)
                     ON CONFLICT (org_id,file_id) DO UPDATE SET status=EXCLUDED.status, requested_by=EXCLUDED.requested_by,
                     note=EXCLUDED.note, created_at=now(), decided_at=EXCLUDED.decided_at""",
                  (o["id"], f["id"], status, user["id"], b.note, None if not own else __import__("datetime").datetime.now()))
        if not own:
            notify(c, f["user_id"], "feature_request", user["id"], file_id=f["id"], org_id=o["id"], data={"note": b.note})
    return {"ok": True, "status": status}


class DecideIn(BaseModel):
    approve: bool


@router.post("/api/orgs/{slug}/feature/{file_id}/decide")
def decide_feature(slug: str, file_id: int, b: DecideIn, user=Depends(require_user)):
    o = org_or_404(slug)
    r = q1("""SELECT of.status, of.requested_by, f.user_id FROM org_features of JOIN files f ON f.id=of.file_id
              WHERE of.org_id=%s AND of.file_id=%s""", (o["id"], file_id))
    if not r or r["user_id"] != user["id"]:
        raise HTTPException(404, "No request for you to decide")
    with tx() as c:
        st = "approved" if b.approve else "declined"
        c.execute("UPDATE org_features SET status=%s, decided_at=now() WHERE org_id=%s AND file_id=%s", (st, o["id"], file_id))
        notify(c, r["requested_by"], "feature_" + st, user["id"], file_id=file_id, org_id=o["id"])
    return {"ok": True, "status": st}


@router.delete("/api/orgs/{slug}/feature/{file_id}")
def unfeature(slug: str, file_id: int, user=Depends(require_user)):
    o = org_or_404(slug)
    r = q1("SELECT f.user_id FROM org_features of JOIN files f ON f.id=of.file_id WHERE of.org_id=%s AND of.file_id=%s", (o["id"], file_id))
    if not r:
        raise HTTPException(404, "Not featured")
    if r["user_id"] != user["id"] and not has_role(o["id"], user, "editor"):
        raise HTTPException(403, "Not allowed")
    with tx() as c:
        c.execute("DELETE FROM org_features WHERE org_id=%s AND file_id=%s", (o["id"], file_id))
    return {"ok": True}


@router.get("/api/orgs/{slug}/requests")
def org_requests(slug: str, user=Depends(require_user)):
    o = org_or_404(slug)
    if not has_role(o["id"], user, "editor"):
        raise HTTPException(403, "Editors only")
    rows = q(FILE_SELECT + """ JOIN org_features of ON of.file_id=f.id AND of.org_id=%(o)s
             WHERE of.status IN ('pending','declined') ORDER BY of.created_at DESC LIMIT 100""", {"o": o["id"]})
    st = {r["file_id"]: r["status"] for r in q("SELECT file_id, status FROM org_features WHERE org_id=%s", (o["id"],))}
    return {"items": [dict(cd, feature_status=st.get(cd["id"])) for cd in cards(rows, user)]}


@router.get("/api/orgs/{slug}/candidates")
def org_candidates(slug: str, user=Depends(require_user)):
    """Members' public/link files not yet in the org - what admins can ask to feature."""
    o = org_or_404(slug)
    if not has_role(o["id"], user, "editor"):
        raise HTTPException(403, "Editors only")
    rows = q(FILE_SELECT + """ WHERE f.user_id IN (SELECT user_id FROM org_members WHERE org_id=%(o)s AND status='active')
             AND f.visibility IN ('public','link') AND f.org_id IS DISTINCT FROM %(o)s AND NOT f.hidden
             AND f.id NOT IN (SELECT file_id FROM org_features WHERE org_id=%(o)s AND status IN ('approved','pending'))
             ORDER BY f.created_at DESC LIMIT 60""", {"o": o["id"]})
    return {"items": cards(rows, user)}


# ---------------------------------------------------------------- collections
class CollectionIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=1000)
    visibility: str = "public"
    org: Optional[str] = None


def coll_visible(cl: dict, user) -> bool:
    if user and cl["user_id"] == user["id"]:
        return True
    if cl["visibility"] in ("public", "link"):
        return True
    if cl["visibility"] == "org" and cl["org_id"] and org_role(cl["org_id"], user):
        return True
    return False


def coll_out(r: dict) -> dict:
    return {"id": r["id"], "title": r["title"], "description": r["description"], "visibility": r["visibility"],
            "count": r.get("n", 0), "updated": rel(r["updated_at"]), "covers": r.get("covers") or [],
            "owner": {"username": r.get("username"), "name": r.get("full_name")},
            "org": {"slug": r.get("org_slug"), "name": r.get("org_name")} if r.get("org_slug") else None}


COLL_SELECT = """SELECT cl.*, u.username, u.full_name, o.slug AS org_slug, o.name AS org_name,
  (SELECT count(*) FROM collection_items ci WHERE ci.collection_id=cl.id) AS n,
  (SELECT array_agg('/thumb/' || x.id || '.webp?v=' || x.version || '-' || x.thumb_rev || '&s=1') FROM (SELECT f.id, f.version, f.thumb_rev FROM collection_items ci JOIN files f ON f.id=ci.file_id
     WHERE ci.collection_id=cl.id AND f.thumb_key IS NOT NULL AND f.visibility='public' ORDER BY ci.position, ci.added_at LIMIT 3) x) AS covers
FROM collections cl JOIN users u ON u.id=cl.user_id LEFT JOIN orgs o ON o.id=cl.org_id"""


@router.post("/api/collections")
def create_collection(b: CollectionIn, user=Depends(require_user)):
    org_id = None
    if b.org:
        o = org_or_404(b.org)
        if not has_role(o["id"], user, "editor"):
            raise HTTPException(403, "Editors only")
        org_id = o["id"]
    if b.visibility not in ("public", "org", "private", "link"):
        raise HTTPException(400, "Bad visibility")
    with tx() as c:
        cid = c.execute("INSERT INTO collections(user_id,org_id,title,description,visibility) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                        (user["id"], org_id, b.title.strip(), b.description.strip(), b.visibility)).fetchone()["id"]
    return {"ok": True, "id": cid}


@router.get("/api/collections")
def list_collections(user_: Optional[str] = None, org: Optional[str] = None, mine: bool = False, user=Depends(get_current_user)):
    if mine:
        if not user:
            return {"items": []}
        rows = q(COLL_SELECT + """ WHERE cl.user_id=%s OR cl.org_id IN (SELECT org_id FROM org_members WHERE user_id=%s AND status='active' AND role IN ('owner','admin','editor'))
                 ORDER BY cl.updated_at DESC""", (user["id"], user["id"]))
    elif org:
        o = org_or_404(org)
        rows = q(COLL_SELECT + " WHERE cl.org_id=%s ORDER BY cl.updated_at DESC", (o["id"],))
    elif user_:
        rows = q(COLL_SELECT + " WHERE lower(u.username)=lower(%s) AND cl.org_id IS NULL ORDER BY cl.updated_at DESC", (user_,))
    else:
        rows = q(COLL_SELECT + " WHERE cl.visibility='public' ORDER BY cl.updated_at DESC LIMIT 30")
    return {"items": [coll_out(r) for r in rows if coll_visible(r, user) and (mine or r["visibility"] != "link" or (user and r["user_id"] == user["id"]))]}


def coll_or_404(cid: int, user) -> dict:
    r = q1(COLL_SELECT + " WHERE cl.id=%s", (cid,))
    if not r or not coll_visible(r, user):
        raise HTTPException(404, "Collection not found")
    return r


def coll_can_edit(cl, user) -> bool:
    return bool(user and (cl["user_id"] == user["id"] or (cl["org_id"] and has_role(cl["org_id"], user, "editor"))))


@router.get("/api/collections/{cid}")
def get_collection(cid: int, user=Depends(get_current_user)):
    cl = coll_or_404(cid, user)
    rows = q(FILE_SELECT + f" JOIN collection_items ci ON ci.file_id=f.id AND ci.collection_id=%(c)s WHERE {VIS} ORDER BY ci.position, ci.added_at",
             {"c": cid, "uid": uid_of(user)})
    return dict(coll_out(cl), items=cards(rows, user), can_edit=coll_can_edit(cl, user))


@router.patch("/api/collections/{cid}")
def patch_collection(cid: int, b: CollectionIn, user=Depends(require_user)):
    cl = coll_or_404(cid, user)
    if not coll_can_edit(cl, user):
        raise HTTPException(403, "Not yours")
    with tx() as c:
        c.execute("UPDATE collections SET title=%s, description=%s, visibility=%s, updated_at=now() WHERE id=%s",
                  (b.title.strip(), b.description.strip(), b.visibility, cid))
    return {"ok": True}


@router.delete("/api/collections/{cid}")
def delete_collection(cid: int, user=Depends(require_user)):
    cl = coll_or_404(cid, user)
    if not coll_can_edit(cl, user):
        raise HTTPException(403, "Not yours")
    with tx() as c:
        c.execute("DELETE FROM collections WHERE id=%s", (cid,))
    return {"ok": True}


class ItemIn(BaseModel):
    file_ids: list[int]
    remove: bool = False


@router.post("/api/collections/{cid}/items")
def collection_items(cid: int, b: ItemIn, user=Depends(require_user)):
    cl = coll_or_404(cid, user)
    if not coll_can_edit(cl, user):
        raise HTTPException(403, "Not yours")
    n = 0
    with tx() as c:
        pos = c.execute("SELECT coalesce(max(position),0) p FROM collection_items WHERE collection_id=%s", (cid,)).fetchone()["p"]
        for fid in b.file_ids[:200]:
            if b.remove:
                n += c.execute("DELETE FROM collection_items WHERE collection_id=%s AND file_id=%s", (cid, fid)).rowcount
            else:
                file_or_404(fid, user)
                pos += 1
                n += c.execute("INSERT INTO collection_items(collection_id,file_id,position) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                               (cid, fid, pos)).rowcount
        c.execute("UPDATE collections SET updated_at=now() WHERE id=%s", (cid,))
    return {"ok": True, "changed": n}


@router.post("/api/admin/orgs/{slug}/verify")
def verify_org(slug: str, user=Depends(require_user)):
    if not is_site_admin(user):
        raise HTTPException(403, "Admins only")
    o = org_or_404(slug)
    with tx() as c:
        c.execute("UPDATE orgs SET verified = NOT verified WHERE id=%s", (o["id"],))
    return {"ok": True, "verified": not o["verified"]}
