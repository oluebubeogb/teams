/* Collab Teams service worker: cache-first for static assets, network-first for pages. */
const V = "teams-__V__";
const STATIC = ["/static/css/app.css?v=__V__", "/static/js/app.js?v=__V__", "/static/fonts/inter-latin-wght.woff2", "/static/img/icon.svg"];
self.addEventListener("install", (e) => { e.waitUntil(caches.open(V).then((c) => c.addAll(STATIC)).catch(() => {})); self.skipWaiting(); });
self.addEventListener("activate", (e) => { e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== V).map((k) => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener("fetch", (e) => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin) return;
  if (u.pathname.startsWith("/static/") || u.pathname.startsWith("/thumb/") || u.pathname.startsWith("/page/")) {
    e.respondWith(caches.open(V).then(async (c) => { const hit = await c.match(r); if (hit) return hit; const res = await fetch(r); if (res.ok && u.pathname.startsWith("/static/")) c.put(r, res.clone()); return res; }));
    return;
  }
  if (r.mode === "navigate") {
    e.respondWith(fetch(r).catch(() => new Response(`<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>Offline · Teams</title><body style="font:16px system-ui;display:grid;place-items:center;height:100vh;margin:0;background:#f6f6f9;color:#14141f;text-align:center"><div><h2>You're offline</h2><p style="color:#7c7c92">Reconnect to keep browsing Teams.</p><button onclick="location.reload()" style="font:inherit;padding:10px 18px;border-radius:12px;border:0;background:#5C5DE2;color:#fff">Try again</button></div>`, { headers: { "content-type": "text/html" } })));
  }
});
