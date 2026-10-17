// Flux web, configure (D1117): the configurator's steps down the left, each with what it says in one line; a step
// visited with nothing to fix is ticked, one with something to fix is marked. D913: on a phone, "Step 2 of 6" and a
// menu of the steps in their place.

import { html } from "../view.js";
import { plural } from "../format.js";
import { Select } from "../components/Select.js";
import { Steps } from "../components/Steps.js";
import { STEPS } from "./model.js";
import { agentWord, isAgent } from "./who.js";

/** Each step's line: what it says now; Save's is `save`, from the points Save's checklist lists (M.standing). */
export function sums(M, state, { files = 0, missing = 0, save = "" }) {
  const f = state.flow || {}, lang = M.language(state, true) || M.impliedLanguage(state);
  const r = M.resolve(state), words = M.describeObjectives(r.objectives), kept = at => (state.kept || []).includes(at);
  const knobs = M.knobNames(state), bp = state.baseline || {};
  const design = M.paramOnly(state) ? "settings only" : f.generate === "command" ? "script designs" : f.generate === "catalog" ? "files, one by one"
    : isAgent(f.generate) ? `${agentWord(M, f.generate)} designs` : "model designs";
  return {
    prompt: [missing ? `${plural(missing, "file")} missing` : "", String(state.id || "").trim() || "unnamed", lang || "any language", missing ? "" : plural(files, "file")].filter(Boolean).join(" · "),
    checks: `${kept("flow.test") ? "checks kept" : plural(r.checks.length, "check")} · ${kept("flow.measure") ? "measurements kept" : plural(r.stages.length, "measurement")}`,
    objective: kept("objectives") ? "kept as written" : words[0] || "no objective yet",
    // D1245: the search is the Graph's (Search's panel); Extra says it in the same words, linked
    graph: [design, knobs.length || (f.dse || "none") !== "none" ? M.searchSays(state) : ""].filter(Boolean).join(" · "),
    extra: [state.partsMode === "decompose" ? "parts as it decides" : M.hasParts(state) ? plural(M.list(state.parts).length, "part") : "one design",
      bp.mode && bp.mode !== "off" ? "baseline first" : ""].filter(Boolean).join(" · "),
    save,                                       // the model's standing: Save's checklist said in one line
  };
}

/** `at` the step shown; `marks[id]` "done" or "bad"; `go(id)`. */
export function Rail({ at, sum, marks, go }) {
  const i = STEPS.findIndex(s => s.id === at);
  return html`<${Steps} cls="rail card cfg-rail" at=${at} go=${go} items=${STEPS.map(s => ({ key: s.id, title: s.title, state: marks[s.id] || "", sum: sum[s.id] }))} />
    <div class="cfg-stephead"><span class="cfg-stepof">${`Step ${i + 1} of ${STEPS.length}`}</span>
      <${Select} aria-label="Step" value=${at} onChange=${go} options=${STEPS.map(s => [s.id, s.title])} /></div>`;
}
