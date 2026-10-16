// Flux web, configure (D1118): a box of the loop opened -- who works it (rules, a model, the coding agent, a script
// where Flux takes one, off), its critic when it has one, and that step's own settings, each default shown greyed.
// Search holds the search policy, Measure the cheap-with-costly comparison (calibrate) (D1139).
// Check, Measure and Choose say where their commands and goals are set; the Library is LibraryPanel.js. `kept`: the
// flow is kept as written (who works each box said, not chosen), `keptBudget` the budget (its knobs read-only).

import { Notice, html } from "../view.js";
import { Head } from "../components/Head.js";
import { Kept, Pick, Text, Who, WhoTag, isKept } from "./fields.js";
import { Library } from "./LibraryPanel.js";
import { Select } from "../components/Select.js";
import { isAgent, whoOn, whoOptions } from "./who.js";

/** A budget knob of the loop, empty for its default (greyed); read-only when the budget is kept as written. */
const Knob = ({ state, edit, label, k, dflt, hint, disabled }) => html`<${Text} label=${label} value=${state.budget[k]} placeholder=${dflt} hint=${hint}
  disabled=${disabled} set=${v => edit(() => { state.budget[k] = v; })} />`;

const Goto = ({ go, step, what, children }) => html`<p class="cfg-says">${children} <button type="button" class="link" onClick=${() => go(step)}>${what}</button></p>`;

/** The Library's title and line: the box of what is read (knowledge), its papers summed up and its lessons. */
const LIBRARY = { title: "Library", says: "What every prompt reads: files, papers summed up once, text, lessons." };

export function BoxPanel({ M, state, box, agent, setAgent = () => {}, edit, go, kept, keptBudget = false, store, direct }) {
  const f = state.flow, b = box === "knowledge" ? LIBRARY : M.BOXES[box] || { title: box };
  const choose = (bx, opts) => k => edit(() => {
    f[bx] = opts.find(o => o.k === k).value();
    if (state.agentRaw) delete state.agentRaw[bx];               // another choice: its own settings go (D728)
  });
  const labels = M.AGENT_LABELS();
  /** D1128: which coding agent, beside the choice when an agent works the box (its own settings aside) */
  const which = (cur, set) => isAgent(cur) && cur !== M.INLINE_AGENT && !kept ? html`<${Select} aria-label="Which coding agent" value=${cur.slice(6)}
    options=${M.AGENTS.map(a => [a, labels[a] || a])} onChange=${a => { setAgent(a); edit(() => set(`agent:${a}`)); }} />` : "";
  const who = (bx = box) => {
    const opts = whoOptions(M, bx, f[bx], agent);
    return html`<span class="cfg-who-row"><${Who} label=${bx === "knowledge" ? "Library" : `Who works ${M.BOXES[bx].title}`} fixed=${kept} options=${opts} on=${whoOn(opts, f[bx])} pick=${choose(bx, opts)} />
      ${which(f[bx], v => { f[bx] = v; if (state.agentRaw) delete state.agentRaw[bx]; })}</span>`;
  };
  const critic = bx => {
    const cur = M.criticsOf(state)[bx], opts = whoOptions(M, "critique", cur, agent);
    return html`<div class="cfg-bar"><span class="muted small">Critic</span><${Who} label=${`The critic on ${M.BOXES[bx].title}`} fixed=${kept} options=${opts}
      on=${whoOn(opts, cur)} pick=${k => edit(() => { M.setCritic(state, bx, opts.find(o => o.k === k).value()); })} />${which(cur, v => M.setCritic(state, bx, v))}</div>`;
  };
  const K = (label, k, dflt, hint) => html`<${Knob} state=${state} edit=${edit} label=${label} k=${k} dflt=${dflt} hint=${hint} disabled=${keptBudget} />`;
  const raw = state.agentRaw && state.agentRaw[box] && isAgent(f[box]);
  let head = who(), body = "";
  switch (box) {
    case "orchestrate": {                       // D1139: the search policy is Search's
      const group = M.dseGroup(f.dse), choices = M.dseChoices(group, f.dse).map(c => [c.value, c.label]);
      const set = v => edit(() => { f.dse = v; if (state.agentRaw) delete state.agentRaw.dse; });
      const policy = html`<div class="cfg-grid2"><${Pick} label="Kind" k="dse-group" disabled=${kept} value=${group} options=${M.DSE_GROUPS}
          set=${v => set(M.dseChoices(v)[0].value)} />
        <${Pick} label="Policy" k="dse" disabled=${kept} value=${f.dse} options=${choices} set=${set} /></div>
        <p class="cfg-says">${M.dseHint(state)}</p>
        ${M.parameterSearch(state) ? html`<div class="cfg-grid2">${K("Designs side by side", "batch", "1")}</div>` : ""}`;
      if (M.parameterSearch(state)) { head = html`<${WhoTag} kind="rules">settings<//>`; body = html`${policy}<${Goto} go=${go} step="extra" what="Extra › Settings to search">Search over settings picks next job:<//>`; break; }
      body = html`${policy}${f.orchestrate === "command" ? html`<div class="cfg-grid2"><${Text} label="Search command" mono wide value=${state.orchestrateCommand}
            placeholder="{python} {home}/search.py {history} {state}" hint="answers each round with the next candidates" set=${v => edit(() => { state.orchestrateCommand = v; })} />
          <${Text} label="Time limit, s" value=${state.orchestrateTimeout} placeholder="600" set=${v => edit(() => { state.orchestrateTimeout = v; })} /></div>`
        : ["default", "model", "tools"].includes(f.orchestrate) ? html`<${Pick} label="How the model picks" value=${f.orchestrate} disabled=${kept}
            options=${M.BOXES.orchestrate.choices.filter(c => c.half === "model").map(c => [c.value, c.label])} set=${v => edit(() => { f.orchestrate = v; })} />` : ""}
        ${critic("orchestrate")}
        <div class="cfg-grid2">${K("Start fresh every", "explore_every", "4", "designs")}${K("Explore after", "explore_after", "2", "passes the decision stands")}
          ${K("A part yields after", "cooldown_after", "3", "designs that do not build")}${K("Share kept for new ideas", "exploration_quota", "0", "0 to 1")}</div>`;
      break;
    }
    case "generate":
      body = html`${M.paramOnly(state) ? html`<${Notice} tone="warn" what="A search over settings is on:" why="only a script turns a point into a design; with a model or agent, the settings only reach the commands." />` : ""}
        ${f.generate === "command" ? html`<${Text} label="Command" mono wide value=${state.generateCommand} placeholder="{python} {home}/gen.py {artifact} {knob}"
          hint="writes {artifact}; each setting is {its name}" set=${v => edit(() => { state.generateCommand = v; })} />` : ""}
        ${f.generate === "catalog" ? html`<${Text} label="Existing designs" wide value=${state.generateCatalog} placeholder="designs/a.sv, designs/b.sv"
          hint="files beside the document, tried in order" set=${v => edit(() => { state.generateCatalog = v; })} />` : ""}
        <div class="cfg-grid2">${K("Designs per pass", "steps", "24")}${K("Repairs per design", "repair_attempts", "12")}
          <${Pick} label="Prototype first" hint="default: with a golden model" disabled=${keptBudget} value=${state.budget.prototype} set=${v => edit(() => { state.budget.prototype = v; })}
            options=${[["", "default"], ["false", "off"], ["true", "on"], ["python", "in Python"], ["systemc", "in SystemC"]]} />
          <${Pick} label="Repair by" disabled=${keptBudget} value=${state.budget.patching} set=${v => edit(() => { state.budget.patching = v; })}
            options=${[["", "edits (default)"], ["true", "edits"], ["false", "rewrites"]]} /></div>
        ${isAgent(f.generate) ? html`<div class="cfg-sub"><h4>Agent turns</h4><div class="cfg-grid2">${K("Continues past time", "agent_continues", "3")}
          ${K("Final turn, s", "agent_grace_s", "¼ of its time")}</div></div>` : ""}`;
      break;
    case "test":
      head = html`<${WhoTag} kind="fixed">checks<//>`;
      body = html`<${Goto} go=${go} step="checks" what="Check & Measure">Its commands are set in<//>${critic("test")}`;
      break;
    case "measure": {                           // D1139: calibration is Measure's
      const calib = whoOptions(M, "calibrate", f.calibrate, agent);
      head = html`<${WhoTag} kind="fixed">measurements<//>`;
      body = html`<${Goto} go=${go} step="checks" what="Check & Measure">Its commands are set in<//>
        <div class="cfg-bar"><span class="muted small">Compare cheap with costly</span><${Who} label="Compare cheap with costly" fixed=${kept}
          options=${calib} on=${whoOn(calib, f.calibrate)} pick=${choose("calibrate", calib)} /></div>
        <div class="cfg-grid2">${K("Tool runs at once", "workers", "auto", "1 for anything timed")}</div>${M.criticsOf(state).measure !== "off" ? critic("measure") : ""}`;
      break;
    }
    case "select":
      body = html`<${Goto} go=${go} step="objective" what="Objective">What it decides by is set in<//>${critic("select")}`;
      break;
    case "records":
      head = html`<${WhoTag} kind="fixed">kept<//>`;
      body = html`<div class="cfg-grid2">${K("Passes", "passes", "until stopped")}${K("Time per pass, s", "budget_s", "no limit")}
        ${K("Worse edits tolerated", "regress_after", "2")}${K("Passes at once", "parallel", "1", "needs an admin on a server")}</div>`;
      break;
    case "knowledge":
      if (isKept(state, "flow.knowledge")) { head = ""; body = html`<${Kept} M=${M} state=${state} at="flow.knowledge" direct=${direct} />`; break; }
      body = html`<${Library} M=${M} state=${state} edit=${edit} store=${store} who=${who} fixed=${kept} />`;
      break;
    default:
  }
  return html`<${Head} title=${b.title} hint=${b.says} actions=${head || null} />
    ${raw ? html`<p class="cfg-says">${`Own settings, kept as written: ${JSON.stringify(state.agentRaw[box].agent)}. Another choice replaces them.`}</p>` : ""}
    ${body}`;
}
