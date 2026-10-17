// Flux web, loops (D693, D1095): the Loops page -- one's own loops, those shared with one, the group's: one search
// and one filter over the three (shell-5), each its own table in one column grid (shell-8).

import { Card, html } from "../view.js";
import { can } from "../state.js";
import { PageHead } from "../components/PageHead.js";
import { LoopsBrowser } from "./Table.js";

export const LoopsPage = ({ loops, shared, group }) => {
  const create = can("create_loops") ? html`<a class="btn primary" href="#/configure">New loop</a>` : "";
  return html`<${PageHead} title="Loops">${create}<//>
  <${Card} cls="loops-page"><${LoopsBrowser} newLoop=${create ? html`<a class="btn small" href="#/configure">New loop</a>` : null}
    lists=${[[null, loops, false, "flux-sort-loops"], ["Shared with me", shared, true, "flux-sort-shared"], ["Group loops", group, true, "flux-sort-group"]]} /><//>`;
};
