// Flux web, results (results-21): a loop's objective and why a design was decided, said one way on every page --
// "minimize area_um2 · fmax_mhz ≥ 1000 (screen)", "smallest area_um2 at fmax_mhz >= 1000".

import { num } from "../format.js";

/** The objective from its list (the goal-free ones first, then each limit), else as the record says it (a `keep`
    or a balance, which the template does not say). */
export function objectiveText(r) {
  const list = r?.objective_list || [];
  if (!list.length || list.some(o => o.keep != null || o.balance)) return r?.objectives || "";
  const at = o => o.stage && o.stage !== "deepest" ? ` (${o.stage})` : "";
  return [...list.filter(o => o.goal == null).map(o => `${o.direction === "maximize" ? "maximize" : "minimize"} ${o.metric}${at(o)}`),
    ...list.filter(o => o.goal != null).map(o => `${o.metric} ${o.direction === "maximize" ? "≥" : "≤"} ${num(o.goal)}${at(o)}`)].join(" · ");
}

/** Why a design was decided, one wording: no leading "the", "smallest"/"largest" as the engine says it (older
    records said "the smallest", "least", "most"). */
export const decidedText = s => String(s || "").replace(/^the /, "").replace(/^least /, "smallest ").replace(/^most /, "largest ");
