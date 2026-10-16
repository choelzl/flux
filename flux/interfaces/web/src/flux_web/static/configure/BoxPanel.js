// Flux web, configure (D1118): a box of the loop opened -- who works it (rules, a model, the coding agent, a script
// where Flux takes one, off), its critic when it has one, and that step's own settings, each default shown greyed.
// Search holds the search policy, Measure the cheap-with-costly comparison (calibrate) (D1139).
// Check, Measure and Choose say where their commands and goals are set; Knowledge is LibraryPanel.js (D1152). An agent's own
// settings (time limit, session, questions) fold under More, Design's probe below its knobs (D1142). `kept`: the
// flow is kept as written (who works each box said, not chosen), `keptBudget` the budget (its knobs read-only).

import { Notice, html } from "../view.js";
import { Head } from "../components/Head.js";
import { Pick, Text, Who, WhoTag } from "./fields.js";
import { Knowledge } from "./LibraryPanel.js";
import { AgentOpts, ProbeFields } from "./AgentOpts.js";
import { Select } from "../components/Select.js";
import { isAgent, whoOn, whoOptions } from "./who.js";

/** A budget knob of the loop, empty for its default (greyed); read-only when the budget is kept as written. */
const Knob = ({ state, edit, label, k, dflt, hint, disabled }) => html`<${Text} label=${label} value=${state.budget[k]} placeholder=${dflt} hint=${hint}
  disabled=${disabled} set=${v => edit(() => { state.budget[k] = v; })} />`;

const Goto = ({ go, step, what, children }) => html`<p class="cfg-says">${children} <button type="button" class="link" onClick=${() => go(step)}>${what}</button></p>`;


export function BoxPanel({ M, state, box, agent, setAgent = () => {}, edit, go, kept, keptBudget = false, store, direct }) {
  const W = M.WORDS.graph, f = state.flow, b = box === "knowledge" ? { title: W.knowledge, says: W.knowledgeSays } : M.BOXES[box] || { title: box };
  /** D1152: the workbench's digest unsaid is the papers' digester's: shown as it, and written only when it differs */
  const cur = bx => bx === "workbench" && (f.workbench === "default" || f.workbench === "off") ? f.digest : f[bx];
  const choose = (bx, opts) => k => edit(() => {
    f[bx] = opts.find(o => o.k === k).value();
    if (bx === "workbench" && f.workbench === f.digest) f.workbench = "default";
    if (state.agentRaw) delete state.agentRaw[bx];               // another choice: its own settings go (D728)
  });
  const labels = M.AGENT_LABELS();
  /** D1128: which coding agent, beside the choice when an agent works the box; D1142: its settings go with it */
  const which = (cur, set, slot) => isAgent(cur) && cur !== M.INLINE_AGENT && !kept ? html`<${Select} aria-label="Which coding agent" value=${cur.slice(6)}
    options=${M.AGENTS.map(a => [a, labels[a] || a])} onChange=${a => { setAgent(a); edit(() => { M.renameAgent(state, slot, a); set(`agent:${a}`); }); }} />` : "";
  const opts = slot => html`<${AgentOpts} M=${M} state=${state} slot=${slot} edit=${edit} disabled=${kept} />`;
  const who = (bx = box) => {
    const opts = whoOptions(M, bx, cur(bx), agent);
    return html`<span class="cfg-who-row"><${Who} label=${`Who works ${M.BOXES[bx].title}`} fixed=${kept} options=${opts} on=${whoOn(opts, cur(bx))} pick=${choose(bx, opts)} />
      ${which(cur(bx), v => { f[bx] = v; }, bx)}</span>`;
  };
  const critic = bx => {
    const cur = M.criticsOf(state)[bx], opts = whoOptions(M, "critique", cur, agent);
    return html`<div class="cfg-bar"><span class="muted small">Critic</span><${Who} label=${`The critic on ${M.BOXES[bx].title}`} fixed=${kept} options=${opts}
      on=${whoOn(opts, cur)} pick=${k => edit(() => { M.setCritic(state, bx, opts.find(o => o.k === k).value()); })} />${which(cur, v => M.setCritic(state, bx, v), `critic:${bx}`)}</div>
      ${isAgent(cur) ? html`<${AgentOpts} M=${M} state=${state} slot=${`critic:${bx}`} edit=${edit} disabled=${kept} />` : ""}`;
  };
  const K = (label, k, dflt, hint) => html`<${Knob} state=${state} edit=${edit} label=${label} k=${k} dflt=${dflt} hint=${hint} disabled=${keptBudget} />`;
  const B = k => K(M.WORDS.budget[k].label, k, M.WORDS.budget[k].dflt, M.WORDS.budget[k].hint);   // D1146: the docs' words too
  let head = who(), body = "";
  switch (box) {
    case "orchestrate": {                       // D1139: the search policy is Search's
      const group = M.dseGroup(f.dse), choices = M.dseChoices(group, f.dse).map(c => [c.value, c.label]);
      const set = v => edit(() => { f.dse = v; if (state.agentRaw) delete state.agentRaw.dse; });
      const policy = html`<div class="cfg-grid2"><${Pick} label=${W.dseKind} k="dse-group" disabled=${kept} value=${group} options=${M.DSE_GROUPS}
          set=${v => set(M.dseChoices(v)[0].value)} />
        <${Pick} label=${W.dsePolicy} k="dse" disabled=${kept} value=${f.dse} options=${choices} set=${set} /></div>
        <p class="cfg-says">${M.dseHint(state)}</p>
        ${M.parameterSearch(state) ? html`<div class="cfg-grid2">${B("batch")}</div>` : ""}`;
      if (M.parameterSearch(state)) { head = html`<${WhoTag} kind="rules">settings<//>`; body = html`${policy}<${Goto} go=${go} step="extra" what=${`Extra › ${M.WORDS.extra.settings}`}>${W.searchPicks}:<//>`; break; }
      body = html`${policy}${f.orchestrate === "command" ? html`<div class="cfg-grid2"><${Text} label=${W.searchCommand} mono wide value=${state.orchestrateCommand}
            placeholder=${W.searchPlaceholder} hint=${W.searchHint} set=${v => edit(() => { state.orchestrateCommand = v; })} />
          <${Text} label=${M.WORDS.checks.timeLimit} value=${state.orchestrateTimeout} placeholder="600" set=${v => edit(() => { state.orchestrateTimeout = v; })} /></div>`
        : ["default", "model", "tools"].includes(f.orchestrate) ? html`<${Pick} label="How the model picks" value=${f.orchestrate} disabled=${kept}
            options=${M.BOXES.orchestrate.choices.filter(c => c.half === "model").map(c => [c.value, c.label])} set=${v => edit(() => { f.orchestrate = v; })} />` : ""}
        ${critic("orchestrate")}
        <div class="cfg-grid2">${K("Start fresh every", "explore_every", "4", "designs")}${K("Explore after", "explore_after", "2", "passes the decision stands")}
          ${K("A part yields after", "cooldown_after", "3", "designs that do not build")}${B("exploration_quota")}</div>`;
      break;
    }
    case "generate":
      body = html`${M.paramOnly(state) ? html`<${Notice} tone="warn" what=${W.searchOn[0]} why=${W.searchOn[1]} />` : ""}
        ${f.generate === "command" ? html`<${Text} label=${W.command} mono wide value=${state.generateCommand} placeholder=${W.generatePlaceholder}
          hint=${W.generateHint} set=${v => edit(() => { state.generateCommand = v; })} />` : ""}
        ${f.generate === "catalog" ? html`<${Text} label=${W.existing} wide value=${state.generateCatalog} placeholder=${W.existingPlaceholder}
          hint=${W.existingHint} set=${v => edit(() => { state.generateCatalog = v; })} />` : ""}
        <div class="cfg-grid2">${B("steps")}${B("repair_attempts")}
          <${Pick} label=${W.prototype} hint=${W.prototypeHint} disabled=${keptBudget} value=${state.budget.prototype} set=${v => edit(() => { state.budget.prototype = v; })}
            options=${W.prototypes} />
          <${Pick} label="Repair by" disabled=${keptBudget} value=${state.budget.patching} set=${v => edit(() => { state.budget.patching = v; })}
            options=${[["", "edits (default)"], ["true", "edits"], ["false", "rewrites"]]} />
          ${K("Revert after", "revert_after", "3", "failed repairs")}${M.hasParts(state) ? K("Parts at once", "parallel_parts", "1") : ""}</div>
        ${isAgent(f.generate) ? html`<div class="cfg-sub"><h4>Agent turns</h4><div class="cfg-grid2">${K("Continues past time", "agent_continues", "3")}
          ${K("Final turn, s", "agent_grace_s", "¼ of its time")}</div></div>
          <${ProbeFields} M=${M} state=${state} edit=${edit} disabled=${kept} />` : ""}`;
      break;
    case "test":
      head = html`<${WhoTag} kind="fixed">checks<//>`;
      body = html`<${Goto} go=${go} step="checks" what=${M.STEP_TITLES[1]}>Its commands are set in<//>${critic("test")}`;
      break;
    case "measure": {                           // D1139: calibration is Measure's
      const calib = whoOptions(M, "calibrate", f.calibrate, agent);
      head = html`<${WhoTag} kind="fixed">measurements<//>`;
      body = html`<${Goto} go=${go} step="checks" what=${M.STEP_TITLES[1]}>Its commands are set in<//>
        <div class="cfg-bar"><span class="muted small">Compare cheap with costly</span><${Who} label="Compare cheap with costly" fixed=${kept}
          options=${calib} on=${whoOn(calib, f.calibrate)} pick=${choose("calibrate", calib)} /></div>
        <div class="cfg-grid2">${B("workers")}</div>${M.criticsOf(state).measure !== "off" ? critic("measure") : ""}`;
      break;
    }
    case "select":
      body = html`<${Goto} go=${go} step="objective" what="Objective">What it decides by is set in<//>${critic("select")}`;
      break;
    case "records":
      head = html`<${WhoTag} kind="fixed">kept<//>`;
      body = html`<div class="cfg-grid2">${B("passes")}${K("Time per pass, s", "budget_s", "no limit")}
        ${K("Worse edits tolerated", "regress_after", "2")}${B("parallel")}</div>`;
      break;
    case "knowledge":
      head = "";                                // D1152: each section its own switch
      body = html`<${Knowledge} M=${M} state=${state} edit=${edit} store=${store} who=${who} opts=${opts} fixed=${kept} direct=${direct} />`;
      break;
    default:
  }
  return html`<${Head} title=${b.title} hint=${b.says} actions=${head || null} />
    ${box !== "knowledge" && isAgent(f[box]) && !M.parameterSearch(state) ? opts(box) : ""}
    ${body}`;
}
