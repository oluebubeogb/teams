#!/usr/bin/env python3
"""End-to-end smoke test against a running Teams instance in AUTH_MODE=local.
    python scripts/smoke_test.py --base http://localhost:8008
Creates throwaway users (smoke_<rand>) so it can run repeatedly."""
from __future__ import annotations

import argparse
import io
import os
import random
import string
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(__file__))
from seed import pdf_bytes  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  -> {extra}"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("BASE_URL", "http://localhost:8008"))
    a = ap.parse_args()
    tag = "".join(random.choices(string.ascii_lowercase, k=5))

    def login(u):
        c = httpx.Client(base_url=a.base, timeout=60)
        r = c.post("/api/signup", json={"username": u, "email": f"{u}@smoke.test", "full_name": u.title(), "password": "smoke-pass-123"})
        if r.status_code >= 400:
            r = c.post("/api/login", json={"identifier": f"{u}@smoke.test"})
        check(f"auth {u}", r.status_code == 200, r.text[:200])
        me = c.get("/api/me").json().get("user") or {}
        check(f"/api/me {u}", me.get("username") == u, me)
        return c

    print("auth")
    A, B, C = login(f"sa{tag}"), login(f"sb{tag}"), login(f"sc{tag}")
    anon = httpx.Client(base_url=a.base, timeout=30)
    check("anon /api/me has no user", anon.get("/api/me").json().get("user") is None)

    print("upload")
    pub_phrase, priv_phrase = f"stay safe zebra{tag}", f"secret walrus{tag}"
    r = A.post("/api/files", data={"title": f"Public guide {tag}", "tags": "smoke,health", "visibility": "public"},
               files={"file": ("pub.pdf", pdf_bytes("Public", [f"Please {pub_phrase} on the road.", "Page two"]), "application/pdf")})
    check("upload public pdf", r.status_code == 200, r.text[:300])
    pub = r.json()["file"]
    r = A.post("/api/files", data={"title": f"Private memo {tag}", "visibility": "private"},
               files={"file": ("priv.pdf", pdf_bytes("Private", [f"The {priv_phrase} and also stay safe zebra{tag} hidden."]), "application/pdf")})
    check("upload private pdf", r.status_code == 200, r.text[:300])
    priv = r.json()["file"]
    r = A.post("/api/files", data={"title": f"Notes {tag}", "visibility": "public"}, files={"file": ("n.txt", b"hello notes", "text/plain")})
    txt = r.json()["file"]
    time.sleep(2.5)  # background extraction / thumbnails
    for _ in range(20):
        f = A.get(f"/api/files/{pub['id']}").json()
        if (f.get("file") or f).get("page_count"):
            break
        time.sleep(.5)
    check("thumbnail served", anon.get(f"/thumb/{pub['id']}.webp").status_code == 200)
    check("page image served", anon.get(f"/page/{pub['id']}/1.webp").status_code == 200)
    check("private raw hidden from anon", anon.get(f"/raw/{priv['id']}").status_code in (403, 404))
    check("private meta hidden from B", B.get(f"/api/files/{priv['id']}").status_code in (403, 404))

    print("search")
    r = B.get("/api/search", params={"q": f"zebra{tag}"}).json()
    fids = [x["id"] for x in r.get("files", [])]
    check("search finds phrase inside public PDF", pub["id"] in fids, r)
    check("search hides private PDF from others", priv["id"] not in fids, fids)
    r = B.get("/api/search", params={"q": f"walrus{tag}"}).json()
    check("private-only phrase returns nothing for others", not r.get("files"), r.get("files"))
    r = A.get("/api/search", params={"q": f"walrus{tag}"}).json()
    check("owner can find own private file", priv["id"] in [x["id"] for x in r.get("files", [])], r)
    snip = next((x for x in B.get("/api/search", params={"q": f"zebra{tag}"}).json().get("files", []) if x["id"] == pub["id"]), {})
    check("search snippet present", "<mark>" in (snip.get("headline") or "") and snip.get("match_in") == "content", snip)
    check("anon search respects visibility", priv["id"] not in [x["id"] for x in anon.get("/api/search", params={"q": f"zebra{tag}"}).json().get("files", [])])

    print("comments & likes")
    r = B.post(f"/api/files/{pub['id']}/comments", json={"body": f"Great doc @sa{tag}"})
    check("comment", r.status_code == 200, r.text[:200])
    cid = r.json()["comment"]["id"]
    r = A.post(f"/api/files/{pub['id']}/comments", json={"body": "Thanks!", "parent_id": cid})
    check("reply", r.status_code == 200, r.text[:200])
    r = C.post(f"/api/files/{pub['id']}/comments", json={"body": "Reply to reply", "parent_id": r.json()["comment"]["id"]})
    check("reply-to-reply flattens to 1 level", r.status_code == 200 and r.json()["comment"].get("root_id") == cid, r.text[:200])
    lst = anon.get(f"/api/files/{pub['id']}/comments").json()
    top = next((x for x in lst.get("items", []) if x["id"] == cid), {})
    check("thread count on top comment", top.get("thread_total", top.get("reply_count")) == 2, top)
    th = anon.get(f"/api/comments/{cid}/thread").json()
    check("thread endpoint", len(th.get("items", [])) == 2, th)
    check("comment like", C.post(f"/api/comments/{cid}/like").status_code == 200)
    r1, r2 = B.post(f"/api/files/{pub['id']}/like").json(), C.post(f"/api/files/{pub['id']}/like").json()
    check("like count", r2.get("count") == 2, (r1, r2))
    r3 = B.post(f"/api/files/{pub['id']}/like").json()
    check("unlike toggles", r3.get("on") is False, r3)
    check("save", B.post(f"/api/files/{pub['id']}/save").json().get("on") is True)
    n = A.get("/api/notifications").json()
    kinds = {x["kind"] for x in n.get("items", [])}
    check("notifications for comment+mention+like", {"comment", "mention", "like"} <= kinds or {"comment", "like"} <= kinds, kinds)

    print("views/shares dedupe")
    v1 = B.post(f"/api/files/{pub['id']}/view", json={"src": "feed"}).json()
    v2 = B.post(f"/api/files/{pub['id']}/view", json={"src": "feed"}).json()
    check("view dedupe", v1.get("counted") is True and v2.get("counted") is False, (v1, v2))
    s1 = B.post(f"/api/files/{pub['id']}/share", json={"channel": "whatsapp"}).json()
    check("share recorded", s1.get("counted", True) is not False, s1)
    check("download", B.get(f"/api/files/{pub['id']}/download").status_code in (200, 302, 307))

    print("orgs")
    slug = f"org{tag}"
    r = A.post("/api/orgs", json={"name": f"Smoke Org {tag}", "slug": slug})
    check("create org", r.status_code == 200, r.text[:200])
    r = A.post(f"/api/orgs/{slug}/invite", json={"who": f"sb{tag}", "role": "editor"})
    check("invite by username", r.status_code == 200, r.text[:200])
    inv = [x for x in B.get("/api/notifications").json().get("items", []) if x["kind"] == "org_invite"]
    check("invite in notifications", bool(inv), inv)
    check("accept invite", B.post(f"/api/orgs/{slug}/respond", json={"accept": True}).status_code == 200)
    mem = A.get(f"/api/orgs/{slug}/members").json()
    check("member listed with role", any(m.get("username") == f"sb{tag}" and m.get("role") == "editor" for m in mem.get("items", mem.get("members", []))), mem)
    r = C.post("/api/files", data={"title": f"C's poster {tag}", "visibility": "public"}, files={"file": ("p.txt", b"poster", "text/plain")})
    cfile = r.json()["file"]["id"]
    r = B.post(f"/api/orgs/{slug}/feature", json={"file_id": cfile, "note": "Please?"})
    check("editor requests feature", r.status_code == 200, r.text[:200])
    of = anon.get(f"/api/orgs/{slug}/files").json()
    check("not featured before consent", cfile not in [x["id"] for x in of.get("items", [])])
    check("owner approves", C.post(f"/api/orgs/{slug}/feature/{cfile}/decide", json={"approve": True}).status_code == 200)
    of = anon.get(f"/api/orgs/{slug}/files").json()
    check("featured after consent", cfile in [x["id"] for x in of.get("items", [])], of)
    check("org page renders", anon.get(f"/o/{slug}").status_code == 200)

    print("bulk")
    r = A.post("/api/bulk", json={"ids": [pub["id"], txt["id"]], "action": "tags_add", "value": ["bulked"]})
    check("bulk tags_add", r.status_code == 200 and r.json().get("affected") == 2, r.text[:200])
    check("bulk tag applied", "bulked" in A.get(f"/api/files/{txt['id']}").json().get("file", {}).get("tags", A.get(f"/api/files/{txt['id']}").json().get("tags", [])))
    r = A.post("/api/bulk", json={"ids": [txt["id"]], "action": "visibility", "value": "private"})
    check("bulk visibility", r.status_code == 200)
    check("bulk visibility applied", B.get(f"/api/files/{txt['id']}").status_code in (403, 404))
    r = A.post("/api/bulk", json={"ids": [txt["id"]], "action": "org", "value": slug})
    check("bulk move to org", r.status_code == 200, r.text[:200])
    check("bulk by non-owner refused", C.post("/api/bulk", json={"ids": [pub["id"]], "action": "delete"}).status_code == 403)
    z = A.get("/api/bulk/zip", params={"ids": f"{pub['id']},{txt['id']}"})
    check("bulk zip", z.status_code == 200 and z.content[:2] == b"PK", z.status_code)

    print("feed/top/topics")
    for t in ("foryou", "following", "latest"):
        r = B.get("/api/feed", params={"tab": t})
        check(f"feed {t}", r.status_code == 200 and "items" in r.json(), r.text[:200])
    r = B.get("/api/feed", params={"tab": "latest"}).json()
    if r.get("next"):
        r2 = B.get("/api/feed", params={"tab": "latest", "cursor": r["next"]}).json()
        check("feed cursor paging no dupes", not ({x["id"] for x in r["items"]} & {x["id"] for x in r2["items"]}))
    check("top", anon.get("/api/top", params={"metric": "views", "period": "week"}).status_code == 200)
    check("topic", anon.get("/api/topics/health").status_code == 200)

    print("analytics")
    r = A.get("/api/analytics", params={"scope": "me", "days": 30})
    check("analytics me", r.status_code == 200 and "series" in r.json(), r.text[:200])
    d = r.json()
    check("analytics counted views", sum(d["series"]["view"]) >= 1, d.get("totals"))
    r = A.get("/api/analytics", params={"scope": f"org:{slug}", "days": 7})
    check("analytics org", r.status_code == 200, r.text[:200])
    r = A.get("/api/analytics", params={"scope": f"file:{pub['id']}", "days": 7})
    check("analytics file", r.status_code == 200, r.text[:200])
    r = A.get("/api/analytics/day", params={"scope": "me", "day": d["labels"][-1] if d.get("labels") else ""})
    check("analytics day drill", r.status_code == 200 and len(r.json().get("hours", [])) == 24, r.text[:200])
    check("analytics scope denied", C.get("/api/analytics", params={"scope": "site"}).status_code == 403)
    check("analytics org denied to non-member", C.get("/api/analytics", params={"scope": f"org:{slug}"}).status_code == 403)

    print("suite sync")
    tok = os.getenv("SUITE_SYNC_TOKEN", "")
    check("suite rejects missing token", anon.get("/api/suite/orgs").status_code in (401, 503))
    if tok:
        H = {"Authorization": f"Bearer {tok}"}
        check("suite rejects bad token", anon.get("/api/suite/orgs", headers={"Authorization": "Bearer nope"}).status_code == 401)
        r = anon.get("/api/suite/orgs", headers=H)
        check("suite list orgs", r.status_code == 200 and any(o["slug"] == slug for o in r.json()["orgs"]), r.text[:200])
        listed = next((o for o in r.json().get("orgs", []) if o["slug"] == slug), {})
        check("suite org has members", any(m["email"] == f"sa{tag}@smoke.test" for m in listed.get("members", [])), listed)
        ext = f"crede-{tag}"
        body = {"source": "crede", "external_id": ext, "name": f"Crede Org {tag}", "org_type": "school",
                "about": "From Crede", "owner": {"email": f"owner{tag}@smoke.test", "full_name": "Crede Owner"},
                "members": [{"email": f"sb{tag}@smoke.test", "role": "admin"}, {"email": f"new{tag}@smoke.test"}]}
        r1 = anon.post("/api/suite/orgs/upsert", json=body, headers=H)
        check("suite upsert creates", r1.status_code == 200 and r1.json()["created"], r1.text[:200])
        body["about"] = "Updated from Crede"
        r2 = anon.post("/api/suite/orgs/upsert", json=body, headers=H)
        check("suite upsert is idempotent", r2.status_code == 200 and not r2.json()["created"] and r2.json()["id"] == r1.json()["id"], r2.text[:200])
        r = anon.get(f"/api/orgs/{r1.json()['slug']}")
        check("crede org visible in Teams", r.status_code == 200 and (r.json().get("org") or r.json()).get("source") == "crede", r.text[:200])
        since = anon.get("/api/suite/orgs", params={"since": "2000-01-01T00:00:00Z", "limit": 5}, headers=H)
        check("suite since filter", since.status_code == 200 and since.json()["count"] <= 5, since.text[:200])

    print("pages")
    for p in ["/", "/explore", "/top", f"/f/{pub['id']}", f"/u/sa{tag}", "/search?q=zebra", "/t/health", "/login", "/manifest.webmanifest", "/sw.js"]:
        r = anon.get(p)
        check(f"GET {p}", r.status_code == 200, r.status_code)
    for p in ["/library", "/upload", "/analytics", "/activity", "/settings", "/orgs"]:
        r = A.get(p)
        check(f"GET {p} (auth)", r.status_code == 200, r.status_code)
    check("private file page 404 for B", B.get(f"/f/{priv['id']}").status_code in (403, 404))

    print("cleanup")
    check("delete own file", A.delete(f"/api/files/{priv['id']}").status_code == 200)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", *FAIL, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
