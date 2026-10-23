"""Analytics with drill-down: scope (me | org:<slug> | file:<id>) -> files -> day -> sources."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_user
from app.core import can_edit, cards, get_file, has_role, is_site_admin, FILE_SELECT
from app.db import q, q1

router = APIRouter()


def _scope(scope: str, user: dict) -> tuple[str, dict, dict]:
    """Return (SQL predicate on files f, params, meta)."""
    if scope == "me":
        return "f.user_id = %(u)s", {"u": user["id"]}, {"label": "Your files", "kind": "me"}
    if scope == "site":
        if not is_site_admin(user):
            raise HTTPException(403, "Admins only")
        return "true", {}, {"label": "All of Teams", "kind": "site"}
    if scope.startswith("org:"):
        o = q1("SELECT id, slug, name FROM orgs WHERE slug=%s", (scope[4:].lower(),))
        if not o:
            raise HTTPException(404, "Organization not found")
        if not has_role(o["id"], user, "editor") and not is_site_admin(user):
            raise HTTPException(403, "Org editors and admins only")
        return ("(f.org_id = %(o)s OR f.id IN (SELECT file_id FROM org_features WHERE org_id=%(o)s AND status='approved'))",
                {"o": o["id"]}, {"label": o["name"], "kind": "org", "slug": o["slug"]})
    if scope.startswith("file:"):
        try:
            fid = int(scope[5:])
        except ValueError:
            raise HTTPException(400, "Bad scope")
        f = get_file(fid)
        if not f or not (can_edit(f, user) or is_site_admin(user)):
            raise HTTPException(404, "File not found")
        return "f.id = %(f)s", {"f": fid}, {"label": f["title"] or f["original_name"], "kind": "file", "id": fid}
    raise HTTPException(400, "scope must be me, org:<slug>, file:<id>")


@router.get("/api/analytics")
def overview(scope: str = "me", days: int = 30, user=Depends(require_user)):
    days = max(1, min(days, 365))
    pred, p, meta = _scope(scope, user)
    p.update(days=days, start=date.today() - timedelta(days=days - 1))
    p["pstart"] = p["start"] - timedelta(days=days)
    series_rows = q(f"""SELECT e.day, e.kind, count(*) n FROM events e JOIN files f ON f.id=e.file_id
                        WHERE {pred} AND e.day >= %(start)s GROUP BY 1,2""", p)
    likes = q(f"""SELECT l.created_at::date AS day, count(*) n FROM likes l JOIN files f ON f.id=l.file_id
                  WHERE {pred} AND l.created_at::date >= %(start)s GROUP BY 1""", p)
    comments = q(f"""SELECT c.created_at::date AS day, count(*) n FROM comments c JOIN files f ON f.id=c.file_id
                     WHERE {pred} AND c.created_at::date >= %(start)s GROUP BY 1""", p)
    idx = {(p["start"] + timedelta(days=i)): i for i in range(days)}
    series = {k: [0] * days for k in ("view", "download", "share", "like", "comment")}
    for r in series_rows:
        if r["day"] in idx:
            series[r["kind"]][idx[r["day"]]] = r["n"]
    for name, rows in (("like", likes), ("comment", comments)):
        for r in rows:
            if r["day"] in idx:
                series[name][idx[r["day"]]] = r["n"]
    prev = {r["kind"]: r["n"] for r in q(f"""SELECT e.kind, count(*) n FROM events e JOIN files f ON f.id=e.file_id
               WHERE {pred} AND e.day >= %(pstart)s AND e.day < %(start)s GROUP BY 1""", p)}
    totals = {k: sum(v) for k, v in series.items()}
    sources = q(f"""SELECT e.source, e.kind, count(*) n FROM events e JOIN files f ON f.id=e.file_id
                    WHERE {pred} AND e.day >= %(start)s GROUP BY 1,2 ORDER BY n DESC""", p)
    top = q(f"""SELECT f.id, count(*) FILTER (WHERE e.kind='view') views, count(*) FILTER (WHERE e.kind='download') downloads,
                  count(*) FILTER (WHERE e.kind='share') shares
                FROM files f LEFT JOIN events e ON e.file_id=f.id AND e.day >= %(start)s
                WHERE {pred} GROUP BY f.id
                ORDER BY count(*) FILTER (WHERE e.kind='download')*3 + count(*) FILTER (WHERE e.kind='view')
                         + count(*) FILTER (WHERE e.kind='share')*4 DESC, f.id DESC LIMIT 25""", p)
    life = q1(f"""SELECT count(*) files, coalesce(sum(view_count),0) views, coalesce(sum(download_count),0) downloads,
                    coalesce(sum(share_count),0) shares, coalesce(sum(like_count),0) likes, coalesce(sum(comment_count),0) comments,
                    coalesce(sum(save_count),0) saves FROM files f WHERE {pred}""", p)
    fmap = {}
    if top:
        ids = [r["id"] for r in top]
        fmap = {c["id"]: c for c in cards(q(FILE_SELECT + " WHERE f.id = ANY(%(ids)s)", {"ids": ids}), user)}
    out = {
        "scope": scope, "meta": meta, "days": days, "start": p["start"].isoformat(),
        "labels": [(p["start"] + timedelta(days=i)).isoformat() for i in range(days)],
        "series": series, "totals": totals, "previous": {k: prev.get(k, 0) for k in ("view", "download", "share")},
        "lifetime": {k: int(v) for k, v in life.items()},
        "sources": _sources(sources),
        "files": [{"id": r["id"], "title": fmap[r["id"]]["title"], "type": fmap[r["id"]]["type"], "thumb": fmap[r["id"]]["thumb"],
                   "visibility": fmap[r["id"]]["visibility"], "views": r["views"], "downloads": r["downloads"], "shares": r["shares"],
                   "likes": fmap[r["id"]]["counts"]["like"], "comments": fmap[r["id"]]["counts"]["comment"]}
                  for r in top if r["id"] in fmap],
    }
    if meta["kind"] == "me":
        out["followers"] = q1("SELECT count(*) n FROM follows WHERE user_id=%s", (user["id"],))["n"]
    if meta["kind"] == "org":
        out["members"] = q(f"""SELECT u.username, u.full_name, count(DISTINCT f.id) files,
                               count(e.id) FILTER (WHERE e.kind='download') downloads, count(e.id) FILTER (WHERE e.kind='view') views
                             FROM files f JOIN users u ON u.id=f.user_id
                             LEFT JOIN events e ON e.file_id=f.id AND e.day >= %(start)s
                             WHERE {pred} GROUP BY u.id ORDER BY downloads DESC, views DESC LIMIT 20""", p)
        out["followers"] = q1("SELECT count(*) n FROM follows WHERE org_id=%(o)s", p)["n"]
    return out


def _sources(rows) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        a = agg.setdefault(r["source"], {"source": r["source"], "view": 0, "download": 0, "share": 0, "total": 0})
        a[r["kind"]] += r["n"]
        a["total"] += r["n"]
    return sorted(agg.values(), key=lambda x: -x["total"])


@router.get("/api/analytics/day")
def day_detail(day: str, scope: str = "me", user=Depends(require_user)):
    """Drill into one day: per-file and per-source breakdown, plus hourly curve."""
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")
    pred, p, meta = _scope(scope, user)
    p["d"] = d
    files = q(f"""SELECT f.id, coalesce(nullif(f.title,''), f.original_name) title, f.file_type,
                    count(*) FILTER (WHERE e.kind='view') views, count(*) FILTER (WHERE e.kind='download') downloads,
                    count(*) FILTER (WHERE e.kind='share') shares
                  FROM events e JOIN files f ON f.id=e.file_id WHERE {pred} AND e.day=%(d)s
                  GROUP BY f.id ORDER BY count(*) DESC LIMIT 50""", p)
    sources = q(f"""SELECT e.source, e.kind, count(*) n FROM events e JOIN files f ON f.id=e.file_id
                    WHERE {pred} AND e.day=%(d)s GROUP BY 1,2""", p)
    hours = [0] * 24
    for r in q(f"""SELECT extract(hour FROM e.created_at)::int h, count(*) n FROM events e JOIN files f ON f.id=e.file_id
                   WHERE {pred} AND e.day=%(d)s GROUP BY 1""", p):
        hours[r["h"]] = r["n"]
    signed = q1(f"""SELECT count(*) FILTER (WHERE e.user_id IS NOT NULL) signed_in, count(*) FILTER (WHERE e.user_id IS NULL) anonymous
                    FROM events e JOIN files f ON f.id=e.file_id WHERE {pred} AND e.day=%(d)s""", p)
    return {"day": d.isoformat(), "scope": scope, "meta": meta, "files": files, "sources": _sources(sources), "hours": hours,
            "audience": signed}
