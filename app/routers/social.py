"""Social: comments (threaded), likes, saves, follows, notifications, reports & moderation."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import config
from app.auth import avatar_src, get_current_user, require_user
from app.core import (dec_cursor, enc_cursor, file_or_404, initials, is_site_admin, iso, notify, org_avatar_url, rel,
                      uid_of)
from app.db import q, q1, tx

router = APIRouter()
MENTION_RE = re.compile(r"(?<![\w@])@([a-z0-9][a-z0-9_-]{1,62})", re.I)

C_SELECT = """SELECT c.id, c.file_id, c.user_id, c.parent_id, c.root_id, c.depth, c.body, c.like_count, c.reply_count,
  c.deleted, c.hidden, c.created_at, c.edited_at, u.username, u.full_name, u.avatar_url, u.avatar_color,
  pu.username AS reply_to
FROM comments c JOIN users u ON u.id=c.user_id
LEFT JOIN comments p ON p.id=c.parent_id LEFT JOIN users pu ON pu.id=p.user_id"""


def c_out(r: dict, liked: set, me: int, file_owner: int) -> dict:
    gone = r["deleted"] or r["hidden"]
    return {
        "id": r["id"], "parent_id": r["parent_id"], "root_id": r["root_id"], "depth": r["depth"],
        "body": "" if gone else r["body"], "deleted": r["deleted"], "hidden": r["hidden"],
        "likes": r["like_count"], "replies": r["reply_count"], "liked": r["id"] in liked,
        "created_at": iso(r["created_at"]), "ago": rel(r["created_at"]), "edited": bool(r["edited_at"]),
        "reply_to": r["reply_to"] if r["depth"] > 1 else None,
        "author": {"username": r["username"], "name": r["full_name"] or r["username"],
                   "avatar": avatar_src(r), "color": r["avatar_color"] or "#5C5DE2", "initials": initials(r["full_name"] or r["username"])},
        "mine": r["user_id"] == me, "by_owner": r["user_id"] == file_owner,
        "can_delete": r["user_id"] == me or file_owner == me,
    }


def _liked(ids, user):
    if not user or not ids:
        return set()
    return {r["comment_id"] for r in q("SELECT comment_id FROM comment_likes WHERE user_id=%s AND comment_id = ANY(%s)", (user["id"], ids))}


@router.get("/api/files/{file_id}/comments")
def list_comments(file_id: int, cursor: Optional[str] = None, sort: str = "top", k: Optional[str] = None,
                  user=Depends(get_current_user)):
    f = file_or_404(file_id, user, k)
    me = uid_of(user)
    cur = dec_cursor(cursor)
    offset = int(cur[0]) if cur else 0
    order = "c.like_count DESC, c.reply_count DESC, c.created_at DESC" if sort == "top" else "c.created_at DESC"
    roots = q(C_SELECT + f""" WHERE c.file_id=%s AND c.parent_id IS NULL AND (NOT c.deleted OR c.reply_count>0)
                 ORDER BY (c.user_id = %s) DESC, {order} LIMIT 21 OFFSET %s""", (file_id, me, offset))
    more = len(roots) > 20
    roots = roots[:20]
    rids = [r["id"] for r in roots]
    # first 2 replies per thread (oldest first)
    reps = q(C_SELECT + """ WHERE c.root_id = ANY(%s) AND c.id IN (
               SELECT id FROM (SELECT id, row_number() OVER (PARTITION BY root_id ORDER BY created_at) rn
                               FROM comments WHERE root_id = ANY(%s)) x WHERE rn <= 2)
             ORDER BY c.created_at""", (rids, rids)) if rids else []
    liked = _liked(rids + [r["id"] for r in reps], user)
    by_root = {}
    for r in reps:
        by_root.setdefault(r["root_id"], []).append(c_out(r, liked, me, f["user_id"]))
    items = []
    for r in roots:
        o = c_out(r, liked, me, f["user_id"])
        o["thread"] = by_root.get(r["id"], [])
        o["thread_total"] = q1("SELECT count(*) n FROM comments WHERE root_id=%s", (r["id"],))["n"] if r["reply_count"] else 0
        items.append(o)
    return {"items": items, "next": enc_cursor(offset + 20) if more else None, "total": f["comment_count"],
            "allow": f["allow_comments"]}


@router.get("/api/comments/{comment_id}/thread")
def thread(comment_id: int, user=Depends(get_current_user)):
    root = q1("SELECT id, file_id, root_id FROM comments WHERE id=%s", (comment_id,))
    if not root:
        raise HTTPException(404, "Comment not found")
    f = file_or_404(root["file_id"], user)
    rid = root["root_id"] or root["id"]
    reps = q(C_SELECT + " WHERE c.root_id=%s ORDER BY c.created_at", (rid,))
    liked = _liked([r["id"] for r in reps], user)
    return {"items": [c_out(r, liked, uid_of(user), f["user_id"]) for r in reps]}


class CommentIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)
    parent_id: Optional[int] = None
    k: Optional[str] = None


@router.post("/api/files/{file_id}/comments")
def add_comment(file_id: int, body: CommentIn, user=Depends(require_user)):
    f = file_or_404(file_id, user, body.k)
    if not f["allow_comments"] and f["user_id"] != user["id"]:
        raise HTTPException(403, "Comments are turned off for this file")
    text = body.body.strip()
    if not text:
        raise HTTPException(400, "Write something first")
    with tx() as c:
        parent = None
        if body.parent_id:
            parent = c.execute("SELECT id, user_id, root_id, depth FROM comments WHERE id=%s AND file_id=%s",
                               (body.parent_id, file_id)).fetchone()
            if not parent:
                raise HTTPException(404, "Parent comment not found")
        root_id = (parent["root_id"] or parent["id"]) if parent else None
        depth = (parent["depth"] + 1) if parent else 0
        cid = c.execute("""INSERT INTO comments(file_id,user_id,parent_id,root_id,depth,body) VALUES (%s,%s,%s,%s,%s,%s)
                           RETURNING id""", (file_id, user["id"], body.parent_id, root_id, depth, text)).fetchone()["id"]
        c.execute("UPDATE files SET comment_count = comment_count + 1 WHERE id=%s", (file_id,))
        if parent:
            c.execute("UPDATE comments SET reply_count = reply_count + 1 WHERE id = ANY(%s)",
                      (list({parent["id"], root_id}),))
            notify(c, parent["user_id"], "reply", user["id"], file_id=file_id, comment_id=cid, data={"text": text[:140]})
        notified = {parent["user_id"]} if parent else set()
        if f["user_id"] not in notified:
            notify(c, f["user_id"], "comment", user["id"], file_id=file_id, comment_id=cid, data={"text": text[:140]})
            notified.add(f["user_id"])
        for name in list(dict.fromkeys(m.lower() for m in MENTION_RE.findall(text)))[:10]:
            u = c.execute("SELECT id FROM users WHERE lower(username)=%s", (name,)).fetchone()
            if u and u["id"] not in notified:
                notify(c, u["id"], "mention", user["id"], file_id=file_id, comment_id=cid, data={"text": text[:140]})
                notified.add(u["id"])
    r = q1(C_SELECT + " WHERE c.id=%s", (cid,))
    return {"ok": True, "comment": dict(c_out(r, set(), user["id"], f["user_id"]), thread=[], thread_total=0)}


@router.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int, user=Depends(require_user)):
    r = q1("SELECT c.id, c.user_id, c.file_id, c.reply_count, c.root_id, f.user_id AS owner FROM comments c JOIN files f ON f.id=c.file_id WHERE c.id=%s",
           (comment_id,))
    if not r or (r["user_id"] != user["id"] and r["owner"] != user["id"] and not is_site_admin(user)):
        raise HTTPException(404, "Comment not found")
    with tx() as c:
        if r["reply_count"]:
            c.execute("UPDATE comments SET deleted=true, body='' WHERE id=%s", (comment_id,))
        else:
            c.execute("DELETE FROM comments WHERE id=%s", (comment_id,))
            if r["root_id"]:
                c.execute("UPDATE comments SET reply_count = GREATEST(reply_count - 1, 0) WHERE id=%s", (r["root_id"],))
        c.execute("UPDATE files SET comment_count = GREATEST(comment_count - 1, 0) WHERE id=%s", (r["file_id"],))
    return {"ok": True}


@router.post("/api/comments/{comment_id}/like")
def like_comment(comment_id: int, user=Depends(require_user)):
    r = q1("SELECT id, user_id, file_id FROM comments WHERE id=%s", (comment_id,))
    if not r:
        raise HTTPException(404, "Comment not found")
    file_or_404(r["file_id"], user)
    with tx() as c:
        if c.execute("DELETE FROM comment_likes WHERE comment_id=%s AND user_id=%s", (comment_id, user["id"])).rowcount:
            n = c.execute("UPDATE comments SET like_count=GREATEST(like_count-1,0) WHERE id=%s RETURNING like_count", (comment_id,)).fetchone()
            return {"liked": False, "likes": n["like_count"]}
        c.execute("INSERT INTO comment_likes(comment_id,user_id) VALUES (%s,%s)", (comment_id, user["id"]))
        n = c.execute("UPDATE comments SET like_count=like_count+1 WHERE id=%s RETURNING like_count", (comment_id,)).fetchone()
        notify(c, r["user_id"], "comment_like", user["id"], file_id=r["file_id"], comment_id=comment_id)
        return {"liked": True, "likes": n["like_count"]}


def _toggle(table: str, col: str, file_id: int, user: dict, notify_kind: Optional[str] = None):
    f = file_or_404(file_id, user)
    with tx() as c:
        if c.execute(f"DELETE FROM {table} WHERE file_id=%s AND user_id=%s", (file_id, user["id"])).rowcount:
            n = c.execute(f"UPDATE files SET {col}=GREATEST({col}-1,0) WHERE id=%s RETURNING {col}", (file_id,)).fetchone()
            return {"on": False, "count": n[col]}
        c.execute(f"INSERT INTO {table}(file_id,user_id) VALUES (%s,%s)", (file_id, user["id"]))
        n = c.execute(f"UPDATE files SET {col}={col}+1 WHERE id=%s RETURNING {col}", (file_id,)).fetchone()
        if notify_kind:
            notify(c, f["user_id"], notify_kind, user["id"], file_id=file_id)
        return {"on": True, "count": n[col]}


@router.post("/api/files/{file_id}/like")
def like(file_id: int, user=Depends(require_user)):
    return _toggle("likes", "like_count", file_id, user, "like")


@router.post("/api/files/{file_id}/save")
def save(file_id: int, user=Depends(require_user)):
    return _toggle("saves", "save_count", file_id, user)


class FollowIn(BaseModel):
    user: Optional[str] = None
    org: Optional[str] = None
    topic: Optional[str] = None


@router.post("/api/follow")
def follow(b: FollowIn, user=Depends(require_user)):
    with tx() as c:
        if b.user:
            t = c.execute("SELECT id FROM users WHERE lower(username)=lower(%s)", (b.user,)).fetchone()
            col, kind = "user_id", "follow"
        elif b.org:
            t = c.execute("SELECT id, owner_id FROM orgs WHERE slug=%s", (b.org.lower(),)).fetchone()
            col, kind = "org_id", "org_follow"
        elif b.topic:
            t = c.execute("SELECT id FROM topics WHERE slug=%s", (b.topic.lower(),)).fetchone()
            col, kind = "topic_id", None
        else:
            raise HTTPException(400, "Nothing to follow")
        if not t:
            raise HTTPException(404, "Not found")
        if col == "user_id" and t["id"] == user["id"]:
            raise HTTPException(400, "You can't follow yourself")
        if c.execute(f"DELETE FROM follows WHERE follower_id=%s AND {col}=%s", (user["id"], t["id"])).rowcount:
            on = False
        else:
            c.execute(f"INSERT INTO follows(follower_id,{col}) VALUES (%s,%s)", (user["id"], t["id"]))
            on = True
            if kind == "follow":
                notify(c, t["id"], "follow", user["id"])
            elif kind == "org_follow":
                notify(c, t["owner_id"], "org_follow", user["id"], org_id=t["id"])
        n = c.execute(f"SELECT count(*) n FROM follows WHERE {col}=%s", (t["id"],)).fetchone()["n"]
    return {"on": on, "followers": n}


# ---------------------------------------------------------------- notifications
N_TEXT = {
    "comment": "commented on", "reply": "replied to your comment on", "mention": "mentioned you on",
    "like": "liked", "comment_like": "liked your comment on", "follow": "started following you",
    "org_follow": "followed", "org_invite": "invited you to join", "feature_request": "wants to feature",
    "feature_approved": "approved featuring", "feature_declined": "declined featuring", "org_post": "posted to",
    "collab_added": "added you as a collaborator on", "new_version": "uploaded a new version of",
    "invite_accepted": "joined", "moderation": "Moderators hid",
}


@router.get("/api/notifications")
def notifications(cursor: Optional[str] = None, user=Depends(require_user)):
    cur = dec_cursor(cursor)
    before = int(cur[0]) if cur else 2**62
    rows = q("""SELECT n.*, a.username, a.full_name, a.avatar_url, a.avatar_color, f.title AS file_title, f.thumb_key, f.thumb_rev,
                       f.version AS file_version, o.slug AS org_slug, o.name AS org_name, o.avatar_key,
                       m.status AS member_status, of.status AS feature_status
                FROM notifications n LEFT JOIN users a ON a.id=n.actor_id LEFT JOIN files f ON f.id=n.file_id
                LEFT JOIN orgs o ON o.id=n.org_id
                LEFT JOIN org_members m ON m.org_id=n.org_id AND m.user_id=n.user_id AND n.kind='org_invite'
                LEFT JOIN org_features of ON of.org_id=n.org_id AND of.file_id=n.file_id AND n.kind='feature_request'
                WHERE n.user_id=%s AND n.id < %s ORDER BY n.id DESC LIMIT 31""", (user["id"], before))
    more = len(rows) > 30
    items = []
    for r in rows[:30]:
        items.append({
            "id": r["id"], "kind": r["kind"], "verb": N_TEXT.get(r["kind"], r["kind"]), "read": bool(r["read_at"]),
            "ago": rel(r["created_at"]), "data": r["data"],
            "actor": {"username": r["username"], "name": r["full_name"] or r["username"], "avatar": avatar_src(r),
                      "color": r["avatar_color"] or "#5C5DE2", "initials": initials(r["full_name"] or r["username"] or "?")} if r["username"] else None,
            "file": {"id": r["file_id"], "title": r["file_title"],
                     "thumb": f"/thumb/{r['file_id']}.webp?v={r['file_version']}-{r['thumb_rev']}&s=1" if r["thumb_key"] else None} if r["file_id"] else None,
            "org": {"slug": r["org_slug"], "name": r["org_name"], "avatar": org_avatar_url(r["org_slug"], r["avatar_key"])} if r["org_slug"] else None,
            "comment_id": r["comment_id"],
            "pending": (r["kind"] == "org_invite" and r["member_status"] == "invited") or
                       (r["kind"] == "feature_request" and r["feature_status"] == "pending"),
        })
    return {"items": items, "next": enc_cursor(rows[29]["id"]) if more else None,
            "unread": q1("SELECT count(*) n FROM notifications WHERE user_id=%s AND read_at IS NULL", (user["id"],))["n"]}


@router.get("/api/notifications/count")
def notif_count(user=Depends(get_current_user)):
    if not user:
        return {"unread": 0}
    return {"unread": q1("SELECT count(*) n FROM notifications WHERE user_id=%s AND read_at IS NULL", (user["id"],))["n"]}


@router.post("/api/notifications/read")
def notif_read(user=Depends(require_user)):
    with tx() as c:
        c.execute("UPDATE notifications SET read_at=now() WHERE user_id=%s AND read_at IS NULL", (user["id"],))
    return {"ok": True}


# ---------------------------------------------------------------- reports & moderation
class ReportIn(BaseModel):
    file_id: Optional[int] = None
    comment_id: Optional[int] = None
    reason: str = Field(..., max_length=60)
    details: str = Field("", max_length=1000)


@router.post("/api/report")
def report(b: ReportIn, user=Depends(require_user)):
    if not (b.file_id or b.comment_id):
        raise HTTPException(400, "Nothing to report")
    with tx() as c:
        if c.execute("""SELECT 1 FROM reports WHERE reporter_id=%s AND file_id IS NOT DISTINCT FROM %s
                        AND comment_id IS NOT DISTINCT FROM %s AND status='open'""", (user["id"], b.file_id, b.comment_id)).fetchone():
            return {"ok": True, "duplicate": True}
        c.execute("INSERT INTO reports(file_id,comment_id,reporter_id,reason,details) VALUES (%s,%s,%s,%s,%s)",
                  (b.file_id, b.comment_id, user["id"], b.reason, b.details))
        # auto-hide public content after N independent reports, pending review
        if b.file_id:
            n = c.execute("SELECT count(DISTINCT reporter_id) n FROM reports WHERE file_id=%s AND status='open'", (b.file_id,)).fetchone()["n"]
            if n >= config.REPORT_AUTOHIDE_THRESHOLD:
                c.execute("UPDATE files SET hidden=true WHERE id=%s", (b.file_id,))
        if b.comment_id:
            n = c.execute("SELECT count(DISTINCT reporter_id) n FROM reports WHERE comment_id=%s AND status='open'", (b.comment_id,)).fetchone()["n"]
            if n >= config.REPORT_AUTOHIDE_THRESHOLD:
                c.execute("UPDATE comments SET hidden=true WHERE id=%s", (b.comment_id,))
    return {"ok": True}


def require_admin(user=Depends(require_user)):
    if not is_site_admin(user):
        raise HTTPException(403, "Admins only")
    return user


@router.get("/api/admin/reports")
def admin_reports(status: str = "open", user=Depends(require_admin)):
    rows = q("""SELECT r.*, u.username AS reporter, f.title AS file_title, f.hidden AS file_hidden, f.user_id AS file_owner,
                       cm.body AS comment_body, cm.hidden AS comment_hidden
                FROM reports r LEFT JOIN users u ON u.id=r.reporter_id LEFT JOIN files f ON f.id=r.file_id
                LEFT JOIN comments cm ON cm.id=r.comment_id WHERE r.status=%s ORDER BY r.created_at DESC LIMIT 200""", (status,))
    return {"items": [dict(r, created_at=iso(r["created_at"]), resolved_at=iso(r["resolved_at"]), ago=rel(r["created_at"])) for r in rows]}


class ModIn(BaseModel):
    action: str  # dismiss | hide | unhide


@router.post("/api/admin/reports/{rid}")
def admin_act(rid: int, b: ModIn, user=Depends(require_admin)):
    r = q1("SELECT * FROM reports WHERE id=%s", (rid,))
    if not r:
        raise HTTPException(404, "Report not found")
    with tx() as c:
        hide = b.action == "hide"
        if b.action in ("hide", "unhide"):
            if r["file_id"]:
                c.execute("UPDATE files SET hidden=%s WHERE id=%s", (hide, r["file_id"]))
                if hide:
                    owner = c.execute("SELECT user_id FROM files WHERE id=%s", (r["file_id"],)).fetchone()
                    if owner:
                        notify(c, owner["user_id"], "moderation", None, file_id=r["file_id"])
            if r["comment_id"]:
                c.execute("UPDATE comments SET hidden=%s WHERE id=%s", (hide, r["comment_id"]))
        status = "actioned" if b.action == "hide" else "dismissed"
        c.execute("""UPDATE reports SET status=%s, resolved_by=%s, resolved_at=now()
                     WHERE status='open' AND file_id IS NOT DISTINCT FROM %s AND comment_id IS NOT DISTINCT FROM %s""",
                  (status, user["id"], r["file_id"], r["comment_id"]))
    return {"ok": True}
