// Flux web, configure (D1136): the Library box opened -- what every prompt reads, in one place: the loop's files and
// top folders checked (`knowledge.files`), library/ and the shared library (read while it is on, their papers summed
// up once), files added here (the loop's, as Prompt › Files adds them, and checked), a text, a method sheet, who sums
// the papers up and the lessons drawn from the record (its agent's settings under More, D1142). On or off is the box's head.

import { html, useChanges, useEffect, useState } from "../view.js";
import { api } from "../ui.js";
import { plural } from "../format.js";
import { Uploader, placedFiles } from "../components/Uploader.js";
import { Pick, Text } from "./fields.js";
import { isAgent } from "./who.js";

const NO_STORE = { subscribe: () => () => {}, s: { files: [] } };
let shared = null;                                       // the shared library's papers, asked once
const sharedPapers = () => (shared = shared || api("/new-by-agent/uses").then(u => u.papers, () => null));

/** The rows of what may be read: each top-level file, each top folder (but library/), and what the document lists besides. */
export function readRows(files, reads) {
  const rows = new Map();
  for (const f of files) {
    if (f.path.startsWith("library/")) continue;
    const i = f.path.indexOf("/");
    if (i < 0) { rows.set(f.path, { path: f.path }); continue; }
    const d = f.path.slice(0, i + 1), r = rows.get(d) || { path: d, n: 0 };
    r.n++; rows.set(d, r);
  }
  for (const p of reads) if (!rows.has(p) && !rows.has(`${p.replace(/\/$/, "")}/`)) rows.set(p, { path: p, missing: !files.some(f => f.path === p || f.path.startsWith(`${p.replace(/\/$/, "")}/`)) });
  return [...rows.values()].sort((a, b) => a.path.localeCompare(b.path));
}

/** `who(box)` the choice of who works a box (BoxPanel's); `fixed` the flow is kept as written. */
export function Library({ M, state, edit, store, who, opts, fixed }) {
  const [up, setUp] = useState([]), [papers, setPapers] = useState(null);
  useChanges(store || NO_STORE);
  useEffect(() => { let live = true; sharedPapers().then(n => { if (live) setPapers(n); }); return () => { live = false; }; }, []);
  const f = state.flow, on = f.knowledge !== "none", files = (store || NO_STORE).s.files;
  const reads = M.list(state.knowledgeFiles), bare = p => p.replace(/\/$/, "");
  const isRead = p => reads.some(x => bare(x) === bare(p));
  const setReads = (add, drop = []) => {
    const out = reads.filter(x => !drop.some(p => bare(p) === bare(x)));
    for (const p of add) if (!out.some(x => bare(x) === bare(p))) out.push(p);
    state.knowledgeFiles = out.join(", ");
  };
  const toggle = p => edit(() => { if (isRead(p)) setReads([], [p]); else setReads([p]); });
  const lib = files.filter(x => x.path.startsWith("library/")).length;
  const add = () => {
    if (!store || up.some(r => r.dest === "custom" && !r.custom.trim())) return;
    store.add(placedFiles(up), "");
    const paths = up.filter(r => !r.loop && !r.zip && r.dest !== "library").map(r => {
      const into = r.dest === "custom" ? r.custom.trim().replace(/^\/+|\/+$/g, "") : r.dest, at = r.folder ? `${r.folder}/` : r.label;
      return into ? (r.folder ? `${into}/` : `${into}/${at}`) : at;
    });
    edit(() => setReads(paths));
    setUp([]);
  };
  const row = (path, note, checked, onToggle, disabled) => html`<li key=${path}><label>
    <input type="checkbox" checked=${checked} disabled=${disabled} onChange=${onToggle} /><code>${path}</code>${note ? html`<span class="muted small">${note}</span>` : ""}</label></li>`;
  return html`${on ? html`<div class="cfg-sub"><h4>What every prompt reads</h4>
      <ul class="cfg-lib-reads" aria-label="What every prompt reads">
        ${readRows(files, reads).map(r => row(r.path, r.missing ? "missing" : r.n ? `folder, ${plural(r.n, "file")}` : "", isRead(r.path), () => toggle(r.path), false))}
        ${row("library/", lib ? `${plural(lib, "paper")}, digested` : "papers added there, digested", true, null, true)}
        ${html`<li key="shared"><label><input type="checkbox" checked disabled /><span>shared library</span>${papers != null ? html`<span class="muted small">${plural(papers, "paper")}</span>` : ""}</label></li>`}</ul>
      ${store ? html`<${Uploader} rows=${up} onChange=${setUp} label="Drop files or folders to read" />
        ${up.length ? html`<div class="row"><button class="small primary" type="button" onClick=${add}>${`Add ${plural(placedFiles(up).length, "file")}`}</button></div>` : ""}` : ""}</div>
    <div class="cfg-sub"><${Text} area rows=${3} label="Text" k="knowledge-text" value=${state.knowledgeText} placeholder="What every prompt should know"
        set=${v => edit(() => { state.knowledgeText = v; })} />
      <${Pick} label="Method sheet" k="knowledge-sheet" value=${state.knowledgeSheet || ""} set=${v => edit(() => { state.knowledgeSheet = v; })}
        options=${[["", "none"], ...[...new Set([...files.map(x => x.path), ...(state.knowledgeSheet ? [state.knowledgeSheet] : [])])].sort().map(p => [p, p])]} /></div>
    <div class="cfg-sub"><h4>Papers summed up by</h4><div class="cfg-bar">${who("digest")}</div></div>`
    : html`<p class="cfg-says">Off: no files, papers or text are read.</p>`}
    <div class="cfg-sub"><h4>Lessons</h4><div class="cfg-bar">${who("lessons")}</div>${isAgent(f.lessons) ? opts("lessons") : ""}</div>`;
}
