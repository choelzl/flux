// Flux web's building blocks (D889: split out of app.js; D1095: the pages are Preact components, view.js): the
// server calls (a loop's owner named, the session's end and the server's absence said), a loop's one stream, a page
// shown only while it is the latest; the notices (notices.js), the dialogs (components/Modal.js) and the words for
// numbers and times (format.js) re-exported for the pages that read them here.

import { me, pageOwner, sessionGen, setMe, setPageOwner } from "./state.js";
import { bytes, dur, fmtTok, money, plural, when } from "./format.js";
import { html } from "./view.js";
import { offline, toast } from "./notices.js";
import { askText, confirmDialog, dialog } from "./components/Modal.js";



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
  const gen = sessionGen;
  try { r = await fetch("/api" + path, opt); }
  catch (x) { if (x.name === "AbortError") throw x; offline(true); throw new Error("The server cannot be reached."); }   // D917: a call let go is no outage
  offline(false);
  if (r.status === 401 && path !== "/login") {
    if (gen !== sessionGen) throw new Error("log in");      // sent before a login since: not this session's end
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
/** D1075: a form posted with its upload's progress -- `onProgress(sent, total)` as the bytes go (fetch says
    nothing until the answer); `signal` cancels it. The answer and its errors as `api`'s. */
function apiUpload(path, form, { onProgress = () => {}, signal } = {}) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", "/api" + owned(path));
    x.setRequestHeader("X-Flux", "1");
    x.upload.onprogress = e => { if (e.lengthComputable) onProgress(e.loaded, e.total); };
    x.onerror = () => { offline(true); reject(new Error("The server cannot be reached.")); };
    x.onabort = () => reject(Object.assign(new Error("cancelled"), { name: "AbortError" }));
    x.onload = () => {
      offline(false);
      let data = null;
      try { data = JSON.parse(x.responseText); } catch { data = x.responseText; }
      if (x.status >= 200 && x.status < 300) return resolve(data);
      if (x.status === 401) { setMe(null); location.hash = "#/login"; return reject(new Error("log in")); }
      const m = data && data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))
        : x.status === 413 ? "Too large for the server (or a proxy in front of it): 413." : `The server answered ${x.status}`;
      reject(Object.assign(new Error(m), { status: x.status }));
    };
    if (signal) signal.addEventListener("abort", () => x.abort());
    x.send(form);
  });
}
/** A loop's server calls (D701, D1095): `at(path)` its address -- `/apps/<name><path>`, its `owner` named when given,
    else the page's (`owned`); `api(path, opts)`, `request(path, opts)` as `api` and `request` there; `url(path)` a
    link to it. */
function loopApi(name, owner = null) {
  const at = (path = "") => `/apps/${enc(name)}${path}${owner ? `${path.includes("?") ? "&" : "?"}owner=${enc(owner)}` : ""}`;
  return { at, api: (path, opts) => api(at(path), opts), request: (path, opts) => request(at(path), opts), url: path => "/api" + owned(at(path)) };
}
async function api(path, opts = {}) {
  const r = await request(path, opts);
  return (r.headers.get("content-type") || "").includes("json") ? r.json() : r.text();
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
const enc = encodeURIComponent;
function appHref(user, app) {
  return user && me && user !== me.name ? `#/u/${enc(user)}/app/${enc(app)}` : `#/app/${enc(app)}`;
}
/** A problem document written as text, a loop made of it (D906): a name taken is said -- open that one, or choose another. */
async function createFromText(name, filename, text) {
  try {
    await api("/apps/from-text", { method: "POST", body: { name, filename, text } });
    return true;
  } catch (x) {
    if (x.status !== 409) throw x;
    const go = await dialog(`${name} exists`, html`<p>${`A loop named ${name} already exists. Creating does not replace it: `
      + "open it (and change it there, with a diff before saving), or choose another name."}</p>`,
      [["Choose another name", false, "primary"], ["Open it", true]]);
    if (go) location.hash = `#/app/${enc(name)}`;
    return false;
  }
}


// D754: a phone's width -- the tables stack their rows (app.js), the log wraps (live/Log.js)
const NARROW = window.matchMedia ? window.matchMedia("(max-width: 640px)") : { matches: false };

export { NARROW, money, plural, api, apiUpload, appHref, loopApi, request, bytes, askText, confirmDialog, createFromText, dialog, dur, enc, fmtTok, loopStream, offline, owned, toast, when, withOwner };
