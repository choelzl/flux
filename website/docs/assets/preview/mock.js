// The docs' Preview app (preview/app.html): the web app's own modules -- published from flux_web/static at assets/app/
// by the site's hook (website/hooks.py) -- over this mock instead of a server. It answers the app's calls to /api from
// recorded fixtures (preview/data/, website/tools/preview_record.py), replays the recorded live run of a loop as its
// stream (EventSource), keeps writes in memory (gone on reload) and refuses, with a word, what needs a server. Nothing
// outside /api is touched: the app's files, the configurator's model and catalog are the site's own.
//
// The fixtures: index.json (who, which loops, which runs); users/<user>.json, a user's own answers (/me, /apps, Admin);
// loops/<owner>/<loop>.json, a loop's, by the path under /apps/<loop> with its query sorted ("/file?path=README.md");
// runs/<owner>/<loop>.json, a live run: its stream's messages and the page's polled answers by recorded time. An
// answer is {j: json} or {x: text}, `s` its status when not 200; a stream {stream: {events, live}}. Recorded times are
// moved to now (a "finished 2 h ago" stays 2 h ago), a replay's to when it started.

import { toast } from "../app/notices.js";
import { host } from "../app/configure/host.js";

const DATA = new URL("../../preview/data/", import.meta.url).href;
/** a recorded run's seconds per second of replay */
export const SPEED = 4;
const realFetch = window.fetch.bind(window);
const RealES = window.EventSource;
const RealXHR = window.XMLHttpRequest;
const NOW = () => Date.now() / 1000;

// ------------------------------------------------------------------------------------------------ what the preview holds
const P = {
  index: null, user: null, shift: 0,
  files: new Map(),                 // fixture file -> Promise<Map(key -> answer)>
  over: new Map(),                  // "owner/loop" -> its writes: {files: Map(path -> text|null), notes, state, document, ideas, created}
  replays: new Map(),               // "owner/loop" -> {run, t0, d, stopped}
  sources: new Set(),               // the open fake EventSources
  made: [],                         // loops made in the preview: [{owner, name, from}]
  gone: new Set(),                  // loops deleted in the preview
  prefs: {}, misses: [],
};
window.__preview = P;

/** `/x?b=2&a=1` as the fixtures key it: the query sorted, `owner` left out. */
export function canon(rest) {
  const i = rest.indexOf("?");
  if (i < 0) return rest;
  const q = new URLSearchParams(rest.slice(i + 1));
  q.delete("owner");
  const sorted = [...q.entries()].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  const s = new URLSearchParams(sorted).toString();
  return rest.slice(0, i) + (s ? "?" + s : "");
}

async function fixture(rel) {
  if (!P.files.has(rel)) {
    P.files.set(rel, realFetch(DATA + rel).then(r => (r.ok ? r.json() : null)).catch(() => null).then(d => {
      const m = new Map();
      for (const [k, v] of Object.entries(d || {})) m.set(canon(k), v);
      return d ? m : null;
    }));
  }
  return P.files.get(rel);
}
const loopFile = key => {
  const made = P.made.find(x => `${x.owner}/${x.name}` === key);
  return fixture(`loops/${made ? made.from : key}.json`);
};

/** Recorded times moved: `d` a number of seconds to add, or a function of a time (a replay's, sped up). Epoch seconds
    and milliseconds, ISO dates; any other number or string is left as it is. */
function shifted(v, d) {
  if (!d) return v;
  const f = typeof d === "function" ? d : t => t + d;
  if (typeof v === "number") {
    if (v > 1.5e9 && v < 2.3e9) return f(v);
    if (v > 1.5e12 && v < 2.3e12) return f(v / 1000) * 1000;
    return v;
  }
  if (typeof v === "string") {
    if (/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d/.test(v) && v.length <= 40) {
      const t = Date.parse(v);
      return Number.isNaN(t) ? v : new Date(f(t / 1000) * 1000).toISOString().replace("Z", "+00:00");
    }
    return v;
  }
  if (Array.isArray(v)) return v.map(x => shifted(x, f));
  if (v && typeof v === "object") { const o = {}; for (const k in v) o[k] = shifted(v[k], f); return o; }
  return v;
}
/** js-yaml (vendored beside this script, MIT): the documents written here read back, as the server's loader would. */
let yamlLib = null;
const yaml = () => yamlLib || (yamlLib = new Promise((resolve, reject) => {
  if (window.jsyaml) return resolve(window.jsyaml);
  const s = document.createElement("script");
  s.src = new URL("./vendor/js-yaml.min.js", import.meta.url).href;
  s.onload = () => resolve(window.jsyaml); s.onerror = () => { yamlLib = null; reject(new Error("the YAML reader did not load")); };
  document.head.append(s);
}));
const clone = v => (v === undefined ? v : JSON.parse(JSON.stringify(v)));
const renamed = (v, from, to) => (from === to ? v : JSON.parse(JSON.stringify(v).split(`"${from}"`).join(`"${to}"`).split(`${from}#`).join(`${to}#`)));

// ------------------------------------------------------------------------------------------------ answers
const ok = j => ({ s: 200, j });
const text = x => ({ s: 200, x });
const fail = (s, detail) => ({ s, j: { detail } });
/** what needs a server: said, not done */
const refuse = (what, why = "") => fail(409, `The preview can't ${what}${why ? `: ${why}` : ""}. It runs in your browser on sample data; a Flux server does it for real.`);

function overOf(key) {
  if (!P.over.has(key)) P.over.set(key, { files: new Map(), notes: null, state: {}, ideas: null, prefs: null, asks: null });
  return P.over.get(key);
}

/** The replay of `key` now: its run, the recorded second it is at, whether it is still going. */
function replayAt(key) {
  const r = P.replays.get(key);
  if (!r) return null;
  const at = r.stopped != null ? r.stopped : Math.min(r.run.duration, (performance.now() - r.t0) / 1000 * SPEED);
  return { ...r, at, going: r.stopped == null && at < r.run.duration };
}
/** The latest of a run's snapshots of `rest` at `at`. */
function snapshot(r, rest) {
  const snaps = r.run.polled[rest];
  if (!snaps || !snaps.length) return undefined;
  let got = snaps[0][1];
  for (const [dt, body] of snaps) { if (dt <= r.at) got = body; else break; }
  return shifted(clone(got), r.d);
}

/** A loop's state as the preview has it: the replay's, then what was done here (a question answered, a stop). */
function stateOf(key, base) {
  let st = { ...(base || {}) };
  const r = replayAt(key);
  if (r) {
    const snap = snapshot(r, "/state");
    if (snap) st = { ...st, ...snap };
    if (r.stopped != null) Object.assign(st, { running: false, stopped: true, since: null });
  }
  return { ...st, ...(P.over.get(key)?.state || {}) };
}

async function loopGet(key, rest, owner) {
  const [user, name] = key.split("/");
  if (P.gone.has(key)) return fail(404, `no loop ${name}`);
  const m = await loopFile(key);
  if (!m) return fail(404, `${name} is not in the preview's sample data`);
  const made = P.made.find(x => `${x.owner}/${x.name}` === key);
  const from = made ? made.from.split("/")[1] : name;
  const o = P.over.get(key);
  const base = rest.split("?")[0], q = new URLSearchParams(rest.split("?")[1] || "");
  const r = replayAt(key);
  const rec = k => { const a = m.get(k); return a === undefined ? undefined : renamed(clone(a), from, name); };
  // the replay's answers while it runs (and as it ended)
  if (r && ["", "/state", "/results", "/runs", "/usage"].includes(rest)) {
    const snap = snapshot(r, rest);
    if (snap !== undefined) {
      if (rest === "") snap.state = stateOf(key, snap.state);
      if (rest === "/state") return ok(stateOf(key, snap));
      return ok(snap);
    }
  }
  if (rest === "") {
    const a = rec(""); if (!a) return fail(404, `no loop ${name}`);
    a.j = shifted(a.j, P.shift); a.j.state = stateOf(key, a.j.state);
    if (made) Object.assign(a.j, { name, id: name });
    return a;
  }
  if (rest === "/state") { const a = rec("/state"); return a ? ok(stateOf(key, shifted(a.j, P.shift))) : fail(404, "no state"); }
  // the files: written, removed, added here
  if (base === "/file" && o?.files.has(q.get("path"))) {
    const t = o.files.get(q.get("path"));
    return t == null ? fail(404, `${q.get("path")} is not in the loop`) : text(t);
  }
  if (base === "/item" && o?.files.has(q.get("path"))) {
    const t = o.files.get(q.get("path")), p = q.get("path");
    if (t == null) return fail(404, `${p} is not in the loop`);
    const known = rec(canon(rest));
    return ok(known?.j || { path: p, name: p.split("/").pop(), kind: "file", size: t.length, mtime: NOW(), revision: `preview.${t.length}`, protected: "", can: { rename: true, move: true, delete: true }, why: "" });
  }
  if (base === "/files" && o?.files.size) {
    const a = rec(canon(rest)) || rec("/files"); const rows = (a && a.j) || [];
    const folder = q.get("path") || "";
    const out = shifted(rows, P.shift).filter(f => !(o.files.has(f.path) && o.files.get(f.path) == null));
    for (const [p, t] of o.files) {
      if (t == null || out.some(f => f.path === p)) continue;
      const dir = p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "";
      if (dir === folder) out.push({ path: p, dir: false, link: false, size: t.length, mtime: NOW(), ignored: false });
      else if (dir.startsWith(folder ? folder + "/" : "")) {
        const top = (folder ? folder + "/" : "") + dir.slice(folder ? folder.length + 1 : 0).split("/")[0];
        if (!out.some(f => f.path === top)) out.push({ path: top, dir: true, link: false, size: 4096, mtime: NOW(), ignored: false });
      }
    }
    return ok(out);
  }
  if (base === "/document" && o?.files.size) {          // the document as written here (the configurator, Direct edit)
    const a = rec("/document") || { j: {} }, doc = a.j.document || "problem.yaml";
    if (o.files.has(doc) && o.files.get(doc) != null) {
      let raw = null, error = "";
      try { raw = (await yaml()).load(o.files.get(doc)); } catch (x) { error = `${doc} is not YAML: ${x.message.split("\n")[0]}`; }
      return ok({ ...shifted(a.j, P.shift), raw, normal: raw, error });
    }
  }
  if (base === "/notes" && o?.notes) return ok(o.notes);
  if (base === "/ideas" && o?.ideas) return ok(o.ideas);
  if (base === "/preferences" && o?.prefs) return ok(o.prefs);
  if (base === "/asks" && o?.asks) return ok(o.asks);
  // a recorded answer, else the nearest that fits
  let a = rec(canon(rest));
  if (a === undefined && base === "/turns" && q.get("k")) {
    const all = rec("/turns"); const t = all?.j?.turns?.find(x => String(x.k) === q.get("k"));
    if (t) a = ok({ turns: [t] });
  }
  if (a === undefined && base === "/run-data") {
    const s = rec("/stream?parts=events%2Clive")?.stream;
    if (s) a = text(s.events.map(e => JSON.stringify(e)).join("\n") + "\n");
  }
  if (a === undefined && ["/design", "/results", "/ideas", "/digests", "/turns", "/files", "/log/raw", "/usage", "/runs", "/notes"].includes(base)) a = rec(base);
  if (a === undefined && base === "/steps") a = ok({ steps: [], total: 0, start: 0 });
  if (a === undefined && base === "/output") a = text("(the preview keeps no tool output)");
  if (a === undefined && base === "/turn-steps") a = ok({ steps: [] });
  if (a === undefined) {
    P.misses.push(`GET /apps/${key}${rest}`);
    return fail(404, "Not in the preview's sample data.");
  }
  if (a.x !== undefined && base === "/run-data") a.x = a.x.split("\n").map(l => { try { return JSON.stringify(shifted(JSON.parse(l), P.shift)); } catch (_) { return l; } }).join("\n");
  if (a.j !== undefined) a.j = shifted(a.j, P.shift);
  return a;
}

/** The loops lists with what the preview did to them: replays' rows, states set here, loops made and deleted. */
async function rowsOf(list, own) {
  const out = [];
  for (const row of list) {
    const key = `${row.owner || row.user || own}/${row.name || row.app}`;
    if (P.gone.has(key)) continue;
    const r = replayAt(key);
    let x = row;
    if (r) { const snap = r.run.polled["row" + (row.name ? "/apps" : "/loops")]; if (snap) x = { ...row, ...snapshot(r, "row" + (row.name ? "/apps" : "/loops")) }; }
    out.push({ ...x, ...stateOf(key, {}), ...(row.owner ? { owner: row.owner } : {}) });
  }
  for (const made of P.made.filter(x => x.owner === own && !P.gone.has(`${x.owner}/${x.name}`))) {
    const tpl = list.find(r => (r.name || r.app) === made.from.split("/")[1]);
    if (tpl) out.unshift({ ...renamed(clone(tpl), made.from.split("/")[1], made.name), ...stateOf(`${made.owner}/${made.name}`, {}) });
  }
  return out;
}

async function userGet(rest) {
  if (rest === "/me") return P.user ? ok(P.index.users[P.user]) : fail(401, "log in");
  if (!P.user) return fail(401, "log in");
  if (rest === "/impersonation") return ok(null);
  const m = await fixture(`users/${P.user}.json`);
  const base = rest.split("?")[0];
  if (base === "/preferences/overview" && P.prefs.overview) return ok(P.prefs.overview);
  if (base === "/settings/models" || base === "/admin/settings/models") return ok({ models: [], error: "The preview asks no endpoint for its models." });
  if (base === "/logins/session") return ok({ running: false, output: "", offset: 0 });
  let a = m && (m.get(canon(rest)) ?? m.get(base));
  if (a === undefined) { P.misses.push(`GET ${rest}`); return fail(404, "Not in the preview's sample data."); }
  a = clone(a);
  if (a.j !== undefined) a.j = shifted(a.j, P.shift);
  if (["/apps", "/loops"].includes(base) && Array.isArray(a.j)) a.j = await rowsOf(a.j, P.user);
  if (base === "/admin/apps" && Array.isArray(a.j)) a.j = await rowsOf(a.j, P.user);
  return a;
}

// ------------------------------------------------------------------------------------------------ writes, in memory
const WRITES = [];
const on = (method, re, f) => WRITES.push([method, re, f]);
const loopOf = (m, q) => `${q.get("owner") || P.user}/${decodeURIComponent(m[1])}`;

on("POST", /^\/login$/, (_m, _q, b) => {
  const name = String(b?.name || "").trim();
  if (!P.index.users[name]) return fail(401, `In the preview, log in as ${Object.keys(P.index.users).join(" or ")} (any password).`);
  P.user = name; window.dispatchEvent(new CustomEvent("preview-user", { detail: name }));
  return ok(P.index.users[name]);
});
on("POST", /^\/logout$/, () => { P.user = null; return ok({ ok: "logged out" }); });
on("POST", /^\/apps\/([^/]+)\/start$/, async (m, q, b) => {
  const key = loopOf(m, q);
  if (!P.index.runs.includes(key)) {
    const names = P.index.runs.map(k => k.split("/")[1]).join(", ");
    return refuse("run this loop", `only ${names} runs here -- it replays a run recorded on a real server`);
  }
  await startReplay(key);
  return ok({ ok: `${key.split("/")[1]} started: the preview replays a recorded run${b?.passes ? "" : ""}` });
});
on("POST", /^\/apps\/([^/]+)\/(stop|restart)$/, (m, q) => {
  const key = loopOf(m, q), r = replayAt(key);
  if (r && r.going) { P.replays.get(key).stopped = r.at; for (const s of P.sources) if (s.key === key) s.halt(); return ok({ ok: "stopped" }); }
  const st = stateOf(key, {});
  if (st.running) { overOf(key).state = { ...overOf(key).state, running: false, stopped: true, since: null, question: null }; return ok({ ok: "stopped (in the preview)" }); }
  return fail(409, "the loop is not running");
});
on("POST", /^\/apps\/([^/]+)\/notes$/, async (m, q, b) => {
  const key = loopOf(m, q), o = overOf(key);
  if (!o.notes) o.notes = shifted(((await loopFile(key))?.get("/notes")?.j) || [], P.shift);
  o.notes = [...o.notes, { id: `preview-${Date.now()}`, by: P.user, t: NOW(), text: String(b?.text || "") }];
  const asked = (await loopGet(key, "/state")).j?.question;
  if (asked) o.state = { ...o.state, question: null };
  return ok({ ok: asked ? "sent: it answers the agent's question (in the preview)" : "sent: it reaches the next prompt (in the preview)" });
});
on("DELETE", /^\/apps\/([^/]+)\/notes\/([^/]+)$/, async (m, q) => {
  const key = loopOf(m, q), o = overOf(key);
  if (!o.notes) o.notes = shifted(((await loopFile(key))?.get("/notes")?.j) || [], P.shift);
  o.notes = o.notes.filter(n => n.id !== decodeURIComponent(m[2]));
  return ok({ ok: "the note is removed" });
});
const ideasOf = async key => { const o = overOf(key); if (!o.ideas) o.ideas = shifted(clone(((await loopFile(key))?.get("/ideas")?.j) || { ideas: [], queue: [] }), P.shift); return o.ideas; };
on("POST", /^\/apps\/([^/]+)\/ideas\/queue-all$/, async (m, q) => {
  const ideas = await ideasOf(loopOf(m, q));
  ideas.queue = [...new Set([...(ideas.queue || []), ...(ideas.ideas || []).filter(i => i.status === "proposed").map(i => i.id)])];
  (ideas.ideas || []).forEach(i => { if (ideas.queue.includes(i.id)) i.queued = ideas.queue.indexOf(i.id) + 1; });
  return ok({ ok: `${ideas.queue.length} queued`, queue: ideas.queue });
});
on("POST", /^\/apps\/([^/]+)\/ideas\/([^/]+)\/queue$/, async (m, q, b) => {
  const ideas = await ideasOf(loopOf(m, q)), id = decodeURIComponent(m[2]);
  const off = b && (b.queued === false || b.on === false || b.remove === true);
  ideas.queue = (ideas.queue || []).filter(x => x !== id);
  if (!off) ideas.queue.push(id);
  (ideas.ideas || []).forEach(i => { i.queued = ideas.queue.includes(i.id) ? ideas.queue.indexOf(i.id) + 1 : null; });
  return ok({ ok: off ? "taken off the queue" : "queued: the next pass tries it first", queue: ideas.queue });
});
on("PUT", /^\/apps\/([^/]+)\/file$/, (m, q, b) => {
  const p = q.get("path"); if (!p) return fail(400, "no path");
  overOf(loopOf(m, q)).files.set(p, String(b?.text ?? ""));
  return ok({ ok: `${p} saved (in the preview)`, revision: `preview.${Date.now()}` });
});
on("DELETE", /^\/apps\/([^/]+)\/file$/, (m, q) => { overOf(loopOf(m, q)).files.set(q.get("path"), null); return ok({ ok: `${q.get("path")} deleted (in the preview)` }); });
async function documentText(key) {
  const o = overOf(key), info = (await loopGet(key, "")).j || {}, doc = info.document || "problem.yaml";
  if (o.files.has(doc) && o.files.get(doc) != null) return [doc, o.files.get(doc)];
  const got = await loopGet(key, canon(`/file?path=${encodeURIComponent(doc)}`));
  return [doc, got.x ?? ""];
}
/** The configurator's text with the keys it keeps carried over, as the server's save would (configure.merged). */
async function mergedText(key, b) {
  const [doc, before] = await documentText(key), text = String(b?.text ?? "");
  if (!(b?.kept || []).length) return [doc, before, text];
  const Y = await yaml(), old = (() => { try { return Y.load(before) || {}; } catch (_) { return {}; } })(), now = Y.load(text) || {};
  const carried = Object.fromEntries(b.kept.filter(k => k in old && !(k in now)).map(k => [k, old[k]]));
  return [doc, before, Object.keys(carried).length ? text.replace(/\n*$/, "\n") + Y.dump(carried) : text];
}
on("POST", /^\/apps\/([^/]+)\/document\/preview$/, async (m, q, b) => { const [doc, before, after] = await mergedText(loopOf(m, q), b); return ok({ document: doc, before, after }); });
on("POST", /^\/apps\/([^/]+)\/document\/named$/, () => ok({ named: [], missing: [], later: [] }));
on("POST", /^\/apps\/([^/]+)\/validate$/, () => ok({ ok: true, error: "" }));
on("PUT", /^\/apps\/([^/]+)\/document$/, async (m, q, b) => {
  const key = loopOf(m, q), [doc, , t] = await mergedText(key, b);
  overOf(key).files.set(doc, t);
  return ok({ ok: "saved (in the preview: gone on reload)", id: key.split("/")[1], error: "" });
});
on("PATCH", /^\/apps\/([^/]+)\/preferences$/, async (m, q, b) => {
  const key = loopOf(m, q), o = overOf(key);
  const now = o.prefs || ((await loopFile(key))?.get("/preferences")?.j) || {};
  o.prefs = { ...now, ...(b || {}) };
  return ok(o.prefs);
});
on("DELETE", /^\/apps\/([^/]+)\/preferences$/, (m, q) => { overOf(loopOf(m, q)).prefs = {}; return ok({}); });
on("PUT", /^\/preferences\/overview$/, (_m, _q, b) => { P.prefs.overview = b; return ok(b); });
on("DELETE", /^\/preferences\/overview$/, () => { P.prefs.overview = null; return ok({}); });
on("POST", /^\/apps\/([^/]+)\/asks$/, async (m, q, b) => {
  const key = loopOf(m, q), o = overOf(key);
  if (!o.asks) o.asks = shifted(((await loopFile(key))?.get("/asks")?.j) || [], P.shift);
  const id = `preview-${Date.now()}`;
  o.asks = [...o.asks, { id, thread_id: b?.thread_id || id, question: String(b?.question || b?.text || ""), author: b?.author || "claude", by: P.user,
    answer: "The preview has no agent to ask. On a Flux server, the agent reads the loop's record and files and answers here.",
    started: NOW(), ended: NOW(), running: false, log: [] }];
  return ok({ ok: "asked (the preview answers for the agent)", id });
});
on("POST", /^\/apps\/(new-empty)$/, async (_m, _q, b) => makeLoop(b?.name));
on("POST", /^\/apps\/(from-text)$/, async (_m, _q, b) => {
  const got = await makeLoop(b?.name);
  if (got.s === 200) overOf(`${P.user}/${b.name}`).files.set(b.filename || "problem.yaml", String(b.text || ""));
  return got;
});
on("DELETE", /^\/apps\/([^/]+)$/, (m, q) => { P.gone.add(loopOf(m, q)); return ok({ ok: `${decodeURIComponent(m[1])} deleted (in the preview)` }); });
on("PUT", /^\/(settings|admin\/settings)$/, (_m, _q, b) => ok({ ok: "saved (in the preview)", values: b?.values || {} }));
on("POST", /^\/(agents|admin\/agents)\/([^/]+)\/test$/, () => refuse("test an agent", "an agent runs on a server, with its login"));
on("POST", /^\/logins\/([^/]+)$/, () => refuse("log an agent in", "its login runs on a server"));
on("POST", /^\/apps\/([^/]+)\/check$/, () => refuse("run the loop's check", "its tools run on a server"));
on("POST", /^\/apps\/new-by-agent$/, () => refuse("ask an agent to write a loop", "agents run on a server"));
on("POST", /^\/apps\/([^/]+)\/author(\/.*)?$/, () => refuse("run the author agent", "agents run on a server"));

async function makeLoop(name) {
  name = String(name || "").trim();
  if (!/^[A-Za-z0-9_-]{1,60}$/.test(name)) return fail(400, "a loop's name: letters, digits, - and _");
  const key = `${P.user}/${name}`;
  if (P.index.loops.includes(key) && !P.gone.has(key) || P.made.some(x => `${x.owner}/${x.name}` === key)) return fail(409, `${name} exists`);
  const from = P.index.loops.includes(`${P.user}/blank`) ? `${P.user}/blank` : P.index.loops.find(k => k.startsWith(P.user + "/")) || P.index.loops[0];
  P.made.push({ owner: P.user, name, from });
  P.gone.delete(key);
  return ok({ ok: `${name} created (in the preview)`, name });
}

async function write(method, rest, body, form) {
  const [path, query] = [rest.split("?")[0], new URLSearchParams(rest.split("?")[1] || "")];
  for (const [mm, re, f] of WRITES) {
    const m = mm === method && path.match(re);
    if (m) return f(m, query, body, form);
  }
  return refuse("save that", "nothing is saved here");
}

// ------------------------------------------------------------------------------------------------ uploads (forms)
const UPLOAD_MAX = 2 * 1024 * 1024;
async function upload(rest, form) {
  const path = rest.split("?")[0], q = new URLSearchParams(rest.split("?")[1] || "");
  const files = form ? [...form.entries()].filter(([, v]) => v instanceof Blob) : [];
  const size = files.reduce((n, [, f]) => n + f.size, 0);
  if (size > UPLOAD_MAX) return refuse("keep that many bytes", `the preview keeps uploads up to ${UPLOAD_MAX / 1048576} MB in this page`);
  if (files.some(([, f]) => /\.(zip|tar|gz|tgz)$/i.test(f.name || ""))) return refuse("unpack an archive", "upload the files themselves");
  let key;
  if (path === "/apps") {
    const made = await makeLoop(form.get("name"));
    if (made.s !== 200) return made;
    key = `${P.user}/${form.get("name")}`;
  } else {
    const m = path.match(/^\/apps\/([^/]+)\/files$/);
    if (!m) return refuse("take that upload");
    key = loopOf(m, q);
  }
  const at = String(form.get("path") || form.get("folder") || "").replace(/^\/+|\/+$/g, "");
  for (const [, f] of files) overOf(key).files.set((at ? at + "/" : "") + (f.webkitRelativePath || f.name), await f.text());
  return ok({ ok: `${files.length} file${files.length === 1 ? "" : "s"} added (in the preview)`, name: key.split("/")[1], files: files.map(([, f]) => f.name) });
}

// ------------------------------------------------------------------------------------------------ the router
/** An answer to `method url`: {s, j|x}. */
export async function respond(method, url, body, form) {
  await ready;
  const u = new URL(url, location.href), rest = u.pathname.slice(4) + u.search;
  if (method !== "GET" && method !== "HEAD") {
    if (form) return upload(rest, form);
    if (!P.user && !/^\/(login|logout)$/.test(u.pathname.slice(4))) return fail(401, "log in");
    return write(method, rest, body);
  }
  const m = u.pathname.slice(4).match(/^\/apps\/([^/]+)(\/.*)?$/);
  if (m && !["from-text", "new-empty", "new-by-agent"].includes(m[1])) {
    if (!P.user) return fail(401, "log in");
    const owner = u.searchParams.get("owner") || P.user;
    return loopGet(`${owner}/${decodeURIComponent(m[1])}`, canon((m[2] || "") + u.search), owner);
  }
  return userGet(canon(rest));
}

const asResponse = a => a.x !== undefined
  ? new Response(a.x, { status: a.s, headers: { "Content-Type": "text/plain; charset=utf-8" } })
  : new Response(JSON.stringify(a.j ?? null), { status: a.s, headers: { "Content-Type": "application/json" } });
const isApi = url => { try { const u = new URL(String(url), location.href); return u.origin === location.origin && u.pathname.startsWith("/api/"); } catch (_) { return false; } };

window.fetch = async (input, init = {}) => {
  const url = typeof input === "string" || input instanceof URL ? String(input) : input.url;
  if (!isApi(url)) return realFetch(input, init);
  const method = (init.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
  let body;
  if (typeof init.body === "string") { try { body = JSON.parse(init.body); } catch (_) { body = init.body; } }
  const form = init.body instanceof FormData ? init.body : null;
  await new Promise(r => setTimeout(r, 40 + Math.random() * 80));          // a server's moment
  return asResponse(await respond(method, url, body, form));
};

/** The app's uploads go through XMLHttpRequest (their progress): those to /api answered here. */
window.XMLHttpRequest = class extends RealXHR {
  open(method, url, ...more) { this._preview = isApi(url) ? { method: method.toUpperCase(), url } : null; if (!this._preview) super.open(method, url, ...more); }
  setRequestHeader(k, v) { if (!this._preview) super.setRequestHeader(k, v); }
  send(body) {
    if (!this._preview) return super.send(body);
    const { method, url } = this._preview, form = body instanceof FormData ? body : null;
    const total = form ? [...form.values()].reduce((n, v) => n + (v instanceof Blob ? v.size : 0), 0) : 0;
    respond(method, url, typeof body === "string" ? JSON.parse(body || "null") : undefined, form).then(a => {
      this.upload.onprogress?.({ lengthComputable: true, loaded: total, total });
      Object.defineProperty(this, "status", { value: a.s, configurable: true });
      Object.defineProperty(this, "responseText", { value: a.x !== undefined ? a.x : JSON.stringify(a.j ?? null), configurable: true });
      Object.defineProperty(this, "readyState", { value: 4, configurable: true });
      this.onload?.();
    });
  }
  abort() { if (!this._preview) return super.abort(); this.onabort?.(); }
};

// ------------------------------------------------------------------------------------------------ the stream
/** A loop's one stream (D917): the journal, the live state and the log -- a replay's as recorded, as time goes; a
    finished loop's at once. Each part's cursor is how many of its messages were sent, so a reopened stream (another
    part wanted) goes on where it was. */
class PreviewSource extends EventTarget {
  static CONNECTING = 0; static OPEN = 1; static CLOSED = 2;
  constructor(url) {
    super();
    this.url = String(url); this.readyState = 0; this.timers = [];
    const u = new URL(this.url, location.href), m = u.pathname.match(/^\/api\/apps\/([^/]+)\/stream$/);
    this.key = `${u.searchParams.get("owner") || P.user}/${decodeURIComponent(m[1])}`;
    this.parts = (u.searchParams.get("parts") || "events,live,log").split(",");
    this.cur = { events: +(u.searchParams.get("events") || "0").split("-").pop() || 0, log: +(u.searchParams.get("log") || "0").split("-").pop() || 0 };
    this.sent = { events: this.cur.events, log: this.cur.log };
    P.sources.add(this);
    setTimeout(() => this.connect(), 30);
  }
  emit(kind, data, counted = true) {
    if (this.readyState === 2) return;
    let lastEventId = "";
    if (counted && (kind === "events" || kind === "log")) {
      this.sent[kind]++;
      lastEventId = ["events", "log"].filter(k => this.parts.includes(k)).map(k => `${k}:1-${this.sent[k]}`).join(",");
    }
    this.dispatchEvent(new MessageEvent(kind, { data: JSON.stringify(data), lastEventId }));
  }
  async connect() {
    await ready;
    if (this.readyState === 2) return;
    this.readyState = 1; this.onopen?.({});
    const r = replayAt(this.key);
    if (r) return this.replay(r);
    const m = await loopFile(this.key), made = P.made.find(x => `${x.owner}/${x.name}` === this.key);
    if (made) { this.emit("ready", {}, false); return; }
    const s = m?.get("/stream?parts=events%2Clive")?.stream;
    if (this.parts.includes("events")) {
      if (s && this.cur.events < 1) this.emit("events", shifted(s.events, P.shift));
      this.emit("ready", {}, false);
    }
    if (this.parts.includes("live") && s?.live) this.emit("live", shifted(s.live, P.shift), false);
    if (this.parts.includes("log") && this.cur.log < 1) {
      const log = m?.get("/log/raw?download=false");
      if (log?.x) this.emit("log", log.x);
    }
  }
  /** The recorded run from where the replay is: what came before at once, the rest at its pace. */
  replay(r) {
    const msgs = r.run.stream, d = r.d;
    const seen = { events: 0, log: 0 };
    const now = { events: [], log: "", live: null };
    const later = [];
    for (const [dt, kind, data] of msgs) {
      if (!this.parts.includes(kind) && kind !== "ready") continue;
      if (kind === "events" || kind === "log") { seen[kind]++; if (seen[kind] <= this.cur[kind]) continue; }
      if (dt <= r.at) {
        if (kind === "events") now.events.push(...data); else if (kind === "log") now.log += data; else if (kind === "live") now.live = data;
      } else later.push([dt, kind, data]);
    }
    if (this.parts.includes("events")) { if (now.events.length) this.emit("events", shifted(now.events, d)); this.emit("ready", {}, false); }
    if (now.log) this.emit("log", now.log);
    if (now.live) this.emit("live", shifted(now.live, d), false);
    if (!r.going) return;
    for (const [dt, kind, data] of later) {
      if (kind === "ready") continue;
      this.timers.push(setTimeout(() => {
        const rr = replayAt(this.key);
        if (!rr || rr.stopped != null) return;
        this.emit(kind, kind === "log" ? data : shifted(data, d), kind !== "live");
      }, (dt - r.at) / SPEED * 1000));
    }
  }
  /** A new start: its journal from the beginning, as a server's stream goes on to the new file. */
  restart() { this.halt(); this.cur = { events: 0, log: 0 }; this.sent = { events: 0, log: 0 }; this.connect(); }
  halt() { this.timers.forEach(clearTimeout); this.timers = []; }
  close() { this.halt(); this.readyState = 2; P.sources.delete(this); }
}
window.EventSource = class extends EventTarget {
  constructor(url, opts) {
    if (!isApi(url)) return new RealES(url, opts);
    return new PreviewSource(url);
  }
  static CONNECTING = 0; static OPEN = 1; static CLOSED = 2;
};

async function startReplay(key) {
  const [owner, name] = key.split("/");
  const run = await realFetch(`${DATA}runs/${owner}/${name}.json`).then(r => r.json());
  const at = NOW();                                     // the run's clock sped up: a recorded second is 1/SPEED of one here
  P.replays.set(key, { run, t0: performance.now(), d: t => at + (t - run.recorded) / SPEED, stopped: null });
  const o = P.over.get(key); if (o) o.state = {};
  for (const s of P.sources) if (s.key === key) s.restart();
}

// ------------------------------------------------------------------------------------------------ links to /api
/** A download or a raw view the app links to (/api/...file?download=1): made here, from the same answers. */
document.addEventListener("click", async e => {
  const a = e.target.closest?.("a[href]");
  if (!a || !isApi(a.href) || e.defaultPrevented) return;
  e.preventDefault();
  const got = await respond("GET", a.href);
  if (got.s !== 200) { toast(got.j?.detail || "Not in the preview's sample data.", "warn"); return; }
  const blob = new Blob([got.x !== undefined ? got.x : JSON.stringify(got.j, null, 2)], { type: got.x !== undefined ? "text/plain" : "application/json" });
  const href = URL.createObjectURL(blob), u = new URL(a.href);
  if (a.hasAttribute("download") || u.searchParams.get("download") === "1" || u.searchParams.get("download") === "true") {
    const d = document.createElement("a");
    d.href = href; d.download = a.getAttribute("download") || (u.searchParams.get("path") || "download").split("/").pop();
    document.body.append(d); d.click(); d.remove();
  } else window.open(href, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(href), 60000);
}, true);

// ------------------------------------------------------------------------------------------------ start
/** The fixtures' index read, the demo user logged in, the recorded run started; the configurator's model the site's. */
export const ready = (async () => {
  P.index = await realFetch(DATA + "index.json").then(r => r.json());
  const as = new URLSearchParams(location.search).get("as");
  P.user = as && P.index.users[as] ? as : P.index.default;
  P.shift = NOW() - P.index.recorded;
  host.assets = new URL("..", import.meta.url).href.replace(/\/$/, "");      // crafter-model.js and tools.json: the site's
  for (const key of P.index.autostart || []) await startReplay(key);
})();

export const preview = P;
