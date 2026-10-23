"""Discovery: feeds (For you / Following / Latest), top lists, unified search, suggest, topics, people."""
from __future__ import annotations

import html
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.auth import avatar_src, get_current_user
from app.core import (FILE_SELECT, VIS, cards, dec_cursor, enc_cursor, initials, org_avatar_url, org_cover_url,
                      org_verified, rel, uid_of)
from app.db import q, q1
from app.extract import slugify

router = APIRouter()
PAGE = 12

# engagement score with time decay (hours); used by For You and Trending
ENGAGE = "(1 + f.view_count*0.2 + f.download_count*1.0 + f.like_count*1.5 + f.comment_count*2.0 + f.share_count*2.5 + f.save_count*1.5)"


def _topic_filter(topic: Optional[str], p: dict) -> str:
    if not topic:
        return ""
    p["topic"] = topic.lower()
    return " AND f.id IN (SELECT ft.file_id FROM file_topics ft JOIN topics t ON t.id=ft.topic_id WHERE t.slug=%(topic)s)"


@router.get("/api/feed")
def feed(tab: str = "foryou", cursor: Optional[str] = None, topic: Optional[str] = None, limit: int = PAGE,
         user=Depends(get_current_user)):
    limit = max(1, min(limit, 30))
    uid = uid_of(user)
    p = {"uid": uid, "lim": limit + 1, "seed": f"{uid}-{date.today().isoformat()}"}
    cur = dec_cursor(cursor)
    tf = _topic_filter(topic, p)
    base = f"WHERE {VIS} AND f.visibility <> 'link' AND NOT f.hidden {tf}"
    if tab == "latest" or (tab == "following" and not user):
        if cur:
            p.update(ts=cur[0], id=int(cur[1]))
            base += " AND (f.created_at, f.id) < (%(ts)s::timestamptz, %(id)s)"
        rows = q(FILE_SELECT + f" {base} ORDER BY f.created_at DESC, f.id DESC LIMIT %(lim)s", p)
        nxt = enc_cursor(rows[limit - 1]["created_at"].isoformat(), rows[limit - 1]["id"]) if len(rows) > limit else None
    elif tab == "following":
        if cur:
            p.update(ts=cur[0], id=int(cur[1]))
            base += " AND (f.created_at, f.id) < (%(ts)s::timestamptz, %(id)s)"
        rows = q(FILE_SELECT + f""" {base} AND f.user_id <> %(uid)s AND (
              f.user_id IN (SELECT user_id FROM follows WHERE follower_id=%(uid)s AND user_id IS NOT NULL)
           OR f.org_id IN (SELECT org_id FROM follows WHERE follower_id=%(uid)s AND org_id IS NOT NULL)
           OR f.id IN (SELECT of.file_id FROM org_features of JOIN follows fl ON fl.org_id=of.org_id
                        WHERE fl.follower_id=%(uid)s AND of.status='approved')
           OR f.id IN (SELECT ft.file_id FROM file_topics ft JOIN follows fl ON fl.topic_id=ft.topic_id WHERE fl.follower_id=%(uid)s))
           ORDER BY f.created_at DESC, f.id DESC LIMIT %(lim)s""", p)
        nxt = enc_cursor(rows[limit - 1]["created_at"].isoformat(), rows[limit - 1]["id"]) if len(rows) > limit else None
    elif tab == "foryou":
        off = int(cur[0]) if cur else 0
        p["off"] = off
        # affinity: followed authors/orgs/topics and topics of things I liked or saved
        rows = q(FILE_SELECT + f"""
          LEFT JOIN LATERAL (SELECT
              (CASE WHEN EXISTS (SELECT 1 FROM follows WHERE follower_id=%(uid)s AND user_id=f.user_id) THEN 2.0 ELSE 0 END)
            + (CASE WHEN f.org_id IS NOT NULL AND EXISTS (SELECT 1 FROM follows WHERE follower_id=%(uid)s AND org_id=f.org_id) THEN 1.5 ELSE 0 END)
            + coalesce((SELECT least(sum(ft.weight), 6) * 0.35 FROM file_topics ft WHERE ft.file_id=f.id AND ft.topic_id IN (
                  SELECT topic_id FROM follows WHERE follower_id=%(uid)s AND topic_id IS NOT NULL
                  UNION SELECT ft2.topic_id FROM likes l JOIN file_topics ft2 ON ft2.file_id=l.file_id WHERE l.user_id=%(uid)s
                  UNION SELECT ft3.topic_id FROM saves s JOIN file_topics ft3 ON ft3.file_id=s.file_id WHERE s.user_id=%(uid)s)), 0)
            AS aff) a ON true
          {base}
          ORDER BY (ln({ENGAGE}) + 1.0) * (1 + a.aff)
                   / power(extract(epoch FROM now() - f.created_at)/3600 + 2, 0.55)
                   * (0.85 + 0.3 * ('x' || substr(md5(%(seed)s || f.id), 1, 6))::bit(24)::int / 16777215.0)
                   * (CASE WHEN f.user_id = %(uid)s THEN 0.35 ELSE 1 END)
                   * (CASE WHEN EXISTS (SELECT 1 FROM events e WHERE e.file_id=f.id AND e.user_id=%(uid)s AND e.kind='view'
                                        AND e.day > current_date - 3) THEN 0.5 ELSE 1 END) DESC, f.id DESC
          LIMIT %(lim)s OFFSET %(off)s""", p)
        nxt = enc_cursor(off + limit) if len(rows) > limit else None
    else:
        raise HTTPException(400, "Unknown feed")
    return {"items": cards(rows[:limit], user), "next": nxt, "tab": tab}


# ---------------------------------------------------------------- top lists
WINDOWS = {"day": 1, "week": 7, "month": 30, "year": 365, "all": None}


@router.get("/api/top")
def top(kind: str = "trending", window: str = "week", topic: Optional[str] = None, limit: int = 20,
        user=Depends(get_current_user)):
    if window not in WINDOWS:
        raise HTTPException(400, "window must be day, week, month, year or all")
    days = WINDOWS[window]
    limit = max(1, min(limit, 50))
    p = {"uid": uid_of(user), "lim": limit, "days": days or 36500}
    tf = _topic_filter(topic, p)
    pub = f"f.visibility='public' AND NOT f.hidden {tf}"
    if kind == "trending":
        # time-decayed engagement over the last 14 days (half-life ~ 2 days)
        rows = q(FILE_SELECT + f""" JOIN (
              SELECT file_id, sum(w * exp(-extract(epoch FROM now()-ts)/172800.0)) AS score FROM (
                SELECT file_id, created_at ts, CASE kind WHEN 'view' THEN 1 WHEN 'download' THEN 3 ELSE 4 END w
                  FROM events WHERE created_at > now() - interval '14 days'
                UNION ALL SELECT file_id, created_at, 3 FROM likes WHERE created_at > now() - interval '14 days'
                UNION ALL SELECT file_id, created_at, 3 FROM saves WHERE created_at > now() - interval '14 days'
                UNION ALL SELECT file_id, created_at, 4 FROM comments WHERE created_at > now() - interval '14 days') x
              GROUP BY file_id) s ON s.file_id=f.id
            WHERE {pub} ORDER BY s.score DESC, f.id DESC LIMIT %(lim)s""", p)
        return {"kind": kind, "items": [dict(c, rank=i + 1) for i, c in enumerate(cards(rows, user))]}
    if kind == "downloads":
        if days:
            rows = q(FILE_SELECT + f""" JOIN (SELECT file_id, count(*) n FROM events WHERE kind='download'
                       AND day > current_date - %(days)s GROUP BY file_id) s ON s.file_id=f.id
                     WHERE {pub} ORDER BY s.n DESC, f.download_count DESC LIMIT %(lim)s""", p)
            ns = {r["file_id"]: r["n"] for r in q("""SELECT file_id, count(*) n FROM events WHERE kind='download'
                   AND day > current_date - %(days)s AND file_id = ANY(%(ids)s) GROUP BY file_id""", dict(p, ids=[r["id"] for r in rows]))}
        else:
            rows = q(FILE_SELECT + f" WHERE {pub} AND f.download_count > 0 ORDER BY f.download_count DESC, f.id DESC LIMIT %(lim)s", p)
            ns = {r["id"]: r["download_count"] for r in rows}
        return {"kind": kind, "window": window,
                "items": [dict(c, rank=i + 1, metric=ns.get(c["id"], 0)) for i, c in enumerate(cards(rows, user))]}
    if kind == "creators":
        rows = q(f"""SELECT u.id, u.username, u.full_name, u.avatar_url, u.avatar_color, u.bio,
                       count(DISTINCT f.id) files, coalesce(sum(d.n),0) downloads,
                       (SELECT count(*) FROM follows WHERE user_id=u.id) followers
                     FROM users u JOIN files f ON f.user_id=u.id AND {pub}
                     LEFT JOIN (SELECT file_id, count(*) n FROM events WHERE kind IN ('download','share')
                                AND day > current_date - %(days)s GROUP BY file_id) d ON d.file_id=f.id
                     GROUP BY u.id ORDER BY coalesce(sum(d.n),0) * 3 + sum(f.like_count) * 2 + (SELECT count(*) FROM follows WHERE user_id=u.id) * 4 DESC,
                       count(DISTINCT f.id) DESC LIMIT %(lim)s""", p)
        return {"kind": kind, "window": window, "items": [dict(person_out(r, user), rank=i + 1, metric=int(r["downloads"]))
                                                           for i, r in enumerate(rows)]}
    if kind == "orgs":
        rows = q(f"""SELECT o.*, count(DISTINCT f.id) files, coalesce(sum(d.n),0) downloads,
                       (SELECT count(*) FROM follows WHERE org_id=o.id) followers,
                       (SELECT count(*) FROM org_members WHERE org_id=o.id AND status='active') members
                     FROM orgs o JOIN files f ON (f.org_id=o.id OR f.id IN (SELECT file_id FROM org_features WHERE org_id=o.id AND status='approved')) AND {pub}
                     LEFT JOIN (SELECT file_id, count(*) n FROM events WHERE kind IN ('download','share')
                                AND day > current_date - %(days)s GROUP BY file_id) d ON d.file_id=f.id
                     GROUP BY o.id ORDER BY coalesce(sum(d.n),0) * 3 + (SELECT count(*) FROM follows WHERE org_id=o.id) * 4 + count(DISTINCT f.id) DESC
                     LIMIT %(lim)s""", p)
        return {"kind": kind, "window": window, "items": [dict(org_mini(r), rank=i + 1, metric=int(r["downloads"]),
                                                                files=r["files"], followers=r["followers"], members=r["members"])
                                                           for i, r in enumerate(rows)]}
    if kind == "topics":
        return {"kind": kind, "items": trending_topics(limit)}
    raise HTTPException(400, "Unknown list")


def trending_topics(limit=20):
    rows = q("""SELECT t.slug, t.name, count(DISTINCT ft.file_id) files,
                  coalesce(sum(f.download_count + f.like_count*2 + f.view_count*0.1),0) heat
                FROM topics t JOIN file_topics ft ON ft.topic_id=t.id JOIN files f ON f.id=ft.file_id
                WHERE f.visibility='public' AND NOT f.hidden
                GROUP BY t.id HAVING count(DISTINCT ft.file_id) > 0
                ORDER BY count(DISTINCT ft.file_id) * 2 + coalesce(sum(f.download_count + f.like_count*2),0) DESC LIMIT %s""", (limit,))
    return [{"slug": r["slug"], "name": r["name"], "files": r["files"]} for r in rows]


def person_out(r: dict, user=None) -> dict:
    name = r.get("full_name") or r["username"]
    return {"username": r["username"], "name": name, "avatar": avatar_src(r), "color": r.get("avatar_color") or "#5C5DE2",
            "initials": initials(name), "bio": (r.get("bio") or "")[:160], "files": r.get("files"), "followers": r.get("followers")}


def org_mini(r: dict) -> dict:
    return {"slug": r["slug"], "name": r["name"], "tagline": r.get("tagline"), "avatar": org_avatar_url(r["slug"], r.get("avatar_key")),
            "cover": org_cover_url(r["slug"], r.get("cover_key")), "initials": initials(r["name"]),
            "verified": org_verified(r), "accent": r.get("accent")}


# ---------------------------------------------------------------- search
S_OPEN, S_CLOSE = "\x02", "\x03"
HL = """ts_headline('english', left(coalesce(f.extracted_text,''), 60000), websearch_to_tsquery('english', %(q)s),
          'StartSel=' || chr(2) || ', StopSel=' || chr(3) || ', MaxWords=28, MinWords=12, ShortWord=2, MaxFragments=2, FragmentDelimiter=" … "') AS hl_text,
        ts_headline('english', f.title, websearch_to_tsquery('english', %(q)s),
          'StartSel=' || chr(2) || ', StopSel=' || chr(3) || ', HighlightAll=true') AS hl_title"""


def mark(s: Optional[str]) -> str:
    return html.escape(s or "").replace(S_OPEN, "<mark>").replace(S_CLOSE, "</mark>")


def _file_search(term: str, user, limit: int, offset: int = 0, topic: Optional[str] = None, type_: Optional[str] = None):
    p = {"uid": uid_of(user), "q": term, "lim": limit + 1, "off": offset, "like": term.lower()}
    tf = _topic_filter(topic, p)
    if type_:
        p["type"] = type_.lower()
        tf += " AND f.file_type = %(type)s"
    # rank cheaply first, then build headlines only for the page we return
    rows = q(f"""WITH hits AS (
          SELECT f.id, ts_rank_cd(f.search_vec, websearch_to_tsquery('english', %(q)s), 32)
                 + similarity(lower(f.title), %(like)s) * 0.6 + ln(1 + f.download_count + f.like_count) * 0.02 AS score,
                 f.search_vec @@ websearch_to_tsquery('english', %(q)s) AS fts_hit
          FROM files f
          WHERE {VIS} AND (f.visibility <> 'link' OR f.user_id = %(uid)s
                 OR EXISTS (SELECT 1 FROM file_collaborators fc WHERE fc.file_id=f.id AND fc.user_id=%(uid)s))
            AND (f.search_vec @@ websearch_to_tsquery('english', %(q)s) OR lower(f.title) %% %(like)s
                 OR lower(f.title) LIKE '%%' || %(like)s || '%%') {tf}
          ORDER BY score DESC, f.id DESC LIMIT %(lim)s OFFSET %(off)s)
        {FILE_SELECT.replace("SELECT f.id,", "SELECT h.score, h.fts_hit, " + HL + ", f.id,", 1)}
        JOIN hits h ON h.id = f.id ORDER BY h.score DESC, f.id DESC""", p)
    more = len(rows) > limit
    out = []
    for r, c in zip(rows[:limit], cards(rows[:limit], user)):
        hl = r["hl_text"] if (r["fts_hit"] and S_OPEN in (r["hl_text"] or "")) else ""
        c["headline"] = mark(hl) if hl else ""
        c["title_html"] = mark(r["hl_title"]) if S_OPEN in (r["hl_title"] or "") else html.escape(c["title"])
        c["match_in"] = "content" if hl and S_OPEN not in (r["hl_title"] or "") else "title"
        out.append(c)
    return out, more


@router.get("/api/search")
def search(q: str = "", type: str = "all", cursor: Optional[str] = None, topic: Optional[str] = None,
           ftype: Optional[str] = None, user=Depends(get_current_user)):
    term = q.strip()[:200]
    if not term:
        return {"q": "", "files": [], "people": [], "orgs": [], "topics": []}
    cur = dec_cursor(cursor)
    off = int(cur[0]) if cur else 0
    res = {"q": term}
    if type in ("all", "files"):
        lim = 8 if type == "all" else 20
        files, more = _file_search(term, user, lim, off, topic, ftype)
        res["files"] = files
        res["next"] = enc_cursor(off + lim) if more and type == "files" else None
        res["more_files"] = more
    if type in ("all", "people"):
        res["people"] = search_people(term, 6 if type == "all" else 30)
    if type in ("all", "orgs"):
        res["orgs"] = search_orgs(term, 6 if type == "all" else 30)
    if type in ("all", "topics"):
        res["topics"] = search_topics(term, 10 if type == "all" else 40)
    return res


def search_people(term: str, limit: int):
    from app.db import q as dbq
    like = term.lower().lstrip("@")
    rows = dbq("""SELECT u.username, u.full_name, u.avatar_url, u.avatar_color, u.bio,
                    (SELECT count(*) FROM files f WHERE f.user_id=u.id AND f.visibility='public' AND NOT f.hidden) files,
                    (SELECT count(*) FROM follows WHERE user_id=u.id) followers
                  FROM users u WHERE lower(u.full_name || ' ' || u.username) %% %(l)s OR lower(u.username) LIKE %(p)s
                     OR lower(u.full_name) LIKE %(c)s
                  ORDER BY (lower(u.username) = %(l)s) DESC, similarity(lower(u.full_name || ' ' || u.username), %(l)s) DESC LIMIT %(n)s""",
               {"l": like, "p": like + "%", "c": "%" + like + "%", "n": limit})
    return [person_out(r) for r in rows]


def search_orgs(term: str, limit: int):
    from app.db import q as dbq
    like = term.lower()
    rows = dbq("""SELECT o.*, (SELECT count(*) FROM follows WHERE org_id=o.id) followers FROM orgs o
                  WHERE lower(o.name || ' ' || o.slug || ' ' || o.tagline) %% %(l)s OR lower(o.name) LIKE %(c)s OR o.slug LIKE %(c)s
                  ORDER BY similarity(lower(o.name || ' ' || o.slug), %(l)s) DESC LIMIT %(n)s""",
               {"l": like, "c": "%" + like + "%", "n": limit})
    return [dict(org_mini(r), followers=r["followers"]) for r in rows]


def search_topics(term: str, limit: int):
    from app.db import q as dbq
    like = term.lower()
    rows = dbq("""SELECT t.slug, t.name, (SELECT count(*) FROM file_topics ft JOIN files f ON f.id=ft.file_id
                    WHERE ft.topic_id=t.id AND f.visibility='public' AND NOT f.hidden) files
                  FROM topics t WHERE lower(t.name) %% %(l)s OR lower(t.name) LIKE %(c)s
                  ORDER BY (lower(t.name)=%(l)s) DESC, similarity(lower(t.name), %(l)s) DESC LIMIT %(n)s""",
               {"l": like, "c": "%" + like + "%", "n": limit})
    return [{"slug": r["slug"], "name": r["name"], "files": r["files"]} for r in rows if r["files"]]


@router.get("/api/suggest")
def suggest(q: str = "", user=Depends(get_current_user)):
    term = q.strip()[:80]
    if len(term) < 1:
        return {"topics": trending_topics(8), "files": [], "people": [], "orgs": []}
    p = {"uid": uid_of(user), "l": term.lower(), "c": "%" + term.lower() + "%"}
    files = q_rows(f"""SELECT f.id, f.title, f.file_type FROM files f WHERE {VIS} AND f.visibility <> 'link'
                     AND (lower(f.title) LIKE %(c)s OR lower(f.title) %% %(l)s)
                     ORDER BY similarity(lower(f.title), %(l)s) DESC, f.download_count DESC LIMIT 5""", p)
    return {"files": [{"id": r["id"], "title": r["title"], "type": r["file_type"]} for r in files],
            "people": search_people(term, 4), "orgs": search_orgs(term, 3), "topics": search_topics(term, 5)}


def q_rows(sql, p):
    from app.db import q as dbq
    return dbq(sql, p)


# ---------------------------------------------------------------- topics
@router.get("/api/topics")
def topics(limit: int = 40):
    return {"items": trending_topics(min(limit, 100))}


@router.get("/api/topics/{slug}")
def topic(slug: str, user=Depends(get_current_user)):
    t = q1("SELECT * FROM topics WHERE slug=%s", (slugify(slug),))
    if not t:
        raise HTTPException(404, "Topic not found")
    st = q1("""SELECT count(DISTINCT f.id) files, count(DISTINCT f.user_id) creators, coalesce(sum(f.download_count),0) downloads
               FROM file_topics ft JOIN files f ON f.id=ft.file_id WHERE ft.topic_id=%s AND f.visibility='public' AND NOT f.hidden""", (t["id"],))
    related = q("""SELECT t2.slug, t2.name, count(*) n FROM file_topics a JOIN file_topics b ON b.file_id=a.file_id AND b.topic_id<>a.topic_id
                   JOIN topics t2 ON t2.id=b.topic_id JOIN files f ON f.id=a.file_id
                   WHERE a.topic_id=%s AND f.visibility='public' AND NOT f.hidden GROUP BY t2.id ORDER BY n DESC LIMIT 10""", (t["id"],))
    following = bool(user and q1("SELECT 1 FROM follows WHERE follower_id=%s AND topic_id=%s", (user["id"], t["id"])))
    followers = q1("SELECT count(*) n FROM follows WHERE topic_id=%s", (t["id"],))["n"]
    return {"slug": t["slug"], "name": t["name"], "stats": {k: int(v) for k, v in st.items()}, "followers": followers,
            "following": following, "related": [{"slug": r["slug"], "name": r["name"]} for r in related]}


# ---------------------------------------------------------------- people
@router.get("/api/users/{username}")
def profile(username: str, user=Depends(get_current_user)):
    u = q1("""SELECT u.*, (SELECT count(*) FROM follows WHERE user_id=u.id) followers,
                 (SELECT count(*) FROM follows WHERE follower_id=u.id AND user_id IS NOT NULL) following,
                 (SELECT count(*) FROM files f WHERE f.user_id=u.id AND f.visibility='public' AND NOT f.hidden) files,
                 (SELECT coalesce(sum(download_count),0) FROM files f WHERE f.user_id=u.id AND f.visibility='public') downloads,
                 (SELECT coalesce(sum(like_count),0) FROM files f WHERE f.user_id=u.id AND f.visibility='public') likes
              FROM users u WHERE lower(u.username)=lower(%s)""", (username,))
    if not u:
        raise HTTPException(404, "User not found")
    orgs = q("""SELECT o.*, m.role FROM org_members m JOIN orgs o ON o.id=m.org_id WHERE m.user_id=%s AND m.status='active' ORDER BY o.name""",
             (u["id"],))
    out = person_out(u)
    out.update({"following_count": u["following"], "downloads": int(u["downloads"]), "likes": int(u["likes"]),
                "joined": rel(u["created_at"]), "me": bool(user and user["id"] == u["id"]),
                "following": bool(user and q1("SELECT 1 FROM follows WHERE follower_id=%s AND user_id=%s", (user["id"], u["id"]))),
                "orgs": [dict(org_mini(o), role=o["role"]) for o in orgs]})
    return out


@router.get("/api/users/{username}/files")
def user_files(username: str, tab: str = "files", cursor: Optional[str] = None, user=Depends(get_current_user)):
    u = q1("SELECT id FROM users WHERE lower(username)=lower(%s)", (username,))
    if not u:
        raise HTTPException(404, "User not found")
    p = {"uid": uid_of(user), "u": u["id"]}
    cur = dec_cursor(cursor)
    if tab == "files":
        where = f"f.user_id=%(u)s AND {VIS} AND (f.visibility <> 'link' OR f.user_id=%(uid)s)"
        order_col = "f.created_at"
    elif tab == "liked":
        where = f"f.id IN (SELECT file_id FROM likes WHERE user_id=%(u)s) AND f.visibility='public' AND NOT f.hidden"
        order_col = "f.created_at"
    else:
        raise HTTPException(400, "Bad tab")
    if cur:
        p.update(ts=cur[0], id=int(cur[1]))
        where += f" AND ({order_col}, f.id) < (%(ts)s::timestamptz, %(id)s)"
    rows = q(FILE_SELECT + f" WHERE {where} ORDER BY {order_col} DESC, f.id DESC LIMIT 25", p)
    return {"items": cards(rows[:24], user), "next": enc_cursor(rows[23]["created_at"].isoformat(), rows[23]["id"]) if len(rows) > 24 else None}
