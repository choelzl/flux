// Flux web, a loop's page (D692, D697, D916, D919, D955, D1094, D1095): each tab's body -- what it reads, then its
// view. A body read afresh is drawn anew (`n`, the tab chosen again); one read again in place keeps what it shows
// meanwhile (`tick`: a note removed, the Overview's minute, a setting changed).

import { Card, html, useEffect, useState } from "../view.js";
import { loopApi } from "../ui.js";
import { me } from "../state.js";
import { Loaded, useLoad } from "../components/Load.js";
import { NoResults, Reading } from "./Cards.js";
import { OverviewAuthor } from "../components/AuthorJob.js";
import { loadOverviewLayout } from "../overview/layout.js";
import { Overview } from "../overview/Overview.js";
import { Checked, ResultsTab } from "../results/Tab.js";
import { Ideas } from "../results/Ideas.js";
import { Library } from "../results/Library.js";
import { AgentsTab } from "../agents/tab.js";
import { FilesTab, WorkbenchTab, showIgnored } from "../files/Tab.js";
import { LoopSettings } from "../settings/LoopSettings.js";
import { Page as LivePage } from "../live/Page.js";
import { useLoop } from "./context.js";
import { Failed } from "../components/Tone.js";

const In = ({ read, children }) => html`<${Loaded} read=${read} wait=${html`<${Reading} />`} card=${true}>${children}<//>`;

/** The loop's front page (D692): state, designs, the decision against the limits, the best so far per objective,
    the latest notes and the agents' newest workbench entries; above it the strip of an agent writing its problem
    (D704, D1131), and Start while it has not run. */
export function OverviewBody({ tick, notRun }) {
  const ctx = useLoop();
  const { name } = ctx;
  const read = useLoad(async () => {
    const [r, notes, bench, use, prefs, author] = await Promise.all([loopApi(name).api("/results"), loopApi(name).api("/notes").catch(() => []),
      loopApi(name).api("/workbench").catch(() => []), loopApi(name).api("/usage").catch(() => null), loadOverviewLayout(),
      loopApi(name).api("/author").catch(e => ({ ever: true, connection_error: e.message }))]);
    const needsIdeas = prefs.layout.stats.includes("ideas") || prefs.layout.columns.flat().includes("ideas");
    return { r, notes, bench, use, prefs, author, ideas: needsIdeas ? await loopApi(name).api("/ideas").catch(() => null) : null };
  }, ctx.name, tick);
  return html`<${In} read=${read}>${d => html`${d.author.ever ? html`<${OverviewAuthor} name=${name} mine=${ctx.mine} first=${d.author} />` : ""}${notRun}
    <${Overview} r=${d.r} notes=${d.notes} bench=${d.bench} use=${d.use} prefs=${d.prefs} ideas=${d.ideas} />`}<//>`;
}

/** The loop's results (D690) and its graphs: two views of one read, the selection kept from one to the other (D916);
    `cache` keeps the read while the tab stays chosen (Ideas and back). A loop with no measured design: the designs
    only their checks judged (D1094). */
export function ResultsBody({ view, cache, n }) {
  const ctx = useLoop();
  const read = useLoad(() => cache.current || loopApi(ctx.name).api("/results").then(r => (cache.current = r)), n);
  return html`<${In} read=${read}>${r => !r.campaign || !r.designs.length ? ((r.checked || []).length ? html`<${Checked} r=${r} />` : html`<${NoResults} />`)
    : html`<div class="results-tab"><${ResultsTab} r=${r} view=${view === "graphs" ? "graphs" : "results"} /></div>`}<//>`;
}

/** The ideas (D1095) or the library (D955), under Results: read again in place by Refresh. */
export function IdeasBody({ library = false }) {
  const ctx = useLoop();
  const read = useLoad(() => loopApi(ctx.name).api(`/${library ? "digests" : "ideas"}`), library);
  return html`<${In} read=${read}>${data => library ? html`<${Library} data=${data} refresh=${read.reload} />` : html`<${Ideas} data=${data} refresh=${read.reload} />`}<//>`;
}

/** What the agents' turns cost, each turn and its conversation. */
export function AgentsBody() {
  const ctx = useLoop();
  const read = useLoad(() => Promise.all([loopApi(ctx.name).api("/turns"), loopApi(ctx.name).api("/usage")]), ctx.name);
  return html`<${In} read=${read}>${([{ turns }, use]) => html`<${AgentsTab} turns=${turns} use=${use} />`}<//>`;
}

/** The loop's files (read again in place when the ignored ones are shown or hidden), or the agents' workbench. */
export function FilesBody({ tick, workbench = false }) {
  const ctx = useLoop();
  const { name } = ctx;
  const read = useLoad(() => workbench ? loopApi(name).api("/workbench").catch(() => [])
    : loopApi(name).api(`/files${showIgnored() ? "?ignored=true" : ""}`), workbench, tick);
  return html`<${In} read=${read}>${d => workbench ? html`<${WorkbenchTab} bench=${d} />` : html`<${FilesTab} root=${d} />`}<//>`;
}

/** The loop's settings (D697): read again in place when a variable changes. */
export function SettingsBody() {
  const ctx = useLoop();
  const { name } = ctx;
  const read = useLoad(() => Promise.all([loopApi(name).api("/env"), loopApi(name).api("/results"),
    loopApi(name).api("/shares").catch(() => null), ctx.isOwner || me.role === "admin" ? loopApi(name).api("/ownership").catch(() => null) : null,
    loopApi(name).api("/maintenance").catch(() => null)]), name);
  return html`<${In} read=${read}>${([e, results, sh, plan, mt]) => html`<${LoopSettings} e=${e} results=${results} sh=${sh} plan=${plan} mt=${mt} reload=${read.reload} />`}<//>`;
}

/** Live (D1096): the starts read, then the page -- its toolbar drawn in the sub-tab row by the loop's page once
    `ready(true)`; the store closed when it goes. */
export function LiveBody({ store, ready }) {
  const ctx = useLoop();
  const [got, setGot] = useState(null), [n, setN] = useState(0);
  useEffect(() => {
    let live = true;
    setGot(null);
    store.prepare(() => live).then(() => { if (!live) return; store.load(); setGot(true); ready(true); },        // its inspector first: the page reads it
      error => live && setGot(error));
    return () => { live = false; store.close(); ready(false); };
  }, [n]);
  if (!got) return html`<${Reading} lines=${6} />`;
  if (got !== true) return html`<${Card}><${Failed} what="Its starts could not be read" why=${got.message} retry=${() => setN(k => k + 1)} /><//>`;
  return html`<${LivePage} store=${store} />`;
}
