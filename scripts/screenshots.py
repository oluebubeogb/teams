#!/usr/bin/env python3
"""Capture UI screenshots (390px mobile + 1440px desktop, light + dark) with Playwright driving a local Chrome.
    python scripts/screenshots.py --base http://localhost:8008 --out ../screenshots [--chrome /usr/bin/google-chrome]
Needs AUTH_MODE=local and the seed data (scripts/seed.py)."""
import argparse
import os

from playwright.sync_api import sync_playwright

PAGES = [
    ("home", "/", True), ("file", "/f/1", True), ("org", "/o/lagos-health", True), ("search", "/search?q=stay%20safe", True),
    ("analytics", "/analytics", True), ("library", "/library", True), ("upload", "/upload", True), ("activity", "/activity", True),
    ("profile", "/u/ada", True), ("top", "/top", True), ("topic", "/t/health", True), ("orgs", "/orgs", True), ("settings", "/settings", True),
    ("admin", "/admin", True), ("collection", "/c/1", True), ("login", "/login", False), ("embed", "/embed/1", False), ("anon-home", "/", False),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8008")
    ap.add_argument("--out", default="screenshots")
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    only = set(filter(None, a.only.split(",")))
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.chrome, args=["--no-sandbox", "--hide-scrollbars"])
        for vp, size, mobile in [("m", (390, 844), True), ("d", (1440, 900), False)]:
            for theme in ("light", "dark"):
                for auth in (True, False):
                    ctx = b.new_context(viewport={"width": size[0], "height": size[1]}, device_scale_factor=2 if mobile else 1,
                                        is_mobile=mobile, has_touch=mobile, color_scheme=theme)
                    if auth:
                        ctx.request.post(a.base + "/api/login", data={"identifier": "ada@example.com"})
                    pg = ctx.new_page()
                    pg.on("pageerror", lambda e, n=None: errors.append(str(e)))
                    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                    for name, path, need in PAGES:
                        if need != auth or (only and name not in only):
                            continue
                        if theme == "dark" and name not in ("home", "file", "analytics", "org", "login", "search"):
                            continue
                        pg.goto(a.base + path, wait_until="networkidle")
                        pg.evaluate("localStorage.setItem('reelHint','1')")
                        pg.wait_for_timeout(700)
                        fn = f"{a.out}/{vp}-{name}{'-dark' if theme == 'dark' else ''}.png"
                        pg.screenshot(path=fn, full_page=False)
                        if mobile and theme == "light" and name in ("file", "org", "analytics", "upload", "search", "profile"):
                            pg.screenshot(path=fn.replace(".png", "-full.png"), full_page=True)
                        print(fn)
                    if auth and mobile and theme == "light" and (not only or "reel" in only):
                        pg.goto(a.base + "/", wait_until="networkidle"); pg.wait_for_timeout(600)
                        cards = pg.query_selector_all("[data-reel] .thumb[data-open]")
                        card = cards[1] if len(cards) > 1 else (cards[0] if cards else None)
                        if card:
                            card.click(); pg.wait_for_timeout(1200)
                            pg.screenshot(path=f"{a.out}/m-reel.png"); print("reel")
                        pg.goto(a.base + "/library", wait_until="networkidle"); pg.wait_for_timeout(500)
                        sel = pg.query_selector('[data-select]')
                        if sel:
                            sel.click(); pg.wait_for_timeout(300)
                            for cb in pg.query_selector_all("[data-pick]")[:3]:
                                cb.click()
                            pg.wait_for_timeout(400)
                            pg.screenshot(path=f"{a.out}/m-library-bulk.png"); print("bulk")
                    ctx.close()
        b.close()
    print("\nJS errors:", *sorted(set(errors)) or ["none"], sep="\n  ")


if __name__ == "__main__":
    main()
