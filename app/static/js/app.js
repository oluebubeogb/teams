/* Collab Teams v2 — vanilla JS client. No build step, no CDN. */
(() => {
"use strict";
const BOOT = JSON.parse(document.getElementById("boot").textContent);
const ME = BOOT.me;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isMobile = () => matchMedia("(max-width: 720px)").matches;
const ic = (n, cls = "") => `<svg class="icon ${cls}"><use href="#${n.startsWith("b-") ? n : "i-" + n}"/></svg>`;
const num = (n) => { n = +n || 0; if (n < 1000) return String(n); if (n < 1e6) return (n / 1e3).toFixed(n < 1e4 ? 1 : 0).replace(/\.0$/, "") + "k"; return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M"; };
const plural = (n, w) => `${num(n)} ${w}${+n === 1 ? "" : "s"}`;
const qs = (o) => Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
const params = new URLSearchParams(location.search);
const KEY = params.get("k") || "";
const SRC = params.get("src") || "";
const bytes = (b) => b < 1024 ? b + " B" : b < 1048576 ? (b / 1024).toFixed(0) + " KB" : (b / 1048576).toFixed(1) + " MB";
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const FT_LABEL = { pdf: "PDF", docx: "DOCX", doc: "DOC", pptx: "PPTX", xlsx: "XLSX", xls: "XLS", csv: "CSV", txt: "TXT", md: "MD", url: "LINK", png: "PNG", jpg: "JPG", jpeg: "JPG", webp: "WEBP", gif: "GIF" };
const VIS = { public: ["globe", "Public"], org: ["building", "Organization"], private: ["lock", "Private"], link: ["link", "Anyone with link"] };

async function api(method, url, body, opts = {}) {
  const init = { method, headers: { Accept: "application/json" }, credentials: "same-origin" };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  const r = await fetch(url, init);
  let data = null;
  try { data = await r.json(); } catch (e) { /* empty */ }
  if (!r.ok) {
    if (r.status === 401 && !opts.quiet) { toast("Please sign in to continue"); setTimeout(() => location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search), 700); }
    const msg = data && (typeof data.detail === "string" ? data.detail : Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : null) || `Request failed (${r.status})`;
    const err = new Error(msg); err.status = r.status; throw err;
  }
  return data;
}
const GET = (u) => api("GET", u);
const POST = (u, b) => api("POST", u, b ?? {});
const need = () => { if (!ME) { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search); return false; } return true; };

/* ---------------- UI primitives ---------------- */
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.innerHTML = (kind === "err" ? ic("x", "sm") : ic("check", "sm")) + `<span>${esc(msg)}</span>`;
  $("#toasts").appendChild(t);
  setTimeout(() => { t.style.transition = "opacity .3s, transform .3s"; t.style.opacity = 0; t.style.transform = "translateY(8px)"; setTimeout(() => t.remove(), 300); }, 2600);
}
const fail = (e) => toast(e.message || String(e), "err");

function avatar(u, size = "", org = false) {
  if (!u) return "";
  const inner = u.avatar ? `<img src="${esc(u.avatar)}" alt="" loading="lazy">` : esc(u.initials || (u.name || "?").slice(0, 1).toUpperCase());
  const c = org ? (u.accent || "#20A653") : (u.color || "#5C5DE2");
  return `<span class="av ${size} ${org ? "org" : ""}" style="--c:${esc(c)}">${inner}</span>`;
}
const verified = (v) => v ? `<span class="verified" data-tip="Verified">${ic("verified", "fill")}</span>` : "";

function sheet({ title = "", body = "", foot = "", wide = false, onClose } = {}) {
  const scrim = document.createElement("div"); scrim.className = "scrim";
  const el = document.createElement("div"); el.className = "sheet" + (wide ? " wide" : ""); el.setAttribute("role", "dialog");
  el.innerHTML = `<div class="grabber"></div><div class="sheet-h"><h3>${title}</h3><button class="iconbtn" data-x aria-label="Close">${ic("x")}</button></div><div class="sheet-b">${body}</div>${foot ? `<div class="sheet-f">${foot}</div>` : ""}`;
  document.body.append(scrim, el);
  document.body.style.overflow = "hidden";
  const close = () => { scrim.remove(); el.remove(); if (!$(".sheet") && !$(".reel")) document.body.style.overflow = ""; document.removeEventListener("keydown", onKey); onClose && onClose(); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  scrim.onclick = close; $("[data-x]", el).onclick = close;
  // drag-to-dismiss on mobile
  let y0 = null, dy = 0;
  const grab = $(".grabber", el), head = $(".sheet-h", el);
  [grab, head].forEach((h) => {
    h.addEventListener("touchstart", (e) => { y0 = e.touches[0].clientY; el.style.transition = "none"; }, { passive: true });
    h.addEventListener("touchmove", (e) => { if (y0 == null) return; dy = Math.max(0, e.touches[0].clientY - y0); el.style.transform = `translateY(${dy}px)`; }, { passive: true });
    h.addEventListener("touchend", () => { el.style.transition = "transform .25s"; if (dy > 90) close(); else el.style.transform = ""; y0 = null; dy = 0; });
  });
  const first = $("input,textarea,select", el); if (first && !isMobile()) setTimeout(() => first.focus(), 50);
  return { el, close };
}
function confirmSheet(text, { ok = "Confirm", danger = false } = {}) {
  return new Promise((res) => {
    let done = false;
    const s = sheet({ title: "Are you sure?", body: `<p style="margin:0;color:var(--text-2)">${text}</p>`,
      foot: `<button class="btn" data-no>Cancel</button><button class="btn ${danger ? "danger solid" : "primary"}" data-yes>${ok}</button>`, onClose: () => !done && res(false) });
    $("[data-no]", s.el).onclick = () => { done = true; s.close(); res(false); };
    $("[data-yes]", s.el).onclick = () => { done = true; s.close(); res(true); };
  });
}
let openMenu = null;
function menu(anchor, items) {
  closeMenu();
  const m = document.createElement("div"); m.className = "menu";
  m.innerHTML = items.filter(Boolean).map((it, i) => it === "-" ? "<hr>" : it.href
    ? `<a href="${esc(it.href)}" ${it.blank ? 'target="_blank" rel="noopener"' : ""} class="${it.danger ? "danger" : ""}">${ic(it.icon || "chev-r")}${esc(it.label)}</a>`
    : `<button data-i="${i}" class="${it.danger ? "danger" : ""}">${ic(it.icon || "chev-r")}${esc(it.label)}</button>`).join("");
  document.body.appendChild(m);
  const r = anchor.getBoundingClientRect();
  const w = m.offsetWidth, h = m.offsetHeight;
  let left = Math.min(r.right - w, innerWidth - w - 8); left = Math.max(8, left);
  let top = r.bottom + 6; if (top + h > innerHeight - 8) top = Math.max(8, r.top - h - 6);
  m.style.left = left + "px"; m.style.top = top + "px";
  m.addEventListener("click", (e) => { const b = e.target.closest("button[data-i]"); if (!b) return; const it = items.filter(Boolean)[+b.dataset.i]; closeMenu(); it.on && it.on(); });
  openMenu = m;
  setTimeout(() => document.addEventListener("click", closeMenuOutside, { once: true }), 0);
}
function closeMenuOutside(e) { if (openMenu && !openMenu.contains(e.target)) closeMenu(); else if (openMenu) document.addEventListener("click", closeMenuOutside, { once: true }); }
function closeMenu() { openMenu && openMenu.remove(); openMenu = null; }
addEventListener("scroll", closeMenu, { passive: true });

/* tooltips (desktop only) */
let tipEl = null;
document.addEventListener("mouseover", (e) => {
  if (matchMedia("(hover: none)").matches) return;
  const t = e.target.closest("[data-tip],[data-tip-r]");
  if (!t) return;
  const right = t.hasAttribute("data-tip-r");
  if (right && innerWidth > 1024) return; // sidebar labels visible
  const text = t.dataset.tip || t.dataset.tipR; if (!text) return;
  tipEl && tipEl.remove();
  tipEl = document.createElement("div"); tipEl.className = "tip"; tipEl.textContent = text; document.body.appendChild(tipEl);
  const r = t.getBoundingClientRect();
  if (right) { tipEl.style.left = r.right + 10 + "px"; tipEl.style.top = r.top + r.height / 2 - tipEl.offsetHeight / 2 + "px"; }
  else { tipEl.style.left = Math.max(6, Math.min(innerWidth - tipEl.offsetWidth - 6, r.left + r.width / 2 - tipEl.offsetWidth / 2)) + "px"; tipEl.style.top = r.top - tipEl.offsetHeight - 8 + "px"; }
  t.addEventListener("mouseleave", () => { tipEl && tipEl.remove(); tipEl = null; }, { once: true });
});

/* theme */
function setTheme(t, persist = true) {
  if (persist) { if (t === "system") localStorage.removeItem("theme"); else localStorage.setItem("theme", t); }
  if (t === "system") t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.theme = t;
  $$('[data-act="theme"] use').forEach((u) => u.setAttribute("href", t === "dark" ? "#i-sun" : "#i-moon"));
}
setTheme(document.documentElement.dataset.theme, false);
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => { if (!localStorage.getItem("theme")) setTheme("system", false); });

/* ---------------- file cards ---------------- */
const STORE = new Map();
const keep = (c) => { STORE.set(c.id, Object.assign(STORE.get(c.id) || {}, c)); return c; };
const fileUrl = (c, src) => `/f/${c.id}` + (src ? `?src=${src}` : "");
const visIcon = (v) => v && v !== "public" ? `<span class="vis" data-tip="${VIS[v][1]}">${ic(VIS[v][0], "sm")}</span>` : "";

function thumbHtml(c, src, { tag = "a" } = {}) {
  const ft = `<span class="ft">${FT_LABEL[c.type] || c.type.toUpperCase()}</span>`;
  const pg = c.pages ? `<span class="pg">${ic("file", "sm")}${c.pages}</span>` : "";
  const k = c.visibility === "link" && c.k ? `&k=${c.k}` : "";
  if (c.thumb) return `<${tag} class="thumb" href="${fileUrl(c, src)}" data-open="${c.id}"><img src="${c.thumb}${k}" alt="" loading="lazy" onload="this.classList.add('ok')" onerror="this.closest('.thumb').classList.add('placeholder');this.remove()">${ft}${pg}</${tag}>`;
  if (c.type === "url") return `<a class="thumb placeholder" href="${fileUrl(c, src)}"><div class="col" style="align-items:center;gap:8px;padding:0 20px;text-align:center"><div class="ft-tile ft-url">${ic("link", "lg")}</div><span class="muted clamp-1" style="font-size:13px">${esc((c.url || "").replace(/^https?:\/\//, ""))}</span></div></a>`;
  if (c.text) return `<${tag} class="thumb text" href="${fileUrl(c, src)}" data-open="${c.id}">${esc(c.text)}</${tag}>`;
  if (c.thumb_status === "pending") return `<${tag} class="thumb placeholder" href="${fileUrl(c, src)}" data-open="${c.id}"><div class="ft-tile ft-${c.type}">${FT_LABEL[c.type] || ""}</div><div class="shimmer"></div></${tag}>`;
  return `<${tag} class="thumb placeholder" href="${fileUrl(c, src)}" data-open="${c.id}"><div class="ft-tile ft-${c.type}">${FT_LABEL[c.type] || ""}</div></${tag}>`;
}

function actionsHtml(c, src) {
  return `<div class="actions">
    <button class="act like ${c.liked ? "on" : ""}" data-like="${c.id}" aria-label="Like" data-tip="Like">${ic("heart")}<span class="n">${c.counts.like ? num(c.counts.like) : ""}</span></button>
    <a class="act" href="/f/${c.id}?src=${src}#comments" aria-label="Comments" data-tip="Comments">${ic("message")}<span class="n">${c.counts.comment ? num(c.counts.comment) : ""}</span></a>
    <button class="act" data-share="${c.id}" aria-label="Share" data-tip="Share">${ic("send")}<span class="n">${c.counts.share ? num(c.counts.share) : ""}</span></button>
    <span class="sp"></span>
    <span class="pill-stat" data-tip="Downloads">${ic("download", "sm")}${num(c.counts.download)}</span>
    <button class="act save ${c.saved ? "on" : ""}" data-save="${c.id}" aria-label="Save" data-tip="Save">${ic("bookmark")}</button>
  </div>`;
}

function feedCard(c, src = "feed") {
  keep(c);
  const a = c.author;
  const desc = c.description || c.summary || "";
  const hl = c.headline ? `<div class="hl"><span class="lbl">${c.match_in === "title" ? "Title match" : "Found inside"}</span>${c.headline}</div>` : "";
  return `<article class="card" data-card="${c.id}">
    <div class="card-h">
      <a href="/u/${esc(a.username)}">${avatar(a)}</a>
      <div class="who"><a href="/u/${esc(a.username)}"><b>${esc(a.name)}</b></a>
        <div class="sub">${c.org ? `<a class="org-link" href="/o/${esc(c.org.slug)}">${esc(c.org.name)}${verified(c.org.verified)}</a><span>·</span>` : ""}<span>${esc(c.ago)}</span>${visIcon(c.visibility) ? `<span>·</span>${visIcon(c.visibility)}` : ""}</div></div>
      <button class="iconbtn" data-more="${c.id}" aria-label="More">${ic("more")}</button>
    </div>
    <a class="card-b" href="${fileUrl(c, src)}" style="display:block">
      <h3 class="card-t">${c.title_html || esc(c.title)}</h3>
      ${desc ? `<p class="card-d clamp-2 ${!c.description && c.summary ? "ai" : ""}">${esc(desc)}</p>` : ""}
    </a>
    ${hl}
    ${thumbHtml(c, src)}
    ${c.topics && c.topics.length ? `<div class="card-topics">${c.topics.map((t) => `<a class="chip topic" href="/t/${esc(t.slug)}">#${esc(t.name)}</a>`).join("")}</div>` : ""}
    ${actionsHtml(c, src)}
  </article>`;
}

function tile(c, { select = false, src = "library", extra = "" } = {}) {
  keep(c);
  const img = c.thumb ? `<img src="${c.thumb}" alt="" loading="lazy" onerror="this.remove()">` : c.text ? `<div class="ph" style="padding:12px;font-size:11px;line-height:1.5;color:var(--muted);white-space:pre-line;overflow:hidden;place-items:start;font-family:ui-monospace,monospace">${esc(c.text.slice(0, 220))}</div>` : `<div class="ph"><div class="ft-tile ft-${c.type}" style="transform:scale(.8)">${FT_LABEL[c.type] || ic("link")}</div></div>`;
  return `<div class="tile" data-tile="${c.id}">
    ${select ? `<input type="checkbox" class="check sel" data-pick="${c.id}" aria-label="Select">` : ""}
    ${c.visibility !== "public" ? `<span class="badge vis-badge" data-tip="${VIS[c.visibility][1]}">${ic(VIS[c.visibility][0], "sm")}</span>` : ""}
    <a class="t-img" href="${fileUrl(c, src)}">${img}</a>
    <div class="t-b"><a class="t-t clamp-2" href="${fileUrl(c, src)}">${esc(c.title)}</a>
      ${extra}
      <div class="t-m"><span class="row gap-4">${ic("heart", "sm")}${num(c.counts.like)}</span><span class="row gap-4">${ic("download", "sm")}${num(c.counts.download)}</span><span class="grow"></span><span>${esc(c.ago)}</span></div>
    </div></div>`;
}

function rankRow(it, kind, i) {
  const r = it.rank || i + 1;
  const rk = `<span class="rank r${r}">${r}</span>`;
  if (kind === "creators") return `<a class="list-row" href="/u/${esc(it.username)}">${rk}${avatar(it, "md")}<div class="grow"><b>${esc(it.name)}</b><div class="muted" style="font-size:13px">@${esc(it.username)} · ${plural(it.files || 0, "file")} · ${plural(it.followers || 0, "follower")}</div></div><div class="metric">${num(it.metric)}<div class="muted" style="font-size:11px;font-weight:500;text-align:right">downloads</div></div></a>`;
  if (kind === "orgs") return `<a class="list-row" href="/o/${esc(it.slug)}">${rk}${avatar(it, "md", true)}<div class="grow"><b>${esc(it.name)}</b>${verified(it.verified)}<div class="muted clamp-1" style="font-size:13px">${esc(it.tagline || `${plural(it.members || 0, "member")} · ${plural(it.files || 0, "file")}`)}</div></div><div class="metric">${num(it.metric)}<div class="muted" style="font-size:11px;font-weight:500;text-align:right">downloads</div></div></a>`;
  if (kind === "topics") return `<a class="list-row" href="/t/${esc(it.slug)}">${rk}<span class="mini-thumb" style="background:var(--disc-soft);color:var(--disc-ink)">${ic("hash")}</span><div class="grow"><b>${esc(it.name)}</b><div class="muted" style="font-size:13px">${plural(it.files, "document")}</div></div>${ic("chev-r")}</a>`;
  keep(it);
  const metric = kind === "downloads" ? `<div class="metric">${num(it.metric)}<div class="muted" style="font-size:11px;font-weight:500;text-align:right">downloads</div></div>` : `<div class="metric" style="color:var(--stats-ink)">${ic("flame", "sm")}</div>`;
  return `<a class="list-row" href="${fileUrl(it, "top")}">${rk}<span class="mini-thumb">${it.thumb ? `<img src="${it.thumb}" alt="" loading="lazy">` : `<b style="font-size:10px;color:var(--files)">${FT_LABEL[it.type] || ""}</b>`}</span><div class="grow"><b class="clamp-1">${esc(it.title)}</b><div class="muted clamp-1" style="font-size:13px">${esc(it.author.name)}${it.org ? " · " + esc(it.org.name) : ""} · ${num(it.counts.like)} likes</div></div>${metric}</a>`;
}

function skeletonCards(n = 3) {
  return Array.from({ length: n }, () => `<div class="card sk-card"><div class="row"><div class="sk" style="width:36px;height:36px;border-radius:50%"></div><div class="grow col gap-4"><div class="sk" style="height:12px;width:40%"></div><div class="sk" style="height:10px;width:24%"></div></div></div><div class="sk" style="height:16px;width:80%;margin-top:14px"></div><div class="sk" style="height:12px;width:60%;margin-top:8px"></div><div class="sk" style="aspect-ratio:4/3;margin-top:12px;border-radius:14px"></div></div>`).join("");
}
const skeletonTiles = (n = 8) => `<div class="grid">${Array.from({ length: n }, () => `<div class="tile"><div class="sk" style="aspect-ratio:4/3;border-radius:0"></div><div class="t-b"><div class="sk" style="height:12px;width:85%"></div><div class="sk" style="height:10px;width:50%;margin-top:6px"></div></div></div>`).join("")}</div>`;
const skeletonRows = (n = 6) => Array.from({ length: n }, () => `<div class="list-row"><div class="sk" style="width:28px;height:16px"></div><div class="sk" style="width:52px;height:52px;border-radius:12px"></div><div class="grow col gap-4"><div class="sk" style="height:12px;width:60%"></div><div class="sk" style="height:10px;width:35%"></div></div></div>`).join("");

function empty(icon, title, text, action = "") {
  return `<div class="empty"><div class="art">${ic(icon)}</div><h3>${esc(title)}</h3><p>${text}</p>${action}</div>`;
}

/* paginated list with infinite scroll */
function pager(container, fetcher, render, { emptyHtml = "", skeleton = skeletonCards(3), onItems } = {}) {
  let cursor = null, done = false, busy = false, total = 0;
  const list = document.createElement("div"); const sentinel = document.createElement("div"); sentinel.className = "load-more";
  container.innerHTML = ""; container.append(list, sentinel);
  list.innerHTML = skeleton;
  const all = [];
  async function more() {
    if (busy || done) return; busy = true;
    if (total) sentinel.innerHTML = '<div class="spinner"></div>';
    try {
      const d = await fetcher(cursor);
      const items = d.items || [];
      if (!total) list.innerHTML = "";
      list.insertAdjacentHTML("beforeend", items.map((it, i) => render(it, total + i)).join(""));
      all.push(...items); total += items.length;
      cursor = d.next; done = !d.next;
      if (!total) list.innerHTML = emptyHtml;
      onItems && onItems(items, all);
    } catch (e) { if (!total) list.innerHTML = empty("x", "Couldn't load this", esc(e.message)); done = true; }
    busy = false; sentinel.innerHTML = "";
    if (!done && sentinel.getBoundingClientRect().top < innerHeight + 600) more();
  }
  const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && more(), { rootMargin: "800px" });
  io.observe(sentinel);
  more();
  return { more, all, get done() { return done; }, list };
}

/* ---------------- actions ---------------- */
function syncCounts(id) {
  const c = STORE.get(id); if (!c) return;
  $$(`[data-like="${id}"]`).forEach((b) => { b.classList.toggle("on", c.liked); const n = $(".n", b); if (n) n.textContent = c.counts.like ? num(c.counts.like) : (b.closest(".rail-act") || b.closest(".big-actions") ? "0" : ""); });
  $$(`[data-save="${id}"]`).forEach((b) => { b.classList.toggle("on", c.saved); const n = $(".n", b); if (n) n.textContent = b.closest(".rail-act") || b.closest(".big-actions") ? num(c.counts.save) : ""; });
  $$(`[data-share="${id}"] .n`).forEach((n) => n.textContent = c.counts.share ? num(c.counts.share) : "");
}
async function toggleLike(id, force) {
  if (!need()) return;
  const c = STORE.get(id); if (!c) return;
  if (force === true && c.liked) return;
  c.liked = !c.liked; c.counts.like += c.liked ? 1 : -1; syncCounts(id);
  if (navigator.vibrate && c.liked) navigator.vibrate(8);
  try { const r = await POST(`/api/files/${id}/like`); c.liked = r.on; c.counts.like = r.count; syncCounts(id); }
  catch (e) { c.liked = !c.liked; c.counts.like += c.liked ? 1 : -1; syncCounts(id); fail(e); }
}
async function toggleSave(id) {
  if (!need()) return;
  const c = STORE.get(id); if (!c) return;
  c.saved = !c.saved; c.counts.save = (c.counts.save || 0) + (c.saved ? 1 : -1); syncCounts(id);
  try { const r = await POST(`/api/files/${id}/save`); c.saved = r.on; c.counts.save = r.count; syncCounts(id); if (r.on) toast("Saved to your library"); }
  catch (e) { fail(e); }
}

function shareLink(c) { return `${location.origin}/f/${c.id}` + (c.visibility === "link" && (c.k || KEY) ? `?k=${c.k || KEY}` : ""); }
function shareSheet(c) {
  const url = shareLink(c);
  const text = `${c.title} — on Collab Teams`;
  const nets = [
    ["whatsapp", "WhatsApp", "#25D366", `https://wa.me/?text=${encodeURIComponent(text + " " + url + "?src=whatsapp")}`],
    ["x", "X", "#000", `https://x.com/intent/post?text=${encodeURIComponent(text)}&url=${encodeURIComponent(url)}`],
    ["linkedin", "LinkedIn", "#0A66C2", `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(url)}`],
    ["telegram", "Telegram", "#229ED9", `https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`],
    ["facebook", "Facebook", "#1877F2", `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(url)}`],
  ];
  const native = navigator.share ? `<button data-net="native"><span class="ic" style="background:var(--files)">${ic("share")}</span>More…</button>` : "";
  const mail = `<button data-net="email"><span class="ic" style="background:#6b7280">${ic("mail")}</span>Email</button>`;
  const embedBtn = c.visibility === "public" || c.visibility === "link" ? `<button data-net="embed"><span class="ic" style="background:#334155">${ic("code")}</span>Embed</button>` : "";
  const s = sheet({ title: "Share", body: `
    ${c.visibility === "private" || c.visibility === "org" ? `<div class="notice" style="margin-bottom:14px">${ic(VIS[c.visibility][0])}<span>This file is ${c.visibility === "org" ? "only visible to organization members" : "private"} — people without access won't be able to open the link.</span></div>` : ""}
    <div class="share-grid">${nets.map(([k, n, col]) => `<button data-net="${k}"><span class="ic" style="background:${col}">${ic("b-" + k, "fill lg")}</span>${n}</button>`).join("")}${mail}${embedBtn}${native}</div>
    <div class="copyrow"><input class="input" readonly value="${esc(url)}"><button class="btn primary" data-net="copy">${ic("copy", "sm")}Copy</button></div>
    <div data-embed class="hide" style="margin-top:14px"><label class="muted" style="font-size:13px;font-weight:600">Embed code</label><textarea class="textarea embed-code" readonly rows="3">&lt;iframe src="${esc(location.origin)}/embed/${c.id}${c.visibility === "link" && (c.k || KEY) ? "?k=" + (c.k || KEY) : ""}" width="100%" height="520" style="border:0;border-radius:16px" loading="lazy"&gt;&lt;/iframe&gt;</textarea></div>` });
  const record = (ch) => POST(`/api/files/${c.id}/share`, { channel: ch, k: c.k || KEY || null }).then((r) => { if (r.counted) { c.counts.share++; syncCounts(c.id); } }).catch(() => {});
  s.el.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-net]"); if (!b) return;
    const ch = b.dataset.net;
    if (ch === "copy") { await navigator.clipboard.writeText(url).catch(() => { $(".copyrow input", s.el).select(); document.execCommand("copy"); }); toast("Link copied"); record("copy"); return; }
    if (ch === "native") { try { await navigator.share({ title: c.title, text, url }); record("native"); } catch (e) { /* cancelled */ } return; }
    if (ch === "embed") { $("[data-embed]", s.el).classList.remove("hide"); $("[data-embed] textarea", s.el).select(); record("embed"); return; }
    if (ch === "email") { location.href = `mailto:?subject=${encodeURIComponent(c.title)}&body=${encodeURIComponent(text + "\n" + url)}`; record("email"); return; }
    const n = nets.find((x) => x[0] === ch); window.open(n[3], "_blank", "noopener,width=640,height=640"); record(ch);
  });
}

async function addToCollection(id) {
  if (!need()) return;
  const d = await GET("/api/collections?mine=true");
  const s = sheet({ title: "Add to collection", body: `<div class="col gap-4">${d.items.map((cl) => `<button class="list-row" data-cl="${cl.id}" style="text-align:left"><span class="mini-thumb">${cl.covers[0] ? `<img src="${cl.covers[0]}">` : ic("layers")}</span><div class="grow"><b>${esc(cl.title)}</b><div class="muted" style="font-size:13px">${plural(cl.count, "item")}</div></div>${ic("plus")}</button>`).join("") || `<p class="muted">No collections yet.</p>`}</div>
    <div class="row" style="margin-top:14px"><input class="input" placeholder="New collection name" data-new><button class="btn primary" data-create>Create</button></div>` });
  s.el.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-cl]");
    try {
      if (b) { await POST(`/api/collections/${b.dataset.cl}/items`, { file_ids: [id] }); toast("Added to collection"); s.close(); }
      if (e.target.closest("[data-create]")) {
        const t = $("[data-new]", s.el).value.trim(); if (!t) return;
        const r = await POST("/api/collections", { title: t }); await POST(`/api/collections/${r.id}/items`, { file_ids: [id] }); toast("Collection created"); s.close();
      }
    } catch (err) { fail(err); }
  });
}

async function featureInOrg(id) {
  const orgs = (ME.orgs || []).filter((o) => ["owner", "admin", "editor"].includes(o.role));
  if (!orgs.length) return toast("You need to be an editor in an organization to feature files", "err");
  const c = STORE.get(id);
  const s = sheet({ title: "Feature in organization", body: `<p class="muted" style="margin-top:0">${c && c.mine ? "It will appear in the organization's library right away." : "The uploader will be asked to approve before it appears."}</p>
    <div class="field"><label>Organization</label><select class="select" data-o>${orgs.map((o) => `<option value="${esc(o.slug)}">${esc(o.name)}</option>`).join("")}</select></div>
    <div class="field"><label>Note to uploader (optional)</label><input class="input" data-note maxlength="300" placeholder="Why this belongs in our library"></div>`,
    foot: `<button class="btn" data-x2>Cancel</button><button class="btn green" data-go>${ic("star", "sm")}Feature</button>` });
  $("[data-x2]", s.el).onclick = s.close;
  $("[data-go]", s.el).onclick = async () => {
    try { const r = await POST(`/api/orgs/${$("[data-o]", s.el).value}/feature`, { file_id: id, note: $("[data-note]", s.el).value }); toast(r.status === "approved" ? "Featured" : "Request sent to the uploader"); s.close(); }
    catch (e) { fail(e); }
  };
}

function reportSheet(target) {
  if (!need()) return;
  const reasons = [["spam", "Spam or misleading"], ["copyright", "Copyright violation"], ["abuse", "Harassment or hate"], ["explicit", "Sexual or explicit content"], ["privacy", "Shares private information"], ["other", "Something else"]];
  const s = sheet({ title: "Report", body: `<div class="col gap-4">${reasons.map(([k, l], i) => `<label class="list-row" style="cursor:pointer"><input type="radio" name="r" value="${k}" ${i ? "" : "checked"}><span>${l}</span></label>`).join("")}</div><div class="field" style="margin-top:12px"><textarea class="textarea" data-d placeholder="Add details (optional)" maxlength="1000"></textarea></div>`,
    foot: `<button class="btn" data-x2>Cancel</button><button class="btn danger solid" data-go>Report</button>` });
  $("[data-x2]", s.el).onclick = s.close;
  $("[data-go]", s.el).onclick = async () => {
    try { await POST("/api/report", Object.assign({ reason: $("input[name=r]:checked", s.el).value, details: $("[data-d]", s.el).value }, target)); toast("Thanks — moderators will review this"); s.close(); }
    catch (e) { fail(e); }
  };
}

function fileMenu(anchor, id) {
  const c = STORE.get(id); if (!c) return;
  menu(anchor, [
    { label: "Open", icon: "external", href: fileUrl(c, "feed") },
    { label: "Copy link", icon: "link", on: () => { navigator.clipboard.writeText(shareLink(c)); toast("Link copied"); POST(`/api/files/${id}/share`, { channel: "copy" }).catch(() => {}); } },
    ME && { label: "Add to collection", icon: "layers", on: () => addToCollection(id) },
    ME && (ME.orgs || []).some((o) => o.role !== "member") && c.visibility === "public" && { label: "Feature in organization", icon: "star", on: () => featureInOrg(id) },
    c.mine && { label: "Analytics", icon: "chart", href: `/analytics?scope=file:${id}` },
    c.mine && "-",
    c.mine && { label: "Edit details", icon: "edit", on: () => editSheet(id) },
    c.mine && { label: "Delete", icon: "trash", danger: true, on: () => deleteFiles([id]) },
    !c.mine && "-",
    !c.mine && { label: "Report", icon: "flag", danger: true, on: () => reportSheet({ file_id: id }) },
  ]);
}

async function deleteFiles(ids, after) {
  if (!(await confirmSheet(`Delete ${ids.length === 1 ? "this file" : ids.length + " files"} permanently? Likes, comments and stats go too.`, { ok: "Delete", danger: true }))) return;
  try {
    const r = ids.length === 1 ? await api("DELETE", `/api/files/${ids[0]}`) : await POST("/api/bulk", { ids, action: "delete" });
    toast(ids.length === 1 ? "Deleted" : `Deleted ${r.affected} file${r.affected === 1 ? "" : "s"}`);
    ids.forEach((id) => $$(`[data-card="${id}"],[data-tile="${id}"]`).forEach((el) => el.remove()));
    if (after) after(); else if (BOOT.page === "file") location.href = "/library";
  } catch (e) { fail(e); }
}

function visOptions(cur, withOrg = true) {
  return Object.entries(VIS).filter(([k]) => withOrg || k !== "org").map(([k, [i, l]]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${l}</option>`).join("");
}
function orgOptions(cur) {
  return `<option value="">— None —</option>` + (ME.orgs || []).map((o) => `<option value="${esc(o.slug)}" ${o.slug === cur ? "selected" : ""}>${esc(o.name)}</option>`).join("");
}
async function editSheet(id) {
  const c = await GET(`/api/files/${id}`);
  const s = sheet({ title: "Edit details", wide: true, body: `
    <div class="field"><label>Title</label><input class="input" data-f="title" value="${esc(c.title)}" maxlength="200"></div>
    <div class="field"><label>Description</label><textarea class="textarea" data-f="description" maxlength="5000">${esc(c.description)}</textarea></div>
    <div class="field"><label>Tags</label><input class="input" data-f="tags" value="${esc((c.tags || []).join(", "))}" placeholder="e.g. safety, onboarding"><span class="hint">Comma separated. Tags become topics people can follow.</span></div>
    <div class="row gap-12 wrap"><div class="field grow"><label>Who can see it</label><select class="select" data-f="visibility">${visOptions(c.visibility)}</select></div>
    <div class="field grow"><label>Organization</label><select class="select" data-f="org">${orgOptions(c.org && c.org.slug)}</select></div></div>
    ${c.is_owner ? `<div class="field"><label>Collaborators</label><input class="input" data-f="collaborators" value="${esc((c.collaborators || []).map((x) => x.email || x.username).join(", "))}" placeholder="emails or usernames"><span class="hint">Collaborators can view private files and edit details.</span></div>` : ""}
    <label class="row between" style="padding:8px 0"><span>Allow comments</span><input type="checkbox" class="sr" data-f="allow_comments" ${c.allow_comments ? "checked" : ""}><span class="switch"></span></label>
    <label class="row between" style="padding:8px 0"><span>Allow downloads</span><input type="checkbox" class="sr" data-f="allow_download" ${c.allow_download ? "checked" : ""}><span class="switch"></span></label>`,
    foot: `<button class="btn" data-x2>Cancel</button><button class="btn primary" data-go>Save changes</button>` });
  $("[data-x2]", s.el).onclick = s.close;
  $("[data-go]", s.el).onclick = async () => {
    const b = {}; $$("[data-f]", s.el).forEach((el) => b[el.dataset.f] = el.type === "checkbox" ? el.checked : el.value);
    try { await api("PATCH", `/api/files/${id}`, b); toast("Saved"); s.close(); if (BOOT.page === "file") location.reload(); }
    catch (e) { fail(e); }
  };
}

/* global delegation */
document.addEventListener("click", (e) => {
  const t = e.target;
  let b;
  if ((b = t.closest("[data-like]"))) { e.preventDefault(); toggleLike(+b.dataset.like); return; }
  if ((b = t.closest("[data-save]"))) { e.preventDefault(); toggleSave(+b.dataset.save); return; }
  if ((b = t.closest("[data-share]"))) { e.preventDefault(); const c = STORE.get(+b.dataset.share); c && shareSheet(c); return; }
  if ((b = t.closest("[data-more]"))) { e.preventDefault(); e.stopPropagation(); fileMenu(b, +b.dataset.more); return; }
  if ((b = t.closest("[data-follow]"))) { e.preventDefault(); follow(b); return; }
  if ((b = t.closest('[data-act="theme"]'))) { setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"); return; }
  if ((b = t.closest('[data-menu="me"]'))) { e.stopPropagation(); meMenu(b); return; }
  if ((b = t.closest('[data-act="open-search"]'))) { $("#topbar").classList.add("search-open"); $("#q").focus(); return; }
  if ((b = t.closest("[data-open]")) && isMobile() && b.closest("[data-reel]")) {
    e.preventDefault(); const host = b.closest("[data-reel]"); openReel(host._reelSource(), +b.dataset.open, host._reelMore); return;
  }
});
async function follow(b) {
  if (!need()) return;
  const body = {}; body[b.dataset.followKind || "user"] = b.dataset.follow;
  b.disabled = true;
  try {
    const r = await POST("/api/follow", body);
    b.classList.toggle("on", r.on); b.classList.toggle("primary", !r.on);
    b.innerHTML = r.on ? `${ic("check", "sm")}Following` : `${ic("plus", "sm")}Follow`;
    const n = $("[data-followers]"); if (n) n.textContent = num(r.followers);
  } catch (e) { fail(e); }
  b.disabled = false;
}
const followBtn = (kind, id, on, sm = "") => `<button class="btn ${sm} ${on ? "on" : "primary"}" data-follow="${esc(id)}" data-follow-kind="${kind}">${on ? ic("check", "sm") + "Following" : ic("plus", "sm") + "Follow"}</button>`;

function meMenu(anchor) {
  menu(anchor, [
    { label: "Your profile", icon: "user", href: `/u/${ME.username}` },
    { label: "Library", icon: "library", href: "/library" },
    { label: "Analytics", icon: "chart", href: "/analytics" },
    { label: "Settings", icon: "settings", href: "/settings" },
    "-",
    { label: "Sign out", icon: "logout", on: logout },
  ]);
}
async function logout() {
  try { const r = await POST("/api/logout"); location.href = r.redirect || "/"; } catch (e) { location.href = "/logout"; }
}

/* ---------------- full-screen swipe viewer ---------------- */
function pageSrc(c, n = 1, w = 700) {
  const k = c.k || (c.visibility === "link" ? KEY : "");
  return `/page/${c.id}/${n}.webp?w=${w}${k ? "&k=" + k : ""}`;
}
const hasPages = (c) => ["pdf", "docx", "pptx", "xlsx", "xls", "doc"].includes(c.type) && c.thumb;
function slideHtml(c) {
  const bg = c.thumb ? `<div class="bgblur" style="background-image:url('${c.thumb}')"></div>` : "";
  const doc = c.thumb ? `<div class="doc"><img src="${hasPages(c) ? pageSrc(c) : c.thumb}" alt="" ${hasPages(c) ? `onerror="this.onerror=null;this.src='${c.thumb}'"` : ""}></div>`
    : `<div class="doc text">${esc(c.text || c.description || c.title)}</div>`;
  const a = c.author;
  return `<section class="slide" data-slide="${c.id}">${bg}${doc}
    <div class="info"><a href="/u/${esc(a.username)}" class="row" style="gap:10px">${avatar(a, "sm")}<b>${esc(a.name)}</b>${c.org ? `<span style="opacity:.75">· ${esc(c.org.name)}</span>` : ""}</a>
      <h3 class="clamp-2">${esc(c.title)}</h3>${c.description || c.summary ? `<p class="clamp-2">${esc(c.description || c.summary)}</p>` : ""}
      <div class="row wrap" style="margin-top:8px;gap:6px">${(c.topics || []).map((t) => `<a href="/t/${esc(t.slug)}" style="font-size:13px;font-weight:600">#${esc(t.name)}</a>`).join("")}${c.pages ? `<span style="font-size:12.5px;opacity:.7">${plural(c.pages, "page")}</span>` : ""}</div></div>
    <div class="rail-act">
      <button class="${c.liked ? "on" : ""}" data-like="${c.id}"><span class="c">${ic("heart")}</span><span class="n">${num(c.counts.like)}</span></button>
      <a href="/f/${c.id}?src=feed#comments"><span class="c">${ic("message")}</span><span>${num(c.counts.comment)}</span></a>
      <button class="sv ${c.saved ? "on" : ""}" data-save="${c.id}"><span class="c">${ic("bookmark")}</span><span class="n">${num(c.counts.save)}</span></button>
      <button data-share="${c.id}"><span class="c">${ic("send")}</span><span class="n">${num(c.counts.share)}</span></button>
      <a href="/f/${c.id}?src=feed"><span class="c">${ic("expand")}</span><span>Open</span></a>
    </div></section>`;
}
function openReel(items, startId, more) {
  const reel = document.createElement("div"); reel.className = "reel";
  const top = document.createElement("div"); top.className = "reel-top";
  top.innerHTML = `<button class="iconbtn" data-close aria-label="Close">${ic("chev-l")}</button><span style="font-weight:700;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.4)">For you</span><span style="width:40px"></span>`;
  reel.innerHTML = items.map(slideHtml).join("");
  document.body.append(reel, top); document.body.style.overflow = "hidden";
  const start = reel.querySelector(`[data-slide="${startId}"]`);
  if (start) reel.scrollTop = start.offsetTop;
  if (items.indexOf(items.find((x) => x.id === startId)) === 0 && !localStorage.getItem("reelHint")) {
    const h = document.createElement("div"); h.className = "reel-hint"; h.innerHTML = `${ic("chev-u", "lg")}Swipe up for more`; (start || reel.firstElementChild).appendChild(h);
    localStorage.setItem("reelHint", "1"); setTimeout(() => h.remove(), 3500);
  }
  history.pushState({ reel: 1 }, "");
  const seen = new Set(), timers = new Map();
  let count = items.length, loading = false;
  const io = new IntersectionObserver((es) => es.forEach((e) => {
    const id = +e.target.dataset.slide;
    if (e.isIntersecting && e.intersectionRatio > .6) {
      if (!seen.has(id)) timers.set(id, setTimeout(() => { seen.add(id); POST(`/api/files/${id}/view`, { src: "feed" }).catch(() => {}); }, 1500));
      const idx = $$(".slide", reel).indexOf(e.target);
      if (more && !loading && idx >= count - 3) {
        loading = true;
        more().then((added) => { if (added && added.length) { reel.insertAdjacentHTML("beforeend", added.map(slideHtml).join("")); count += added.length; $$(".slide", reel).slice(-added.length).forEach((s) => io.observe(s)); } loading = false; });
      }
    } else if (timers.has(id)) { clearTimeout(timers.get(id)); timers.delete(id); }
  }), { root: reel, threshold: [0, .6, 1] });
  $$(".slide", reel).forEach((s) => io.observe(s));
  let lastTap = 0;
  reel.addEventListener("click", (e) => {
    const d = e.target.closest(".doc"); if (!d) return;
    const id = +d.closest(".slide").dataset.slide;
    const now = Date.now();
    if (now - lastTap < 300) {
      toggleLike(id, true);
      const b = document.createElement("div"); b.className = "burst"; b.innerHTML = ic("heart"); d.closest(".slide").appendChild(b); setTimeout(() => b.remove(), 800);
      lastTap = 0; clearTimeout(reel._tap);
    } else { lastTap = now; reel._tap = setTimeout(() => { location.href = `/f/${id}?src=feed`; }, 320); }
  });
  const close = () => { io.disconnect(); reel.remove(); top.remove(); document.body.style.overflow = ""; removeEventListener("popstate", onPop); };
  const onPop = () => close();
  addEventListener("popstate", onPop);
  $("[data-close]", top).onclick = () => history.back();
  const onKey = (e) => { if (!document.body.contains(reel)) return document.removeEventListener("keydown", onKey); if (e.key === "Escape") history.back(); if (e.key === "ArrowDown" || e.key === "j") reel.scrollBy({ top: innerHeight, behavior: "smooth" }); if (e.key === "ArrowUp" || e.key === "k") reel.scrollBy({ top: -innerHeight, behavior: "smooth" }); };
  document.addEventListener("keydown", onKey);
}
/* make a list container reel-capable: tapping a thumbnail on mobile opens the swipe viewer over its items */
function reelify(container, pg) {
  container.setAttribute("data-reel", "");
  container._reelSource = () => pg.all;
  container._reelMore = async () => { const before = pg.all.length; await pg.more(); return pg.all.slice(before); };
}

/* ---------------- notifications badge ---------------- */
async function pollCount() {
  if (!ME) return;
  try {
    const r = await api("GET", "/api/notifications/count", undefined, { quiet: true });
    $$("[data-ncount]").forEach((el) => { el.textContent = r.unread > 99 ? "99+" : r.unread; el.classList.toggle("hide", !r.unread); });
    document.title = document.title.replace(/^\(\d+\+?\) /, "");
    if (r.unread) document.title = `(${r.unread > 99 ? "99+" : r.unread}) ` + document.title;
  } catch (e) { /* ignore */ }
}
if (ME) { pollCount(); setInterval(() => !document.hidden && pollCount(), 60000); document.addEventListener("visibilitychange", () => !document.hidden && pollCount()); }

/* ---------------- search suggest ---------------- */
(function () {
  const input = $("#q"); if (!input) return;
  const form = input.closest("form");
  let box = null, idx = -1;
  const close = () => { box && box.remove(); box = null; idx = -1; };
  const render = (d, term) => {
    close();
    const parts = [];
    if (d.files && d.files.length) parts.push(`<div class="s-h">Documents</div>` + d.files.map((f) => `<a href="/f/${f.id}?src=search"><span class="mini-thumb" style="width:30px;height:30px;border-radius:8px;font-size:9px;font-weight:800;color:var(--files)">${FT_LABEL[f.type] || ""}</span><span class="clamp-1">${esc(f.title)}</span></a>`).join(""));
    if (d.people && d.people.length) parts.push(`<div class="s-h">People</div>` + d.people.map((p) => `<a href="/u/${esc(p.username)}">${avatar(p, "sm")}<span><b>${esc(p.name)}</b> <span class="muted">@${esc(p.username)}</span></span></a>`).join(""));
    if (d.orgs && d.orgs.length) parts.push(`<div class="s-h">Organizations</div>` + d.orgs.map((o) => `<a href="/o/${esc(o.slug)}">${avatar(o, "sm", true)}<span><b>${esc(o.name)}</b>${verified(o.verified)}</span></a>`).join(""));
    if (d.topics && d.topics.length) parts.push(`<div class="s-h">${term ? "Topics" : "Trending topics"}</div><div class="row wrap" style="padding:4px 10px 8px;gap:6px">` + d.topics.map((t) => `<a class="chip topic" href="/t/${esc(t.slug)}" style="padding:0 10px">#${esc(t.name)}</a>`).join("") + "</div>");
    if (term) parts.push(`<a href="/search?q=${encodeURIComponent(term)}" style="color:var(--disc-ink);font-weight:600">${ic("search", "sm")}Search for “${esc(term)}”</a>`);
    if (!parts.length) return;
    box = document.createElement("div"); box.className = "suggest"; box.innerHTML = parts.join(""); form.appendChild(box);
  };
  const load = debounce(async () => { const term = input.value.trim(); try { render(await GET("/api/suggest?q=" + encodeURIComponent(term)), term); } catch (e) { /* */ } }, 140);
  input.addEventListener("input", load);
  input.addEventListener("focus", load);
  input.addEventListener("keydown", (e) => {
    if (!box) return; const links = $$("a", box);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); idx = (idx + (e.key === "ArrowDown" ? 1 : -1) + links.length) % links.length; links.forEach((l, i) => l.classList.toggle("on", i === idx)); }
    if (e.key === "Enter" && idx >= 0) { e.preventDefault(); location.href = links[idx].href; }
    if (e.key === "Escape") { close(); input.blur(); }
  });
  document.addEventListener("click", (e) => { if (!form.contains(e.target)) { close(); $("#topbar").classList.remove("search-open"); } });
  document.addEventListener("keydown", (e) => { if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); if (isMobile()) $("#topbar").classList.add("search-open"); input.focus(); } });
})();
const topbar = $("#topbar");
if (topbar) addEventListener("scroll", () => topbar.classList.toggle("scrolled", scrollY > 4), { passive: true });
$$("[data-nav]").forEach((a) => { const n = a.dataset.nav; const p = BOOT.page; if (n === p || (n === "top" && p === "topic") || (n === "orgs" && p === "org") || (n === "library" && p === "collection")) a.classList.add("on"); });
if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("/sw.js").catch(() => {});

/* =================================================================== pages */
const V = $("#view");
const PAGES = {};

/* ---------------- home feed ---------------- */
PAGES.home = async (d) => {
  let tab = d.tab, topic = "";
  const tabs = ME ? [["foryou", "For you"], ["following", "Following"], ["latest", "Latest"]] : [["latest", "Latest"], ["foryou", "Popular"]];
  V.innerHTML = `<div class="with-rail"><div class="feed-col">
    ${ME ? "" : `<div class="panel" style="padding:22px;margin-bottom:16px;background:linear-gradient(135deg,var(--files-soft),var(--surface) 60%);overflow:hidden;position:relative">
      <h2 style="font-size:22px">Discover what people are reading</h2><p class="muted" style="margin:6px 0 14px;max-width:440px">Share documents, follow topics and organizations, and build a library together.</p>
      <div class="row"><a class="btn primary" href="/signup">Create free account</a><a class="btn ghost" href="/login">Sign in</a></div></div>`}
    <div class="row between" style="margin-bottom:6px"><div class="tabs" style="margin:0;border:0">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join("")}</div>
      <button class="btn sm ghost" data-immersive data-tip="Full-screen swipe view">${ic("play", "sm")}<span>Immersive</span></button></div>
    <div class="chips" data-topics style="margin-bottom:14px"></div>
    <div data-feed></div></div>
    <aside class="rail">
      <div class="panel"><div class="panel-h"><span class="row">${ic("flame", "sm")}Trending now</span><a class="muted" href="/top" style="font-size:13px">See all</a></div><div class="panel-b" data-trend>${skeletonRows(4)}</div></div>
      <div class="panel"><div class="panel-h"><span class="row">${ic("trophy", "sm")}Top creators</span><a class="muted" href="/top?kind=creators" style="font-size:13px">See all</a></div><div class="panel-b" data-creators>${skeletonRows(3)}</div></div>
      <div class="panel"><div class="panel-h"><span class="row">${ic("building", "sm")}Organizations</span><a class="muted" href="/orgs" style="font-size:13px">See all</a></div><div class="panel-b" data-orgs>${skeletonRows(3)}</div></div>
      <p class="faint" style="font-size:12px;padding:0 8px">Collab Teams · <a href="/top">Top lists</a> · <a href="/orgs">Organizations</a></p>
    </aside></div>`;
  const feedEl = $("[data-feed]", V);
  let pg;
  const load = () => {
    pg = pager(feedEl, (cur) => GET(`/api/feed?${qs({ tab, cursor: cur, topic })}`), (c) => feedCard(c, "feed"), {
      emptyHtml: tab === "following" ? empty("users", "Your following feed is quiet", "Follow people, organizations or topics and their new documents show up here.", `<a class="btn primary" href="/top?kind=creators">Find people to follow</a>`)
        : empty("file", "Nothing here yet", "Be the first to share a document.", ME ? `<a class="btn primary" href="/upload">${ic("upload", "sm")}Upload</a>` : ""),
    });
    reelify(feedEl, pg);
  };
  load();
  $$("[data-tab]", V).forEach((b) => b.onclick = () => { tab = b.dataset.tab; $$("[data-tab]", V).forEach((x) => x.classList.toggle("on", x === b)); history.replaceState(null, "", tab === (ME ? "foryou" : "latest") ? "/" : `/?tab=${tab}`); load(); });
  $("[data-immersive]", V).onclick = () => { if (pg.all.length) openReel(pg.all, pg.all[0].id, feedEl._reelMore); };
  GET("/api/topics?limit=14").then((t) => {
    const el = $("[data-topics]", V);
    if (!t.items.length) return el.remove();
    el.innerHTML = `<button class="chip on" data-t="">All</button>` + t.items.map((x) => `<button class="chip" data-t="${esc(x.slug)}">#${esc(x.name)}</button>`).join("");
    el.onclick = (e) => { const b = e.target.closest("[data-t]"); if (!b) return; topic = b.dataset.t; $$("[data-t]", el).forEach((x) => x.classList.toggle("on", x === b)); load(); };
  });
  GET("/api/top?kind=trending&limit=5").then((r) => $("[data-trend]", V).innerHTML = r.items.map((x, i) => rankRow(x, "trending", i)).join("") || `<p class="muted" style="margin:4px 0">Nothing trending yet.</p>`);
  GET("/api/top?kind=creators&window=month&limit=4").then((r) => $("[data-creators]", V).innerHTML = r.items.map((x, i) => rankRow(x, "creators", i)).join("") || `<p class="muted" style="margin:4px 0">No creators yet.</p>`);
  GET("/api/top?kind=orgs&window=all&limit=4").then((r) => $("[data-orgs]", V).innerHTML = r.items.map((x, i) => rankRow(x, "orgs", i)).join("") || `<p class="muted" style="margin:4px 0">No organizations yet. <a href="/orgs" style="color:var(--orgs-ink);font-weight:600">Create one</a></p>`);
};

/* ---------------- file page ---------------- */
PAGES.file = async (c) => {
  c.k = c.visibility === "link" ? (KEY || (c.link.split("k=")[1] || "")) : "";
  keep(c);
  const a = c.author;
  const isText = ["txt", "md", "csv"].includes(c.type);
  const isImg = ["png", "jpg", "jpeg", "webp", "gif"].includes(c.type);
  const kq = c.k ? `k=${c.k}` : "";
  const dl = `/api/files/${c.id}/download?${qs({ k: c.k, src: SRC || "direct" })}`;
  V.innerHTML = `<div class="file-layout">
    <div class="col gap-16" style="min-width:0">
      <div class="viewer">
        <div class="viewer-bar"><a class="iconbtn" href="javascript:history.length>1?history.back():location.href='/'" aria-label="Back">${ic("chev-l")}</a>
          <span class="badge files">${FT_LABEL[c.type] || c.type}</span><span class="muted grow clamp-1" style="font-size:13.5px">${c.pages ? plural(c.pages, "page") + " · " : ""}${c.size ? bytes(c.size) : ""}</span>
          ${c.type !== "url" && !isText && !isImg ? `<a class="btn sm ghost" href="/raw/${c.id}${kq ? "?" + kq : ""}" target="_blank" rel="noopener" data-tip="Open original">${ic("external", "sm")}</a>` : ""}
          ${hasPages(c) && c.pages ? `<button class="btn sm ghost" data-fs data-tip="Full screen">${ic("expand", "sm")}</button>` : ""}
        </div>
        <div data-viewer></div>
      </div>
      <div class="panel" id="comments"><div class="panel-h"><span>Comments <span class="muted" data-ccount>${c.counts.comment ? num(c.counts.comment) : ""}</span></span><div class="seg" data-csort><button class="on" data-s="top">Top</button><button data-s="new">Newest</button></div></div><div class="panel-b" data-comments></div></div>
      <div data-related></div>
    </div>
    <aside class="side-sticky">
      <div class="panel"><div class="panel-b" style="padding-top:18px">
        <div class="row" style="gap:10px;margin-bottom:14px"><a href="/u/${esc(a.username)}">${avatar(a, "md")}</a><div class="grow" style="line-height:1.25"><a href="/u/${esc(a.username)}"><b>${esc(a.name)}</b></a><div class="muted" style="font-size:13px">@${esc(a.username)} · ${esc(c.ago)}</div></div>
          ${c.mine ? `<button class="iconbtn" data-more="${c.id}">${ic("more")}</button>` : followBtn("user", a.username, false, "sm")}</div>
        <h1 style="font-size:22px">${esc(c.title)}</h1>
        ${c.org ? `<a class="org-link" style="margin-top:8px;display:inline-flex" href="/o/${esc(c.org.slug)}">${avatar(c.org, "xs", true)} ${esc(c.org.name)}${verified(c.org.verified)}</a>` : ""}
        ${c.description ? `<p class="about" style="margin:12px 0 0">${esc(c.description)}</p>` : ""}
        ${c.summary ? `<div class="notice info" style="margin-top:12px">${ic("sparkle", "sm")}<span><b>Summary.</b> ${esc(c.summary)}</span></div>` : ""}
        ${c.topics.length ? `<div class="row wrap" style="gap:6px;margin-top:14px">${c.topics.map((t) => `<a class="chip topic" href="/t/${esc(t.slug)}">#${esc(t.name)}</a>`).join("")}</div>` : ""}
        <div class="big-actions" style="margin-top:16px">
          <button class="act like ${c.liked ? "on" : ""}" data-like="${c.id}">${ic("heart")}<span class="n">${num(c.counts.like)}</span></button>
          <button class="act save ${c.saved ? "on" : ""}" data-save="${c.id}">${ic("bookmark")}<span class="n">${num(c.counts.save)}</span></button>
          <button class="act" data-share="${c.id}">${ic("send")}<span class="n">${num(c.counts.share)}</span></button>
          <button class="act" data-more="${c.id}">${ic("more")}<span>More</span></button>
        </div>
        ${c.allow_download ? `<a class="btn primary lg block" style="margin-top:12px" href="${dl}" data-dl>${ic("download")}${c.type === "url" ? "Open link" : "Download"}</a>` : `<div class="notice" style="margin-top:12px">${ic("lock", "sm")}Downloads are turned off for this file</div>`}
        <dl class="meta-list" style="margin-top:16px">
          <dt>Views</dt><dd class="tnum">${num(c.counts.view)}</dd><dt>Downloads</dt><dd class="tnum">${num(c.counts.download)}</dd>
          <dt>Visibility</dt><dd><span class="row" style="justify-content:flex-end;gap:4px">${ic(VIS[c.visibility][0], "sm")}${VIS[c.visibility][1]}</span></dd>
          ${c.version > 1 ? `<dt>Version</dt><dd>v${c.version}</dd>` : ""}
        </dl>
        ${c.mine ? `<div class="row" style="margin-top:14px"><button class="btn sm grow" data-edit>${ic("edit", "sm")}Edit</button><a class="btn sm grow" href="/analytics?scope=file:${c.id}">${ic("chart", "sm")}Stats</a><button class="btn sm grow" data-version>${ic("history", "sm")}New version</button></div>` : ""}
      </div></div>
      ${c.featured_in.length ? `<div class="panel"><div class="panel-h">Featured in</div><div class="panel-b">${c.featured_in.map((o) => `<a class="list-row" href="/o/${esc(o.slug)}"><span class="av sm org" style="--c:var(--orgs)">${esc(o.name[0])}</span><b class="grow">${esc(o.name)}</b>${o.status !== "approved" ? `<span class="badge stats">${esc(o.status)}</span>` : ""}</a>`).join("")}</div></div>` : ""}
      ${c.is_owner && c.collaborators.length ? `<div class="panel"><div class="panel-h">Collaborators</div><div class="panel-b">${c.collaborators.map((x) => `<div class="list-row">${avatar({ name: x.name || x.email, initials: (x.name || x.email || "?")[0].toUpperCase() }, "sm")}<div class="grow"><b>${esc(x.name || x.email)}</b>${x.registered ? "" : `<div class="muted" style="font-size:12px">Invited — not on Teams yet</div>`}</div></div>`).join("")}</div></div>` : ""}
      ${c.versions.length > 1 ? `<div class="panel"><div class="panel-h">Version history</div><div class="panel-b">${c.versions.map((v) => `<div class="list-row"><span class="badge">v${v.version}</span><div class="grow"><div class="clamp-1" style="font-size:13.5px">${esc(v.note || v.original_name)}</div><div class="muted" style="font-size:12px">${new Date(v.created_at).toLocaleDateString()}</div></div>${c.allow_download ? `<a class="iconbtn" href="/api/files/${c.id}/download?v=${v.version}${c.k ? "&k=" + c.k : ""}">${ic("download", "sm")}</a>` : ""}</div>`).join("")}</div></div>` : ""}
    </aside></div>`;

  // viewer
  const vw = $("[data-viewer]", V);
  if (hasPages(c) && c.pages) {
    vw.innerHTML = `<div class="pages">${Array.from({ length: Math.min(c.pages, 300) }, (_, i) => `<div class="page-img" data-n="${i + 1}"><span class="no">${i + 1}</span></div>`).join("")}</div>`;
    const w = Math.min(1600, Math.ceil(vw.clientWidth * (devicePixelRatio || 1) / 100) * 100);
    const io = new IntersectionObserver((es) => es.forEach((e) => {
      if (!e.isIntersecting) return; io.unobserve(e.target);
      const img = new Image(); img.alt = `Page ${e.target.dataset.n}`; img.decoding = "async";
      img.onload = () => { e.target.style.setProperty("--ar", img.naturalWidth / img.naturalHeight); img.classList.add("ok"); };
      img.onerror = () => { e.target.innerHTML = `<div class="empty" style="padding:30px">Couldn't render this page</div>`; };
      img.src = pageSrc(c, e.target.dataset.n, w); e.target.prepend(img);
    }), { root: isMobile() ? null : $(".pages", vw), rootMargin: "1200px" });
    $$(".page-img", vw).forEach((p) => io.observe(p));
  } else if (isImg) {
    vw.innerHTML = `<img class="img-full" src="/raw/${c.id}${kq ? "?" + kq : ""}" alt="${esc(c.title)}">`;
  } else if (isText) {
    vw.innerHTML = `<div class="textdoc">Loading…</div>`;
    fetch(`/raw/${c.id}${kq ? "?" + kq : ""}`).then((r) => r.text()).then((t) => {
      if (c.type === "csv") {
        const rows = t.split(/\r?\n/).filter(Boolean).slice(0, 300).map((l) => l.split(","));
        vw.innerHTML = `<div style="overflow:auto;max-height:calc(100dvh - 160px);background:var(--surface)"><table class="table">${rows.map((r, i) => `<tr>${r.map((x) => i ? `<td>${esc(x)}</td>` : `<th>${esc(x)}</th>`).join("")}</tr>`).join("")}</table></div>`;
      } else $(".textdoc", vw).textContent = t;
    });
  } else if (c.type === "url") {
    vw.innerHTML = `<div class="empty"><div class="art" style="background:var(--disc-soft);color:var(--disc)">${ic("link")}</div><h3 class="clamp-2" style="word-break:break-all">${esc((c.url || "").replace(/^https?:\/\//, ""))}</h3><p>An external link shared by ${esc(a.name)}.</p><a class="btn primary" href="${dl}" target="_blank" rel="noopener">${ic("external", "sm")}Open link</a></div>`;
  } else if (c.thumb) {
    vw.innerHTML = `<div class="pages"><div class="page-img"><img class="ok" src="${c.thumb}${c.k ? "&k=" + c.k : ""}" alt=""></div></div>${c.thumb_status === "pending" ? `<p class="muted" style="text-align:center;padding:8px">Preparing the full preview…</p>` : ""}`;
  } else {
    vw.innerHTML = `<div class="empty"><div class="art">${ic("file")}</div><h3>${c.thumb_status === "pending" ? "Preparing preview…" : "No preview for this file"}</h3><p>${c.allow_download ? "Download it to open on your device." : ""}</p></div>`;
  }
  const fsb = $("[data-fs]", V);
  if (fsb) fsb.onclick = () => { const p = $(".pages", vw); (p.requestFullscreen || p.webkitRequestFullscreen || (() => {})).call(p); };

  POST(`/api/files/${c.id}/view`, { src: SRC || (document.referrer && new URL(document.referrer).host === location.host ? "direct" : (document.referrer ? "external" : "direct")), k: c.k || null }).catch(() => {});
  if ($("[data-edit]", V)) $("[data-edit]", V).onclick = () => editSheet(c.id);
  if ($("[data-version]", V)) $("[data-version]", V).onclick = () => versionSheet(c);
  comments(c, $("[data-comments]", V));
  $("[data-csort]", V).onclick = (e) => { const b = e.target.closest("[data-s]"); if (!b) return; $$("[data-s]", V).forEach((x) => x.classList.toggle("on", x === b)); comments(c, $("[data-comments]", V), b.dataset.s); };
  if (location.hash === "#comments") setTimeout(() => $("#comments").scrollIntoView({ behavior: "smooth" }), 300);
  if (!c.k) GET(`/api/files/${c.id}/related`).then((r) => { if (r.items.length) $("[data-related]", V).innerHTML = `<div class="section-title" style="margin-top:8px">More like this</div><div class="grid">${r.items.map((x) => tile(x, { src: "related" })).join("")}</div>`; }).catch(() => {});
};

function versionSheet(c) {
  const s = sheet({ title: "Upload a new version", body: `<p class="muted" style="margin-top:0">Likes, comments and stats stay with the file. Old versions remain downloadable.</p>
    <div class="field"><input type="file" class="input" data-file></div><div class="field"><label>What changed?</label><input class="input" data-note maxlength="300" placeholder="e.g. Updated figures for Q3"></div>`,
    foot: `<button class="btn" data-x2>Cancel</button><button class="btn primary" data-go>Upload</button>` });
  $("[data-x2]", s.el).onclick = s.close;
  $("[data-go]", s.el).onclick = async () => {
    const f = $("[data-file]", s.el).files[0]; if (!f) return toast("Choose a file", "err");
    const fd = new FormData(); fd.append("file", f); fd.append("note", $("[data-note]", s.el).value);
    $("[data-go]", s.el).disabled = true;
    try { await api("POST", `/api/files/${c.id}/version`, fd); toast("New version uploaded"); setTimeout(() => location.reload(), 600); } catch (e) { fail(e); $("[data-go]", s.el).disabled = false; }
  };
}

/* ---------------- comments ---------------- */
function renderBody(t) {
  return esc(t).replace(/(^|\s)@([a-zA-Z0-9_.]{2,40})/g, '$1<a class="mention" href="/u/$2">@$2</a>').replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener nofollow" style="color:var(--disc-ink);text-decoration:underline">$1</a>');
}
function cmHtml(m) {
  const gone = m.deleted || m.hidden;
  return `<div class="cm" data-cm="${m.id}" data-root="${m.root_id || m.id}" data-user="${esc(m.author.username)}">
    <a href="/u/${esc(m.author.username)}">${avatar(m.author, m.depth ? "sm" : "")}</a>
    <div class="bubble"><div class="head"><a href="/u/${esc(m.author.username)}"><b>${esc(m.author.name)}</b></a>${m.by_owner ? `<span class="owner-tag">Author</span>` : ""}<span class="muted">· ${esc(m.ago)}${m.edited ? " · edited" : ""}</span></div>
      <div class="body ${gone ? "gone" : ""}">${gone ? (m.deleted ? "Comment deleted" : "Hidden by moderators") : (m.reply_to ? `<a class="reply-to" href="/u/${esc(m.reply_to)}">@${esc(m.reply_to)}</a> ` : "") + renderBody(m.body)}</div>
      ${gone ? "" : `<div class="tools"><button data-clike="${m.id}" class="${m.liked ? "on" : ""}">${ic("heart", "sm")}<span>${m.likes || ""}</span></button><button data-reply="${m.id}">Reply</button>${m.can_delete ? `<button data-cdel="${m.id}">Delete</button>` : `<button data-crep="${m.id}">Report</button>`}</div>`}
    </div></div>`;
}
function threadHtml(m) {
  const shown = m.thread || [];
  const rest = (m.thread_total || 0) - shown.length;
  return `<div data-thread="${m.id}">${cmHtml(m)}<div class="thread ${shown.length || rest > 0 ? "" : "hide"}">${shown.map(cmHtml).join("")}${rest > 0 ? `<button class="more-replies" data-more-replies="${m.id}">View ${plural(rest, "more reply").replace("replys", "replies")}</button>` : ""}</div></div>`;
}
function comments(file, el, sort = "top") {
  const box = (ph = "Add a comment…") => ME ? `<div class="composer">${avatar(ME)}<div class="box"><div class="replying hide"></div><textarea rows="1" placeholder="${ph}" maxlength="3000"></textarea><div class="bar"><span class="muted grow" style="font-size:12px;padding-left:8px">Use @username to mention</span><button class="btn sm rose" data-post disabled>${ic("send", "sm")}Post</button></div></div></div>`
    : `<a class="btn block" href="/login?next=${encodeURIComponent(location.pathname + "#comments")}" style="margin-bottom:12px">Sign in to join the conversation</a>`;
  el.innerHTML = `<div data-composer></div><div data-list>${Array.from({ length: 2 }, () => `<div class="cm"><div class="sk" style="width:36px;height:36px;border-radius:50%"></div><div class="grow col gap-4"><div class="sk" style="height:11px;width:30%"></div><div class="sk" style="height:13px;width:80%"></div></div></div>`).join("")}</div><div data-cmore></div>`;
  let replyTo = null;
  GET(`/api/files/${file.id}/comments?${qs({ sort, k: file.k })}`).then((d) => {
    $("[data-composer]", el).innerHTML = d.allow ? box() : `<div class="notice" style="margin-bottom:12px">${ic("lock", "sm")}Comments are turned off for this file</div>`;
    const list = $("[data-list]", el);
    list.innerHTML = d.items.map(threadHtml).join("") || (d.allow ? `<p class="muted" data-none style="text-align:center;padding:16px 0;margin:0">No comments yet — start the conversation.</p>` : "");
    let next = d.next;
    const moreBtn = () => $("[data-cmore]", el).innerHTML = next ? `<button class="btn block ghost" data-cnext>Show more comments</button>` : "";
    moreBtn();
    el.onclick = async (e) => {
      const t = e.target; let b;
      if ((b = t.closest("[data-cnext]"))) { const r = await GET(`/api/files/${file.id}/comments?${qs({ sort, cursor: next, k: file.k })}`); list.insertAdjacentHTML("beforeend", r.items.map(threadHtml).join("")); next = r.next; moreBtn(); }
      else if ((b = t.closest("[data-more-replies]"))) {
        const r = await GET(`/api/comments/${b.dataset.moreReplies}/thread`);
        const th = b.closest(".thread"); th.innerHTML = r.items.map(cmHtml).join("");
      } else if ((b = t.closest("[data-reply]"))) {
        if (!need()) return;
        const cm = b.closest("[data-cm]"); replyTo = { id: +b.dataset.reply, user: cm.dataset.user, root: +cm.dataset.root };
        const ta = $("[data-composer] textarea", el); const rp = $("[data-composer] .replying", el);
        rp.innerHTML = `${ic("reply", "sm")}Replying to <b>@${esc(replyTo.user)}</b> <button data-cancel-reply style="margin-left:auto;color:var(--muted)">${ic("x", "sm")}</button>`; rp.classList.remove("hide");
        ta.focus(); $("[data-composer]", el).scrollIntoView({ behavior: "smooth", block: "center" });
      } else if ((b = t.closest("[data-cancel-reply]"))) { replyTo = null; $("[data-composer] .replying", el).classList.add("hide"); }
      else if ((b = t.closest("[data-clike]"))) {
        if (!need()) return;
        try { const r = await POST(`/api/comments/${b.dataset.clike}/like`); b.classList.toggle("on", r.liked); $("span", b).textContent = r.likes || ""; } catch (err) { fail(err); }
      } else if ((b = t.closest("[data-cdel]"))) {
        if (!(await confirmSheet("Delete this comment?", { ok: "Delete", danger: true }))) return;
        try { await api("DELETE", `/api/comments/${b.dataset.cdel}`); const cm = b.closest("[data-cm]"); const th = cm.parentElement.closest("[data-thread]");
          if (th && th.firstElementChild === cm && $(".thread .cm", th)) { $(".body", cm).className = "body gone"; $(".body", cm).textContent = "Comment deleted"; $(".tools", cm).remove(); } else (th && th.firstElementChild === cm ? th : cm).remove();
          toast("Comment deleted"); } catch (err) { fail(err); }
      } else if ((b = t.closest("[data-crep]"))) reportSheet({ comment_id: +b.dataset.crep });
      else if ((b = t.closest("[data-post]"))) {
        const ta = $("[data-composer] textarea", el); const body = ta.value.trim(); if (!body) return;
        b.disabled = true;
        try {
          const r = await POST(`/api/files/${file.id}/comments`, { body, parent_id: replyTo ? replyTo.id : null, k: file.k || null });
          ta.value = ""; ta.style.height = "";
          $("[data-none]", el) && $("[data-none]", el).remove();
          if (replyTo) {
            const th = $(`[data-thread="${replyTo.root}"] .thread`, el); th.classList.remove("hide"); th.insertAdjacentHTML("beforeend", cmHtml(r.comment));
            replyTo = null; $("[data-composer] .replying", el).classList.add("hide");
          } else list.insertAdjacentHTML("afterbegin", threadHtml(r.comment));
          const cc = $("[data-ccount]"); file.counts.comment++; cc.textContent = num(file.counts.comment);
        } catch (err) { fail(err); }
        b.disabled = !ta.value.trim();
      }
    };
    const ta = $("[data-composer] textarea", el);
    if (ta) {
      ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = Math.min(180, ta.scrollHeight) + "px"; $("[data-post]", el).disabled = !ta.value.trim(); });
      ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("[data-post]", el).click(); });
    }
  }).catch((e) => el.innerHTML = `<p class="muted">${esc(e.message)}</p>`);
}

/* ---------------- search ---------------- */
PAGES.search = async (d) => {
  const q = d.q || "";
  let type = d.type || "all", ftype = "";
  $("#q") && ($("#q").value = q);
  V.innerHTML = `<div class="page-narrow">
    <form class="searchbox big" data-sform style="margin-bottom:14px">${ic("search")}<input name="q" value="${esc(q)}" placeholder="Search documents, people, organizations, topics" autocomplete="off" enterkeyhint="search"></form>
    <div class="tabs">${[["all", "All"], ["files", "Documents"], ["people", "People"], ["orgs", "Organizations"], ["topics", "Topics"]].map(([k, l]) => `<button data-type="${k}" class="${k === type ? "on" : ""}">${l}</button>`).join("")}</div>
    <div data-res></div></div>`;
  $("[data-sform]", V).onsubmit = (e) => { e.preventDefault(); const v = e.target.q.value.trim(); location.href = "/search?q=" + encodeURIComponent(v); };
  const res = $("[data-res]", V);
  if (!q) { res.innerHTML = empty("search", "Search everything", "Search finds words inside public documents too — try a phrase in quotes like <b>\"fire safety\"</b>.") ; const t = await GET("/api/topics?limit=20"); if (t.items.length) res.insertAdjacentHTML("beforeend", `<div class="section-title">Popular topics</div><div class="row wrap" style="gap:8px">${t.items.map((x) => `<a class="chip topic" href="/t/${esc(x.slug)}">#${esc(x.name)} <span class="muted">${x.files}</span></a>`).join("")}</div>`); return; }
  const peopleRow = (p) => `<a class="list-row" href="/u/${esc(p.username)}">${avatar(p, "md")}<div class="grow"><b>${esc(p.name)}</b><div class="muted clamp-1" style="font-size:13px">@${esc(p.username)}${p.bio ? " · " + esc(p.bio) : ""}</div></div><span class="muted" style="font-size:13px">${plural(p.files || 0, "file")}</span></a>`;
  const orgRow = (o) => `<a class="list-row" href="/o/${esc(o.slug)}">${avatar(o, "md", true)}<div class="grow"><b>${esc(o.name)}</b>${verified(o.verified)}<div class="muted clamp-1" style="font-size:13px">${esc(o.tagline || "@" + o.slug)}</div></div><span class="muted" style="font-size:13px">${plural(o.followers || 0, "follower")}</span></a>`;
  const topicRow = (t) => `<a class="chip topic" href="/t/${esc(t.slug)}" style="height:34px;padding:0 14px">#${esc(t.name)} <span class="muted">${t.files || ""}</span></a>`;
  const run = async () => {
    res.innerHTML = skeletonCards(2);
    const first = await GET(`/api/search?${qs({ q, type, ftype })}`);
    let h = "";
    const anyOther = first.people.length || first.orgs.length || first.topics.length;
    if (type === "all" && first.topics.length) h += `<div class="row wrap" style="gap:8px;margin-bottom:14px">${first.topics.map(topicRow).join("")}</div>`;
    if (type === "all" && (first.people.length || first.orgs.length)) h += `<div class="panel" style="margin-bottom:16px"><div class="panel-b">${first.orgs.slice(0, 3).map(orgRow).join("")}${first.people.slice(0, 3).map(peopleRow).join("")}</div></div>`;
    if (type === "people") h += first.people.map(peopleRow).join("") || empty("users", "No people found", `Nobody matches “${esc(q)}”.`);
    if (type === "orgs") h += first.orgs.map(orgRow).join("") || empty("building", "No organizations found", `Nothing matches “${esc(q)}”.`);
    if (type === "topics") h += `<div class="row wrap" style="gap:8px">${first.topics.map(topicRow).join("")}</div>` + (first.topics.length ? "" : empty("hash", "No topics found", ""));
    if (type === "all" || type === "files") h += `<div class="row between" style="margin:4px 0 10px"><div class="section-title" style="margin:0">Documents</div><select class="select sm" data-ftype style="width:auto">${[["", "Any type"], ["pdf", "PDF"], ["docx", "Word"], ["pptx", "PowerPoint"], ["xlsx", "Excel"], ["image", "Images"], ["txt", "Text"], ["url", "Links"]].map(([k, l]) => `<option value="${k}" ${k === ftype ? "selected" : ""}>${l}</option>`).join("")}</select></div><div data-files></div>`;
    res.innerHTML = h;
    if (type === "all" || type === "files") {
      $("[data-ftype]", res).onchange = (e) => { ftype = e.target.value; run(); };
      const fe = $("[data-files]", res);
      let firstUsed = false;
      const pg = pager(fe, (cur) => { if (!cur && !firstUsed) { firstUsed = true; return Promise.resolve({ items: first.files, next: first.next }); } return GET(`/api/search?${qs({ q, type: "files", ftype, cursor: cur })}`); },
        (c) => feedCard(c, "search"), { emptyHtml: anyOther && type === "all" ? `<p class="muted">No documents match “${esc(q)}”.</p>` : empty("search", "No results", `We couldn't find anything for “${esc(q)}”. Try fewer words or check the spelling.`) });
      reelify(fe, pg);
    }
  };
  $$("[data-type]", V).forEach((b) => b.onclick = () => { type = b.dataset.type; $$("[data-type]", V).forEach((x) => x.classList.toggle("on", x === b)); history.replaceState(null, "", `/search?${qs({ q, type: type === "all" ? "" : type })}`); run(); });
  run().catch(fail);
};

/* ---------------- topic ---------------- */
PAGES.topic = async (t) => {
  let tab = "latest";
  V.innerHTML = `<div class="with-rail"><div class="feed-col">
    <div class="hero topic-hero"><div class="cover" style="background:linear-gradient(120deg,var(--disc),var(--files))"><span class="hash">#</span></div>
      <div class="hero-b"><div class="row between wrap" style="gap:12px"><div><h1>#${esc(t.name)}</h1>
        <div class="stats"><span><b>${num(t.stats.files)}</b> documents</span><span><b>${num(t.stats.creators)}</b> creators</span><span><b data-followers>${num(t.followers)}</b> followers</span><span><b>${num(t.stats.downloads)}</b> downloads</span></div></div>
        ${followBtn("topic", t.slug, t.following)}</div></div></div>
    <div class="tabs">${[["latest", "Latest"], ["foryou", "Popular"]].map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join("")}<a href="/top?topic=${esc(t.slug)}" style="margin-left:auto" class="btn sm ghost">${ic("trophy", "sm")}Top in #${esc(t.name)}</a></div>
    <div data-feed></div></div>
    <aside class="rail">${t.related.length ? `<div class="panel"><div class="panel-h">Related topics</div><div class="panel-b row wrap" style="gap:6px">${t.related.map((r) => `<a class="chip topic" href="/t/${esc(r.slug)}">#${esc(r.name)}</a>`).join("")}</div></div>` : ""}
      <div class="panel"><div class="panel-h"><span class="row">${ic("trophy", "sm")}Top creators</span></div><div class="panel-b" data-creators>${skeletonRows(3)}</div></div></aside></div>`;
  const fe = $("[data-feed]", V);
  const load = () => { const pg = pager(fe, (cur) => GET(`/api/feed?${qs({ tab, cursor: cur, topic: t.slug })}`), (c) => feedCard(c, "topic"), { emptyHtml: empty("hash", "No documents yet", "Tag an upload with this topic to start it off.") }); reelify(fe, pg); };
  load();
  $$("[data-tab]", V).forEach((b) => b.onclick = () => { tab = b.dataset.tab; $$("[data-tab]", V).forEach((x) => x.classList.toggle("on", x === b)); load(); });
  GET(`/api/top?kind=creators&window=all&limit=5&topic=${t.slug}`).then((r) => $("[data-creators]", V).innerHTML = r.items.map((x, i) => rankRow(x, "creators", i)).join("") || `<p class="muted" style="margin:4px 0">—</p>`);
};

/* ---------------- top lists ---------------- */
PAGES.top = async (d) => {
  let kind = params.get("kind") || "trending", win = params.get("window") || "week", topic = params.get("topic") || "";
  const kinds = [["trending", "Trending", "flame"], ["downloads", "Most downloaded", "download"], ["creators", "Top creators", "trophy"], ["orgs", "Top organizations", "building"], ["topics", "Topics", "hash"]];
  const wins = [["week", "This week"], ["month", "This month"], ["year", "This year"], ["all", "All time"]];
  const topics = (await GET("/api/topics?limit=30")).items;
  V.innerHTML = `<div class="page-narrow"><div class="page-head"><div><h1>Top lists</h1><p class="muted">What people are reading, downloading and following.</p></div></div>
    <div class="chips" data-kinds style="margin-bottom:12px">${kinds.map(([k, l, i]) => `<button class="chip ${k === kind ? "on" : ""}" data-k="${k}">${ic(i, "sm")}${l}</button>`).join("")}</div>
    <div class="row wrap" style="gap:10px;margin-bottom:16px"><div class="seg" data-wins>${wins.map(([k, l]) => `<button data-w="${k}" class="${k === win ? "on" : ""}">${l}</button>`).join("")}</div>
      <select class="select sm" data-topic style="width:auto;min-width:160px"><option value="">All topics</option>${topics.map((t) => `<option value="${esc(t.slug)}" ${t.slug === topic ? "selected" : ""}>#${esc(t.name)}</option>`).join("")}</select></div>
    <div class="panel"><div class="panel-b" data-list></div></div></div>`;
  const load = async () => {
    $("[data-wins]", V).style.visibility = kind === "trending" || kind === "topics" ? "hidden" : "";
    $("[data-topic]", V).style.display = kind === "topics" ? "none" : "";
    history.replaceState(null, "", "/top?" + qs({ kind, window: win, topic }));
    const el = $("[data-list]", V); el.innerHTML = skeletonRows(8);
    try {
      const r = await GET(`/api/top?${qs({ kind, window: win, topic, limit: 50 })}`);
      el.innerHTML = r.items.map((x, i) => rankRow(x, kind, i)).join("") || empty("trophy", "Nothing ranked yet", "As people view, like and download documents, rankings appear here.");
    } catch (e) { el.innerHTML = empty("x", "Couldn't load", esc(e.message)); }
  };
  $("[data-kinds]", V).onclick = (e) => { const b = e.target.closest("[data-k]"); if (!b) return; kind = b.dataset.k; $$("[data-k]", V).forEach((x) => x.classList.toggle("on", x === b)); load(); };
  $("[data-wins]", V).onclick = (e) => { const b = e.target.closest("[data-w]"); if (!b) return; win = b.dataset.w; $$("[data-w]", V).forEach((x) => x.classList.toggle("on", x === b)); load(); };
  $("[data-topic]", V).onchange = (e) => { topic = e.target.value; load(); };
  load();
};
PAGES.explore = PAGES.top;

/* ---------------- profile ---------------- */
PAGES.profile = async (u) => {
  let tab = "files";
  V.innerHTML = `<div class="page-wide">
    <div class="hero"><div class="cover" style="background:linear-gradient(120deg,${esc(u.color || "#5C5DE2")},color-mix(in srgb,${esc(u.color || "#5C5DE2")} 40%,var(--disc)))"></div>
      <div class="hero-b"><div class="hero-av">${avatar(u, "xl")}</div>
        <div class="row between wrap" style="gap:12px;align-items:flex-end"><div><h1>${esc(u.name)}</h1><div class="muted">@${esc(u.username)} · Joined ${esc(u.joined || "")}</div></div>
          <div class="row">${u.me ? `<a class="btn" href="/settings">${ic("edit", "sm")}Edit profile</a><a class="btn" href="/analytics">${ic("chart", "sm")}Analytics</a>` : followBtn("user", u.username, u.following)}
          <button class="btn" data-pshare>${ic("share", "sm")}</button></div></div>
        ${u.bio ? `<p class="about">${esc(u.bio)}</p>` : ""}
        <div class="stats"><span><b>${num(u.files)}</b> files</span><span><b data-followers>${num(u.followers)}</b> followers</span><span><b>${num(u.following_count)}</b> following</span><span><b>${num(u.downloads)}</b> downloads</span><span><b>${num(u.likes)}</b> likes</span></div>
        ${u.orgs.length ? `<div class="row wrap" style="gap:8px;margin-top:12px">${u.orgs.map((o) => `<a class="chip" href="/o/${esc(o.slug)}" style="height:34px">${avatar(o, "xs", true)}${esc(o.name)}${verified(o.verified)}<span class="muted" style="font-weight:500">${esc(o.role)}</span></a>`).join("")}</div>` : ""}
      </div></div>
    <div class="tabs"><button data-tab="files" class="on">Files</button><button data-tab="liked">Liked</button><button data-tab="collections">Collections</button></div>
    <div data-list></div></div>`;
  $("[data-pshare]", V).onclick = async () => { const url = location.origin + "/u/" + u.username; if (navigator.share && isMobile()) navigator.share({ title: u.name, url }).catch(() => {}); else { await navigator.clipboard.writeText(url); toast("Profile link copied"); } };
  const el = $("[data-list]", V);
  const load = () => {
    if (tab === "collections") return collectionsGrid(el, `/api/collections?user_=${encodeURIComponent(u.username)}`, u.me);
    pager(el, (cur) => GET(`/api/users/${u.username}/files?${qs({ tab, cursor: cur })}`).then((r) => r), (c) => tile(c, { src: "profile" }), {
      skeleton: skeletonTiles(8), emptyHtml: empty(tab === "liked" ? "heart" : "file", tab === "liked" ? "No likes yet" : "No public files yet", u.me && tab === "files" ? "Upload your first document to start building your profile." : "", u.me && tab === "files" ? `<a class="btn primary" href="/upload">${ic("upload", "sm")}Upload</a>` : ""),
    });
    wrapGrid(el);
  };
  $$("[data-tab]", V).forEach((b) => b.onclick = () => { tab = b.dataset.tab; $$("[data-tab]", V).forEach((x) => x.classList.toggle("on", x === b)); load(); });
  load();
};
function wrapGrid(el) { const l = el.firstElementChild; if (l) l.classList.add("grid"); }

async function collectionsGrid(el, url, canCreate, orgSlug) {
  el.innerHTML = skeletonTiles(4);
  const d = await GET(url);
  const card = (cl) => `<a class="tile coll" href="/c/${cl.id}"><div class="t-img coll-covers">${[0, 1, 2].map((i) => cl.covers[i] ? `<img src="${cl.covers[i]}" loading="lazy">` : `<span></span>`).join("")}</div><div class="t-b"><b class="t-t clamp-1">${esc(cl.title)}</b><div class="t-m"><span>${plural(cl.count, "item")}</span><span class="grow"></span>${cl.visibility !== "public" ? ic(VIS[cl.visibility][0], "sm") : ""}<span>${esc(cl.updated)}</span></div></div></a>`;
  const add = canCreate ? `<button class="tile coll new" data-newcoll><div class="t-img" style="display:grid;place-items:center;color:var(--files)">${ic("plus", "lg")}</div><div class="t-b"><b class="t-t">New collection</b><div class="t-m">Group documents together</div></div></button>` : "";
  el.innerHTML = d.items.length || add ? `<div class="grid">${add}${d.items.map(card).join("")}</div>` : empty("layers", "No collections", "Collections group related documents into a reading list.");
  const nb = $("[data-newcoll]", el);
  if (nb) nb.onclick = () => {
    const s = sheet({ title: "New collection", body: `<div class="field"><label>Title</label><input class="input" data-t maxlength="120"></div><div class="field"><label>Description</label><textarea class="textarea" data-d maxlength="1000"></textarea></div><div class="field"><label>Visibility</label><select class="select" data-v>${visOptions("public", !!orgSlug)}</select></div>`,
      foot: `<button class="btn" data-x2>Cancel</button><button class="btn primary" data-go>Create</button>` });
    $("[data-x2]", s.el).onclick = s.close;
    $("[data-go]", s.el).onclick = async () => { try { const r = await POST("/api/collections", { title: $("[data-t]", s.el).value, description: $("[data-d]", s.el).value, visibility: $("[data-v]", s.el).value, org: orgSlug || null }); location.href = "/c/" + r.id; } catch (e) { fail(e); } };
  };
}

/* ---------------- collection ---------------- */
PAGES.collection = async (cl) => {
  V.innerHTML = `<div class="page-wide"><div class="page-head"><div><div class="muted" style="font-size:13px;font-weight:600">${ic("layers", "sm")} Collection${cl.org ? ` · <a href="/o/${esc(cl.org.slug)}">${esc(cl.org.name)}</a>` : ""}</div><h1>${esc(cl.title)}</h1>
    <p class="muted">${cl.description ? esc(cl.description) + " · " : ""}${plural(cl.count, "item")} · by <a href="/u/${esc(cl.owner.username)}">${esc(cl.owner.name)}</a> · updated ${esc(cl.updated)}</p></div>
    <div class="row">${cl.can_edit ? `<button class="btn" data-cedit>${ic("edit", "sm")}Edit</button><button class="btn danger" data-cdel>${ic("trash", "sm")}</button>` : ""}<button class="btn" data-cshare>${ic("share", "sm")}Share</button></div></div>
    <div data-list>${cl.items.length ? `<div class="grid">${cl.items.map((c) => tile(c, { src: "collection", extra: cl.can_edit ? `<button class="btn sm ghost" data-rm="${c.id}" style="margin:-4px 0 4px -8px">${ic("x", "sm")}Remove</button>` : "" })).join("")}</div>` : empty("layers", "This collection is empty", cl.can_edit ? "Use the ••• menu on any document and choose “Add to collection”." : "")}</div></div>`;
  $("[data-cshare]", V).onclick = async () => { await navigator.clipboard.writeText(location.href); toast("Link copied"); };
  V.onclick = async (e) => {
    const b = e.target.closest("[data-rm]"); if (!b) return;
    e.preventDefault();
    try { await POST(`/api/collections/${cl.id}/items`, { file_ids: [+b.dataset.rm], remove: true }); b.closest(".tile").remove(); toast("Removed"); } catch (err) { fail(err); }
  };
  if (cl.can_edit) {
    $("[data-cdel]", V).onclick = async () => { if (await confirmSheet("Delete this collection? The documents themselves are not deleted.", { ok: "Delete", danger: true })) { await api("DELETE", `/api/collections/${cl.id}`); location.href = "/library"; } };
    $("[data-cedit]", V).onclick = () => {
      const s = sheet({ title: "Edit collection", body: `<div class="field"><label>Title</label><input class="input" data-t value="${esc(cl.title)}"></div><div class="field"><label>Description</label><textarea class="textarea" data-d>${esc(cl.description)}</textarea></div><div class="field"><label>Visibility</label><select class="select" data-v>${visOptions(cl.visibility, !!cl.org)}</select></div>`,
        foot: `<button class="btn" data-x2>Cancel</button><button class="btn primary" data-go>Save</button>` });
      $("[data-x2]", s.el).onclick = s.close;
      $("[data-go]", s.el).onclick = async () => { try { await api("PATCH", `/api/collections/${cl.id}`, { title: $("[data-t]", s.el).value, description: $("[data-d]", s.el).value, visibility: $("[data-v]", s.el).value }); location.reload(); } catch (e) { fail(e); } };
    };
  }
};

/* ---------------- organization ---------------- */
const ROLE_TIP = { owner: "Full control, can delete the org", admin: "Manage members, settings and curation", editor: "Post as the org and curate content", member: "Post files visible to the org" };
PAGES.org = async (o) => {
  const role = o.my_role, manage = role === "owner" || role === "admin", curate = manage || role === "editor";
  let tab = "all";
  const tabs = [["all", "Library"], ["featured", "Featured"], ["top", "Top"], ["members", "Members"], ["collections", "Collections"]];
  if (curate) tabs.push(["curate", "Curate"]);
  if (manage) tabs.push(["settings", "Settings"]);
  const accent = o.accent || "#20A653";
  V.innerHTML = `<div class="page-wide">
    <div class="hero org-hero" style="--accent:${esc(accent)}"><div class="cover" style="${o.cover ? `background-image:url('${o.cover}')` : `background:linear-gradient(120deg,${esc(accent)},color-mix(in srgb,${esc(accent)} 35%,var(--files)))`}">${manage ? `<label class="btn sm cover-edit">${ic("camera", "sm")}<span>Cover</span><input type="file" accept="image/*" hidden data-up="cover"></label>` : ""}</div>
      <div class="hero-b"><div class="hero-av">${avatar(o, "xl", true)}${manage ? `<label class="av-edit" data-tip="Change logo">${ic("camera", "sm")}<input type="file" accept="image/*" hidden data-up="avatar"></label>` : ""}</div>
        <div class="row between wrap" style="gap:12px;align-items:flex-end"><div><h1>${esc(o.name)}${verified(o.verified)}</h1><div class="muted">@${esc(o.slug)}${o.location ? " · " + esc(o.location) : ""}${o.website ? ` · <a href="${esc(/^https?:/.test(o.website) ? o.website : "https://" + o.website)}" target="_blank" rel="noopener nofollow" style="color:var(--disc-ink)">${esc(o.website.replace(/^https?:\/\//, ""))}</a>` : ""}</div></div>
          <div class="row wrap" style="gap:8px" data-orgacts>${o.invited ? `<button class="btn green" data-respond="1">${ic("check", "sm")}Accept invite</button><button class="btn" data-respond="0">Decline</button>` : ""}
            ${role ? `<span class="badge orgs" data-tip="${ROLE_TIP[role]}">${ic("shield", "sm")}${role}</span>` : ""}
            ${followBtn("org", o.slug, o.following)}
            ${role ? `<a class="btn primary" href="/upload?org=${esc(o.slug)}">${ic("upload", "sm")}<span>Post</span></a>` : ""}
            ${manage ? `<button class="btn" data-invite>${ic("users", "sm")}<span>Invite</span></button>` : ""}
            ${manage ? `<a class="iconbtn" href="/analytics?scope=org:${esc(o.slug)}" data-tip="Analytics">${ic("chart")}</a>` : ""}</div></div>
        ${o.tagline ? `<p class="about" style="font-weight:600">${esc(o.tagline)}</p>` : ""}${o.about ? `<p class="about muted">${esc(o.about)}</p>` : ""}
        <div class="stats"><span><b>${num(o.stats.files)}</b> documents</span><span><b>${num(o.stats.members)}</b> members</span><span><b data-followers>${num(o.stats.followers)}</b> followers</span><span><b>${num(o.stats.downloads)}</b> downloads</span></div></div></div>
    <div class="tabs scroll">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join("")}</div>
    <div data-body></div></div>`;
  const body = $("[data-body]", V);
  $$("[data-up]", V).forEach((inp) => inp.onchange = async () => {
    const f = inp.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try { await api("POST", `/api/orgs/${o.slug}/${inp.dataset.up}`, fd); toast("Updated"); setTimeout(() => location.reload(), 400); } catch (e) { fail(e); }
  });
  $$("[data-respond]", V).forEach((b) => b.onclick = async () => { try { await POST(`/api/orgs/${o.slug}/respond`, { accept: b.dataset.respond === "1" }); toast(b.dataset.respond === "1" ? `Welcome to ${o.name}` : "Invite declined"); setTimeout(() => location.reload(), 500); } catch (e) { fail(e); } });
  if ($("[data-invite]", V)) $("[data-invite]", V).onclick = () => inviteSheet(o);
  const load = async () => {
    if (["all", "featured", "top"].includes(tab)) {
      pager(body, (cur) => GET(`/api/orgs/${o.slug}/files?${qs({ tab, cursor: cur })}`), (c) => tile(c, { src: "org" }), { skeleton: skeletonTiles(8),
        emptyHtml: empty(tab === "featured" ? "star" : "file", tab === "featured" ? "Nothing featured yet" : "No documents yet", tab === "featured" ? "Editors can feature members' public documents — with the uploader's permission." : (role ? "Post the first document for your organization." : ""), role && tab !== "featured" ? `<a class="btn green" href="/upload?org=${esc(o.slug)}">${ic("upload", "sm")}Post a document</a>` : "") });
      wrapGrid(body);
    } else if (tab === "members") membersTab(o, body);
    else if (tab === "collections") collectionsGrid(body, `/api/collections?org=${o.slug}`, curate, o.slug);
    else if (tab === "curate") curateTab(o, body);
    else if (tab === "settings") orgSettings(o, body);
  };
  $$("[data-tab]", V).forEach((b) => b.onclick = () => { tab = b.dataset.tab; $$("[data-tab]", V).forEach((x) => x.classList.toggle("on", x === b)); load(); });
  load();
};
function inviteSheet(o) {
  const s = sheet({ title: `Invite to ${esc(o.name)}`, body: `<div class="field"><label>Usernames or emails</label><textarea class="textarea" data-who rows="3" placeholder="@ada, grace@example.com"></textarea><span class="hint">People on Teams get a notification. Emails not yet on Teams are held until they sign up.</span></div>
    <div class="row gap-12"><div class="field grow"><label>Role</label><select class="select" data-role>${["member", "editor", "admin"].filter((r) => r !== "admin" || o.my_role === "owner").map((r) => `<option value="${r}">${r[0].toUpperCase() + r.slice(1)} — ${ROLE_TIP[r]}</option>`).join("")}</select></div></div>
    <div class="field"><label>Title (optional)</label><input class="input" data-title placeholder="e.g. Research lead" maxlength="80"></div><div data-res></div>`,
    foot: `<button class="btn" data-x2>Close</button><button class="btn green" data-go>${ic("send", "sm")}Send invites</button>` });
  $("[data-x2]", s.el).onclick = s.close;
  $("[data-go]", s.el).onclick = async () => {
    try {
      const r = await POST(`/api/orgs/${o.slug}/invite`, { who: $("[data-who]", s.el).value, role: $("[data-role]", s.el).value, title: $("[data-title]", s.el).value });
      const lbl = { invited: ["green", "Invited"], emailed_pending: ["stats", "Will join on sign-up"], not_found: ["rose", "No such user"], already_active: ["", "Already a member"], already_invited: ["", "Already invited"] };
      $("[data-res]", s.el).innerHTML = r.results.map((x) => `<div class="list-row"><b class="grow">${esc(x.who)}</b><span class="badge ${(lbl[x.status] || [""])[0]}">${(lbl[x.status] || ["", x.status])[1]}</span></div>`).join("");
      $("[data-who]", s.el).value = "";
    } catch (e) { fail(e); }
  };
}
async function membersTab(o, el) {
  el.innerHTML = skeletonRows(5);
  const d = await GET(`/api/orgs/${o.slug}/members`);
  const me = ME && ME.username;
  el.innerHTML = `<div class="panel"><div class="panel-b">${d.items.map((m) => `<div class="list-row" data-m="${esc(m.username)}"><a href="/u/${esc(m.username)}">${avatar(m, "md")}</a><div class="grow"><a href="/u/${esc(m.username)}"><b>${esc(m.name)}</b></a>${m.status === "invited" ? ` <span class="badge stats">Invited</span>` : ""}
      <div class="muted" style="font-size:13px">@${esc(m.username)}${m.title ? " · " + esc(m.title) : ""}${m.email ? " · " + esc(m.email) : ""} · ${plural(m.files, "file")}</div></div>
      ${d.can_manage && m.role !== "owner" && m.username !== me ? `<select class="select sm" data-role style="width:auto">${["member", "editor", "admin"].map((r) => `<option ${r === m.role ? "selected" : ""}>${r}</option>`).join("")}</select><button class="iconbtn" data-rm data-tip="Remove">${ic("x")}</button>`
        : `<span class="badge ${m.role === "owner" ? "files" : m.role === "admin" ? "orgs" : ""}" data-tip="${ROLE_TIP[m.role]}">${esc(m.role)}</span>${m.username === me && m.role !== "owner" ? `<button class="btn sm ghost" data-leave>Leave</button>` : ""}`}</div>`).join("")}
    ${d.email_invites.map((x) => `<div class="list-row">${avatar({ initials: "@", color: "#94a3b8" }, "md")}<div class="grow"><b>${esc(x.email)}</b><div class="muted" style="font-size:13px">Pending sign-up · ${esc(x.role)}</div></div><button class="iconbtn" data-cancel-email="${esc(x.email)}" data-tip="Cancel invite">${ic("x")}</button></div>`).join("")}</div></div>`;
  el.onchange = async (e) => { const s = e.target.closest("[data-role]"); if (!s) return; try { await api("PATCH", `/api/orgs/${o.slug}/members/${s.closest("[data-m]").dataset.m}`, { role: s.value }); toast("Role updated"); } catch (err) { fail(err); } };
  el.onclick = async (e) => {
    let b;
    if ((b = e.target.closest("[data-rm],[data-leave]"))) {
      const u = b.closest("[data-m]").dataset.m; const leaving = b.hasAttribute("data-leave");
      if (!(await confirmSheet(leaving ? `Leave ${esc(o.name)}?` : `Remove @${esc(u)} from ${esc(o.name)}?`, { ok: leaving ? "Leave" : "Remove", danger: true }))) return;
      try { await api("DELETE", `/api/orgs/${o.slug}/members/${u}`); if (leaving) location.reload(); else b.closest("[data-m]").remove(); } catch (err) { fail(err); }
    }
    if ((b = e.target.closest("[data-cancel-email]"))) { await api("DELETE", `/api/orgs/${o.slug}/email-invites/${encodeURIComponent(b.dataset.cancelEmail)}`); b.closest(".list-row").remove(); }
  };
}
async function curateTab(o, el) {
  el.innerHTML = skeletonRows(4);
  const [req, cand] = await Promise.all([GET(`/api/orgs/${o.slug}/requests`), GET(`/api/orgs/${o.slug}/candidates`)]);
  el.innerHTML = `<div class="notice info" style="margin-bottom:16px">${ic("shield")}<span><b>Curation by consent.</b> When you feature a member's document, its uploader gets a request and decides whether it appears in ${esc(o.name)}'s library.</span></div>
    <div class="section-title">Requests sent</div>
    <div class="panel" style="margin-bottom:20px"><div class="panel-b">${req.items.map((c) => `<div class="list-row"><span class="mini-thumb">${c.thumb ? `<img src="${c.thumb}">` : ""}</span><div class="grow"><a href="/f/${c.id}"><b class="clamp-1">${esc(c.title)}</b></a><div class="muted" style="font-size:13px">by ${esc(c.author.name)}</div></div><span class="badge ${c.feature_status === "pending" ? "stats" : "rose"}">${c.feature_status === "pending" ? "Awaiting approval" : "Declined"}</span></div>`).join("") || `<p class="muted" style="margin:6px 0">No open requests.</p>`}</div></div>
    <div class="section-title">Members' documents you can feature</div>
    ${cand.items.length ? `<div class="grid">${cand.items.map((c) => tile(c, { src: "org", extra: `<button class="btn sm green" data-feature="${c.id}" style="margin:2px 0 6px">${ic("star", "sm")}${c.mine ? "Feature" : "Request feature"}</button>` })).join("")}</div>` : empty("star", "Nothing to feature", "When members share public documents, they show up here.")}`;
  el.onclick = async (e) => {
    const b = e.target.closest("[data-feature]"); if (!b) return; e.preventDefault();
    try { const r = await POST(`/api/orgs/${o.slug}/feature`, { file_id: +b.dataset.feature }); b.outerHTML = `<span class="badge ${r.status === "approved" ? "green" : "stats"}" style="margin:2px 0 6px">${r.status === "approved" ? "Featured" : "Request sent"}</span>`; } catch (err) { fail(err); }
  };
}
function orgSettings(o, el) {
  el.innerHTML = `<div class="panel" style="max-width:680px"><div class="panel-b" style="padding-top:18px">
    <div class="field"><label>Name</label><input class="input" data-f="name" value="${esc(o.name)}"></div>
    <div class="field"><label>Tagline</label><input class="input" data-f="tagline" value="${esc(o.tagline)}" maxlength="140"></div>
    <div class="field"><label>About</label><textarea class="textarea" data-f="about" rows="4">${esc(o.about)}</textarea></div>
    <div class="row gap-12 wrap"><div class="field grow"><label>Website</label><input class="input" data-f="website" value="${esc(o.website)}"></div><div class="field grow"><label>Location</label><input class="input" data-f="location" value="${esc(o.location)}"></div></div>
    <div class="field"><label>Accent colour</label><div class="row"><input type="color" data-f="accent" value="${esc(o.accent || "#20A653")}" style="width:48px;height:36px;border:0;background:none"><span class="hint">Used on your organization's page.</span></div></div>
    <div class="row" style="justify-content:flex-end"><button class="btn green" data-save-org>Save changes</button></div></div></div>
    ${o.my_role === "owner" ? `<div class="panel danger-zone" style="max-width:680px;margin-top:16px"><div class="panel-b" style="padding-top:16px"><b>Delete organization</b><p class="muted" style="margin:4px 0 12px">Org-only documents become private to their uploaders. This can't be undone.</p><button class="btn danger" data-del-org>${ic("trash", "sm")}Delete ${esc(o.name)}</button></div></div>` : ""}`;
  $("[data-save-org]", el).onclick = async () => { const b = {}; $$("[data-f]", el).forEach((x) => b[x.dataset.f] = x.value); try { await api("PATCH", `/api/orgs/${o.slug}`, b); toast("Saved"); setTimeout(() => location.reload(), 400); } catch (e) { fail(e); } };
  const d = $("[data-del-org]", el);
  if (d) d.onclick = async () => { if (await confirmSheet(`Delete ${esc(o.name)} permanently?`, { ok: "Delete", danger: true })) { await api("DELETE", `/api/orgs/${o.slug}`); location.href = "/orgs"; } };
}

/* ---------------- organizations index ---------------- */
PAGES.orgs = async () => {
  V.innerHTML = `<div class="page-wide"><div class="page-head"><div><h1>Organizations</h1><p class="muted">Teams, departments and communities that publish together.</p></div>${ME ? `<button class="btn green" data-new-org>${ic("plus", "sm")}New organization</button>` : ""}</div>
    <div data-mine></div><div class="section-title">Top organizations</div><div data-top class="org-grid">${skeletonTiles(4)}</div></div>`;
  const orgCard = (x, extra = "") => `<a class="org-card" href="/o/${esc(x.slug)}" style="--accent:${esc(x.accent || "#20A653")}"><div class="oc-cover" style="${x.cover ? `background-image:url('${x.cover}')` : ""}"></div><div class="oc-b">${avatar(x, "lg", true)}<b class="clamp-1">${esc(x.name)}${verified(x.verified)}</b><span class="muted clamp-2" style="font-size:13px">${esc(x.tagline || "@" + x.slug)}</span>${extra}</div></a>`;
  if (ME) GET("/api/me/orgs").then((r) => { if (r.items.length) $("[data-mine]", V).innerHTML = `<div class="section-title">Yours</div><div class="org-grid" style="margin-bottom:24px">${r.items.map((x) => orgCard(x, `<span class="badge ${x.status === "invited" ? "stats" : "orgs"}">${x.status === "invited" ? "Invitation" : esc(x.role)}</span>`)).join("")}</div>`; });
  GET("/api/top?kind=orgs&window=all&limit=40").then((r) => $("[data-top]", V).innerHTML = r.items.map((x) => orgCard(x, `<span class="muted" style="font-size:12.5px">${plural(x.members || 0, "member")} · ${plural(x.files || 0, "doc")}</span>`)).join("") || empty("building", "No organizations yet", "Create one for your team, department or community.")).then(() => { if (!$("[data-top] .org-card", V)) $("[data-top]", V).classList.remove("org-grid"); });
  const nb = $("[data-new-org]", V);
  if (nb) nb.onclick = () => {
    const s = sheet({ title: "New organization", body: `<p class="muted" style="margin-top:0">An organization is a shared space you own. You can invite people and curate a library together.</p>
      <div class="field"><label>Name</label><input class="input" data-n maxlength="80" placeholder="e.g. Lagos Health Network"></div>
      <div class="field"><label>Handle</label><div class="input-prefix"><span>/o/</span><input class="input" data-s maxlength="40" placeholder="lagos-health"></div></div>
      <div class="field"><label>Tagline</label><input class="input" data-t maxlength="140"></div>`,
      foot: `<button class="btn" data-x2>Cancel</button><button class="btn green" data-go>Create</button>` });
    const n = $("[data-n]", s.el), sl = $("[data-s]", s.el); let touched = false;
    n.oninput = () => { if (!touched) sl.value = n.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40); };
    sl.oninput = () => touched = true;
    $("[data-x2]", s.el).onclick = s.close;
    $("[data-go]", s.el).onclick = async () => { try { const r = await POST("/api/orgs", { name: n.value, slug: sl.value, tagline: $("[data-t]", s.el).value }); location.href = "/o/" + r.org.slug; } catch (e) { fail(e); } };
  };
};

/* ---------------- library + bulk edit ---------------- */
PAGES.library = PAGES.dashboard = async () => {
  if (!need()) return;
  let tab = params.get("tab") || "files", items = [], filter = "", vis = "", sort = "new", selecting = false, last = null;
  const picked = new Set();
  V.innerHTML = `<div class="page-wide"><div class="page-head"><div><h1>Library</h1><p class="muted">Everything you've uploaded, saved and been shared.</p></div>
      <div class="row"><a class="btn" href="/analytics">${ic("chart", "sm")}<span>Analytics</span></a><a class="btn primary" href="/upload">${ic("upload", "sm")}Upload</a></div></div>
    <div class="tabs scroll">${[["files", "My files"], ["shared", "Shared with me"], ["saved", "Saved"], ["liked", "Liked"], ["collections", "Collections"]].map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join("")}</div>
    <div class="toolbar" data-tools><div class="searchbox sm">${ic("search", "sm")}<input data-filter placeholder="Filter by title or tag"></div>
      <select class="select sm" data-vis style="width:auto"><option value="">Any visibility</option>${Object.entries(VIS).map(([k, [, l]]) => `<option value="${k}">${l}</option>`).join("")}</select>
      <select class="select sm" data-sort style="width:auto"><option value="new">Newest</option><option value="old">Oldest</option><option value="views">Most viewed</option><option value="downloads">Most downloaded</option><option value="likes">Most liked</option><option value="title">Title A–Z</option></select>
      <span class="grow"></span><button class="btn sm" data-select>${ic("check-square", "sm")}<span>Select</span></button></div>
    <div data-grid></div></div>
    <div class="bulkbar hide" data-bulk><button class="iconbtn" data-bx data-tip="Clear selection">${ic("x")}</button><b data-bn>0 selected</b><button class="btn sm ghost" data-ball>All</button><span class="grow"></span>
      <div class="bulk-acts"><button class="btn sm" data-b="visibility">${ic("globe", "sm")}<span>Visibility</span></button><button class="btn sm" data-b="tags">${ic("tag", "sm")}<span>Tags</span></button><button class="btn sm" data-b="org">${ic("building", "sm")}<span>Org</span></button>
      <button class="btn sm" data-b="collab">${ic("users", "sm")}<span>People</span></button><button class="btn sm" data-b="coll">${ic("layers", "sm")}<span>Collection</span></button><button class="btn sm" data-b="zip">${ic("zip", "sm")}<span>Zip</span></button><button class="btn sm" data-b="more">${ic("more", "sm")}</button><button class="btn sm danger" data-b="delete">${ic("trash", "sm")}</button></div></div>`;
  const grid = $("[data-grid]", V), bar = $("[data-bulk]", V);
  const canBulk = () => tab === "files";
  const view = () => {
    let list = items.filter((c) => (!vis || c.visibility === vis) && (!filter || (c.title + " " + c.tags.join(" ")).toLowerCase().includes(filter)));
    const by = { new: (a, b) => b.id - a.id, old: (a, b) => a.id - b.id, views: (a, b) => b.counts.view - a.counts.view, downloads: (a, b) => b.counts.download - a.counts.download, likes: (a, b) => b.counts.like - a.counts.like, title: (a, b) => a.title.localeCompare(b.title) };
    list.sort(by[sort]);
    grid.innerHTML = list.length ? `<div class="grid ${selecting ? "selecting" : ""}">${list.map((c) => tile(c, { select: canBulk(), src: "library", extra: tab === "files" ? `<div class="row gap-4 muted" style="font-size:12px">${ic("eye", "sm")}${num(c.counts.view)} views${c.org ? ` · ${esc(c.org.name)}` : ""}</div>` : "" })).join("")}</div>`
      : items.length ? empty("filter", "No matches", "Try a different filter.") : empty({ files: "upload", shared: "users", saved: "bookmark", liked: "heart" }[tab], { files: "Your library is empty", shared: "Nothing shared with you yet", saved: "No saved documents", liked: "No liked documents" }[tab],
        { files: "Upload documents, or drop a whole folder at once.", shared: "When someone adds you as a collaborator, it shows up here.", saved: "Tap the bookmark on any document to keep it here.", liked: "Documents you like show up here." }[tab], tab === "files" ? `<a class="btn primary" href="/upload">${ic("upload", "sm")}Upload files</a>` : "");
    $$("[data-pick]", grid).forEach((cb) => { cb.checked = picked.has(+cb.dataset.pick); cb.closest(".tile").classList.toggle("picked", cb.checked); });
    return list;
  };
  const sync = () => {
    bar.classList.toggle("hide", !selecting);
    $("[data-bn]", bar).textContent = `${picked.size} selected`;
    $$(".bulk-acts .btn", bar).forEach((b) => b.disabled = !picked.size);
    $$("[data-pick]", grid).forEach((cb) => { cb.checked = picked.has(+cb.dataset.pick); cb.closest(".tile").classList.toggle("picked", cb.checked); });
    const g = $(".grid", grid); g && g.classList.toggle("selecting", selecting);
    $("[data-select]", V).classList.toggle("on", selecting);
    document.body.classList.toggle("has-bulk", selecting);
  };
  const setSel = (on) => { selecting = on; if (!on) picked.clear(); sync(); };
  const load = async () => {
    const tools = $("[data-tools]", V); tools.classList.toggle("hide", tab === "collections");
    $("[data-select]", V).classList.toggle("hide", !canBulk()); setSel(false);
    if (tab === "collections") return collectionsGrid(grid, "/api/collections?mine=true", true);
    grid.innerHTML = skeletonTiles(8);
    try { items = (await GET(`/api/me/library?tab=${tab}`)).items; items.forEach(keep); view(); } catch (e) { grid.innerHTML = empty("x", "Couldn't load", esc(e.message)); }
  };
  $$("[data-tab]", V).forEach((b) => b.onclick = () => { tab = b.dataset.tab; $$("[data-tab]", V).forEach((x) => x.classList.toggle("on", x === b)); history.replaceState(null, "", `/library${tab === "files" ? "" : "?tab=" + tab}`); load(); });
  $("[data-filter]", V).oninput = debounce((e) => { filter = e.target.value.toLowerCase().trim(); view(); sync(); }, 120);
  $("[data-vis]", V).onchange = (e) => { vis = e.target.value; view(); sync(); };
  $("[data-sort]", V).onchange = (e) => { sort = e.target.value; view(); sync(); };
  $("[data-select]", V).onclick = () => setSel(!selecting);
  $("[data-bx]", bar).onclick = () => setSel(false);
  $("[data-ball]", bar).onclick = () => { const vis_ = $$("[data-pick]", grid).map((x) => +x.dataset.pick); const all = vis_.every((i) => picked.has(i)); vis_.forEach((i) => all ? picked.delete(i) : picked.add(i)); sync(); };
  // selection: checkbox, shift-click range, click-in-select-mode, long-press
  const order = () => $$("[data-pick]", grid).map((x) => +x.dataset.pick);
  const pick = (id, shift) => {
    if (shift && last != null) { const o = order(); const [a, b] = [o.indexOf(last), o.indexOf(id)].sort((x, y) => x - y); const on = !picked.has(id); o.slice(a, b + 1).forEach((i) => on ? picked.add(i) : picked.delete(i)); }
    else picked.has(id) ? picked.delete(id) : picked.add(id);
    last = id; if (!selecting) selecting = true; sync();
  };
  grid.addEventListener("click", (e) => {
    const cb = e.target.closest("[data-pick]");
    if (cb) { e.preventDefault(); pick(+cb.dataset.pick, e.shiftKey); return; }
    const t = e.target.closest("[data-tile]");
    if (t && selecting && canBulk()) { e.preventDefault(); pick(+t.dataset.tile, e.shiftKey); }
  }, true);
  let lp = null, lpFired = false;
  grid.addEventListener("touchstart", (e) => { const t = e.target.closest("[data-tile]"); if (!t || !canBulk()) return; lpFired = false; lp = setTimeout(() => { lpFired = true; navigator.vibrate && navigator.vibrate(15); pick(+t.dataset.tile); }, 480); }, { passive: true });
  ["touchend", "touchmove", "touchcancel"].forEach((ev) => grid.addEventListener(ev, () => clearTimeout(lp), { passive: true }));
  grid.addEventListener("click", (e) => { if (lpFired) { e.preventDefault(); e.stopPropagation(); lpFired = false; } }, true);
  grid.addEventListener("contextmenu", (e) => { if (e.target.closest("[data-tile]") && matchMedia("(hover: none)").matches) e.preventDefault(); });
  document.addEventListener("keydown", (e) => {
    if (BOOT.page !== "library" && BOOT.page !== "dashboard") return;
    if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
    if (e.key === "Escape" && selecting) setSel(false);
    if ((e.metaKey || e.ctrlKey) && e.key === "a" && canBulk()) { e.preventDefault(); order().forEach((i) => picked.add(i)); selecting = true; sync(); }
  });

  const run = async (action, value, msg) => {
    try { const r = await POST("/api/bulk", { ids: [...picked], action, value }); toast(`${msg} · ${plural(r.affected, "file")}${r.skipped ? ` (${r.skipped} skipped)` : ""}`); await load(); }
    catch (e) { fail(e); }
  };
  const ids = () => [...picked];
  bar.addEventListener("click", (e) => {
    const b = e.target.closest("[data-b]"); if (!b || b.disabled) return;
    const n = picked.size, k = b.dataset.b;
    const form = (title, body, go) => { const s = sheet({ title: `${title} · ${plural(n, "file")}`, body, foot: `<button class="btn" data-x2>Cancel</button><button class="btn primary" data-go>Apply</button>` }); $("[data-x2]", s.el).onclick = s.close; $("[data-go]", s.el).onclick = () => { s.close(); go(s.el); }; return s; };
    if (k === "visibility") form("Change visibility", `<div class="col gap-4">${Object.entries(VIS).map(([v, [i, l]], j) => `<label class="list-row" style="cursor:pointer"><input type="radio" name="v" value="${v}" ${j ? "" : "checked"}>${ic(i)}<div><b>${l}</b><div class="muted" style="font-size:12.5px">${{ public: "Anyone can find and view", org: "Only members of the file's organization", private: "Only you and collaborators", link: "Anyone with the secret link; not listed" }[v]}</div></div></label>`).join("")}</div>`, (el) => run("visibility", $("input[name=v]:checked", el).value, "Visibility updated"));
    if (k === "tags") form("Edit tags", `<div class="seg" data-mode style="margin-bottom:12px"><button class="on" data-m="tags_add">Add</button><button data-m="tags_remove">Remove</button><button data-m="tags_set">Replace all</button></div><input class="input" data-t placeholder="safety, onboarding">`, (el) => run($(".on[data-m]", el).dataset.m, $("[data-t]", el).value, "Tags updated")).el.addEventListener("click", (e2) => { const m = e2.target.closest("[data-m]"); if (m) $$("[data-m]", m.parentElement).forEach((x) => x.classList.toggle("on", x === m)); });
    if (k === "org") form("Move to organization", `<select class="select" data-o>${orgOptions("")}</select><p class="hint">Choosing “None” makes org-only files public.</p>`, (el) => run("org", $("[data-o]", el).value, "Organization updated"));
    if (k === "collab") form("Add collaborators", `<input class="input" data-c placeholder="emails or usernames, comma separated"><p class="hint">Collaborators can see private files and edit details.</p>`, (el) => run("collaborators_add", $("[data-c]", el).value, "Collaborators added"));
    if (k === "zip") { location.href = `/api/bulk/zip?ids=${ids().join(",")}`; toast("Preparing your zip…"); }
    if (k === "delete") deleteFiles(ids(), load);
    if (k === "coll") bulkCollection(ids());
    if (k === "more") menu(b, [
      { label: "Turn comments on", icon: "message", on: () => run("allow_comments", true, "Comments on") },
      { label: "Turn comments off", icon: "message", on: () => run("allow_comments", false, "Comments off") },
      { label: "Allow downloads", icon: "download", on: () => run("allow_download", true, "Downloads allowed") },
      { label: "Block downloads", icon: "lock", on: () => run("allow_download", false, "Downloads blocked") },
    ]);
  });
  load();
};
async function bulkCollection(ids) {
  const d = await GET("/api/collections?mine=true");
  const s = sheet({ title: `Add ${plural(ids.length, "file")} to collection`, body: `${d.items.map((cl) => `<button class="list-row" data-cl="${cl.id}" style="width:100%;text-align:left"><span class="mini-thumb">${ic("layers")}</span><b class="grow">${esc(cl.title)}</b>${ic("plus")}</button>`).join("")}<div class="row" style="margin-top:12px"><input class="input" data-new placeholder="New collection"><button class="btn primary" data-create>Create</button></div>` });
  s.el.onclick = async (e) => {
    let id = e.target.closest("[data-cl]") && e.target.closest("[data-cl]").dataset.cl;
    if (e.target.closest("[data-create]")) { const t = $("[data-new]", s.el).value.trim(); if (!t) return; id = (await POST("/api/collections", { title: t })).id; }
    if (!id) return;
    try { await POST(`/api/collections/${id}/items`, { file_ids: ids }); toast("Added to collection"); s.close(); } catch (err) { fail(err); }
  };
}

/* ---------------- upload queue ---------------- */
PAGES.upload = async () => {
  if (!need()) return;
  const presetOrg = params.get("org") || "";
  const queue = [];
  V.innerHTML = `<div class="page-narrow"><div class="page-head"><div><h1>Upload</h1><p class="muted">PDF, Word, PowerPoint, Excel, images, text — or paste a link. Drop many files at once.</p></div></div>
    <label class="drop" data-drop><input type="file" multiple hidden data-input><div class="art">${ic("upload")}</div><b>Drop files here or <span style="color:var(--files)">browse</span></b><span class="muted" style="font-size:13px">Up to 50 files at a time · max ${esc(String(BOOT.max_mb || 50))} MB each</span></label>
    <div class="row" style="margin:12px 0"><div class="divider grow"><span>or share a link</span></div></div>
    <div class="row"><input class="input grow" data-url placeholder="https://…"><button class="btn" data-addurl>${ic("link", "sm")}Add link</button></div>
    <div class="panel" style="margin-top:18px"><div class="panel-h">Details for all files <span class="muted" style="font-weight:500;font-size:12.5px">Applied to every file in the queue — edit titles individually below</span></div><div class="panel-b">
      <div class="field"><label>Description</label><textarea class="textarea" data-desc rows="2" placeholder="What are these documents about?"></textarea></div>
      <div class="field"><label>Tags</label><input class="input" data-tags placeholder="e.g. safety, training"><span class="hint">Tags become topics people can follow and browse.</span></div>
      <div class="row gap-12 wrap"><div class="field grow"><label>Who can see them</label><select class="select" data-vis>${visOptions("public")}</select></div>
        <div class="field grow"><label>Post as organization</label><select class="select" data-org>${orgOptions(presetOrg)}</select></div></div>
      <div class="field"><label>Collaborators</label><input class="input" data-collab placeholder="emails or usernames (optional)"></div>
      <div class="row wrap" style="gap:18px"><label class="row"><input type="checkbox" class="sr" data-ac checked><span class="switch"></span>Allow comments</label><label class="row"><input type="checkbox" class="sr" data-ad checked><span class="switch"></span>Allow downloads</label></div>
    </div></div>
    <div data-q style="margin-top:18px"></div>
    <div class="row upload-go hide" data-go-row><span class="muted grow" data-qsum></span><button class="btn" data-clear>Clear</button><button class="btn primary lg" data-go>${ic("upload", "sm")}Upload all</button></div></div>`;
  const qEl = $("[data-q]", V), input = $("[data-input]", V), drop = $("[data-drop]", V);
  const visSel = $("[data-vis]", V), orgSel = $("[data-org]", V);
  const syncVis = () => { const o = visSel.querySelector('option[value="org"]'); o.disabled = !orgSel.value; if (!orgSel.value && visSel.value === "org") visSel.value = "public"; };
  orgSel.onchange = syncVis; syncVis();
  const icFor = (q) => q.url ? "link" : /image/.test(q.file.type) ? "image" : "file";
  const row = (q) => `<div class="q-item" data-qi="${q.id}"><span class="mini-thumb ft-${q.ext}">${q.preview ? `<img src="${q.preview}">` : ic(icFor(q))}</span>
    <div class="grow" style="min-width:0"><input class="q-title" value="${esc(q.title)}" ${q.state !== "ready" ? "disabled" : ""} aria-label="Title"><div class="muted" style="font-size:12px" data-st>${q.url ? esc(q.url) : bytes(q.file.size)}</div><div class="progress"><i style="width:${q.pct}%"></i></div></div>
    ${q.state === "ready" ? `<button class="iconbtn" data-rm data-tip="Remove">${ic("x")}</button>` : q.state === "done" ? `<a class="iconbtn ok" href="/f/${q.fileId}" data-tip="Open">${ic("check")}</a>` : q.state === "error" ? `<button class="iconbtn" data-retry data-tip="Retry">${ic("history")}</button>` : `<span class="spinner sm"></span>`}</div>`;
  const render = () => {
    qEl.innerHTML = queue.map(row).join("");
    const ready = queue.filter((q) => q.state === "ready").length, done = queue.filter((q) => q.state === "done").length;
    $("[data-go-row]", V).classList.toggle("hide", !queue.length);
    $("[data-qsum]", V).textContent = `${plural(queue.length, "item")}${done ? ` · ${done} uploaded` : ""}`;
    $("[data-go]", V).disabled = !ready;
  };
  let seq = 0;
  const add = (files) => {
    for (const f of files) {
      if (queue.length >= 50) { toast("Up to 50 files at a time", "err"); break; }
      const q = { id: ++seq, file: f, title: f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " "), ext: (f.name.split(".").pop() || "").toLowerCase(), state: "ready", pct: 0 };
      if (/^image\//.test(f.type)) q.preview = URL.createObjectURL(f);
      queue.push(q);
    }
    render();
  };
  input.onchange = () => { add(input.files); input.value = ""; };
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => add(e.dataTransfer.files));
  document.addEventListener("paste", (e) => { if (BOOT.page === "upload" && e.clipboardData.files.length) add(e.clipboardData.files); });
  $("[data-addurl]", V).onclick = () => { const u = $("[data-url]", V).value.trim(); if (!u) return; queue.push({ id: ++seq, url: u, title: u.replace(/^https?:\/\//, "").slice(0, 120), ext: "url", state: "ready", pct: 0 }); $("[data-url]", V).value = ""; render(); };
  qEl.addEventListener("input", (e) => { const t = e.target.closest(".q-title"); if (t) queue.find((q) => q.id === +t.closest("[data-qi]").dataset.qi).title = t.value; });
  qEl.addEventListener("click", (e) => {
    const it = e.target.closest("[data-qi]"); if (!it) return; const q = queue.find((x) => x.id === +it.dataset.qi);
    if (e.target.closest("[data-rm]")) { queue.splice(queue.indexOf(q), 1); render(); }
    if (e.target.closest("[data-retry]")) { q.state = "ready"; q.pct = 0; render(); $("[data-go]", V).click(); }
  });
  $("[data-clear]", V).onclick = () => { for (let i = queue.length - 1; i >= 0; i--) if (queue[i].state !== "uploading") queue.splice(i, 1); render(); };
  const send = (q) => new Promise((res) => {
    const fd = new FormData();
    if (q.url) fd.append("url", q.url); else fd.append("file", q.file);
    fd.append("title", q.title); fd.append("description", $("[data-desc]", V).value); fd.append("tags", $("[data-tags]", V).value);
    fd.append("visibility", visSel.value); fd.append("org", orgSel.value); fd.append("collaborators", $("[data-collab]", V).value);
    fd.append("allow_comments", $("[data-ac]", V).checked); fd.append("allow_download", $("[data-ad]", V).checked);
    const x = new XMLHttpRequest(); x.open("POST", "/api/files");
    const el = () => $(`[data-qi="${q.id}"]`, qEl);
    x.upload.onprogress = (e) => { if (!e.lengthComputable) return; q.pct = Math.round(e.loaded / e.total * 95); const r = el(); if (r) { $(".progress i", r).style.width = q.pct + "%"; $("[data-st]", r).textContent = q.pct >= 95 ? "Processing…" : `${q.pct}% · ${bytes(e.loaded)} of ${bytes(e.total)}`; } };
    x.onload = () => {
      let d = {}; try { d = JSON.parse(x.responseText); } catch (e) { /* */ }
      if (x.status < 300) { q.state = "done"; q.pct = 100; q.fileId = d.file.id; }
      else { q.state = "error"; q.err = typeof d.detail === "string" ? d.detail : "Upload failed"; }
      render(); if (q.state === "error") { const r = el(); r && ($("[data-st]", r).innerHTML = `<span style="color:var(--danger)">${esc(q.err)}</span>`); r && r.classList.add("err"); }
      res();
    };
    x.onerror = () => { q.state = "error"; q.err = "Network error"; render(); res(); };
    x.send(fd);
  });
  $("[data-go]", V).onclick = async () => {
    const todo = queue.filter((q) => q.state === "ready");
    todo.forEach((q) => q.state = "uploading"); render();
    let i = 0;
    const worker = async () => { while (i < todo.length) { const q = todo[i++]; await send(q); } };
    await Promise.all([worker(), worker(), worker()]);
    const ok = todo.filter((q) => q.state === "done");
    if (ok.length) toast(`Uploaded ${plural(ok.length, "file")}`);
    if (ok.length === 1 && todo.length === 1) setTimeout(() => location.href = `/f/${ok[0].fileId}`, 700);
    else if (ok.length === todo.length) $("[data-go-row]", V).insertAdjacentHTML("beforeend", `<a class="btn green lg" href="/library">${ic("library", "sm")}View in library</a>`);
  };
};

/* ---------------- analytics ---------------- */
const SERIES = [["view", "Views", "var(--files)"], ["download", "Downloads", "var(--orgs)"], ["share", "Shares", "var(--disc)"], ["like", "Likes", "var(--social)"], ["comment", "Comments", "var(--stats)"]];
function niceMax(v) { if (v <= 4) return 4; const p = Math.pow(10, Math.floor(Math.log10(v))); const n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }
function chartSvg(labels, series, active, { h = 240 } = {}) {
  const W = 1000, H = h, pl = 44, pr = 12, pt = 14, pb = 28;
  const keys = active.length ? active : ["view"];
  const max = niceMax(Math.max(1, ...keys.flatMap((k) => series[k])));
  const n = labels.length, x = (i) => pl + (n === 1 ? (W - pl - pr) / 2 : i * (W - pl - pr) / (n - 1)), y = (v) => pt + (H - pt - pb) * (1 - v / max);
  let g = "";
  for (let i = 0; i <= 4; i++) { const v = max * i / 4, yy = y(v); g += `<line class="grid-l" x1="${pl}" x2="${W - pr}" y1="${yy}" y2="${yy}"/><text class="axis" x="${pl - 8}" y="${yy + 4}" text-anchor="end">${num(Math.round(v))}</text>`; }
  const step = Math.max(1, Math.ceil(n / 7));
  labels.forEach((l, i) => { if (i % step === 0 || i === n - 1) g += `<text class="axis" x="${x(i)}" y="${H - 8}" text-anchor="middle">${new Date(l + "T00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" })}</text>`; });
  keys.forEach((k, j) => {
    const col = SERIES.find((s) => s[0] === k)[2];
    const pts = series[k].map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    if (j === 0) g += `<path class="area" d="M${x(0)},${y(0)} L${pts.join(" L")} L${x(n - 1)},${y(0)} Z" style="--c:${col}"/>`;
    g += `<polyline class="line" points="${pts.join(" ")}" style="--c:${col}"/>`;
  });
  const bw = (W - pl - pr) / Math.max(1, n);
  labels.forEach((l, i) => g += `<rect class="hit" data-day="${l}" data-i="${i}" x="${x(i) - bw / 2}" y="${pt}" width="${bw}" height="${H - pt - pb}"/>`);
  g += `<line class="cursor hide" y1="${pt}" y2="${H - pb}"/>`;
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="chart" role="img" aria-label="Activity chart"><defs><linearGradient id="ag" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="currentColor" stop-opacity=".28"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs>${g}</svg>`;
}
const hbars = (rows, key = "total") => { const m = Math.max(1, ...rows.map((r) => r[key])); return rows.map((r) => `<div class="hbar"><span class="lbl">${esc(srcLabel(r.source))}</span><span class="track"><i style="width:${(r[key] / m * 100).toFixed(1)}%;--c:${srcColor(r.source)}"></i></span><b class="tnum">${num(r[key])}</b></div>`).join("") || `<p class="muted" style="margin:4px 0">No traffic in this period.</p>`; };
const SRC_LBL = { feed: "Feed", search: "Search", direct: "Direct / external", org: "Organization page", profile: "Profile", topic: "Topic page", top: "Top lists", embed: "Embeds", collection: "Collections", notification: "Notifications", library: "Library", related: "Related", viewer: "Swipe viewer" };
const srcLabel = (s) => s.startsWith("share:") ? "Shared · " + ({ whatsapp: "WhatsApp", x: "X", linkedin: "LinkedIn", telegram: "Telegram", facebook: "Facebook", copy: "Copied link", native: "Device share", email: "Email", embed: "Embed code" }[s.slice(6)] || s.slice(6)) : (SRC_LBL[s] || s);
const srcColor = (s) => s.startsWith("share:") ? ({ whatsapp: "#25D366", x: "#111", linkedin: "#0A66C2", telegram: "#229ED9", facebook: "#1877F2" }[s.slice(6)] || "var(--disc)") : s === "search" ? "var(--disc)" : s === "feed" ? "var(--files)" : s === "org" ? "var(--orgs)" : "var(--stats)";

PAGES.analytics = async () => {
  if (!need()) return;
  let scope = params.get("scope") || "me", days = +params.get("days") || 30;
  const from = params.get("from") || "";
  const active = new Set(["view", "download"]);
  const scopes = [["me", "Your files"], ...(ME.orgs || []).filter((o) => o.role !== "member").map((o) => [`org:${o.slug}`, o.name]), ...(ME.is_admin ? [["site", "All of Teams"]] : [])];
  V.innerHTML = `<div class="page-wide" data-an><div class="skeleton-page">${skeletonRows(3)}</div></div>`;
  const root = $("[data-an]", V);
  const draw = async () => {
    history.replaceState(null, "", "/analytics?" + qs({ scope: scope === "me" ? "" : scope, days: days === 30 ? "" : days, from }));
    let d;
    try { d = await GET(`/api/analytics?${qs({ scope, days })}`); } catch (e) { root.innerHTML = empty("chart", "Analytics unavailable", esc(e.message)); return; }
    const crumbs = [`<a href="/analytics">Analytics</a>`];
    if (from && from !== "me") crumbs.push(`<a href="/analytics?scope=${esc(from)}">${esc((scopes.find((s) => s[0] === from) || [, from.replace("org:", "")])[1])}</a>`);
    crumbs.push(`<b>${esc(d.meta.label)}</b>`);
    const delta = (k) => { const a = d.totals[k] || 0, b = d.previous[k]; if (b === undefined) return ""; if (!b) return a ? `<span class="delta up">new</span>` : ""; const p = Math.round((a - b) / b * 100); return `<span class="delta ${p >= 0 ? "up" : "down"}">${p >= 0 ? "▲" : "▼"} ${Math.abs(p)}%</span>`; };
    const kpi = (k, label, icon, color) => `<button class="kpi ${active.has(k) ? "on" : ""}" data-series="${k}" style="--c:${color}"><span class="row between"><span class="lbl">${ic(icon, "sm")}${label}</span>${delta(k)}</span><b class="tnum">${num(d.totals[k] || 0)}</b><span class="muted" style="font-size:12px">${num(d.lifetime[k + "s"] ?? 0)} all time</span></button>`;
    root.innerHTML = `<div class="crumbs">${crumbs.join(ic("chev-r", "sm"))}</div>
      <div class="page-head"><div><h1>${esc(d.meta.label)}</h1><p class="muted">${d.meta.kind === "file" ? `<a href="/f/${d.meta.id}" style="color:var(--disc-ink)">Open document</a> · ` : ""}${d.lifetime.files !== undefined && d.meta.kind !== "file" ? plural(d.lifetime.files, "file") + " · " : ""}${d.followers !== undefined ? plural(d.followers, "follower") + " · " : ""}last ${days} days</p></div>
        <div class="row wrap">${d.meta.kind !== "file" && scopes.length > 1 ? `<select class="select sm" data-scope style="width:auto">${scopes.map(([k, l]) => `<option value="${esc(k)}" ${k === scope ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>` : ""}
          <div class="seg" data-days>${[7, 30, 90, 365].map((n) => `<button data-d="${n}" class="${n === days ? "on" : ""}">${n === 365 ? "1y" : n + "d"}</button>`).join("")}</div></div></div>
      <div class="kpis">${kpi("view", "Views", "eye", "var(--files)")}${kpi("download", "Downloads", "download", "var(--orgs)")}${kpi("share", "Shares", "send", "var(--disc)")}${kpi("like", "Likes", "heart", "var(--social)")}${kpi("comment", "Comments", "message", "var(--stats)")}</div>
      <div class="panel chart-panel"><div class="panel-h"><span>Activity</span><span class="muted" style="font-size:12.5px;font-weight:500">Tap a day to drill down</span></div><div class="panel-b"><div class="chart-wrap" data-chart>${chartSvg(d.labels, d.series, [...active])}<div class="chart-tip hide"></div></div></div></div>
      <div class="an-grid">
        <div class="panel"><div class="panel-h">Where people come from</div><div class="panel-b">${hbars(d.sources)}</div></div>
        ${d.members ? `<div class="panel"><div class="panel-h">Top contributors</div><div class="panel-b">${d.members.map((m) => `<a class="list-row" href="/u/${esc(m.username)}"><b class="grow">${esc(m.full_name || m.username)}</b><span class="muted tnum" style="font-size:13px">${num(m.views)} views · ${num(m.downloads)} dl</span></a>`).join("") || `<p class="muted">No activity yet.</p>`}</div></div>` : ""}
      </div>
      ${d.meta.kind !== "file" ? `<div class="panel" style="margin-top:16px"><div class="panel-h">Documents <span class="muted" style="font-weight:500;font-size:12.5px">in this period · tap to drill down</span></div><div style="overflow-x:auto"><table class="table">
        <thead><tr><th>Document</th><th class="r">Views</th><th class="r">Downloads</th><th class="r">Shares</th><th class="r hide-m">Likes</th><th class="r hide-m">Comments</th></tr></thead>
        <tbody>${d.files.map((f) => `<tr data-file="${f.id}"><td><div class="row" style="gap:10px"><span class="mini-thumb sm">${f.thumb ? `<img src="${f.thumb}">` : ""}</span><span class="clamp-1">${esc(f.title)}</span>${f.visibility !== "public" ? ic(VIS[f.visibility][0], "sm") : ""}</div></td><td class="r tnum">${num(f.views)}</td><td class="r tnum">${num(f.downloads)}</td><td class="r tnum">${num(f.shares)}</td><td class="r tnum hide-m">${num(f.likes)}</td><td class="r tnum hide-m">${num(f.comments)}</td></tr>`).join("") || `<tr><td colspan="6" class="muted" style="text-align:center;padding:24px">No documents yet.</td></tr>`}</tbody></table></div></div>` : ""}`;
    const sc = $("[data-scope]", root); if (sc) sc.onchange = () => { scope = sc.value; draw(); };
    $("[data-days]", root).onclick = (e) => { const b = e.target.closest("[data-d]"); if (b) { days = +b.dataset.d; draw(); } };
    $$("[data-series]", root).forEach((b) => b.onclick = () => { const k = b.dataset.series; if (active.has(k) && active.size > 1) active.delete(k); else active.add(k); b.classList.toggle("on", active.has(k)); $("[data-chart]", root).firstElementChild.outerHTML = chartSvg(d.labels, d.series, [...active]); bindChart(); });
    $$("[data-file]", root).forEach((tr) => tr.onclick = () => location.href = `/analytics?${qs({ scope: "file:" + tr.dataset.file, from: scope, days })}`);
    const bindChart = () => {
      const wrap = $("[data-chart]", root), svg = $("svg", wrap), tip = $(".chart-tip", wrap), cur = $(".cursor", svg);
      svg.addEventListener("mousemove", (e) => {
        const r = e.target.closest(".hit"); if (!r) return;
        const i = +r.dataset.i, xx = +r.getAttribute("x") + +r.getAttribute("width") / 2;
        cur.setAttribute("x1", xx); cur.setAttribute("x2", xx); cur.classList.remove("hide");
        tip.innerHTML = `<b>${new Date(r.dataset.day + "T00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}</b>${[...active].map((k) => { const s = SERIES.find((x) => x[0] === k); return `<span><i style="background:${s[2]}"></i>${s[1]} <b>${num(d.series[k][i])}</b></span>`; }).join("")}`;
        tip.classList.remove("hide");
        const px = xx / 1000 * wrap.clientWidth; tip.style.left = Math.min(wrap.clientWidth - tip.offsetWidth - 4, Math.max(4, px - tip.offsetWidth / 2)) + "px";
      });
      svg.addEventListener("mouseleave", () => { cur.classList.add("hide"); tip.classList.add("hide"); });
      svg.addEventListener("click", (e) => { const r = e.target.closest(".hit"); if (r) dayDrill(r.dataset.day, scope); });
    };
    bindChart();
  };
  draw();
};
async function dayDrill(day, scope) {
  const s = sheet({ title: new Date(day + "T00:00").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" }), wide: true, body: skeletonRows(4) });
  try {
    const d = await GET(`/api/analytics/day?${qs({ day, scope })}`);
    const mh = Math.max(1, ...d.hours);
    $(".sheet-b", s.el).innerHTML = `<div class="section-title" style="margin-top:0">By hour</div>
      <div class="hours">${d.hours.map((v, h) => `<span style="height:${Math.max(3, v / mh * 100)}%" data-tip="${h}:00 · ${v} events"></span>`).join("")}</div><div class="row between muted" style="font-size:11px;margin-top:4px"><span>00:00</span><span>12:00</span><span>23:00</span></div>
      <div class="row gap-12" style="margin:14px 0"><div class="kpi sm"><span class="lbl">Signed in</span><b>${num(d.audience.signed_in)}</b></div><div class="kpi sm"><span class="lbl">Anonymous</span><b>${num(d.audience.anonymous)}</b></div></div>
      <div class="section-title">Sources</div>${hbars(d.sources)}
      <div class="section-title">Documents</div>${d.files.map((f) => `<a class="list-row" href="/analytics?${qs({ scope: "file:" + f.id, from: scope })}"><span class="badge files">${FT_LABEL[f.file_type] || f.file_type}</span><span class="grow clamp-1">${esc(f.title)}</span><span class="muted tnum" style="font-size:13px">${num(f.views)} · ${num(f.downloads)} · ${num(f.shares)}</span></a>`).join("") || `<p class="muted">No activity on this day.</p>`}`;
  } catch (e) { $(".sheet-b", s.el).innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

/* ---------------- activity ---------------- */
const N_ICON = { comment: "message", reply: "message", mention: "at", like: "heart", comment_like: "heart", follow: "user-plus", org_follow: "user-plus", org_invite: "building", feature_request: "star", feature_approved: "check", feature_declined: "x", org_post: "building", collab_added: "users", new_version: "layers", invite_accepted: "check", moderation: "flag" };
function notifHtml(n) {
  const who = n.actor ? `<b>${esc(n.actor.name)}</b> ` : "";
  const obj = n.kind === "follow" ? "" : n.org && ["org_follow", "org_invite", "invite_accepted"].includes(n.kind) ? ` <b>${esc(n.org.name)}</b>` :
    n.file ? ` <b>${esc(n.file.title || "a document")}</b>${n.org && n.kind.startsWith("feature") ? ` ${n.kind === "feature_request" ? "in" : "in"} <b>${esc(n.org.name)}</b>` : ""}${n.org && n.kind === "org_post" ? "" : ""}` : "";
  const org2 = n.kind === "org_post" && n.org ? ` <b>${esc(n.org.name)}</b>` : "";
  const text = n.data && n.data.text ? `<div class="n-quote clamp-2">${esc(n.data.text)}</div>` : "";
  const href = n.kind === "follow" && n.actor ? `/u/${n.actor.username}` : ["org_invite", "org_follow", "invite_accepted"].includes(n.kind) && n.org ? `/o/${n.org.slug}` : n.file ? `/f/${n.file.id}?src=notification${n.comment_id ? "#c" + n.comment_id : ""}` : "#";
  let act = "";
  if (n.pending && n.kind === "org_invite") act = `<div class="row" style="gap:8px;margin-top:8px"><button class="btn solid sm" data-n-inv="${n.org.slug}" data-ok="1">Accept</button><button class="btn sm" data-n-inv="${n.org.slug}" data-ok="0">Decline</button></div>`;
  if (n.pending && n.kind === "feature_request") act = `<div class="row" style="gap:8px;margin-top:8px"><button class="btn solid sm" data-n-feat="${n.org.slug}" data-f="${n.file.id}" data-ok="1">Allow</button><button class="btn sm" data-n-feat="${n.org.slug}" data-f="${n.file.id}" data-ok="0">Decline</button></div>`;
  return `<div class="notif ${n.read ? "" : "unread"}" data-href="${href}">
    <div class="n-av">${n.actor ? avatar(n.actor, "md") : n.org ? avatar({ name: n.org.name, avatar: n.org.avatar, initials: n.org.name.slice(0, 1) }, "md", true) : `<span class="av" style="--c:var(--stats);--s:44px">${ic("flag", "sm")}</span>`}<span class="n-k k-${n.kind}">${ic(N_ICON[n.kind] || "bell", "xs")}</span></div>
    <div class="grow"><div class="n-t">${who}${esc(n.verb)}${obj}${org2}${n.kind === "moderation" ? " after a report." : ""}</div>${text}<div class="faint" style="font-size:12px;margin-top:2px">${esc(n.ago)}</div>${act}</div>
    ${n.file && n.file.thumb ? `<span class="mini-thumb"><img src="${n.file.thumb}" alt="" loading="lazy"></span>` : ""}</div>`;
}
PAGES.activity = async () => {
  if (!need()) return;
  V.innerHTML = `<div class="page-narrow"><div class="page-head"><h1>Activity</h1><button class="btn sm" data-readall>${ic("check", "sm")}Mark all read</button></div><div data-list class="notif-list"></div></div>`;
  const list = $("[data-list]", V);
  pager(list, (cur) => GET(`/api/notifications?${qs({ cursor: cur })}`), (it) => notifHtml(it),
    { emptyHtml: empty("bell", "You're all caught up", "Likes, comments, follows and org invites show up here."), skeleton: skeletonRows(6),
      onItems: () => { POST("/api/notifications/read", {}, { quiet: true }).then(() => $$("[data-ncount]").forEach((e) => { e.textContent = ""; e.classList.add("hide"); })).catch(() => {}); } });
  $("[data-readall]", V).onclick = async () => { await POST("/api/notifications/read", {}); $$(".notif.unread", V).forEach((e) => e.classList.remove("unread")); toast("All caught up"); };
  list.addEventListener("click", async (e) => {
    const inv = e.target.closest("[data-n-inv]"), ft = e.target.closest("[data-n-feat]");
    if (inv) { e.stopPropagation(); try { await POST(`/api/orgs/${inv.dataset.nInv}/respond`, { accept: inv.dataset.ok === "1" }); inv.parentElement.outerHTML = `<div class="notice ${inv.dataset.ok === "1" ? "ok" : ""}" style="margin-top:8px">${inv.dataset.ok === "1" ? "You joined — welcome!" : "Invite declined"}</div>`; } catch (x) { fail(x); } return; }
    if (ft) { e.stopPropagation(); try { await POST(`/api/orgs/${ft.dataset.nFeat}/feature/${ft.dataset.f}/decide`, { approve: ft.dataset.ok === "1" }); ft.parentElement.outerHTML = `<div class="notice ${ft.dataset.ok === "1" ? "ok" : ""}" style="margin-top:8px">${ft.dataset.ok === "1" ? "Approved — it's now featured" : "Declined"}</div>`; } catch (x) { fail(x); } return; }
    const n = e.target.closest("[data-href]"); if (n && n.dataset.href !== "#" && !e.target.closest("a,button")) location.href = n.dataset.href;
  });
};

/* ---------------- settings ---------------- */
PAGES.settings = async () => {
  if (!need()) return;
  const acc = BOOT.auth_mode === "accounts";
  const colors = ["#5C5DE2", "#20A653", "#447AA6", "#E2595C", "#E28A2B", "#9B51E0", "#0EA5A5", "#D946A8"];
  const draw = () => {
    V.innerHTML = `<div class="page-narrow">
      <div class="page-head"><div><h1>Settings</h1><p class="muted">Your profile is shared across the Collab suite${acc ? " via Collab Accounts" : ""}.</p></div></div>
      <div class="panel"><div class="panel-h">Profile picture</div><div class="panel-b row gap-16 wrap">
        <div class="av-edit-wrap">${avatar(ME, "xl")}</div>
        <div class="col" style="gap:8px"><div class="row" style="gap:8px"><label class="btn solid sm">${ic("upload", "sm")}Upload photo<input type="file" accept="image/*" hidden data-av></label>${ME.avatar ? `<button class="btn sm" data-av-del>Remove</button>` : ""}</div>
        <span class="faint" style="font-size:12.5px">Square crop, 256px. JPG, PNG, WebP or GIF up to 8 MB.${acc ? " Updates everywhere you use Collab." : ""}</span>
        ${!ME.avatar ? `<div class="row" style="gap:6px;margin-top:4px">${colors.map((c) => `<button class="swatch ${c.toLowerCase() === (ME.color || "").toLowerCase() ? "on" : ""}" style="--c:${c}" data-color="${c}" aria-label="Colour ${c}"></button>`).join("")}</div>` : ""}</div></div></div>
      <div class="panel"><div class="panel-h">About you</div><div class="panel-b">
        <label class="field"><span>Name</span><input class="input" data-name value="${esc(ME.full_name)}" maxlength="100"></label>
        <label class="field"><span>Username</span><div class="input-prefix"><b>@</b><input class="input" value="${esc(ME.username)}" disabled></div></label>
        <label class="field"><span>Bio</span><textarea class="input" data-bio rows="3" maxlength="400" placeholder="What do you share about?">${esc(ME.bio || "")}</textarea></label>
        <label class="field"><span>Email</span><input class="input" value="${esc(ME.email || "")}" disabled></label>
        <div class="row between"><span class="faint" style="font-size:12.5px">${acc ? `Email, phone and password live in <a href="${esc(BOOT.accounts_url)}/profile" style="color:var(--disc-ink)">Collab Accounts</a>.` : "Local mode — no password needed."}</span><button class="btn solid" data-save-p>Save</button></div>
      </div></div>
      <div class="panel"><div class="panel-h">Appearance</div><div class="panel-b"><div class="seg" data-theme-seg>${[["system", "Auto"], ["light", "Light"], ["dark", "Dark"]].map(([k, l]) => `<button data-t="${k}" class="${(localStorage.theme || "system") === k ? "on" : ""}">${l}</button>`).join("")}</div></div></div>
      <div class="panel"><div class="panel-h">Shortcuts</div><div class="panel-b list-kv"><span><kbd>/</kbd> Search</span><span><kbd>j</kbd> <kbd>k</kbd> Next / previous in the viewer</span><span><kbd>Shift</kbd>+click Select a range in your library</span><span><kbd>Ctrl</kbd>+<kbd>A</kbd> Select all (select mode)</span></div></div>
      <div class="row between" style="margin-top:8px"><a class="btn" href="/u/${esc(ME.username)}">${ic("user", "sm")}View profile</a><button class="btn danger" data-logout>${ic("logout", "sm")}Sign out</button></div>
    </div>`;
    const set = (u) => { Object.assign(ME, u); $$("[data-menu=me] .av, .tabbar .av, .side .me-chip .av").forEach((e) => { const sz = [...e.classList].find((c) => ["xs", "sm", "md", "lg", "xl"].includes(c)) || ""; e.outerHTML = avatar(ME, sz); }); draw(); };
    $("[data-av]", V).onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      const fd = new FormData(); fd.append("file", f);
      $(".av-edit-wrap", V).classList.add("loading");
      try { const r = await api("POST", "/api/me/avatar", fd); set(r.user); toast("Photo updated"); } catch (x) { fail(x); $(".av-edit-wrap", V).classList.remove("loading"); }
    };
    const del = $("[data-av-del]", V); if (del) del.onclick = async () => { try { const r = await api("DELETE", "/api/me/avatar"); set(r.user); toast("Photo removed"); } catch (x) { fail(x); } };
    $$("[data-color]", V).forEach((b) => b.onclick = async () => { try { const r = await api("PATCH", "/api/me", { avatar_color: b.dataset.color }); set(r.user); } catch (x) { fail(x); } });
    $("[data-save-p]", V).onclick = async (e) => {
      e.target.disabled = true;
      try { const r = await api("PATCH", "/api/me", { full_name: $("[data-name]", V).value, bio: $("[data-bio]", V).value }); Object.assign(ME, r.user); toast("Saved"); } catch (x) { fail(x); }
      e.target.disabled = false;
    };
    $("[data-theme-seg]", V).onclick = (e) => { const b = e.target.closest("[data-t]"); if (!b) return; setTheme(b.dataset.t); $$("[data-t]", V).forEach((x) => x.classList.toggle("on", x === b)); };
    $("[data-logout]", V).onclick = logout;
  };
  draw();
};

/* ---------------- admin ---------------- */
PAGES.admin = async () => {
  if (!need()) return;
  let status = "open";
  V.innerHTML = `<div class="page-wide"><div class="page-head"><div><h1>Moderation</h1><p class="muted">Reports from the community. Hidden items disappear from feeds, search and links.</p></div>
    <div class="seg" data-st><button data-s="open" class="on">Open</button><button data-s="resolved">Resolved</button><button data-s="dismissed">Dismissed</button></div></div>
    <div class="panel"><div class="panel-h">Verify an organization</div><div class="panel-b row" style="gap:8px"><div class="input-prefix grow"><b>/o/</b><input class="input" data-vslug placeholder="org-slug"></div><button class="btn" data-verify>${ic("badge", "sm")}Toggle verified</button></div></div>
    <div data-reports></div></div>`;
  const box = $("[data-reports]", V);
  const load = async () => {
    box.innerHTML = skeletonRows(4);
    try {
      const d = await GET(`/api/admin/reports?status=${status}`);
      box.innerHTML = d.items.map((r) => `<div class="panel report" data-r="${r.id}"><div class="panel-b">
        <div class="row between wrap"><div class="row" style="gap:8px"><span class="badge rose">${esc(r.reason)}</span><b>${r.file_id ? `<a href="/f/${r.file_id}">${esc(r.file_title || "Document #" + r.file_id)}</a>` : "Comment"}</b>${(r.file_hidden || r.comment_hidden) ? `<span class="badge">hidden</span>` : ""}</div><span class="faint" style="font-size:12.5px">by @${esc(r.reporter || "anon")} · ${esc(r.ago)}</span></div>
        ${r.comment_body ? `<div class="n-quote">${esc(r.comment_body)}</div>` : ""}${r.details ? `<p class="muted" style="margin:8px 0 0">${esc(r.details)}</p>` : ""}
        ${status === "open" ? `<div class="row" style="gap:8px;margin-top:12px"><button class="btn danger sm" data-a="hide">${ic("eye-off", "sm")}Hide</button><button class="btn sm" data-a="dismiss">Dismiss</button></div>` :
          (r.file_hidden || r.comment_hidden) ? `<div class="row" style="margin-top:12px"><button class="btn sm" data-a="unhide">Restore</button></div>` : ""}
      </div></div>`).join("") || empty("flag", status === "open" ? "No open reports" : "Nothing here", "Reports from the community will appear here.");
    } catch (e) { box.innerHTML = empty("flag", "Couldn't load reports", esc(e.message)); }
  };
  box.onclick = async (e) => { const b = e.target.closest("[data-a]"); if (!b) return; const id = b.closest("[data-r]").dataset.r; try { await POST(`/api/admin/reports/${id}`, { action: b.dataset.a }); toast("Done"); load(); } catch (x) { fail(x); } };
  $("[data-st]", V).onclick = (e) => { const b = e.target.closest("[data-s]"); if (!b) return; status = b.dataset.s; $$("[data-s]", V).forEach((x) => x.classList.toggle("on", x === b)); load(); };
  $("[data-verify]", V).onclick = async () => { const s = $("[data-vslug]", V).value.trim().replace(/^\/?o\//, ""); if (!s) return; try { const r = await POST(`/api/admin/orgs/${encodeURIComponent(s)}/verify`, {}); toast(r.verified ? "Verified ✓" : "Verification removed"); } catch (x) { fail(x); } };
  load();
};

/* ---------------- auth ---------------- */
PAGES.auth = async (d) => {
  document.body.classList.add("auth-body");
  const acc = BOOT.auth_mode === "accounts", mode = d.mode;
  if (mode === "forgot" && !acc) { location.replace("/login"); return; }
  const nxt = encodeURIComponent(d.next || "/");
  V.innerHTML = `<div class="auth-wrap"><div class="auth-art" aria-hidden="true"><div class="art-cards"><i></i><i></i><i></i></div><h2>Share what you know.<br>Discover what others are reading.</h2><p>Documents, organizations and conversations — one calm, fast home for your team's knowledge.</p></div>
    <form class="auth-card" data-form novalidate>
      <a href="/" class="brand">${ic("logo", "lg")}<b>Teams</b></a>
      <h1>${mode === "signup" ? "Create your account" : mode === "forgot" ? "Reset password" : "Welcome back"}</h1>
      <p class="muted">${mode === "signup" ? "One Collab account works across the whole suite." : mode === "forgot" ? "We'll email you a reset link." : acc ? "Sign in with your Collab account." : "Local mode — sign in with any email."}</p>
      ${acc && mode !== "forgot" ? `<a class="btn sso" href="${esc(d.sso)}">${ic("logo", "sm")}Continue with Collab Accounts</a><div class="divider"><span>or</span></div>` : ""}
      ${mode === "signup" ? `<label class="field"><span>Full name</span><input class="input" name="full_name" autocomplete="name" required minlength="2"></label>
        <label class="field"><span>Username</span><div class="input-prefix"><b>@</b><input class="input" name="username" autocomplete="username" required pattern="[a-z0-9][a-z0-9_-]{2,62}"></div></label>` : ""}
      <label class="field"><span>${mode === "login" ? "Email or username" : "Email"}</span><input class="input" name="${mode === "login" ? "identifier" : "email"}" type="${mode === "login" ? "text" : "email"}" autocomplete="${mode === "login" ? "username" : "email"}" required autofocus></label>
      ${acc && mode !== "forgot" ? `<label class="field"><span class="row between">Password${mode === "login" ? `<a href="/forgot?next=${nxt}" class="faint" style="font-weight:500">Forgot?</a>` : ""}</span><input class="input" name="password" type="password" autocomplete="${mode === "signup" ? "new-password" : "current-password"}" required minlength="6"></label>` : ""}
      <div class="form-err hide" data-err></div>
      <button class="btn solid big" type="submit">${mode === "signup" ? "Create account" : mode === "forgot" ? "Send link" : "Sign in"}</button>
      <p class="muted" style="text-align:center;margin:4px 0 0">${mode === "signup" ? `Already have an account? <a href="/login?next=${nxt}">Sign in</a>` : `New to Collab? <a href="/signup?next=${nxt}">Create an account</a>`}</p>
    </form></div>`;
  const form = $("[data-form]", V), err = $("[data-err]", V);
  form.onsubmit = async (e) => {
    e.preventDefault(); err.classList.add("hide");
    const body = Object.fromEntries(new FormData(form)); const btn = $("button[type=submit]", form);
    if (body.username) body.username = body.username.toLowerCase();
    btn.disabled = true; btn.innerHTML = `<span class="spinner sm"></span>`;
    try {
      await api("POST", mode === "signup" ? "/api/signup" : mode === "forgot" ? "/api/forgot" : "/api/login", body, { quiet: true });
      if (mode === "forgot") { form.innerHTML = `<h1>Check your email</h1><p class="muted">If an account exists, a reset link is on its way.</p><a class="btn" href="/login">Back to sign in</a>`; return; }
      location.href = d.next || "/";
    } catch (x) { err.textContent = x.message; err.classList.remove("hide"); btn.disabled = false; btn.textContent = mode === "signup" ? "Create account" : "Sign in"; }
  };
};

/* ---------------- error ---------------- */
PAGES.error = async (d) => {
  d = d || { status: 404 };
  const nf = d.status === 404;
  V.innerHTML = `<div class="page-narrow">${empty(nf ? "search" : "alert", nf ? "This page wandered off" : (d.status === 403 ? "You don't have access" : "Something went wrong"),
    esc(typeof d.detail === "string" ? d.detail : nf ? "The link may be broken, or the document is private." : "Please try again in a moment."),
    `<div class="row" style="gap:8px;justify-content:center"><a class="btn solid" href="/">Go home</a>${!ME ? `<a class="btn" href="/login?next=${encodeURIComponent(location.pathname)}">Sign in</a>` : ""}</div>`)}</div>`;
};

/* ---------------- boot ---------------- */
try { (PAGES[BOOT.page] || PAGES.error)(BOOT.data || {}); }
catch (e) { console.error(e); V.innerHTML = `<div class="page-narrow">${empty("alert", "Something went wrong", esc(e.message))}</div>`; }
})();
