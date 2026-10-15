// Flux web: the configurator -- a new loop or a changed one, by form, upload, direct edit or agent (D889: split out
// of app.js; D1095: its views are configure/, this the page; a loop's Settings › Problem is loop/LoopPage.js's).

import { can, me } from "./state.js";
import { appHref, pageShow } from "./ui.js";
import { Card, html, node, useState } from "./view.js";
import { Crumbs } from "./components/Crumbs.js";
import { PageHead } from "./components/PageHead.js";
import { Configure, modesOf } from "./configure/Configure.js";

const modeOf = (name, mode) => {
  if (!name && mode === "empty") mode = "upload";          // older links open the combined form
  return modesOf(name).includes(mode) ? mode : "configurator";
};

/** The ways, their tabs above them; the address follows the one chosen. */
function Ways({ name, owner, first, base }) {
  const [mode, setMode] = useState(() => modeOf(name, first));
  const pick = k => { setMode(k); history.replaceState(null, "", base + (k === "configurator" ? "" : "/" + k)); };
  return html`<${Configure} name=${name} owner=${owner} mode=${mode} pick=${pick} />`;
}

/** Make or change a loop's problem (D704), as a page: New loop, or Configure a loop. */
function configurePage(name, owner, mode = "configurator") {
  const show = pageShow(), isNew = !name;
  if (isNew && !can("create_loops")) { show(node(html`<${Card} title="New loop">Your account cannot create, upload or clone loops.<//>`)); return; }
  const sub = isNew ? "Start empty, upload a loop, build it with the configurator, or have an agent write it."
    : "Change the problem with the configurator, edit the document and its files directly, or have an agent revise it.";
  show(node(html`<${Crumbs} parts=${isNew ? [["Loops", "#/"], ["New loop", null]] : [["Loops", "#/"], owner && owner !== me.name ? [owner, null] : null, [name, appHref(owner, name)], ["Configure", null]]} />
    <div class="configure-head"><${PageHead} title=${isNew ? "New loop" : html`<span>Configure <a href=${appHref(owner, name)}>${name}</a></span>`} sub=${sub} /></div>
    <${Ways} name=${name} owner=${owner} first=${mode} base=${isNew ? "#/configure" : `${appHref(owner, name)}/settings/problem`} />`));
}

export { configurePage };
