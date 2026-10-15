// Flux web: the configurator -- a new loop or a changed one, by form, upload, direct edit or agent (D889: split out
// of app.js; D1095: its views are configure/, this the page and a loop's Settings › Problem).

import { can, me } from "./state.js";
import { appHref, pageShow } from "./ui.js";
import { Card, html, mount, node } from "./view.js";
import { Crumbs } from "./components/Crumbs.js";
import { PageHead } from "./components/PageHead.js";
import { Configure, ModeTabs, modesOf } from "./configure/Configure.js";

const modeOf = (name, mode) => {
  if (!name && mode === "empty") mode = "upload";          // older links open the combined form
  return modesOf(name).includes(mode) ? mode : "configurator";
};
const addressOf = (base, k) => base + (k === "configurator" ? "" : "/" + k);

/** The ways to make or change a problem (D704), into `host`: a loop's Settings › Problem (D713), its tabs in
    `barHost` (the page's sub-tab row), `base` the address the ways extend. */
function configureInto(host, name, owner, mode, base, { small = false, barHost = null } = {}) {
  mode = modeOf(name, mode);
  const bar = barHost ? node(null) : null;
  const pick = k => { mode = k; history.replaceState(null, "", addressOf(base, k)); draw(); };
  function draw() {
    if (bar) mount(bar, html`<${ModeTabs} name=${name} mode=${mode} pick=${pick} small=${small} />`);
    mount(host, html`<${Configure} name=${name} owner=${owner} mode=${mode} pick=${pick} tabs=${!bar} />`);
  }
  if (bar) barHost.append(bar);
  host.replaceChildren();
  draw();
}

/** Make or change a loop's problem (D704), as a page: New loop, or Configure a loop. */
async function configurePage(name, owner, mode = "configurator") {
  const show = pageShow(), isNew = !name;
  if (isNew && !can("create_loops")) { show(node(html`<${Card} title="New loop">Your account cannot create, upload or clone loops.<//>`)); return; }
  const sub = isNew ? "Start empty, upload a loop, build it with the configurator, or have an agent write it."
    : "Change the problem with the configurator, edit the document and its files directly, or have an agent revise it.";
  const host = node(null);
  show(node(html`<${Crumbs} parts=${isNew ? [["Loops", "#/"], ["New loop", null]] : [["Loops", "#/"], owner && owner !== me.name ? [owner, null] : null, [name, appHref(owner, name)], ["Configure", null]]} />
    <div class="configure-head"><${PageHead} title=${isNew ? "New loop" : html`<span>Configure <a href=${appHref(owner, name)}>${name}</a></span>`} sub=${sub} /></div>`), host);
  configureInto(host, name, owner, mode, isNew ? "#/configure" : `${appHref(owner, name)}/settings/problem`);
}

export { configureInto, configurePage };
