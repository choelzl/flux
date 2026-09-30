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

async function api(path, { method = "GET", body, form } = {}) {
  const opt = { method, headers: { "X-Flux": "1" }, credentials: "same-origin" };
  if (form) opt.body = form;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  let r;
  try { r = await fetch("/api" + path, opt); }
  catch (x) { offline(true); throw new Error("The server cannot be reached."); }
  offline(false);
  if (r.status === 401 && path !== "/login") { me = null; location.hash = "#/login"; throw new Error("log in"); }
  const type = r.headers.get("content-type") || "";
  const data = type.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : r.statusText);
  return data;
}

// ---- the server out of reach (D694): a banner while it is, gone at the next answer
const offlineBar = h("div", { class: "offline", role: "alert", hidden: true }, "The server cannot be reached: retrying…");
document.body.append(offlineBar);
function offline(on) { if (offlineBar.hidden === on) offlineBar.hidden = !on; }

/** A server-sent stream that outlives a dropped connection (D694). The browser reconnects by
    itself with the last event's id; when it gives up (a proxy's error page, a restarted server),
    the stream is opened again with that id, waiting longer each time, up to 30 s. */
function followStream(url, event, onData, onState) {
  let es = null, last = null, closed = false, wait = 1000, timer = null;
  const say = (st) => { if (onState) onState(st); };
  const open = () => {
    es = new EventSource(last ? `${url}${url.includes("?") ? "&" : "?"}offset=${encodeURIComponent(last)}` : url);
    es.addEventListener(event, (m) => { if (m.lastEventId) last = m.lastEventId; onData(JSON.parse(m.data)); });
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
function head(title, sub, ...actions) {
  return h("div", { class: "page-head" }, h("div", {}, h("h1", {}, title), sub ? h("p", { class: "sub" }, sub) : ""),
    actions.length ? h("div", { class: "actions" }, actions) : "");
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
  const t = h("div", { class: `toast ${kind}` }, href ? h("a", { href }, text) : text,
    h("button", { class: "x", "aria-label": "dismiss", onclick: () => t.remove() }, "×"));
  toasts.append(t);
  if (timeout) setTimeout(() => t.remove(), timeout);
}
function dialog(title, body, buttons) {
  return new Promise((resolve) => {
    const d = h("dialog", { class: "dlg" });
    const done = (v) => { d.close(); d.remove(); resolve(v); };
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
const bell = { list: [], seen: new Map(), unread: 0, primed: false };
try { bell.list = JSON.parse(localStorage.getItem("flux-notes") || "[]"); } catch (_) { bell.list = []; }
function notify(text, kind, href) {
  bell.list.unshift({ text, kind, href, t: Date.now() / 1000 });
  bell.list = bell.list.slice(0, 30); bell.unread++;
  try { localStorage.setItem("flux-notes", JSON.stringify(bell.list)); } catch (_) {}
  toast(text, kind, { timeout: 9000, href });
  if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
    try { new Notification("Flux", { body: text, tag: href }); } catch (_) {}
  }
  drawBell();
}
/** Every 10 s: a loop that stopped (finished, failed, stopped) or whose agent asks. */
async function pollLoops() {
  if (!me) return;
  let loops;
  try { loops = await api("/loops"); } catch (_) { return; }
  let changed = false;
  for (const l of loops) {
    const was = bell.seen.get(l.app);
    const key = l.question ? `q:${l.question.asked}` : "";
    if (!was || was.running !== l.running) changed = true;
    if (bell.primed && was) {
      const href = `#/app/${enc(l.app)}`;
      if (was.running && !l.running) {
        if (l.failed) notify(`${l.app} failed`, "bad", href);
        else if (l.stopped) notify(`${l.app} stopped`, "warn", href);
        else notify(`${l.app} finished its passes`, "ok", href);
      }
      if (key && key !== was.key) notify(`${l.app}: the agent asks a question`, "warn", href);
    }
    bell.seen.set(l.app, { running: l.running, key });
  }
  if (changed && bell.primed && pageRefresh) pageRefresh().catch(() => {});
  bell.primed = true;
}
setInterval(pollLoops, 10000);
const bellBtn = h("button", { class: "bell", title: "Notifications", "aria-label": "Notifications" });
const bellMenu = h("div", { class: "bell-menu", hidden: true });
function drawBell() {
  bellBtn.replaceChildren("🔔", bell.unread ? h("span", { class: "badge" }, String(bell.unread)) : "");
  const canAsk = "Notification" in window && Notification.permission === "default";
  bellMenu.replaceChildren(
    h("div", { class: "bell-head" }, h("strong", {}, "Notifications"),
      canAsk ? h("button", { class: "link", onclick: async () => { await Notification.requestPermission(); drawBell(); } }, "Allow desktop notifications") : "",
      bell.list.length ? h("button", { class: "link", onclick: () => { bell.list = []; bell.unread = 0; localStorage.removeItem("flux-notes"); drawBell(); } }, "Clear") : ""),
    ...(bell.list.length ? bell.list.map(n => h("a", { class: `bell-item ${n.kind}`, href: n.href || "#/", onclick: () => { bellMenu.hidden = true; } },
      h("span", {}, n.text), h("small", {}, ago(n.t)))) : [h("p", { class: "muted" }, "Nothing yet: you are told here when a loop stops, fails, or its agent asks.")]));
}
bellBtn.addEventListener("click", (e) => { e.stopPropagation(); bellMenu.hidden = !bellMenu.hidden; bell.unread = 0; drawBell(); });
document.addEventListener("click", (e) => { if (!bellMenu.hidden && !bellMenu.contains(e.target)) bellMenu.hidden = true; });

// ================================================================ pages
async function loginPage() {
  const name = h("input", { autocomplete: "username", required: true });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const err = h("p", { class: "err" });
  const form = h("form", { class: "card login", onsubmit: async (e) => {
      e.preventDefault(); err.textContent = "";
      try { me = await api("/login", { method: "POST", body: { name: name.value, password: pw.value } }); location.hash = "#/"; route(); }
      catch (x) { err.textContent = x.message; }
    } },
    h("h1", {}, "Flux"), h("p", { class: "sub" }, "Log in to your loops."),
    h("label", { class: "stack" }, "Name", name), h("label", { class: "stack" }, "Password", pw),
    h("button", { class: "primary wide", type: "submit" }, "Log in"), err);
  show(form);
  name.focus();
}

/** Start or stop a loop: the dialog for a start's options, a confirm for "now". */
async function startLoop(name) {
  // D693: the last start's options, and the check when the inputs changed since it was run
  const pre = await api(`/apps/${enc(name)}/preflight`).catch(() => ({}));
  if (pre.paused) { toast(`New starts are paused by an admin: ${pre.paused}`, "warn", { timeout: 8000 }); return false; }
  const last = pre.options || {};
  const passes = h("input", { type: "number", min: 1, value: last.passes || 1, style: "width:90px" });
  const forever = h("input", { type: "checkbox", checked: last.passes === null });
  const screen = h("input", { type: "checkbox", checked: !!last.screen_only });
  const allow = h("input", { placeholder: "empty: open network", style: "width:100%", value: (last.allow || []).join(", ") });
  passes.disabled = forever.checked;
  forever.addEventListener("change", () => { passes.disabled = forever.checked; });
  const checkBox = h("div", { class: "preflight" });
  const body = h("div", {},
    h("p", { class: "muted" }, "It resumes from its record: what was judged stays judged."),
    checkBox,
    h("div", { class: "row" }, h("label", { class: "stack" }, "Passes", passes), h("label", { class: "check" }, forever, "until I stop it")),
    h("label", { class: "check" }, screen, "screen only (skip the costly stages)"),
    h("label", { class: "stack", style: "margin-top:10px" }, "Network allowlist (hosts, domains, CIDRs)", allow));
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
  if (pre.checked && pre.ok !== null) verdict(pre.ok, pre.output);
  else {
    said(null, pre.changed ? "The inputs changed since the last start: checking them in the sandbox…" : "Checking the inputs in the sandbox…", "");
    if (startBtn) startBtn.disabled = true;
    api(`/apps/${enc(name)}/check`, { method: "POST" }).then(r => verdict(r.ok, r.output), x => said(false, "The check could not run: " + x.message, ""))
      .finally(() => { if (startBtn) startBtn.disabled = false; });
  }
  const go = await waiting;
  if (!go) return false;
  const r = await api(`/apps/${enc(name)}/start`, { method: "POST", body: {
    passes: forever.checked ? null : (Number(passes.value) || 1), screen_only: screen.checked,
    allow: allow.value.split(",").map(x => x.trim()).filter(Boolean) } });
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
  if (!loops.length) return empty("No loop yet.", h("p", {}, h("a", { class: "btn primary", href: "#/configure" }, "Build a new loop"), " or upload a document with its files."));
  return h("table", { class: "list" },
    h("thead", {}, h("tr", {}, who ? h("th", {}, "User") : "", h("th", {}, "Loop"), h("th", {}, "State"), h("th", {}, "Activity"),
      h("th", { class: "num" }, "Designs"), h("th", {}, "Best"), h("th", {}, "Document"), h("th", {}, ""))),
    h("tbody", {}, loops.map(l => {
      const name = l.name || l.app, owner = l.owner && l.owner !== me.name ? l.owner : null, sm = l.summary || {};
      const href = owner ? `#/u/${enc(owner)}/app/${enc(name)}` : `#/app/${enc(name)}`;
      const acts = owner ? (l.running ? [act("Stop", () => stopLoop(name, false, owner).then(() => pageRefresh && pageRefresh()), { cls: "small" })] : [])
        : l.running ? [act("Stop", () => stopLoop(name, false).then(() => pageRefresh && pageRefresh()), { cls: "small" })]
        : [act("Start", async () => { if (await startLoop(name)) location.hash = href; }, { cls: "small primary" }),
           h("a", { class: "btn small", href: `#/app/${enc(name)}/configure` }, "Configure")];
      return h("tr", { class: "clickable", onclick: (e) => { if (!e.target.closest("a, button")) location.hash = href; } },
        who ? h("td", {}, l.owner) : "",
        h("td", {}, h("a", { href, class: "strong" }, name)),
        h("td", {}, statePill(l), l.question ? h("span", { class: "pill warn" }, "asks") : ""),
        h("td", { class: "muted" }, lastSaid(l)),
        h("td", { class: "num mono" }, sm.designs ? [String(sm.accepted), h("span", { class: "muted" }, ` / ${sm.designs}`)] : h("span", { class: "muted" }, "—")),
        h("td", { class: "mono" }, sm.best ? h("span", { class: sm.best.meets === false ? "misses" : sm.best.meets === true ? "meets" : "",
          title: `the decision, ${sm.best.design}` }, h("span", { class: "muted" }, sm.best.metric + " "), num4(sm.best.value),
          sm.best.meets === true ? " ✓" : sm.best.meets === false ? " ✗" : "") : ""),
        h("td", { class: "mono muted" }, l.document || ""),
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

async function appsPage() {
  const loops = await api("/apps");
  const name = h("input", { placeholder: "my_adder", pattern: "[A-Za-z0-9][A-Za-z0-9_-]*", required: true });
  const files = h("input", { type: "file", multiple: true });
  const folder = h("input", { type: "file", webkitdirectory: true, multiple: true });
  let dropped = [];
  const droppedSaid = h("div", { class: "muted small" });
  const dz = dropZone("Drop the loop's folder or files here", (got, dir) => {
    dropped = got;
    if (dir && !name.value) name.value = dir.replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^[^A-Za-z0-9]+/, "");
    droppedSaid.replaceChildren(`${got.length} file(s) ready`, dir ? ` from ${dir}/` : "", " · ",
      h("a", { href: "javascript:void 0", onclick: () => { dropped = []; droppedSaid.replaceChildren(); } }, "clear"));
  });
  const upload = h("form", { class: "stack-form", onsubmit: async (e) => {
      e.preventDefault();
      const chosen = [...[...files.files, ...folder.files].map(f => ({ file: f, path: f.webkitRelativePath || f.name })), ...dropped];
      if (!chosen.length) { toast("Choose or drop files, a folder or a .zip.", "warn"); return; }
      const form = new FormData(); form.append("name", name.value);
      for (const f of chosen) form.append("files", f.file, f.path);
      try { await api("/apps", { method: "POST", form }); toast(`${name.value} uploaded`, "ok"); location.hash = `#/app/${enc(name.value)}`; }
      catch (x) { toast(x.message, "bad"); }
    } },
    h("label", { class: "stack" }, "Name", name),
    dz, droppedSaid,
    h("label", { class: "stack" }, "Files or a .zip", files),
    h("label", { class: "stack" }, "or a folder", folder),
    h("button", { class: "primary", type: "submit" }, "Upload"));
  const box = h("div", {}, loopsBrowser(loops));
  show(
    head("Loops", "Each loop is a problem document and its files; it runs or it does not, and a start resumes it.",
      h("a", { class: "btn primary", href: "#/configure" }, "New loop")),
    h("div", { class: "grid-main" }, card(null, box), card("Upload a loop", upload, { cls: "side" })));
  pageRefresh = async () => { if (!box.contains(document.activeElement)) box.replaceChildren(loopsBrowser(await api("/apps"))); };
}

async function newPage() {
  const name = h("input", { placeholder: "application name", required: true });
  const file = h("input", { value: "problem.problem.yaml", size: 28 });
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

async function loopPage(name, owner, tab = "Overview") {
  const qs = owner ? `?owner=${enc(owner)}` : "";
  const q = owner ? `&owner=${enc(owner)}` : "";
  const base = `/api/apps/${enc(name)}`;
  const info = await api(`/apps/${enc(name)}${qs}`);
  const mine = info.mine;
  let st = info.state;
  const header = h("div", {}), banner = h("div", {}), body = h("div", {});
  const tabs = ["Overview", "Live", "Log", "Timeline", "Agent turns", "Results", "Files", "Workbench"];
  const tabBar = h("div", { class: "tabs", role: "tablist" });
  let question = st.question || null;
  const live = liveTree(base, qs, (qq) => { question = qq; drawBanner(); });
  const log = logView(base, qs);
  cleanup.push(() => { live.close(); log.close(); });

  function drawTabs() {
    tabBar.replaceChildren(...tabs.map(t => h("button", { role: "tab", class: t === tab ? "on" : "", "aria-selected": t === tab ? "true" : "false",
      onclick: () => { tab = t; history.replaceState(null, "", `#/${owner ? `u/${enc(owner)}/` : ""}app/${enc(name)}${t === "Overview" ? "" : "/" + t.toLowerCase().replace(" ", "-")}`); drawTabs(); drawBody(); } }, t)));
  }
  function drawHead() {
    const acts = [];
    if (st.running) {
      acts.push(act("Stop after this pass", () => stopLoop(name, false, owner)), act("Stop now", () => stopLoop(name, true, owner), { cls: "danger" }));
    } else if (mine) {
      acts.push(act(st.last_active ? "Start (resume)" : "Start", async () => { if (await startLoop(name)) { await refresh(); tab = "Live"; drawTabs(); drawBody(); } }, { cls: "primary" }));
    }
    if (mine) {
      acts.push(act("Check", async () => {
        const out = h("pre", { class: "log small" }, "Checking in the sandbox…");
        const d = dialog("Check the document", out, [["Close", null]]);
        const r = await api(`/apps/${enc(name)}/check`, { method: "POST" });
        out.textContent = (r.ok ? "Ready to run.\n\n" : "NOT READY\n\n") + r.output;
        await d;
      }));
      if (info.document) acts.push(h("a", { class: "btn", href: `#/app/${enc(name)}/configure` }, "Configure"));
      if (!st.running) acts.push(act("Delete", async () => {
        if (!await confirmDialog(`Delete ${name}?`, "Its document, files, record and log go. This cannot be undone.", { ok: "Delete", danger: true })) return;
        await api(`/apps/${enc(name)}`, { method: "DELETE" }); toast(`${name} deleted`, "ok"); location.hash = "#/";
      }, { cls: "danger" }));
    }
    header.replaceChildren(head(h("span", {}, name, " ", statePill(st), mine ? "" : h("span", { class: "pill" }, `${info.owner}'s · read only`)),
      h("span", {}, info.document ? h("span", { class: "mono" }, info.document) : "", " · ", lastSaid(st),
        st.container ? h("span", { class: "muted" }, ` · sandbox ${st.container}`) : ""), ...acts));
  }
  async function refresh() { const was = st.running; st = await api(`${base.slice(4)}/state${qs}`); drawHead(); drawBanner(); if (was !== st.running && (tab === "Live" || tab === "Overview")) drawBody(); }
  // notes and the agent's question
  const noteText = h("textarea", { rows: 3, placeholder: "A note: it joins the next prompt, or answers the agent's open question." });
  const noteList = h("div", { class: "notes" });
  async function sendNote(text) {
    const r = await api(`/apps/${enc(name)}/notes`, { method: "POST", body: { text } });
    toast(r.ok, "ok"); noteText.value = ""; question = null; drawBanner(); drawNotes();
  }
  async function drawNotes() {
    const notes = await api(`/apps/${enc(name)}/notes${qs}`).catch(() => []);
    noteList.replaceChildren(...notes.slice(-20).reverse().map(n => h("div", { class: "note" }, h("small", { class: "muted" }, n.by, " · ", ago(n.t)), h("div", {}, n.text))));
  }
  function drawBanner() {
    if (!question || !st.running) { banner.replaceChildren(); return; }
    const left = Math.max(0, Math.round(question.asked + question.wait_s - Date.now() / 1000));
    const ans = h("textarea", { rows: 3, placeholder: "Your answer" });
    banner.replaceChildren(h("section", { class: "card ask" }, h("div", { class: "card-head" }, h("h2", {}, "The agent asks"),
        h("span", { class: "muted" }, left ? `answer within ${dur(left)}, or it decides` : "its time is up: it decided")),
      h("pre", { class: "question" }, question.question), mine ? [ans,
      h("div", { class: "form-actions" }, act("Answer", async () => { if (ans.value.trim()) await sendNote(ans.value.trim()); }, { cls: "primary" }))] : ""));
  }
  const notesCard = () => st.running && mine ? card("Notes to the loop", [noteText,
    h("div", { class: "form-actions" }, act("Send", async () => { if (noteText.value.trim()) await sendNote(noteText.value.trim()); })), noteList]) : "";

  // files and the workbench
  const viewer = h("div", { class: "viewer" });
  const fileUrl = (path, dl) => `/api/apps/${enc(name)}/file?path=${enc(path)}${dl ? "&download=1" : ""}${q}`;
  const size = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
  async function openFile(path, dir) {
    viewer.replaceChildren(h("p", { class: "muted" }, "Loading…"));
    if (dir) {
      const list = await api(`/apps/${enc(name)}/files?path=${enc(path)}${q}`);
      viewer.replaceChildren(h("div", { class: "viewer-head" }, h("span", { class: "mono" }, path + "/")), fileList(list));
      return;
    }
    const r = await fetch(fileUrl(path), { credentials: "same-origin" });
    if ((r.headers.get("content-type") || "").startsWith("text/")) {
      const ed = codeEditor(await r.text(), langOf(path), { readonly: !mine });
      viewer.replaceChildren(h("div", { class: "viewer-head" }, h("span", { class: "mono" }, path),
          h("div", { class: "actions" }, mine ? act("Save", async () => {
            await api(`/apps/${enc(name)}/file?path=${enc(path)}`, { method: "PUT", body: { text: ed.textarea.value } }); toast(`${path} saved`, "ok");
          }, { cls: "small" }) : "", h("a", { class: "btn small", href: fileUrl(path, true) }, "Download"))), ed.el);
    } else {
      viewer.replaceChildren(h("div", { class: "viewer-head" }, h("span", { class: "mono" }, path)),
        empty("A binary file.", h("a", { class: "btn", href: fileUrl(path, true) }, "Download")));
    }
  }
  function fileList(list) {
    return h("ul", { class: "files" }, list.map(f => h("li", {},
      h("a", { href: "javascript:void 0", onclick: () => openFile(f.path, f.dir) }, h("span", { class: "ic" }, f.dir ? "▸" : "·"), f.path.split("/").pop() + (f.dir ? "/" : "")),
      f.dir ? "" : h("small", { class: "muted" }, size(f.size)))));
  }
  function adder() {
    const addFiles = h("input", { type: "file", multiple: true });
    const addFolder = h("input", { placeholder: "folder (optional)" });
    const dz = dropZone("Drop files or folders to add them", async (got) => {
      const form = new FormData(); form.append("folder", addFolder.value);
      for (const f of got) form.append("files", f.file, f.path);
      try {
        const r = await api(`/apps/${enc(name)}/files`, { method: "POST", form });
        toast(`Added ${r.written.length} file(s)${addFolder.value ? " into " + addFolder.value : ""}`, "ok"); drawBody();
      } catch (x) { toast(x.message, "bad"); }
    });
    return h("div", {}, dz, h("details", { class: "adder" }, h("summary", {}, "Add files"),
      h("label", { class: "stack" }, "Files or a .zip", addFiles), h("label", { class: "stack" }, "Into folder", addFolder),
      act("Add", async () => {
        if (!addFiles.files.length) { toast("Choose files or a .zip.", "warn"); return; }
        const form = new FormData(); form.append("folder", addFolder.value);
        for (const f of addFiles.files) form.append("files", f, f.name);
        const r = await api(`/apps/${enc(name)}/files`, { method: "POST", form });
        toast(`Added ${r.written.length} file(s)`, "ok"); drawBody();
      }, { cls: "small primary" })));
  }

  /** Where the time goes (D694): one start's phases as bars in lanes by kind of work, and per
      kind the busy time (parallel work once), its share of the wall clock and the summed time. */
  const PALETTE = ["#5b8def", "#e8804f", "#4fb286", "#b176e0", "#d9b440", "#e0607e", "#48b3c9", "#8f9aa6", "#a3c956", "#c98a56"];
  let tlStart = null, tlPass = "";
  async function timelineView() {
    const params = new URLSearchParams(owner ? { owner } : {});
    if (tlStart != null) params.set("start", tlStart);
    const t = await api(`/apps/${enc(name)}/timeline?${params}`);
    if (tab !== "Timeline") return;
    if (!t.bars.length) { body.replaceChildren(card(null, empty("No phase in the journal yet."))); return; }
    const color = {}; t.kinds.forEach((k, i) => { color[k.kind] = PALETTE[i % PALETTE.length]; });
    const startSel = h("select", { onchange: (e) => { tlStart = Number(e.target.value); tlPass = ""; timelineView(); } },
      t.starts.slice().reverse().map(st => h("option", { value: st.index, selected: st.index === t.start },
        `start ${st.index + 1} · ${st.t0 ? new Date(st.t0 * 1000).toLocaleString() : "?"}${st.t0 && st.t1 ? " · " + dur(st.t1 - st.t0) : ""}`)));
    const passSel = h("select", { onchange: (e) => { tlPass = e.target.value; draw(); } },
      h("option", { value: "" }, `every pass (${t.passes.length})`), t.passes.map((p, i) => h("option", { value: String(i), selected: tlPass === String(i) }, `pass ${i + 1}`)));
    const kindsTable = h("table", { class: "list compact kinds" },
      h("thead", {}, h("tr", {}, h("th", {}, "Kind of work"), h("th", { class: "num" }, "Phases"), h("th", { class: "num" }, "Busy"),
        h("th", {}, "Share of the wall clock"), h("th", { class: "num", title: "Every phase's own time added: above Busy when they ran side by side" }, "Summed"),
        h("th", { class: "num", title: "Summed over busy: how many ran at once, on average" }, "At once"))),
      h("tbody", {}, t.kinds.map(k => h("tr", {},
        h("td", {}, h("i", { class: "sw", style: `background:${color[k.kind]}` }), k.kind),
        h("td", { class: "num mono" }, String(k.count)), h("td", { class: "num mono" }, dur(k.busy)),
        h("td", {}, h("div", { class: "share" }, h("div", { class: "share-bar", style: `width:${Math.min(100, k.share * 100).toFixed(1)}%;background:${color[k.kind]}` }),
          h("span", {}, `${(k.share * 100).toFixed(k.share < 0.1 ? 1 : 0)}%`))),
        h("td", { class: "num mono" }, dur(k.summed)), h("td", { class: "num mono" }, k.busy > 0 ? `×${(k.summed / k.busy).toFixed(1)}` : "")))));
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
          return sv("rect", { x: x0, y: T + i * lane + 3, width: Math.max(1.5, x1 - x0), height: lane - 6, rx: 2,
            fill: color[x.kind], class: `bar${x.failed ? " failed" : ""}${x.running ? " running" : ""}` },
            sv("title", {}, `${x.name}${x.why ? " · " + x.why : ""}\n${dur(x.t1 - x.t0)}${x.running ? " so far" : ""}${x.failed ? " · failed" : ""}`)); })));
    }
    draw();
    body.replaceChildren(
      card(null, h("div", { class: "tl-head" }, startSel, passSel,
        h("span", { class: "muted" }, t.running ? "running · " : "", `${dur(t.wall)} on the wall clock · ${t.bars.length} phase(s) · ${t.passes.length} pass(es)`))),
      card("Phases over time", [chartBox, h("p", { class: "muted small" }, "Dashed lines: a pass begins. Hover a bar for its phase.")]),
      card("Where the time goes", [kindsTable,
        h("p", { class: "muted small" }, "Each phase that does the work (a tool, an agent, a model call) counts in the kind of its nearest named phase. Busy: the wall clock it held, work side by side counted once.")]));
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
      detail.replaceChildren(h("p", { class: "muted" }, "Loading…"));
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
    const charts = nums.length ? h("div", { class: "grid-2 charts" }, card("Pareto front", paretoBox), card("Improvement over time", [timeBox, thinned])) : "";
    return h("div", {},
      card(null, h("div", { class: "results-head" }, h("div", {}, h("h2", {}, "Objective"), h("p", { class: "muted" }, r.objectives,
          r.total > r.designs.length ? ` · the newest ${r.designs.length} of ${r.total} designs` : "")),
        h("div", { class: "actions" }, r.answer ? h("a", { class: "btn small", href: `/api/apps/${enc(name)}/file?path=runs/answer.json&download=1${q}` }, "The answer (JSON)") : "",
          h("a", { class: "btn small", href: `${base}/report${qs}`, target: "_blank", rel: "noopener" }, "Open the report")))),
      charts,
      h("div", { class: "split results" }, card(null, [chipBox, table]), card(null, detail, { cls: "detail-card" })));
  }

  const goTab = (t) => { tab = t; drawTabs(); drawBody(); };
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
        h("div", { class: "decision-nums" }, (r.metrics || []).filter(m => dec.numbers[m] != null).slice(0, 6).map(m => {
          const lim = (r.limits || []).find(l => l.metric === m), ok = dec.meets[m];
          return h("div", { class: "num-cell" + (ok === false ? " misses" : ok === true ? " meets" : "") }, h("small", {}, m),
            h("div", { class: "big mono" }, num4(dec.numbers[m])), lim ? h("small", { class: "muted" }, `${lim.direction === "maximize" ? "≥" : "≤"} ${lim.goal}${ok === true ? " ✓" : ok === false ? " ✗" : ""}`) : "");
        })),
        dec.why.length ? h("ul", { class: "misses" }, dec.why.map(w => h("li", {}, w))) : ""],
        { actions: [h("button", { class: "small", onclick: () => goTab("Results") }, "All results")] })
      : card("The decision", empty(designs.length ? "No decision yet." : "No design measured yet."));
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
          use.total.cost_usd ? ` · $${use.total.cost_usd.toFixed(2)}` : ""], () => goTab("Agent turns")) : "",
        stat("Objective", h("span", { class: "obj-line" }, r.objectives || "—"), "", null)),
      q0 && st.running ? h("section", { class: "card ask" }, h("div", { class: "card-head" }, h("h2", {}, "The agent asks"),
        h("button", { class: "small primary", onclick: () => goTab("Live") }, "Answer")), h("pre", { class: "question" }, q0.question)) : "",
      h("div", { class: "grid-2" }, decisionCard,
        card("Best so far", objs.length ? objs.map(o => bestChart(r.rows || [], o, r.passes)) : empty("The objective has no number to chart."))),
      h("div", { class: "grid-2" },
        card("Latest notes", notes.length ? h("div", { class: "notes" }, notes.slice(-5).reverse().map(n => h("div", { class: "note" },
          h("small", { class: "muted" }, n.by, " · ", ago(n.t)), h("div", {}, n.text)))) : empty(st.running && mine ? "No note yet: send one from the Live tab." : "No note yet.")),
        card("Agents' workbench", bench.length ? h("ul", { class: "bench" }, bench.slice(0, 5).map(b => h("li", {},
          h("a", { href: "javascript:void 0", onclick: () => goTab("Workbench") }, b.path.split("/").pop()), h("small", { class: "muted" }, " ", ago(b.mtime)),
          b.first ? h("div", { class: "first" }, b.first) : ""))) : empty("Empty."))));
  }

  async function drawBody() {
    if (tab === "Overview") {
      if (!st.running && !st.last_active) {
        body.replaceChildren(card(null, empty("This loop has not run yet.", mine ? act("Start", async () => { if (await startLoop(name)) { await refresh(); goTab("Live"); } }, { cls: "primary" }) : "")));
        return;
      }
      body.replaceChildren(h("p", { class: "muted" }, "Loading…"));
      await overview();
      return;
    }
    if (tab === "Live") {
      if (!st.running && !st.last_active) {
        body.replaceChildren(card(null, empty("This loop has not run yet.", mine ? act("Start", async () => { if (await startLoop(name)) { await refresh(); drawBody(); } }, { cls: "primary" }) : "")));
        return;
      }
      body.replaceChildren(h("div", { class: "split" }, card(null, [st.running ? "" : h("p", { class: "muted" }, "Not running: the last start's tree."), live.tree], { cls: "tree-card" }),
        h("div", { class: "side-col" }, card(null, live.detail, { cls: "detail-card" }), notesCard(), card(null, live.stand, { cls: "stand-card" }))));
      live.draw(); drawNotes();
    } else if (tab === "Timeline") {
      body.replaceChildren(h("p", { class: "muted" }, "Loading…"));
      await timelineView();
    } else if (tab === "Log") {
      body.replaceChildren(card(null, log.el, { cls: "log-card" }));
      log.render();
    } else if (tab === "Agent turns") {
      body.replaceChildren(h("p", { class: "muted" }, "Loading…"));
      const [{ turns }, use] = await Promise.all([api(`/apps/${enc(name)}/turns${qs}`), api(`/apps/${enc(name)}/usage${qs}`)]);
      const one = h("div", { class: "detail" }, empty("Select a turn to read its prompt, reply and tool calls."));
      const pick = async (t, tr) => {
        for (const x of tr.parentNode.children) x.classList.remove("sel"); tr.classList.add("sel");
        const full = (await api(`/apps/${enc(name)}/turns?k=${t.k}${q}`)).turns[0] || {};
        one.replaceChildren(h("div", { class: "detail-head" }, h("h2", {}, full.agent || full.model || full.kind), h("span", { class: "muted" }, ago(full.ts), " · ", dur(full.seconds))),
          ...["error", "reply", "prompt", "stderr"].filter(k => full[k]).map(k => h("div", { class: "blk" }, h("h3", {}, k), proseBlock(String(full[k])))),
          ...((full.hops || []).length ? [h("h3", {}, "Tool calls"), ...(full.hops || []).map(x => h("pre", { class: "val" }, x))] : []));
      };
      const tokOf = (t) => { const n = t.notes && typeof t.notes === "object" ? t.notes : {};
        const i = t.tokens_in ?? n.input_tokens, o = t.tokens_out ?? n.output_tokens;
        return i == null && o == null ? "" : `${fmtTok(i || 0)} → ${fmtTok(o || 0)}`; };
      body.replaceChildren(usageCard(use), h("div", { class: "split" },
        card(null, turns.length ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, "Who"), h("th", {}, "When"), h("th", {}, "Took"),
            h("th", { class: "num", title: "tokens in → out" }, "Tokens"), h("th", {}, ""))),
          h("tbody", {}, turns.slice().reverse().map(t => { const tr = h("tr", { class: "clickable", onclick: () => pick(t, tr) },
            h("td", { class: "strong" }, t.agent || t.model || t.kind), h("td", {}, ago(t.ts)), h("td", { class: "muted" }, dur(t.seconds)),
            h("td", { class: "num mono muted" }, tokOf(t)),
            h("td", {}, t.error ? h("span", { class: "pill bad" }, "error") : t.ok === false ? h("span", { class: "pill bad" }, `exit ${t.rc}`) : h("span", { class: "pill ok" }, "ok"))); return tr; })))
          : empty("No model or agent turn yet.")),
        card(null, one, { cls: "detail-card" })));
    } else if (tab === "Results") {
      body.replaceChildren(h("p", { class: "muted" }, "Loading…"));
      const r = await api(`/apps/${enc(name)}/results${qs}`);
      if (!r.campaign || !r.designs.length) { body.replaceChildren(card(null, empty("No result yet: a design is a result once a stage measured it."))); return; }
      body.replaceChildren(resultsView(r));
    } else if (tab === "Files") {
      const files = await api(`/apps/${enc(name)}/files${qs}`);
      body.replaceChildren(h("div", { class: "grid-app" }, card("Files", [fileList(files), mine ? adder() : ""], { cls: "files-card" }), card(null, viewer, { cls: "viewer-card" })));
      if (info.document) openFile(info.document, false);
    } else if (tab === "Workbench") {
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
    // the Overview follows a running loop (D693): its numbers and charts every 10 s
    if (++beat % 2 === 0 && (tab === "Overview" || tab === "Timeline") && st.running && !document.hidden && !busy) {
      busy = true; try { await (tab === "Overview" ? overview() : timelineView()); } catch (_) { /* the next beat */ } finally { busy = false; }
    }
  }, 5000);
  cleanup.push(() => clearInterval(tick));
  pageRefresh = () => refresh();
  drawHead(); drawTabs(); drawBanner(); drawBody();
  show(header, banner, tabBar, body);
}

/** The log: follow, wrap, a filter (text or /regex/), problems only, download; the loop's starts to
    pick one from, and the previous or next problem to jump to (D692). */
function logView(base, qs) {
  const lines = []; let partial = "", seen = 0;
  const MAX = 50000, SHOWN = 4000;
  const box = h("div", { class: "logview" });
  const follow = h("input", { type: "checkbox", checked: true });
  const wrap = h("input", { type: "checkbox" });
  const problems = h("input", { type: "checkbox" });
  const filter = h("input", { placeholder: "filter (text or /regex/)", class: "filter" });
  const count = h("span", { class: "muted" });
  const startSel = h("select", { class: "starts", title: "Show one start of the loop" });
  const PROBLEM = /\b(error|errors|traceback|exception|failed|failure|refused|did not build|timed out|killed)\b|✗/i;
  const WARN = /\b(warning|nudged|retry|stopping|interrupted|could not)\b/i;
  const GOOD = /\b(ADMITTED|DECISION|passed|decided)\b/;
  const MARK = /^── started (.+?) ──$/;
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
    return h("div", { class: "ln " + cls, "data-n": String(l.n) }, h("span", { class: "no" }, String(l.n)), h("span", { class: "tx" }, l.text || " "));
  }
  function drawStarts() {
    const cur = startSel.value;
    startSel.replaceChildren(h("option", { value: "-1" }, `All starts (${starts.length})`),
      ...starts.map((st, i) => h("option", { value: String(i) }, MARK.exec(st.text)[1])));
    startSel.value = cur && Number(cur) < starts.length ? cur : String(startIdx);
  }
  function render() {
    const shown = lines.filter(keep);
    box.replaceChildren(...(shown.length > SHOWN ? [h("div", { class: "ln more" }, `… ${shown.length - SHOWN} earlier line(s): download the log for all`)] : []),
      ...shown.slice(-SHOWN).map(lineEl));
    count.textContent = `${shown.length === lines.length ? lines.length : shown.length + " of " + lines.length} line(s)`;
    if (follow.checked) box.scrollTop = box.scrollHeight;
  }
  function add(chunk) {
    const parts = (partial + chunk).split("\n"); partial = parts.pop();
    const fresh = parts.map(t => { const l = { n: ++seen, text: t }; lines.push(l); if (MARK.test(t)) starts.push(l); return l; });
    if (lines.length > MAX) lines.splice(0, lines.length - MAX);
    if (fresh.some(l => MARK.test(l.text))) drawStarts();
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    for (const l of fresh) if (keep(l)) box.append(lineEl(l));
    while (box.childElementCount > SHOWN + 200) box.firstChild.remove();
    count.textContent = `${lines.length} line(s)`;
    if (follow.checked && (atEnd || fresh.length)) box.scrollTop = box.scrollHeight;
  }
  /** The previous (-1) or next (+1) problem line from the middle of the view: scrolled to, flashed. */
  function jump(dir) {
    const rows = [...box.querySelectorAll(".ln.bad")];
    if (!rows.length) { toast("No problem line in view.", "info", { timeout: 2500 }); return; }
    follow.checked = false;
    const mid = box.scrollTop + box.clientHeight / 2;
    const target = dir > 0 ? rows.find(r => r.offsetTop > mid + 4) : rows.reverse().find(r => r.offsetTop < mid - 4);
    if (!target) { toast(dir > 0 ? "No later problem." : "No earlier problem.", "info", { timeout: 2500 }); return; }
    box.scrollTop = target.offsetTop - box.clientHeight / 2;
    target.classList.remove("flash"); void target.offsetWidth; target.classList.add("flash");
  }
  box.addEventListener("scroll", () => {                       // scrolling up pauses the follow
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    if (!atEnd && follow.checked) follow.checked = false;
  });
  follow.addEventListener("change", () => { if (follow.checked) box.scrollTop = box.scrollHeight; });
  wrap.addEventListener("change", () => box.classList.toggle("wrap", wrap.checked));
  problems.addEventListener("change", render);
  startSel.addEventListener("change", () => { startIdx = Number(startSel.value); render(); });
  let t; filter.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { makeMatcher(); render(); }, 150); });
  drawStarts();
  const bar = h("div", { class: "toolbar" }, startSel,
    h("label", { class: "check" }, follow, "follow"), h("label", { class: "check" }, wrap, "wrap"),
    h("label", { class: "check" }, problems, "problems only"), filter,
    h("div", { class: "actions" }, h("button", { class: "small", title: "The previous problem", onclick: () => jump(-1) }, "◀ problem"),
      h("button", { class: "small", title: "The next problem", onclick: () => jump(1) }, "problem ▶")),
    count, h("a", { class: "btn small", href: `${base}/log/raw${qs}` }, "Download"));
  const pill = streamPill();
  bar.append(pill.el);
  const es = followStream(`${base}/log${qs}`, "log", add, pill.set);
  return { el: h("div", {}, bar, box), close: () => es.close(), render };
}

/** The live task tree: follow the running task, collapse what finished, search. */
function liveTree(base, qs, onQuestion) {
  const nodes = new Map(), roots = [], standings = new Map();
  let selected = null, dirty = true;
  const open = new Map();                 // id -> true/false, what the user chose
  const follow = h("input", { type: "checkbox", checked: true });
  const collapse = h("input", { type: "checkbox", checked: true });
  const search = h("input", { placeholder: "search tasks", class: "filter" });
  const treeBox = h("div", { class: "tree" }), detail = h("div", { class: "detail" }), stand = h("div", { class: "standings" });
  function onEvent(e) {
    if (e.ev === "hello") {                        // a new start: its tree begins afresh (D689)
      nodes.clear(); roots.length = 0; standings.clear(); open.clear(); selected = null; dirty = true;
      return;
    }
    if (e.ev === "start") {
      const n = { id: e.id, name: e.name, why: e.why, params: e.params, t0: e.t, fields: {}, kids: [], parent: null };
      nodes.set(e.id, n);
      const p = e.parent != null && nodes.get(e.parent);
      if (p) { n.parent = p; p.kids.push(n); } else roots.push(n);
    } else if (e.ev === "update") { const n = nodes.get(e.id); if (n) Object.assign(n.fields, e.fields); }
    else if (e.ev === "end") { const n = nodes.get(e.id); if (n) { n.t1 = e.t; n.seconds = e.seconds; n.failed = e.failed; n.output = e.output; } }
    else if (e.ev === "publish") standings.set(e.key, e.payload);
    else if (e.ev === "mark" && e.name === "question") { try { onQuestion(JSON.parse(e.why)); } catch (_) {} }
    dirty = true;
  }
  const running = (n) => n.t1 == null;
  const failedBelow = (n) => n.failed || n.kids.some(failedBelow);
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
  function isOpen(n) {
    if (open.has(n.id)) return open.get(n.id);
    if (!collapse.checked) return true;
    return running(n) || failedBelow(n);
  }
  function matches(n, q) { return (n.name + " " + (n.why || "")).toLowerCase().includes(q); }
  function visibleUnder(n, q) { return matches(n, q) || n.kids.some(k => visibleUnder(k, q)); }
  function draw() {
    const now = Date.now() / 1000;
    if (follow.checked) { const t = followTarget(); if (t) selected = t; }
    const q = search.value.trim().toLowerCase();
    const row = (n) => {
      if (q && !visibleUnder(n, q)) return "";
      const hasKids = n.kids.length > 0, opened = q ? true : isOpen(n);
      const state = running(n) ? "running" : n.failed ? "failed" : "done";
      return h("div", { class: "tnode" },
        h("div", { class: `node ${state}${n === selected ? " sel" : ""}${q && matches(n, q) ? " hit" : ""}`,
            onclick: () => { selected = n; follow.checked = false; draw(); } },
          h("span", { class: "caret", onclick: (e) => { if (!hasKids) return; e.stopPropagation(); open.set(n.id, !opened); draw(); } }, hasKids ? (opened ? "▾" : "▸") : ""),
          h("span", { class: "st" }, running(n) ? "●" : n.failed ? "✗" : "✓"),
          h("span", { class: "nm" }, n.name), n.why ? h("span", { class: "why" }, n.why) : "",
          hasKids && !opened ? h("span", { class: "kidsn" }, String(n.kids.length)) : "",
          h("span", { class: "dur" }, dur(running(n) ? now - n.t0 : n.seconds))),
        hasKids && opened ? h("div", { class: "kids" }, n.kids.slice(-300).map(row)) : "");
    };
    treeBox.replaceChildren(...(roots.length ? roots.slice(-150).map(row) : [empty("Waiting for the run's first events…")]));
    drawDetail(now);
    drawStandings();
    dirty = false;
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
      const shown = new Set(["step", "steps", "judged", "proven", "parts_total", "measured", "at", "axes", "front", "parts"]);
      const rest = Object.entries(v).filter(([k]) => !shown.has(k));
      if (rest.length) out.push(h("div", { class: "rest" }, rest.map(([k, x]) => h("div", { class: "kv row-kv" }, h("span", { class: "k" }, k), h("span", { class: "mono" }, short(x))))));
    }
    stand.replaceChildren(...(out.length ? [h("h2", {}, "Standings"), ...out] : [h("p", { class: "muted" }, "No standings yet.")]));
  }
  function drawDetail(now) {
    if (!selected) { detail.replaceChildren(empty("Select a task to see its parameters, live fields and output.")); return; }
    const n = selected;
    const block = (title, obj) => obj && Object.keys(obj).length ? h("div", { class: "blk" }, h("h3", {}, title), Object.entries(obj).map(([k, v]) => {
      const text = typeof v === "string" ? v : JSON.stringify(v, null, 1);
      const long = text.length > 120 || text.includes("\n");
      return h("div", { class: "kv" }, h("div", { class: "k" }, k), long ? h("pre", { class: "val" }, text) : h("div", { class: "val mono" }, text));
    })) : "";
    const path = []; for (let p = n.parent; p; p = p.parent) path.unshift(p.name);
    detail.replaceChildren(
      h("div", { class: "detail-head" }, h("h2", {}, n.name),
        h("span", { class: `pill ${running(n) ? "live" : n.failed ? "bad" : "ok"}` }, running(n) ? "running" : n.failed ? "failed" : "done"),
        h("span", { class: "muted" }, dur(running(n) ? now - n.t0 : n.seconds))),
      path.length ? h("p", { class: "crumbs" }, path.join(" › ")) : "",
      n.why ? h("p", { class: "muted" }, n.why) : "",
      block("Parameters", n.params), block(running(n) ? "So far" : "Live fields", n.fields), block("Output", n.output));
    for (const pre of detail.querySelectorAll("pre.val")) pre.scrollTop = pre.scrollHeight;   // a live tail shows its end
  }
  search.addEventListener("input", draw);
  follow.addEventListener("change", draw);
  collapse.addEventListener("change", () => { open.clear(); draw(); });
  const pill = streamPill();
  const es = followStream(`${base}/events${qs}`, "events", onEvent, pill.set);
  const tick = setInterval(() => { if (dirty || [...nodes.values()].some(running)) draw(); }, 1000);
  const bar = h("div", { class: "toolbar" }, h("label", { class: "check" }, follow, "follow the running task"),
    h("label", { class: "check" }, collapse, "collapse finished"), search, pill.el);
  return { tree: h("div", {}, bar, treeBox), detail, stand, draw, close: () => { es.close(); clearInterval(tick); } };
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

async function configurePage(name) {
  const C = window.FluxCrafter;
  if (!C) { show(card(null, empty("The configurator's script did not load."))); return; }
  if (!crafterCatalog) {
    crafterCatalog = await fetch("/crafter-assets/tools.json").then(r => r.json()).catch(() => []);
    C.setCatalog(crafterCatalog);
  }
  const host = h("div", { class: "flux-crafter" });
  if (name) {                                           // an existing loop, read back
    const v = await api(`/apps/${enc(name)}/document`);
    const got = C.fromDoc(v.raw, v.normal || v.raw);
    show(head(h("span", {}, "Configure ", h("a", { href: `#/app/${enc(name)}` }, name)),
        h("span", {}, h("span", { class: "mono" }, v.document), " · saving rewrites it from this form; comments are not kept",
          got.kept.length ? "; what the form does not edit is kept as written" : "")),
      v.error ? h("p", { class: "callout bad" }, "The loader refuses the document as it stands: " + v.error) : "",
      host);
    C.mount(host, false, { state: got.state, notes: got.notes, saveLabel: "Save to " + v.document,
      save: async (yaml) => {
        // D693: what the save changes, line by line, before it writes
        const p = await api(`/apps/${enc(name)}/document/preview`, { method: "POST", body: { text: yaml, kept: got.kept } });
        if (p.before === p.after) { toast("Nothing changes: the document already says this.", "info"); return "No change."; }
        const ops = lineDiff(p.before, p.after);
        const plus = ops.filter(o => o[0] === "+").length, minus = ops.filter(o => o[0] === "-").length;
        const ok = await dialog(`Save ${p.document}?`, h("div", {}, h("p", { class: "muted" }, `${plus} line(s) added, ${minus} removed.`), diffView(ops)),
          [["Cancel", false], ["Save", true, "primary"]]);
        if (!ok) return "Not saved.";
        const r = await api(`/apps/${enc(name)}/document`, { method: "PUT", body: { text: yaml, kept: got.kept } });
        toast(r.ok, r.error ? "warn" : "ok"); return r.ok;
      } });
    return;
  }
  const appName = h("input", { placeholder: "application name", required: true });
  show(head("New loop", h("span", {}, "Build the document, then create the application; add its files (golden model, scripts) on its page. ",
      h("a", { href: "#/new" }, "Or write the YAML yourself."))),
    card(null, h("label", { class: "stack narrow" }, "Application name", appName)), host);
  C.mount(host, false, { saveLabel: "Create the application", save: async (yaml, state) => {
    if (!appName.value.trim()) { appName.focus(); throw new Error("Name the application first (above)."); }
    const id = String(state.id || "").trim() || "my_problem";
    await api("/apps/from-text", { method: "POST", body: { name: appName.value.trim(), filename: `${id}.problem.yaml`, text: yaml } });
    toast(`${appName.value.trim()} created`, "ok");
    setTimeout(() => { location.hash = `#/app/${enc(appName.value.trim())}`; }, 400);
    return "Created.";
  } });
}

// ================================================================ admin and account
/** The admin's pages (D695): every loop and the controls over all of them, what the machine
    holds up (containers, disk, caches), users with their limits and usage, the audit trail. */
const ADMIN_TABS = { "": "Loops", resources: "Resources", users: "Users", audit: "Audit" };
const bytes = (n) => n == null ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : n < 1073741824 ? `${(n / 1048576).toFixed(1)} MB` : `${(n / 1073741824).toFixed(2)} GB`;
function meter(frac, cls = "") {
  const f = Math.max(0, Math.min(1, frac || 0));
  return h("div", { class: `meter ${cls}${f > 0.9 ? " high" : f > 0.75 ? " mid" : ""}` }, h("div", { style: `width:${(f * 100).toFixed(1)}%` }));
}
async function adminPage(sub = "") {
  const tab = ADMIN_TABS[sub] ? sub : "";
  const tabBar = h("div", { class: "tabs", role: "tablist" }, Object.entries(ADMIN_TABS).map(([k, label]) =>
    h("a", { role: "tab", class: k === tab ? "on" : "", href: `#/admin${k ? "/" + k : ""}` }, label)));
  const body = h("div", {});
  show(head("Admin", "Every loop and the controls over them, what the machine holds up, users and their limits, the audit trail."), tabBar, body);
  if (tab === "") return adminLoops(body);
  if (tab === "resources") return adminResources(body);
  if (tab === "users") return adminUsers(body);
  const audit = await api("/audit");
  body.replaceChildren(card(null, h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Who"), h("th", {}, "What"), h("th", {}, "Detail"))),
    h("tbody", {}, audit.map(x => h("tr", {}, h("td", { class: "muted" }, ago(x.t)), h("td", {}, x.user || ""), h("td", {}, x.action), h("td", { class: "mono muted" }, x.detail)))))));
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
        h("tr", { class: "sum" }, h("td", {}, "All loops"), h("td", {}), ...["inputs", "record", "log", "workbench", "cache", "total"].map(k => h("td", { class: "num mono strong" }, bytes(totalOf(k)))), h("td", {}))))),
      { actions: [h("span", { class: "muted small" }, "sizes kept for a minute")] });
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
        act("Measure again", load, { cls: "small" })), machineCard, contCard, diskCard, cacheCard);
  }
  await load();
  const t = setInterval(() => { if (!document.hidden && !body.contains(document.querySelector("dialog.dlg"))) load().catch(() => {}); }, 15000);
  cleanup.push(() => clearInterval(t));
}

async function adminUsers(body) {
  const [users, use, res] = await Promise.all([api("/users"), api("/admin/usage").catch(() => []), api("/admin/resources").catch(() => null)]);
  const name = h("input", { placeholder: "name" }); const pw = h("input", { type: "password", placeholder: "password (10+)" });
  const admin = h("input", { type: "checkbox" });
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
        h("td", { class: "strong" }, u.name), h("td", {}, h("span", { class: "pill" }, u.role), u.disabled ? h("span", { class: "pill bad" }, "disabled") : ""),
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
    h("div", { class: "row add-user" }, name, pw, h("label", { class: "check" }, admin, "admin"),
      act("Add user", async () => {
        await api("/users", { method: "POST", body: { name: name.value, password: pw.value, role: admin.checked ? "admin" : "user" } });
        toast(`${name.value} added`, "ok"); route();
      }, { cls: "primary" }))]));
}

async function accountPage() {
  const st = await api("/settings");
  const inputs = {};
  const labels = { FLUX_REMOTE_BASE_URL: "Model endpoint (OpenAI-compatible URL)", FLUX_REMOTE_MODEL: "Model name on it",
    FLUX_LLM_MODEL: "Local model (Ollama tag)", OLLAMA_BASE_URL: "Ollama URL", FLUX_LLM_TIMEOUT_S: "Seconds per model request",
    FLUX_REMOTE_API_KEY: "Endpoint key", OPENROUTER_API_KEY: "OpenRouter key", ANTHROPIC_API_KEY: "Anthropic key (Claude Code agents)",
    OPENAI_API_KEY: "OpenAI key (Codex agents)" };
  async function save(values) { await api("/settings", { method: "PUT", body: { values } }); toast("Settings saved", "ok"); route(); }
  const row = (k, secret) => {
    const cur = st.values[k];
    inputs[k] = h("input", { type: secret ? "password" : "text", autocomplete: "off",
      placeholder: secret ? (cur ? "set · type to replace" : "not set") : "", value: secret ? "" : (cur || "") });
    return [h("span", { class: "lbl" }, labels[k] || k), h("span", { class: "inline" }, inputs[k],
      cur ? act("Clear", () => save({ [k]: null }), { cls: "small" }) : "")];
  };
  const pw = h("input", { type: "password", autocomplete: "new-password" });
  const mine = await api("/usage").catch(() => null);
  show(head("Account", `Logged in as ${me.name}`),
    mine && mine.turns ? card("My usage", h("p", {}, `${mine.turns} model and agent turn(s) over ${mine.loops} loop(s), ${dur(mine.seconds)}`,
      mine.counted ? `, ${fmtTok(mine.tokens_in)} tokens in and ${fmtTok(mine.tokens_out)} out` : "",
      mine.cost_usd ? `, $${mine.cost_usd.toFixed(2)} as the agents priced it` : "", ".")) : "",
    card("Model for my runs", [
      h("p", { class: "muted" }, "Empty: the server's model. With your own endpoint, none of the server's keys go to your runs. Keys are stored encrypted and never shown again."),
      h("div", { class: "grid2" }, ...st.public.map(k => row(k, false)).flat(), ...st.secret.map(k => row(k, true)).flat()),
      h("div", { class: "form-actions" }, act("Save", () => {
        const values = {};
        for (const k of st.public) if ((inputs[k].value || "") !== (st.values[k] || "")) values[k] = inputs[k].value || null;
        for (const k of st.secret) if (inputs[k].value) values[k] = inputs[k].value;
        return save(values);
      }, { cls: "primary" }))]),
    h("div", { class: "grid-2" },
      card("Password", [h("label", { class: "stack" }, "New password (10+)", pw),
        h("div", { class: "form-actions" }, act("Change", async () => { await api("/password", { method: "POST", body: { text: pw.value } }); pw.value = ""; toast("Password changed", "ok"); }))]),
      card("Notifications", [h("p", { class: "muted" }, "You are told when a loop stops, fails, or its agent asks a question, in the page and in the bell."),
        "Notification" in window ? (Notification.permission === "granted" ? h("p", {}, "Desktop notifications are on.")
          : Notification.permission === "denied" ? h("p", { class: "muted" }, "Desktop notifications are blocked in this browser's settings.")
          : act("Allow desktop notifications", async () => { await Notification.requestPermission(); route(); })) : ""])));
}

// ================================================================ routing
async function route() {
  for (const f of cleanup.splice(0)) f();
  pageRefresh = null;
  for (const d of document.querySelectorAll("dialog.dlg")) d.dispatchEvent(new Event("cancel"));   // a dialog belongs to its page
  const hash = location.hash || "#/";
  if (hash === "#/login") { drawNav(); return loginPage(); }
  if (!me) { try { me = await api("/me"); pollLoops(); } catch (_) { return; } }
  drawNav();
  const TABS = { "": "Overview", live: "Live", log: "Log", timeline: "Timeline", "agent-turns": "Agent turns", results: "Results", files: "Files", workbench: "Workbench" };
  try {
    let m;
    if ((m = hash.match(/^#\/app\/([^/]+)\/configure$/))) return await configurePage(decodeURIComponent(m[1]));
    if ((m = hash.match(/^#\/app\/([^/]+)(?:\/([a-z-]+))?$/))) return await loopPage(decodeURIComponent(m[1]), null, TABS[m[2] || ""] || "Overview");
    if ((m = hash.match(/^#\/u\/([^/]+)\/app\/([^/]+)(?:\/([a-z-]+))?$/))) return await loopPage(decodeURIComponent(m[2]), decodeURIComponent(m[1]), TABS[m[3] || ""] || "Overview");
    if (hash === "#/new") return await newPage();
    if (hash === "#/configure") return await configurePage(null);
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
const themeBtn = h("button", { class: "small theme", title: "Theme: system, light or dark" });
themeBtn.addEventListener("click", () => { const order = ["system", "light", "dark"]; applyTheme(order[(order.indexOf(theme()) + 1) % 3]); themeBtn.textContent = THEMES[theme()]; });
themeBtn.textContent = THEMES[theme()];

function drawNav() {
  const here = location.hash || "#/";
  const link = (href, text, on) => h("a", { href, class: on ? "on" : "" }, text);
  document.getElementById("nav").replaceChildren(...(me ? [
    link("#/", "Loops", here === "#/" || (here.startsWith("#/app") && !here.endsWith("/configure")) || here.startsWith("#/u/")),
    link("#/configure", "New loop", here === "#/configure" || here === "#/new"),
    me.role === "admin" ? link("#/admin", "Admin", here.startsWith("#/admin")) : ""] : []));
  drawBell();
  document.getElementById("who").replaceChildren(themeBtn, ...(me ? [h("div", { class: "bell-wrap" }, bellBtn, bellMenu), h("a", { href: "#/account", class: "me" }, me.name),
    h("button", { class: "small", onclick: async () => { await api("/logout", { method: "POST" }).catch(() => {}); me = null; location.hash = "#/login"; } }, "Log out")] : []));
}
window.addEventListener("hashchange", route);
route();
