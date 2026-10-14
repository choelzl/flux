// Flux web: a loop's Agents tab -- what the turns cost, each turn and its conversation
// (D892: out of loopPage).

import { proseBlock } from "./highlight.js";
import { act, ago, api, card, dur, empty, enc, fmtTok, h, skeleton, plural } from "./ui.js";
import { conversation } from "./loops.js";
import { fullscreen, viewerTools } from "./viewer.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** What the model and agent turns cost (D694): in all, and per agent or model. */
function usageCard(u) {
  const t = u.total;
  if (!t.turns) return "";
  const fig = (label, value, sub) => h("div", { class: "stat" }, h("small", {}, label), h("div", { class: "big" }, value), sub ? h("div", { class: "muted" }, sub) : "");
  return card("Usage", [
    h("div", { class: "stats five" },
      fig("Turns", String(t.turns), t.errors ? `${t.errors} failed` : ""),
      fig("Time", dur(t.seconds) || "0s", t.turns ? `${dur(t.seconds / t.turns)} a turn` : ""),
      fig("Tokens in", t.counted ? fmtTok(t.tokens_in) : "—", t.tokens_cached ? `${fmtTok(t.tokens_cached)} from the cache` : ""),
      // D1092: said once -- a turn without tokens is also one whose usage is incomplete
      fig("Tokens out", t.counted ? fmtTok(t.tokens_out) : "—", !t.counted ? (t.turns ? "no turn reported its tokens" : "")
        : [t.counted < t.turns ? `${plural(t.turns - t.counted, "turn")} without tokens` : "", t.partial ? `${t.partial} partial` : ""].filter(Boolean).join(" · ")),
      fig("Cost", t.cost_usd ? `$${t.cost_usd.toFixed(2)}` : "—", t.cost_usd ? "at the prices set, else the agent's own" : "no prices set")),
    u.by.length > 1 ? h("table", { class: "list compact" }, h("thead", {}, h("tr", {}, ["Who", "Kind", "Turns", "Time", "Tokens in", "Tokens out", "Cost"].map((x, i) => h("th", { class: i > 1 ? "num" : "" }, x)))),
      h("tbody", {}, u.by.map(b => h("tr", {}, h("td", { class: "strong" }, b.who), h("td", { class: "muted" }, b.kind),
        h("td", { class: "num mono" }, String(b.turns)), h("td", { class: "num mono" }, dur(b.seconds)),
        h("td", { class: "num mono" }, b.counted ? fmtTok(b.tokens_in) : "—"), h("td", { class: "num mono" }, b.counted ? fmtTok(b.tokens_out) : "—"),
        h("td", { class: "num mono" }, b.cost_usd ? `$${b.cost_usd.toFixed(2)}` : "—"))))) : ""]);
}

/** The Agents tab: the turns' cost, the turns, one turn's detail. */
async function agentsView(ctx) {
  const { name, qs, q, body } = ctx;
  const ok = ctx.still();                                 // D919: drawn only while still the tab chosen
  body.replaceChildren(card(null, skeleton(7)));
  const [{ turns }, use] = await Promise.all([api(`/apps/${enc(name)}/turns${qs}`), ctx.history ? null : api(`/apps/${enc(name)}/usage${qs}`)]);
  if (!ok()) return;
  const one = h("div", { class: "detail" }, empty("Select a turn."));
  let picked = 0;                                         // D919: the turn selected last is the one shown
  const pick = async (t, tr) => {
    for (const x of tr.parentNode.children) x.classList.remove("sel"); tr.classList.add("sel");
    const my = ++picked;
    const full = (await api(`/apps/${enc(name)}/turns?k=${t.k}${q}`)).turns[0] || {};
    if (my !== picked || !ok()) return;
    const nt = full.notes && typeof full.notes === "object" ? full.notes : {};
    // D1094: what the agent says it is, without its own name again ("Python 3.13.13", not "python3 Python 3.13.13")
    const about = String(full.about || nt.model || full.model || ""), mine = String(full.agent || "");
    const facts = [["kind", full.kind], ["model", mine && about.startsWith(mine + " ") ? about.slice(mine.length + 1) : about || null], ["server", full.server],
      ["session", full.session ? `${full.session}${full.session_id ? " · " + full.session_id : ""}` : null], ["exit", full.rc],
      ["tokens in", full.tokens_in ?? nt.input_tokens], ["tokens out", full.tokens_out ?? nt.output_tokens], ["from the cache", full.tokens_cached],
      // D1094: "partial" only beside tokens it did report -- a turn that reported none says so
      ["token usage", full.tokens_complete === false ? ((full.tokens_in ?? nt.input_tokens) == null && (full.tokens_out ?? nt.output_tokens) == null
        ? "Not reported by the agent" : "Partial: includes only tokens the agent reported before stopping") : null],
      ["cost", full.cost_usd ? `$${Number(full.cost_usd).toFixed(4)}` : null], ["tool calls", full.tool_calls ?? (full.hops || []).length],
      ["prompt", full.prompt_chars ? `${full.prompt_chars} chars` : full.prompt ? `${String(full.prompt).length} chars` : null],
      ["finish", nt.finish], ["schema", nt.schema], ["folder", full.workdir]].filter(([, v]) => v != null && v !== "");
    // D712: what most want first -- the model, the exit, the tokens, the tools; the rest folded
    const MAIN = new Set(["model", "exit", "tokens in", "tokens out", "token usage", "tool calls", "cost"]);
    const factEl = ([k, v]) => h("div", { class: `fact${k === "exit" && v !== 0 && v !== "0" ? (Number(v) === 124 ? " warn" : " bad") : ""}` }, h("small", {}, k), h("span", { class: k === "folder" ? "mono small" : "mono" }, String(v)));
    const more = facts.filter(([k]) => !MAIN.has(k));
    one.replaceChildren(h("div", { class: "detail-head" }, h("h2", {}, full.agent || full.model || full.kind), h("span", { class: "muted" }, ago(full.ts), " · ", dur(full.seconds))),
      h("div", { class: "actions" }, full.prompt ? act("Prompt", () => fullscreen(h("pre", { class: "raw-content" }, String(full.prompt)), "Agent prompt"), { cls: "small agent-prompt-button" }) : "",
        ...viewerTools(one, { title: "Agent turn", rawText: JSON.stringify(full, null, 2) })),
      h("div", { class: "facts" }, facts.filter(([k]) => MAIN.has(k)).map(factEl)),
      more.length ? h("details", { class: "facts-more" }, h("summary", {}, `More: ${more.map(([k]) => k).join(", ")}`), h("div", { class: "facts" }, more.map(factEl))) : "",
      ...(Array.isArray(full.steps) && full.steps.length
        ? [full.error ? h("div", { class: "blk" }, h("h3", {}, "error"), proseBlock(String(full.error))) : "",
           conversation(full.steps, { key: `turn${t.k}` }),
           full.stderr ? h("details", { class: "blk" }, h("summary", {}, "stderr"), proseBlock(String(full.stderr))) : "",
           full.prompt ? h("details", { class: "blk" }, h("summary", {}, `Prompt (${String(full.prompt).length.toLocaleString()} characters)`), proseBlock(String(full.prompt))) : ""]
        : [...["error", "reply", "prompt", "stderr"].filter(k => full[k]).map(k => h("div", { class: "blk" }, h("h3", {}, k), proseBlock(String(full[k])))),
           ...((full.hops || []).length ? [h("h3", {}, "Tool calls"), ...(full.hops || []).map(x => h("pre", { class: "val" }, x))] : [])]));
  };
  const ROLES = { generate: "design", critique: "critic" };
  const roleOf = (t) => { const m = /\/agents\/([^/]+)\//.exec(String(t.workdir || "")); return m ? ROLES[m[1]] || m[1] : ""; };   // D1094: a role or nothing -- "model" is its kind, the name says it
  const tokOf = (t) => { const n = t.notes && typeof t.notes === "object" ? t.notes : {};
    const i = t.tokens_in ?? n.input_tokens, o = t.tokens_out ?? n.output_tokens;
    if (i == null && o == null) return t.tokens_complete === false ? h("span", { title: "the agent stopped before it reported its tokens" }, "—") : "";   // D1094: "—", as everywhere
    return `${i == null ? "—" : fmtTok(i)} → ${o == null ? "—" : fmtTok(o)}${t.tokens_complete === false ? " (partial)" : ""}`; };
  body.replaceChildren(use ? usageCard(use) : "", h("div", { class: "split" },
    card(null, turns.length ? h("div", { class: "rows-scroll agent-turns" }, h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, "Who"), h("th", {}, "When"), h("th", {}, "Took"),
        h("th", { class: "num", title: "tokens in → out" }, "Tokens"), h("th", { class: "num", title: "tool calls" }, "Tools"), h("th", {}, ""))),
      h("tbody", {}, turns.slice().reverse().map(t => { const tr = h("tr", { class: "clickable", onclick: () => pick(t, tr) },
        // D1092: what the turn was for (design, critic, prototype, plan) beside who took it; its version on hover -- one line
        h("td", { title: [t.about || (t.notes && t.notes.model) || "", t.session === "resumed" ? "resumed its session" : ""].filter(Boolean).join(" · ") || null },
          roleOf(t) ? h("span", { class: "muted" }, `${roleOf(t)} · `) : "",
          h("button", { type: "button", class: "link strong open-turn", onclick: (e) => { e.stopPropagation(); pick(t, tr); } },   // D929: Tab, Enter
            t.agent || t.model || t.kind), t.session === "resumed" ? h("span", { class: "muted small" }, " · resumed") : ""),
        h("td", { style: "white-space:nowrap" }, ago(t.ts)), h("td", { class: "muted", style: "white-space:nowrap" }, dur(t.seconds)),   // D1093: one line a turn
        h("td", { class: "num mono muted" }, tokOf(t)), h("td", { class: "num mono muted" }, t.tool_calls != null ? String(t.tool_calls) : t.hops ? String(t.hops) : ""),
        h("td", {}, t.error ? h("span", { class: "pill bad" }, "error") : t.ok === false ? h("span", { class: `pill ${t.rc === 124 ? "warn" : "bad"}`, title: t.rc === 124 ? "stopped at its time limit" : null }, t.rc === 124 ? "time limit" : `exit ${t.rc}`) : h("span", { class: "pill ok" }, "ok"))); return tr; }))))
      : empty("No model or agent turn yet.")),
    card(null, one, { cls: "detail-card" })));
  // D1094: the newest turn open at once, not an empty "Select a turn." beside the list
  const first = body.querySelector(".agent-turns tbody tr");
  if (first && turns.length) pick(turns[turns.length - 1], first);
}

export { agentsView, usageCard };
