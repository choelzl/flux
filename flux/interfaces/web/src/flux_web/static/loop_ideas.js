// Flux web: a loop's ideas, under Results (D1095: the view is results/Ideas.js).

import { api, enc } from "./ui.js";
import { html, showIn } from "./view.js";
import { Ideas } from "./results/Ideas.js";

export async function ideasView(ctx) {
  const { name, qs, body, still } = ctx;
  const ok = still();
  const data = await api(`/apps/${enc(name)}/ideas${qs}`);
  if (!ok()) return;
  showIn(body, "ideas", html`<${Ideas} data=${data} refresh=${() => ideasView(ctx)} />`);
}
