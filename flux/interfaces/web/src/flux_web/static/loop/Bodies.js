// Flux web, a loop's page (D692, D697, D916, D919, D955, D1094, D1095): each tab's body -- what it reads, then its
// view. A body read afresh is drawn anew (`n`, the tab chosen again); one read again in place keeps what it shows
// meanwhile (`tick`: a note removed, the Overview's minute, a setting changed).

import { Card, html, useEffect, useState } from "../view.js";
import { api, enc } from "../ui.js";
import { me } from "../state.js";
import { Loaded, useLoad } from "../components/Load.js";
import { NoResults, Reading } from "./Cards.js";
import { AuthorBox } from "./Authoring.js";
import { loadOverviewLayout } from "../overview_layout.js";
import { Overview } from "../overview/Overview.js";
import { Checked, ResultsTab } from "../results/Tab.js";
import { Ideas } from "../results/Ideas.js";
import { Library } from "../results/Library.js";
import { AgentsTab } from "../agents/tab.js";
import { FilesTab, WorkbenchTab, showIgnored } from "../files/Tab.js";
import { LoopSettings } from "../settings/LoopSettings.js";
import { Page as LivePage } from "../live/Page.js";

const In = ({ read, children }) => html`<${Loaded} read=${read} wait=${html`<${Reading} />`} card=${true}>${children}<//>`;

/** The loop's front page (D692): state, designs, the decision against the limits, the best so far per objective,
    the latest notes and the agents' newest workbench entries; above it the card of an agent writing its problem
    (D704), and Start while it has not run. */
export function OverviewBody({ ctx, tick, notRun }) {
  const { name, qs } = ctx;
  const read = useLoad(async () => {
    const [r, notes, bench, use, prefs, author] = await Promise.all([api(`/apps/${enc(name)}/results${qs}`), api(`/apps/${enc(name)}/notes${qs}`).catch(() => []),
      api(`/apps/${enc(name)}/workbench${qs}`).catch(() => []), api(`/apps/${enc(name)}/usage${qs}`).catch(() => null), loadOverviewLayout(),
      api(`/apps/${enc(name)}/author${qs}`).catch(e => ({ ever: true, connection_error: e.message }))]);
    const needsIdeas = prefs.layout.stats.includes("ideas") || prefs.layout.columns.flat().includes("ideas");
    return { r, notes, bench, use, prefs, author, ideas: needsIdeas ? await api(`/apps/${enc(name)}/ideas${qs}`).catch(() => null) : null };
  }, ctx.name, tick);
  return html`<${In} read=${read}>${d => html`${d.author.ever ? html`<${AuthorBox} name=${name} qs=${qs} mine=${ctx.mine} first=${d.author} />` : ""}${notRun}
    <${Overview} ctx=${ctx} r=${d.r} notes=${d.notes} bench=${d.bench} use=${d.use} prefs=${d.prefs} ideas=${d.ideas} />`}<//>`;
}

/** The loop's results (D690) and its graphs: two views of one read, the selection kept from one to the other (D916);
    `cache` keeps the read while the tab stays chosen (Ideas and back). A loop with no measured design: the designs
    only their checks judged (D1094). */
export function ResultsBody({ ctx, view, cache, n }) {
  const read = useLoad(() => cache.current || api(`/apps/${enc(ctx.name)}/results${ctx.qs}`).then(r => (cache.current = r)), n);
  return html`<${In} read=${read}>${r => !r.campaign || !r.designs.length ? ((r.checked || []).length ? html`<${Checked} ctx=${ctx} r=${r} />` : html`<${NoResults} />`)
    : html`<div class="results-tab"><${ResultsTab} ctx=${ctx} r=${r} view=${view === "graphs" ? "graphs" : "results"} /></div>`}<//>`;
}

/** The ideas (D1095) or the library (D955), under Results: read again in place by Refresh. */
export function IdeasBody({ ctx, library = false }) {
  const read = useLoad(() => api(`/apps/${enc(ctx.name)}/${library ? "digests" : "ideas"}${ctx.qs}`), library);
  return html`<${In} read=${read}>${data => library ? html`<${Library} data=${data} refresh=${read.reload} />` : html`<${Ideas} data=${data} refresh=${read.reload} />`}<//>`;
}

/** What the agents' turns cost, each turn and its conversation. */
export function AgentsBody({ ctx }) {
  const read = useLoad(() => Promise.all([api(`/apps/${enc(ctx.name)}/turns${ctx.qs}`), api(`/apps/${enc(ctx.name)}/usage${ctx.qs}`)]), ctx.name);
  return html`<${In} read=${read}>${([{ turns }, use]) => html`<${AgentsTab} ctx=${ctx} turns=${turns} use=${use} />`}<//>`;
}

/** The loop's files (read again in place when the ignored ones are shown or hidden), or the agents' workbench. */
export function FilesBody({ ctx, tick, workbench = false }) {
  const { name, qs } = ctx;
  const read = useLoad(() => workbench ? api(`/apps/${enc(name)}/workbench${qs}`).catch(() => [])
    : api(`/apps/${enc(name)}/files${qs}${showIgnored() ? (qs ? "&" : "?") + "ignored=true" : ""}`), workbench, tick);
  return html`<${In} read=${read}>${d => workbench ? html`<${WorkbenchTab} ctx=${ctx} bench=${d} />` : html`<${FilesTab} ctx=${ctx} root=${d} />`}<//>`;
}

/** The loop's settings (D697): read again in place when a variable changes. */
export function SettingsBody({ ctx }) {
  const { name, qs } = ctx;
  const read = useLoad(() => Promise.all([api(`/apps/${enc(name)}/env${qs}`), api(`/apps/${enc(name)}/results${qs}`),
    api(`/apps/${enc(name)}/shares${qs}`).catch(() => null), ctx.isOwner || me.role === "admin" ? api(`/apps/${enc(name)}/ownership${qs}`).catch(() => null) : null,
    api(`/apps/${enc(name)}/maintenance${qs}`).catch(() => null)]), name);
  return html`<${In} read=${read}>${([e, results, sh, plan, mt]) => html`<${LoopSettings} ctx=${ctx} e=${e} results=${results} sh=${sh} plan=${plan} mt=${mt} reload=${read.reload} />`}<//>`;
}

/** Live (D1096): the starts read, then the page -- its toolbar drawn in the sub-tab row by the loop's page once
    `ready(true)`; the store closed when it goes. */
export function LiveBody({ ctx, store, ready }) {
  const [got, setGot] = useState(null), [n, setN] = useState(0);
  useEffect(() => {
    let live = true;
    setGot(null);
    store.prepare(() => live).then(() => { if (!live) return; store.load(); setGot(true); ready(true); },        // its inspector first: the page reads it
      error => live && setGot(error));
    return () => { live = false; store.close(); ready(false); };
  }, [n]);
  if (!got) return html`<${Reading} lines=${6} />`;
  if (got !== true) return html`<${Card} actions=${html`<button type="button" onClick=${() => setN(k => k + 1)}>Retry</button>`}><div class="empty"><p>${`Starts unavailable: ${got.message}`}</p></div><//>`;
  return html`<${LivePage} store=${store} ctx=${ctx} />`;
}
