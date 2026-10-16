// Flux web, configure (D1117): the configurator's steps down the left, each with what it says in one line; a step
// visited with nothing to fix is ticked, one with something to fix is marked. D913: on a phone, "Step 2 of 6" and a
// menu of the steps in their place.

import { cx, html } from "../view.js";
import { plural } from "../format.js";
import { Select } from "../components/Select.js";
import { STEPS } from "./model.js";
import { agentWord, isAgent } from "./who.js";

/** Each step's line: what it says now. */
export function sums(M, state, { files = 0, missing = 0, errors = 0, calm = false, isNew = false }) {
  const f = state.flow || {}, lang = M.language(state, true) || M.impliedLanguage(state);
  const r = M.resolve(state), words = M.describeObjectives(r.objectives);
  const knobs = M.knobNames(state), bp = state.baseline || {};
  const design = M.paramOnly(state) ? "settings only, nothing designs" : f.generate === "command" ? "a script designs" : f.generate === "catalog" ? "your designs, one by one"
    : isAgent(f.generate) ? `${agentWord(M, f.generate)} designs` : "a model designs";
  return {
    prompt: [missing ? `${plural(missing, "file")} missing` : "", String(state.id || "").trim() || "unnamed", lang || "any language", missing ? "" : plural(files, "file")].filter(Boolean).join(" · "),
    checks: `${plural(r.checks.length, "check")} · ${plural(r.stages.length, "measurement")}`,
    objective: words[0] || "nothing to decide by yet",
    graph: design,
    extra: [M.parameterSearch(state) ? `searches ${plural(knobs.length, "setting")}` : "no search",
      state.partsMode === "decompose" ? "parts as it decides" : M.hasParts(state) ? plural(M.list(state.parts).length, "part") : "one design",
      bp.mode && bp.mode !== "off" ? "a baseline first" : ""].filter(Boolean).join(" · "),
    save: errors ? `${plural(errors, "thing")} to ${calm ? "do" : "fix"}` : isNew ? "ready to create" : "ready to save",
  };
}

/** `at` the step shown; `marks[id]` "done" or "bad"; `go(id)`. */
export function Rail({ at, sum, marks, go }) {
  const i = STEPS.findIndex(s => s.id === at);
  return html`<nav class="card cfg-rail" aria-label="Steps">${STEPS.map((s, n) => html`<button type="button" key=${s.id} data-step=${s.id}
      class=${cx(s.id === at && "on", marks[s.id])} aria-current=${s.id === at ? "step" : null} onClick=${() => go(s.id)}>
      <span class="num">${marks[s.id] === "done" ? "✓" : n + 1}</span><span class="t">${s.title}</span><span class="sum">${sum[s.id]}</span></button>`)}</nav>
    <div class="cfg-stephead"><span class="cfg-stepof">${`Step ${i + 1} of ${STEPS.length}`}</span>
      <${Select} aria-label="Step" value=${at} onChange=${go} options=${STEPS.map(s => [s.id, s.title])} /></div>`;
}
