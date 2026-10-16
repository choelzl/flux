// Flux web, configure (D686, D934, D1116): the configurator's model -- website/docs/assets/crafter-model.js, the docs
// page's too: the document written from a state (`buildYaml`), what is wrong with it (`check`), a document read back
// (`fromDoc`) and the vocabulary. Loaded once, where a configurator is drawn (not on every page), with the tool catalog
// (/crafter-assets/tools.json) and the server's agents in its choices.

import { api } from "../ui.js";
import { crafterCatalog, setCrafterCatalog } from "../state.js";
import { useEffect, useState } from "../view.js";

let loading = null;
const script = src => new Promise((ok, no) => {
  const s = document.createElement("script");
  s.src = src; s.onload = ok; s.onerror = () => no(new Error(`${src} did not load`));
  document.head.appendChild(s);
});

/** D934: the server's agents in every box's choices -- asked by the first configurator, then again behind each. */
const agentsOf = M => api("/agents").then(list => M.setAgents(list.filter(a => a.id !== "model").map(a => ({ name: a.id, label: a.label }))), () => {});

/** The model, its catalog and agents set: a promise of it (null when its script does not load). */
function loadModel() {
  if (loading) { loading.then(M => M && agentsOf(M)); return loading; }
  loading = (async () => {
    if (!window.FluxCrafterModel) await script("/crafter-assets/crafter-model.js");
    const M = window.FluxCrafterModel;
    window.FluxCrafter = window.FluxCrafter || M;          // configure/Views.js's agent draft starts from its base()
    if (!crafterCatalog) setCrafterCatalog(await fetch("/crafter-assets/tools.json").then(r => r.json()).catch(() => []));
    M.setCatalog(crafterCatalog);
    await agentsOf(M);
    return M;
  })().catch(() => { loading = null; return null; });
  return loading;
}

/** The model once loaded: undefined while it loads, null when it did not. */
export function useModel() {
  const [M, setM] = useState(() => window.FluxCrafterModel && crafterCatalog ? window.FluxCrafterModel : undefined);
  useEffect(() => { let live = true; loadModel().then(m => { if (live) setM(m); }); return () => { live = false; }; }, []);
  return M;
}

/** The configurator's six steps (D941), by the ids an address names (`?step=checks`). Their titles are the model's
    STEP_TITLES (the docs' page's), here before it loads; test_loop_crafter.py keeps them the same (D1146). */
export const STEPS = [
  { id: "prompt", title: "Prompt" }, { id: "checks", title: "Check & Measure" }, { id: "objective", title: "Objective" },
  { id: "graph", title: "Graph" }, { id: "extra", title: "Extra" }, { id: "save", title: "Save" }];
/** A step's id from an index, an id, or an older name ("measure", "review", D941). */
export const stepId = (M, x) => STEPS[M.stepIndex(x == null || x === "" ? 0 : x)].id;

/** The step an address asks for: `#/configure?step=checks`, `#/app/NAME/settings/problem?step=graph`. */
export function stepOfHash() {
  const q = location.hash.split("?")[1] || "";
  return new URLSearchParams(q).get("step") || "";
}
/** The address follows the step shown (replaced, not a new entry). */
export function stepToHash(id) {
  const [path] = location.hash.split("?");
  const next = `${path}?step=${id}`;
  if (location.hash !== next) history.replaceState(null, "", next);
}
