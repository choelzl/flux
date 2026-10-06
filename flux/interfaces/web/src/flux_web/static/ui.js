// Flux web's building blocks (D889: split out of app.js): h(), the server calls, notices and
// dialogs, a page's parts and the formatting every page shares.

import { me, navSeq, pageOwner, setMe, setPageOwner } from "./state.js";

const main = document.getElementById("main");

// ================================================================ building blocks
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false || kid === "") continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

// D701: on a loop shared with this user (or an admin's look at another's), every call about it
// names its owner -- the server checks what this user may do with it
function owned(path) {
  if (!pageOwner || !/^\/apps\/[^/?]+/.test(path) || /[?&]owner=/.test(path)) return path;
  return path + (path.includes("?") ? "&" : "?") + "owner=" + encodeURIComponent(pageOwner);
}
async function withOwner(owner, fn) {
  const was = pageOwner; setPageOwner(owner || null);
  try { return await fn(); } finally { setPageOwner(was); }
}
/** A server call's answer, checked (D907): the session that ended, the server out of reach and an
    error said as for any call -- for a caller that reads the body itself (a file, its headers). */
async function request(path, { method = "GET", body, form, signal } = {}) {
  path = owned(path);
  const opt = { method, headers: { "X-Flux": "1" }, credentials: "same-origin", signal };
  if (form) opt.body = form;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  let r;
  try { r = await fetch("/api" + path, opt); }
  catch (x) { if (x.name === "AbortError") throw x; offline(true); throw new Error("The server cannot be reached."); }   // D917: a call let go is no outage
  offline(false);
  if (r.status === 401 && path !== "/login") {
    // D757: a session that ended (logged out elsewhere, expired) is said, not a silent jump to the login
    if (me && location.hash !== "#/login") toast("Your session ended: log in again.", "warn", { timeout: 8000 });
    setMe(null); location.hash = "#/login"; throw new Error("log in");
  }
  if (!r.ok) {
    const type = r.headers.get("content-type") || "";
    const data = type.includes("json") ? await r.json().catch(() => null) : await r.text();
    // D906: the status goes with the error -- a caller tells a conflict (409) from a refusal
    const fail = (m) => Object.assign(new Error(m), { status: r.status });
    if (data && data.detail) throw fail(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
    // D700: an error page that is not the server's own (a proxy's, a crash): its status at least
    const said = typeof data === "string" ? data.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 160) : "";
    throw fail(r.status === 413 ? "Too large for the server (or a proxy in front of it): 413." : `The server answered ${r.status} ${r.statusText}${said ? ": " + said : ""}`);
  }
  return r;
}
async function api(path, opts = {}) {
  const r = await request(path, opts);
  return (r.headers.get("content-type") || "").includes("json") ? r.json() : r.text();
}

// ---- the server out of reach (D694): a banner while it is, gone at the next answer
const offlineBar = h("div", { class: "offline", role: "alert", hidden: true }, "The server cannot be reached: retrying…");
document.body.append(offlineBar);
function offline(on) { if (offlineBar.hidden === on) offlineBar.hidden = !on; }

/** A server-sent stream that outlives a dropped connection (D694). The browser reconnects by
    itself with the last event's id; when it gives up (a proxy's error page, a restarted server),
    the stream is opened again with that id, waiting longer each time, up to 30 s. */
function followStream(url, event, onData, onState, onSkipped) {
  let es = null, last = null, closed = false, wait = 1000, timer = null;
  const say = (st) => { if (onState) onState(st); };
  const open = () => {
    es = new EventSource(last ? `${url}${url.includes("?") ? "&" : "?"}offset=${encodeURIComponent(last)}` : url);
    es.addEventListener(event, (m) => {
      if (m.lastEventId) last = m.lastEventId;
      const d = JSON.parse(m.data);
      if (Array.isArray(d)) d.forEach(onData); else onData(d);       // D855: a journal slice comes whole
    });
    if (onSkipped) es.addEventListener("skipped", (m) => onSkipped(JSON.parse(m.data)));   // D759: what a tail left out
    es.onopen = () => { wait = 1000; say("live"); };
    es.onerror = () => {
      if (closed) return;
      say("reconnecting");
      if (es.readyState === EventSource.CLOSED) { es.close(); timer = setTimeout(open, wait); wait = Math.min(wait * 2, 30000); }
    };
  };
  open();
  return { close: () => { closed = true; clearTimeout(timer); if (es) es.close(); } };
}
/** D917: a loop's journal, live state and log over ONE server-sent stream (/stream): three per Live
    page took half of the six connections a browser keeps to a server on HTTP/1, and a second Live
    tab left none for an ordinary request. Each part is its view's (`on`); the stream is open only
    while a part is wanted (`want`, the view shown) and the page is visible, and reopens with each
    part's cursor -- nothing missed, nothing twice. `restart`: a part from its beginning ("Load all"). */
function loopStream(base, qs) {
  const subs = {}, cur = { events: "", log: "" }, opt = { window: 0, tail: 0 };
  let wanted = [], es = null, parts = "", closed = false, wait = 1000, timer = null;
  const say = (st) => { for (const k of parts.split(",")) if (subs[k] && subs[k].onState) subs[k].onState(st); };
  const url = () => {
    const p = new URLSearchParams(qs.replace(/^\?/, ""));
    p.set("parts", parts);
    for (const k of ["events", "log"]) if (cur[k]) p.set(k, cur[k]);
    if (opt.window) p.set("window", opt.window);
    if (opt.tail) p.set("tail", opt.tail);
    return `${base}/stream?${p}`;
  };
  const took = (m) => {                                    // each part's cursor, as delivered up to this message
    for (const piece of (m.lastEventId || "").split(",")) { const i = piece.indexOf(":"); if (i > 0) cur[piece.slice(0, i)] = piece.slice(i + 1); }
  };
  function connect() {
    const mine = es = new EventSource(url());
    for (const kind of ["events", "log"]) {
      mine.addEventListener(kind, (m) => {
        took(m);
        const d = JSON.parse(m.data), f = subs[kind] && subs[kind].onData;
        if (f) { if (Array.isArray(d)) d.forEach(f); else f(d); }       // D855: a journal slice comes whole
      });
    }
    mine.addEventListener("live", (m) => { let d; try { d = JSON.parse(m.data); } catch (_) { return; } if (subs.live) subs.live.onData(d); });
    mine.addEventListener("skipped", (m) => { if (subs.log && subs.log.onSkipped) subs.log.onSkipped(JSON.parse(m.data)); });   // D759: what a tail left out
    mine.addEventListener("ready", () => { if (subs.events && subs.events.onReady) subs.events.onReady(); });
    mine.onopen = () => { wait = 1000; say("live"); };
    mine.onerror = () => {
      if (es !== mine) return;
      say("reconnecting");
      if (mine.readyState === EventSource.CLOSED) {       // given up (a proxy's page, a restarted server): again, with the cursors
        mine.close(); es = null;
        timer = setTimeout(() => { timer = null; if (!es && parts) connect(); }, wait); wait = Math.min(wait * 2, 30000);
      }
    };
  }
  function sync() {
    const now = closed || document.hidden ? "" : wanted.filter(k => subs[k]).sort().join(",");
    if (now === parts && (es || timer || !now)) return;
    clearTimeout(timer); timer = null;
    if (es) { es.close(); es = null; }
    parts = now;
    if (parts) connect();
  }
  document.addEventListener("visibilitychange", sync);
  return {
    on(kind, handlers, o = {}) { subs[kind] = handlers; Object.assign(opt, o); },
    want(kinds) { wanted = kinds; sync(); },
    restart(kind, o = {}) { cur[kind] = ""; Object.assign(opt, o); parts = ""; if (es) { es.close(); es = null; } sync(); },
    close() { closed = true; document.removeEventListener("visibilitychange", sync); sync(); },
  };
}
function streamPill() {
  // D856: said only while the page is not connected -- a green "live" beside an idle loop read as running
  const el = h("span", { class: "pill stream warn", title: "The page's connection to the loop" }, "connecting…");
  return { el, set: (st) => { el.hidden = st === "live"; el.textContent = "reconnecting…"; } };
}
const fmtTok = (n) => !n ? "0" : n >= 1e9 ? (n / 1e9).toFixed(2) + "G" : n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e4 ? Math.round(n / 1e3) + "k" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(Math.round(n));

const enc = encodeURIComponent;
const when = (t) => t ? new Date(t * 1000).toLocaleString() : "";
function dur(s) {
  if (s == null || !isFinite(s)) return "";
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}m`;
}
function ago(t) {
  if (!t) return "";
  const s = Date.now() / 1000 - t;
  const text = s < 45 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
  return h("time", { title: when(t) }, text);
}
/** A loop's state: running (since), idle, or how its last start ended. */
function statePill(st) {
  if (!st) return "";
  if (st.running) return h("span", { class: "pill live" }, h("i", { class: "dot" }), st.stop_requested ? "stopping" : "running");
  if (st.failed) return h("span", { class: "pill bad" }, "failed");
  if (st.stopped) return h("span", { class: "pill warn" }, "stopped");
  return h("span", { class: "pill" }, st.last_active ? "idle" : "never run");
}
function show(...nodes) { main.replaceChildren(...nodes); window.scrollTo(0, 0); }
/** D719: each navigation's number; a page shows itself only while it is the latest -- a slow page
    (waiting on the server) must not draw over the one the user went to since. A page function's
    first line shadows `show` with its own: `const show = pageShow();`. */
function pageShow() {
  const mine = navSeq;
  const f = (...nodes) => { if (mine === navSeq) show(...nodes); };
  f.stale = () => mine !== navSeq;                 // left already: no timers, no refresh hook
  return f;
}
/** Where this page is (D702): [label, href] from Loops down; the last is the page itself. */
function crumbs(...parts) {
  return h("nav", { class: "crumbs-bar", "aria-label": "Where you are" }, parts.filter(Boolean).map(([label, href], i, all) =>
    [i ? h("span", { class: "sep", "aria-hidden": "true" }, "›") : "", i < all.length - 1 && href ? h("a", { href }, label) : h("span", { "aria-current": i === all.length - 1 ? "page" : null }, label)]));
}
function head(title, sub, ...actions) {
  return h("div", { class: "page-head" }, h("div", {}, h("h1", {}, title), sub ? h("p", { class: "sub" }, sub) : ""),
    actions.length ? h("div", { class: "actions" }, actions) : "");
}
/** A placeholder while a page loads (D702): grey lines of the shape to come, not a word. */
function skeleton(lines = 5) {
  return h("div", { class: "skeleton", "aria-busy": "true", "aria-label": "Loading" },
    Array.from({ length: lines }, (_, i) => h("div", { class: "sk-line", style: `width:${[92, 76, 84, 60, 70, 88, 54][i % 7]}%` })));
}
function card(title, kids, { actions, cls } = {}) {
  return h("section", { class: "card " + (cls || "") },
    title || actions ? h("div", { class: "card-head" }, title ? h("h2", {}, title) : "", actions ? h("div", { class: "actions" }, actions) : "") : "",
    kids);
}
function empty(text, ...more) { return h("div", { class: "empty" }, h("p", {}, text), more); }
function appHref(user, app) {
  return user && me && user !== me.name ? `#/u/${enc(user)}/app/${enc(app)}` : `#/app/${enc(app)}`;
}
/** A button whose async action disables it while it runs, and says a failure as a notice. */
/** D833: a field that saves as it changes -- a moment after typing stops, or on leaving it -- with a
    mark beside it: saving, saved, or why the server refused it. `run()` saves; saves never overlap. */
function saveMark() { return h("span", { class: "save-mark small", "aria-live": "polite" }); }
function autosave(fields, run, mark, { delay = 900, typing = true } = {}) {
  let t = null, chain = Promise.resolve(), clear = null;
  const go = () => {
    clearTimeout(t);
    chain = chain.then(async () => {
      clearTimeout(clear);
      mark.className = "save-mark small"; mark.textContent = "saving…";
      try {
        await run();
        mark.classList.add("ok"); mark.textContent = "saved";
        clear = setTimeout(() => { if (mark.textContent === "saved") mark.textContent = ""; }, 2500);
      } catch (x) { mark.classList.add("bad"); mark.textContent = x.message === "log in" ? "" : x.message; }
    });
    return chain;
  };
  for (const el of [].concat(fields)) {
    if (typing) el.addEventListener("input", () => { clearTimeout(t); t = setTimeout(go, delay); });
    el.addEventListener("change", go);
  }
  return go;
}
function act(label, fn, { cls = "", title } = {}) {
  const b = h("button", { class: cls, title, type: "button" }, label);
  b.addEventListener("click", async () => {
    b.disabled = true; b.classList.add("busy");
    try { await fn(b); } catch (x) { if (x.message !== "log in") toast(x.message, "bad"); }
    finally { b.disabled = false; b.classList.remove("busy"); }
  });
  return b;
}

// ---- notices and dialogs (no alert, confirm or prompt)
const toasts = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
document.body.append(toasts);
function toast(text, kind = "info", { timeout = 5000, href } = {}) {
  // D700: a dialog is in the top layer: a notice shown under it was never seen
  const host = [...document.querySelectorAll("dialog.dlg[open]")].pop() || document.body;
  if (toasts.parentNode !== host) host.append(toasts);
  const t = h("div", { class: `toast ${kind}` }, href ? h("a", { href }, text) : text,
    h("button", { class: "x", "aria-label": "dismiss", onclick: () => t.remove() }, "×"));
  toasts.append(t);
  if (timeout) setTimeout(() => t.remove(), timeout);
}
// D700: a failure nobody caught is said, not lost in the console
window.addEventListener("unhandledrejection", (e) => {
  const m = e.reason && e.reason.message ? e.reason.message : String(e.reason || "");
  if (m && m !== "log in") toast(m, "bad", { timeout: 8000 });
});
window.addEventListener("error", (e) => { if (e.message) toast(`The page failed: ${e.message}`, "bad", { timeout: 8000 }); });

function dialog(title, body, buttons) {
  return new Promise((resolve) => {
    const d = h("dialog", { class: "dlg" });
    const done = (v) => { d.close(); if (toasts.parentNode === d) document.body.append(toasts); d.remove(); resolve(v); };
    d.append(h("h2", {}, title), body, h("div", { class: "dlg-actions" }, buttons.map(([label, value, cls]) =>
      h("button", { class: cls || "", type: "button", onclick: () => done(typeof value === "function" ? value() : value) }, label))));
    d.addEventListener("cancel", (e) => { e.preventDefault(); done(null); });
    document.body.append(d); d.showModal();
    const f = d.querySelector("input, textarea"); (f || d.querySelector("button.primary, button.danger") || d).focus();
  });
}
function confirmDialog(title, text, { ok = "OK", danger = false } = {}) {
  return dialog(title, h("p", {}, text), [["Cancel", false], [ok, true, danger ? "danger solid" : "primary"]]);
}
function promptDialog(title, label, { type = "text", ok = "Save", min = 0 } = {}) {
  const input = h("input", { type, autocomplete: "off", style: "width:100%" });
  const body = h("label", { class: "stack" }, label, input);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") body.closest("dialog").querySelector("button.primary").click(); });
  return dialog(title, body, [["Cancel", null], [ok, () => (input.value.length >= min ? input.value : null), "primary"]]);
}

/** A new loop from a document's text (D906): a name already taken is never replaced -- the user
    opens that loop or chooses another name. True when made (or found made by this same request). */
async function createFromText(name, filename, text) {
  try {
    await api("/apps/from-text", { method: "POST", body: { name, filename, text } });
    return true;
  } catch (x) {
    if (x.status !== 409) throw x;
    const go = await dialog(`${name} exists`, h("p", {}, `A loop named ${name} already exists. Creating does not replace it: `
      + "open it (and change it there, with a diff before saving), or choose another name."),
      [["Choose another name", false, "primary"], ["Open it", true]]);
    if (go) location.hash = `#/app/${enc(name)}`;
    return false;
  }
}

/** Sizes as people read them (moved from the admin pages, D889: the uploads use it too); D908:
    powers of 1024 named as such -- KiB, MiB, GiB -- everywhere a size is shown. */
const bytes = (n) => n == null ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KiB` : n < 1073741824 ? `${(n / 1048576).toFixed(1)} MiB` : `${(n / 1073741824).toFixed(2)} GiB`;
/** A table sorted by a click on a column's header (D859): a button in each sortable header (so the
    keyboard reaches it too) and `aria-sort`; the same header again turns the order round. A column's
    first order is descending (most, newest) unless it says `asc`. Kept per table in this browser.
    D926: names natural and case-insensitive (item2 before Item10), numbers as numbers, a list key one
    value after the other (what a cell shows, in its order); ties keep the rows' own order; a missing
    value (null, "") last either way. Each row's node is made once and moved, never rebuilt -- a field
    being edited, its pending save and the focus stay. On a phone (the header hidden) `table.strip`,
    a column menu and an order button above the rows, sorts the same way: the caller places it. */
const COLLATE = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const isMissing = (v) => v == null || v === "" || (typeof v === "number" && isNaN(v))
  || (Array.isArray(v) && v.every(x => x == null || x === ""));
function cmpSort(x, y) {
  if (Array.isArray(x) || Array.isArray(y)) {
    const a = [].concat(x), b = [].concat(y);
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      const ma = isMissing(a[i]), mb = isMissing(b[i]);
      if (ma || mb) { if (ma !== mb) return ma ? 1 : -1; continue; }
      const c = cmpSort(a[i], b[i]); if (c) return c;
    }
    return 0;
  }
  if (typeof x === "number" && typeof y === "number") return x - y;
  return COLLATE.compare(String(x), String(y));
}
const sortMemo = new Map();                       // D926: when this browser keeps nothing, kept for the page's life
function sortableTable(memo, cols, rows, rowFn, firstCol = 0, { cls = "list compact" } = {}) {
  const idOf = (c) => c.id || c.label;
  let col = firstCol, desc = !cols[firstCol].asc;
  try {
    let m = sortMemo.get(memo);
    try { m = JSON.parse(localStorage.getItem(memo) || "null") || m; } catch (_) { /* this page's own */ }
    const at = m ? (m.id != null ? cols.findIndex(c => c.key && idOf(c) === m.id) : m.col) : -1;
    if (at >= 0 && cols[at] && cols[at].key) { col = at; desc = !!m.desc; }
  } catch (_) { /* a default */ }
  const nodes = new Map();                         // a row -> its <tr>, made once
  const nodeOf = (r) => { let n = nodes.get(r); if (!n) nodes.set(r, n = rowFn(r)); return n; };
  const body = h("tbody", {});
  const ths = cols.map((c, i) => {
    if (!c.key) return h("th", { class: c.num ? "num" : "", title: c.title || null }, c.label);
    const arrow = h("span", { class: "th-arrow", "aria-hidden": "true" });
    const btn = h("button", { type: "button", class: "th-sort", onclick: () => pick(i, i === col ? !desc : !c.asc) }, c.label, arrow);
    return Object.assign(h("th", { class: c.num ? "num" : "", title: c.title || null, "data-label": c.label }, btn), { arrow, btn });
  });
  const colSel = h("select", { "aria-label": "Sort by", onchange: () => pick(Number(colSel.value), !cols[Number(colSel.value)].asc) },
    cols.map((c, i) => c.key ? h("option", { value: String(i) }, c.label) : null));
  const dirBtn = h("button", { type: "button", class: "small sort-dir", onclick: () => pick(col, !desc) });
  const strip = h("div", { class: "sort-strip" }, h("label", { class: "sort" }, "Sort by ", colSel), dirBtn);
  function pick(i, d) {
    col = i; desc = d;
    const m = { id: idOf(cols[col]), desc };
    sortMemo.set(memo, m);
    try { localStorage.setItem(memo, JSON.stringify(m)); } catch (_) { /* per viewer */ }
    draw();
  }
  function draw() {
    const k = cols[col].key;
    const keyed = rows.map((r, i) => ({ r, i, v: k(r) }));
    keyed.sort((a, b) => {
      const ma = isMissing(a.v), mb = isMissing(b.v);
      if (ma || mb) return ma === mb ? a.i - b.i : ma ? 1 : -1;               // missing last, either way
      return (desc ? cmpSort(b.v, a.v) : cmpSort(a.v, b.v)) || a.i - b.i;    // ties: the rows' own order
    });
    const had = document.activeElement;
    body.append(...keyed.map(x => nodeOf(x.r)));                           // moved, not rebuilt
    if (had && had !== document.activeElement && body.contains(had)) had.focus({ preventScroll: true });
    ths.forEach((th, i) => {
      if (!cols[i].key) return;
      th.setAttribute("aria-sort", i === col ? (desc ? "descending" : "ascending") : "none");
      th.btn.classList.toggle("on", i === col);
      th.arrow.textContent = i === col ? (desc ? " ▾" : " ▴") : "";
    });
    colSel.value = String(col);
    dirBtn.textContent = desc ? "▾ Descending" : "▴ Ascending";
    dirBtn.title = "Turn the order round";
  }
  draw();
  const table = h("table", { class: `${cls} sortable` }, h("thead", {}, h("tr", {}, ths)), body);
  table.strip = strip;
  return table;
}

// D754: a phone's width -- the tables stack their rows (app.js), the log wraps (live.js)
const NARROW = window.matchMedia ? window.matchMedia("(max-width: 640px)") : { matches: false };

export { NARROW, act, ago, api, appHref, request, autosave, bytes, card, confirmDialog, createFromText, crumbs, dialog, dur, empty,
  enc, fmtTok, followStream, h, head, loopStream, offline, owned, pageShow, saveMark, show, skeleton, sortableTable,
  statePill, streamPill, toast, toasts, when, withOwner };
