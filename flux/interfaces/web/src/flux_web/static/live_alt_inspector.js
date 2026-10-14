// D982: LiveAlt's inspector -- a task as the left side names it, what it gave before what it was
// given, and for the tasks that decide something, what was decided. Live's own inspector is unchanged.
import { dur, h } from "./ui.js";
import { WORK_COLORS as COLORS, AGENT_COLOR, phaseKind, workLabel, ownSpans } from "./work_style.js";
import { candidateOf, passName, ranFor, taskLabel } from "./live_alt_views.js";
import { conversation, markdown } from "./loops.js";

const turnOf = n => String(n.params?.turn || n.name);          // D1050: a wrapper's taken-in turn
const isAgent = n => /^\s*agent\s*:/i.test(turnOf(n));
const isTool = n => /^\s*tool\s*:/i.test(String(n.name));
const isModel = n => /^\s*(llm\b|model\b)/i.test(turnOf(n));
const head = n => String(n.name).toLowerCase().split(":")[0].trim();
const colorOf = n => isAgent(n) ? AGENT_COLOR : COLORS[phaseKind(n)] || COLORS.setup;
const running = n => n.t1 == null;
const stateOf = n => running(n) ? ["live", "running"] : n.interrupted ? ["warn", "interrupted"] : n.failed ? ["bad", "failed"] : n.timedOut ? ["warn", "time limit"]
  : n.tryFailed ? ["bad", `failed · ${n.tryFailed}`] : n.rejected ? ["warn", "rejected"] : ["ok", "done"];   // D1075: a try whose check failed is a failed try
const fieldsOf = n => ({ ...(n.fields || {}), ...(running(n) ? {} : (n.output || {})) });
const stdoutOf = n => { const f = fieldsOf(n); return String(f.stdout ?? f["stdout (live tail)"] ?? ""); };
const lines = s => String(s).split("\n").filter(l => l.trim());

/** `name=value` pairs a command printed (the loop's own reading of a measurement). */
export function metricsOf(text) {
  const out = {};
  for (const m of String(text).matchAll(/(?:^|\s)([A-Za-z_][\w.]*)=([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)(?=\s|$)/g)) if (!(m[1] in out)) out[m[1]] = Number(m[2]);
  return out;
}
/** A check's own result line, "N failing" or "N failing of M" (every preset prints it). */
export function failingOf(text) {
  const all = [...String(text).matchAll(/(\d+)\s+failing(?:\s+of\s+(\d+))?/g)], m = all.at(-1);
  return m ? { failing: Number(m[1]), of: m[2] != null ? Number(m[2]) : null, index: m.index } : null;
}
/** D1061: a design said by its id ("#4") -- the loop's name is the page's already; the whole name on hover.
    D1065: by the lines' rule -- only the loop's own name goes; a part's design keeps its part, a baseline is "baseline". */
let loopNames = [], loopTests = [];
export function nameLoop(...names) { loopNames = names.filter(Boolean).map(String); }
/** D1065: the document's checks, in order -- a check stopped at a failure lists those it never reached. */
export function nameTests(names) { loopTests = (names || []).map(String); }
export const notReached = n => n.failed && loopTests.length && (n.kids || []).some(k => k.failed)
  ? loopTests.filter(t => !(n.kids || []).some(k => ranFor(k) === t)) : [];
/** D1066: a label whose "what" is the loop's own name is its kind alone ("Design", not "Design · demo-x"). */
const plain = t => { const at = String(t).lastIndexOf(" · "); return at > 0 && loopNames.includes(String(t).slice(at + 3)) ? String(t).slice(0, at) : byId(t); };
const byId = t => String(t).replace(/([^\s·#]+)(#[^#\s]+)/g, (_, pre, id) => id === "#baseline" ? "baseline" : !loopNames.length || loopNames.includes(pre) ? id : pre + id);
const fmt = v => Math.abs(v) >= 100 ? String(Math.round(v * 10) / 10) : Math.abs(v) >= 1 ? v.toFixed(2).replace(/\.?0+$/, "") : Number(v.toPrecision(2)).toString();

function pill(n) {
  const [cls, text] = stateOf(n);
  return h("span", { class: `pill ${cls}` }, running(n) ? h("span", { class: "alt-spin", "aria-hidden": "true" }) : "", text);
}
function stepRow(x, env) {
  const s = summaryOf(x);
  return h("button", { type: "button", class: "alt-step-row", style: `--k:${x.failed ? "var(--bad)" : colorOf(x)}`, "data-task": String(x.id), onclick: () => env.pick?.(x.id) },
    running(x) ? h("span", { class: "alt-spin", "aria-hidden": "true" }) : h("span", { class: `alt-task-state ${x.failed ? "failed" : x.rejected ? "partial" : "done"}` }, x.failed ? "✗" : x.rejected ? "!" : "✓"),
    h("span", { class: "alt-task-name", title: taskLabel(x) }, byId(taskLabel(x)), s ? h("span", { class: "muted" }, ` — ${s}`) : "",
      /^\s*critique\s*:/i.test(String(x.name)) ? h("span", { class: "muted" }, ` — ${/^(rejected|sent back)/i.test(String(x.output?.verdict ?? "")) ? "rejected" : x.output?.verdict ? "no objection" : ""}`) : ""),   // D1065: its verdict
    h("span", { class: "mono muted small" }, dur(x.seconds ?? (x.t1 ?? Date.now() / 1000) - x.t0)));
}
/** One line of what a task gave: a check's failing count, a measurement's numbers, else nothing. */
function summaryOf(x) {
  const leaves = x.kids?.length ? x.kids.flatMap(k => k.kids?.length ? k.kids : [k]) : [x];
  // D1059: a step that failed says so first -- which test, and how many of its cases
  const bad = leaves.length > 1 && leaves.find(l => l.failed && failingOf(stdoutOf(l))?.failing);
  if (bad) { const f = failingOf(stdoutOf(bad)); return `${ranFor(bad) || "a test"} ${f.failing} failing${f.of != null ? ` of ${f.of}` : ""}`; }
  // D1068: several checks that passed -- how many, not the first one's own line
  if (leaves.length > 1 && leaves.every(l => !l.failed && failingOf(stdoutOf(l)))) return `${leaves.length} checks, none failing`;
  for (const l of leaves) {
    const out = stdoutOf(l), f = failingOf(out);
    if (f) return `${f.failing} failing${f.of != null ? ` of ${f.of}` : ""}`;
    const m = Object.entries(metricsOf(out)).slice(0, 2);
    if (m.length) return m.map(([k, v]) => `${k} ${fmt(v)}`).join(", ");
  }
  return running(x) ? "running" : "";
}
function fold(key, title, summary, kids, env, attrs = {}) {
  return h("details", { class: `alt-fold-block ${attrs.cls || ""}`, "data-fold": key, open: env.place?.folds?.get(key) || false },
    h("summary", {}, h("b", {}, title), summary ? h("span", { class: "alt-fold-summary" }, summary) : ""), kids);
}

/** The inspector's head (P1): the task as the tree names it, its kind's stripe, state, time, Raw and
    Fullscreen; the parents as chips that open them; the purpose and the engine's own name. */
export function inspectorHead(n, env) {
  const tools = env.tools();
  for (const b of tools) if (b && b.textContent) { b.title = b.textContent; b.setAttribute("aria-label", b.textContent); b.textContent = b.textContent === "Raw" ? "{ }" : "⤢"; b.classList.add("alt-icon-btn"); }
  // D1067: a crumb that says something -- not the title again, not a kind word alone, not the evaluation's wrapper
  const parents = [], title = plain(taskLabel(n));
  for (let p = n.parent; p; p = p.parent) { const l = plain(taskLabel(p)); if (l !== title && l.includes(" · ") && !/·\s*evaluation$/i.test(l)) parents.unshift(p); }
  return [h("div", { class: "detail-head alt-ihead" }, h("span", { class: "alt-stripe", style: `background:${colorOf(n)}` }),
      h("h2", { title: taskLabel(n) }, plain(taskLabel(n))), pill(n), h("span", { class: "muted" }, dur(running(n) ? env.now - n.t0 : n.seconds)), h("span", { class: "alt-head-tools" }, tools)),
    parents.length ? h("div", { class: "alt-crumbs" }, parents.flatMap((p, i) => [i ? h("span", { class: "muted" }, "›") : "",
      h("button", { type: "button", class: "alt-pchip", style: `--k:${colorOf(p)}`, onclick: () => env.pick?.(p.id), title: taskLabel(p) }, plain(taskLabel(p)))])) : "",
    // D1066: the reason and the engine's name by the same rule -- a reason that is the loop's own name says nothing
    h("p", { class: "alt-meta muted" }, n.why && !loopNames.includes(String(n.why).trim()) ? `${byId(n.why)} · ` : "", h("span", { class: "mono", title: n.name }, n.tryNo ? byId(n.name).replace(/\bdraft \d+$/, `try ${n.tryNo}`) : byId(n.name)))];   // D1075: the tree's try, not a run's own draft count
}

/** The usual time of a task like this one (the same name and purpose's first word), from its earlier runs. */
function usualOf(n, env) {
  const word = String(n.why || "").split(/\s+/)[0], times = [];
  for (const x of env.nodes.values()) if (x !== n && x.t1 != null && x.name === n.name && String(x.why || "").split(/\s+/)[0] === word) times.push(x.seconds ?? x.t1 - x.t0);
  if (!times.length) return null;
  times.sort((a, b) => a - b);
  return times[Math.floor(times.length / 2)];
}
/** The best of each metric among the earlier runs of this measurement, by the goals' directions. */
function bestOf(n, env) {
  const goals = Object.fromEntries((env.goals?.() || []).map(g => [g.metric, /max/i.test(g.direction || "") ? 1 : -1]));
  const best = {};
  // D1063: the best of the other designs measured before it on the same stage -- never itself, never another stage
  const stageOf = x => String(x.why || "").split(/\s+/).slice(0, 2).join(" "), mine = candidateOf(n);
  for (const x of env.nodes.values()) {
    if (x === n || x.t1 == null || x.failed || x.name !== n.name || stageOf(x) !== stageOf(n) || x.t0 > n.t0 || (mine && candidateOf(x) === mine)) continue;
    const who = candidateOf(x);
    for (const [k, v] of Object.entries(metricsOf(stdoutOf(x)))) if (goals[k] && (best[k] == null || goals[k] * v > goals[k] * best[k].v)) best[k] = { v, who };
    // D1064: a dictionary goal's best -- the design whose values average best, in the goal's direction
    for (const [k, d] of Object.entries(dictsOf(stdoutOf(x)))) {
      const m = meanOf(d);
      if (goals[k] && m != null && (best[k] == null || goals[k] * m > goals[k] * best[k].m)) best[k] = { v: d, m, who };
    }
  }
  return { best, goals };
}
const meanOf = d => { const xs = Object.values(d || {}).filter(x => typeof x === "number"); return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null; };

/** A command (P2-P6): what it gave first -- running time and the latest lines, a check's failing
    count and its first failures, a measurement's numbers against the best so far -- then the input,
    stdout and stderr folded, each kept where the person left it. */
function toolBody(n, env) {
  const f = fieldsOf(n), p = n.params || {}, out = stdoutOf(n), err = String(f.stderr ?? f["stderr (live tail)"] ?? "");
  const top = [];
  if (running(n)) {
    const took = env.now - n.t0, usual = usualOf(n, env);
    top.push(h("div", { class: "alt-run" }, h("div", { class: "alt-label" }, `${dur(took)}${usual ? ` · usually ${dur(usual)}` : ""}`),
      usual ? h("div", { class: "alt-bar" }, h("i", { style: `width:${Math.min(100, 100 * took / usual)}%;background:${colorOf(n)}` })) : ""));
    if (lines(out).length) top.push(h("pre", { class: "val alt-tail", "data-k": "stdout" }, lines(out).slice(-8).join("\n")));   // the tail, once there is one
  } else {
    const fails = failingOf(out), metrics = metricsOf(out);
    if (fails) {
      const ok = fails.failing === 0, before = lines(out.slice(0, fails.index)).filter(l => !/^\s*\d+\s+failing/.test(l));
      top.push(h("div", { class: `alt-result ${ok ? "ok" : "bad"}`, style: `--k:${ok ? colorOf(n) : "var(--bad)"}` },
        h("span", { class: "alt-big" }, `${ok ? "✓" : "✗"} ${fails.failing} failing`), h("span", { class: "muted" }, [fails.of != null ? `of ${fails.of}` : "", f.exit && f.exit !== 0 ? `exit ${f.exit}` : ""].filter(Boolean).join(" · "))));   // D1061: an exit 0 beside failures says nothing
      if (!ok && before.length) top.push(h("div", {}, h("div", { class: "alt-label" }, "The first failures"),
        h("pre", { class: "val alt-cases" }, before.slice(0, 3).join("\n") + (before.length > 3 ? `\n… and ${before.length - 3} more` : ""))));
    } else if (Object.keys(metrics).length) {
      const { best, goals } = bestOf(n, env), dicts = dictsOf(out), me = candidateOf(n);
      // D1064: one row per number, a dictionary's too; "best so far" says whose; no comparison columns when nothing compares
      // D1068: the goals first, in the document's order -- then the rest
      const order = Object.keys(goals), rank = k => order.includes(k) ? order.indexOf(k) : order.length;
      const rows = [...Object.entries(metrics).map(([k, v]) => [k, v, v]), ...Object.entries(dicts).map(([k, d]) => [k, d, meanOf(d)])].sort((a, b) => rank(a[0]) - rank(b[0]));
      const compares = rows.some(([k]) => best[k]);
      const pct = c => Math.abs(c) < 0.0005 ? "=" : `${c > 0 ? "+" : "−"}${Math.abs(c * 100).toFixed(c && Math.abs(c) < 10 ? 1 : 0)}%`;
      const change = (k, m) => { const b = best[k], bm = b && (b.m ?? b.v), dir = goals[k];
        if (b == null || m == null || !dir || !bm) return ["", ""];
        // D1068: a dictionary key by key -- one mean's percentage hides a key that moved the other way
        if (b.m != null && dicts[k] && typeof b.v === "object") {
          const keys = Object.keys(dicts[k]).filter(x => typeof dicts[k][x] === "number" && typeof b.v[x] === "number" && b.v[x]);
          // D1075: each key coloured by its own way -- t1 down is better under a minimised goal though t2 went up
          return [keys.flatMap((x, i) => { const c = (dicts[k][x] - b.v[x]) / Math.abs(b.v[x]);
            return [i ? " · " : "", h("span", { class: `alt-change ${dir * c > 0 ? "up" : dir * c < 0 ? "down" : ""}` }, `${x} ${pct(c)}`)]; }), ""];
        }
        const c = (m - bm) / Math.abs(bm);
        return [Math.abs(c) < 0.0005 ? "=" : `${c > 0 ? "+" : "−"}${Math.abs(c * 100).toFixed(1)}%`, dir * c > 0 ? "up" : dir * c < 0 ? "down" : ""]; };
      top.push(h("div", { class: "alt-table-wrap" }, h("table", { class: "list compact alt-metrics" },
        h("thead", {}, h("tr", {}, h("th", {}, "metric"), h("th", { class: "num", title: me, style: "text-transform:none" }, me ? byId(me) : "this design"),   // D1065: a design's name as it is
          compares ? [h("th", { class: "num" }, "best so far"), h("th", { class: "num" }, "change")] : "")),
        h("tbody", {}, rows.map(([k, v, m]) => {
          const b = best[k], [said, better] = change(k, m);
          return h("tr", {}, h("td", {}, k, goals[k] ? ` ${goals[k] > 0 ? "↑" : "↓"}` : ""), h("td", { class: "num mono" }, cell(v)),
            compares ? [h("td", { class: "num mono", title: b?.who || "" }, b ? [cell(b.v), h("span", { class: "muted small" }, ` ${byId(b.who || "")}`)] : "—"),
              h("td", { class: `alt-change ${better}` }, said)] : "");
        })))));
    } else if (n.timedOut) top.push(h("div", { class: "alt-result warn", style: "--k:var(--warn)" },     // D997: a time limit warns
      h("span", { class: "alt-big" }, `! exit ${f.exit} · time limit`)));
    else if (f.exit != null) top.push(h("div", { class: `alt-result ${f.exit === 0 ? "ok" : "bad"}`, style: `--k:${f.exit === 0 ? colorOf(n) : "var(--bad)"}` },
      h("span", { class: "alt-big" }, f.exit === 0 ? "✓ exit 0" : `✗ exit ${f.exit}`)));
  }
  const command = String(p.command || "");
  return [h("div", { class: "task-inspector alt-tool" }, ...top,
    fold("input", "Input", env.tidy(command, p.folder).split("\n")[0], env.block("Input", p), env),
    out && !running(n) ? fold("stdout", "stdout", `${lines(out).length} line${lines(out).length === 1 ? "" : "s"}`, h("pre", { class: "val astream-body", "data-k": "stdout" }, out), env) : "",
    err ? fold("stderr", "stderr", `${lines(err).length} line${lines(err).length === 1 ? "" : "s"}`,
      h("section", { class: `astream ${n.failed ? "err" : ""}` }, h("pre", { class: "val astream-body", "data-k": "stderr" }, err)), env) : "")];
}


/* D984: an agent's turn, or a model's call, as a chat that keeps up -- the prompt and what was
   fed back on the right, its words on the left, its thinking and its commands as quiet rows; it
   opens at the newest end and follows it while you are there; scrolling up shows the history in
   steps without moving your place; scrolled up, new activity waits behind "↓ latest". */
const CHAT_STEP = 40;
const chatLimit = new Map(), chatAnchor = new Map();       // per task: how many steps shown, the place to keep
function chatBox(n, env, kids, more) {
  // D1035: its handlers find the box from the event -- a redraw may keep the old box and button (a morph)
  const atBottom = box => box.scrollHeight - box.scrollTop - box.clientHeight < 24;
  const box = h("div", { class: "alt-chat cv", "data-k": "chat", tabindex: "0", "aria-label": "Conversation", onscroll: e => {
    const b = e.currentTarget, jump = b.parentElement?.querySelector(".alt-chat-jump");
    if (jump) jump.hidden = atBottom(b);
    if (more && b.scrollTop < 40 && !chatAnchor.has(n.id)) {           // the history, a step at a time, the place kept
      chatAnchor.set(n.id, b.scrollHeight - b.scrollTop);
      chatLimit.set(n.id, (chatLimit.get(n.id) || CHAT_STEP) + CHAT_STEP);
      env.redraw();
    }
  } }, kids);
  const jump = h("button", { type: "button", class: "alt-chat-jump", hidden: true,
    onclick: e => { const b = e.currentTarget.parentElement?.querySelector(".alt-chat"); if (b) b.scrollTop = b.scrollHeight; } }, "↓ latest");
  const wrap = h("div", { class: "alt-chat-wrap" }, box, jump);
  requestAnimationFrame(() => {
    // a fresh box (another task, the first draw); a morph keeps the old one, the panel's restore keeps its place
    const live = wrap.isConnected ? box : document.querySelector(`.alt-detail[data-task="${n.id}"] .alt-chat`);
    if (!live) return;
    const anchor = fetching.has(n.id) ? null : chatAnchor.get(n.id);     // a page on its way: the place is kept for it
    if (anchor != null) { chatAnchor.delete(n.id); live.scrollTop = live.scrollHeight - anchor; }
    else if (wrap.isConnected && !env.place?.blocks?.get("chat")) live.scrollTop = live.scrollHeight;  // a new task opens at its newest end
    const j = live.parentElement?.querySelector(".alt-chat-jump");
    if (j) j.hidden = atBottom(live);
  });
  return wrap;
}
const bubble = (side, label, body, key, env, open) => h("details", { class: `alt-bubble ${side}${side === "me" ? " inspector-input" : ""}`, "data-fold": key, open: open ?? env.place?.folds?.get(key) ?? false },
  h("summary", {}, h("small", {}, label)), body);
function usage(f, extra) {
  const facts = [...extra, ["status", f.status], ["exit", f.exit], ...Object.entries(f).filter(([k]) => /token|cost|usd|first/i.test(k))]
    .filter(([, v]) => v != null && v !== "");
  return facts.length ? h("div", { class: "facts alt-usage" }, facts.map(([k, v]) => h("div", { class: "fact" }, h("small", {}, k), h("span", { class: "mono" }, String(v))))) : "";
}
/** D1036: an agent turn's earlier steps, from the file it wrote -- one block per task, joined to its
    live window: [start, upTo) of all its steps. */
const older = new Map(), fetching = new Set();
function earlierSteps(n, env, file, earlier, want) {
  const have = older.get(n.id) || { start: earlier, upTo: earlier, steps: [] };
  if (have.upTo > earlier || (have.start === have.upTo && have.upTo !== earlier)) { have.start = have.upTo = earlier; have.steps = []; }   // another file
  const need = [];
  if (have.upTo < earlier && have.steps.length) need.push([have.upTo, earlier - have.upTo]);    // left the live window since
  if (have.start > earlier - want) { const from = Math.max(0, earlier - want); need.push([from, have.start - from]); }   // scrolled up for more
  older.set(n.id, have);
  if (need.length && env.steps && !fetching.has(n.id)) {
    fetching.add(n.id);
    Promise.all(need.map(([start, count]) => env.steps(file, start, count).then(got => [start, got.steps || []])))
      .then(pages => {
        for (const [start, rows] of pages) {
          if (start >= have.upTo) { have.steps.push(...rows); have.upTo = start + rows.length; }
          else { have.steps.unshift(...rows); have.start = start; }
        }
        if (have.start === earlier && !have.steps.length) have.upTo = earlier;
      })
      .catch(() => { /* the file gone: the live window only */ })
      .finally(() => { fetching.delete(n.id); env.redraw(); });
  }
  return have.steps.slice(Math.max(0, have.steps.length - want));
}
/** D1039: a finished turn that wrote no steps file (before D1036): its kept steps from the transcript, asked once. */
const fromTranscript = new Map();
function transcriptSteps(n, env, agent) {
  if (!fromTranscript.has(n.id) && env.turnSteps) {
    fromTranscript.set(n.id, null);
    env.turnSteps(n.t0, n.t1, agent).then(got => { fromTranscript.set(n.id, got.steps || []); env.redraw(); }).catch(() => fromTranscript.set(n.id, []));
  }
  return fromTranscript.get(n.id);
}
const lostSaid = (n, kept, steps, lost) => running(n) ? `${lost} earlier step${lost === 1 ? "" : "s"}, shown when the turn ends`
  : kept == null ? "Loading the earlier steps…"
  : kept === steps ? `${lost} earliest step${lost === 1 ? " was" : "s were"} not kept`
  : `${lost} earlier step${lost === 1 ? " is" : "s are"} not in the transcript`;
function agentChat(n, env) {
  const f = { ...(running(n) ? {} : (n.output || {})), ...(n.fields || {}) }, p = n.params || {};
  const live = Array.isArray(f.steps) ? f.steps : [], total = Number(f["steps total"] || live.length), file = f["steps file"];
  const kept = !file && total > live.length && !running(n) ? transcriptSteps(n, env, String(f.agent || "")) : null;
  const steps = kept && kept.length > live.length ? kept : live;
  const limit = chatLimit.get(n.id) || CHAT_STEP, earlier = total - steps.length;
  // D1036: the whole conversation -- the steps before the live window come from the turn's file as one scrolls up
  const before = file && earlier > 0 ? earlierSteps(n, env, file, earlier, Math.max(0, limit - steps.length)) : [];
  const all = [...before, ...steps], shown = all.slice(-limit), above = (file ? earlier - before.length : 0) + all.length - shown.length;
  const lost = file ? 0 : earlier;                                         // past the 400 a turn keeps
  const conv = conversation(shown, { key: `task${n.id}`, offset: total - shown.length, live: running(n) });
  conv.querySelector(":scope > p.muted.small")?.remove();                   // the chat says what is above, below
  const prompt = typeof p.prompt === "string" ? p.prompt : "";
  const top = above > 0 ? h("div", { class: "alt-chat-more" }, `↑ ${above} earlier — scroll up`)
    : [lost > 0 ? h("div", { class: "alt-chat-more" }, lostSaid(n, kept, steps, lost)) : "",
      prompt ? bubble("me", `Prompt · ${prompt.length.toLocaleString()} characters`, h("pre", { class: "val tall", "data-k": "prompt" }, prompt), "input", env) : ""];
  // D1033: one "working" line -- the conversation's own when it is in a stretch of work (D1030), else this one
  const now = running(n) && !conv.querySelector(".cv-work.live") ? h("div", { class: "alt-chat-now" }, h("span", { class: "alt-spin" }), String(f.status || "working") + "…") : "";
  const out = f.stdout ?? f["stdout (live tail)"], err = f.stderr;
  return [usage(f, [["model", f.agent || p.model]]),
    h("div", { class: "agent-view" }, chatBox(n, env, [top, all.length ? conv : (running(n) ? "" : h("p", { class: "muted" }, "No conversation recorded.")), now], above > 0)),
    out ? fold("stdout", "stdout", `${lines(out).length} line${lines(out).length === 1 ? "" : "s"}`, h("pre", { class: "val astream-body", "data-k": "stdout" }, out), env) : "",
    err ? fold("stderr", "stderr", `${lines(err).length} line${lines(err).length === 1 ? "" : "s"}`, h("section", { class: `astream ${n.failed ? "err" : ""}` }, h("pre", { class: "val astream-body", "data-k": "stderr" }, err)), env) : ""];
}
function modelChat(n, env) {
  const f = fieldsOf(n), p = n.params || {};
  const reply = String(f.reply ?? f["reply (live tail)"] ?? ""), thinking = String(f.thinking ?? f["thinking (live tail)"] ?? "");
  let shown = reply;
  try { if (/^\s*[{[]/.test(reply)) shown = JSON.stringify(JSON.parse(reply), null, 2); } catch (_) { /* not JSON: as it came */ }
  const kids = [
    p.system ? bubble("me", `System · ${String(p.system).length.toLocaleString()} characters`, h("pre", { class: "val tall" }, String(p.system)), "system", env) : "",
    p.prompt ? bubble("me", `Prompt · ${String(p.prompt).length.toLocaleString()} characters`, h("pre", { class: "val tall", "data-k": "prompt" }, String(p.prompt)), "input", env) : "",
    thinking ? h("details", { class: "cv-step cv-think" }, h("summary", {}, h("span", { class: "cv-kind" }, "Thinking"), h("span", { class: "cv-sum muted" }, thinking.slice(0, 140))),
      h("pre", { class: "cv-body cv-thought" }, thinking)) : "",
    reply ? h("div", { class: "cv-text alt-reply" }, shown !== reply ? h("pre", { class: "mono" }, shown) : markdown(reply.trim())) : "",
    f.error ? h("div", { class: "alt-chat-more bad" }, String(f.error)) : "",
    running(n) ? h("div", { class: "alt-chat-now" }, h("span", { class: "alt-spin" }), "answering…") : ""];
  return [usage(f, [["model", p.model], ["thinking", p.think === false ? "off" : p.think ? "on" : ""]]), chatBox(n, env, kids, false)];
}

/** A search (P8): the point it proposed and that candidate's way through the pass. */
function searchBody(n, env) {
  const c = candidateOf(n);
  return [h("div", { class: "task-inspector" }, c ? h("div", { class: "kv" }, h("div", { class: "k" }, "point"), h("div", { class: "val mono" }, c)) : "",
    n.kids.length ? h("div", { class: "alt-steps" }, n.kids.map(k => stepRow(k, env))) : h("p", { class: "muted" }, running(n) ? "Proposing…" : "Nothing under it."))];
}

/** The candidates measured so far, with their numbers: the frontier a choice is made from. */
function measured(env) {
  const by = new Map();
  for (const x of env.nodes.values()) {
    if (x.t1 == null || x.failed || !isTool(x)) continue;
    const c = candidateOf(x), m = { ...metricsOf(stdoutOf(x)), ...dictsOf(stdoutOf(x)) };
    if (c && Object.keys(m).length) by.set(c, { ...(by.get(c) || {}), ...m });
  }
  return by;
}
/** D1055: a metric that is a dictionary ("sim_us={"t1": 41, "t2": 79}") -- its numbers by name. */
function dictsOf(text) {
  const out = {};
  for (const m of String(text).matchAll(/(?:^|\s)([A-Za-z_][\w.]*)=(\{[^{}]*\})/g)) {
    try { const d = JSON.parse(m[2]); if (d && typeof d === "object" && !(m[1] in out)) out[m[1]] = d; } catch (_) { /* not a dictionary */ }
  }
  return out;
}
const cell = v => v == null ? "—" : typeof v === "object" ? Object.entries(v).map(([k, x]) => `${k} ${typeof x === "number" ? fmt(x) : x}`).join(" · ") : fmt(v);
/** A choice (P9): what was chosen, and the frontier it was chosen from, sorted by the goals. */
function chooseBody(n, env) {
  // the decision may be the task's own or a task's under it (the outer "decide" holds the inner one)
  const all = [], walk = x => { all.push(fieldsOf(x)); (x.kids || []).forEach(walk); }; walk(n);
  const said = all.find(f => f.decision) || {};
  const text = String(said.decision || "") || all.map(o => Object.values(o).map(v => typeof v === "string" ? v : JSON.stringify(v)).join(" ")).join(" ");
  const rows = [...measured(env).entries()], goals = (env.goals?.() || []).filter(g => g.metric);
  // D1061: the goals first, then the other numbers measured (a dictionary metric too), three at most
  const seen = [...new Set(rows.flatMap(([, m]) => Object.keys(m)))];
  const cols = [...new Set([...goals.map(g => g.metric), ...seen])].slice(0, 3);
  // D1068: a pick measured in a pass not loaded here is still its table's first row -- its numbers from the decision itself
  const dec0 = String(said.decision || "").trim();
  if (dec0 && !rows.some(([c]) => c === dec0)) {
    const m = {}; for (const [k, v] of Object.entries(said["decision metrics"] || {})) { const at = k.indexOf(".");
      if (at > 0) (m[k.slice(0, at)] ||= {})[k.slice(at + 1)] = v; else m[k] = v; }
    rows.push([dec0, m, true]);
  }
  // D1063: the decision's own name; else the longest name the text holds -- "x#2" is the start of "x#2.2"
  const names = rows.map(([c]) => c), dec = dec0;
  const chosen = names.find(c => c === dec) || names.filter(c => text.includes(c)).sort((a, b) => b.length - a.length)[0] || "";
  // D1059: a design the select critic rejected stays on the record, marked -- never shown as chosen
  const rejected = new Map();
  // D1064: the measure critic's too -- a rejected measurement is never chosen either (its why is "x#3@stage")
  for (const x of env.nodes.values()) if (x.failed && /^\s*critique\s*:\s*(decision|measurement)/i.test(String(x.name)))
    rejected.set(String(x.why || "").replace(/@\S*$/, ""), String(fieldsOf(x).verdict || "").replace(/^REJECTED:\s*/i, ""));
  const refused = chosen && rejected.has(chosen);
  // D1055: a goal written as its name alone takes its direction from what the decision said ("the largest score")
  const g0 = goals[0], dir = g0?.direction || (/\b(largest|highest|most)\b/i.test(String(said["decided by"] || "")) ? "maximize" : /\b(smallest|lowest|least)\b/i.test(String(said["decided by"] || "")) ? "minimize" : "");
  const sign = g0 && dir ? (/max/i.test(dir) ? -1 : 1) : 0;
  const num = v => typeof v === "number" ? v : null;           // a dictionary is not ordered here: the chosen one leads
  rows.sort((a, b) => (b[0] === chosen) - (a[0] === chosen) || (g0 && sign ? sign * ((num(a[1][g0.metric]) ?? Infinity * sign) - (num(b[1][g0.metric]) ?? Infinity * sign)) : 0));
  return [h("div", { class: "task-inspector" },
    refused ? h("div", { class: "alt-result bad", style: "--k:var(--bad)" }, h("span", { class: "alt-big", title: chosen }, `✗ ${byId(chosen)}`),
      h("span", { class: "muted" }, `picked, then rejected by the critic${rejected.get(chosen) ? `: ${rejected.get(chosen)}` : ""}`))
    : chosen || said.decision ? h("div", { class: "alt-result ok", style: `--k:${colorOf(n)}` }, h("span", { class: "alt-big", title: chosen || said.decision }, `★ ${byId(chosen || said.decision)}`),
      said["decided by"] ? h("span", { class: "muted" }, said["decided by"]) : "") : "",
    rows.length ? h("div", { class: "alt-table-wrap" }, h("table", { class: "list compact alt-metrics" },
      h("thead", {}, h("tr", {}, h("th", {}, "design"), cols.map(k => { const g = goals.find(x => x.metric === k), d = g === g0 ? dir : g?.direction; return h("th", { class: "num" }, k, d ? ` ${/max/i.test(d) ? "↑" : "↓"}` : ""); }))),
      h("tbody", {}, rows.map(([c, m]) => h("tr", { class: rejected.has(c) ? "alt-rejected" : c === chosen ? "alt-chosen" : "", title: rejected.has(c) ? `rejected by the critic: ${rejected.get(c)}` : "" },
        h("td", { title: c }, rejected.has(c) ? [`✗ ${byId(c)}`, h("span", { class: "alt-critic-tag" }, "critic")] : c === chosen ? `★ ${byId(c)}` : byId(c),
          rows.find(r => r[0] === c)?.[2] ? h("span", { class: "muted small" }, " · an earlier pass") : ""), cols.map(k => h("td", { class: "num mono" }, cell(m[k])))))))) : "",
    n.kids.length ? h("div", { class: "alt-steps" }, n.kids.map(k => stepRow(k, env))) : "")];
}

/** A critic (P10): the verdict as a badge, the subject as a design's name and its numbers. */
function criticBody(n, env) {
  const f = fieldsOf(n), verdict = String(f.verdict ?? ""), subject = String(f.subject ?? "");
  const agreed = /no objection|stands|agree|^ok/i.test(verdict);
  const m = /^([^:{]+):\s*(\{.*\})\s*$/s.exec(subject), numbers = m ? metricsOf(m[2].replace(/['"{},]/g, " ").replace(/\s*:\s*/g, "=")) : {};
  return [h("div", { class: "task-inspector" },
    verdict ? h("div", { class: "alt-verdict" }, h("span", { class: `alt-badge ${agreed ? "ok" : "objection"}` }, agreed ? "No objection" : /^rejected/i.test(verdict) ? "Rejected" : "Objection"), agreed ? "" : h("span", {}, verdict)) : "",
    subject ? h("div", {}, h("div", { class: "alt-label" }, "Subject"), m ? h("div", { class: "alt-verdict" }, h("span", { class: "alt-pchip", style: `--k:${COLORS.choose}` }, m[1].trim()),
      h("span", { class: "mono muted small" }, Object.entries(numbers).map(([k, v]) => `${k} ${fmt(v)}`).join(" · "))) : h("div", { class: "mono" }, subject)) : "")];
}

/** Any other task: its steps (the tasks under it) with what each gave. */
function stepsBody(n, env) {
  // D1029: its own box's steps -- a check run inside a design's generation is the Check box's, not listed here
  const kind = phaseKind(n), kids = n.kids.filter(k => phaseKind(k) === kind);
  if (kids.length) return [h("div", { class: "task-inspector" }, h("div", { class: "alt-steps" }, kids.map(k => stepRow(k, env)),
    notReached(n).map(t => h("div", { class: "alt-step-row note skipped", title: "not run: a check before it failed" }, h("span", {}, `○ ${t} · not run`)))))];   // D1066: as the tree says
  // D1059: no steps of its own -- what it came to (a plan's pick, its menu), its short results said plainly
  const said = Object.entries(n.output || {}).filter(([k, v]) => !k.startsWith("_") && v != null && v !== "" && (typeof v !== "object") && String(v).length <= 160).slice(0, 4);
  return said.length ? [h("div", { class: "task-inspector" }, ...said.map(([k, v]) => h("div", { class: "kv" }, h("div", { class: "k" }, k), h("div", { class: "val" }, String(v)))))] : [];
}

/** The inspector of a task in LiveAlt (D982). `env`: now, nodes, tools(), block(), tidy(), agent(n),
    agentRest(n) (its stdout and folded prompt), pick(id), goals(), place. */
export function altInspector(n, env) {
  const hd = head(n);
  let body;
  if (isAgent(n)) body = [h("div", { class: "task-inspector" }, agentChat(n, env))];
  else if (isModel(n)) body = [h("div", { class: "task-inspector" }, modelChat(n, env))];
  else if (isTool(n)) body = toolBody(n, env);
  // D1055: a stage or check whose one child is its command is one line (D1034) -- its panel is the command's result
  else if (n.kids.length === 1 && isTool(n.kids[0]) && !(n.kids[0].kids || []).length) body = toolBody(n.kids[0], env);
  else if (hd === "dse") body = searchBody(n, env);
  else if (["decide", "frontier", "decision", "select"].includes(hd) || /^propose:\s*what next/i.test(n.name)) body = chooseBody(n, env);
  else if (hd === "critique") body = criticBody(n, env);
  else body = stepsBody(n, env);
  const f = n.fields || {}, o = n.output || {};
  const every = Object.keys(f).length || Object.keys(o).length || Object.keys(n.params || {}).length
    ? fold("fields", "Every field", "", [env.block("Input", n.params), env.block("Fields", f), env.block("Output", o)], env) : "";
  return [...inspectorHead(n, env), ...body, notesOf(n, env), isAgent(n) ? "" : every];
}
/** D1047: the quiet steps this task took (a brief, a frontier, the tools' gate) -- what each said, folded */
function notesOf(n, env) {
  const notes = n.notes || [];
  if (!notes.length) return "";
  const said = o => Object.entries(o || {}).filter(([, v]) => v !== "" && v != null).slice(0, 3)
    .map(([k, v]) => `${k}: ${String(typeof v === "object" ? JSON.stringify(v) : v).split("\n")[0].slice(0, 120)}`).join(" · ");
  return fold("notes", "Steps", `${notes.length}`, h("ul", { class: "alt-notes" }, notes.map(x =>
    h("li", {}, h("b", {}, taskLabel({ name: x.name, why: x.why, kids: [] })), said(x.output) ? h("span", { class: "muted small" }, ` ${said(x.output)}`) : ""))), env);
}

/** A pass at a glance (P11): what it tried and how that ended, where its time went, its steps. */
export function passSummary(p, nodes, env) {
  const now = Date.now() / 1000, end = n => n.t1 ?? now;
  const top = nodes.filter(n => !n.parent || !nodes.includes(n.parent));
  const t0 = Math.min(...nodes.map(n => n.t0)), t1 = Math.max(...nodes.map(end));
  const kinds = {};
  for (const n of nodes) for (const [a, b] of ownSpans(n, end)) { const k = isAgent(n) ? "design" : phaseKind(n); kinds[k] = (kinds[k] || 0) + (b - a); }
  const total = Object.values(kinds).reduce((a, b) => a + b, 0) || 1;
  const order = Object.keys(COLORS).filter(k => kinds[k]);
  const live = nodes.some(n => n.t1 == null), failed = nodes.some(n => n.failed);
  const cand = nodes.map(candidateOf).find(Boolean) || "";
  const meas = nodes.filter(n => isTool(n) && phaseKind(n) === "measure").map(n => metricsOf(stdoutOf(n))).find(m => Object.keys(m).length) || {};
  const steps = top.filter(n => !["setup"].includes(phaseKind(n)));
  return [h("div", { class: "detail-head alt-ihead" }, h("h2", {}, passName(p)),
      h("span", { class: `pill ${live ? "live" : failed ? "bad" : "ok"}` }, live ? h("span", { class: "alt-spin" }) : "", live ? "running" : failed ? "failed" : "done"), h("span", { class: "muted" }, dur(t1 - t0))),
    cand ? h("div", { class: "alt-tried" }, h("div", { class: "alt-label" }, "Tried"), h("b", {}, cand),
      h("span", { class: "mono muted small" }, Object.entries(meas).slice(0, 3).map(([k, v]) => `${k} ${fmt(v)}`).join(" · "))) : "",
    h("div", {}, h("div", { class: "alt-label" }, "Where the time went"),
      h("div", { class: "alt-bar tall" }, order.map(k => h("i", { style: `width:${100 * kinds[k] / total}%;background:${COLORS[k]}`, title: `${workLabel(k)} ${dur(kinds[k])}` }))),
      h("div", { class: "mono muted small" }, order.slice().sort((a, b) => kinds[b] - kinds[a]).filter(k => kinds[k] >= 1).map(k => `${workLabel(k)} ${dur(kinds[k])}`).join(" · ") || "all under a second")),
    steps.length ? h("div", { class: "alt-steps" }, steps.map(n => stepRow(n, env))) : ""];
}
