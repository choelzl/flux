// Flux web: a loop's library, under Results (D955; D1095: the view is results/Library.js).
import { api, enc } from "./ui.js";
import { html, showIn } from "./view.js";
import { Library } from "./results/Library.js";

export async function libraryView(ctx) {
  const { name, qs, body, still } = ctx;
  const ok = still();
  const data = await api(`/apps/${enc(name)}/digests${qs}`);
  if (ok()) showIn(body, "library", html`<${Library} data=${data} refresh=${() => libraryView(ctx)} />`);
}
