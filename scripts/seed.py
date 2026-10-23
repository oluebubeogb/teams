#!/usr/bin/env python3
"""Seed a local Teams instance with demo people, orgs, documents and activity.

Requires the app running with AUTH_MODE=local (email-only sign-in) and COOKIE_SECURE=false for http.
    python scripts/seed.py --base http://localhost:8008 [--history]
--history backfills ~45 days of view/download/share events straight into Postgres (DATABASE_URL)
so the analytics charts have shape.
"""
from __future__ import annotations

import argparse
import io
import os
import random
import sys
from datetime import date, timedelta

import httpx

PEOPLE = [
    ("ada", "Ada Okafor", "Public health researcher. Writing about safety, data and Lagos.", "#5C5DE2"),
    ("tunde", "Tunde Bakare", "Engineer at Naija Dev Collective. APIs, Python, Postgres.", "#20A653"),
    ("chioma", "Chioma Eze", "Product designer. Interfaces that feel calm.", "#E2595C"),
    ("emeka", "Emeka Nwosu", "Finance & ops. Spreadsheets are my love language.", "#E28A2B"),
    ("zainab", "Zainab Bello", "Teacher, curriculum builder, lifelong learner.", "#9B51E0"),
]

DOCS = [
    # owner, title, tags, visibility, org, pages (list of paragraphs)
    ("ada", "Community Health Safety Guide 2026", "health,safety,community", "public", "lagos-health",
     ["This guide explains how households can stay safe during the rainy season.",
      "Boil drinking water, keep drains clear, and stay safe by sleeping under treated nets.",
      "Clinics across Lagos offer free malaria testing every first Saturday of the month."]),
    ("ada", "Confidential Board Notes — Q3", "board,internal", "private", "",
     ["Internal only. Remember to stay safe when travelling to the board retreat.",
      "Budget line items are not final. Do not circulate."]),
    ("ada", "Cholera Response Playbook", "health,emergency,water", "org", "lagos-health",
     ["Step one: isolate the water source.", "Step two: oral rehydration points within 2km of every ward."]),
    ("tunde", "Designing Postgres Full-Text Search", "postgres,search,engineering", "public", "naija-dev",
     ["Weighted tsvector columns let titles outrank body text.", "pg_trgm handles typos with similarity()."]),
    ("tunde", "FastAPI in Production: A Field Guide", "python,fastapi,engineering", "public", "naija-dev",
     ["Use a connection pool. Run migrations idempotently on boot.", "Put uvicorn behind a reverse proxy and set proxy headers."]),
    ("tunde", "API Rate Limiting Patterns", "engineering,api", "link", "",
     ["Token buckets, sliding windows and leaky buckets compared."]),
    ("chioma", "Calm Interfaces — Design Principles", "design,ux", "public", "",
     ["Colour should carry meaning. Motion should explain, not decorate.", "Skeletons beat spinners: they promise structure."]),
    ("chioma", "Mobile Navigation Patterns 2026", "design,mobile,ux", "public", "naija-dev",
     ["Bottom tab bars win for thumb reach.", "Sheets beat modals on small screens."]),
    ("emeka", "SME Cashflow Template Explained", "finance,sme,templates", "public", "",
     ["Track receivables weekly.", "Forecast 13 weeks ahead and stress test for a 30% revenue drop."]),
    ("emeka", "Personal Budgeting for Young Professionals", "finance,personal", "public", "",
     ["Pay yourself first. Automate savings on payday.", "Keep three months of expenses in an emergency fund."]),
    ("zainab", "JSS2 Mathematics Lesson Plans — Term 1", "education,maths,teaching", "public", "",
     ["Week 1: Whole numbers and place value.", "Week 2: Fractions, decimals and percentages."]),
    ("zainab", "Reading Circles: A Teacher's Handbook", "education,literacy,teaching", "public", "",
     ["Give every child a role: summariser, questioner, connector, illustrator."]),
    ("ada", "Maternal Health Data Dashboard Methodology", "health,data,research", "public", "lagos-health",
     ["Data sources: 42 primary health centres.", "Indicators: antenatal visits, skilled birth attendance, postnatal checks."]),
    ("tunde", "Onboarding Checklist for New Engineers", "engineering,onboarding", "org", "naija-dev",
     ["Day 1: laptop, accounts, first PR.", "Week 1: ship something small to production."]),
]

COMMENTS = [
    ("tunde", 0, "This is really clear — sharing with my family group. @ada thank you!"),
    ("chioma", 0, "Could we get a Yoruba translation of page 2?"),
    ("emeka", 3, "Weighted vectors were the missing piece for us. Great write-up."),
    ("ada", 3, "Bookmarking for our dashboard search."),
    ("zainab", 6, "Skeletons beat spinners — 100% agree."),
    ("tunde", 8, "Used this for my side business. The 13-week view is gold."),
    ("ada", 10, "My niece's teacher should see this, very well structured."),
]
REPLIES = [("ada", 1, "Yes! @chioma we're working on it — Yoruba and Hausa by next month."), ("zainab", 1, "Happy to help review the Hausa version."),
           ("tunde", 2, "Thanks Emeka 🙏")]


def pdf_bytes(title: str, paras: list[str], color=(0.36, 0.36, 0.89)) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    for i, p in enumerate(paras):
        c.setFillColorRGB(*color)
        c.rect(0, h - 110, w, 110, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 22)
        c.drawString(48, h - 66, title[:48])
        c.setFont("Helvetica", 11)
        c.drawString(48, h - 90, f"Page {i + 1} of {len(paras)}")
        c.setFillColorRGB(0.1, 0.1, 0.15)
        c.setFont("Helvetica", 14)
        y = h - 160
        words, line = p.split(), ""
        for wd in words:
            if c.stringWidth(line + " " + wd, "Helvetica", 14) > w - 96:
                c.drawString(48, y, line); y -= 22; line = wd
            else:
                line = (line + " " + wd).strip()
        c.drawString(48, y, line)
        c.setFillColorRGB(0.92, 0.92, 0.96)
        for k in range(6):
            c.roundRect(48, 120 + k * 60, w - 96, 40, 8, fill=1, stroke=0)
        c.showPage()
    c.save()
    return buf.getvalue()


def image_bytes(text: str, rgb) -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1200, 800), rgb)
    d = ImageDraw.Draw(im)
    for i in range(8):
        d.rounded_rectangle([80 + i * 30, 120 + i * 60, 1120 - i * 30, 160 + i * 60], radius=16, fill=tuple(min(255, c + 30 + i * 8) for c in rgb))
    d.text((80, 60), text, fill="white")
    out = io.BytesIO(); im.save(out, "PNG"); return out.getvalue()


def avatar_bytes(rgb) -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (400, 400), rgb)
    d = ImageDraw.Draw(im)
    d.ellipse([130, 70, 270, 210], fill=(255, 230, 205))
    d.ellipse([70, 230, 330, 500], fill=(255, 255, 255))
    out = io.BytesIO(); im.save(out, "JPEG", quality=90); return out.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("BASE_URL", "http://localhost:8008"))
    ap.add_argument("--history", action="store_true")
    a = ap.parse_args()
    S: dict[str, httpx.Client] = {}

    def client(u):
        if u not in S:
            c = httpx.Client(base_url=a.base, timeout=60)
            r = c.post("/api/login", json={"identifier": f"{u}@example.com"})
            if r.status_code != 200:
                full = next(p for p in PEOPLE if p[0] == u)[1]
                r = c.post("/api/signup", json={"username": u, "email": f"{u}@example.com", "full_name": full})
            r.raise_for_status()
            S[u] = c
        return S[u]

    def ok(r):
        if r.status_code >= 400:
            print("  !", r.request.method, r.request.url, r.status_code, r.text[:200], file=sys.stderr)
        return r

    for u, name, bio, color in PEOPLE:
        c = client(u)
        ok(c.patch("/api/me", json={"full_name": name, "bio": bio, "avatar_color": color}))
        if u in ("ada", "tunde", "chioma"):
            rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
            ok(c.post("/api/me/avatar", files={"file": ("me.jpg", avatar_bytes(rgb), "image/jpeg")}))
    print("users ok")

    ada, tunde = client("ada"), client("tunde")
    ok(ada.post("/api/orgs", json={"name": "Lagos Health Network", "slug": "lagos-health", "tagline": "Public health knowledge for every ward in Lagos",
                                   "about": "A coalition of clinics, researchers and volunteers sharing what works.", "location": "Lagos, Nigeria", "website": "https://example.org"}))
    ok(tunde.post("/api/orgs", json={"name": "Naija Dev Collective", "slug": "naija-dev", "tagline": "Engineers building in public", "location": "Remote · Nigeria"}))
    ok(ada.post("/api/orgs/lagos-health/avatar", files={"file": ("a.png", image_bytes("LHN", (32, 166, 83)), "image/png")}))
    ok(ada.post("/api/orgs/lagos-health/cover", files={"file": ("c.png", image_bytes("Lagos Health", (20, 120, 70)), "image/png")}))
    ok(tunde.post("/api/orgs/naija-dev/avatar", files={"file": ("a.png", image_bytes("NDC", (68, 122, 166)), "image/png")}))
    ok(ada.post("/api/orgs/lagos-health/invite", json={"who": "zainab", "role": "editor"}))
    ok(client("zainab").post("/api/orgs/lagos-health/respond", json={"accept": True}))
    ok(tunde.post("/api/orgs/naija-dev/invite", json={"who": "chioma,ada", "role": "member"}))
    ok(client("chioma").post("/api/orgs/naija-dev/respond", json={"accept": True}))
    print("orgs ok")

    ids = []
    for i, (u, title, tags, vis, org, paras) in enumerate(DOCS):
        c = client(u)
        if org and u == "chioma" and org == "naija-dev":
            pass
        data = {"title": title, "tags": tags, "visibility": vis, "org": org, "description": paras[0][:180]}
        r = ok(c.post("/api/files", data=data, files={"file": (title.lower().replace(" ", "-")[:40] + ".pdf", pdf_bytes(title, paras), "application/pdf")}))
        ids.append(r.json()["file"]["id"] if r.status_code == 200 else None)
    # a markdown note, a csv, an image and a link
    extra = [
        ("chioma", "Design tokens cheat sheet", "design,tokens", ("tokens.md", b"# Design tokens\n\n- files: indigo\n- social: rose\n- orgs: green\n- analytics: amber\n", "text/markdown")),
        ("emeka", "Lagos fuel prices 2026", "finance,data", ("fuel.csv", b"month,price\nJan,1020\nFeb,1050\nMar,990\nApr,1010\n", "text/csv")),
        ("zainab", "Classroom poster: The water cycle", "education,science", ("poster.png", image_bytes("The water cycle", (68, 122, 166)), "image/png")),
    ]
    for u, title, tags, f in extra:
        r = ok(client(u).post("/api/files", data={"title": title, "tags": tags, "visibility": "public"}, files={"file": f}))
        ids.append(r.json()["file"]["id"] if r.status_code == 200 else None)
    ok(tunde.post("/api/files", data={"url": "https://www.postgresql.org/docs/current/textsearch.html", "title": "Postgres docs: Full Text Search", "tags": "postgres,search", "visibility": "public"}))
    print("files ok", ids)

    pub = [ids[i] for i, d in enumerate(DOCS) if d[3] == "public" and ids[i]] + [x for x in ids[len(DOCS):] if x]
    random.seed(7)
    for u, *_ in PEOPLE:
        c = client(u)
        for fid in random.sample(pub, k=min(len(pub), 7)):
            ok(c.post(f"/api/files/{fid}/like"))
        for fid in random.sample(pub, k=3):
            ok(c.post(f"/api/files/{fid}/save"))
        for fid in pub:
            c.post(f"/api/files/{fid}/view", json={"src": random.choice(["feed", "search", "direct", "org", "topic"])})
    for u, other in [("tunde", "ada"), ("chioma", "ada"), ("zainab", "ada"), ("ada", "tunde"), ("emeka", "tunde"), ("ada", "chioma")]:
        ok(client(u).post("/api/follow", json={"user": other}))
    ok(client("emeka").post("/api/follow", json={"org": "lagos-health"}))
    ok(client("zainab").post("/api/follow", json={"topic": "health"}))
    cids = []
    for u, di, body in COMMENTS:
        r = ok(client(u).post(f"/api/files/{ids[di]}/comments", json={"body": body}))
        cids.append(r.json().get("comment", {}).get("id") if r.status_code == 200 else None)
    for u, ci, body in REPLIES:
        ok(client(u).post(f"/api/files/{ids[COMMENTS[ci][1]]}/comments", json={"body": body, "parent_id": cids[ci]}))
    # curation by consent: Lagos Health asks Zainab to feature her lesson plans; she approves. Asks Emeka too (left pending).
    ok(ada.post("/api/orgs/lagos-health/feature", json={"file_id": ids[11], "note": "Great for our school health clubs"}))
    ok(client("zainab").post(f"/api/orgs/lagos-health/feature/{ids[11]}/decide", json={"approve": True}))
    ok(ada.post("/api/orgs/lagos-health/feature", json={"file_id": ids[9], "note": "Would love this in our wellbeing hub"}))
    r = ok(ada.post("/api/collections", json={"title": "Rainy season essentials", "description": "Everything for staying healthy this season", "visibility": "public"}))
    if r.status_code == 200:
        ok(ada.post(f"/api/collections/{r.json()['id']}/items", json={"file_ids": [ids[0], ids[12], ids[16]]}))
    print("social ok")

    if a.history:
        import psycopg
        url = os.getenv("DATABASE_URL", "postgresql://teams:teams@localhost:5432/teams")
        srcs = ["feed"] * 6 + ["search"] * 4 + ["direct"] * 3 + ["org"] * 2 + ["topic", "top", "profile", "embed", "collection"]
        chans = ["whatsapp"] * 5 + ["x", "linkedin", "telegram", "facebook", "copy", "copy", "native"]
        with psycopg.connect(url) as cx:
            files = cx.execute("SELECT id FROM files WHERE visibility IN ('public','link','org')").fetchall()
            n = 0
            for (fid,) in files:
                pop = random.uniform(.3, 2.2)
                for back in range(1, 46):
                    d = date.today() - timedelta(days=back)
                    wk = 1.25 if d.weekday() < 5 else .7
                    trend = 1 + (45 - back) / 45
                    views = int(random.gauss(6, 3) * pop * wk * trend)
                    for v in range(max(0, views)):
                        kind = "view"
                        r = random.random()
                        src = random.choice(srcs)
                        rows = [(fid, "view", f"h:{fid}:{back}:{v}", src, d)]
                        if r < .28:
                            rows.append((fid, "download", f"h:{fid}:{back}:{v}", src, d))
                        if r < .07:
                            ch = random.choice(chans)
                            rows.append((fid, "share", f"h:{fid}:{back}:{v}:{ch}", "share:" + ch, d))
                        for row in rows:
                            cx.execute("""INSERT INTO events(file_id,kind,visitor,source,day,created_at)
                                          VALUES (%s,%s,%s,%s,%s,%s::date + make_interval(hours => %s, mins => %s)) ON CONFLICT DO NOTHING""",
                                       (*row, row[4], random.choice([8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]), random.randint(0, 59)))
                            n += 1
            cx.execute("""UPDATE files f SET view_count=e.v, download_count=e.d, share_count=e.s FROM
                          (SELECT file_id, count(*) FILTER (WHERE kind='view') v, count(*) FILTER (WHERE kind='download') d,
                                  count(*) FILTER (WHERE kind='share') s FROM events GROUP BY file_id) e WHERE e.file_id=f.id""")
        print("history events:", n)
    print("seed complete")


if __name__ == "__main__":
    main()
