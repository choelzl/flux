// Flux web (D683-D689): hash-routed pages over /api. A loop -- an application -- is running or not;
// a start resumes it from its record. Every node is built with h() -- text goes in as text, never as
// HTML -- so nothing a run prints can inject script.

import { codeBlock, codeEditor, langOf, proseBlock } from "./highlight.js";

const main = document.getElementById("main");
let me = null;
let cleanup = [];
let pageRefresh = null;                  // a page's own redraw, when a loop changes state (D688)

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
let pageOwner = null;
function owned(path) {
  if (!pageOwner || !/^\/apps\/[^/?]+/.test(path) || /[?&]owner=/.test(path)) return path;
  return path + (path.includes("?") ? "&" : "?") + "owner=" + encodeURIComponent(pageOwner);
}
async function withOwner(owner, fn) {
  const was = pageOwner; pageOwner = owner || null;
  try { return await fn(); } finally { pageOwner = was; }
}
async function api(path, { method = "GET", body, form } = {}) {
  path = owned(path);
  const opt = { method, headers: { "X-Flux": "1" }, credentials: "same-origin" };
  if (form) opt.body = form;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  let r;
  try { r = await fetch("/api" + path, opt); }
  catch (x) { offline(true); throw new Error("The server cannot be reached."); }
  offline(false);
  if (r.status === 401 && path !== "/login") {
    // D757: a session that ended (logged out elsewhere, expired) is said, not a silent jump to the login
    if (me && location.hash !== "#/login") toast("Your session ended: log in again.", "warn", { timeout: 8000 });
    me = null; location.hash = "#/login"; throw new Error("log in");
  }
  const type = r.headers.get("content-type") || "";
  const data = type.includes("json") ? await r.json() : await r.text();
  if (!r.ok) {
    if (data && data.detail) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
    // D700: an error page that is not the server's own (a proxy's, a crash): its status at least
    const said = typeof data === "string" ? data.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 160) : "";
    throw new Error(r.status === 413 ? "Too large for the server (or a proxy in front of it): 413." : `The server answered ${r.status} ${r.statusText}${said ? ": " + said : ""}`);
  }
  return data;
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
    es.addEventListener(event, (m) => { if (m.lastEventId) last = m.lastEventId; onData(JSON.parse(m.data)); });
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
function streamPill() {
  const el = h("span", { class: "pill stream", title: "The live stream" }, "connecting");
  return { el, set: (st) => { el.textContent = st === "live" ? "● live" : "reconnecting…"; el.className = `pill stream ${st === "live" ? "live" : "warn"}`; } };
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
let navSeq = 0;
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

// ================================================================ charts (D692)
const SVGNS = "http://www.w3.org/2000/svg";
function sv(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== "") el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}
/** The Flux mark (D696): a chip whose pins and body follow the theme, the orange trace its "f". */
function logo(size = 22) {
  const pins = [[78, 8, 16, 28], [120, 8, 16, 28], [162, 8, 16, 28], [120, 220, 16, 28], [162, 220, 16, 28], [8, 78, 28, 16], [8, 120, 28, 16], [8, 162, 28, 16],
    [220, 78, 28, 16], [220, 120, 28, 16], [220, 162, 28, 16]];
  return sv("svg", { viewBox: "0 0 256 256", width: size, height: size, class: "logo", "aria-hidden": "true" },
    sv("g", { class: "lg-g" }, pins.map(([x, y, w, hh]) => sv("rect", { x, y, width: w, height: hh, rx: 8 }))),
    sv("rect", { x: 78, y: 208, width: 16, height: 40, rx: 8, class: "lg-o" }),
    sv("rect", { x: 40, y: 40, width: 176, height: 176, rx: 32, class: "lg-gs" }),
    sv("path", { d: "M86 248V118A32 32 0 0 1 118 86H174", class: "lg-os" }),
    sv("path", { d: "M128 248V146A18 18 0 0 1 146 128H166", class: "lg-gs" }));
}
function bellIcon() {
  return sv("svg", { viewBox: "0 0 24 24", width: 18, height: 18, class: "icon", "aria-hidden": "true" },
    sv("path", { d: "M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z", fill: "none", stroke: "currentColor", "stroke-width": 1.7, "stroke-linejoin": "round" }),
    sv("path", { d: "M10 20.5a2 2 0 0 0 4 0", fill: "none", stroke: "currentColor", "stroke-width": 1.7, "stroke-linecap": "round" }));
}
const num4 = (v) => v == null ? "" : Math.abs(v) >= 1000 ? String(Math.round(v)) : Math.abs(v) < 0.01 && v !== 0 ? v.toExponential(2) : String(Number(v.toPrecision(4)));
/** One objective over time: every measurement (dots), the best so far (a step line), its limit
    (dashed), the passes (faint ticks). `rows`: [{when, stage, metrics}]. */
function bestChart(rows, obj, passes) {
  const W = 560, H = 190, L = 58, R = 12, T = 14, B = 26;
  const pts = rows.filter(r => r.metrics[obj.metric] != null && (!obj.stage || obj.stage === "deepest" || r.stage === obj.stage))
    .map(r => ({ t: r.when, v: Number(r.metrics[obj.metric]) })).sort((a, b) => a.t - b.t);
  if (!pts.length) return empty(`No ${obj.metric} measured${obj.stage ? " at " + obj.stage : ""} yet.`);
  const maxi = obj.direction !== "minimize";
  let best = null; const steps = [];
  for (const p of pts) { if (best === null || (maxi ? p.v > best : p.v < best)) best = p.v; steps.push({ t: p.t, v: best }); }
  const vals = pts.map(p => p.v).concat(obj.goal != null ? [obj.goal] : []);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) { lo -= Math.abs(lo) * 0.1 || 1; hi += Math.abs(hi) * 0.1 || 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  // x is the order of measurement: a loop measures in bursts, and time would pile them up
  const n = pts.length, t0 = pts[0].t, t1 = pts[n - 1].t;
  const xi = (i) => L + (W - L - R) * (n === 1 ? 0.5 : i / (n - 1)), y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  pts.forEach((p, i) => { p.x = xi(i); }); steps.forEach((p, i) => { p.x = xi(i); });
  const passX = (w) => { const k = pts.filter(p => p.t <= w).length; return k <= 0 || k >= n ? null : (xi(k - 1) + xi(k)) / 2; };
  const path = steps.map((p, i) => (i ? `H${p.x.toFixed(1)}V${y(p.v).toFixed(1)}` : `M${p.x.toFixed(1)},${y(p.v).toFixed(1)}`)).join("") + `H${xi(n - 1).toFixed(1)}`;
  const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": `${obj.metric}: best so far ${num4(best)}` },
    sv("line", { x1: L, x2: W - R, y1: H - B, y2: H - B, class: "axis" }),
    [lo + pad, (lo + hi) / 2, hi - pad].map(v => [sv("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }),
      sv("text", { x: L - 6, y: y(v) + 4, class: "tick", "text-anchor": "end" }, num4(v))]),
    (passes || []).map(p => passX(p.when)).filter(v => v != null).map(v => sv("line", { x1: v, x2: v, y1: T, y2: H - B, class: "pass" })),
    obj.goal != null ? [sv("line", { x1: L, x2: W - R, y1: y(obj.goal), y2: y(obj.goal), class: "limit" }),
      sv("text", { x: W - R, y: y(obj.goal) - 4, class: "tick limit-t", "text-anchor": "end" }, `${maxi ? "≥" : "≤"} ${num4(obj.goal)}`)] : "",
    pts.map(p => sv("circle", { cx: p.x, cy: y(p.v), r: 3, class: "pt" + (obj.goal != null && (maxi ? p.v < obj.goal : p.v > obj.goal) ? " miss" : "") },
      sv("title", {}, `${num4(p.v)} · ${new Date(p.t * 1000).toLocaleString()}`))),
    sv("path", { d: path, class: "best" }),
    sv("text", { x: (W + L - R) / 2, y: H - 8, class: "tick", "text-anchor": "middle" }, `${n} measurement(s), in order`),
    sv("text", { x: L, y: H - 8, class: "tick" }, new Date(t0 * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })),
    sv("text", { x: W - R, y: H - 8, class: "tick", "text-anchor": "end" }, new Date(t1 * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })));
  return h("figure", { class: "chart-box" }, h("figcaption", {}, h("strong", {}, obj.metric), h("span", { class: "muted" },
    ` ${maxi ? "higher" : "lower"} is better${obj.stage && obj.stage !== "deepest" ? " · at " + obj.stage : ""} · best `), h("strong", {}, num4(best))), g);
}

/** Which way a metric is better (D693): the objective's direction, else the name's plain sense. */
function directionOf(metric, objectives) {
  const o = (objectives || []).find(x => x.metric === metric);
  if (o && o.direction) return o.direction;
  return /area|power|energy|delay|latency|time|cells?|count|luts?|ffs?|error|loss|slack_viol|cost|size|bytes|cycles/i.test(metric) ? "minimize" : "maximize";
}
/** Two metrics against each other (D693): every design measured with both (accepted, failed, the
    decision), the non-dominated front joined, the limits dashed. A click opens the design. */
function paretoChart(designs, xm, ym, stage, objectives, onPick) {
  const W = 560, H = 300, L = 62, R = 14, T = 14, B = 34;
  const pts = designs.map(d => { const n = stage ? d.stages[stage] : d.numbers; return n && n[xm] != null && n[ym] != null ? { d, x: Number(n[xm]), y: Number(n[ym]) } : null; })
    .filter(Boolean);
  if (!pts.length) return empty(`No design has both ${xm} and ${ym}${stage ? " at " + stage : ""}.`);
  const dx = directionOf(xm, objectives), dy = directionOf(ym, objectives);
  const better = (a, b, dir) => dir === "minimize" ? a < b : a > b, notWorse = (a, b, dir) => dir === "minimize" ? a <= b : a >= b;
  const dominated = (p) => pts.some(o => o !== p && notWorse(o.x, p.x, dx) && notWorse(o.y, p.y, dy) && (better(o.x, p.x, dx) || better(o.y, p.y, dy)));
  const front = pts.filter(p => !dominated(p)).sort((a, b) => a.x - b.x);
  const goal = (m) => { const o = (objectives || []).find(x => x.metric === m); return o && o.goal != null ? Number(o.goal) : null; };
  const gx = goal(xm), gy = goal(ym);
  const span = (vals) => { let lo = Math.min(...vals), hi = Math.max(...vals); if (lo === hi) { lo -= Math.abs(lo) * 0.1 || 1; hi += Math.abs(hi) * 0.1 || 1; } const p = (hi - lo) * 0.08; return [lo - p, hi + p]; };
  const [x0, x1] = span(pts.map(p => p.x).concat(gx != null ? [gx] : [])), [y0, y1] = span(pts.map(p => p.y).concat(gy != null ? [gy] : []));
  const X = (v) => L + (W - L - R) * (v - x0) / (x1 - x0), Y = (v) => T + (H - T - B) * (1 - (v - y0) / (y1 - y0));
  const ticks = (a, b) => [a + (b - a) * 0.08 / 1.16, (a + b) / 2, b - (b - a) * 0.08 / 1.16];
  // the front as a staircase: between two designs on it, the corner neither beats
  const worseY = (a, b) => (dy === "minimize" ? Math.max(a, b) : Math.min(a, b));
  const line = front.map((p, i) => i ? `L${X(p.x).toFixed(1)},${Y(worseY(p.y, front[i - 1].y)).toFixed(1)}L${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`
    : `M${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
  const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart pareto", role: "img", "aria-label": `${ym} against ${xm}: ${front.length} design(s) on the front` },
    sv("line", { x1: L, x2: W - R, y1: H - B, y2: H - B, class: "axis" }), sv("line", { x1: L, x2: L, y1: T, y2: H - B, class: "axis" }),
    ticks(y0, y1).map(v => [sv("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: "grid" }), sv("text", { x: L - 6, y: Y(v) + 4, class: "tick", "text-anchor": "end" }, num4(v))]),
    ticks(x0, x1).map(v => [sv("line", { x1: X(v), x2: X(v), y1: T, y2: H - B, class: "grid" }), sv("text", { x: X(v), y: H - B + 14, class: "tick", "text-anchor": "middle" }, num4(v))]),
    gx != null ? sv("line", { x1: X(gx), x2: X(gx), y1: T, y2: H - B, class: "limit" }) : "",
    gy != null ? sv("line", { x1: L, x2: W - R, y1: Y(gy), y2: Y(gy), class: "limit" }) : "",
    front.length > 1 ? sv("path", { d: line, class: "front" }) : "",
    pts.sort((a, b) => (a.d.decision ? 1 : 0) - (b.d.decision ? 1 : 0)).map(p => {
      const on = front.includes(p);
      const c = sv(p.d.decision ? "rect" : "circle", p.d.decision
        ? { x: X(p.x) - 5, y: Y(p.y) - 5, width: 10, height: 10, transform: `rotate(45 ${X(p.x)} ${Y(p.y)})`, class: `pt ${p.d.verdict} decided` }
        : { cx: X(p.x), cy: Y(p.y), r: on ? 4.5 : 3.2, class: `pt ${p.d.verdict}${on ? " on-front" : ""}` },
        sv("title", {}, `${p.d.name}${p.d.part ? " (" + p.d.part + ")" : ""} · ${p.d.verdict}${on ? " · on the front" : ""}\n${xm} ${num4(p.x)} · ${ym} ${num4(p.y)}`));
      if (onPick) { c.style.cursor = "pointer"; c.addEventListener("click", () => onPick(p.d)); }
      return c;
    }),
    sv("text", { x: (W + L - R) / 2, y: H - 6, class: "tick", "text-anchor": "middle" }, `${xm} · ${dx === "minimize" ? "lower" : "higher"} is better`),
    sv("text", { x: 12, y: (H - B + T) / 2, class: "tick", "text-anchor": "middle", transform: `rotate(-90 12 ${(H - B + T) / 2})` }, `${ym} · ${dy === "minimize" ? "lower" : "higher"} is better`));
  return h("figure", { class: "chart-box" }, h("figcaption", {}, h("strong", {}, `${front.length} on the front`),
    h("span", { class: "muted" }, ` of ${pts.length} design(s)${stage ? " at " + stage : ", each at its deepest stage"} · `),
    h("span", { class: "legend" }, h("i", { class: "sw accepted" }), "accepted ", h("i", { class: "sw failed" }), "failed ", h("i", { class: "sw decided" }), "the decision")), g);
}

// ================================================================ notifications (D688, D689)
const bell = { list: [], seen: new Map(), unread: 0, primed: false, user: null };
// D702: the bell is each user's -- kept under their name, started afresh when another logs in here
const bellKey = () => `flux-notes:${bell.user}`;
function bellFor(user) {
  if (bell.user === user) return;
  bell.user = user; bell.seen = new Map(); bell.unread = 0; bell.primed = false;
  try { bell.list = user ? JSON.parse(localStorage.getItem(bellKey()) || "[]") : []; } catch (_) { bell.list = []; }
  try { localStorage.removeItem("flux-notes"); } catch (_) { /* the old, shared key */ }
}
function notify(text, kind, href) {
  bell.list.unshift({ text, kind, href, t: Date.now() / 1000 });
  bell.list = bell.list.slice(0, 30); bell.unread++;
  try { localStorage.setItem(bellKey(), JSON.stringify(bell.list)); } catch (_) {}
  toast(text, kind, { timeout: 9000, href });
  if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
    try { new Notification("Flux", { body: text, tag: href }); } catch (_) {}
  }
  drawBell();
}
/** Every 10 s: a loop that stopped (finished, failed, stopped) or whose agent asks. */
async function pollLoops() {
  if (!me) return;
  bellFor(me.name);
  let loops;
  try { loops = await api("/loops"); } catch (_) { return; }
  let changed = false;
  try { for (const n of await api("/notices")) notify(n.text, n.kind || "info", n.href || ""); } catch (_) { /* the next poll */ }
  for (const l of loops) {
    const id = `${l.owner || ""}/${l.app}`, label = l.owner ? `${l.owner}'s ${l.app}` : l.app;   // D702: shared loops too
    const was = bell.seen.get(id);
    const key = l.question ? `q:${l.question.asked}` : "";
    if (!was || was.running !== l.running) changed = true;
    if (bell.primed && was) {
      const href = appHref(l.owner, l.app);
      if (was.running && !l.running) {
        if (l.failed) notify(`${label} failed`, "bad", href);
        else if (l.stopped) notify(`${label} stopped`, "warn", href);
        else notify(`${label} finished its passes`, "ok", href);
      }
      if (key && key !== was.key) notify(`${label}: the agent asks a question`, "warn", href);
    }
    bell.seen.set(id, { running: l.running, key });
  }
  if (changed && bell.primed && pageRefresh) pageRefresh().catch(() => {});
  bell.primed = true;
}
setInterval(pollLoops, 10000);
const bellBtn = h("button", { class: "bell", title: "Notifications", "aria-label": "Notifications" });
const bellMenu = h("div", { class: "bell-menu", hidden: true });
function drawBell() {
  bellBtn.replaceChildren(bellIcon(), bell.unread ? h("span", { class: "badge" }, String(bell.unread)) : "");
  const canAsk = "Notification" in window && Notification.permission === "default";
  bellMenu.replaceChildren(
    h("div", { class: "bell-head" }, h("strong", {}, "Notifications"),
      canAsk ? h("button", { class: "link", onclick: async () => { await Notification.requestPermission(); drawBell(); } }, "Allow desktop notifications") : "",
      bell.list.length ? h("button", { class: "link", onclick: () => { bell.list = []; bell.unread = 0; localStorage.removeItem(bellKey()); drawBell(); } }, "Clear") : ""),
    ...(bell.list.length ? bell.list.map(n => h("a", { class: `bell-item ${n.kind}`, href: n.href || "#/", onclick: () => { bellMenu.hidden = true; } },
      h("span", {}, n.text), h("small", {}, ago(n.t)))) : [h("p", { class: "muted" }, "Nothing yet: you are told here when a loop stops, fails, or its agent asks.")]));
}
bellBtn.addEventListener("click", (e) => { e.stopPropagation(); bellMenu.hidden = !bellMenu.hidden; bell.unread = 0; drawBell(); });
document.addEventListener("click", (e) => { if (!bellMenu.hidden && !bellMenu.contains(e.target)) bellMenu.hidden = true; });

// ================================================================ pages
async function loginPage() {
  const show = pageShow();
  // D699: a phone's keyboard neither capitalises nor corrects a name
  const name = h("input", { autocomplete: "username", autocapitalize: "none", autocorrect: "off", spellcheck: "false", required: true });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const err = h("p", { class: "err" });
  const form = h("form", { class: "card login", onsubmit: async (e) => {
      e.preventDefault(); err.textContent = "";
      try { me = await api("/login", { method: "POST", body: { name: name.value, password: pw.value } }); location.hash = "#/"; route(); }
      catch (x) { err.textContent = x.message; }
    } },
    h("div", { class: "login-mark" }, logo(56)), h("h1", {}, "Flux"), h("p", { class: "sub" }, "Log in to your loops."),
    h("label", { class: "stack" }, "Name", name), h("label", { class: "stack" }, "Password", pw),
    h("button", { class: "primary wide", type: "submit" }, "Log in"), err);
  show(form);
  name.focus();
}

/** Start or stop a loop: the dialog for a start's options, a confirm for "now". */
async function startLoop(name, owner) {
  return withOwner(owner || pageOwner, () => startLoopOwned(name));
}
async function startLoopOwned(name) {
  // D693: the last start's options, and the check when the inputs changed since it was run
  const pre = await api(`/apps/${enc(name)}/preflight`).catch(() => ({}));
  if (pre.paused) { toast(`New starts are paused by an admin: ${pre.paused}`, "warn", { timeout: 8000 }); return false; }
  const last = pre.options || {};
  const passes = h("input", { type: "number", min: 1, value: last.passes || 1, style: "width:90px" });
  const forever = h("input", { type: "checkbox", checked: last.passes === null });
  const screen = h("input", { type: "checkbox", checked: !!last.screen_only });
  const net = pre.network || {};
  const allow = h("input", { placeholder: net.network === "allowlist" ? "more hosts for this start" : "empty: open network", style: "width:100%", value: (last.allow || []).join(", ") });
  if (net.network === "allowlist" && !net.users_add) allow.disabled = true;
  const netSaid = net.network === "allowlist" ? h("p", { class: "muted small" },
    net.allow ? `The server allows only: ${net.allow.join(", ") || "nothing"}` : "The network is limited to the hosts an admin allows",
    net.users_add ? "; hosts added here join them for this start." : ".") : "";
  passes.disabled = forever.checked;
  forever.addEventListener("change", () => { passes.disabled = forever.checked; });
  const checkBox = h("div", { class: "preflight" });
  // D787: a loop with several problems (problem.yaml, NAME.problem.yaml): the start says which
  const docs = (pre.documents || []).filter(d => d.ok);
  const pick = docs.length > 1 ? h("select", {}, ...docs.map(d => h("option", { value: d.path, selected: d.path === pre.document },
    `${d.path} — record ${d.record}`))) : null;
  const body = h("div", {},
    h("p", { class: "muted" }, "It resumes from its record: what was judged stays judged."),
    pick ? h("label", { class: "stack" }, `Which problem (${docs.length} in this loop)`, pick) : "",
    checkBox,
    h("div", { class: "row" }, h("label", { class: "stack" }, "Passes", passes), h("label", { class: "check" }, forever, "until I stop it")),
    h("label", { class: "check" }, screen, "screen only (skip the costly stages)"),
    h("label", { class: "stack", style: "margin-top:10px" }, "Network allowlist (hosts, domains, CIDRs)", allow), netSaid);
  const said = (ok, text, output) => checkBox.replaceChildren(h("div", { class: `callout ${ok === true ? "good" : ok === false ? "bad" : ""}` },
    h("strong", {}, text), output ? h("details", {}, h("summary", {}, "the check's output"), h("pre", { class: "log small" }, output)) : ""));
  const waiting = dialog(`Start ${name}`, body, [["Cancel", false], ["Start", true, "primary"]]);
  const dlg = [...document.querySelectorAll("dialog.dlg")].pop();
  const startBtn = dlg ? dlg.querySelector("button.primary") : null;
  const verdict = (ok, output) => {
    if (ok) said(true, pre.checked ? `The check passed on these inputs (${when(pre.when)}).` : "The check passes on these inputs.", "");
    else {
      said(false, "The check fails: the loop would not get far.", output);
      if (startBtn) { startBtn.textContent = "Start anyway"; startBtn.className = "danger solid"; }
    }
  };
  const runCheck = (why) => {
    said(null, why, "");
    if (startBtn) { startBtn.disabled = true; startBtn.textContent = "Start"; startBtn.className = "primary"; }
    const q = pick ? `?document=${enc(pick.value)}` : "";
    api(`/apps/${enc(name)}/check${q}`, { method: "POST" }).then(r => verdict(r.ok, r.output), x => said(false, "The check could not run: " + x.message, ""))
      .finally(() => { if (startBtn) startBtn.disabled = false; });
  };
  if (pre.checked && pre.ok !== null && (!pick || pick.value === pre.document)) verdict(pre.ok, pre.output);
  else runCheck(pre.changed ? "The inputs changed since the last start: checking them in the sandbox…" : "Checking the inputs in the sandbox…");
  if (pick) pick.addEventListener("change", () => runCheck(`Checking ${pick.value} in the sandbox…`));
  const go = await waiting;
  if (!go) return false;
  const r = await api(`/apps/${enc(name)}/start`, { method: "POST", body: {
    passes: forever.checked ? null : (Number(passes.value) || 1), screen_only: screen.checked,
    allow: allow.value.split(",").map(x => x.trim()).filter(Boolean), document: pick ? pick.value : null } });
  toast(r.ok, "ok");
  return true;
}
async function stopLoop(name, now, owner) {
  if (now && !await confirmDialog(`Stop ${name} now?`, "The pass ends at once; the record keeps what was judged. Starting it again resumes from there.", { ok: "Stop now", danger: true })) return;
  const r = await api(`/apps/${enc(name)}/stop${owner ? "?owner=" + enc(owner) : ""}`, { method: "POST", body: { now } });
  toast(r.ok, now ? "warn" : "info");
}
function lastSaid(st) {
  if (st.running) return ["running since ", ago(st.since), st.passes != null ? ` · pass ${st.passes + (st.at_rest ? 0 : 1)}` : ""];
  return st.last_active ? ["last active ", ago(st.last_active)] : ["never run"];
}

/** An upload's progress (D702): a dialog Escape does not close -- a Cancel stops the upload
    between batches and parts, and says what was already written. */
function progressDialog(title, said) {
  const ctl = new AbortController();
  const bar = h("progress", { max: 1, value: 0, class: "upload-bar" }), line = h("span", { class: "muted small" });
  const cancel = h("button", { type: "button", onclick: () => { ctl.abort(); cancel.disabled = true; cancel.textContent = "Stopping…"; } }, "Cancel");
  const d = h("dialog", { class: "dlg" }, h("h2", {}, title), h("p", {}, said), bar, line, h("div", { class: "dlg-actions" }, cancel));
  d.addEventListener("cancel", (e) => e.preventDefault());            // Escape: the upload goes on, the dialog stays
  document.body.append(d); d.showModal();
  return { signal: ctl.signal, set: (a, b) => { bar.value = b ? a / b : 1; line.textContent = ` ${bytes(a)} of ${bytes(b)}`; },
    close: () => { d.close(); if (toasts.parentNode === d) document.body.append(toasts); d.remove(); } };
}

/** Files to a loop (D700): in batches of at most 300 files and 40 MB, a file over 40 MB in
    parts of 32 MB, the document first when the loop is created. `entries`: [{file, path}];
    `onProgress(sentBytes, totalBytes)`. A folder dropped whole loses its top folder first. */
async function sendFiles(name, entries, { create = false, folder = "", onProgress = () => {}, signal = null } = {}) {
  const stopped = () => { if (signal && signal.aborted) throw new Error("the files sent before the cancel stay"); };
  const BATCH_FILES = 300, BATCH_BYTES = 40 * 2 ** 20, PART = 32 * 2 ** 20;
  // the paths as given: a dropped folder's name is gone already (dropZone), a chosen folder's is taken off by its caller
  let list = entries.map(e => ({ file: e.file, path: String(e.path || e.file.name).replace(/^\/+/, "") }));
  if (folder) list = list.map(e => ({ ...e, path: `${folder.replace(/^\/+|\/+$/g, "")}/${e.path}` }));
  const isDoc = (e) => !e.path.includes("/") && /(^|\.)(problem\.ya?ml|task\.(json|ya?ml))$/i.test(e.path);   // D786: problem.yaml
  list.sort((a, b) => (isDoc(b) ? 1 : 0) - (isDoc(a) ? 1 : 0));
  const total = list.reduce((n, e) => n + e.file.size, 0);
  let sent = 0, written = 0, made = !create;
  const small = list.filter(e => e.file.size <= BATCH_BYTES), big = list.filter(e => e.file.size > BATCH_BYTES);
  if (create && !small.some(isDoc)) throw new Error("No problem document (problem.yaml) at the top of the upload.");
  for (let i = 0; i < small.length;) {
    const batch = [];
    let bytes = 0;
    while (i < small.length && batch.length < BATCH_FILES && (bytes + small[i].file.size <= BATCH_BYTES || !batch.length)) { bytes += small[i].file.size; batch.push(small[i++]); }
    stopped();
    const form = new FormData();
    if (!made) form.append("name", name); else form.append("folder", "");
    for (const e of batch) form.append("files", e.file, e.path);
    const r = await api(made ? `/apps/${enc(name)}/files` : "/apps", { method: "POST", form });
    made = true; written += r.written ? r.written.length : batch.length;
    sent += bytes; onProgress(sent, total);
  }
  for (const e of big) {
    for (let off = 0; off < e.file.size; off += PART) {
      if (signal && signal.aborted && off) await api(`/apps/${enc(name)}/part?path=${enc(e.path)}`, { method: "DELETE" }).catch(() => {});   // its parts go
      stopped();
      const chunk = e.file.slice(off, off + PART);
      const final = off + PART >= e.file.size;
      const r = await fetch("/api" + owned(`/apps/${enc(name)}/part?path=${enc(e.path)}&offset=${off}&final=${final}`),
        { method: "PUT", body: chunk, headers: { "X-Flux": "1" }, credentials: "same-origin" }).catch(() => { offline(true); throw new Error("The server cannot be reached."); });
      if (!r.ok) { const d = await r.json().catch(() => null); throw new Error(d && d.detail ? `${e.path}: ${d.detail}` : `${e.path}: the server answered ${r.status}`); }
      sent += chunk.size; onProgress(sent, total);
    }
    written++;
  }
  return written;
}

/** A drop target for files and folders (D693): a folder keeps its paths ("rtl/top.sv"). `onFiles`
    gets [{file, path}] and the dropped folder's name, when one folder was dropped. */
function dropZone(label, onFiles) {
  const z = h("div", { class: "dropzone", tabindex: "-1" }, h("span", { class: "dz-ic" }, "⇪"), h("span", {}, label));
  const walk = (entry, prefix, out) => new Promise((resolve) => {
    if (!entry) return resolve();
    if (entry.isFile) { entry.file(f => { out.push({ file: f, path: prefix + f.name }); resolve(); }, () => resolve()); return; }
    const reader = entry.createReader(), all = [];
    const more = () => reader.readEntries(async (batch) => {        // a directory reads in batches
      if (batch.length) { all.push(...batch); more(); return; }
      for (const e of all) await walk(e, prefix + entry.name + "/", out);
      resolve();
    }, () => resolve());
    more();
  });
  let depth = 0;
  z.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; z.classList.add("over"); });
  z.addEventListener("dragover", (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
  z.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; z.classList.remove("over"); } });
  z.addEventListener("drop", async (e) => {
    e.preventDefault(); depth = 0; z.classList.remove("over");
    const items = [...(e.dataTransfer.items || [])].filter(i => i.kind === "file");
    const entries = items.map(i => i.webkitGetAsEntry ? i.webkitGetAsEntry() : null);
    const out = [];
    if (entries.length && entries.every(Boolean)) for (const en of entries) await walk(en, "", out);
    else for (const f of e.dataTransfer.files) out.push({ file: f, path: f.name });
    const dirs = entries.filter(en => en && en.isDirectory);
    // one folder dropped: its contents at the top, the folder's name for the loop
    const folder = entries.length === 1 && dirs.length === 1 ? dirs[0].name : null;
    if (folder) for (const o of out) o.path = o.path.slice(folder.length + 1);
    if (out.length) onFiles(out, folder);
  });
  return z;
}

function loopsTable(loops, { who = false } = {}) {
  if (!loops.length) return empty("No loop yet.");
  return h("table", { class: "list" },
    h("thead", {}, h("tr", {}, who ? h("th", {}, "User") : "", h("th", {}, "Loop"), h("th", {}, "State"), h("th", {}, "Activity"),
      h("th", { class: "num" }, "Designs"), h("th", {}, "Best"), h("th", {}, ""))),
    h("tbody", {}, loops.map(l => {
      const name = l.name || l.app, owner = l.owner && l.owner !== me.name ? l.owner : null, sm = l.summary || {};
      const href = owner ? `#/u/${enc(owner)}/app/${enc(name)}` : `#/app/${enc(name)}`;
      const acts = owner ? (l.perm === "watch" ? [h("span", { class: "pill" }, "watching")]
          : l.running ? [act("Stop", () => stopLoop(name, false, owner).then(() => pageRefresh && pageRefresh()), { cls: "small" })]
          : l.perm === "edit" ? [act("Start", async () => { if (await startLoop(name, owner)) location.hash = href; }, { cls: "small primary" })] : [])
        : l.running ? [act("Stop", () => stopLoop(name, false).then(() => pageRefresh && pageRefresh()), { cls: "small" })]
        : [act("Start", async () => { if (await startLoop(name)) location.hash = href; }, { cls: "small primary" }),
           h("a", { class: "btn small", href: `#/app/${enc(name)}/settings/problem` }, "Configure")];
      return h("tr", { class: "clickable", onclick: (e) => { if (!e.target.closest("a, button")) location.hash = href; } },
        who ? h("td", {}, l.owner) : "",
        h("td", {}, h("a", { href, class: "strong" }, name)),
        h("td", {}, statePill(l), l.question ? h("span", { class: "pill warn" }, "asks") : ""),
        h("td", { class: "muted" }, lastSaid(l)),
        h("td", { class: "num mono" }, sm.designs ? [String(sm.accepted), h("span", { class: "muted" }, ` / ${sm.designs}`)] : h("span", { class: "muted" }, "—")),
        h("td", { class: "mono" }, sm.best ? h("span", { class: sm.best.meets === false ? "misses" : sm.best.meets === true ? "meets" : "",
          title: `the decision, ${sm.best.design}` }, h("span", { class: "muted" }, sm.best.metric + " "), num4(sm.best.value),
          sm.best.meets === true ? " ✓" : sm.best.meets === false ? " ✗" : "") : ""),
        h("td", { class: "right" }, h("div", { class: "actions end" }, acts)));
    })));
}

const loopView = { q: "", state: "all", sort: "activity" };     // the list's search, filter and order (D693)
function loopsBrowser(loops, { who = false } = {}) {
  const box = h("div", {});
  const stateOf = (l) => l.running ? "running" : l.failed ? "failed" : "idle";
  const bestOf = (l) => (l.summary && l.summary.best) ? l.summary.best.value : null;
  function draw() {
    const q = loopView.q.trim().toLowerCase();
    let list = loops.filter(l => (loopView.state === "all" || stateOf(l) === loopView.state)
      && (!q || [l.name, l.document, l.owner].some(x => String(x || "").toLowerCase().includes(q))));
    if (loopView.sort === "name") list = list.slice().sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    else if (loopView.sort === "designs") list = list.slice().sort((a, b) => ((b.summary || {}).accepted || 0) - ((a.summary || {}).accepted || 0));
    else if (loopView.sort === "best") list = list.slice().sort((a, b) => (bestOf(a) == null) - (bestOf(b) == null) || a.name.localeCompare(b.name));
    const count = (k) => loops.filter(l => k === "all" || stateOf(l) === k).length;
    bar.replaceChildren(search,
      h("div", { class: "chips" }, ["all", "running", "idle", "failed"].map(k => h("button", { class: `chip${loopView.state === k ? " on" : ""}`,
        onclick: () => { loopView.state = k; draw(); } }, `${k[0].toUpperCase() + k.slice(1)} ${count(k)}`))),
      h("label", { class: "sort" }, "Order ", h("select", { onchange: (e) => { loopView.sort = e.target.value; draw(); } },
        [["activity", "latest activity"], ["name", "name"], ["designs", "accepted designs"], ["best", "has a decision"]].map(([v, t]) => h("option", { value: v, selected: loopView.sort === v }, t)))));
    table.replaceChildren(loops.length && !list.length ? empty("No loop matches.") : loopsTable(list, { who }));
  }
  const search = h("input", { type: "search", placeholder: "Search loops", value: loopView.q, class: "search",
    oninput: (e) => { loopView.q = e.target.value; draw(); } });
  const bar = h("div", { class: "list-bar" }), table = h("div", {});
  draw();
  box.append(loops.length ? bar : "", table);
  return box;
}

/** Upload a loop (D696, D704: a tab of New loop): a folder or files dropped or chosen, a `.zip`,
    under a name. */
function uploadForm() {
  const name = h("input", { placeholder: "my_adder", pattern: "[A-Za-z0-9][A-Za-z0-9_-]*", style: "width:100%", id: "up-name" });
  const files = h("input", { type: "file", multiple: true });
  const folder = h("input", { type: "file", webkitdirectory: true, multiple: true });
  let dropped = [];
  const said = h("div", { class: "muted small" });
  const dz = dropZone("Drop the loop's folder, its files or a .zip here", (got, dir) => {
    dropped = got;
    if (dir && !name.value) name.value = dir.replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^[^A-Za-z0-9]+/, "");
    said.replaceChildren(`${got.length} file(s) ready`, dir ? ` from ${dir}/` : "");
  });
  const go = act("Upload", async () => {
    // a chosen folder names every file under its own name: that name goes (D702: once, here)
    const picked = [...folder.files].map(f => ({ file: f, path: f.webkitRelativePath || f.name }));
    const top = new Set(picked.map(e => e.path.split("/")[0]));
    const fromFolder = top.size === 1 && picked.every(e => e.path.includes("/")) ? picked.map(e => ({ ...e, path: e.path.split("/").slice(1).join("/") })) : picked;
    const chosen = [...[...files.files].map(f => ({ file: f, path: f.name })), ...fromFolder, ...dropped];
    if (!chosen.length) { toast("Drop or choose the loop's files first.", "warn"); return; }
    if (!name.value.trim()) { toast("Name the loop first.", "warn"); name.focus(); return; }
    const pd = progressDialog("Uploading", `${chosen.length} file(s) to ${name.value.trim()}`);
    try {
      const n = await sendFiles(name.value.trim(), chosen, { create: true, onProgress: pd.set, signal: pd.signal });
      pd.close();
      toast(`${name.value} uploaded: ${n} file(s)`, "ok"); location.hash = `#/app/${enc(name.value.trim())}`;
    } catch (x) {
      pd.close();
      toast(pd.signal.aborted ? `The upload was cancelled: ${x.message}.` : `The upload failed: ${x.message}`, pd.signal.aborted ? "warn" : "bad", { timeout: 12000 });
    }
  }, { cls: "primary" });
  return card(null, h("div", { class: "upload" }, h("p", { class: "muted" }, "A loop you already have: its problem.yaml and the files it runs, as a folder, files or a .zip. The loop's name is the problem's id."),
    h("label", { class: "stack" }, "Name", name), dz, said,
    h("div", { class: "row" }, h("label", { class: "stack" }, "or choose files / a .zip", files), h("label", { class: "stack" }, "or a folder", folder)),
    h("div", { class: "form-actions" }, go)));
}

/** An answer's Markdown (D705), built node by node -- never HTML: headings, lists, tables, code
    blocks, quotes, paragraphs with **bold**, *italics* and `code`. */
function markdown(text) {
  const inline = (t) => {
    const out = [];
    const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g;
    let at = 0, m;
    while ((m = re.exec(t))) {
      if (m.index > at) out.push(t.slice(at, m.index));
      const x = m[0];
      out.push(x.startsWith("`") ? h("code", {}, x.slice(1, -1)) : x.startsWith("**") ? h("strong", {}, x.slice(2, -2)) : h("em", {}, x.slice(1, -1)));
      at = m.index + x.length;
    }
    if (at < t.length) out.push(t.slice(at));
    return out;
  };
  const lines = String(text || "").split("\n"), out = [];
  for (let i = 0; i < lines.length;) {
    const l = lines[i];
    if (/^```/.test(l)) {
      const lang = l.slice(3).trim(), body = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]); i++) body.push(lines[i]);
      i++; out.push(codeBlock(body.join("\n"), lang === "systemverilog" || lang === "verilog" ? "sv" : lang, "val code"));
    } else if (/^#{1,6}\s/.test(l)) {
      const n = l.match(/^#+/)[0].length; out.push(h(n <= 2 ? "h3" : "h4", {}, inline(l.replace(/^#+\s*/, "")))); i++;
    } else if (/^\s*\|.*\|\s*$/.test(l)) {
      const rows = [];
      for (; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++) rows.push(lines[i].trim().slice(1, -1).split("|").map(c => c.trim()));
      const body = rows.filter(r => !r.every(c => /^:?-{2,}:?$/.test(c)));
      out.push(h("div", { class: "scroll-x" }, h("table", { class: "list compact md" }, h("thead", {}, h("tr", {}, body[0].map(c => h("th", {}, inline(c))))),
        h("tbody", {}, body.slice(1).map(r => h("tr", {}, r.map(c => h("td", {}, inline(c)))))))));
    } else if (/^\s*([-*+]|\d+\.)\s+/.test(l)) {
      const ordered = /^\s*\d+\./.test(l), items = [];
      for (; i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i]); i++) items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ""));
      out.push(h(ordered ? "ol" : "ul", {}, items.map(x => h("li", {}, inline(x)))));
    } else if (/^>\s?/.test(l)) {
      const q = [];
      for (; i < lines.length && /^>\s?/.test(lines[i]); i++) q.push(lines[i].replace(/^>\s?/, ""));
      out.push(h("blockquote", {}, inline(q.join(" "))));
    } else if (!l.trim()) { i++; } else {
      const para = [];
      for (; i < lines.length && lines[i].trim() && !/^(```|#{1,6}\s|\s*\||\s*([-*+]|\d+\.)\s|>)/.test(lines[i]); i++) para.push(lines[i]);
      out.push(h("p", {}, inline(para.join(" "))));
    }
  }
  return h("div", { class: "md" }, out);
}

/** An agent's conversation (D712): what it did, in order -- its words as text, its thinking and
    each tool call (input and output) folded, opened on a click and kept open across the redraws.
    `offset`: how many earlier steps are not in `steps` (a live row sends its latest). */
const convOpen = new Set();
function conversation(steps, { key = "", offset = 0, live = false, scroll = false } = {}) {
  const items = [];
  if (offset > 0) items.push(h("p", { class: "muted small" }, `${offset} earlier step${offset === 1 ? "" : "s"} not shown here: the Agent turns tab has the whole turn once it ends.`));
  const preview = (t, n = 140) => { const x = String(t || "").replace(/\s+/g, " ").trim(); return x.length > n ? x.slice(0, n) + "…" : x; };
  steps.forEach((st, i) => {
    const id = `${key}:${offset + i}`, last = i === steps.length - 1;
    if (st.k === "text") { if (String(st.text || "").trim()) items.push(h("div", { class: "cv-text" }, markdown(String(st.text).trim()))); return; }
    const det = h("details", { class: `cv-step cv-${st.k}${st.error ? " bad" : ""}` });
    if (convOpen.has(id)) det.open = true;
    det.addEventListener("toggle", () => { if (det.open) convOpen.add(id); else convOpen.delete(id); });
    if (st.k === "think") {
      const t = String(st.text || "").trim();
      det.append(h("summary", {}, h("span", { class: "cv-kind" }, "Thinking"),
        h("span", { class: "cv-sum muted" }, t ? preview(t) : `redacted, about ${Number(st.redacted || 0).toLocaleString()} tokens`),
        live && last ? h("span", { class: "cv-live" }, "…") : ""),
        t ? h("pre", { class: "cv-body cv-thought" }, t) : "");
    } else {
      const state = st.out != null ? (st.error ? "failed" : "") : live && last ? "running" : "";
      det.append(h("summary", {}, h("span", { class: "cv-kind" }, st.name || "tool"),
        h("code", { class: "cv-sum" }, preview(String(st.call || "").replace(/^[^:]*:\s*/, ""), 160)),
        state ? h("span", { class: `cv-state ${state === "failed" ? "bad" : "live"}` }, state) : ""),
        ...Object.entries(st.input && typeof st.input === "object" ? st.input : st.input ? { input: String(st.input) } : {}).map(([k, v]) => {
          const t = String(v ?? "");
          return h("div", { class: "cv-io" }, h("small", {}, k || "input"),
            t.includes("\n") || t.length > 90 ? h("pre", { class: "cv-body" }, t) : h("div", {}, h("code", { class: "cv-arg" }, t)));
        }),
        st.out != null ? h("div", { class: "cv-io" }, h("small", {}, st.error ? "error" : "output"),
          h("pre", { class: `cv-body${st.error ? " err" : ""}` }, String(st.out).trim() || "(nothing)")) : "");
    }
    items.push(det);
  });
  return h("div", { class: `cv${scroll ? " cv-live-box" : ""}`, "data-k": `cv-${key}` }, items.length ? items : h("p", { class: "muted" }, "Nothing yet."));
}

/** The agents that can write a problem here (D704), as a select; the unavailable say why. */
async function agentSelect(id) {
  const list = await api("/agents").catch(() => []);
  // D705: the agent by default -- the user's (Account), else the admin's (Models) -- else the first that works here
  const first = list.find(a => a.available && a.default) || list.find(a => a.available);
  return h("select", { id }, list.map(a => h("option", { value: a.id, disabled: !a.available, selected: first && a.id === first.id },
    a.label + (a.available ? "" : ` (${a.why})`))));
}
/** Files for an agent to read (D704): dropped or chosen, listed, removable. */
function attachBox() {
  let got = [];
  const listEl = h("ul", { class: "files flist" });
  const draw = () => listEl.replaceChildren(...got.map((g, i) => h("li", {}, h("span", { class: "mono" }, g.path), h("small", { class: "muted" }, bytes(g.file.size)),
    h("button", { class: "link danger-link", type: "button", onclick: () => { got.splice(i, 1); draw(); } }, "×"))));
  const pickIn = h("input", { type: "file", multiple: true, onchange: (e) => { got.push(...[...e.target.files].map(f => ({ file: f, path: f.name }))); e.target.value = ""; draw(); } });
  const dz = dropZone("Drop a spec, a reference model, tests, papers: the agent reads them", (g) => { got.push(...g); draw(); });
  return { el: h("div", { class: "attach" }, dz, h("label", { class: "stack" }, "or choose files", pickIn), listEl),
    form(fd) { for (const g of got) fd.append("files", g.file, g.path); }, count: () => got.length };
}
/** The agent writing a loop's problem (D704): its state, its log's tail, Stop; a revision's diff. */
function authoringCard(name, st, { onStop } = {}) {
  if (!st || !st.ever) return "";
  const running = st.running;
  const head = running ? h("span", { class: "pill live" }, h("i", { class: "dot" }), "writing")
    : st.ok ? h("span", { class: "pill ok" }, "done") : h("span", { class: "pill bad" }, "failed");
  const diff = !running && st.revise && st.before && st.after && st.before !== st.after ? h("details", { class: "blk", open: true },
    h("summary", {}, `What it changed in ${st.revise}`), diffView(lineDiff(st.before, st.after))) : "";
  return card(`The agent ${st.revise ? "revising" : "writing"} the problem`, [
    h("div", { class: "row" }, head, h("span", { class: "muted" }, `${st.author} · by ${st.by} · started `, ago(st.started),
      st.ended ? [" · ended ", ago(st.ended)] : "")),
    st.prompt ? h("details", {}, h("summary", { class: "muted" }, "What it was asked"), h("pre", { class: "val small" }, st.prompt)) : "",
    h("pre", { class: "log small author-log" }, (st.log || []).join("\n") || "…"),
    diff,
    !running && !st.ok ? h("p", { class: "callout bad" }, "The agent did not leave a document that passes its checks: read its log above, then try again or write the document yourself.") : "",
    running && onStop ? h("div", { class: "form-actions" }, act("Stop the agent", onStop, { cls: "danger" })) : ""], { cls: "authoring" });
}

async function appsPage() {
  const show = pageShow();
  const [loops, shared] = await Promise.all([api("/apps"), api("/shared").catch(() => [])]);
  const box = h("div", {}, loopsBrowser(loops));
  const sharedBox = h("div", {}, shared.length ? loopsTable(shared, { who: true }) : "");
  show(
    head("Loops", "Each loop is a problem document and its files; it runs or it does not, and a start resumes it.",
      h("a", { class: "btn primary", href: "#/configure" }, "New loop")),
    card(null, box), shared.length ? card("Shared with me", sharedBox) : "");
  pageRefresh = async () => {
    if (!box.contains(document.activeElement)) box.replaceChildren(loopsBrowser(await api("/apps")));
    const sh = await api("/shared").catch(() => []);
    sharedBox.replaceChildren(sh.length ? loopsTable(sh, { who: true }) : "");
  };
}

async function newPage() {
  const show = pageShow();
  const name = h("input", { placeholder: "application name", required: true });
  const file = h("input", { value: "problem.yaml", size: 28 });
  const ed = codeEditor("", "yaml");
  ed.textarea.placeholder = "id: my_problem\nstatement: >-\n  What the design must do.\n...";
  const text = ed.textarea;
  show(head("Write a problem document", "Paste or write the YAML; upload its other files afterwards on the application's page.",
      h("a", { class: "btn", href: "#/configure" }, "Use the configurator instead")),
    card(null, [h("div", { class: "row" }, h("label", { class: "stack" }, "Application", name), h("label", { class: "stack" }, "File", file)), ed.el,
      h("div", { class: "form-actions" }, act("Create", async () => {
        await api("/apps/from-text", { method: "POST", body: { name: name.value, filename: file.value, text: text.value } });
        toast(`${name.value} created`, "ok"); location.hash = `#/app/${enc(name.value)}`;
      }, { cls: "primary" }))]));
}

/** What the model and agent turns cost (D694): in all, and per agent or model. */
function usageCard(u) {
  const t = u.total;
  if (!t.turns) return "";
  const fig = (label, value, sub) => h("div", { class: "stat" }, h("small", {}, label), h("div", { class: "big" }, value), sub ? h("div", { class: "muted" }, sub) : "");
  return card("What the turns cost", [
    h("div", { class: "stats five" },
      fig("Turns", String(t.turns), t.errors ? `${t.errors} failed` : ""),
      fig("Time", dur(t.seconds) || "0s", t.turns ? `${dur(t.seconds / t.turns)} a turn` : ""),
      fig("Tokens in", t.counted ? fmtTok(t.tokens_in) : "—", t.tokens_cached ? `${fmtTok(t.tokens_cached)} from the cache` : ""),
      fig("Tokens out", t.counted ? fmtTok(t.tokens_out) : "—", t.counted < t.turns ? `${t.turns - t.counted} turn(s) without a count (recorded since D694)` : ""),
      fig("Cost", t.cost_usd ? `$${t.cost_usd.toFixed(2)}` : "—", t.cost_usd ? "as the agents priced it" : "no agent priced its turns")),
    u.by.length > 1 ? h("table", { class: "list compact" }, h("thead", {}, h("tr", {}, ["Who", "Kind", "Turns", "Time", "Tokens in", "Tokens out", "Cost"].map((x, i) => h("th", { class: i > 1 ? "num" : "" }, x)))),
      h("tbody", {}, u.by.map(b => h("tr", {}, h("td", { class: "strong" }, b.who), h("td", { class: "muted" }, b.kind),
        h("td", { class: "num mono" }, String(b.turns)), h("td", { class: "num mono" }, dur(b.seconds)),
        h("td", { class: "num mono" }, b.counted ? fmtTok(b.tokens_in) : "—"), h("td", { class: "num mono" }, b.counted ? fmtTok(b.tokens_out) : "—"),
        h("td", { class: "num mono" }, b.cost_usd ? `$${b.cost_usd.toFixed(2)}` : "—"))))) : ""]);
}

/** Who else sees or edits a loop (D701): the owner shares it with a user to watch (its runs and
    outputs) or to edit (change and run it too); everyone else with it sees the list. */
async function sharingCard(name, isOwner) {
  const sh = await api(`/apps/${enc(name)}/shares`).catch(() => null);
  if (!sh) return "";
  const set = async (user, perm) => { await api(`/apps/${enc(name)}/shares`, { method: "PUT", body: { user, perm } }); toast(perm ? `Shared with ${user}: ${perm}` : `No longer shared with ${user}`, "ok"); route(); };
  // D723: one grid -- who, what they may do, the action -- the row to add in the same columns
  const CAN = { watch: "Can watch", edit: "Can edit" };
  const access = (attrs, cur) => h("select", attrs, Object.entries(CAN).map(([p, label]) => h("option", { value: p, selected: cur === p }, label)));
  const person = (u) => h("div", { class: "share-who" }, h("span", { class: "share-av", "aria-hidden": "true" }, u.slice(0, 1).toUpperCase()), h("span", { class: "strong" }, u));
  const rows = sh.shares.flatMap(x => [person(x.user),
    isOwner ? access({ "aria-label": `What ${x.user} may do`, onchange: (e) => set(x.user, e.target.value) }, x.perm) : h("span", { class: "pill" }, CAN[x.perm] || x.perm),
    isOwner ? h("button", { type: "button", class: "small", onclick: () => set(x.user, null) }, "Remove") : h("span", {})]);
  const none = h("p", { class: "muted share-none" }, isOwner ? "Only you: share it with someone below." : "Shared with nobody else.");
  if (!isOwner) return card("Sharing", sh.shares.length ? h("div", { class: "share-grid" }, rows) : none);
  const free = sh.users.filter(u => !sh.shares.some(x => x.user === u));
  const who = h("select", { id: "share-user", "aria-label": "Share with" }, h("option", { value: "" }, free.length ? "Choose a user…" : "No other user"), free.map(u => h("option", { value: u }, u)));
  const how = access({ id: "share-perm", "aria-label": "What they may do" }, "watch");
  if (!free.length) who.disabled = how.disabled = true;
  const add = act("Share", async () => { if (!who.value) { toast("Choose a user.", "warn"); return; } await set(who.value, how.value); }, { cls: "primary small" });
  if (!free.length) add.disabled = true;
  return card("Sharing", [
    sh.shares.length ? "" : none,
    h("div", { class: "share-grid" }, rows, h("div", { class: "share-add-sep" }), who, how, add),
    h("p", { class: "muted small share-note" }, h("strong", {}, "Watch"), ": its runs, log, results, turns and files. ",
      h("strong", {}, "Edit"), ": also its files and settings, and starting and stopping it; their runs use your model settings and keys, and the log says who started each.")]);
}

/** Environment variables (D697): a table, and for whoever may change them a row to add one. */
/** D808: a bin at a card's top right that removes it once confirmed. */
function binButton(what, title, said, remove) {
  return h("button", { type: "button", class: "bin", title: `Remove this ${what}`, "aria-label": `Remove this ${what}`,
    onclick: async () => { if (await confirmDialog(title, said, { ok: "Remove", danger: true })) await remove(); } },
    sv("svg", { viewBox: "0 0 16 16", width: 15, height: 15, "aria-hidden": "true" },
      sv("path", { d: "M2.5 4h11M6 4V2.5h4V4M4 4l.7 9.5h6.6L12 4M6.6 6.5v5M9.4 6.5v5", fill: "none", stroke: "currentColor",
        "stroke-width": 1.3, "stroke-linecap": "round", "stroke-linejoin": "round" })));
}
function envTable(rows, shadowed = new Set()) {
  return h("table", { class: "list compact env" }, h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Value"), h("th", {}, "From"))),
    h("tbody", {}, rows.map(x => h("tr", { class: shadowed.has(x.name) ? "shadowed" : "" }, h("td", { class: "mono" }, x.name),
      h("td", { class: "mono" }, x.secret ? h("span", { class: "muted" }, "secret · set") : x.value), h("td", { class: "muted" }, x.from, shadowed.has(x.name) ? " · overridden" : "")))));
}
function envEditor(rows, save, scope) {
  const nameIn = h("input", { placeholder: "NAME", class: "mono", id: `env-${scope}-name`, style: "width:180px", autocomplete: "off" });
  const valIn = h("input", { placeholder: "value", class: "mono", id: `env-${scope}-value`, style: "flex:1;min-width:160px", autocomplete: "off" });
  const secret = h("input", { type: "checkbox", id: `env-${scope}-secret` });
  secret.addEventListener("change", () => { valIn.type = secret.checked ? "password" : "text"; });
  const list = rows.length ? h("table", { class: "list compact env" }, h("tbody", {}, rows.map(x => h("tr", {}, h("td", { class: "mono" }, x.name),
      h("td", { class: "mono" }, x.secret ? h("span", { class: "muted" }, "secret · set") : x.value),
      h("td", { class: "right" }, save ? act("Remove", async () => { await save({ name: x.name, value: null }); toast(`${x.name} removed`, "ok"); }, { cls: "small" }) : "")))))
    : h("p", { class: "muted" }, "None yet.");
  if (!save) return list;
  return h("div", {}, list, h("div", { class: "row env-add" }, nameIn, valIn, h("label", { class: "check" }, secret, "secret"),
    act("Add", async () => {
      if (!nameIn.value.trim()) { toast("Name the variable.", "warn"); return; }
      const n = nameIn.value.trim();
      await save({ name: n, value: valIn.value, secret: secret.checked });
      toast(`${n} saved: from the next start`, "ok");                 // D758: an action says it happened
    }, { cls: "primary small" })));
}
/** A loop's advanced settings (D697): only an admin changes them; everyone sees them. */
function advancedCard(e, save, saveLabel = "Save") {
  const a = e.advanced || {};
  const said = [a.sandbox === false ? "runs on the host, without the sandbox" : "runs in the sandbox",
    ...["memory", "cpus", "pids", "tmp_size"].filter(k => a[k] != null).map(k => `${e.advanced_said[k].split(" (")[0]}: ${a[k]}`),
    ...(a.allow && a.allow.length ? [`may reach ${a.allow.join(", ")}`] : []),
    a.parallel ? "parallel work allowed" : "one at a time"].join(" · ");
  if (!e.can_advance) return card("Advanced", h("p", { class: "muted" }, said, ". An admin sets these."));
  const sb = h("input", { type: "checkbox", checked: a.sandbox !== false, id: "adv-sandbox" });
  const f = (k, ph) => h("input", { id: `adv-${k}`, value: a[k] ?? "", placeholder: ph, style: "width:120px" });
  const mem = f("memory", "no limit"), cpus = f("cpus", "no limit"), pids = f("pids", "4096"), tmp = f("tmp_size", "no limit");
  const par = h("input", { type: "checkbox", checked: !!a.parallel, id: "adv-parallel" });
  const hosts = h("textarea", { id: "adv-allow", rows: 2, class: "mono", placeholder: "huggingface.co\n10.1.2.0/24", value: (a.allow || []).join("\n") });
  return card("Advanced (admins)", [h("p", { class: "muted" }, "Apply from the loop's next start, whoever starts it.",
      e.sandboxed_server ? "" : " This server runs without the sandbox (--no-sandbox): the limits do nothing."),
    h("label", { class: "check" }, sb, "Run in the sandbox (off: on the host, with this machine's files and network: only for code you trust)"),
    h("div", { class: "row" }, h("label", { class: "stack" }, "Memory", mem), h("label", { class: "stack" }, "CPUs", cpus),
      h("label", { class: "stack" }, "Processes", pids), h("label", { class: "stack" }, "Scratch /tmp", tmp)),
    h("label", { class: "check" }, par, "Allow parallel work: tool runs and parts at once, as many as the document asks (budget.workers, parallel_parts); off: one at a time"),
    h("label", { class: "stack" }, "Hosts this loop may reach as well, under a network allowlist (one per line)", hosts),
    h("div", { class: "form-actions" }, act(saveLabel, async () => {
      if (!sb.checked && !await confirmDialog("Run this loop on the host?", "Its document's commands and its agents run on this machine, outside the sandbox, as the server's user.", { ok: "Run on the host", danger: true })) return;
      await save({ sandbox: sb.checked, memory: mem.value.trim() || null, cpus: cpus.value.trim() || null,
        pids: pids.value.trim() ? Number(pids.value) : null, tmp_size: tmp.value.trim() || null, parallel: par.checked,
        allow: hosts.value.split(/[\n,]/).map(x => x.trim()).filter(Boolean) });
    }, { cls: "primary" }))]);
}

async function loopPage(name, owner, path = "") {
  const show = pageShow();
  const qs = owner ? `?owner=${enc(owner)}` : "";
  const q = owner ? `&owner=${enc(owner)}` : "";
  const base = `/api/apps/${enc(name)}`;
  const info = await api(`/apps/${enc(name)}${qs}`);
  if (show.stale()) return;                         // D719: the user went elsewhere while it loaded
  // D701: "owner", "edit" (shared to change and run it), "watch" (shared to see it), "admin"
  const perm = info.perm || (info.mine ? "owner" : "admin");
  const mine = perm === "owner" || perm === "edit" || perm === "admin", isOwner = perm === "owner";   // D812: an admin edits anyone's
  let st = info.state;
  const header = h("div", {}), banner = h("div", {}), body = h("div", {});
  // D713: six tabs; the log and the timeline under Live, the workbench under Files, the problem
  // (the configurator, direct edit, an agent) under Settings, Delete at Settings' end; Ask a panel
  // that opens over any tab. The old addresses lead to their new places.
  const tabs = ["Overview", "Live", "Results", "Agents", "Files", "Settings"];
  const SLUG = { Overview: "", Live: "live", Results: "results", Agents: "agents", Files: "files", Settings: "settings" };
  const TAB_OF = Object.fromEntries(Object.entries(SLUG).map(([t, k]) => [k, t]));
  const ALIAS = { log: "live/log", timeline: "live/timeline", "agent-turns": "agents", workbench: "files/workbench", configure: "settings/problem" };
  let parts = String(path || "").split("/").filter(Boolean);
  if (ALIAS[parts[0]]) parts = [...ALIAS[parts[0]].split("/"), ...parts.slice(1)];
  let askOpen = parts[0] === "ask";
  if (askOpen) parts = [];
  let tab = TAB_OF[parts[0] || ""] || "Overview", sub = parts[1] || "", mode = parts[2] || "";
  const SUBS = { Live: [["", "Tasks"], ["log", "Log"], ["timeline", "Timeline"]], Files: [["", "Loop files"], ["workbench", "Workbench"]],
                 Settings: [["problem", "Problem"], ["loop", "Variables and sharing"]] };
  const subsOf = (t) => (SUBS[t] || []).filter(([k]) => !(t === "Settings" && k === "problem" && !mine));
  const curSub = () => { const o = subsOf(tab); return o.some(([k]) => k === sub) ? sub : (o[0] ? o[0][0] : ""); };
  function setUrl() {
    const segs = [SLUG[tab], tab === "Overview" ? "" : (curSub() === (subsOf(tab)[0] || [""])[0] && !mode ? "" : curSub()), mode].filter(Boolean);
    history.replaceState(null, "", `#/${owner ? `u/${enc(owner)}/` : ""}app/${enc(name)}${segs.length ? "/" + segs.join("/") : ""}`);
  }
  const tabBar = h("div", { class: "tabs", role: "tablist" }), subHolder = h("div", { class: "subrow" });
  let question = st.question || null;
  const log = logView(base, qs);
  const live = liveTree(base, qs, (qq) => { question = qq; drawBanner(); });
  cleanup.push(() => { live.close(); log.close(); });

  const crumbBar = h("div", {});
  const leaveBtn = () => act("Leave", async () => {           // D702: a shared loop, left by its guest
    if (!await confirmDialog(`Leave ${info.owner}'s ${name}?`, `It goes from your list; ${info.owner} is told and may share it again.`, { ok: "Leave" })) return;
    toast((await api(`/apps/${enc(name)}/shares/me?owner=${enc(info.owner)}`, { method: "DELETE" })).ok, "ok"); location.hash = "#/";
  });
  function drawCrumbs() {
    crumbBar.replaceChildren(crumbs(["Loops", "#/"], owner && owner !== me.name ? [owner, null] : null,
      [name, appHref(owner, name)], tab !== "Overview" ? [tab, null] : null,
      curSub() && curSub() !== (subsOf(tab)[0] || [""])[0] ? [subsOf(tab).find(([k]) => k === curSub())[1], null] : null));
  }
  function drawTabs() {
    drawCrumbs();
    tabBar.replaceChildren(...tabs.map(t => h("button", { role: "tab", class: t === tab ? "on" : "", "aria-selected": t === tab ? "true" : "false",
      onclick: () => { tab = t; sub = ""; mode = ""; setUrl(); drawTabs(); drawBody(); } }, t)));
  }
  function drawHead() {
    const acts = [];
    if (st.running && perm !== "watch") {
      acts.push(act("Stop after this pass", () => stopLoop(name, false, owner)), act("Stop now", () => stopLoop(name, true, owner), { cls: "danger" }));
    } else if (!st.running && mine && info.document) {
      acts.push(act(st.last_active ? "Start (resume)" : "Start", async () => { if (await startLoop(name, owner)) { await refresh(); goTab("Live"); } }, { cls: "primary" }));
    }
    if (mine) {
      acts.push(act("Check", async () => {
        const out = h("pre", { class: "log small" }, "Checking in the sandbox…");
        const d = dialog("Check the document", out, [["Close", null]]);
        const r = await api(`/apps/${enc(name)}/check`, { method: "POST" });
        out.textContent = (r.ok ? "Ready to run.\n\n" : "NOT READY\n\n") + r.output;
        await d;
      }));
      if (perm === "edit") acts.push(leaveBtn());
    }
    if (perm === "watch") acts.push(leaveBtn());
    const whose = perm === "owner" ? "" : h("span", { class: `pill ${perm === "edit" || perm === "admin" ? "live" : ""}`, title: perm === "edit" ? "Shared with you: you may change and run it"
      : perm === "watch" ? "Shared with you: you may see its runs and outputs" : "An admin: you may change and run it; it runs on its owner's agents and settings" },
      `${info.owner}'s · ${perm === "edit" ? "you may edit" : perm === "watch" ? "watching" : "an admin's edit"}`);
    header.replaceChildren(head(h("span", {}, name, " ", statePill(st), whose),
      h("span", {}, info.document ? h("span", { class: "mono" }, info.document) : "", " · ", lastSaid(st),
        st.container ? h("span", { class: "muted" }, ` · sandbox ${st.container}`) : ""), ...acts));
  }
  async function refresh() { const was = st.running; st = await api(`${base.slice(4)}/state${qs}`); drawHead(); drawBanner(); if (was !== st.running && ((tab === "Live" && !curSub()) || tab === "Overview")) drawBody(); }
  // notes and the agent's question
  const noteText = h("textarea", { rows: 3, placeholder: "A note: it joins the next prompt, or answers the agent's open question." });
  const noteList = h("div", { class: "notes" });
  async function sendNote(text) {
    const r = await api(`/apps/${enc(name)}/notes`, { method: "POST", body: { text } });
    toast(r.ok, "ok"); noteText.value = ""; question = null; drawBanner(); drawNotes();
  }
  async function drawNotes() {
    const notes = await api(`/apps/${enc(name)}/notes${qs}`).catch(() => []);
    noteList.replaceChildren(...notes.slice(-20).reverse().map(n => h("div", { class: "note has-bin" }, h("small", { class: "muted" }, n.by, " · ", ago(n.t)), h("div", {}, n.text),
      mine ? binButton("note", "Remove this note?", "It goes from the page, and from the loop if it has not read it yet; what the loop read already stays in its record.",
        async () => { await api(`/apps/${enc(name)}/notes/${enc(n.id)}${qs}`, { method: "DELETE" }); toast("The note is removed", "ok"); drawNotes(); }) : "")));
  }
  function drawBanner() {
    composer.update();
    askFab.classList.toggle("asking", !!(question && st.running));   // D758: the agent waits: the button says so
    if (!question || !st.running) { banner.replaceChildren(); return; }
    const left = Math.max(0, Math.round(question.asked + question.wait_s - Date.now() / 1000));
    const ans = h("textarea", { rows: 3, placeholder: "Your answer" });
    banner.replaceChildren(h("section", { class: "card ask" }, h("div", { class: "card-head" }, h("h2", {}, "The agent asks"),
        h("span", { class: "muted" }, left ? `answer within ${dur(left)}, or it decides` : "its time is up: it decided")),
      h("pre", { class: "question" }, question.question), mine ? [ans,
      h("div", { class: "form-actions" }, act("Answer", async () => { if (ans.value.trim()) await sendNote(ans.value.trim()); }, { cls: "primary" }))] : ""));
  }
  /** Notes and answers (D697): one line docked under the Live tab, as a chat's. Enter sends,
      Shift+Enter breaks the line. When the agent asks, the line says so and answers it. */
  const composer = (() => {
    const ta = h("textarea", { rows: 1, class: "composer-in", "aria-label": "A note to the loop" });
    const sendBtn = h("button", { class: "primary", type: "button" }, "Send");
    const ask = h("div", { class: "composer-ask" });
    const hist = h("div", { class: "composer-hist", hidden: true }, noteList);
    const grow = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 180) + "px"; };
    async function send() {
      const text = ta.value.trim();
      if (!text) return;
      sendBtn.disabled = true;
      try { await sendNote(text); ta.value = ""; grow(); } catch (x) { toast(x.message, "bad"); } finally { sendBtn.disabled = false; }
    }
    ta.addEventListener("input", grow);
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
    sendBtn.addEventListener("click", send);
    hist.hidden = false;                              // D758: in the drawer, the notes sent so far show under the line
    const el = h("div", { class: "composer" }, ask, h("div", { class: "composer-row" }, ta, sendBtn), hist);
    function update() {
      const open = question && st.running;
      el.classList.toggle("asking", !!open);
      if (open) {
        const left = Math.max(0, Math.round(question.asked + question.wait_s - Date.now() / 1000));
        ask.replaceChildren(h("strong", {}, "The agent asks"), h("span", { class: "muted" }, left ? ` · answer within ${dur(left)}, or it decides` : " · its time is up: it decided"),
          h("pre", { class: "question" }, question.question));
        ta.placeholder = "Your answer to the agent (Enter sends)"; sendBtn.textContent = "Answer";
      } else {
        ask.replaceChildren();
        ta.placeholder = "A note to the loop: it joins the next prompt (Enter sends, Shift+Enter for a new line)"; sendBtn.textContent = "Send";
      }
    }
    update();
    return { el, update };
  })();
  /** The log as it grows (D697), under the task: coloured as the Log tab, following its end. */
  const liveLog = (() => {
    const box = h("div", { class: "logview mini wrap" });
    const toEnd = () => requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });   // once laid out
    const N = 80;
    const onlyBad = h("input", { type: "checkbox" });
    const keep = (l) => !onlyBad.checked || log.problem(l.text);
    const fill = () => { box.replaceChildren(...log.recent(4000).filter(keep).slice(-N).map(log.lineEl)); toEnd(); };
    onlyBad.addEventListener("change", fill);
    log.onLines((fresh) => {
      if (!box.isConnected) return;
      const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
      for (const l of fresh) if (keep(l)) box.append(log.lineEl(l));
      while (box.childElementCount > N) box.firstChild.remove();
      if (atEnd) toEnd();
    });
    log.onTimes(fill);
    const liveTimes = h("input", { type: "checkbox", checked: log.times.checked });
    liveTimes.addEventListener("change", () => { log.times.checked = liveTimes.checked; log.times.dispatchEvent(new Event("change")); });
    log.onTimes(() => { liveTimes.checked = log.times.checked; });
    const el = card("The log", box, { cls: "livelog-card", actions: [h("label", { class: "check small" }, onlyBad, "problems only"),
      h("label", { class: "check small" }, liveTimes, "times"),
      h("button", { class: "small", type: "button", onclick: () => goTab("Live", "log") }, "The whole log")] });
    return { el, fill };
  })();

  // files and the workbench
  const viewer = h("div", { class: "viewer" });
  const fileUrl = (path, dl) => `/api/apps/${enc(name)}/file?path=${enc(path)}${dl ? "&download=1" : ""}${q}`;
  const size = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
  /** D808: a path as its folders, each one a link that opens it; the loop's own folder first. */
  function pathCrumbs(path, dir) {
    const segs = String(path || "").split("/").filter(Boolean);
    const link = (label, to) => h("a", { href: "javascript:void 0", onclick: () => openFile(to, true) }, label);
    return h("span", { class: "mono path-crumbs" }, link(name, ""), ...segs.flatMap((s, i) => [h("span", { class: "muted" }, " / "),
      i < segs.length - 1 || dir ? (i < segs.length - 1 ? link(s, segs.slice(0, i + 1).join("/")) : h("strong", {}, s)) : h("strong", {}, s)]));
  }
  async function openFile(path, dir) {
    viewer.replaceChildren(skeleton(8));
    if (dir) {
      const list = await api(`/apps/${enc(name)}/files?path=${enc(path)}${showIgnored() ? "&ignored=true" : ""}${q}`);
      viewer.replaceChildren(h("div", { class: "viewer-head" }, pathCrumbs(path, true)), fileList(list));
      return;
    }
    const r = await fetch(fileUrl(path), { credentials: "same-origin" });
    if ((r.headers.get("content-type") || "").startsWith("text/")) {
      const ed = codeEditor(await r.text(), langOf(path), { readonly: !mine });
      viewer.replaceChildren(h("div", { class: "viewer-head" }, pathCrumbs(path, false),
          h("div", { class: "actions" }, mine ? act("Save", async () => {
            await api(`/apps/${enc(name)}/file?path=${enc(path)}`, { method: "PUT", body: { text: ed.textarea.value } }); toast(`${path} saved`, "ok");
          }, { cls: "small" }) : "", h("a", { class: "btn small", href: fileUrl(path, true) }, "Download"))), ed.el);
    } else {
      viewer.replaceChildren(h("div", { class: "viewer-head" }, pathCrumbs(path, false)),
        empty("A binary file.", h("a", { class: "btn", href: fileUrl(path, true) }, "Download")));
    }
  }
  // D703: what .gitignore ignores is left out, unless asked for (remembered in this browser)
  const showIgnored = () => { try { return localStorage.getItem("flux-show-ignored") === "1"; } catch (_) { return false; } };
  function ignoredToggle() {
    const box = h("input", { type: "checkbox", checked: showIgnored(), id: "show-ignored" });
    box.addEventListener("change", () => { try { localStorage.setItem("flux-show-ignored", box.checked ? "1" : "0"); } catch (_) { /* per viewer */ } drawBody(); });
    return h("label", { class: "check small ignored-toggle", title: "Files the loop's .gitignore ignores; .git is never shown" }, box, "show ignored files");
  }
  function fileList(list) {
    return h("ul", { class: "files" }, list.map(f => h("li", { class: f.ignored ? "ignored" : "" },
      h("a", { href: "javascript:void 0", onclick: () => openFile(f.path, f.dir) }, h("span", { class: "ic" }, f.dir ? "▸" : "·"), f.path.split("/").pop() + (f.dir ? "/" : "")),
      f.ignored ? h("span", { class: "pill small" }, "ignored") : "", f.dir ? "" : h("small", { class: "muted" }, size(f.size)))));
  }
  function adder() {
    const addFiles = h("input", { type: "file", multiple: true });
    const addFolder = h("input", { placeholder: "folder (optional)" });
    const dz = dropZone("Drop files or folders to add them", async (got) => {
      const pd = progressDialog("Adding files", `${got.length} file(s)${addFolder.value ? " into " + addFolder.value : ""}`);
      try {
        const n = await sendFiles(name, got, { folder: addFolder.value.trim(), onProgress: pd.set, signal: pd.signal });
        toast(`Added ${n} file(s)${addFolder.value ? " into " + addFolder.value : ""}`, "ok");
      } catch (x) { toast(pd.signal.aborted ? `The upload was cancelled: ${x.message}.` : `The upload failed: ${x.message}`, pd.signal.aborted ? "warn" : "bad", { timeout: 12000 }); }
      finally { pd.close(); drawBody(); }
    });
    return h("div", {}, dz, h("details", { class: "adder" }, h("summary", {}, "Add files"),
      h("label", { class: "stack" }, "Files or a .zip", addFiles), h("label", { class: "stack" }, "Into folder", addFolder),
      act("Add", async () => {
        if (!addFiles.files.length) { toast("Choose files or a .zip.", "warn"); return; }
        const n = await sendFiles(name, [...addFiles.files].map(f => ({ file: f, path: f.name })), { folder: addFolder.value.trim() });
        toast(`Added ${n} file(s)`, "ok"); drawBody();
      }, { cls: "small primary" })));
  }

  /** Where the time goes (D694): one start's phases as bars in lanes by kind of work, and per
      kind its calls, the average and longest, the total (parallel work once) and its share (D772). */
  const PALETTE = ["#5b8def", "#e8804f", "#4fb286", "#b176e0", "#d9b440", "#e0607e", "#48b3c9", "#8f9aa6", "#a3c956", "#c98a56"];
  let tlStart = null, tlPass = "";
  async function timelineView() {
    const params = new URLSearchParams(owner ? { owner } : {});
    if (tlStart != null) params.set("start", tlStart);
    const t = await api(`/apps/${enc(name)}/timeline?${params}`);
    if (tab !== "Live" || curSub() !== "timeline") return;
    if (!t.bars.length) { body.replaceChildren(card(null, empty("No phase in the journal yet."))); return; }
    const color = {}; t.kinds.forEach((k, i) => { color[k.kind] = PALETTE[i % PALETTE.length]; });
    const startSel = h("select", { onchange: (e) => { tlStart = Number(e.target.value); tlPass = ""; timelineView(); } },
      t.starts.slice().reverse().map(st => h("option", { value: st.index, selected: st.index === t.start },
        `start ${st.index + 1} · ${st.t0 ? new Date(st.t0 * 1000).toLocaleString() : "?"}${st.t0 && st.t1 ? " · " + dur(st.t1 - st.t0) : ""}`)));
    const passSel = h("select", { onchange: (e) => { tlPass = e.target.value; draw(); } },
      h("option", { value: "" }, `every pass (${t.passes.length})`), t.passes.map((p, i) => h("option", { value: String(i), selected: tlPass === String(i) }, `pass ${i + 1}`)));
    // D772: per kind its calls, the time of one, the time of all and their share; side by side only when it happened
    const together = (k) => k.busy > 0 && k.summed > k.busy * 1.05 && k.summed - k.busy >= 1;
    const side = t.kinds.some(together);
    const kindsTable = h("table", { class: "list compact kinds" },
      h("thead", {}, h("tr", {}, h("th", {}, "Kind of work"), h("th", { class: "num" }, "Calls"), h("th", { class: "num" }, "Average"),
        h("th", { class: "num" }, "Longest"), h("th", { class: "num", title: "The wall clock its calls held (side by side counted once)" }, "Total"),
        h("th", {}, "Share of the wall clock"),
        side ? h("th", { class: "num", title: "Every call's own time added, and how many ran at once on average" }, "Summed · at once") : "")),
      h("tbody", {}, t.kinds.map(k => h("tr", {},
        h("td", {}, h("i", { class: "sw", style: `background:${color[k.kind]}` }), k.kind),
        h("td", { class: "num mono" }, String(k.count)), h("td", { class: "num mono" }, dur(k.mean)),
        h("td", { class: "num mono" }, dur(k.longest)), h("td", { class: "num mono strong" }, dur(k.busy)),
        h("td", {}, h("div", { class: "share" }, h("div", { class: "share-bar", style: `width:${Math.min(100, k.share * 100).toFixed(1)}%;background:${color[k.kind]}` }),
          h("span", {}, `${(k.share * 100).toFixed(k.share < 0.1 ? 1 : 0)}%`))),
        side ? h("td", { class: "num mono" }, together(k) ? `${dur(k.summed)} · ×${(k.summed / k.busy).toFixed(1)}` : "—") : ""))));
    const chartBox = h("div", { class: "gantt-box" });
    function draw() {
      let a = t.t0, b = t.t1;
      if (tlPass !== "") { const i = Number(tlPass); a = t.passes[i]; b = t.passes[i + 1] || t.t1; }
      const bars = t.bars.filter(x => x.t1 >= a && x.t0 <= b);
      const lanes = t.kinds.map(k => k.kind).filter(k => bars.some(x => x.kind === k));
      const W = 1200, L = 120, R = 12, T = 8, lane = 30, B = 26, H = T + lanes.length * lane + B, span = Math.max(b - a, 1e-6);
      const X = (v) => L + (W - L - R) * (Math.min(Math.max(v, a), b) - a) / span;
      const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => a + f * span);
      chartBox.replaceChildren(sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart gantt", role: "img", "aria-label": "phases over time by kind" },
        lanes.map((k, i) => [sv("text", { x: L - 8, y: T + i * lane + lane / 2 + 4, class: "tick", "text-anchor": "end" }, k),
          sv("line", { x1: L, x2: W - R, y1: T + (i + 1) * lane, y2: T + (i + 1) * lane, class: "grid" })]),
        ticks.map(v => [sv("line", { x1: X(v), x2: X(v), y1: T, y2: H - B, class: "grid" }),
          sv("text", { x: X(v), y: H - 8, class: "tick", "text-anchor": v === a ? "start" : v === b ? "end" : "middle" }, `+${dur(v - a) || "0s"}`)]),
        t.passes.filter(p => p > a && p < b).map(p => sv("line", { x1: X(p), x2: X(p), y1: T, y2: H - B, class: "pass-line" })),
        bars.map(x => { const i = lanes.indexOf(x.kind); const x0 = X(x.t0), x1 = X(x.t1);
          return sv("rect", { x: x0, y: T + i * lane + 3, width: Math.max(3, x1 - x0), height: lane - 6, rx: 2,
            fill: color[x.kind], class: `bar${x.failed ? " failed" : ""}${x.running ? " running" : ""}` },
            sv("title", {}, `${x.name}${x.why ? " · " + x.why : ""}\n${dur(x.t1 - x.t0)}${x.running ? " so far" : ""}${x.failed ? " · failed" : ""}`)); })));
    }
    draw();
    body.replaceChildren(
      card(null, h("div", { class: "tl-head" }, startSel, passSel,
        h("span", { class: "muted" }, t.running ? "running · " : "", `${dur(t.wall)} on the wall clock · ${t.bars.length} phase(s) · ${t.passes.length} pass(es)`))),
      card("Phases over time", [chartBox, h("p", { class: "muted small" }, "Dashed lines: a pass begins. Hover a bar for its phase.")]),
      card("Where the time goes", [kindsTable,
        h("p", { class: "muted small" }, "Each call that does the work (a tool, an agent, a model call) counts in the kind of its nearest named phase. Total: the wall clock its calls held, work side by side counted once.")]));
  }

  /** The loop's designs (D690): accepted or failed, with their measurements against the limits. */
  function resultsView(r) {
    let filter = "all";
    const fmt = (v) => v == null ? "" : v !== 0 && Math.abs(v) < 0.01 ? Number(v).toExponential(2)
      : Math.abs(v) >= 1000 || Number.isInteger(v) ? String(Math.round(v * 100) / 100) : String(Number(Number(v).toPrecision(4)));
    const unit = { fmax_mhz: "MHz", area_um2: "µm²", power_w: "W", time_ms: "ms", cell_count: "cells" };
    const limitOf = (m) => r.limits.find(l => l.metric === m);
    const verdictPill = (d) => d.verdict === "accepted" ? h("span", { class: "pill ok" }, "accepted") : h("span", { class: "pill bad" }, "failed");
    const detail = h("div", { class: "detail" }, empty("Select a design to see the limits it misses, every stage's numbers and its source."));
    async function open(d, tr) {
      if (tr.parentNode) for (const x of tr.parentNode.children) x.classList.remove("sel");
      tr.classList.add("sel");
      detail.replaceChildren(skeleton(6));
      const full = await api(`/apps/${enc(name)}/design?design=${enc(d.name)}&part=${enc(d.part)}${q}`);
      const stages = Object.entries(d.stages).filter(([, m]) => Object.keys(m).length);
      const metrics = [...new Set(stages.flatMap(([, m]) => Object.keys(m)))];
      detail.replaceChildren(
        h("div", { class: "detail-head" }, h("h2", {}, d.name), verdictPill(d), d.decision ? h("span", { class: "pill ok" }, "★ decision") : "",
          d.part ? h("span", { class: "muted" }, `part ${d.part}`) : ""),
        d.why.length ? h("div", { class: "blk" }, h("h3", {}, "Limits it misses"), h("ul", { class: "misses" }, d.why.map(w => h("li", {}, w)))) : "",
        stages.length ? h("div", { class: "blk" }, h("h3", {}, "Measurements"), h("table", { class: "list compact" },
          h("thead", {}, h("tr", {}, h("th", {}, "stage"), ...metrics.map(m => h("th", { class: "num" }, m)))),
          h("tbody", {}, stages.map(([st, m]) => h("tr", {}, h("td", {}, st), ...metrics.map(k => h("td", { class: "mono num" }, fmt(m[k])))))))) : "",
        full.artifact ? h("div", { class: "blk" }, h("h3", {}, "The design"), codeBlock(full.artifact, "")) : "");
    }
    const table = h("div", {});
    let sortKey = null, sortDir = 1;                      // null: the decision, then the newest (D692)
    const PAGE = 200;
    let pageN = PAGE;                                     // the rows drawn: a long loop's table grows by pages (D694)
    const keyOf = (d) => `${d.part}|${d.name}`;
    let picked = [];                                      // two designs to compare (D694)
    const cmpBtn = h("button", { class: "small", disabled: true, onclick: () => compare() }, "Compare");
    const drawPicked = () => { cmpBtn.disabled = picked.length !== 2; cmpBtn.textContent = picked.length ? `Compare ${picked.length}/2` : "Compare"; };
    async function compare() {
      const [a, b] = picked;
      const [fa, fb] = await Promise.all([a, b].map(d => api(`/apps/${enc(name)}/design?design=${enc(d.name)}&part=${enc(d.part)}${q}`)));
      const stages = (r.stages || []).filter(st => a.stages[st] || b.stages[st]).concat(Object.keys({ ...a.stages, ...b.stages }).filter(st => !(r.stages || []).includes(st)));
      const rows = [];
      for (const st of stages) {
        const ms = [...new Set([...Object.keys(a.stages[st] || {}), ...Object.keys(b.stages[st] || {})])];
        for (const m of ms) rows.push({ st, m, va: (a.stages[st] || {})[m], vb: (b.stages[st] || {})[m] });
      }
      const dirs = r.objective_list || r.limits || [];
      const cell = (v) => h("td", { class: "num mono" }, v == null ? "—" : fmt(v));
      const delta = (row) => {
        if (row.va == null || row.vb == null) return h("td", {}, "");
        const d = row.vb - row.va, rel = row.va ? d / Math.abs(row.va) : null;
        const better = d === 0 ? null : (directionOf(row.m, dirs) === "minimize" ? d < 0 : d > 0);
        return h("td", { class: `num mono${better === true ? " meets" : better === false ? " misses" : ""}` },
          d === 0 ? "=" : `${d > 0 ? "+" : ""}${fmt(d)}${rel != null && isFinite(rel) ? ` (${d > 0 ? "+" : ""}${(rel * 100).toFixed(1)}%)` : ""}`);
      };
      const ops = fa.artifact != null && fb.artifact != null ? lineDiff(fa.artifact, fb.artifact) : null;
      const changed = ops ? ops.filter(o => o[0] !== " ").length : 0;
      await dialog(`${a.name} → ${b.name}`, h("div", { class: "compare" },
        h("p", { class: "muted" }, "B against A: the change, green where B is better by the metric's direction."),
        h("table", { class: "list compact" }, h("thead", {}, h("tr", {}, h("th", {}, "stage"), h("th", {}, "metric"),
            h("th", { class: "num" }, "A ", verdictPill(a)), h("th", { class: "num" }, "B ", verdictPill(b)), h("th", { class: "num" }, "B − A"))),
          h("tbody", {}, rows.map(row => h("tr", {}, h("td", { class: "muted" }, row.st), h("td", {}, row.m), cell(row.va), cell(row.vb), delta(row))))),
        h("h3", {}, "The source", ops ? h("span", { class: "muted" }, changed ? ` · ${changed} line(s) differ` : " · the same") : ""),
        ops ? (changed ? diffView(ops) : empty("The two sources are the same.")) : empty("A source is missing.")),
        [["Close", null, "primary"]]);
    }
    const valueOf = (d, key) => key === "name" ? d.name : key === "verdict" ? d.verdict : key === "stage" ? (r.stages || []).indexOf(d.shown)
      : key === "when" ? Date.parse(d.last || "") || 0 : d.numbers[key];
    function sorted(list) {
      if (!sortKey) return list;
      return list.slice().sort((a, b) => {
        const x = valueOf(a, sortKey), y = valueOf(b, sortKey);
        if (x == null && y == null) return 0;
        if (x == null) return 1;                            // missing values last, either way
        if (y == null) return -1;
        return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * sortDir;
      });
    }
    const th = (key, label, extra = {}, ...more) => h("th", { ...extra, class: `sortable ${extra.class || ""}${sortKey === key ? " sorted" : ""}`,
      onclick: () => { if (sortKey === key) sortDir = -sortDir; else { sortKey = key; sortDir = ["name", "verdict", "stage"].includes(key) ? 1 : -1; } drawTable(); } },
      label, sortKey === key ? h("span", { class: "arrow" }, sortDir > 0 ? " ▲" : " ▼") : "", ...more);
    function drawTable() {
      const all = sorted(r.designs.filter(d => filter === "all" || d.verdict === filter));
      const shown = all.slice(0, pageN);
      const boxes = new Map();
      const tick = (d) => { const box = h("input", { type: "checkbox", title: "compare", checked: picked.some(p => keyOf(p) === keyOf(d)),
        onclick: (e) => {
          e.stopPropagation();
          if (box.checked) {
            picked.push(d);
            if (picked.length > 2) { const gone = picked.shift(); const b = boxes.get(keyOf(gone)); if (b) b.checked = false; }   // the oldest pick goes
          } else picked = picked.filter(p => keyOf(p) !== keyOf(d));
          drawPicked();
        } });
        boxes.set(keyOf(d), box);
        return h("td", { class: "pick" }, box); };
      const more = all.length > shown.length ? h("div", { class: "more" }, h("button", { class: "small", onclick: () => { pageN += PAGE; drawTable(); } },
        `Show ${Math.min(PAGE, all.length - shown.length)} more`), h("span", { class: "muted" }, ` ${shown.length} of ${all.length} shown`)) : "";
      table.replaceChildren(shown.length ? h("div", { class: "scroll-x" }, h("table", { class: "list designs" },
        h("thead", {}, h("tr", {}, h("th", { class: "pick", title: "Tick two to compare" }, ""), th("name", "Design"), th("verdict", "Verdict"), th("stage", "Stage"),
          ...r.metrics.map(m => { const l = limitOf(m); return th(m, m, { class: "num", title: l ? `${l.direction === "maximize" ? "at least" : "at most"} ${l.goal}` : "" },
            l ? h("div", { class: "lim" }, `${l.direction === "maximize" ? "≥" : "≤"} ${l.goal}`) : ""); }),
          th("when", "When"))),
        h("tbody", {}, shown.map(d => { const tr = h("tr", { class: `clickable ${d.verdict}${d.decision ? " decided" : ""}`, onclick: () => open(d, tr) },
          tick(d),
          h("td", { class: "mono" }, d.decision ? h("span", { class: "star", title: "the decision" }, "★ ") : "", d.name, d.part ? h("div", { class: "muted small" }, d.part) : ""),
          h("td", {}, verdictPill(d)),
          h("td", { class: "muted" }, d.shown),
          ...r.metrics.map(m => { const v = d.numbers[m]; const ok = d.meets[m];
            return h("td", { class: `mono num${ok === true ? " meets" : ok === false ? " misses" : ""}` }, v == null ? "" : [fmt(v), unit[m] ? h("small", {}, " " + unit[m]) : "", ok === false ? " ✗" : ok === true ? " ✓" : ""]); }),
          h("td", { class: "muted" }, d.last ? ago(Date.parse(d.last) / 1000) : "")); return tr; }))), more) : empty("No design matches."));
    }
    const chip = (key, label) => h("button", { class: `chip${filter === key ? " on" : ""}`, onclick: () => { filter = key; pageN = PAGE; chips(); drawTable(); } }, label);
    const chipBox = h("div", { class: "chips" });
    function chips() {
      chipBox.replaceChildren(chip("all", `All ${r.designs.length}`), chip("accepted", `Accepted ${r.counts.accepted}`), chip("failed", `Failed ${r.counts.failed}`),
        h("span", { class: "grow" }), cmpBtn);
    }
    chips(); drawTable();
    // the charts (D693): two metrics against each other, and each metric's best so far
    const objectives = r.objective_list || r.limits || [];
    const nums = r.metrics.filter(m => r.designs.some(d => Object.values(d.stages).some(n => n[m] != null)));
    const stageNames = (r.stages && r.stages.length ? r.stages : [...new Set(r.designs.flatMap(d => Object.keys(d.stages)))])
      .filter(s => r.designs.some(d => d.stages[s] && Object.keys(d.stages[s]).length));
    const sel = (opts, value, onchange) => { const e = h("select", { onchange: () => onchange(e.value) }, opts.map(([v, l]) => h("option", { value: v, selected: v === value }, l))); return e; };
    let px = nums[1] || nums[0], py = nums[0], pst = "", tMetrics = new Set(nums.slice(0, 2)), tst = "";
    const paretoBox = h("div", {}), timeBox = h("div", {});
    const pickRow = (d) => {
      const all = sorted(r.designs.filter(x => filter === "all" || x.verdict === filter));
      const at = all.indexOf(d);
      if (at >= pageN) { pageN = Math.ceil((at + 1) / PAGE) * PAGE; drawTable(); }
      const tr = [...table.querySelectorAll("tbody tr")][at];
      if (tr) { tr.scrollIntoView({ block: "nearest" }); open(d, tr); } else open(d, h("tr"));   // filtered out: the detail alone
    };
    const stageOpts = (all) => [["", all], ...stageNames.map(s => [s, s])];
    function drawPareto() {
      paretoBox.replaceChildren(h("div", { class: "chart-ctl" },
        h("label", {}, "x ", sel(nums.map(m => [m, m]), px, v => { px = v; drawPareto(); })),
        h("label", {}, "y ", sel(nums.map(m => [m, m]), py, v => { py = v; drawPareto(); })),
        h("label", {}, "stage ", sel(stageOpts("each design's deepest"), pst, v => { pst = v; drawPareto(); }))),
        nums.length < 2 ? empty("A front needs two measured metrics.") : paretoChart(r.designs, px, py, pst, objectives, pickRow));
    }
    function drawTime() {
      const objFor = (m) => { const o = objectives.find(x => x.metric === m) || {}; return { metric: m, direction: directionOf(m, objectives), goal: o.goal, stage: tst || (o.stage && o.stage !== "deepest" ? o.stage : null) }; };
      timeBox.replaceChildren(h("div", { class: "chart-ctl" },
        h("div", { class: "chips" }, nums.map(m => h("button", { class: `chip${tMetrics.has(m) ? " on" : ""}`,
          onclick: () => { if (tMetrics.has(m)) tMetrics.delete(m); else tMetrics.add(m); drawTime(); } }, m))),
        h("label", {}, "stage ", sel(stageOpts("the objective's stage"), tst, v => { tst = v; drawTime(); }))),
        tMetrics.size ? h("div", { class: "chart-grid" }, nums.filter(m => tMetrics.has(m)).map(m => bestChart(r.rows || [], objFor(m), r.passes)))
          : empty("Pick a metric to chart."));
    }
    drawPareto(); drawTime();
    const thinned = r.rows_total > (r.rows || []).length ? h("p", { class: "muted small" }, `${r.rows.length} of ${r.rows_total} measurements drawn: every new best, and an even share of the rest.`) : "";
    let shut = false;
    try { shut = localStorage.getItem("flux-charts") === "shut"; } catch (_) { /* a default */ }
    const charts = nums.length ? h("details", { class: "charts-box", open: !shut, ontoggle: (e) => { try { localStorage.setItem("flux-charts", e.target.open ? "open" : "shut"); } catch (_) { /* per viewer */ } } },
      h("summary", {}, "Charts: the Pareto front and the improvement over time"),
      h("div", { class: "grid-2 charts" }, card("Pareto front", paretoBox), card("Improvement over time", [timeBox, thinned]))) : "";
    return h("div", {},
      card(null, h("div", { class: "results-head" }, h("div", {}, h("h2", {}, "Objective"), h("p", { class: "muted" }, r.objectives,
          r.total > r.designs.length ? ` · the newest ${r.designs.length} of ${r.total} designs` : "")),
        h("div", { class: "actions" }, r.answer ? h("a", { class: "btn small", href: `/api/apps/${enc(name)}/file?path=runs/answer.json&download=1${q}` }, "The answer (JSON)") : "",
          h("a", { class: "btn small", href: `${base}/report${qs}`, target: "_blank", rel: "noopener" }, "Open the report")))),
      charts,
      h("div", { class: "split results" }, card(null, [chipBox, table]), card(null, detail, { cls: "detail-card" })));
  }

  const goTab = (t, s = "") => { tab = t; sub = s; mode = ""; setUrl(); drawTabs(); drawBody(); };
  /** Questions about the loop (D705): an agent reads it -- its files, its record, its log -- and
      answers; nothing changes. Kept with the loop, newest first; one answered at a time. */
  let askTimer = null;
  cleanup.push(() => clearTimeout(askTimer));
  const askBox = h("div", { class: "drawer-body" });
  // D758: one place to talk to a loop -- a note to it while it runs (an answer when its agent asks), and a
  // question to an agent about it -- the drawer, from every tab; no bar docked under Live any more
  const drawer = h("aside", { class: "drawer", "aria-label": "Talk to this loop" },
    h("div", { class: "drawer-head" }, h("h2", {}, "Talk to this loop"), h("button", { class: "small", type: "button", onclick: () => setAsk(false) }, "Close")), askBox);
  const askFab = h("button", { class: "ask-fab", type: "button", title: "Notes to the running loop, and questions to an agent about it", onclick: () => setAsk(!askOpen) }, "Talk");
  function setAsk(open) {
    askOpen = open; drawer.classList.toggle("open", open); askFab.classList.toggle("on", open);
    if (open) { askBox.replaceChildren(skeleton(4)); askView(); } else clearTimeout(askTimer);
  }
  const onKey = (e) => { if (e.key === "Escape" && askOpen && !document.querySelector("dialog[open]")) setAsk(false); };
  document.addEventListener("keydown", onKey);
  cleanup.push(() => document.removeEventListener("keydown", onKey));
  async function askView() {
    const list = await api(`/apps/${enc(name)}/asks${qs}`).catch(() => []);
    if (!askOpen) return;
    const busy = list.some(a => a.running);
    clearTimeout(askTimer);
    if (busy) askTimer = setTimeout(() => { if (askOpen && !askBox.contains(document.activeElement)) askView(); else if (askOpen) askTimer = setTimeout(askView, 3000); }, 3000);
    let form = "", steer = "";
    if (mine && st.running) {                       // D758: the running loop's notes, and its agent's open question
      composer.update();
      drawNotes();
      steer = card("A note to the running loop", [h("p", { class: "muted" }, "It joins the loop's next prompt; when its agent asks, it is the answer."),
        composer.el], { cls: "steer-card" });
    }
    if (mine) {
      const q = h("textarea", { rows: 3, id: "ask-q", placeholder: "e.g. Why did it stall at 2 GHz? Which design is best on area, and by how much? What should the next pass try?" });
      const who = await agentSelect("ask-who");
      form = card(null, [h("p", { class: "muted" }, "The agent reads the loop -- its document and files, a copy of its record, its log -- in the sandbox, and answers. It changes nothing."),
        h("label", { class: "stack" }, "Your question", q),
        h("div", { class: "row" }, h("label", { class: "stack" }, "Who answers", who), h("span", { class: "grow" }),
          act("Ask", async () => {
            if (!q.value.trim()) { toast("Ask something.", "warn"); q.focus(); return; }
            toast((await api(`/apps/${enc(name)}/asks${qs}`, { method: "POST", body: { question: q.value, author: who.value } })).ok, "ok");
            askView();
          }, { cls: "primary", title: busy ? "Another question is being answered" : null }))]);
    }
    const one = (a) => card(null, [
      mine && !a.running ? binButton("question", "Remove this question?", "The question and its answer are removed for everyone who sees this loop.",
        async () => { await api(`/apps/${enc(name)}/asks/${a.id}${qs}`, { method: "DELETE" }); toast("The question and its answer are removed", "ok"); askView(); }) : "",
      h("div", { class: "ask-head" }, h("strong", {}, a.question), h("div", { class: "muted small" }, `${a.author} · asked by ${a.by} `, ago(a.started),
        a.ended ? [" · took ", dur(a.ended - a.started)] : "")),
      a.running ? [h("div", { class: "row" }, h("span", { class: "pill live" }, h("i", { class: "dot" }), "reading the loop"),
          mine ? act("Stop", async () => { toast((await api(`/apps/${enc(name)}/asks/${a.id}/stop${qs}`, { method: "POST" })).ok, "ok"); askView(); }, { cls: "small" }) : ""),
          h("pre", { class: "log small author-log" }, (a.log || []).join("\n") || "…")]
        : a.answer ? markdown(a.answer) : [h("p", { class: "callout bad" }, "No answer."), h("pre", { class: "log small author-log" }, (a.log || []).join("\n"))]],
      { cls: "ask-card has-bin" });
    askBox.replaceChildren(steer, mine ? h("h3", { class: "drawer-sub" }, "Ask an agent about it") : "", form,
      ...(list.length ? list.map(one) : [card(null, empty(mine ? "No question yet." : "No question asked yet."))]));
  }
  /** The loop's settings (D697): its environment variables over the user's and the server's, and
      what only an admin sets -- the sandbox and its limits. */
  async function settingsView() {
    body.replaceChildren(card(null, skeleton(7)));
    const e = await api(`/apps/${enc(name)}/env${qs}`);
    if (tab !== "Settings") return;
    const varsCard = card("Environment variables", [
      h("p", { class: "muted" }, isOwner ? "What this loop's runs and checks get, over your own and the server's. A secret is stored encrypted and never shown again."
        : `What this loop's runs and checks get, over ${info.owner}'s own and the server's: the runs are ${info.owner}'s. A secret is stored encrypted and never shown again.`),
      envEditor(e.loop, mine ? async (v) => { await api(`/apps/${enc(name)}/env`, { method: "PUT", body: v }); settingsView(); } : null, "loop"),
      e.user.length || e.server.length ? h("div", { class: "blk" }, h("h3", {}, "Under them"),
        envTable([...e.server.map(x => ({ ...x, from: "the server" })), ...e.user.map(x => ({ ...x, from: isOwner ? "yours (Account)" : `${info.owner}'s (their Account)` }))],
          new Set(e.loop.map(x => x.name)))) : ""]);
    const danger = isOwner ? card("Delete this loop", [h("p", { class: "muted" }, "Its document, files, record and log go. This cannot be undone."),
      h("div", { class: "form-actions" }, st.running ? h("span", { class: "muted" }, "Stop it first.") : act("Delete", async () => {
        if (!await confirmDialog(`Delete ${name}?`, "Its document, files, record and log go. This cannot be undone.", { ok: "Delete", danger: true })) return;
        await api(`/apps/${enc(name)}`, { method: "DELETE" }); toast(`${name} deleted`, "ok"); location.hash = "#/";
      }, { cls: "danger" }))], { cls: "danger-card" }) : "";
    body.replaceChildren(varsCard, await sharingCard(name, isOwner), advancedCard(e, async (adv) => {
      await api(`/apps/${enc(name)}/advanced${qs}`, { method: "PUT", body: adv }); toast("Advanced settings saved: they apply from the next start", "ok"); settingsView();
    }), danger);
  }
  /** A pass's conclusion as lines (D701: it is a record, not text): each field on its own line,
      a list one item a line. */
  function conclusionText(c) {
    if (c == null) return "";
    if (typeof c !== "object") return String(c);
    const one = (v) => typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
    return Object.entries(c).filter(([, v]) => v != null && v !== "" && !(Array.isArray(v) && !v.length))
      .map(([k, v]) => Array.isArray(v) ? `${k.replace(/_/g, " ")}:\n${v.map(x => "  - " + one(x)).join("\n")}` : `${k.replace(/_/g, " ")}: ${one(v)}`).join("\n");
  }
  /** The last pass in a line (D699): when it ended, what it concluded, how many measurements
      it took, under the charts where the Overview had room. */
  function lastPass(r) {
    const ps = r.passes || [];
    if (!ps.length) return "";
    const p = ps[ps.length - 1], prev = ps.length > 1 ? ps[ps.length - 2].when : 0;
    const took = (r.rows || []).filter(x => x.when > prev && x.when <= p.when).length;
    return card(`The last pass (${ps.length})`, [h("p", {}, ago(p.when), took ? ` · ${took} measurement(s)` : ""),
      p.conclusion ? h("pre", { class: "val small conclusion" }, conclusionText(p.conclusion)) : "",
      h("div", { class: "form-actions" }, h("button", { class: "small", onclick: () => goTab("Timeline") }, "Where its time went"))]);
  }
  /** The best designs (D696): the decision, then the others by the loop's own order -- accepted
      first, the deepest stage reached, then each objective without a limit in turn. */
  function topDesigns(r, n) {
    const objs = r.objective_list || [], order = r.stages || [];
    // D809: the server's ranking by the loop's own rule; the old key only where none came
    const key = (d) => [d.rank != null ? d.rank : Infinity, d.decision ? 0 : 1, d.verdict === "accepted" ? 0 : 1, -order.indexOf(d.shown),
      ...objs.filter(o => o.goal == null).map(o => { const v = d.numbers[o.metric]; return v == null ? Infinity : o.direction === "minimize" ? v : -v; })];
    const cmp = (a, b) => { const x = key(a), y = key(b); for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1; return 0; };
    const top = (r.designs || []).slice().sort(cmp).slice(0, n);
    if (top.length < 2) return "";
    const ms = [...new Set([...objs.map(o => o.metric), ...(r.metrics || [])])].filter(m => top.some(d => d.numbers[m] != null)).slice(0, 4);
    return h("div", { class: "blk" }, h("h3", {}, `The best ${top.length}`),
      h("table", { class: "list compact best-n" }, h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Design"), h("th", {}, "Stage"), ...ms.map(m => h("th", { class: "num" }, m)))),
        h("tbody", {}, top.map((d, i) => h("tr", { class: `clickable ${d.verdict}`, onclick: () => goTab("Results") },
          h("td", { class: "muted" }, d.decision ? "★" : String(i + 1)), h("td", { class: "mono" }, d.name, d.verdict === "failed" ? h("span", { class: "pill bad small" }, "failed") : ""),
          h("td", { class: "muted" }, d.shown),
          ...ms.map(m => { const ok = d.meets[m]; return h("td", { class: `num mono${ok === true ? " meets" : ok === false ? " misses" : ""}` }, d.numbers[m] == null ? "" : num4(d.numbers[m])); }))))));
  }
  /** The loop's front page (D692): state, designs, the decision against the limits, the best so far
      per objective, the latest notes and the agents' newest workbench entries. */
  async function overview() {
    const [r, notes, bench, use] = await Promise.all([api(`/apps/${enc(name)}/results${qs}`), api(`/apps/${enc(name)}/notes${qs}`).catch(() => []),
      api(`/apps/${enc(name)}/workbench${qs}`).catch(() => []), api(`/apps/${enc(name)}/usage${qs}`).catch(() => null)]);
    const designs = r.designs || [], dec = designs.find(d => d.decision) || null;
    const objs = (r.objective_list || []).slice(0, 2);
    const stat = (label, value, sub, onclick) => h("div", { class: "stat" + (onclick ? " clickable" : ""), onclick },
      h("small", {}, label), h("div", { class: "big" }, value), sub ? h("div", { class: "muted" }, sub) : "");
    const decisionCard = dec ? card("The decision", [
        h("div", { class: "decision-head" }, h("span", { class: "mono strong" }, dec.name), dec.verdict === "accepted" ? h("span", { class: "pill ok" }, "meets the limits") : h("span", { class: "pill bad" }, "misses a limit"),
          h("span", { class: "muted" }, `measured at ${dec.shown}`)),
        // D815: why this one, as the loop said it -- a limit is a floor to meet, the next objective decides among those that meet it
        r.decided_by ? h("p", { class: "small decided-by" }, h("span", { class: "muted" }, "Chosen as "), r.decided_by, ".") : "",
        h("div", { class: "decision-nums" }, (r.metrics || []).filter(m => dec.numbers[m] != null).slice(0, 6).map(m => {
          const lim = (r.limits || []).find(l => l.metric === m), ok = dec.meets[m];
          return h("div", { class: "num-cell" + (ok === false ? " misses" : ok === true ? " meets" : "") }, h("small", {}, m),
            h("div", { class: "big mono" }, num4(dec.numbers[m])), lim ? h("small", { class: "muted" }, `${lim.direction === "maximize" ? "≥" : "≤"} ${lim.goal}${ok === true ? " ✓" : ok === false ? " ✗" : ""}`) : "");
        })),
        dec.why.length ? h("ul", { class: "misses" }, dec.why.map(w => h("li", {}, w))) : "",
        topDesigns(r, 3)],
        { actions: [h("button", { class: "small", onclick: () => goTab("Results") }, "All results")] })
      : card("The decision", [empty(designs.length ? "No decision yet: the loop decides at the end of its first pass." : "No design measured yet."),
          topDesigns(r, 3)]);
    const q0 = st.question;
    if (tab !== "Overview") return;                   // the tab changed while it loaded
    body.replaceChildren(
      h("div", { class: "stats five" },
        stat("State", st.running ? "running" : st.last_active ? (st.failed ? "failed" : st.stopped ? "stopped" : "idle") : "never run",
          st.running ? ["since ", ago(st.since), st.passes != null ? ` · pass ${st.passes + (st.at_rest ? 0 : 1)}` : ""] : st.last_active ? ["last active ", ago(st.last_active)] : "", () => goTab("Live")),
        stat("Designs measured", String(designs.length), `${r.counts ? r.counts.accepted : 0} accepted · ${r.counts ? r.counts.failed : 0} failed`, () => goTab("Results")),
        stat("Passes on record", String((r.passes || []).length), r.passes && r.passes.length ? ["last ", ago(r.passes[r.passes.length - 1].when)] : "", null),
        use ? stat("Models and agents", `${use.total.turns} turn(s)`, [dur(use.total.seconds) || "0s",
          use.total.counted ? ` · ${fmtTok(use.total.tokens_in)} → ${fmtTok(use.total.tokens_out)} tokens` : "",
          use.total.cost_usd ? ` · $${use.total.cost_usd.toFixed(2)}` : ""], () => goTab("Agents")) : "",
        stat("Objective", h("span", { class: "obj-line" }, r.objectives || "—"), "", null)),
      // D757: a failed start says why, in its log's own words, where the loop is opened
      st.failed && (st.error || []).length ? h("section", { class: "card why-failed", role: "alert" }, h("div", { class: "card-head" }, h("h2", {}, "Why it stopped"),
        h("button", { class: "small", onclick: () => goTab("Live", "log") }, "The log")),
        h("pre", { class: "why-lines" }, st.error.join("\n"))) : "",
      q0 && st.running ? h("section", { class: "card ask" }, h("div", { class: "card-head" }, h("h2", {}, "The agent asks"),
        h("button", { class: "small primary", onclick: () => goTab("Live") }, "Answer")), h("pre", { class: "question" }, q0.question)) : "",
      h("div", { class: "grid-2 ov" }, h("div", { class: "col" }, decisionCard,
        // D755: a card with nothing in it is not drawn -- a quiet loop's Overview is its decision and charts
        notes.length ? card("Latest notes", h("div", { class: "notes" }, notes.slice(-5).reverse().map(n => h("div", { class: "note has-bin" },
          h("small", { class: "muted" }, n.by, " · ", ago(n.t)), h("div", {}, n.text),
          mine ? binButton("note", "Remove this note?", "It goes from the page, and from the loop if it has not read it yet; what the loop read already stays in its record.",
            async () => { await api(`/apps/${enc(name)}/notes/${enc(n.id)}${qs}`, { method: "DELETE" }); toast("The note is removed", "ok"); drawBody(); }) : "")))) : "",
        bench.length ? card("Agents' workbench", h("ul", { class: "bench" }, bench.slice(0, 5).map(b => h("li", {},
          h("a", { href: "javascript:void 0", onclick: () => goTab("Files", "workbench") }, b.path.split("/").pop()), h("small", { class: "muted" }, " ", ago(b.mtime)),
          b.first ? h("div", { class: "first" }, b.first) : "")))) : ""),
        h("div", { class: "col" }, card("Best so far", objs.length ? objs.map(o => bestChart(r.rows || [], o, r.passes)) : empty("The objective has no number to chart.")),
          lastPass(r))));
  }

  // D704: an agent writing (or revising) the problem shows on the Overview, followed every 3 s
  let authorTimer = null;
  cleanup.push(() => clearTimeout(authorTimer));
  async function authorBox() {
    const st = await api(`/apps/${enc(name)}/author${qs}`).catch(() => null);
    if (!st || !st.ever) return "";
    clearTimeout(authorTimer);
    if (st.running) authorTimer = setTimeout(async () => {
      if (tab !== "Overview") return;
      const was = body.querySelector(".card.authoring");
      const now = await authorBox();
      if (was && now) was.replaceWith(now);
      if (now && !now.querySelector(".pill.live")) { const fresh = await api(`/apps/${enc(name)}${qs}`).catch(() => null); if (fresh && fresh.document) location.reload(); }
    }, 3000);
    // a document written long ago: no card
    if (!st.running && st.ended && Date.now() / 1000 - st.ended > 3600 * 6) return "";
    return authoringCard(name, st, { onStop: mine ? async () => { toast((await api(`/apps/${enc(name)}/author/stop${qs}`, { method: "POST" })).ok, "ok"); } : null });
  }
  function drawSubs() {
    const o = subsOf(tab), cur = curSub();
    subHolder.replaceChildren(o.length > 1 ? h("div", { class: "subtabs views", role: "tablist" }, o.map(([k, label]) => h("button", { role: "tab", type: "button",
      class: k === cur ? "on" : "", "aria-selected": k === cur ? "true" : "false", onclick: () => { sub = k; mode = ""; setUrl(); drawCrumbs(); drawBody(); } }, label))) : "");
  }
  async function drawBody() {
    drawBanner(); drawSubs();
    if (tab === "Settings") {
      if (curSub() === "problem") { configureInto(body, name, owner, mode, `${appHref(owner, name)}/settings/problem`, { small: true, barHost: subHolder }); return; }
      return settingsView();
    }
    if (tab === "Overview") {
      if (!st.running && !st.last_active) {
        const ab = await authorBox();
        body.replaceChildren(ab, card(null, info.document ? empty("This loop has not run yet.", mine ? act("Start", async () => { if (await startLoop(name, owner)) { await refresh(); goTab("Live"); } }, { cls: "primary" }) : "")
          : empty("This loop has no problem document yet.", mine ? h("a", { class: "btn", href: `${appHref(info.owner, name)}/settings/problem/agent` }, "Have an agent write it") : "")));
        return;
      }
      body.replaceChildren(card(null, skeleton(7)));
      await overview();
      return;
    }
    if (tab === "Live" && !curSub()) {
      if (!st.running && !st.last_active) {
        body.replaceChildren(card(null, empty("This loop has not run yet.", mine ? act("Start", async () => { if (await startLoop(name, owner)) { await refresh(); drawBody(); } }, { cls: "primary" }) : "")));
        return;
      }
      body.replaceChildren(h("div", { class: "live-wrap" },
        h("div", { class: "split" }, card(null, [st.running ? "" : h("p", { class: "muted" }, "Not running: the last start's tree."), live.tree], { cls: "tree-card" }),
          h("div", { class: "side-col" }, card(null, live.detail, { cls: "detail-card" }), liveLog.el, card(null, live.stand, { cls: "stand-card" }))),
        ""));
      live.draw(); liveLog.fill(); composer.update();
    } else if (tab === "Live" && curSub() === "timeline") {
      body.replaceChildren(card(null, skeleton(7)));
      await timelineView();
    } else if (tab === "Live" && curSub() === "log") {
      body.replaceChildren(card(null, log.el, { cls: "log-card" }));
      log.render();
    } else if (tab === "Agents") {
      body.replaceChildren(card(null, skeleton(7)));
      const [{ turns }, use] = await Promise.all([api(`/apps/${enc(name)}/turns${qs}`), api(`/apps/${enc(name)}/usage${qs}`)]);
      const one = h("div", { class: "detail" }, empty("Select a turn to read its prompt, reply and tool calls."));
      const pick = async (t, tr) => {
        for (const x of tr.parentNode.children) x.classList.remove("sel"); tr.classList.add("sel");
        const full = (await api(`/apps/${enc(name)}/turns?k=${t.k}${q}`)).turns[0] || {};
        const nt = full.notes && typeof full.notes === "object" ? full.notes : {};
        const facts = [["kind", full.kind], ["model", full.about || nt.model || full.model], ["server", full.server],
          ["session", full.session ? `${full.session}${full.session_id ? " · " + full.session_id : ""}` : null], ["exit", full.rc],
          ["tokens in", full.tokens_in ?? nt.input_tokens], ["tokens out", full.tokens_out ?? nt.output_tokens], ["from the cache", full.tokens_cached],
          ["cost", full.cost_usd ? `$${Number(full.cost_usd).toFixed(4)}` : null], ["tool calls", full.tool_calls ?? (full.hops || []).length],
          ["prompt", full.prompt_chars ? `${full.prompt_chars} chars` : full.prompt ? `${String(full.prompt).length} chars` : null],
          ["finish", nt.finish], ["schema", nt.schema], ["folder", full.workdir]].filter(([, v]) => v != null && v !== "");
        // D712: what most want first -- the model, the exit, the tokens, the tools; the rest folded
        const MAIN = new Set(["model", "exit", "tokens in", "tokens out", "tool calls", "cost"]);
        const factEl = ([k, v]) => h("div", { class: `fact${k === "exit" && v !== 0 && v !== "0" ? " bad" : ""}` }, h("small", {}, k), h("span", { class: k === "folder" ? "mono small" : "mono" }, String(v)));
        const more = facts.filter(([k]) => !MAIN.has(k));
        one.replaceChildren(h("div", { class: "detail-head" }, h("h2", {}, full.agent || full.model || full.kind), h("span", { class: "muted" }, ago(full.ts), " · ", dur(full.seconds))),
          h("div", { class: "facts" }, facts.filter(([k]) => MAIN.has(k)).map(factEl)),
          more.length ? h("details", { class: "facts-more" }, h("summary", {}, `More: ${more.map(([k]) => k).join(", ")}`), h("div", { class: "facts" }, more.map(factEl))) : "",
          ...(Array.isArray(full.steps) && full.steps.length
            ? [full.error ? h("div", { class: "blk" }, h("h3", {}, "error"), proseBlock(String(full.error))) : "",
               conversation(full.steps, { key: `turn${t.k}` }),
               full.stderr ? h("details", { class: "blk" }, h("summary", {}, "stderr"), proseBlock(String(full.stderr))) : "",
               full.prompt ? h("details", { class: "blk" }, h("summary", {}, `The prompt (${String(full.prompt).length.toLocaleString()} characters)`), proseBlock(String(full.prompt))) : ""]
            : [...["error", "reply", "prompt", "stderr"].filter(k => full[k]).map(k => h("div", { class: "blk" }, h("h3", {}, k), proseBlock(String(full[k])))),
               ...((full.hops || []).length ? [h("h3", {}, "Tool calls"), ...(full.hops || []).map(x => h("pre", { class: "val" }, x))] : [])]));
      };
      const tokOf = (t) => { const n = t.notes && typeof t.notes === "object" ? t.notes : {};
        const i = t.tokens_in ?? n.input_tokens, o = t.tokens_out ?? n.output_tokens;
        return i == null && o == null ? "" : `${fmtTok(i || 0)} → ${fmtTok(o || 0)}`; };
      body.replaceChildren(usageCard(use), h("div", { class: "split" },
        card(null, turns.length ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, "Who"), h("th", {}, "When"), h("th", {}, "Took"),
            h("th", { class: "num", title: "tokens in → out" }, "Tokens"), h("th", { class: "num", title: "tool calls" }, "Tools"), h("th", {}, ""))),
          h("tbody", {}, turns.slice().reverse().map(t => { const tr = h("tr", { class: "clickable", onclick: () => pick(t, tr) },
            h("td", {}, h("span", { class: "strong" }, t.agent || t.model || t.kind),
              h("div", { class: "muted small" }, [t.about || (t.notes && t.notes.model) || "", t.session === "resumed" ? "resumed" : ""].filter(Boolean).join(" · "))),
            h("td", {}, ago(t.ts)), h("td", { class: "muted" }, dur(t.seconds)),
            h("td", { class: "num mono muted" }, tokOf(t)), h("td", { class: "num mono muted" }, t.tool_calls != null ? String(t.tool_calls) : t.hops ? String(t.hops) : ""),
            h("td", {}, t.error ? h("span", { class: "pill bad" }, "error") : t.ok === false ? h("span", { class: "pill bad" }, `exit ${t.rc}`) : h("span", { class: "pill ok" }, "ok"))); return tr; })))
          : empty("No model or agent turn yet.")),
        card(null, one, { cls: "detail-card" })));
    } else if (tab === "Results") {
      body.replaceChildren(card(null, skeleton(7)));
      const r = await api(`/apps/${enc(name)}/results${qs}`);
      if (!r.campaign || !r.designs.length) { body.replaceChildren(card(null, empty("No result yet: a design is a result once a stage measured it."))); return; }
      body.replaceChildren(resultsView(r));
    } else if (tab === "Files" && !curSub()) {
      const files = await api(`/apps/${enc(name)}/files${qs}${showIgnored() ? (qs ? "&" : "?") + "ignored=true" : ""}`);
      body.replaceChildren(h("div", { class: "grid-app" }, card("Files", [fileList(files), mine ? adder() : ""], { cls: "files-card", actions: [ignoredToggle()] }), card(null, viewer, { cls: "viewer-card" })));
      if (info.document) openFile(info.document, false);
    } else if (tab === "Files" && curSub() === "workbench") {
      const bench = await api(`/apps/${enc(name)}/workbench${qs}`).catch(() => []);
      body.replaceChildren(h("div", { class: "grid-app" }, card("Agents' workbench", bench.length
        ? ["tools", "notes", ""].map(kind => {
            const items = bench.filter(b => b.kind === kind || (kind === "" && !["tools", "notes"].includes(b.kind)));
            if (!items.length) return "";
            return h("div", { class: "bench-group" }, h("h3", {}, kind || "other"), h("ul", { class: "bench" }, items.map(b => h("li", {},
              h("a", { href: "javascript:void 0", onclick: () => openFile(b.path, false) }, b.path.split("/").pop()),
              h("small", { class: "muted" }, " ", ago(b.mtime)), b.first ? h("div", { class: "first" }, b.first) : ""))));
          })
        : empty("Empty. The coding agents keep the tools they build and the notes they write here, across starts.")),
        card(null, viewer, { cls: "viewer-card" })));
      viewer.replaceChildren(empty("Select a tool or a note."));
    }
  }
  let beat = 0, busy = false;
  const tick = setInterval(async () => {
    await refresh().catch(() => {});
    // the Overview and the Timeline follow a running loop (D693, D696): once a minute
    if (++beat % 12 === 0 && (tab === "Overview" || (tab === "Live" && curSub() === "timeline")) && st.running && !document.hidden && !busy) {   // every minute (D696)
      busy = true; try { await (tab === "Overview" ? overview() : timelineView()); } catch (_) { /* the next beat */ } finally { busy = false; }
    }
  }, 5000);
  cleanup.push(() => clearInterval(tick));
  pageRefresh = () => refresh();
  drawHead(); drawTabs(); drawBanner(); drawBody();
  show(crumbBar, header, banner, tabBar, subHolder, body, askFab, drawer);
  if (askOpen) setAsk(true);
}

/** The log: follow, wrap, a filter (text or /regex/), problems only, download; the loop's starts to
    pick one from (D692). */
function logView(base, qs) {
  const lines = []; let partial = "", seen = 0;
  const listeners = [];                                   // D697: the Live tab's log follows the same stream
  const MAX = 200000, WRAPPED = 3000;                     // D699: lines kept; with wrap on, the last drawn
  const box = h("div", { class: "logview virt" });
  // D699: only the lines in view are drawn -- a row has one height, so the scroll position says
  // which; a spacer gives the box the full log's height. Wrap on: lines differ in height, and the
  // last WRAPPED are drawn instead.
  const spacer = h("div", { class: "spacer" }), win = h("div", { class: "win" });
  box.append(spacer, win);
  let shown = [], ROW = 0, drawn = "";
  const follow = h("input", { type: "checkbox", checked: true });
  const wrap = h("input", { type: "checkbox", checked: NARROW.matches });   // D754: on a phone a line wraps, never scrolls
  if (wrap.checked) box.classList.add("wrap");
  const problems = h("input", { type: "checkbox" });
  const filter = h("input", { placeholder: "filter (text or /regex/)", class: "filter" });
  const count = h("span", { class: "muted" });
  const startSel = h("select", { class: "starts", title: "Show one start of the loop" });
  const PROBLEM = /\b(error|errors|traceback|exception|failed|failure|refused|did not build|timed out|killed)\b|✗/i;
  const WARN = /\b(warning|nudged|retry|stopping|interrupted|could not)\b/i;
  const GOOD = /\b(ADMITTED|DECISION|passed|decided)\b/;
  const MARK = /^── started (.+?) ──$/;
  const STAMP = /^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d{3} /;   // flux_web.stamp.STAMP_RE (D732)
  const times = h("input", { type: "checkbox" });
  try { times.checked = localStorage.getItem("flux-log-times") === "on"; } catch (_) { /* per browser, when it can */ }
  const timeListeners = [];
  times.addEventListener("change", () => {
    try { localStorage.setItem("flux-log-times", times.checked ? "on" : "off"); } catch (_) { /* per browser */ }
    ROW = 0; render(); for (const f of timeListeners) f();
  });
  const starts = [];                                      // [{n, text}], a line per start
  let startIdx = -1;                                      // -1: every start
  let matcher = null;
  function makeMatcher() {
    const f = filter.value.trim(); matcher = null; filter.classList.remove("bad");
    if (!f) return;
    if (f.length > 2 && f.startsWith("/") && f.lastIndexOf("/") > 0) {
      try { matcher = new RegExp(f.slice(1, f.lastIndexOf("/")), f.slice(f.lastIndexOf("/") + 1) || "i"); } catch (_) { filter.classList.add("bad"); }
    } else { const low = f.toLowerCase(); matcher = { test: (x) => x.toLowerCase().includes(low) }; }
  }
  function inStart(l) {
    if (startIdx < 0 || !starts[startIdx]) return true;
    const from = starts[startIdx].n, to = starts[startIdx + 1] ? starts[startIdx + 1].n : Infinity;
    return l.n >= from && l.n < to;
  }
  const keep = (l) => inStart(l) && (!problems.checked || PROBLEM.test(l.text) || WARN.test(l.text) || MARK.test(l.text)) && (!matcher || matcher.test(l.text));
  function lineEl(l) {
    const cls = MARK.test(l.text) ? "marker" : PROBLEM.test(l.text) ? "bad" : WARN.test(l.text) ? "warn" : GOOD.test(l.text) ? "good" : "";
    return h("div", { class: "ln " + cls, "data-n": String(l.n) }, h("span", { class: "no" }, String(l.n)),
      times.checked ? h("span", { class: "at", title: l.at || "written before times were kept" }, l.at ? l.at.slice(11) : "") : "",
      h("span", { class: "tx" }, l.text || " "));
  }
  function drawStarts() {
    const cur = startSel.value;
    startSel.replaceChildren(h("option", { value: "-1" }, `All starts (${starts.length})`),
      ...starts.map((st, i) => h("option", { value: String(i) }, MARK.exec(st.text)[1])));
    startSel.value = cur && Number(cur) < starts.length ? cur : String(startIdx);
  }
  function rowHeight() {
    if (!ROW && box.isConnected) {
      const probe = lineEl({ n: 1, text: "x" });
      win.append(probe); ROW = probe.getBoundingClientRect().height || 18; probe.remove();
    }
    return ROW || 18;
  }
  function counted() {
    count.textContent = `${shown.length === lines.length ? lines.length : shown.length + " of " + lines.length} line(s)`;
  }
  /** The lines in view, a screen above and below; the same window is not drawn twice. */
  function paint() {
    if (wrap.checked) {
      const tail = shown.slice(-WRAPPED);
      spacer.style.height = "0px"; win.style.transform = "";
      win.replaceChildren(...(shown.length > WRAPPED ? [h("div", { class: "ln more" }, `… ${shown.length - WRAPPED} earlier line(s): turn wrap off to scroll through all, or download the log`)] : []),
        ...tail.map(lineEl));
      drawn = "";
      return;
    }
    const r = rowHeight();
    spacer.style.height = `${shown.length * r}px`;
    const first = Math.max(0, Math.floor(box.scrollTop / r) - 60);
    const last = Math.min(shown.length, Math.ceil((box.scrollTop + box.clientHeight) / r) + 60);
    const key = `${first}:${last}:${shown.length}`;
    if (key === drawn) return;
    drawn = key;
    win.style.transform = `translateY(${first * r}px)`;
    win.replaceChildren(...shown.slice(first, last).map(lineEl));
  }
  let queued = false;
  const later = () => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; paint(); if (follow.checked) toEnd(); }); } };
  const toEnd = () => { box.scrollTop = box.scrollHeight; paint(); };
  function render() {
    shown = lines.filter(keep);
    drawn = "";
    counted();
    later();
  }
  function add(chunk) {
    const parts = (partial + chunk).split("\n"); partial = parts.pop();
    const fresh = parts.map(t => {
      const m = STAMP.exec(t);                              // D732: the run's own time, written by the stamper
      const l = { n: ++seen, text: m ? t.slice(m[0].length) : t, at: m ? m[1] : null };
      lines.push(l); if (MARK.test(l.text)) starts.push(l); return l;
    });
    if (lines.length > MAX) {
      lines.splice(0, lines.length - MAX);
      shown = shown.filter(l => l.n >= lines[0].n);
    }
    if (fresh.some(l => MARK.test(l.text))) drawStarts();
    for (const f of listeners) f(fresh);
    for (const l of fresh) if (keep(l)) shown.push(l);
    counted();
    later();
  }
  box.addEventListener("scroll", () => {                       // scrolling up pauses the follow
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    if (!atEnd && follow.checked) follow.checked = false;
    if (!wrap.checked) paint();
  });
  follow.addEventListener("change", () => { if (follow.checked) toEnd(); });
  wrap.addEventListener("change", () => { box.classList.toggle("wrap", wrap.checked); drawn = ""; later(); });
  problems.addEventListener("change", render);
  startSel.addEventListener("change", () => { startIdx = Number(startSel.value); render(); });
  let t; filter.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { makeMatcher(); render(); }, 150); });
  drawStarts();
  const bar = h("div", { class: "toolbar" }, startSel,
    h("label", { class: "check" }, follow, "follow"), h("label", { class: "check" }, wrap, "wrap"),
    h("label", { class: "check" }, problems, "problems only"), h("label", { class: "check", title: "Each line's time (lines written since D732)" }, times, "times"),
    filter, count, h("a", { class: "btn small", href: `${base}/log/raw${qs}` }, "Download"));
  const pill = streamPill();
  // D759: a day-long run's log opens on its last 2 MB; the earlier lines on asking
  const earlier = h("span", { class: "log-earlier small", hidden: true });
  bar.append(earlier, pill.el);
  const TAIL = 2 << 20;
  const url = (all) => `${base}/log${qs}${qs ? "&" : "?"}tail=${all ? 0 : TAIL}`;
  let es;
  const open = (all) => {
    es = followStream(url(all), "log", add, pill.set, (sk) => {
      earlier.hidden = false;
      earlier.replaceChildren(`${(sk.bytes / 1048576).toFixed(1)} MB of earlier lines not loaded · `,
        h("button", { type: "button", class: "small", onclick: () => { es.close(); lines.length = 0; shown = []; partial = ""; seen = 0;
          starts.length = 0; earlier.hidden = true; drawStarts(); render(); open(true); } }, "Load all"));
    });
  };
  open(false);
  return { el: h("div", {}, bar, box), close: () => es.close(), render: () => { ROW = 0; render(); }, lineEl, recent: (k) => lines.slice(-k), onLines: (f) => listeners.push(f),
           problem: (t) => PROBLEM.test(t), times, onTimes: (f) => timeListeners.push(f) };
}

/** The live task tree: follow the running task, collapse what finished, search; as a tree or as a
    graph (D723), the same tasks, selection and collapse either way. */
function liveTree(base, qs, onQuestion) {
  const LT = window.FluxLoopTree;                   // D752: the tree's building, in looptree.js
  let loadAll = () => {};                           // D759: the passes a window left out
  const mdl = LT.model(), nodes = mdl.nodes, roots = mdl.roots, standings = mdl.standings;
  let selected = null, selLeafKey = null, dirty = true;
  const open = new Map();                 // id -> true/false, what the user chose
  const follow = h("input", { type: "checkbox", checked: true });
  const collapse = h("input", { type: "checkbox", checked: true });
  const search = h("input", { placeholder: "search tasks", class: "filter" });
  const treeBox = h("div", { class: "tree" }), graphBox = h("div", { class: "tgraph" }), detail = h("div", { class: "detail" }), stand = h("div", { class: "standings" });
  function onEvent(e) {
    const r = LT.apply(mdl, e);
    if (r.reset) { open.clear(); selected = null; selLeafKey = null; }
    if (r.question) onQuestion(r.question);
    dirty = true;
  }
  const running = LT.running, failedBelow = LT.failedBelow;
  function followTarget() {                              // the deepest running task, an agent first
    let best = null, bestDepth = -1;
    const walk = (n, d) => {
      if (!running(n)) return;
      const score = d + (String(n.name).startsWith("agent:") ? 100 : 0);
      if (score > bestDepth) { best = n; bestDepth = score; }
      n.kids.forEach(k => walk(k, d + 1));
    };
    roots.forEach(r => walk(r, 0));
    return best;
  }
  function lastEnded() {                                  // at rest: the task that ended last (D696)
    let best = null;
    for (const n of nodes.values()) if (!n.kids.length && n.t1 != null && (!best || n.t1 >= best.t1)) best = n;
    return best;
  }
  function isOpen(n) {
    if (open.has(n.id)) return open.get(n.id);
    if (!collapse.checked) return true;
    return running(n) || failedBelow(n);
  }
  let mode = "tree";
  try { mode = localStorage.getItem("flux-tasks-view") === "graph" ? "graph" : "tree"; } catch (_) { /* per browser, when it can */ }
  const modeBtns = { tree: h("button", { type: "button", class: "small" }, "Tree"), graph: h("button", { type: "button", class: "small" }, "Graph") };
  const setMode = (m) => {
    mode = m;
    try { localStorage.setItem("flux-tasks-view", m); } catch (_) {}
    for (const [k, b] of Object.entries(modeBtns)) { b.classList.toggle("on", k === m); b.setAttribute("aria-pressed", String(k === m)); }
    treeBox.hidden = m !== "tree"; graphBox.hidden = m !== "graph";
    if (typeof collapseLbl !== "undefined") { collapseLbl.hidden = m === "graph"; search.hidden = m === "graph"; }   // the tree's own (D726)
    draw();
  };
  for (const [k, b] of Object.entries(modeBtns)) b.addEventListener("click", () => setMode(k));
  function matches(n, q) { return (n.name + " " + (n.why || "")).toLowerCase().includes(q); }
  function visibleUnder(n, q) { return matches(n, q) || n.kids.some(k => visibleUnder(k, q)); }
  /** The loop as it ran (D739): built in looptree.js (D752); here only drawn and selected in. */
  const within = LT.within, itemTasks = LT.itemTasks, itemHas = LT.itemHas;
  const loopTree = () => LT.build(mdl);
  const boxName = (it) => LT.boxName(it, window.FluxCrafter && window.FluxCrafter.boxTitle);
  const itemRunning = (it) => itemTasks(it).some(running);
  const itemFailed = (it) => itemTasks(it).some(failedBelow);
  function itemMatches(it, q) {
    if (it.title.toLowerCase().includes(q)) return true;
    return it.leaf ? it.tasks.some(v => visibleUnder(v, q)) : it.kids.some(k => itemMatches(k, q));
  }
  function itemSpan(it, now) {
    const ts = itemTasks(it);
    if (!ts.length) return 0;
    const t0 = Math.min(...ts.map(t => t.t0)), t1 = Math.max(...ts.map(t => (t.t1 == null ? now : t.t1)));
    return it.leaf && ts.length > 1 ? ts.reduce((a, t) => a + (t.t1 == null ? now - t.t0 : t.seconds || 0), 0) : t1 - t0;
  }
  let shownItems = [];
  function draw() {
    const now = Date.now() / 1000;
    if (follow.checked) { const t = followTarget() || lastEnded(); if (t) selected = t; }
    const q = search.value.trim().toLowerCase();
    if (mode === "graph") drawGraph(now);
    else drawLoopTree(now, q);
    drawDetail(now);
    drawStandings();
    dirty = false;
  }
  const leafLine = LT.leafLine;
  function drawLoopTree(now, q) {
    const items = loopTree();
    shownItems = items;
    const leafSel = (() => {                                  // the leaf the selection is in
      let hit = null;
      const walk = (it) => { if (it.leaf) { if ((selLeafKey && it.key === selLeafKey && (itemHas(it, selected) || (selected && selected.pseudo))) || (!hit && itemHas(it, selected))) hit = it; } else it.kids.forEach(walk); };
      items.forEach(walk);
      return hit;
    })();
    const row = (it, known = "") => {
      if (q && !itemMatches(it, q)) return "";
      const live = itemRunning(it), bad = !live && itemFailed(it);
      const state = live ? "running" : bad ? "failed" : "done";
      const took = itemSpan(it, now);
      if (it.leaf) {
        const latest = it.tasks.reduce((a, x) => (x.t0 >= a.t0 ? x : a));
        const pick = () => { selected = focusOf(latest); selLeafKey = it.key; follow.checked = false; draw(); };
        return h("div", { class: "tnode" },
          h("div", { class: `node leaf ${state} box-${it.box}${it === leafSel ? " sel" : ""}${q && itemMatches(it, q) ? " hit" : ""}`, tabindex: "0", role: "button",
              onclick: pick, onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } } },
            h("span", { class: "caret" }, ""),
            h("span", { class: "st" }, live ? "●" : bad ? "✗" : "✓"),
            h("span", { class: "nm", title: boxName(it) }, it.title), it.tasks.length > 1 && it.title !== "Setup" ? h("span", { class: "kidsn" }, `×${it.tasks.length}`) : "",
            (() => { const line = leafLine(it).split(" · ").filter(x => x && x !== known).join(" · "); return line ? h("span", { class: "why" }, line) : ""; })(),
            latest.pseudo ? "" : h("span", { class: "dur" }, dur(took))));
      }
      const opened = q ? true : open.has(it.key) ? open.get(it.key) : (!collapse.checked || live || bad || itemHas(it, selected));
      return h("div", { class: "tnode" },
        h("div", { class: `node branch ${state}`, onclick: () => { if (it.earlier) { loadAll(); return; } open.set(it.key, !opened); draw(); },
            title: it.earlier ? "Load every pass of this start" : null },
          h("span", { class: "caret" }, opened ? "▾" : "▸"),
          h("span", { class: "st" }, live ? "●" : bad ? "✗" : "✓"),
          h("span", { class: "nm" }, it.title), it.why ? h("span", { class: "why" }, it.why) : "",
          !opened && !it.earlier ? h("span", { class: "kidsn" }, String(it.kids.length)) : "",
          it.key === "end" || it.earlier ? "" : h("span", { class: "dur" }, dur(took))),
        opened ? h("div", { class: "kids" }, it.kids.map(k => row(k, /^Pass /.test(it.title) ? it.why.split(" · ")[0] : ""))) : "");
    };
    treeBox.replaceChildren(...(items.length ? items.map(row) : [empty("Waiting for the run's first events…")]));
  }
  /** The leaf the detail belongs to, when it has several tasks (D739): each a line to open. */
  function leafOf(n) {
    let hit = null;
    const walk = (it) => { if (it.leaf) { if (!hit && it.tasks.length > 1 && itemHas(it, n) && (!selLeafKey || it.key === selLeafKey)) hit = it; } else it.kids.forEach(walk); };
    shownItems.forEach(walk);
    return hit;
  }
  /** The tasks as the loop's own drawing (D726, after D723): the configurator's diagram of this
      loop's document, read-only, each task placed on its box by its name -- how often the box
      ran, for how long, running or failed; a box selects its latest task. */
  const boxOfTask = LT.boxOf;
  let drawingHandle = null, drawingTried = false, latestOf = {}, visits = [];
  // D727: a bar to go through the boxes' visits in order -- the drawing says which box, the
  // detail panel what it did; following keeps it on the newest
  const stepRange = h("input", { type: "range", min: "0", max: "0", value: "0", class: "step-range", "aria-label": "Step" });
  const stepSaid = h("span", { class: "step-said muted small" });
  let runs = [];                                   // D728: the selected box's runs (every visit when none is)
  const goStep = (k) => {
    if (!runs.length) return;
    k = Math.max(0, Math.min(runs.length - 1, k));
    selected = focusOf(runs[k]); follow.checked = false; draw();
  };
  const stepAt = () => { const k = runs.indexOf(visitOf(selected)); return k < 0 ? runs.length - 1 : k; };
  const stepBtn = (label, title, to) => h("button", { type: "button", class: "small", title, "aria-label": title, onclick: () => goStep(to()) }, label);
  const stepBar = h("div", { class: "step-bar" },
    stepBtn("⏮", "The first step", () => 0), stepBtn("◀", "The step before", () => stepAt() - 1),
    stepRange, stepBtn("▶", "The step after", () => stepAt() + 1), stepBtn("⏭", "The newest step", () => runs.length - 1), stepSaid);
  stepRange.addEventListener("input", () => goStep(Number(stepRange.value)));
  stepBar.addEventListener("keydown", (e) => {
    if (e.target === stepRange) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); goStep(stepAt() - 1); } else if (e.key === "ArrowRight") { e.preventDefault(); goStep(stepAt() + 1); }
  });
  const focusOf = LT.focusOf, visitOf = LT.visitOf;
  /** The selected run's own tasks (D730), top to bottom as an indented tree under the step bar --
      it fits the column: a line per task (state, its whole name, what for, time), same-named
      siblings past three grouped as one ("tool:python3 ×6", opening the latest); a click opens
      a task in the detail. */
  const runBox = h("div", { class: "run-graph" });
  function drawRunGraph(run, now) {
    if (!run) { runBox.replaceChildren(); return; }
    const MAX = 80;
    let count = 0;
    const group = (kids) => {
      const out = [], by = new Map();
      for (const k of kids) { const key = String(k.name); if (!by.has(key)) { by.set(key, []); out.push(key); } by.get(key).push(k); }
      return out.flatMap(key => { const xs = by.get(key); return xs.length > 3 ? [{ many: xs }] : xs.map(n => ({ n })); });
    };
    const rows = [];
    const lay = (it, depth, last) => {
      if (count >= MAX) return;
      count++;
      const xs = it.many || [it.n], n = it.many ? xs.reduce((a, x) => (x.t0 >= a.t0 ? x : a)) : it.n;
      const live = xs.some(running), failed = xs.filter(x => x.failed).length;
      const state = live ? "running" : failed ? "failed" : "done";
      const took = live ? `running · ${dur(now - n.t0)}` : dur(xs.reduce((t, x) => t + (x.seconds || 0), 0));
      const pick = () => { selected = n; follow.checked = false; draw(); };
      rows.push(h("div", { class: `rg-row ${state}${xs.includes(selected) ? " sel" : ""}`, style: `--d:${depth}`, tabindex: "0", role: "button",
          onclick: pick, onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } } },
        depth ? h("span", { class: "rg-branch", "aria-hidden": "true" }, last ? "└" : "├") : "",
        h("span", { class: "rg-st" }, live ? "●" : failed ? "✗" : "✓"),
        h("span", { class: "rg-nm" }, String(n.name) + (it.many ? ` ×${xs.length}` : "")),
        n.why && !it.many ? h("span", { class: "rg-why" }, n.why) : "",
        failed && it.many ? h("span", { class: "rg-why bad" }, `${failed} failed`) : "",
        h("span", { class: "rg-dur" }, took)));
      if (!it.many) { const ks = group(n.kids); ks.forEach((k, i) => lay(k, depth + 1, i === ks.length - 1)); }
    };
    lay({ n: run }, 0, true);
    runBox.replaceChildren(h("div", { class: "run-graph-head muted small" }, "This run's tasks", count >= MAX ? ` (the first ${MAX})` : ""), h("div", { class: "run-graph-rows" }, rows));
  }
  async function loadDrawing() {
    drawingTried = true;
    const C = window.FluxCrafter;
    if (!C) { graphBox.replaceChildren(empty("The configurator's script did not load.")); return; }
    try {
      if (!crafterCatalog) { crafterCatalog = await fetch("/crafter-assets/tools.json").then(r => r.json()).catch(() => []); C.setCatalog(crafterCatalog); }
      const v = await fetch(`${base}/document${qs}`, { credentials: "same-origin" }).then(r => r.json());
      if (!v.raw) { graphBox.replaceChildren(empty("The loop's document cannot be drawn: " + (v.error || "there is none."))); return; }
      const host = h("div", { class: "flux-crafter tasks-drawing" });
      graphBox.replaceChildren(stepBar, runBox, host);
      drawingHandle = C.mount(host, true, { state: C.fromDoc(v.raw, v.normal || v.raw).state, activity: {},
        onBox: (id) => { if (latestOf[id]) { selected = focusOf(latestOf[id]); follow.checked = false; draw(); } } });
      draw();
    } catch (x) { graphBox.replaceChildren(empty("The loop's drawing could not be made: " + x.message)); }
  }
  function drawGraph(now) {
    if (!drawingHandle) { if (!drawingTried) { graphBox.replaceChildren(skeleton(6)); loadDrawing(); } return; }
    latestOf = {};
    visits = [];
    const used = {};
    for (const n of nodes.values()) {
      const b = boxOfTask(n);
      if (!b || (n.parent && boxOfTask(n.parent) === b)) continue;      // one visit per stretch of a box, not per sub-task
      visits.push(n);
      (used[b] || (used[b] = [])).push(n);
      if (!latestOf[b] || n.t0 >= latestOf[b].t0) latestOf[b] = n;
    }
    visits.sort((a, b) => a.t0 - b.t0 || a.id - b.id);
    const cur = visitOf(selected);
    const curBox = cur ? boxOfTask(cur) : null;
    const activity = {};
    for (const [b, list] of Object.entries(used)) {                      // a box this start used; its state only where it is now
      const live = list.some(running);
      activity[b] = { state: live ? "running" : b === curBox && cur.failed ? "failed" : "done", sel: b === curBox,
        title: `latest: ${latestOf[b].name}${latestOf[b].why ? " — " + latestOf[b].why : ""}` };
    }
    drawingHandle.setActivity(activity);
    drawRunGraph(curBox ? cur : null, now);
    runs = curBox ? used[curBox] || [] : visits;
    runs.sort((a, b) => a.t0 - b.t0 || a.id - b.id);
    const r = cur ? runs.indexOf(cur) : -1;
    stepRange.max = String(Math.max(runs.length - 1, 0));
    if (document.activeElement !== stepRange) stepRange.value = String(r < 0 ? Math.max(runs.length - 1, 0) : r);
    stepRange.disabled = runs.length < 2;
    const box = curBox && ((window.FluxCrafter && window.FluxCrafter.boxTitle && window.FluxCrafter.boxTitle(curBox)) || curBox);
    const shown = selected && cur ? selected : cur;                      // the task the detail shows, inside the run
    stepSaid.textContent = !visits.length ? "No step yet." : !curBox ? `${visits.length} steps: select a box, or step through them all` :
      `${box} · run ${r + 1} of ${runs.length} · ${shown.name}${shown.why ? " — " + shown.why : ""} · ${running(shown) ? "running" : shown.failed ? "failed" : dur(shown.seconds)}`;
  }
  /** The loop's standings (D418l) as a reader wants them: a line of counts, the frontier and the
      parts as small tables, anything else as short key/value lines. */
  function drawStandings() {
    const short = (v) => { const t = typeof v === "string" ? v : JSON.stringify(v); return t.length > 90 ? t.slice(0, 90) + "…" : t; };
    const num = (v) => typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toPrecision(5)) : short(v);
    const out = [];
    for (const [key, v] of standings) {
      if (!v || typeof v !== "object" || Array.isArray(v)) { out.push(h("div", { class: "kv" }, h("div", { class: "k" }, key), h("div", { class: "mono" }, short(v)))); continue; }
      const counts = [["step", v.step != null ? `${v.step}${v.steps ? "/" + v.steps : ""}` : null], ["judged", v.judged],
        ["proven", v.proven != null ? `${v.proven}${v.parts_total ? "/" + v.parts_total : ""}` : null], ["measured", v.measured], ["at", v.at]]
        .filter(([, x]) => x != null && x !== "");
      if (counts.length) out.push(h("div", { class: "stats" }, counts.map(([k, x]) => h("span", {}, h("small", {}, k), " ", h("strong", {}, String(x))))));
      const axes = Array.isArray(v.axes) ? v.axes : ["x", "y"];
      if (Array.isArray(v.front) && v.front.length) out.push(h("h3", {}, "Frontier"), h("table", { class: "list compact" },
        h("thead", {}, h("tr", {}, h("th", {}, "design"), h("th", {}, "stage"), h("th", { class: "num" }, axes[0]), h("th", { class: "num" }, axes[1] || "y"))),
        h("tbody", {}, v.front.slice(0, 12).map(f => h("tr", {}, h("td", { class: "mono" }, f.name, f.decision ? h("span", { class: "pill ok" }, "decision") : ""),
          h("td", {}, f.stage || ""), h("td", { class: "mono num" }, num(f.x)), h("td", { class: "mono num" }, num(f.y)))))));
      if (Array.isArray(v.parts) && v.parts.length) out.push(h("h3", {}, "Parts"), h("table", { class: "list compact" },
        h("tbody", {}, v.parts.slice(0, 20).map(p => h("tr", {}, h("td", { class: "mono" }, p.part || ""),
          h("td", {}, h("span", { class: `pill ${p.state === "proven" ? "ok" : p.state === "refused" ? "bad" : ""}` }, p.state || "")),
          h("td", { class: "mono muted" }, p.name || ""))))));
      // D699: the rest as a reader wants it -- plain values as chips, lists of records as tables
      const shown = new Set(["step", "steps", "judged", "proven", "parts_total", "measured", "at", "axes", "front", "parts"]);
      const rest = Object.entries(v).filter(([k]) => !shown.has(k));
      const plain = rest.filter(([, x]) => x == null || typeof x !== "object");
      if (plain.length) out.push(h("div", { class: "chips-kv" }, plain.map(([k, x]) => h("span", { class: "kvchip" }, h("small", {}, k.replace(/_/g, " ")), " ",
        h("strong", { class: "mono" }, x === true ? "yes" : x === false ? "no" : x == null ? "—" : num(x))))));
      for (const [k, x] of rest.filter(([, x]) => x != null && typeof x === "object")) out.push(h("h3", {}, k.replace(/_/g, " ")), valueView(x));
    }
    stand.replaceChildren(...(out.length ? [h("h2", {}, "Standings"), ...out] : [h("p", { class: "muted" }, "No standings yet.")]));
  }
  /** Any value of the standings, readable (D699): a list of records is a table (a record's own
      numbers become columns), a record of plain values is chips, a list of plain values a line. */
  function valueView(x) {
    const short = (v) => { const t = typeof v === "string" ? v : JSON.stringify(v); return t.length > 70 ? t.slice(0, 70) + "…" : t; };
    const cell = (v) => v == null ? "" : typeof v === "number" ? (Number.isInteger(v) ? String(v) : Number(v.toPrecision(5)).toString()) : typeof v === "boolean" ? (v ? "yes" : "no") : short(v);
    const isRec = (v) => v && typeof v === "object" && !Array.isArray(v);
    if (Array.isArray(x)) {
      if (!x.length) return h("p", { class: "muted small" }, "none");
      if (!x.every(isRec)) return h("p", { class: "mono small" }, x.map(cell).join(", "));
      const flat = x.map(r => { const o = {}; for (const [k, v] of Object.entries(r)) {
        if (isRec(v) && Object.values(v).every(y => y == null || typeof y !== "object")) Object.assign(o, v);   // numbers: {time_ms: …} -> columns
        else if (!(typeof v === "string" && v.length > 160)) o[k] = v;                                             // an artifact's text: not here
      } return o; });
      const cols = [...new Set(flat.flatMap(Object.keys))].slice(0, 8);
      return h("div", { class: "scroll-x" }, h("table", { class: "list compact stand-t" },
        h("thead", {}, h("tr", {}, cols.map(c => h("th", { class: flat.some(r => typeof r[c] === "number") ? "num" : "" }, c.replace(/_/g, " "))))),
        h("tbody", {}, flat.slice(0, 15).map(r => h("tr", {}, cols.map(c => h("td", { class: typeof r[c] === "number" ? "num mono" : "mono", title: typeof r[c] === "string" && r[c].length > 70 ? r[c] : null }, cell(r[c])))))),
        x.length > 15 ? h("tfoot", {}, h("tr", {}, h("td", { colspan: cols.length, class: "muted" }, `and ${x.length - 15} more`))) : ""));
    }
    if (isRec(x)) {
      const entries = Object.entries(x);
      if (entries.every(([, v]) => v == null || typeof v !== "object"))
        return h("div", { class: "chips-kv" }, entries.map(([k, v]) => h("span", { class: "kvchip" }, h("small", {}, k.replace(/_/g, " ")), " ", h("span", { class: "mono" }, cell(v)))));
      return h("div", { class: "nested" }, entries.map(([k, v]) => h("div", { class: "kv" }, h("div", { class: "k" }, k.replace(/_/g, " ")), valueView(v))));
    }
    return h("span", { class: "mono" }, cell(x));
  }
  /** A coding agent at work (D702): its model, status and output as facts; its thinking, its
      commands, the last command's output and its words, each a stream that keeps its place
      when read upward and follows its end otherwise. */
  function agentView(n, now) {
    const f = { ...(running(n) ? {} : (n.output || {})), ...(n.fields || {}) };
    const facts = [["model", f.agent], ["status", f.status], ["output", f.output], ["rate limit", f["rate limit"]],
      ["exit", f.exit], ["took", dur(running(n) ? now - n.t0 : n.seconds)]].filter(([, v]) => v != null && v !== "");
    const stream = (key, title, text, cls = "") => text ? h("section", { class: `astream ${cls}` }, h("h3", {}, title),
      h("pre", { class: "val astream-body", "data-k": key }, text)) : "";
    const tools = String(f["tool calls"] || "").split("\n").filter(Boolean);
    const thinking = f["thinking (live tail)"] || f.thinking || "";
    const steps = Array.isArray(f.steps) ? f.steps : null;
    if (steps) {                                       // D712: one conversation, in order
      const total = Number(f["steps total"] || steps.length);
      // D731: its words first -- the last text it said, readable without scrolling through its work;
      // the conversation below without that last text, and without a scroll of its own
      const lastText = [...steps].reverse().find(st => st.k === "text" && String(st.text || "").trim());
      const ends = lastText && steps[steps.length - 1] === lastText;
      const said = lastText ? h("section", { class: "agent-reply" }, h("h3", {}, running(n) ? "Its latest words" : "Its reply"),
        h("div", { class: "cv-text" }, markdown(String(lastText.text).trim()))) : "";
      return h("div", { class: "agent-view" },
        h("div", { class: "facts" }, facts.map(([k, v]) => h("div", { class: "fact" }, h("small", {}, k), h("span", { class: "mono" }, String(v))))),
        said,
        h("h3", { class: "cv-title" }, "What it did"),
        conversation(ends ? steps.slice(0, -1) : steps, { key: `task${n.id}`, offset: Math.max(0, total - steps.length), live: running(n) }),
        stream("stderr", "stderr", f.stderr, "err"));
    }
    return h("div", { class: "agent-view" },
      h("div", { class: "facts" }, facts.map(([k, v]) => h("div", { class: "fact" }, h("small", {}, k), h("span", { class: "mono" }, String(v))))),
      stream("thinking", "Thinking", thinking, "think"),
      tools.length ? h("section", { class: "astream" }, h("h3", {}, `Commands (${tools.length >= 8 ? "the last 8" : tools.length})`),
        h("ol", { class: "acmds" }, tools.map(t => { const m = /^(\d+)\.\s*(.*)$/.exec(t); return h("li", { value: m ? m[1] : null }, h("code", {}, m ? m[2] : t)); }))) : "",
      stream("result", "The last command's output", f["last tool output"]),
      stream("reply", "Its words", f["reply (live tail)"], "reply"),
      stream("stderr", "stderr", f.stderr, "err"),
      !thinking && !tools.length && !f["reply (live tail)"] ? h("p", { class: "muted" }, running(n) ? "Nothing from the agent yet: it is starting, or thinking without saying." : "The agent said nothing the page could show.") : "");
  }
  /** A tool at work (D709): its command, folder and exit, and the ends of its stdout and
      stderr -- live while it runs, kept when it ends. */
  function toolView(n, now) {
    const f = { ...(n.fields || {}), ...(running(n) ? {} : (n.output || {})) }, p = n.params || {};
    const out = f.stdout ?? f["stdout (live tail)"] ?? "", err = f.stderr ?? f["stderr (live tail)"] ?? "";
    const exit = running(n) ? null : f.exit;
    const facts = [["exit", exit], ["took", dur(running(n) ? now - n.t0 : n.seconds)], ["folder", p.folder]]
      .filter(([, v]) => v != null && v !== "");
    const stream = (key, title, text, cls = "") => text ? h("section", { class: `astream ${cls}` }, h("h3", {}, title),
      h("pre", { class: "val astream-body", "data-k": key }, text)) : "";
    return h("div", { class: "agent-view" },
      h("div", { class: "facts" }, facts.map(([k, v]) => h("div", { class: `fact${k === "exit" && v !== 0 ? " bad" : ""}` }, h("small", {}, k), h("span", { class: "mono" }, String(v))))),
      p.command ? h("section", { class: "astream" }, h("h3", {}, "Command"), h("pre", { class: "val mono" }, p.command)) : "",
      stream("stdout", running(n) ? "stdout, so far" : "stdout", out),
      stream("stderr", running(n) ? "stderr, so far" : "stderr", err, "err"),
      !out && !err ? h("p", { class: "muted" }, running(n) ? "Nothing printed yet." : p.command ? "It printed nothing." : "This run of the tool was recorded without its command and output.") : "");
  }
  let detailTab = "";
  function drawDetail(now) {
    if (!selected) { detail.replaceChildren(empty("Select a task to see its parameters, live fields and output.")); return; }
    const n = selected;
    // D702: a stream read upward keeps its place across the redraw each second
    const kept = new Map([...detail.querySelectorAll("pre[data-k], .cv[data-k]")].map(p => [p.dataset.k, p.scrollTop + p.clientHeight >= p.scrollHeight - 8 ? -1 : p.scrollTop]));
    const sameTask = detail.dataset.task === String(n.id);
    const panelTop = detail.scrollTop, panelAtEnd = detail.scrollTop + detail.clientHeight >= detail.scrollHeight - 12;
    detail.dataset.task = String(n.id);
    const block = (title, obj) => obj && Object.keys(obj).length ? h("div", { class: "blk" }, h("h3", {}, title), Object.entries(obj).map(([k, v]) => {
      const text = typeof v === "string" ? v : JSON.stringify(v, null, 1);
      const long = text.length > 120 || text.includes("\n");
      return h("div", { class: "kv" }, h("div", { class: "k" }, k), long ? h("pre", { class: "val" }, text) : h("div", { class: "val mono" }, text));
    })) : "";
    const path = []; for (let p = n.parent; p; p = p.parent) path.unshift(p.name);
    // D739: what a task was given, what it gave, its log, and what it does now -- each a tab,
    // the tabs it has; the one chosen stays chosen from task to task
    const isAgent = String(n.name).startsWith("agent:"), isTool = String(n.name).startsWith("tool:");
    const has = (o) => o && Object.keys(o).length;
    const f = { ...(n.fields || {}), ...(running(n) ? {} : (n.output || {})) };
    const logText = [f.stdout ?? f["stdout (live tail)"], f.stderr ?? f["stderr (live tail)"]].filter(Boolean).join("\n");
    const tabs = [];
    if (isAgent) tabs.push([running(n) ? "Live" : "Conversation", () => agentView(n, now)]);
    else if (isTool) tabs.push([running(n) ? "Live" : "Output", () => toolView(n, now)]);
    else {
      if (running(n) && has(n.fields)) tabs.push(["Live", () => block("So far", n.fields)]);
      if (has(n.output)) tabs.push(["Output", () => block("Output", n.output)]);
    }
    if (has(n.params)) tabs.push(["Input", () => block("Given", n.params)]);
    if (isAgent && logText) tabs.push(["Log", () => h("pre", { class: "val astream-body", "data-k": "log" }, logText)]);
    if ((isAgent || isTool) && (has(n.fields) || has(n.output))) tabs.push(["Every field", () => h("div", {}, block("Fields", n.fields), block("Output", n.output))]);
    const want = tabs.find(([t]) => t === detailTab) || tabs.find(([t]) => (detailTab === "Live" && t === "Output") || (detailTab === "Output" && t === "Live") || (detailTab === "Conversation" && t === "Live")) || tabs[0];
    const tabBar = tabs.length > 1 ? h("div", { class: "dtabs", role: "tablist" }, tabs.map(([t]) => h("button", { type: "button", class: `small${want && t === want[0] ? " on" : ""}`, role: "tab",
      "aria-selected": String(!!want && t === want[0]), onclick: () => { detailTab = t; drawDetail(Date.now() / 1000); } }, t))) : "";
    const leaf = n.pseudo ? null : leafOf(n);
    const leafRows = leaf ? h("div", { class: "leaf-tasks" }, h("h3", {}, `${boxName(leaf)} ×${leaf.tasks.length}`),
      h("div", { class: "run-graph-rows" }, leaf.tasks.map(v => {
        const fo = focusOf(v), on = within(v, n);
        return h("div", { class: `rg-row ${running(v) ? "running" : failedBelow(v) ? "failed" : "done"}${on ? " sel" : ""}`, tabindex: "0", role: "button",
          onclick: () => { selected = fo; follow.checked = false; draw(); },
          onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selected = fo; follow.checked = false; draw(); } } },
          h("span", { class: "rg-st" }, running(v) ? "●" : failedBelow(v) ? "✗" : "✓"),
          h("span", { class: "rg-nm" }, String(fo.name)), fo.why ? h("span", { class: "rg-why" }, fo.why) : "",
          h("span", { class: "rg-dur" }, running(v) ? `running · ${dur(now - v.t0)}` : dur(v.seconds)));
      }))) : "";
    const step = n.pseudo ? null : (() => {         // the leaf whose own visit holds the task, not one above it
      const v = visitOf(n); let hit = null, near = null;
      const walk = (it) => { if (it.leaf) { if (!hit && v && it.tasks.includes(v)) hit = it; if (!near && itemHas(it, n)) near = it; } else it.kids.forEach(walk); };
      shownItems.forEach(walk); return hit || near;
    })();
    detail.replaceChildren(
      h("div", { class: "detail-head" }, h("h2", {}, step ? boxName(step) : n.name), step ? h("span", { class: "mono muted small" }, n.name) : "",
        n.pseudo ? "" : h("span", { class: `pill ${running(n) ? "live" : n.failed ? "bad" : "ok"}` }, running(n) ? "running" : n.failed ? "failed" : "done"),
        n.pseudo ? "" : h("span", { class: "muted" }, dur(running(n) ? now - n.t0 : n.seconds))),
      path.length ? h("p", { class: "crumbs" }, path.join(" › ")) : "",
      n.why ? h("p", { class: "muted" }, n.why) : "",
      leafRows, tabBar, want ? want[1]() : h("p", { class: "muted" }, running(n) ? "Nothing from it yet." : "It recorded nothing more."));
    for (const pre of detail.querySelectorAll("pre.val, .cv[data-k]")) {
      const k = pre.dataset.k, at = sameTask && k ? kept.get(k) : undefined;
      pre.scrollTop = at === undefined || at === -1 ? pre.scrollHeight : at;   // a live tail shows its end, unless read upward
    }
    // D731: the panel's own place -- kept across the redraw; a running task read at its end stays at the end
    if (sameTask) detail.scrollTop = panelAtEnd && running(n) ? detail.scrollHeight : panelTop;
    else detail.scrollTop = 0;
  }
  search.addEventListener("input", draw);
  follow.addEventListener("change", draw);
  collapse.addEventListener("change", () => { open.clear(); draw(); });
  const pill = streamPill();
  // D759: a day-long run's tree opens on its last 30 passes; "Earlier" loads the rest
  const WINDOW = 30;
  let es = followStream(`${base}/events${qs}${qs ? "&" : "?"}window=${WINDOW}`, "events", onEvent, pill.set);
  // D761: what runs now -- its live fields, the standings -- from live.json, whole each time it changes
  const liveEs = new EventSource(`${base}/live${qs}`);
  let lastLive = null;
  liveEs.addEventListener("live", (m) => { try { lastLive = JSON.parse(m.data); LT.applyLive(mdl, lastLive); dirty = true; } catch (_) {} });
  loadAll = () => {
    es.close();
    LT.apply(mdl, { ev: "hello" }); open.clear(); selected = null; selLeafKey = null; dirty = true;
    es = followStream(`${base}/events${qs}`, "events", onEvent, (st) => { pill.set(st); if (lastLive) LT.applyLive(mdl, lastLive); });
  };
  const tick = setInterval(() => { if (dirty || [...nodes.values()].some(running)) draw(); }, 1000);
  const collapseLbl = h("label", { class: "check" }, collapse, "collapse finished");
  const bar = h("div", { class: "toolbar" }, h("div", { class: "seg", role: "group", "aria-label": "View" }, modeBtns.tree, modeBtns.graph),
    h("label", { class: "check" }, follow, "follow the running task"),
    collapseLbl, search, pill.el);
  setMode(mode);
  return { tree: h("div", {}, bar, treeBox, graphBox), detail, stand, draw, close: () => { es.close(); liveEs.close(); clearInterval(tick); } };
}

// ================================================================ the configurator (D686)
let crafterCatalog = null;
/** A line diff (D693): the longest common subsequence of lines, as [op, text] with op " ", "-"
    or "+". The ends every document shares are cut off before the table, so it stays small. */
function lineDiff(a, b) {
  const A = String(a).split("\n"), B = String(b).split("\n");
  let s = 0; while (s < A.length && s < B.length && A[s] === B[s]) s++;
  let e = 0; while (e < A.length - s && e < B.length - s && A[A.length - 1 - e] === B[B.length - 1 - e]) e++;
  const a2 = A.slice(s, A.length - e), b2 = B.slice(s, B.length - e), n = a2.length, m = b2.length;
  const L = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = a2[i] === b2[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const mid = []; let i = 0, j = 0;
  while (i < n && j < m) {
    if (a2[i] === b2[j]) { mid.push([" ", a2[i]]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) mid.push(["-", a2[i++]]);
    else mid.push(["+", b2[j++]]);
  }
  while (i < n) mid.push(["-", a2[i++]]);
  while (j < m) mid.push(["+", b2[j++]]);
  return [...A.slice(0, s).map(t => [" ", t]), ...mid, ...A.slice(A.length - e).map(t => [" ", t])];
}
/** The changes with three lines of context each, the rest folded. */
function diffView(ops, context = 3) {
  const keep = ops.map(() => false);
  ops.forEach((o, k) => { if (o[0] !== " ") for (let d = -context; d <= context; d++) if (ops[k + d]) keep[k + d] = true; });
  const pre = h("pre", { class: "diff" });
  let a = 0, b = 0, folded = 0;
  const fold = () => { if (folded) { pre.append(h("div", { class: "d-fold" }, `⋯ ${folded} unchanged line(s)`)); folded = 0; } };
  ops.forEach((o, k) => {
    if (o[0] !== "+") a++; if (o[0] !== "-") b++;
    if (!keep[k]) { folded++; return; }
    fold();
    pre.append(h("div", { class: o[0] === "+" ? "d-add" : o[0] === "-" ? "d-del" : "d-ctx" },
      h("span", { class: "d-no" }, o[0] === "+" ? "" : String(a)), h("span", { class: "d-no" }, o[0] === "-" ? "" : String(b)),
      h("span", { class: "d-op" }, o[0]), o[1] || " "));
  });
  fold();
  return pre;
}

/** The files that go with a loop's document (D696): scripts, golden models, specs. For a loop
    that exists, its own files, edited in place; for a new one, files kept here until it is
    created. Each file the document names as `{home}/…` and nobody has is said to be missing. */
function filesPanel(name, yamlOf) {
  const staged = new Map();                                   // a new loop: path -> {text} | {file}
  const box = h("div", { class: "files-panel" });
  const into = h("input", { placeholder: "folder (optional)", class: "narrow-in" });
  const named = () => [...new Set([...String(yamlOf() || "").matchAll(/\{home\}\/([\w.\/-]+)/g)].map(m => m[1].replace(/[.,;:)]+$/, "")))];
  async function list() {
    if (!name) return [...staged.entries()].map(([path, x]) => ({ path, size: x.text != null ? x.text.length : x.file.size, staged: true }));
    return (await api(`/apps/${enc(name)}/inputs`)).filter(f => !f.document && !f.ignored);   // D703: .gitignore followed
  }
  async function editor(path, text) {
    const pathIn = h("input", { value: path || "", placeholder: "check.py, scripts/bench.sh", style: "width:100%", readonly: path ? true : null });
    const ed = codeEditor(text || "", langOf(path || ""));
    pathIn.addEventListener("input", () => { /* the language follows the name on the next open */ });
    const ok = await dialog(path ? `Edit ${path}` : "A new file", h("div", { class: "file-edit" }, h("label", { class: "stack" }, "Path in the loop", pathIn), ed.el),
      [["Cancel", false], ["Save", true, "primary"]]);
    if (!ok) return;
    const p = pathIn.value.trim();
    if (!p) { toast("Name the file.", "warn"); return; }
    if (name) { await api(`/apps/${enc(name)}/file?path=${enc(p)}`, { method: "PUT", body: { text: ed.textarea.value } }); toast(`${p} saved`, "ok"); }
    else staged.set(p, { text: ed.textarea.value });
    draw();
  }
  async function open(f) {
    if (!name) { const x = staged.get(f.path); if (x.text != null) return editor(f.path, x.text); toast("A dropped file is kept as it is.", "info"); return; }
    const r = await fetch(`/api/apps/${enc(name)}/file?path=${enc(f.path)}`, { credentials: "same-origin" });
    if (!(r.headers.get("content-type") || "").startsWith("text/")) { toast("A binary file: replace it by dropping a new one.", "info"); return; }
    editor(f.path, await r.text());
  }
  async function remove(f) {
    if (!name) { staged.delete(f.path); draw(); return; }
    if (!await confirmDialog(`Delete ${f.path}?`, "The file goes from the loop; the document may still name it.", { ok: "Delete", danger: true })) return;
    await api(`/apps/${enc(name)}/file?path=${enc(f.path)}`, { method: "DELETE" }); toast(`${f.path} deleted`, "ok"); draw();
  }
  const dz = dropZone("Drop scripts, models or folders here", async (got) => {
    const pre = into.value.trim().replace(/^\/+|\/+$/g, "");
    if (!name) { for (const g of got) staged.set(pre ? `${pre}/${g.path}` : g.path, { file: g.file }); draw(); return; }
    const pd = progressDialog("Adding files", `${got.length} file(s)`);
    try { const n = await sendFiles(name, got, { folder: pre, onProgress: pd.set, signal: pd.signal }); toast(`Added ${n} file(s)`, "ok"); }
    catch (x) { toast(pd.signal.aborted ? `The upload was cancelled: ${x.message}.` : `The upload failed: ${x.message}`, pd.signal.aborted ? "warn" : "bad", { timeout: 12000 }); }
    finally { pd.close(); draw(); }
  });
  async function draw() {
    const files = await list().catch(() => []);
    const have = new Set(files.map(f => f.path));
    const missing = named().filter(p => !have.has(p));
    box.replaceChildren(
      files.length ? h("ul", { class: "files flist" }, files.map(f => h("li", {},
        h("a", { href: "javascript:void 0", onclick: () => open(f) }, h("span", { class: "ic" }, "·"), f.path),
        h("small", { class: "muted" }, f.size < 1024 ? `${f.size} B` : `${(f.size / 1024).toFixed(1)} KB`, f.staged ? " · with the new loop" : ""),
        h("button", { class: "link danger-link", title: `Delete ${f.path}`, onclick: () => remove(f) }, "×")))) : h("p", { class: "muted" }, "No file beside the document yet."),
      missing.length ? h("div", { class: "callout bad" }, h("strong", {}, "The document names these, and the loop does not have them: "),
        missing.map((p, i) => [i ? ", " : "", h("a", { href: "javascript:void 0", title: "Write it here", onclick: () => editor(p, "") }, p)])) : "",
      h("div", { class: "row" }, h("button", { class: "small", type: "button", onclick: () => editor("", "") }, "New file"), into), dz);
  }
  let t;
  const watch = () => { clearTimeout(t); t = setTimeout(draw, 600); };
  draw();
  return { el: card("Files that go with it", box, { cls: "files-card" }), watch, draw,
    async upload(appName) {                                  // a new loop: its files, once it exists
      if (!staged.size) return 0;
      return sendFiles(appName, [...staged].map(([p, x]) => ({ file: x.file || new File([x.text], p.split("/").pop(), { type: "text/plain" }), path: p })));
    } };
}

/** Make or change a loop's problem, three ways (D704). New: the configurator, an upload, or an
    agent that writes it from a description and files. Existing: the configurator, the document
    and its files edited directly, or an agent that revises it as told. */
const CONFIG_MODES = { configurator: "Configurator", upload: "Upload", edit: "Direct edit", agent: "Agent" };
async function configurePage(name, owner, mode = "configurator") {
  const show = pageShow();
  const isNew = !name;
  const host = h("div", {});
  const sub = isNew ? "Build the problem with the configurator, upload one you have, or have an agent write it from what you tell it and the files you give it."
    : "Change the problem with the configurator, edit the document and its files directly, or have an agent revise it.";
  show(isNew ? crumbs(["Loops", "#/"], ["New loop", null]) : crumbs(["Loops", "#/"], owner && owner !== me.name ? [owner, null] : null, [name, appHref(owner, name)], ["Configure", null]),
    head(isNew ? "New loop" : h("span", {}, "Configure ", h("a", { href: appHref(owner, name) }, name)), sub), host);
  configureInto(host, name, owner, mode, isNew ? "#/configure" : `${appHref(owner, name)}/settings/problem`);
}

/** The ways to make or change a problem (D704), as tabs, into `host`: New loop's page, and a
    loop's Settings › Problem (D713). `base`: the address the modes extend. */
function configureInto(host, name, owner, mode, base, { small = false, barHost = null } = {}) {
  const isNew = !name;
  const modes = isNew ? ["configurator", "upload", "agent"] : ["configurator", "edit", "agent"];
  if (!modes.includes(mode)) mode = "configurator";
  const body = h("div", {}), tabBar = h("div", { class: small ? "subtabs" : "tabs", role: "tablist" });
  function drawTabs() {
    tabBar.replaceChildren(...modes.map(k => h("button", { role: "tab", type: "button", class: k === mode ? "on" : "", "aria-selected": k === mode ? "true" : "false",
      onclick: () => { mode = k; history.replaceState(null, "", base + (k === "configurator" ? "" : "/" + k)); drawTabs(); draw(); } }, CONFIG_MODES[k])));
  }
  async function draw() {
    body.replaceChildren(card(null, skeleton(6)));
    try {
      if (mode === "configurator") await crafterView(body, name, owner);
      else if (mode === "upload") body.replaceChildren(uploadForm());
      else if (mode === "edit") await directEdit(body, name);
      else await (isNew ? newByAgent(body) : reviseByAgent(body, name, owner));
    } catch (x) { body.replaceChildren(card(null, h("p", { class: "err" }, x.message))); }
  }
  if (barHost) { barHost.append(tabBar); host.replaceChildren(body); } else host.replaceChildren(tabBar, body);
  drawTabs(); draw();
}

/** The configurator (D686): the crafter, the loop's files beside it. */
async function crafterView(body, name, owner) {
  const C = window.FluxCrafter;
  if (!C) { body.replaceChildren(card(null, empty("The configurator's script did not load."))); return; }
  if (!crafterCatalog) {
    crafterCatalog = await fetch("/crafter-assets/tools.json").then(r => r.json()).catch(() => []);
    C.setCatalog(crafterCatalog);
  }
  const host = h("div", { class: "flux-crafter" });
  const yamlOf = () => { const c = host.querySelector(".fc-yaml code"); return c ? c.textContent : ""; };
  if (name) {                                           // an existing loop, read back
    const v = await api(`/apps/${enc(name)}/document`);
    if (!v.document) { body.replaceChildren(card(null, empty("This loop has no problem document yet: an agent may be writing it (the loop's Overview), or use Direct edit."))); return; }
    if (v.raw == null) {                                // D710: not YAML at all -- the form would read nothing and save over it
      body.replaceChildren(card(null, [h("p", { class: "callout bad" }, "The loader refuses the document as it stands: " + v.error),
        h("p", { class: "muted" }, "The configurator cannot read it. Fix it in ", h("a", { href: `${appHref(owner, name)}/settings/problem/edit` }, "Direct edit"), ".")]));
      return;
    }
    const got = C.fromDoc(v.raw, v.normal || v.raw);
    const panel = filesPanel(name, yamlOf);
    body.replaceChildren(h("p", { class: "muted" }, h("span", { class: "mono" }, v.document), " · saving rewrites it from this form; comments are not kept",
        got.kept.length ? "; what the form does not edit is kept as written" : ""),
      v.error ? h("p", { class: "callout bad" }, "The loader refuses the document as it stands: " + v.error) : "",
      host, panel.el);
    host.addEventListener("input", panel.watch); host.addEventListener("change", panel.watch);
    C.mount(host, false, { state: got.state, notes: got.notes, saveLabel: "Save to " + v.document, nextSteps: false, foldSteps: true,
      save: async (yaml) => {
        // D693: what the save changes, line by line, before it writes
        const p = await api(`/apps/${enc(name)}/document/preview`, { method: "POST", body: { text: yaml, kept: got.kept } });
        if (p.before === p.after) { toast("Nothing changes: the document already says this.", "info"); return "No change."; }
        if (!await confirmDiff(p.document, p.before, p.after)) return "Not saved.";
        const r = await api(`/apps/${enc(name)}/document`, { method: "PUT", body: { text: yaml, kept: got.kept } });
        toast(r.ok, r.error ? "warn" : "ok"); panel.draw(); return r.ok;
      } });
    setTimeout(panel.draw, 300);
    return;
  }
  const panel = filesPanel(null, yamlOf);
  let adv = null;                                           // D697: an admin's advanced settings, applied once it exists
  const advBox = me.role === "admin" ? advancedCard({ advanced: {}, advanced_said: { memory: "memory", cpus: "CPUs", pids: "processes", tmp_size: "scratch" },
    can_advance: true, sandboxed_server: true }, async (a) => { adv = a; toast("Kept: applied when the loop is created", "ok"); }, "Keep for the new loop") : "";
  body.replaceChildren(host, panel.el, advBox);
  host.addEventListener("input", panel.watch); host.addEventListener("change", panel.watch);
  setTimeout(panel.draw, 300);
  // D719: one name -- the form's, the problem's id and the loop's; the checklist calm until used;
  // no command-line next steps; who does each step folded, its defaults being usually right
  C.mount(host, false, { saveLabel: "Create the loop", nextSteps: false, calmChecks: true, foldSteps: true,
    nameLabel: "Loop name", namePlaceholder: "my_loop", nameHint: "Letters, digits and _: the loop's name and its problem's id",
    save: async (yaml, state) => {
      const name = String(state.id || "").trim();
      if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(name)) throw new Error("Give the loop a name first (1. What do you want? › Loop name): a letter, then letters, digits or _.");
      await api("/apps/from-text", { method: "POST", body: { name, filename: "problem.yaml", text: yaml } });
      const n = await panel.upload(name);
      if (adv) await api(`/apps/${enc(name)}/advanced`, { method: "PUT", body: adv });
      toast(`${name} created${n ? ` with ${n} file(s)` : ""}`, "ok");
      setTimeout(() => { location.hash = `#/app/${enc(name)}`; }, 400);
      return "Created.";
    } });
}

/** The changes of a save, shown before it writes (D693): true to write. */
async function confirmDiff(file, before, after, problem = "") {
  const ops = lineDiff(before, after);
  const plus = ops.filter(o => o[0] === "+").length, minus = ops.filter(o => o[0] === "-").length;
  // D757: a document that does not load is said before it is written, not after a start fails
  return dialog(`Save ${file}?`, h("div", {},
    problem ? h("div", { class: "callout bad" }, h("strong", {}, "This document does not load: "), problem,
      h("p", { class: "small" }, "A start or a check of it will be refused until it is fixed.")) : "",
    h("p", { class: "muted" }, `${plus} line(s) added, ${minus} removed.`), diffView(ops)),
    [["Cancel", false], problem ? ["Save anyway", true, "danger"] : ["Save", true, "primary"]]);
}

/** Direct edit (D704): the document's YAML as written, saved with its diff shown; its files beside. */
async function directEdit(body, name) {
  const info = await api(`/apps/${enc(name)}`);
  const doc = info.document;
  let before = "";
  // D757: the panel may ask for the text while the document is still on its way -- the editor is not made yet
  const panel = filesPanel(name, () => (ed ? ed.textarea.value : before));
  if (doc) {
    const r = await fetch(`/api${owned(`/apps/${enc(name)}/file?path=${enc(doc)}`)}`, { credentials: "same-origin" });
    before = r.ok ? await r.text() : "";
  }
  const fileIn = h("input", { value: doc || "problem.yaml", class: "mono", style: "width:280px", readonly: doc ? true : null });
  var ed = codeEditor(before, "yaml");
  const save = act("Save", async () => {
    const text = ed.textarea.value, file = fileIn.value.trim();
    if (text === before) { toast("Nothing changes.", "info"); return; }
    const v = file === doc ? await api(`/apps/${enc(name)}/validate`, { method: "POST", body: { text } }).catch(() => ({ ok: true })) : { ok: true };
    if (!await confirmDiff(file, before, text, v.ok ? "" : v.error)) return;
    await api(`/apps/${enc(name)}/file?path=${enc(file)}`, { method: "PUT", body: { text } });
    before = text; panel.draw();
    const err = await loaderSays();
    toast(err ? `${file} saved, but the loader refuses it: ${err}` : `${file} saved`, err ? "warn" : "ok");
  }, { cls: "primary" });
  // D710: a document the loader refuses is said here, on opening and on saving -- not first at Start
  const refused = h("div", {});
  async function loaderSays() {
    const v = await api(`/apps/${enc(name)}/document`).catch(() => ({}));
    refused.replaceChildren(v.error ? h("p", { class: "callout bad" }, "The loader refuses the document as it stands: " + v.error) : "");
    return v.error || "";
  }
  body.replaceChildren(card(null, [h("div", { class: "row" }, h("label", { class: "stack" }, "The document", fileIn),
      h("span", { class: "muted" }, "As written: comments and everything kept. Check before starting: Start runs the check on what you saved.")),
    refused, ed.el, h("div", { class: "form-actions" }, save)]), panel.el);
  if (doc) loaderSays();
  setTimeout(panel.draw, 200);
}

/** A new loop whose problem an agent writes (D704): a name, what it should do, the files to read. */
async function newByAgent(body) {
  const name = h("input", { placeholder: "my_adder", style: "width:100%", id: "ag-name" });
  const ask = h("textarea", { rows: 6, id: "ag-ask", placeholder: "What the loop should make, and what matters: e.g. a signed 8x8 multiplier in SystemVerilog, the smallest that makes 1 GHz placed on ASAP7, exact for every input." });
  const who = await agentSelect("ag-who");
  const files = attachBox();
  const go = act("Write the problem", async () => {
    if (!name.value.trim()) { toast("Name the loop.", "warn"); name.focus(); return; }
    if (!ask.value.trim()) { toast("Say what the loop should do.", "warn"); ask.focus(); return; }
    const fd = new FormData(); fd.append("name", name.value.trim()); fd.append("prompt", ask.value); fd.append("author", who.value); files.form(fd);
    const r = await api("/apps/new-by-agent", { method: "POST", form: fd });
    toast(r.ok, "ok"); location.hash = `#/app/${enc(name.value.trim())}`;
  }, { cls: "primary" });
  body.replaceChildren(card(null, [
    h("p", { class: "muted" }, "The agent writes the problem document and the files it names (a golden model, a generator, checks) in the loop's folder, in the sandbox, and the document is checked. Nothing runs: you review it (Configure), then start it."),
    h("div", { class: "row" }, h("label", { class: "stack", style: "flex:1" }, "Name", name), h("label", { class: "stack" }, "Agent", who)),
    h("label", { class: "stack" }, "What should the loop do?", ask),
    h("h3", {}, "Files it should read"), files.el,
    h("div", { class: "form-actions" }, go)]));
}

/** An agent revising an existing loop's problem as told (D704); its progress, then the diff. */
async function reviseByAgent(body, name, owner) {
  const ask = h("textarea", { rows: 5, id: "ag-ask", placeholder: "What should change: e.g. measure at 1.2 GHz too, add a check for the carry out, keep everything else." });
  const who = await agentSelect("ag-who");
  const files = attachBox();
  const status = h("div", {});
  let timer = null;
  cleanup.push(() => clearTimeout(timer));
  async function poll() {
    const st = await api(`/apps/${enc(name)}/author`).catch(() => null);
    status.replaceChildren(authoringCard(name, st, { onStop: async () => { toast((await api(`/apps/${enc(name)}/author/stop`, { method: "POST" })).ok, "ok"); } }));
    if (st && st.running) timer = setTimeout(poll, 3000);
  }
  const go = act("Revise the problem", async () => {
    if (!ask.value.trim()) { toast("Say what should change.", "warn"); ask.focus(); return; }
    const fd = new FormData(); fd.append("prompt", ask.value); fd.append("author", who.value); files.form(fd);
    toast((await api(`/apps/${enc(name)}/author`, { method: "POST", form: fd })).ok, "ok");
    poll();
  }, { cls: "primary" });
  body.replaceChildren(card(null, [
    h("p", { class: "muted" }, "The agent edits the document (and its files) in the loop's folder as you say, in the sandbox, and the document is checked; the loop's record stays. You see what changed below."),
    h("div", { class: "row" }, h("label", { class: "stack" }, "Agent", who)),
    h("label", { class: "stack" }, "What should change?", ask),
    h("h3", {}, "Files it should read"), files.el,
    h("div", { class: "form-actions" }, go)]), status);
  poll();
}

// ================================================================ admin and account
/** The admin's pages (D695): every loop and the controls over all of them, what the machine
    holds up (containers, disk, caches), users with their limits and usage, the audit trail. */
const ADMIN_TABS = { "": "Loops", insights: "Insights", applications: "Applications", documents: "Documents", resources: "Resources", sandbox: "Sandbox", agents: "Agents and models", users: "Users", audit: "Audit" };
const bytes = (n) => n == null ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : n < 1073741824 ? `${(n / 1048576).toFixed(1)} MB` : `${(n / 1073741824).toFixed(2)} GB`;
function meter(frac, cls = "") {
  const f = Math.max(0, Math.min(1, frac || 0));
  return h("div", { class: `meter ${cls}${f > 0.9 ? " high" : f > 0.75 ? " mid" : ""}` }, h("div", { style: `width:${(f * 100).toFixed(1)}%` }));
}
async function adminPage(sub = "") {
  const show = pageShow();
  const tab = sub === "models" ? "agents" : ADMIN_TABS[sub] ? sub : "";       // D814: Models and variables are the agents' tab
  const tabBar = h("div", { class: "tabs", role: "tablist" }, Object.entries(ADMIN_TABS).map(([k, label]) =>
    h("a", { role: "tab", class: k === tab ? "on" : "", href: `#/admin${k ? "/" + k : ""}` }, label)));
  const body = h("div", {});
  show(crumbs(["Admin", "#/admin"], tab ? [ADMIN_TABS[tab], null] : null),
    head("Admin", "Every loop and the controls over them, what the machine holds up, users and their limits, the audit trail."), tabBar, body);
  if (tab === "") return adminLoops(body);
  if (tab === "resources") return adminResources(body);
  if (tab === "users") return adminUsers(body);
  if (tab === "sandbox") return adminSandbox(body);
  if (tab === "agents") return adminAgents(body);
  if (tab === "insights") return adminInsights(body);
  if (tab === "applications") return adminApplications(body);
  if (tab === "documents") return adminDocuments(body);
  const audit = await api("/audit");
  // D708: the hosts a loop's sandbox refused are here too, once per host and run.
  // D723: narrowed by what happened and by whom, each a list of what the trail holds
  const NOONE = "\u0000";                                   // an entry without a user (the server's own)
  const tally = (key) => { const m = new Map(); for (const x of audit) m.set(key(x), (m.get(key(x)) || 0) + 1); return [...m].sort((a, b) => a[0] < b[0] ? -1 : 1); };
  const pick = (label, all, entries, name) => h("select", { "aria-label": label },
    h("option", { value: "" }, `${all} (${audit.length})`), entries.map(([v, n]) => h("option", { value: v }, `${name(v)} (${n})`)));
  // D724: the kinds in groups; a kind not listed is Other
  const GROUPS = [["Users and sign-in", ["login", "login refused", "add user", "change user", "change password"]],
    ["Runs", ["start", "stop", "note", "stop all", "starts paused", "running limit", "kill container"]],
    ["Loops and their files", ["loop by an agent", "configure", "write document", "problem revised by an agent",
      "edit", "upload", "add files", "delete file", "delete app", "asked about a loop"]],
    ["Sharing and loop settings", ["share", "left a share", "variable", "settings", "advanced settings"]],
    ["Server", ["server settings", "sandbox settings", "clean cache", "application refreshed"]],
    ["Network", ["network refused"]]];
  const groupOf = (a) => a.startsWith("cli ") ? "Users and sign-in" : (GROUPS.find(([, ks]) => ks.includes(a)) || ["Other"])[0];
  const kinds = new Map(tally(x => x.action));
  const what = h("select", { "aria-label": "What" }, h("option", { value: "" }, `Every kind (${audit.length})`),
    [...GROUPS.map(([g]) => g), "Other"].map(g => {
      const mine = [...kinds].filter(([k]) => groupOf(k) === g);
      if (!mine.length) return "";
      const n = mine.reduce((t, [, c]) => t + c, 0);
      // D733: a group only -- one kind alone is never what is looked for; the rows keep their kind
      return h("option", { value: g, title: mine.map(([k, c]) => `${k} (${c})`).join(", ") }, `${g} (${n})`);
    }));
  const isWhat = (x) => !what.value || groupOf(x.action) === what.value;
  const who = pick("Who", "Everyone", tally(x => x.user || NOONE), v => v === NOONE ? "no user" : v);
  const find = h("input", { placeholder: "search the details", class: "filter" });
  const count = h("span", { class: "muted" }), rows = h("tbody", {});
  const draw = () => {
    const f = find.value.trim().toLowerCase();
    const got = audit.filter(x => isWhat(x) && (!who.value || (x.user || NOONE) === who.value)
      && (!f || String(x.detail || "").toLowerCase().includes(f)));
    count.textContent = got.length === audit.length ? `${audit.length} entries` : `${got.length} of ${audit.length} entries`;
    rows.replaceChildren(...(got.length ? got.map(x => h("tr", {},
      h("td", { class: "muted" }, ago(x.t)), h("td", {}, x.user || ""),
      h("td", { class: x.action === "network refused" || x.action === "login refused" ? "bad" : "" }, x.action),
      h("td", { class: "mono muted" }, x.detail))) : [h("tr", {}, h("td", { colspan: 4 }, empty("Nothing matches.")))]));
  };
  what.onchange = who.onchange = draw; find.oninput = draw; draw();
  body.replaceChildren(card(null, [h("div", { class: "toolbar" }, what, who, find, count),
    h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Who"), h("th", {}, "What"), h("th", {}, "Detail"))), rows)]));
}

async function adminLoops(body) {
  const [allApps, res] = await Promise.all([api("/admin/apps"), api("/admin/resources").catch(() => null)]);
  const paused = res ? res.paused : null;
  const running = allApps.filter(l => l.running).length;
  const reason = h("input", { placeholder: "why (users see it)", style: "min-width:260px" });
  const controls = card("Controls", [
    paused ? h("div", { class: "callout bad" }, h("strong", {}, "New starts are paused: "), paused, " ",
      act("Resume starts", async () => { await api("/admin/paused", { method: "PUT", body: { reason: null } }); toast("Starts resumed", "ok"); route(); }, { cls: "small primary" }))
      : h("div", { class: "row" }, reason, act("Pause new starts", async () => {
          await api("/admin/paused", { method: "PUT", body: { reason: reason.value.trim() || "maintenance" } }); toast("New starts paused; running loops go on", "ok"); route();
        }), h("span", { class: "muted" }, "running loops go on; nobody can start one")),
    h("div", { class: "row", style: "margin-top:10px" },
      h("span", {}, `${running} loop(s) running`),
      act("Stop every loop after its pass", async () => {
        if (!await confirmDialog("Stop every loop?", `Each of the ${running} running loop(s) stops at the end of its pass.`, { ok: "Stop after the pass" })) return;
        const r = await api("/admin/stop-all", { method: "POST", body: { now: false } }); toast(`${Object.keys(r.stopped).length} loop(s) asked to stop`, "ok"); route();
      }),
      act("Stop every loop now", async () => {
        if (!await confirmDialog("Stop every loop now?", `Each of the ${running} running loop(s) ends its pass at once; the records keep what was judged.`, { ok: "Stop now", danger: true })) return;
        const r = await api("/admin/stop-all", { method: "POST", body: { now: true } }); toast(`${Object.keys(r.stopped).length} loop(s) stopping`, "ok"); route();
      }, { cls: "danger" }))]);
  const box = h("div", {}, loopsBrowser(allApps, { who: true }));
  body.replaceChildren(controls, card("Every loop", box));
  pageRefresh = async () => { if (!box.contains(document.activeElement)) box.replaceChildren(loopsBrowser(await api("/admin/apps"), { who: true })); };
}

/** A small time chart (D699): each series a line (the first filled), over the samples' times;
    `top` fixes the scale (a CPU count, 100%), `ref` draws a dashed level. */
function timeChart(samples, series, { title, top = null, ref = null, refLabel = "", fmt = (v) => num4(v) } = {}) {
  const W = 420, H = 130, L = 62, R = 8, T = 10, B = 20;
  const pts = samples.filter(s => series.some(se => se.get(s) != null));
  if (pts.length < 2) return h("figure", { class: "tchart" }, h("figcaption", {}, h("strong", {}, title)), h("p", { class: "muted small" }, "Not enough samples yet: one a minute."));
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const vals = pts.flatMap(s => series.map(se => se.get(s)).filter(v => v != null));
  const hi = top != null ? top : Math.max(...vals, ref || 0) * 1.1 || 1;
  const X = (t) => L + (W - L - R) * (t - t0) / Math.max(1, t1 - t0), Y = (v) => T + (H - T - B) * (1 - Math.min(v, hi) / hi);
  const span = t1 - t0, stamp = (t) => new Date(t * 1000).toLocaleString(undefined, span > 86400 ? { weekday: "short", hour: "2-digit" } : { hour: "2-digit", minute: "2-digit" });
  const last = pts[pts.length - 1];
  // the sample under the pointer: a guide, its points, and a bubble with its time and values
  const guide = sv("line", { y1: T, y2: H - B, class: "hover-guide", visibility: "hidden" });
  const dots = series.map((_, i) => sv("circle", { r: 3, class: `hover-dot s${i}`, visibility: "hidden" }));
  const tip = h("div", { class: "tchart-tip", hidden: true });
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart tchart-svg", role: "img", "aria-label": title },
      [0, 0.5, 1].map(f => [sv("line", { x1: L, x2: W - R, y1: Y(hi * f), y2: Y(hi * f), class: "grid" }),
        sv("text", { x: L - 5, y: Y(hi * f) + 4, class: "tick", "text-anchor": "end" }, fmt(hi * f))]),
      ref != null ? [sv("line", { x1: L, x2: W - R, y1: Y(ref), y2: Y(ref), class: "limit" }), sv("text", { x: W - R, y: Y(ref) - 3, class: "tick limit-t", "text-anchor": "end" }, refLabel)] : "",
      series.map((se, i) => {
        const p = pts.filter(s => se.get(s) != null);
        const d = p.map((s, j) => `${j ? "L" : "M"}${X(s.t).toFixed(1)},${Y(se.get(s)).toFixed(1)}`).join("");
        return [i === 0 ? sv("path", { d: `${d}L${X(p[p.length - 1].t).toFixed(1)},${Y(0)}L${X(p[0].t).toFixed(1)},${Y(0)}Z`, class: "area s0" }) : "",
          sv("path", { d, class: `ln s${i}` })];
      }),
      sv("text", { x: L, y: H - 5, class: "tick" }, stamp(t0)), sv("text", { x: W - R, y: H - 5, class: "tick", "text-anchor": "end" }, stamp(t1)),
      guide, dots);
  const fig = h("figure", { class: "tchart" }, h("figcaption", {}, h("strong", {}, title), " ",
      series.map((se, i) => h("span", { class: "muted" }, i ? " · " : "", h("i", { class: `sw s${i}` }), se.label, " ", h("strong", {}, last && se.get(last) != null ? fmt(se.get(last)) : "—")))),
    h("div", { class: "tchart-plot" }, svg, tip));
  const hide = () => { tip.hidden = true; guide.setAttribute("visibility", "hidden"); dots.forEach(d => d.setAttribute("visibility", "hidden")); };
  svg.addEventListener("pointermove", (e) => {
    const box = svg.getBoundingClientRect();
    if (!box.width) return;
    const x = (e.clientX - box.left) * W / box.width;
    if (x < L - 4 || x > W - R + 4) { hide(); return; }
    const t = t0 + (Math.min(Math.max(x, L), W - R) - L) / (W - L - R) * Math.max(1, t1 - t0);
    let s = pts[0];
    for (const p of pts) if (Math.abs(p.t - t) < Math.abs(s.t - t)) s = p;
    const gx = X(s.t);
    guide.setAttribute("x1", gx); guide.setAttribute("x2", gx); guide.setAttribute("visibility", "visible");
    series.forEach((se, i) => { const v = se.get(s);
      if (v == null) { dots[i].setAttribute("visibility", "hidden"); return; }
      dots[i].setAttribute("cx", gx); dots[i].setAttribute("cy", Y(v)); dots[i].setAttribute("visibility", "visible"); });
    tip.replaceChildren(h("div", { class: "mono small" }, new Date(s.t * 1000).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })),
      ...series.map((se, i) => h("div", {}, h("i", { class: `sw s${i}` }), se.label, " ", h("strong", {}, se.get(s) != null ? fmt(se.get(s)) : "—"))));
    tip.hidden = false;
    const px = gx / W * box.width, half = tip.offsetWidth / 2;
    tip.style.left = `${Math.min(Math.max(px - half, 0), box.width - tip.offsetWidth)}px`;
  });
  svg.addEventListener("pointerleave", hide);
  return fig;
}

let historyHours = 24;
async function adminResources(body) {
  body.replaceChildren(h("p", { class: "muted" }, "Measuring…"));
  let r;
  async function load() { r = await api("/admin/resources"); draw(); }
  function draw() {
    const m = r.machine, mem = m.memory || {};
    const machineCard = card("The machine", h("div", { class: "stats five" },
      h("div", { class: "stat" }, h("small", {}, "CPUs"), h("div", { class: "big" }, String(m.cpus))),
      h("div", { class: "stat" }, h("small", {}, "Load (1 · 5 · 15 min)"), h("div", { class: "big" }, (m.load || []).map(x => x.toFixed(1)).join(" · ")),
        meter((m.load || [0])[0] / m.cpus), h("div", { class: "muted" }, `${Math.round((m.load || [0])[0] / m.cpus * 100)}% of the CPUs`)),
      h("div", { class: "stat" }, h("small", {}, "Memory used"), h("div", { class: "big" }, mem.total ? bytes(mem.total - mem.available) : "?"),
        mem.total ? [meter(1 - mem.available / mem.total), h("div", { class: "muted" }, `of ${bytes(mem.total)}`)] : ""),
      ...m.disks.filter(d => !d.same_as).slice(0, 2).map(d => h("div", { class: "stat" }, h("small", {}, `Disk: ${d.label}`), h("div", { class: "big" }, `${bytes(d.free)} free`),
        meter(d.used / d.total), h("div", { class: "muted", title: d.path }, `of ${bytes(d.total)}${m.disks.some(x => x.same_as === d.label) ? " · also " + m.disks.filter(x => x.same_as === d.label).map(x => x.label).join(", ") : ""}`)))));
    const cs = r.containers || [];
    const loopLink = (u, a) => u ? h("a", { href: appHref(u, a) }, `${u} / ${a}`) : h("span", { class: "muted" }, "no loop");
    const contCard = card(`Sandbox containers (${r.engine || "none"})`, r.error ? h("p", { class: "callout bad" }, r.error)
      : cs.length ? h("div", { class: "scroll-x" }, h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Container", "Loop", "State", "CPU", "Memory", "PIDs", ""].map((x, i) => h("th", { class: i >= 3 && i <= 5 ? "num" : "" }, x)))),
          h("tbody", {}, cs.map(c => h("tr", {},
            h("td", { class: "mono" }, c.name), h("td", {}, loopLink(c.user, c.loop)),
            h("td", {}, h("span", { class: `pill ${c.state === "running" ? (c.orphan ? "warn" : "live") : ""}` }, c.orphan && c.state === "running" ? "left behind" : c.state), " ", h("small", { class: "muted" }, c.status)),
            h("td", { class: "num mono" }, c.cpu != null ? `${c.cpu.toFixed(1)}%` : ""),
            h("td", { class: "num mono" }, c.mem != null ? bytes(Math.round(c.mem)) : ""),
            h("td", { class: "num mono" }, c.pids != null ? String(c.pids) : ""),
            h("td", { class: "right" }, c.orphan ? act("Kill", async () => {
                if (!await confirmDialog(`Kill ${c.name}?`, "No running loop owns it: it is stopped and removed.", { ok: "Kill", danger: true })) return;
                toast((await api(`/admin/containers/${enc(c.name)}/kill`, { method: "POST" })).ok, "ok"); load();
              }, { cls: "small danger" })
              : act("Stop the loop now", async () => { await stopLoop(c.loop, true, c.user); load(); }, { cls: "small" }))))))) : empty("No sandbox container."));
    const loops = r.loops.slice().sort((a, b) => b.total - a.total);
    const totalOf = (k) => loops.reduce((s, l) => s + (l[k] || 0), 0);
    const cleanBtn = (l, what, label, text) => act(label, async () => {
      if (!await confirmDialog(`${label}: ${l.user} / ${l.app}?`, text, { ok: label, danger: what === "all" })) return;
      const x = await api(`/admin/caches/${enc(l.cache_key || l.key)}/clean`, { method: "POST", body: { what } }); toast(`${bytes(x.freed)} freed`, "ok"); load();
    }, { cls: "small" });
    const diskCard = card("Disk per loop", h("div", { class: "scroll-x" }, h("table", { class: "list compact" },
      h("thead", {}, h("tr", {}, ["Loop", "", "Inputs", "Record", "Log", "Workbench", "Sandbox cache", "Total", ""].map((x, i) => h("th", { class: i >= 2 && i <= 7 ? "num" : "" }, x)))),
      h("tbody", {}, loops.map(l => h("tr", {}, h("td", {}, loopLink(l.user, l.app)), h("td", {}, l.running ? h("span", { class: "pill live" }, "running") : ""),
          ...["inputs", "record", "log", "workbench", "cache", "total"].map(k => h("td", { class: `num mono${k === "total" ? " strong" : ""}` }, bytes(l[k]))),
          h("td", { class: "right" }, l.running || !l.cache ? "" : h("div", { class: "actions end" },
            cleanBtn(l, "tools", "Clear tools' cache", "The tools' own cache in the sandbox (XDG_CACHE_HOME) is emptied; they rebuild what they need."),
            cleanBtn(l, "scratch", "Clear past scratch", "The agents' working folders of past passes go. The journal, the transcript, the record and the workbench stay."))))),
        h("tr", { class: "sum" }, h("td", {}, "All loops"), h("td", {}), ...["inputs", "record", "log", "workbench", "cache", "total"].map(k => h("td", { class: "num mono strong" }, bytes(totalOf(k)))), h("td", {}))))));
    const other = r.caches;
    const cacheCard = other.length ? card("Caches no loop owns", [h("p", { class: "muted" }, "A cache of a deleted loop, or not the web's (a `flux task run` on this machine). Deleting one frees its space; a loop that comes back rebuilds it."),
      h("table", { class: "list compact" }, h("thead", {}, h("tr", {}, ["Cache", "Whose", "Size", "Last touched", ""].map((x, i) => h("th", { class: i === 2 ? "num" : "" }, x)))),
        h("tbody", {}, other.map(c => h("tr", {}, h("td", { class: "mono" }, c.key),
          h("td", {}, c.kind === "gone" ? h("span", {}, `${c.user}'s ${c.app}, `, h("span", { class: "pill warn" }, "deleted")) : h("span", { class: "muted" }, "not the web's")),
          h("td", { class: "num mono" }, bytes(c.size)), h("td", { class: "muted" }, c.touched ? ago(c.touched) : ""),
          h("td", { class: "right" }, act("Delete", async () => {
            if (!await confirmDialog(`Delete the cache ${c.key}?`, `${bytes(c.size)}: its scratch, the agents' sessions and the tools' cache.`, { ok: "Delete", danger: true })) return;
            const x = await api(`/admin/caches/${enc(c.key)}/clean`, { method: "POST", body: { what: "all" } }); toast(`${bytes(x.freed)} freed`, "ok"); load();
          }, { cls: "small danger" }))))))]) : "";
    body.replaceChildren(h("div", { class: "row end" }, h("span", { class: "muted" }, "measured ", ago(Date.now() / 1000)),
        act("Measure again", load, { cls: "small" })), machineCard, overTime, contCard, diskCard, cacheCard);
    drawHistory();
  }
  // D699: the machine over time, a sample a minute while `flux serve` runs
  const overTime = card("Over time", skeleton(4));
  async function drawHistory() {
    const hx = await api(`/admin/history?hours=${historyHours}`).catch(() => null);
    if (!hx) return;
    const ss = hx.samples, cpus = ss.length ? ss[ss.length - 1].cpus : null;
    const pct = (v) => `${Math.round(v * 100)}%`;
    const disks = ss.length ? Object.keys(ss[ss.length - 1].disks || {}) : [];
    const ranges = [[1, "1 h"], [6, "6 h"], [24, "24 h"], [168, "7 d"]];
    overTime.replaceChildren(h("div", { class: "card-head" }, h("h2", {}, "Over time"),
        h("div", { class: "chips" }, ranges.map(([hrs, label]) => h("button", { class: `chip${historyHours === hrs ? " on" : ""}`, onclick: () => { historyHours = hrs; drawHistory(); } }, label)))),
      hx.sampling ? "" : h("p", { class: "muted small" }, "This server process does not sample (only `flux serve` does): what is shown was sampled before."),
      h("div", { class: "tcharts" },
        timeChart(ss, [{ label: "load", get: (s) => s.load1 }], { title: "Load", ref: cpus, refLabel: cpus ? `${cpus} CPUs` : "", fmt: (v) => v.toFixed(1) }),
        timeChart(ss, [{ label: "used", get: (s) => s.mem_total ? s.mem_used / s.mem_total : null }], { title: "Memory", top: 1, fmt: pct }),
        timeChart(ss, disks.map(k => ({ label: k, get: (s) => (s.disks || {})[k] })), { title: "Disks", top: 1, fmt: pct }),
        timeChart(ss, [{ label: "CPU", get: (s) => s.cpu }], { title: "The containers' CPU (100% = one core)",
          top: Math.max(100, ...ss.map(s => s.cpu || 0)) * 1.05, fmt: (v) => `${Math.round(v)}%` }),
        timeChart(ss, [{ label: "memory", get: (s) => s.cmem }], { title: "The containers' memory", fmt: (v) => bytes(Math.round(v)) }),
        timeChart(ss, [{ label: "loops", get: (s) => s.loops }, { label: "containers", get: (s) => s.containers }],
          { title: "Running", top: 2 * Math.ceil((Math.max(1, ...ss.map(s => Math.max(s.loops || 0, s.containers || 0))) + 1) / 2), fmt: (v) => String(Math.round(v)) })));
  }
  await load();
  const t = setInterval(() => { if (!document.hidden && !body.contains(document.querySelector("dialog.dlg"))) load().catch(() => {}); }, 15000);
  cleanup.push(() => clearInterval(t));
}

/** The applications of this Flux (D700): an admin sees each and makes it one of their loops --
    its files linked in, its record its own; Refresh takes the folder's files again. */
async function adminApplications(body) {
  const r = await api("/admin/applications");
  if (!r.root) { body.replaceChildren(card(null, empty("No applications folder: set FLUX_APPLICATIONS to one."))); return; }
  const use = async (a, refresh) => {
    if (refresh && !await confirmDialog(`Refresh ${a.name}?`, "Its files are taken again from the folder; edits made to them in the loop go. Its record, log and workbench stay.", { ok: "Refresh" })) return;
    await api(`/admin/applications/${enc(a.name)}/use${refresh ? "?refresh=true" : ""}`, { method: "POST" });
    toast(refresh ? `${a.name}: its files taken again` : `${a.name} is one of your loops`, "ok");
    location.hash = `#/app/${enc(a.name)}`;
  };
  body.replaceChildren(card(`The applications folder`, [h("p", { class: "muted" }, h("span", { class: "mono" }, r.root),
      ". Use one to make it a loop of yours: its files are linked in (no copy on the same disk), and a run writes only the loop's own record, log and workbench."),
    h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Application", "What it asks", "Size", ""].map((x, i) => h("th", { class: i === 2 ? "num" : "" }, x)))),
      h("tbody", {}, r.applications.map(a => h("tr", {},
        h("td", {}, h("strong", {}, a.name), h("div", { class: "mono muted small" }, a.document)),
        h("td", { class: "muted small app-what" }, a.statement),
        h("td", { class: "num mono" }, bytes(a.size)),
        h("td", { class: "right" }, h("div", { class: "actions end" }, a.loop
          ? [h("a", { class: "btn small primary", href: `#/app/${enc(a.name)}` }, "Open"), a.linked ? act("Refresh", () => use(a, true), { cls: "small" }) : h("span", { class: "muted small", title: "A loop of yours has this name; it was not made from this folder" }, "name taken")]
          : act("Use", () => use(a, false), { cls: "small primary" })))))))]));
}

/** Admin › Documents (D811): every loop's documents of an earlier form, what each would change to
    be of today's, and the migration -- one loop or all; a result is written only when it loads, the
    original kept as `<file>.orig`; what needs a person is said, not written. */
async function adminDocuments(body) {
  body.replaceChildren(skeleton(4));
  const r = await api("/admin/documents");
  const PILL = { "would migrate": "live", "needs a hand": "bad", failed: "bad", current: "ok", migrated: "ok" };
  const run = async (b, what) => {
    const got = await api("/admin/documents/migrate", { method: "POST", body: b });
    const left = got.done.flatMap(x => x.why ? [`${x.user}/${x.app}: ${x.why}`] : x.documents.filter(d => d.status !== "migrated" && d.status !== "current").map(d => `${x.user}/${x.app}/${d.file}: ${d.status}`));
    toast(`${what}: ${got.migrated} document(s) migrated${left.length ? `; left: ${left.join("; ")}` : ""}`, left.length ? "warn" : "ok");
    route();
  };
  const ready = r.loops.filter(l => !l.running && l.documents.some(d => d.status === "would migrate"));
  const rows = r.loops.map(l => h("div", { class: "mig-loop" },
    h("div", { class: "mig-head" }, h("strong", {}, `${l.user} / `, h("a", { href: appHref(l.user, l.app) }, l.app)), l.running ? h("span", { class: "pill live" }, "running") : "",
      h("span", { class: "grow" }),
      l.documents.some(d => d.status === "would migrate") ? (l.running ? h("span", { class: "muted small" }, "stop it to migrate: its document is in use")
        : act("Migrate", () => run({ user: l.user, app: l.app }, `${l.user}/${l.app}`), { cls: "small primary", title: "Write the documents that load; keep each original" }))
        // D813: no document it can write by itself -- why, said where the button would be, and the way to do it by hand
        : [h("span", { class: "small bad" }, l.documents.some(d => d.status === "needs a hand") ? "not by itself: a part needs rewriting by hand (below)"
            : "not by itself: its result would not load (below)"),
          h("a", { class: "btn small", href: `${appHref(l.user, l.app)}/settings/problem` }, "Edit its document")]),
    ...l.documents.filter(d => d.status !== "current").map(d => h("div", { class: "mig-doc" },
      h("div", {}, h("span", { class: "mono" }, d.file), d.to !== d.file ? h("span", { class: "mono muted" }, ` → ${d.to}`) : "", " ",
        h("span", { class: `pill ${PILL[d.status] || ""}` }, d.status)),
      d.why ? h("p", { class: "small bad" }, d.why) : "",
      d.manual.length ? h("ul", { class: "small bad" }, d.manual.map(m => h("li", {}, m))) : "",
      d.said.length ? h("details", {}, h("summary", { class: "small" }, `${d.said.length} change(s)`),
        h("ul", { class: "small mono" }, d.said.map(x => h("li", {}, x))),
        d.text ? h("pre", { class: "log small" }, d.text) : "") : ""))));
  body.replaceChildren(card("Documents of an earlier form", [
    h("p", { class: "muted" }, `${r.documents} document(s) in ${r.total} loop(s); `, r.loops.length ? `${r.loops.length} loop(s) with one to bring to today's form.` : "all of today's form.",
      " A migration writes a document only when the result loads; the original is kept beside it as ", h("code", {}, "<file>.orig"),
      " (YAML comments are not carried over). A loop whose id was not its folder's name keeps its record, renamed. What a migration cannot do -- a ",
      h("code", {}, "world:"), " to say as commands -- is said for a person."),
    ready.length ? h("div", { class: "toolbar" }, act(`Migrate all (${ready.length})`, () => run({}, "Every loop"), { cls: "primary" })) : "",
    ...(r.loops.length ? rows : [empty("Nothing to migrate.")])]));
}

/** What every sandbox gets (D698): the network, PATH directories, what every home starts with (D744). */
async function adminSandbox(body) {
  const r = await api("/admin/sandbox");
  const c = r.config;
  const lines = (a) => (a || []).join("\n");
  const list = (ta) => ta.value.split(/[\n,]/).map(x => x.trim()).filter(Boolean);
  const ta = (id, value, rows, ph) => h("textarea", { id, rows, placeholder: ph, class: "mono", value });
  const mode = h("select", { id: "sb-net" }, h("option", { value: "open", selected: c.network !== "allowlist" }, "open: the containers reach any host"),
    h("option", { value: "allowlist", selected: c.network === "allowlist" }, "allowlist: only the hosts below"));
  const allow = ta("sb-allow", lines(c.allow), 5, "localai.example.org\n*.anthropic.com\n10.0.0.0/8\n192.168.1.20");
  const usersAdd = h("input", { type: "checkbox", id: "sb-users", checked: c.users_add !== false });
  const endpoints = h("input", { type: "checkbox", id: "sb-ep", checked: c.endpoints !== false });
  const paths = ta("sb-path", lines(c.path), 3, "/opt/tools/bin");
  const loginP = h("input", { type: "checkbox", id: "sb-login", checked: !!c.login_path });
  const adds = r.login_path.filter(d => !r.path.includes(d));
  const seed = ta("sb-seed", lines(c.home_seed), 3, ".config/opencode\n.gitconfig\n.npmrc");
  const allowBox = h("div", { class: "sb-allow" }, h("label", { class: "stack" }, "Allowed: one per line, a host (and its subdomains), *.domain, an IP or a CIDR", allow),
    h("label", { class: "check" }, endpoints, "also the model endpoints set under Models (their hosts)"),
    h("label", { class: "check" }, usersAdd, "a user may add hosts when starting a loop"));
  const showAllow = () => { allowBox.hidden = mode.value !== "allowlist"; };
  mode.addEventListener("change", showAllow); showAllow();
  body.replaceChildren(
    r.sandboxed ? "" : h("p", { class: "callout bad" }, "This server runs without the sandbox (--no-sandbox): none of this applies."),
    card("Network", [h("p", { class: "muted" }, "What the containers may reach. With an allowlist they have no network of their own: a proxy on this machine forwards to the allowed hosts and refuses the rest, a name resolved and checked against the IPs and CIDRs. A loop's Settings may add hosts for that loop; a loop an admin runs on the host has the machine's network."),
      h("label", { class: "stack" }, "Mode", mode), allowBox]),
    card("PATH", [h("p", { class: "muted" }, "Every directory on the runs' PATH is mounted read-only in the container."),
      h("details", {}, h("summary", { class: "muted" }, `The server's own PATH: ${r.path.length} directories`), h("pre", { class: "val small" }, r.path.join("\n"))),
      h("label", { class: "check" }, loginP, `add ${r.home}'s login PATH`, adds.length ? `: ${adds.join(", ")}` : " (it adds nothing to the above)"),
      h("label", { class: "stack" }, "and these directories, first", paths)]),
    card("Homes", [h("p", { class: "muted" }, "Every user has a home of their own: their runs' HOME, writable and kept -- their agents' settings, logins and sessions. ",
        "Each user logs their agents in on their Account page; no one's login is shared."),
      h("label", { class: "stack" }, `Every home starts with (paths inside ${r.home}, copied where a home lacks them, never over what is there)`, seed)]),
    h("div", { class: "form-actions" }, act("Save", async () => {
      await api("/admin/sandbox", { method: "PUT", body: { network: mode.value, allow: list(allow), users_add: usersAdd.checked, endpoints: endpoints.checked,
        path: list(paths), login_path: loginP.checked, home_seed: list(seed) } });
      toast("Sandbox settings saved: they apply from each loop's next start", "ok"); route();
    }, { cls: "primary" })));
}

/** Admin › Insights (D766): what went wrong, what was used, how the endpoints and agents did,
    what the network refused, where the disk goes -- over the last days. */
async function adminInsights(body) {
  let days = 7;
  try { days = Number(localStorage.getItem("flux-insights-days")) || 7; } catch (_) {}
  const pick = h("select", { "aria-label": "Over the last", onchange: () => { try { localStorage.setItem("flux-insights-days", pick.value); } catch (_) {} adminInsights(body); } },
    [[1, "day"], [7, "7 days"], [30, "30 days"]].map(([v, t]) => h("option", { value: v, selected: v === days }, t)));
  body.replaceChildren(skeleton(8));
  const r = await api(`/admin/insights?days=${days}`);
  const ago2 = (t) => t ? ago(t) : "—";
  const loopLink = (u, a) => h("a", { href: `#/u/${enc(u)}/app/${enc(a)}` }, `${u}/${a}`);
  const spark = (xs, label) => {                       // a bar per day, to its row's own scale
    const max = Math.max(...xs, 0) || 1, w = 6, gap = 2;
    return h("span", { class: "spark", title: label, "aria-label": label },
      ...xs.map(x => h("i", { class: x > 0 ? "" : "z", style: `height:${x > 0 ? Math.max(2, Math.round(16 * x / max)) : 1}px;width:${w}px;margin-right:${gap}px` })));
  };
  const f = r.failures;
  const failCard = card("Failures", [
    f.starts.length ? h("table", { class: "list compact" }, h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Loop"), h("th", {}, "Why"))),
      h("tbody", {}, f.starts.slice(0, 20).map(s => h("tr", {}, h("td", { class: "muted" }, ago2(s.when)), h("td", {}, loopLink(s.user, s.app)),
        h("td", { class: "mono small why-cell" }, (s.why || []).slice(-2).join(" · ") || `exit ${s.rc}`)))))
      : h("p", { class: "muted" }, "No start failed."),
    f.tests.length ? [h("h3", {}, "Agent Tests that failed"), h("table", { class: "list compact" },
      h("thead", {}, h("tr", {}, h("th", {}, "User"), h("th", {}, "Agent"), h("th", {}, "Step"), h("th", {}, "Why"), h("th", {}, "When"))),
      h("tbody", {}, f.tests.map(t => h("tr", {}, h("td", {}, t.user), h("td", {}, t.agent), h("td", {}, t.step), h("td", { class: "small" }, t.why), h("td", { class: "muted" }, ago2(t.when))))))] : ""]);
  const u = r.usage;
  const rowsOf = (by) => Object.entries(by).sort((a, b) => b[1].tokens.reduce((s, x) => s + x, 0) - a[1].tokens.reduce((s, x) => s + x, 0));
  const usageTable = (by, head) => h("table", { class: "list compact" },
    h("thead", {}, h("tr", {}, h("th", {}, head), h("th", { class: "num" }, "Turns"), h("th", { class: "num" }, "Tokens"), h("th", { class: "num" }, "Cost"), h("th", {}, `Tokens a day (${u.days[0]} – ${u.days[u.days.length - 1]})`))),
    h("tbody", {}, rowsOf(by).map(([k, v]) => h("tr", {}, h("td", { class: "strong" }, k),
      h("td", { class: "num" }, String(v.turns.reduce((s, x) => s + x, 0))), h("td", { class: "num mono" }, fmtTok(v.tokens.reduce((s, x) => s + x, 0))),
      h("td", { class: "num mono" }, `$${v.cost.reduce((s, x) => s + x, 0).toFixed(2)}`), h("td", {}, spark(v.tokens, `${k}: tokens a day`))))));
  const usageCard = card("Usage", [Object.keys(u.users).length ? [h("h3", {}, "By user"), usageTable(u.users, "User"), h("h3", {}, "By agent or model"), usageTable(u.agents, "Agent or model"),
    u.top.length ? [h("h3", {}, "The loops that used most"), h("table", { class: "list compact" },
      h("thead", {}, h("tr", {}, h("th", {}, "Loop"), h("th", { class: "num" }, "Turns"), h("th", { class: "num" }, "Tokens"), h("th", { class: "num" }, "Cost"), h("th", { class: "num" }, "Time"))),
      h("tbody", {}, u.top.map(t => h("tr", {}, h("td", {}, loopLink(t.user, t.app)), h("td", { class: "num" }, String(t.turns)),
        h("td", { class: "num mono" }, fmtTok(t.tokens)), h("td", { class: "num mono" }, `$${t.cost.toFixed(2)}`), h("td", { class: "num" }, dur(t.seconds))))))] : ""]
    : h("p", { class: "muted" }, "No model or agent turn in this time.")]);
  const epCard = card("Endpoints and agents", r.endpoints.length ? h("table", { class: "list compact" },
    h("thead", {}, h("tr", {}, h("th", {}, "Which"), h("th", { class: "num" }, "Turns"), h("th", { class: "num" }, "Failed"), h("th", { class: "num" }, "Median"),
      h("th", { class: "num" }, "Slow (95%)"), h("th", {}, "Last used"), h("th", {}, "Last failure"))),
    h("tbody", {}, r.endpoints.map(e => h("tr", {}, h("td", {}, h("span", { class: "pill" }, e.kind), " ", h("span", { class: "mono small" }, e.where)),
      h("td", { class: "num" }, String(e.turns)),
      h("td", { class: `num${e.rate > 0.2 ? " bad" : ""}` }, `${e.failed} (${Math.round(100 * e.rate)}%)`),
      h("td", { class: "num" }, dur(e.p50)), h("td", { class: "num" }, dur(e.p95)), h("td", { class: "muted" }, ago2(e.last)),
      h("td", { class: "small why-cell", title: e.last_error || "" }, e.last_error ? [ago2(e.last_error_at), ": ", e.last_error.slice(0, 140)] : "—")))))
    : h("p", { class: "muted" }, "No turn in this time."));
  const netCard = card("Network refused", r.network.length ? h("table", { class: "list compact" },
    h("thead", {}, h("tr", {}, h("th", {}, "Host"), h("th", { class: "num" }, "Times"), h("th", {}, "By"), h("th", {}, "Last"))),
    h("tbody", {}, r.network.map(n => h("tr", {}, h("td", { class: "mono" }, `${n.host}:${n.port}`), h("td", { class: "num" }, String(n.count)),
      h("td", { class: "small" }, n.loops.map(([a, c]) => `${a} ×${c}`).join(", ")), h("td", { class: "muted" }, ago2(n.last))))))
    : h("p", { class: "muted" }, "Nothing refused: every host the loops asked for was allowed (or the network is open)."));
  const maxDisk = Math.max(...r.disk.map(d => d.total), 1);
  const diskCard = card("Disk by user", h("table", { class: "list compact" },
    h("thead", {}, h("tr", {}, h("th", {}, "User"), h("th", { class: "num" }, "Home"), h("th", { class: "num" }, "Loops"), h("th", {}, "Largest loop"), h("th", { class: "num" }, "Total"), h("th", {}, ""))),
    h("tbody", {}, r.disk.map(d => h("tr", {}, h("td", { class: "strong" }, d.user), h("td", { class: "num mono" }, bytes(d.home)),
      h("td", { class: "num mono" }, `${bytes(d.loops)} (${d.count})`), h("td", {}, d.largest ? [loopLink(d.user, d.largest.app), " ", h("span", { class: "muted mono small" }, bytes(d.largest.size))] : "—"),
      h("td", { class: "num mono strong" }, bytes(d.total)), h("td", { class: "meter-cell" }, meter(d.total / maxDisk)))))));
  body.replaceChildren(h("div", { class: "toolbar" }, h("span", { class: "muted" }, "Over the last"), pick), failCard, usageCard, epCard, netCard, diskCard);
}

/** Admin › Agents and models (D756, D807, D814): one tab per tool -- Flux's own model and the agent by
    default; each agent, its program and login (found or not, its version, who has it ready) with its
    model settings and its own variables under it, one Save; the other providers; the variables every
    agent and run gets; adding an agent. An agent whose program is not found has its program only:
    users are offered it, and its model, once it is found. */
async function adminAgents(body) {
  body.replaceChildren(skeleton(6));
  const [r, st, genv] = await Promise.all([api("/admin/agents"), api("/admin/settings"), api("/admin/env")]);
  const lines = (a) => (a || []).join("\n");
  const list = (ta) => ta.value.split(/[\n,]/).map(x => x.trim()).filter(Boolean);
  const HOME_PH = { opencode: ".config/opencode", claude: ".claude/settings.json", codex: ".codex/config.toml" };
  const panelOf = (a) => {
    const f = (id, value, ph) => h("input", { id: `ag-${a.id}-${id}`, value, placeholder: ph, class: "mono", autocomplete: "off" });
    const label = h("input", { id: `ag-${a.id}-label`, value: a.label, placeholder: a.id, autocomplete: "off" });
    const bin = f("bin", a.bin, a.builtin ? a.id : "/path/to/its/program"), login = f("login", a.login, a.login_default), args = f("args", a.args, "none");
    const home = h("textarea", { id: `ag-${a.id}-home`, rows: 2, class: "mono", placeholder: HOME_PH[a.kind] || "", value: lines(a.home) });
    const hosts = h("textarea", { id: `ag-${a.id}-hosts`, rows: 2, class: "mono", placeholder: "auth.example.com", value: lines(a.hosts) });
    const creds = h("textarea", { id: `ag-${a.id}-creds`, rows: 1, class: "mono", placeholder: "its usual; e.g. .local/share/nga/auth.json", value: lines(a.login_files) });
    const ready = a.users.filter(u => u.state === "ready").map(u => u.user), failed = a.users.filter(u => u.state === "failed").map(u => u.user);
    const body_ = () => ({ label: label.value, bin: bin.value, login: login.value, args: args.value, home: list(home), hosts: list(hosts), login_files: list(creds) });
    const first = JSON.stringify(body_());
    const el = h("div", { class: "agent-panel" },
      h("h2", { class: "agent-panel-name" }, a.label),
      h("div", { class: "agent-found" },
        h("span", { class: `pill ${a.found ? "ok" : "bad"}` }, a.found ? "found" : "not found"),
        h("span", { class: "pill" }, a.builtin ? "built in" : `a ${a.kind}`), h("code", { class: "small" }, a.id),
        h("span", { class: "mono small" }, a.found ? `${a.found}${a.version ? " · " + a.version : ""}`
          : a.builtin ? `${a.bin || a.id} is not on the runs' PATH: not offered to users` : `${a.bin ? a.bin + " is not there or not runnable" : "no program yet"}: not offered to users`)),
      h("h4", { class: "set-sub" }, "Its program and login"),
      h("div", { class: "grid-2" },
        h("label", { class: "stack" }, "Name shown", label),
        h("label", { class: "stack" }, a.builtin ? "Program (a path, or a name on PATH)" : "Program (a path)", bin),
        h("label", { class: "stack" }, "Login command", login),
        h("label", { class: "stack" }, "Extra arguments, every run", args),
        h("label", { class: "stack", title: "Where a login of this build is kept, in a user's home: what says they are logged in" }, "Login files (when not its usual)", creds),
        h("label", { class: "stack" }, "Every home starts with (paths in this server account's home)", home),
        h("label", { class: "stack" }, "Hosts it needs, under a network allowlist", hosts)),
      h("p", { class: "small" }, h("strong", {}, "Ready for: "), ready.length ? ready.join(", ") : "nobody yet",
        failed.length ? h("span", { class: "bad" }, ` · its test failed for ${failed.join(", ")}`) : "",
        h("span", { class: "muted" }, " (each user tests it on their Account page; it is tested again each day)")),
      a.builtin ? "" : h("div", { class: "form-actions" }, act("Remove", async () => {
        if (!await confirmDialog(`Remove ${a.label}?`, "Its settings and variables go with it, the server's and every user's; a loop that names it no longer starts.", { ok: "Remove", danger: true })) return;
        await api(`/admin/agents/${a.id}`, { method: "DELETE" }); toast(`${a.label} removed`, "ok"); route();
      }, { cls: "danger small" })));
    return { el, dirty: () => JSON.stringify(body_()) !== first,
             save: async () => { await api(`/admin/agents/${a.id}`, { method: "PUT", body: body_() }); toast(`${label.value || a.label} saved: from the next start, login and test`, "ok"); } };
  };
  const offered = new Set(st.groups.filter(g => g.agent).map(g => g.agent));
  const panels = {}, extraTabs = [];
  for (const a of r.agents) {
    const p = panelOf(a);
    if (offered.has(a.id)) panels[a.id] = p;
    else extraTabs.push({ tab: a.label, before: "other", el: h("fieldset", { class: "set-group with-panel" }, h("legend", {}, a.label), p.el,
      h("p", { class: "muted small" }, "Its model and its own variables are set here once its program is found.")), save: p.save, dirty: p.dirty });
  }
  extraTabs.push({ tab: "Every agent", noSave: true, el: h("fieldset", { class: "set-group" }, h("legend", {}, "Variables for every run and every agent"),
    h("p", { class: "muted small" }, "Every run on this server gets these, and each of its agents whatever the name (an ANTHROPIC_API_KEY here reaches Claude Code and OpenCode alike); a user's and a loop's own come over them. The sandbox's own variables cannot be set here: a loop's Settings tab has them."),
    envEditor(genv, async (v) => { await api("/admin/env", { method: "PUT", body: v }); route(); }, "server")) });
  const name = h("input", { id: "ag-new-name", placeholder: "nga", class: "mono", autocomplete: "off" });
  const kind = h("select", { id: "ag-new-kind", "aria-label": "Its kind" }, r.kinds.map(k => h("option", { value: k.id }, k.label)));
  const nlabel = h("input", { id: "ag-new-label", placeholder: "NGA (our OpenCode)", autocomplete: "off" });
  const nbin = h("input", { id: "ag-new-bin", placeholder: "/opt/nga/bin/nga", class: "mono", autocomplete: "off" });
  extraTabs.push({ tab: "+ Add an agent", noSave: true, el: h("fieldset", { class: "set-group" }, h("legend", {}, "Add an agent"),
    h("p", { class: "muted small" }, "Another build of a kind -- an OpenCode of your own beside the plain one -- under a name of its own, which a document names (",
      h("code", {}, "generate: nga"), "). It runs as its kind does, with its own program, login and settings, and is offered to users once its program is found."),
    h("div", { class: "grid-2" }, h("label", { class: "stack" }, "Name (lower case)", name), h("label", { class: "stack" }, "Kind", kind),
      h("label", { class: "stack" }, "Name shown", nlabel), h("label", { class: "stack" }, "Program (a path)", nbin)),
    h("div", { class: "form-actions" }, act("Add", async () => {
      await api("/admin/agents", { method: "POST", body: { name: name.value.trim(), kind: kind.value, label: nlabel.value, bin: nbin.value } });
      try { localStorage.setItem("flux-models-tab-server", nlabel.value.trim() || name.value.trim()); } catch (_) { /* per viewer */ }
      toast(`${name.value.trim()} added`, "ok"); route();
    }, { cls: "primary" }))) });
  const save = async (values) => { if (Object.keys(values).length) await api("/admin/settings", { method: "PUT", body: { values } }); toast("Saved", "ok"); route(); };
  body.replaceChildren(card("Agents and models", [h("p", { class: "muted" }, "Each tool on a tab of its own: an agent's program and login, then the model it uses and the variables only it gets; ",
      "Flux's own model; the variables every agent gets. What the server sets, every run gets unless its user sets their own on their Account page. Keys are stored encrypted and never shown again."),
    ...settingsForm(st, { save, scope: "server", panels, extraTabs, agentEnv: (a) => ({ rows: (st.agent_env || {})[a] || [],
      save: async (v) => { await api(`/admin/agents/${a}/env`, { method: "PUT", body: v }); route(); } }) })]));
}

async function adminUsers(body) {
  const [users, use, res] = await Promise.all([api("/users"), api("/admin/usage").catch(() => []), api("/admin/resources").catch(() => null)]);
  const name = h("input", { placeholder: "name" }); const pw = h("input", { type: "password", placeholder: "password (10+)" });
  // D734: the kinds -- internal users' runs inherit the server's settings, external ones bring their own
  const KINDS = [["internal", "internal"], ["external", "external"], ["admin", "admin"]];
  const kindSel = (value, onchange, label) => h("select", { "aria-label": label, onchange }, KINDS.map(([v, t]) => h("option", { value: v, selected: v === value }, t)));
  const newKind = kindSel("internal", null, "Kind of the new user");
  const useOf = (n) => use.find(u => u.user === n) || {};
  const def = res ? res.max_running : 4;
  const limitCell = (u) => {
    const cur = res && res.limits ? res.limits[u.name] : null;
    const inp = h("input", { type: "number", min: 0, max: 64, value: cur ?? "", placeholder: String(def), style: "width:64px" });
    return h("td", {}, h("span", { class: "inline" }, inp, act("Set", async () => {
      const v = inp.value.trim() === "" ? null : Number(inp.value);
      await api(`/admin/users/${enc(u.name)}/limit`, { method: "PUT", body: { max_running: v } });
      toast(`${u.name}: ${v == null ? `the default (${def})` : v} loop(s) at once`, "ok");
    }, { cls: "small" })));
  };
  body.replaceChildren(card("Users", [h("div", { class: "scroll-x" }, h("table", { class: "list" },
      h("thead", {}, h("tr", {}, h("th", {}, "User"), h("th", {}, "Role"), h("th", { title: "Loops running at once; empty: the server's default" }, "Running limit"),
        h("th", { class: "num" }, "Loops"), h("th", { class: "num" }, "Turns"), h("th", { class: "num" }, "Time"), h("th", { class: "num" }, "Tokens in → out"), h("th", { class: "num" }, "Cost"), h("th", {}, ""))),
      h("tbody", {}, users.map(u => { const x = useOf(u.name); return h("tr", {},
        h("td", { class: "strong" }, u.name), h("td", {}, u.name === me.name ? h("span", { class: "pill" }, u.role)
          : kindSel(u.role, async (e) => {
              const to = e.target.value;
              try { await api(`/users/${enc(u.name)}`, { method: "PATCH", body: { role: to } }); toast(`${u.name} is ${to} now`, "ok"); }
              catch (_) { e.target.value = u.role; }
            }, `${u.name}'s kind`), u.disabled ? h("span", { class: "pill bad" }, "disabled") : ""),
        limitCell(u),
        h("td", { class: "num mono" }, String(x.loops ?? "")), h("td", { class: "num mono" }, String(x.turns ?? "")), h("td", { class: "num mono" }, x.seconds ? dur(x.seconds) : ""),
        h("td", { class: "num mono" }, x.counted ? `${fmtTok(x.tokens_in)} → ${fmtTok(x.tokens_out)}` : "—"), h("td", { class: "num mono" }, x.cost_usd ? `$${x.cost_usd.toFixed(2)}` : "—"),
        h("td", { class: "right" }, h("div", { class: "actions end" },
          act(u.disabled ? "Enable" : "Disable", async () => {
            if (!u.disabled && !await confirmDialog(`Disable ${u.name}?`, "They are logged out and cannot log in; their loops stay.", { ok: "Disable", danger: true })) return;
            await api(`/users/${enc(u.name)}`, { method: "PATCH", body: { disabled: !u.disabled } }); toast(`${u.name} ${u.disabled ? "enabled" : "disabled"}`, "ok"); route();
          }, { cls: "small" }),
          act("Reset password", async () => {
            const p = await promptDialog(`New password for ${u.name}`, "At least 10 characters", { type: "password", min: 10 });
            if (p === null) return;
            await api(`/users/${enc(u.name)}`, { method: "PATCH", body: { password: p } }); toast(`${u.name}'s password changed`, "ok");
          }, { cls: "small" })))); })))),
    h("div", { class: "row add-user" }, name, pw, newKind,
      act("Add user", async () => {
        await api("/users", { method: "POST", body: { name: name.value, password: pw.value, role: newKind.value } });
        toast(`${name.value} added`, "ok"); route();
      }, { cls: "primary" })),
    h("p", { class: "muted small" }, "Internal: their runs use the server's model, agent and environment settings. External: they set their own on their Account page, ",
      "and log their agents in there, into a home of their own. The network rules apply to everyone.")]));
}

/** Model settings by what uses them (D696): Flux's own model and each coding agent. `server`:
    the admin's values a field falls back to when empty (a key only said to be set). */
const SETTING_LABELS = { FLUX_REMOTE_BASE_URL: "Endpoint URL", FLUX_REMOTE_MODEL: "Model", FLUX_LLM_TIMEOUT_S: "Seconds per request",
  FLUX_LLM_MODEL: "Local model (Ollama tag)", OLLAMA_BASE_URL: "Ollama URL", FLUX_REMOTE_API_KEY: "Key", OPENROUTER_API_KEY: "OpenRouter key",
  FLUX_DEFAULT_AGENT: "Agent" };
/** D807: each agent offered has a tab of its own -- its kind's endpoint, model and key, and variables
    for it alone (`agentEnv(name)`: its rows and how to save one, or null). */
function settingsForm(st, { server = null, save, scope, agentEnv = null, panels = {}, extraTabs = [] }) {
  // D814: `panels[agent]` -- {el, save, dirty} -- its program and login above its model; `extraTabs` --
  // [{tab, el, before, save, dirty, noSave}] -- a tab of its own (an agent not offered, every agent's
  // variables, adding one); one Save writes what changed on any tab
  const inputs = {};
  const secret = new Set(st.secret);
  const labels = Object.assign({}, SETTING_LABELS, ...st.groups.map(g => g.labels || {}));
  const row = (k) => {
    const cur = st.values[k], fall = server ? server[k] : null, sec = secret.has(k);
    inputs[k] = h("input", { type: sec ? "password" : "text", autocomplete: "off", value: sec ? "" : (cur || ""),
      placeholder: sec ? (cur ? "set · type to replace" : fall ? "the server's key" : "not set") : (fall ? `the server's: ${fall}` : "not set") });
    return h("div", { class: "set-row" }, h("label", { class: "lbl", for: `set-${scope}-${k}` }, labels[k] || k),
      h("span", { class: "inline" }, Object.assign(inputs[k], { id: `set-${scope}-${k}` }),
        cur ? act("Clear", () => save({ [k]: null }), { cls: "small" }) : ""),
      h("code", { class: "muted small var" }, k));
  };
  const groups = st.groups.map(g => {
    const own = [...g.public, ...g.secret].some(k => st.values[k]);
    const note = server && st.values[g.endpoint] ? "your own endpoint: none of the server's values of this group are used"
      : server && [...g.public, ...g.secret].some(k => server[k]) && !own ? "the server's settings apply" : "";
    const vars = g.agent && agentEnv ? agentEnv(g.agent) : null;
    const panel = g.agent ? panels[g.agent] : null;
    const el = h("fieldset", { class: "set-group" + (panel ? " with-panel" : "") }, h("legend", {}, g.label), panel ? panel.el : "",
      panel ? h("h4", { class: "set-sub" }, "Its model") : "", g.hint ? h("p", { class: "muted small" }, g.hint) : "",
      note ? h("p", { class: "small hint-line" }, note) : "",
      ...g.public.map(row), ...g.secret.map(row),
      // D807: variables for this agent alone (a variable for every agent is an ordinary one)
      vars ? h("div", { class: "blk agent-vars" }, h("h4", {}, `Variables for ${g.label} alone`),
        h("p", { class: "muted small" }, "Only this agent gets these (e.g. ANTHROPIC_API_KEY for an OpenCode); a variable for every agent goes with the environment variables."),
        envEditor(vars.rows, vars.save, `${scope}-${g.agent}`),
        vars.server && vars.server.length ? h("div", {}, h("p", { class: "muted small" }, "The server's, under yours:"),
          envTable(vars.server.map(x => ({ ...x, from: "the server" })), new Set(vars.rows.map(x => x.name)))) : "") : "",
      "");
    return { g, el, own };
  });
  // D721: a tab per tool -- Flux, OpenCode, Claude Code, Codex, Other; one Save for all of them;
  // a tab that holds a value is marked; the tab last looked at is kept in this browser
  const own = st.groups.filter(g => g.id !== "other").map(g => g.tab || g.label);
  const tabs = [...new Set([...own, ...extraTabs.filter(x => x.before === "other").map(x => x.tab),
    ...st.groups.filter(g => g.id === "other").map(g => g.tab || g.label), ...extraTabs.filter(x => x.before !== "other").map(x => x.tab)])];
  const extras = extraTabs.map(x => ({ ...x, holder: h("div", { class: "set-extra" }, x.el) }));
  const memo = `flux-models-tab-${scope}`;
  let cur = (() => { try { return localStorage.getItem(memo); } catch (_) { return null; } })();
  if (!tabs.includes(cur)) cur = tabs[0];
  const bar = h("div", { class: "subtabs set-tabs", role: "tablist" });
  const draw = () => {
    bar.replaceChildren(...tabs.map(t => {
      const set = groups.some(x => (x.g.tab || x.g.label) === t && x.own);
      return h("button", { type: "button", role: "tab", class: t === cur ? "on" : "", "aria-selected": t === cur ? "true" : "false",
        title: set ? "has settings of its own" : null, onclick: () => { cur = t; try { localStorage.setItem(memo, t); } catch (_) { /* per viewer */ } draw(); } },
        t, set ? h("span", { class: "set-dot", "aria-label": "set" }, " •") : "");
    }));
    for (const x of groups) x.el.hidden = (x.g.tab || x.g.label) !== cur;
    for (const x of extras) x.holder.hidden = x.tab !== cur;
    actions.hidden = extras.some(x => x.tab === cur && x.noSave);
  };
  const actions = h("div", { class: "form-actions" }, act("Save", async () => {
    const values = {};
    for (const k of st.public) if ((inputs[k].value || "") !== (st.values[k] || "")) values[k] = inputs[k].value || null;
    for (const k of st.secret) if (inputs[k].value) values[k] = inputs[k].value;
    for (const x of [...Object.values(panels), ...extras]) if (x.save && x.dirty && x.dirty()) await x.save();
    return save(values);
  }, { cls: "primary" }));
  draw();
  return [bar, h("div", { class: "set-groups" }, groups.map(x => x.el), extras.map(x => x.holder)), actions];
}

async function accountPage() {
  const show = pageShow();
  const [st, myEnv] = await Promise.all([api("/settings"), api("/env")]);
  async function save(values) { await api("/settings", { method: "PUT", body: { values } }); toast("Settings saved", "ok"); route(); }
  const pw = h("input", { type: "password", autocomplete: "new-password" });
  const mine = await api("/usage").catch(() => null);
  const holders = Object.fromEntries(st.groups.filter(g => g.agent).map(g => [g.agent, h("div", { class: "agent-login" })]));
  const lg = await loginsCard(holders);
  const logins = { box: lg.box, term: lg.term, panels: Object.fromEntries(Object.entries(holders).map(([a, el]) =>
    [a, { el: h("div", { class: "agent-panel" }, h("h4", { class: "set-sub first" }, "Its login"), el) }])) };
  show(head("Account", `Logged in as ${me.name}`),
    mine && mine.turns ? card("My usage", h("p", {}, `${mine.turns} model and agent turn(s) over ${mine.loops} loop(s), ${dur(mine.seconds)}`,
      mine.counted ? `, ${fmtTok(mine.tokens_in)} tokens in and ${fmtTok(mine.tokens_out)} out` : "",
      mine.cost_usd ? `, $${mine.cost_usd.toFixed(2)} as the agents priced it` : "", ".")) : "",
    // D814: one card, a tab per tool -- each agent's login and Test, its model, its own variables; Flux's
    // model; the variables every agent of yours gets
    card("My agents and models", [
      h("p", { class: "muted" }, st.external ? "Your runs use these alone (an external account: nothing of the server's). Log each agent in on its tab, or set its endpoint, model and key. Keys are stored encrypted and never shown again. "
        : "Empty: the server's settings, shown in grey. Naming your own endpoint for a tool sends none of the server's values of it to your runs. Keys are stored encrypted and never shown again. ",
        "Your agents log in into a home of your own on the server, in the sandbox; your loops use an agent once its Test passed for you, and it is tested again each day."),
      ...settingsForm(st, { server: st.server, save, scope: "me", panels: logins.panels,
        extraTabs: [{ tab: "Every agent", noSave: true, el: h("fieldset", { class: "set-group" }, h("legend", {}, "Variables for every run and every agent of yours"),
          h("p", { class: "muted small" }, st.external ? "Every run of yours gets these (yours alone: nothing of the server's), and each of its agents; a loop's own (its Settings tab) come over them."
            : "Every run of yours gets these, over the server's, and each of its agents; a loop's own (its Settings tab) come over them."),
          envEditor(myEnv.mine, async (v) => { await api("/env", { method: "PUT", body: v }); route(); }, "me"),
          myEnv.server.length ? h("div", { class: "blk" }, h("h4", {}, "The server's"), envTable(myEnv.server.map(x => ({ ...x, from: "the server" })), new Set(myEnv.mine.map(x => x.name)))) : "") }],
        agentEnv: (a) => { const e = (st.agent_env || {})[a] || { mine: [], server: [] };
          return { rows: e.mine, server: e.server, save: async (v) => { await api(`/agents/${a}/env`, { method: "PUT", body: v }); route(); } }; } }),
      logins.box, logins.term]),
    h("div", { class: "grid-2" },
      card("Password", [h("label", { class: "stack" }, "New password (10+)", pw),
        h("div", { class: "form-actions" }, act("Change", async () => { await api("/password", { method: "POST", body: { text: pw.value } }); pw.value = ""; toast("Password changed", "ok"); }))]),
      card("Notifications", [h("p", { class: "muted" }, "You are told when a loop stops, fails, or its agent asks a question, in the page and in the bell."),
        "Notification" in window ? (Notification.permission === "granted" ? h("p", {}, "Desktop notifications are on.")
          : Notification.permission === "denied" ? h("p", { class: "muted" }, "Desktop notifications are blocked in this browser's settings.")
          : act("Allow desktop notifications", async () => { await Notification.requestPermission(); route(); })) : ""])));
}

/** A user's agent logins (D734; every user's, D747): each agent, logged in or not, and its login run in a
    small terminal -- its output (links clickable), a line to type, the keys a menu wants. */
async function loginsCard(holders = null) {           // D814: `holders[agent]`: where its row goes (its tab)
  const box = h("div", {});
  const out = h("pre", { class: "login-out", "aria-live": "polite" });
  const line = h("input", { placeholder: "type here, then Send (or a key below)", class: "login-in", "aria-label": "Input to the login" });
  let offset = 0, text = "", timer = null, testTimer = null, wasTesting = new Set();
  const linkify = (t) => {                                  // links as links, the rest as text nodes
    const parts = [], re = /https?:\/\/[^\s"'<>]+/g; let at = 0, m;
    while ((m = re.exec(t))) { parts.push(t.slice(at, m.index), h("a", { href: m[0], target: "_blank", rel: "noopener noreferrer" }, m[0])); at = m.index + m[0].length; }
    parts.push(t.slice(at));
    return parts;
  };
  const send = async (body) => { try { await api("/logins/session/input", { method: "POST", body }); } catch (_) { /* said by the toast */ } setTimeout(poll, 150); };
  const keyBtn = (label, key, title) => h("button", { type: "button", class: "small", title, onclick: () => send({ key }) }, label);
  const term = h("div", { class: "login-term", hidden: true },
    h("div", { class: "login-head" }, h("strong", { class: "login-what" }), h("span", { class: "grow" }),
      h("button", { type: "button", class: "small danger", onclick: async () => { await api("/logins/session/stop", { method: "POST" }); setTimeout(poll, 300); } }, "Stop")),
    out,
    h("div", { class: "row login-row" }, line, act("Send", async () => { await send({ text: line.value, key: "enter" }); line.value = ""; }, { cls: "primary small" })),
    h("div", { class: "row login-keys" }, keyBtn("↑", "up", "Up"), keyBtn("↓", "down", "Down"), keyBtn("Enter", "enter", "Enter"),
      keyBtn("Esc", "escape", "Escape"), keyBtn("Tab", "tab", "Tab"), keyBtn("Ctrl-C", "ctrl-c", "Interrupt")));
  line.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); send({ text: line.value, key: "enter" }); line.value = ""; } });
  async function poll() {
    clearTimeout(timer);
    let st;
    try { st = await api(`/logins/session?since=${offset}`); } catch (_) { return; }
    if (!box.isConnected) return;
    if (st.text) { text += st.text; offset = st.offset; out.replaceChildren(...linkify(text.slice(-60000))); out.scrollTop = out.scrollHeight; }
    term.querySelector(".login-what").textContent = st.running ? `Logging ${st.agent} in…` : st.agent ? `${st.agent}: the login ended${st.rc ? ` (exit ${st.rc})` : ""}` : "";
    for (const el of term.querySelectorAll(".login-row, .login-keys, .login-head button")) el.hidden = !st.running;
    if (st.running) timer = setTimeout(poll, 700);
    else setTimeout(drawList, 500);                         // D768: its Test has begun by then
  }
  async function drawList() {
    const lg = await api("/logins").catch(() => null);
    if (!lg || !box.isConnected && box.parentNode) return;
    // D751: an agent is used in your loops once its test passed -- the program, the login, one short answer
    const tested = (a) => { const t = a.tested || {}; return a.testing ? ["live", "testing…"] : t.ok ? ["ok", "ready"] : t.when ? ["bad", "test failed"] : ["", "not tested"]; };
    // D768: a login that ended well is tested at once, on the server -- said here when it is done
    for (const a of lg.agents) if (wasTesting.has(a.id) && !a.testing && a.tested)
      toast(a.tested.ok ? `${a.label} is logged in and ready for your loops` : `${a.label} is logged in but its Test failed: see its steps`, a.tested.ok ? "ok" : "warn");
    wasTesting = new Set(lg.agents.filter(a => a.testing).map(a => a.id));
    clearTimeout(testTimer);
    if (wasTesting.size) testTimer = setTimeout(drawList, 2000);
    const steps = (t) => h("ul", { class: "agent-steps small" }, (t.steps || []).map(st =>
      h("li", { class: st.ok ? "" : "bad" }, h("span", { class: "mono" }, st.ok ? "✓ " : "✗ "), h("strong", {}, st.step), " ", st.said)));
    const rowsOf = (a) => [h("tr", {},
      h("td", { class: "strong" }, a.label),
      h("td", {}, (() => { const viaKey = !a.logged_in && ((a.tested || {}).steps || []).some(st => st.step === "login" && st.ok);
        return h("span", { class: `pill ${a.logged_in || viaKey ? "ok" : ""}`, title: viaKey ? "No login of its own: a key or endpoint from the settings" : "" },
          a.logged_in ? "logged in" : viaKey ? "key in settings" : "not logged in"); })()),
      h("td", {}, h("span", { class: `pill ${tested(a)[0]}`, title: a.tested && a.tested.when ? `tested ${new Date(a.tested.when * 1000).toLocaleString()}` : "" }, tested(a)[1])),
      h("td", { class: "mono muted small", title: a.command }, a.command.replace(/^\S*\//, "")),
      h("td", { class: "right" }, h("div", { class: "actions end" },
        a.testing ? h("span", { class: "muted small" }, "testing…") : act("Test", async () => {
          toast(`Testing ${a.label}: it is asked one short question…`, "info");
          const got = await api(`/agents/${a.id}/test`, { method: "POST" });
          toast(got.ok ? `${a.label} is ready for your loops` : `${a.label} is not ready: see its steps`, got.ok ? "ok" : "warn");
          await drawList();
        }, { cls: "small", title: "Its program, your login, one short answer -- as your loops run it" }),
        act(a.logged_in ? "Log in again" : "Log in", async () => {
          await api(`/logins/${a.id}`, { method: "POST" });
          text = ""; offset = 0; out.replaceChildren(); term.hidden = false; poll();
        }, { cls: "small" })))),
      ...(a.tested && a.tested.steps && a.tested.steps.length ? [h("tr", { class: "agent-test-row" }, h("td", { colspan: 5 }, steps(a.tested)))] : [])];
    const table = (as) => h("table", { class: "list compact" }, h("tbody", {}, as.flatMap(rowsOf)));
    const placed = holders ? lg.agents.filter(a => holders[a.id]) : [];
    for (const a of placed) holders[a.id].replaceChildren(table([a]));
    const rest = lg.agents.filter(a => !placed.includes(a));
    box.replaceChildren(rest.length ? table(rest) : "");
    if (lg.session && lg.session.running && term.hidden) { term.hidden = false; poll(); }
  }
  cleanup.push(() => { clearTimeout(timer); clearTimeout(testTimer); });
  await drawList();
  if (holders) return { box, term };
  return card("Agent logins", [h("p", { class: "muted" }, "Your agents log in into a home of your own on the server; your runs use what the login writes. ",
    "The login runs as a run does, in the sandbox, under the server's network rules. ",
    "Your loops run on your logins -- also when someone you share one with starts it -- and use an agent once its Test passed for you; a login that ends well is tested at once."), box, term]);
}

// ================================================================ routing
// ---- D754: on a phone nothing scrolls sideways -- a list's rows stack, each value under its column's name
const NARROW = window.matchMedia ? window.matchMedia("(max-width: 640px)") : { matches: false };
function labelTables(root) {
  for (const t of root.querySelectorAll("table.list")) {
    const heads = [...t.querySelectorAll(":scope > thead th")].map(th => th.textContent.trim());
    if (!heads.some(Boolean)) continue;
    for (const tr of t.querySelectorAll(":scope > tbody > tr")) {
      [...tr.children].forEach((td, i) => { if (heads[i] && td.dataset.label !== heads[i]) td.dataset.label = heads[i]; });
    }
  }
}
{
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; labelTables(document.body); });
  }).observe(document.body, { childList: true, subtree: true });
}

async function route() {
  navSeq++;
  for (const f of cleanup.splice(0)) f();
  pageRefresh = null;
  for (const d of document.querySelectorAll("dialog.dlg")) d.dispatchEvent(new Event("cancel"));   // a dialog belongs to its page
  const hash = location.hash || "#/";
  if (hash === "#/login") { drawNav(); return loginPage(); }
  if (!me) { try { me = await api("/me"); pollLoops(); } catch (_) { return; } }
  drawNav();
  try {
    let m;
    pageOwner = null;
    // D713: a loop's address is its tab and what is under it: /live/log, /files/workbench, /settings/problem/edit
    if ((m = hash.match(/^#\/app\/([^/]+)((?:\/[a-z-]+)*)$/))) return await loopPage(decodeURIComponent(m[1]), null, m[2].slice(1));
    if ((m = hash.match(/^#\/u\/([^/]+)\/app\/([^/]+)((?:\/[a-z-]+)*)$/))) { pageOwner = decodeURIComponent(m[1]); return await loopPage(decodeURIComponent(m[2]), pageOwner, m[3].slice(1)); }
    if (hash === "#/new") return await newPage();
    if ((m = hash.match(/^#\/configure(?:\/([a-z]+))?$/))) return await configurePage(null, null, m[1]);
    if ((m = hash.match(/^#\/admin(?:\/([a-z]+))?$/)) && me.role === "admin") return await adminPage(m[1] || "");
    if (hash === "#/account") return await accountPage();
    return await appsPage();
  } catch (x) { if (x.message !== "log in") show(card(null, h("p", { class: "err" }, x.message))); }
}
// ---- the theme: system, light or dark, remembered in this browser (D691)
const THEMES = { system: "◐ System", light: "☀ Light", dark: "☾ Dark" };
function theme() { try { return localStorage.getItem("flux-theme") || "system"; } catch (_) { return "system"; } }
function applyTheme(t) {
  if (t === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  try { localStorage.setItem("flux-theme", t); } catch (_) {}
}
applyTheme(theme());
document.querySelector(".brand").replaceChildren(logo(24), h("span", {}, "flux"));    // D696: the mark
const themeBtn = h("button", { class: "small theme", title: "Theme: system, light or dark" });
themeBtn.addEventListener("click", () => { const order = ["system", "light", "dark"]; applyTheme(order[(order.indexOf(theme()) + 1) % 3]); themeBtn.textContent = THEMES[theme()]; });
themeBtn.textContent = THEMES[theme()];

function drawNav() {
  bellFor(me ? me.name : null);
  const here = location.hash || "#/";
  const link = (href, text, on) => h("a", { href, class: on ? "on" : "" }, text);
  document.getElementById("nav").replaceChildren(...(me ? [
    link("#/", "Loops", here === "#/" || here.startsWith("#/app") || here.startsWith("#/u/")),
    link("#/configure", "New loop", here.startsWith("#/configure") || here === "#/new"),
    me.role === "admin" ? link("#/admin", "Admin", here.startsWith("#/admin")) : ""] : []));
  drawBell();
  document.getElementById("who").replaceChildren(themeBtn, ...(me ? [h("div", { class: "bell-wrap" }, bellBtn, bellMenu), h("a", { href: "#/account", class: "me" }, me.name),
    h("button", { class: "small", onclick: async () => { await api("/logout", { method: "POST" }).catch(() => {}); me = null; location.hash = "#/login"; } }, "Log out")] : []));
}
window.addEventListener("hashchange", route);
route();
