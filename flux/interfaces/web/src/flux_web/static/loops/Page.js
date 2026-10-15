// Flux web, loops (D693, D1095): the Loops page -- one's own loops, those shared with one, the group's.

import { Card, html } from "../view.js";
import { can } from "../state.js";
import { PageHead } from "../components/PageHead.js";
import { LoopsBrowser, LoopsTable } from "./Table.js";

export const LoopsPage = ({ loops, shared, group }) => html`<${PageHead} title="Loops" sub="Each loop is a problem document and its files; it runs or it does not, and a start resumes it.">
    ${can("create_loops") ? html`<a class="btn primary" href="#/configure">New loop</a>` : ""}<//>
  <${Card}><${LoopsBrowser} loops=${loops} /><//>
  <${Card} title="Shared with me" hidden=${!shared.length}><div>${shared.length ? html`<${LoopsTable} loops=${shared} who memo="flux-sort-shared" />` : ""}</div><//>
  <${Card} title="Group loops" cls="group-loops" hidden=${!group.length}><div>${group.length ? html`<${LoopsTable} loops=${group} who memo="flux-sort-group" />` : ""}</div><//>`;
