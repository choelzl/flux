// Flux web, configure (D1166): what the configurator asks of where it runs -- the web app's server here; the docs'
// loop crafter page (website/docs/assets/crafter-app.js) sets its own, with no server: the model and its tool catalog
// beside its page, a fixed list of agents, no shared library. Read when asked, so a page sets it before it draws.

import { api } from "../ui.js";

export const host = {
  /** where crafter-model.js and tools.json are served (D686) */
  assets: "/crafter-assets",
  /** a promise of the agents a box may name ([{id, label}], D934) */
  agents: () => api("/agents"),
  /** a promise of the shared library's papers and Flux's own skills ({papers, loop_skills}, D1152), or null */
  uses: () => api("/new-by-agent/uses"),
};
