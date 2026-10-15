// Flux web: a loop's Files tab -- its files, a viewer and editor, adding files; the agents' workbench (D892:
// out of loopPage; D1095: the views are files/).

import { api, enc } from "./ui.js";
import { html, showIn } from "./view.js";
import { FilesTab, WorkbenchTab, showIgnored } from "./files/Tab.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** The Files tab of a loop's page: `filesView()` (the loop's files), `workbenchView()` (the agents' workbench). */
function filesTab(ctx) {
  const { name, qs, body } = ctx;
  async function filesView() {
    const ok = ctx.still();                               // D919: Results -> Files: the late answer draws nothing
    const root = await api(`/apps/${enc(name)}/files${qs}${showIgnored() ? (qs ? "&" : "?") + "ignored=true" : ""}`);
    if (!ok()) return;
    showIn(body, "files", html`<${FilesTab} ctx=${ctx} root=${root} />`);
  }
  async function workbenchView() {
    const ok = ctx.still();                               // D919
    const bench = await api(`/apps/${enc(name)}/workbench${qs}`).catch(() => []);
    if (!ok()) return;
    showIn(body, "workbench", html`<${WorkbenchTab} ctx=${ctx} bench=${bench} />`);
  }
  return { filesView, workbenchView };
}

export { filesTab };
