"""Collab Teams v2 - share, discover and discuss documents."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from app import ai, backfill, config
from app.auth import get_current_user
from app.core import card, file_or_404, get_file, initials, is_site_admin, org_avatar_url, org_cover_url, queue_pending, thumb_url
from app.db import close_pool, q1, run_migrations
from app.routers import analytics, discover, files, orgs, social, suite, users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
BASE = Path(__file__).resolve().parent
ASSET_V = "2.2.0"

app = FastAPI(title=config.APP_NAME, version="2.0.0", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")


class RelayCookies(BaseHTTPMiddleware):
    """When the SSO session was renewed during this request, forward Accounts' Set-Cookie headers to the browser."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for c in getattr(request.state, "relay_cookies", None) or []:
            response.headers.append("set-cookie", c)
        if request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=604800")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response


app.add_middleware(RelayCookies)
if config.CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals.update(cfg=config, asset_v=ASSET_V)

for r in (files, social, orgs, discover, analytics, users, suite):
    app.include_router(r.router)


@app.on_event("startup")
def startup():
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.CACHE_DIR).mkdir(parents=True, exist_ok=True)
    run_migrations()
    queue_pending()
    backfill.start_on_boot()  # AI titles/descriptions for older files: background, throttled, never blocks boot


@app.on_event("shutdown")
def shutdown():
    backfill.stop()
    close_pool()


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith(("/api/", "/raw/", "/thumb/", "/page/", "/media/")) or "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))
    if exc.status_code == 401:
        return RedirectResponse(login_url(request), status_code=302)
    user = getattr(request.state, "user", None)
    return templates.TemplateResponse(request, "page.html", ctx(request, user, "error", title="Not found" if exc.status_code == 404 else "Something went wrong",
                                                                 data={"status": exc.status_code, "detail": exc.detail}),
                                      status_code=exc.status_code)


def login_url(request: Request) -> str:
    nxt = str(request.url.path) + (("?" + request.url.query) if request.url.query else "")
    return f"/login?next={quote(nxt)}"


def me_json(user) -> Optional[dict]:
    if not user:
        return None
    return users.me_out(user)


def ctx(request: Request, user, page: str, title: str = "", data=None, meta: Optional[dict] = None, **kw):
    m = {"title": (f"{title} · Teams" if title else "Collab Teams — share & discover documents"),
         "description": "Share documents, discover what people are reading, and build a library with your organization.",
         "image": f"{config.APP_URL}/static/img/og-default.png", "url": f"{config.APP_URL}{request.url.path}", "type": "website"}
    if meta:
        m.update({k: v for k, v in meta.items() if v})
    boot = {"me": me_json(user), "page": page, "data": data, "accounts_url": config.ACCOUNTS_URL,
            "auth_mode": config.AUTH_MODE, "app_url": config.APP_URL, "max_mb": config.MAX_UPLOAD_MB,
            "ai_enabled": ai.enabled(), "ai_meta_default": config.AI_META_DEFAULT}
    return dict(request=request, user=user, me=boot["me"], page=page, meta=m,
                boot=json.dumps(boot, default=str).replace("</", "<\\/"), **kw)


def render(request, user, page, title="", data=None, meta=None, status_code=200, **kw):
    return templates.TemplateResponse(request, "page.html", ctx(request, user, page, title, data, meta, **kw), status_code=status_code)


# ---------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, tab: Optional[str] = None, user=Depends(get_current_user)):
    return render(request, user, "home", data={"tab": tab or ("foryou" if user else "latest")})


@app.get("/f/{file_id}", response_class=HTMLResponse)
@app.get("/view/{file_id}", response_class=HTMLResponse)
async def file_page(request: Request, file_id: int, k: Optional[str] = None, user=Depends(get_current_user)):
    f = file_or_404(file_id, user, k)
    detail = files.detail(file_id, k, user)
    c = detail["file"] if "file" in detail else detail
    desc = (f.get("ai_summary") or f.get("description") or (f.get("extracted_text") or "")[:220] or "").strip()
    img = thumb_url(f)
    meta = {"title": None, "description": desc[:200] or f"A {f['file_type'].upper()} shared on Collab Teams",
            "image": (f"{config.APP_URL}{img}" + (f"&k={k}" if f["visibility"] == "link" and k else "")) if img and f["visibility"] in ("public", "link") else None,
            "type": "article"}
    return render(request, user, "file", title=c["title"], data=detail, meta=meta, robots=f["visibility"] == "public")


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = "", type: str = "all", user=Depends(get_current_user)):
    return render(request, user, "search", title=f"“{q}”" if q else "Search", data={"q": q, "type": type})


@app.get("/t/{slug}", response_class=HTMLResponse)
async def topic_page(request: Request, slug: str, user=Depends(get_current_user)):
    t = discover.topic(slug, user)
    return render(request, user, "topic", title=t["name"], data=t, meta={"description": f"{t['stats']['files']} documents about {t['name']} on Collab Teams"})


@app.get("/top", response_class=HTMLResponse)
@app.get("/explore", response_class=HTMLResponse)
async def top_page(request: Request, user=Depends(get_current_user)):
    return render(request, user, "top", title="Top lists")


@app.get("/u/{username}", response_class=HTMLResponse)
async def user_page(request: Request, username: str, user=Depends(get_current_user)):
    p = discover.profile(username, user)
    return render(request, user, "profile", title=p["name"], data=p,
                  meta={"description": p["bio"] or f"{p['name']} shares documents on Collab Teams", "image": p["avatar"], "type": "profile"})


@app.get("/o/{slug}", response_class=HTMLResponse)
async def org_page(request: Request, slug: str, user=Depends(get_current_user)):
    o = orgs.org_out(orgs.org_or_404(slug), user)
    img = o["cover"] or o["avatar"]
    return render(request, user, "org", title=o["name"], data=o,
                  meta={"description": o["tagline"] or o["about"][:200], "image": f"{config.APP_URL}{img}" if img else None})


@app.get("/orgs", response_class=HTMLResponse)
async def orgs_page(request: Request, user=Depends(get_current_user)):
    return render(request, user, "orgs", title="Organizations")


@app.get("/c/{cid}", response_class=HTMLResponse)
async def collection_page(request: Request, cid: int, user=Depends(get_current_user)):
    return render(request, user, "collection", title="Collection", data=orgs.get_collection(cid, user))


def _need(user, request):
    if not user:
        raise HTTPException(401, "Sign in")


@app.get("/library", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def library_page(request: Request, tab: str = "files", user=Depends(get_current_user)):
    _need(user, request)
    return render(request, user, "library", title="Your library", data={"tab": tab})


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request, org: Optional[str] = None, user=Depends(get_current_user)):
    _need(user, request)
    return render(request, user, "upload", title="Upload", data={"org": org})


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, scope: str = "me", user=Depends(get_current_user)):
    _need(user, request)
    return render(request, user, "analytics", title="Analytics", data={"scope": scope})


@app.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request, user=Depends(get_current_user)):
    _need(user, request)
    return render(request, user, "activity", title="Activity")


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user=Depends(get_current_user)):
    _need(user, request)
    return render(request, user, "settings", title="Settings")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user=Depends(get_current_user)):
    _need(user, request)
    if not is_site_admin(user):
        raise HTTPException(403, "Admins only")
    return render(request, user, "admin", title="Moderation")


def _safe_next(nxt: Optional[str]) -> str:
    if not nxt or not nxt.startswith("/") or nxt.startswith("//"):
        return "/"
    return nxt


@app.get("/login", response_class=HTMLResponse)
@app.get("/signup", response_class=HTMLResponse)
@app.get("/forgot", response_class=HTMLResponse)
async def auth_page(request: Request, next: Optional[str] = None, user=Depends(get_current_user)):
    nxt = _safe_next(next)
    if user:
        return RedirectResponse(nxt, status_code=302)
    mode = request.url.path.strip("/")
    if mode == "forgot" and config.USE_ACCOUNTS:
        return RedirectResponse(f"{config.ACCOUNTS_URL}/forgot?next={quote(config.APP_URL + nxt)}", status_code=302)
    return render(request, None, "auth", title={"login": "Sign in", "signup": "Create account"}.get(mode, "Sign in"),
                  data={"mode": mode, "next": nxt, "sso": f"{config.ACCOUNTS_URL}/login?next={quote(config.APP_URL + nxt)}"})


@app.get("/logout")
async def logout(request: Request):
    r = RedirectResponse(f"{config.ACCOUNTS_URL}/logout?next={quote(config.APP_URL + '/')}" if config.USE_ACCOUNTS else "/", status_code=302)
    for key in ("access_token", "refresh_token"):
        r.delete_cookie(key=key, path="/")
        if config.COOKIE_DOMAIN:
            r.delete_cookie(key=key, path="/", domain=config.COOKIE_DOMAIN)
    return r


@app.get("/embed/{file_id}", response_class=HTMLResponse)
async def embed(request: Request, file_id: int, k: Optional[str] = None, user=Depends(get_current_user)):
    f = file_or_404(file_id, user, k)
    if f["visibility"] not in ("public", "link"):
        raise HTTPException(404, "File not found")
    c = files.detail(file_id, k, user)
    resp = templates.TemplateResponse(request, "embed.html", {"request": request, "f": c["file"] if "file" in c else c, "k": k or ""})
    resp.headers["Content-Security-Policy"] = "frame-ancestors *"
    return resp


@app.get("/manifest.webmanifest")
async def manifest():
    return JSONResponse({
        "name": "Collab Teams", "short_name": "Teams", "start_url": "/?src=pwa", "display": "standalone",
        "background_color": "#0b0b12", "theme_color": "#5C5DE2", "description": "Share & discover documents",
        "icons": [{"src": "/static/img/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/img/icon-512.png", "sizes": "512x512", "type": "image/png"},
                  {"src": "/static/img/icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
        "shortcuts": [{"name": "Upload", "url": "/upload"}, {"name": "Search", "url": "/search"}],
    }, media_type="application/manifest+json")


@app.get("/sw.js")
async def sw():
    js = (BASE / "static" / "js" / "sw.js").read_text().replace("__V__", ASSET_V)
    return Response(js, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return RedirectResponse("/static/img/icon-192.png", status_code=301)


@app.get("/robots.txt")
async def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /api/\nDisallow: /raw/\nAllow: /\n")


@app.get("/health")
async def health():
    try:
        q1("SELECT 1 AS ok")
        db = "ok"
    except Exception as e:
        db = f"error: {e.__class__.__name__}"
    return {"status": "ok" if db == "ok" else "degraded", "db": db, "version": "2.0.0", "auth_mode": config.AUTH_MODE,
            "storage": config.STORAGE_BACKEND}
