// Flux web, configure (D1136, D1152): the Knowledge box opened -- what every agent and prompt gets, in four sections,
// each a head with its switch (or its choice) and its settings under it while on:
//   Skills: the loop's skills/ and the built-in ones (flux-loop-commands), each with a switch; skill folders dropped
//     here go to skills/ (`skills:` -- off, or `{off: [names]}`);
//   Library: what every prompt reads -- the loop's files and top folders checked (`knowledge.files`), library/ and the
//     shared library (read while it is on, their papers summed up once), files added here, a text, a method sheet, who
//     sums the papers up;
//   Lessons from records: off, mined or an agent (its settings under More, D1142);
//   Workbench digest: on or off, who sums it up (the papers' digester unless said);
//   Hidden from AI (D1159): the files, folders and patterns the agents may not read (`knowledge.hidden`), added and
//     removed here -- the loop's checks and measurements still use them.

import { html, useChanges, useEffect, useState } from "../view.js";
import { host } from "./host.js";
import { plural } from "../format.js";
import { Uploader, placedFiles } from "../components/Uploader.js";
import { Choice } from "../components/Tabs.js";
import { Kept, Pick, Text, isKept } from "./fields.js";
import { isAgent } from "./who.js";
import { Check } from "../components/Check.js";
import { Tool } from "../components/Head.js";
import { HiddenMark, hiddenBy } from "../files/hidden.js";

const NO_STORE = { subscribe: () => () => {}, s: { files: [] } };
let uses = null;                                         // the shared library's papers and Flux's own skills, asked once
const usesOnce = () => (uses = uses || Promise.resolve().then(host.uses).catch(() => null));
const SKILL = /^skills\/([^/]+)\/SKILL\.md$/;
/** Folders the Library's list leaves out: the loop's papers (their own row) and its skills (their own section). */
const NOT_READ = ["library/", "skills/"];

/** The rows of what may be read: each top-level file, each top folder (but library/ and skills/), and what the document lists besides. */
function readRows(files, reads) {
  const rows = new Map();
  for (const f of files) {
    if (NOT_READ.some(p => f.path.startsWith(p))) continue;
    const i = f.path.indexOf("/");
    if (i < 0) { rows.set(f.path, { path: f.path }); continue; }
    const d = f.path.slice(0, i + 1), r = rows.get(d) || { path: d, n: 0 };
    r.n++; rows.set(d, r);
  }
  for (const p of reads) if (!rows.has(p) && !rows.has(`${p.replace(/\/$/, "")}/`)) rows.set(p, { path: p, missing: !files.some(f => f.path === p || f.path.startsWith(`${p.replace(/\/$/, "")}/`)) });
  return [...rows.values()].sort((a, b) => a.path.localeCompare(b.path));
}

/** A section's head: its title, a line, and its switch (`on`, `set(on)`) or any control (`control`). */
const Head = ({ title, says, on, set, control, disabled }) => html`<div class="cfg-bar"><h4>${title}${says ? html` <span class="muted">${says}</span>` : ""}</h4><span class="grow" />
  ${control || html`<${Choice} small label=${title} value=${on ? "on" : "off"} onChange=${v => { if (!disabled) set(v === "on"); }} options=${[["on", "on"], ["off", "off"]]} />`}</div>`;

/** Adds the rows picked to the loop's files at `into` ("" its folder, "skills": a skill folder dropped as is). */
function AddFiles({ store, rows, setRows, into, label, then }) {
  if (!store) return "";
  const add = () => {
    if (into === "" && rows.some(r => r.dest === "custom" && !r.custom.trim())) return;
    const placed = into === "skills" ? rows.map(r => ({ ...r, dest: r.folder === "skills" ? "" : "skills" })) : rows;
    store.add(placedFiles(placed), "");
    then?.(placed);
    setRows([]);
  };
  return html`<${Uploader} rows=${rows} onChange=${setRows} places=${into === ""} label=${label} />
    ${rows.length ? html`<div class="row"><button class="small primary" type="button" onClick=${add}>${`Add ${plural(placedFiles(rows).length, "file")}`}</button></div>` : ""}`;
}

/** `who(box)` the choice of who works a box (BoxPanel's), `opts(slot)` an agent's settings; `fixed` the flow is kept as written. */
export function Knowledge({ M, state, edit, store, who, opts, fixed, direct }) {
  const [up, setUp] = useState([]), [upSkills, setUpSkills] = useState([]), [got, setGot] = useState(null), [hideIn, setHideIn] = useState("");
  useChanges(store || NO_STORE);
  useEffect(() => { let live = true; usesOnce().then(u => { if (live) setGot(u); }); return () => { live = false; }; }, []);
  const W = M.WORDS.graph, f = state.flow, files = (store || NO_STORE).s.files, keptKnow = isKept(state, "flow.knowledge");
  const papers = got ? got.papers : null, builtIn = (got && got.loop_skills) || [];
  const row = (key, words, note, checked, onToggle, disabled, title) => html`<li key=${key} title=${title || null}><${Check} checked=${checked} disabled=${disabled} onChange=${onToggle}>
    ${words}${note ? html`<span class="muted small">${note}</span>` : ""}<//></li>`;

  // Skills: the loop's own (skills/<name>/SKILL.md) and the built-in ones, each switched on or off
  const own = [...new Set(files.map(x => (x.path.match(SKILL) || [])[1]).filter(Boolean))].sort();
  const skillsKept = isKept(state, "skills"), skillsOn = state.skillsOn !== false;
  const skillRow = (name, note, title) => row(`skill:${name}`, html`<code>${name}</code>`, note, M.skillOn(state, name),
    on => edit(() => M.setSkill(state, name, on)), skillsKept, title);
  const skills = html`<div class="cfg-sub" data-sec="skills">
    <${Head} title=${W.skills} on=${skillsOn} disabled=${skillsKept} set=${on => edit(() => { state.skillsOn = on; })} />
    ${skillsKept ? html`<${Kept} M=${M} state=${state} at="skills" direct=${direct} />` : skillsOn ? html`<p class="cfg-says">${W.skillsSays}</p>
      <ul class="cfg-lib-reads" aria-label=${W.skills}>
        ${own.map(n => skillRow(n, `skills/${n}`))}
        ${builtIn.filter(s => !own.includes(s.name)).map(s => skillRow(s.name, W.builtIn, s.description))}</ul>
      ${own.length ? "" : html`<p class="cfg-says">${W.skillsNone}</p>`}
      ${M.list(state.skillsFrom).length ? html`<p class="cfg-says">${W.skillsFrom}: ${M.list(state.skillsFrom).map((p, i) => html`${i ? ", " : ""}<code>${p}</code>`)}</p>` : ""}
      <${AddFiles} store=${store} rows=${upSkills} setRows=${setUpSkills} into="skills" label=${W.skillsDrop} />` : ""}</div>`;

  // Library: what every prompt reads
  const libOn = f.knowledge !== "none", reads = M.list(state.knowledgeFiles), bare = p => p.replace(/\/$/, "");
  const isRead = p => reads.some(x => bare(x) === bare(p));
  const setReads = (add, drop = []) => {
    const out = reads.filter(x => !drop.some(p => bare(p) === bare(x)));
    for (const p of add) if (!out.some(x => bare(x) === bare(p))) out.push(p);
    state.knowledgeFiles = out.join(", ");
  };
  const toggle = p => edit(() => { if (isRead(p)) setReads([], [p]); else setReads([p]); });
  const inLib = files.filter(x => x.path.startsWith("library/")), hid = M.hiddenOf(state);
  const libHidden = inLib.filter(x => hiddenBy(x.path, hid)).length, lib = inLib.length - libHidden;   // a hidden file is no paper read
  const readAdded = placed => edit(() => setReads(placed.filter(r => !r.loop && !r.zip && r.dest !== "library").map(r => {
    const into = r.dest === "custom" ? r.custom.trim().replace(/^\/+|\/+$/g, "") : r.dest, at = r.folder ? `${r.folder}/` : r.label;
    return into ? (r.folder ? `${into}/` : `${into}/${at}`) : at;
  })));
  const library = html`<div class="cfg-sub" data-sec="library">
    <${Head} title=${W.library} on=${libOn} disabled=${fixed} set=${on => edit(() => { f.knowledge = on ? "default" : "none"; })} />
    ${libOn ? html`<h4 class="muted small">${W.reads}</h4>
      <ul class="cfg-lib-reads" aria-label=${W.reads}>
        ${readRows(files, reads).filter(r => !hiddenBy(r.path, hid))   // D1209: hidden from AI is read by no prompt -- listed under Hidden only
          .map(r => row(r.path, html`<code>${r.path}</code>`, r.missing ? "missing" : r.n ? `folder, ${plural(r.n, "file")}` : "", isRead(r.path), () => toggle(r.path), false))}
        ${row("library/", html`<code>library/</code>`, (lib ? `${plural(lib, "paper")}, digested` : "papers added there, digested") + (libHidden ? `; ${libHidden} hidden from AI` : ""), true, null, true)}
        ${row("shared", html`<span>shared library</span>`, papers != null ? plural(papers, "paper") : "", true, null, true)}</ul>
      <${AddFiles} store=${store} rows=${up} setRows=${setUp} into="" label="Drop files or folders to read" then=${readAdded} />
      <${Text} area rows=${3} label="Text" k="knowledge-text" value=${state.knowledgeText} placeholder="What every prompt should know"
        set=${v => edit(() => { state.knowledgeText = v; })} />
      <${Pick} label="Method sheet" k="knowledge-sheet" value=${state.knowledgeSheet || ""} set=${v => edit(() => { state.knowledgeSheet = v; })}
        options=${[["", "none"], ...[...new Set([...files.map(x => x.path), ...(state.knowledgeSheet ? [state.knowledgeSheet] : [])])].sort().map(p => [p, p])]} />
      <div class="cfg-bar"><span class="muted small">${W.papersBy}</span>${who("digest")}</div>`
    : html`<p class="cfg-says">${W.libraryOff}</p>`}</div>`;

  // Lessons from records: off, mined, an agent
  const lessons = html`<div class="cfg-sub" data-sec="lessons"><${Head} title=${W.lessons} control=${who("lessons")} />
    ${isAgent(f.lessons) ? opts("lessons") : ""}</div>`;

  // Workbench digest: on or off, and who
  const wbOn = f.workbench !== "off";
  const workbench = html`<div class="cfg-sub" data-sec="workbench">
    <${Head} title=${W.workbench} on=${wbOn} disabled=${fixed} set=${on => edit(() => { f.workbench = on ? "default" : "off"; if (state.agentRaw) delete state.agentRaw.workbench; })} />
    ${wbOn ? html`<p class="cfg-says">${W.workbenchSays}</p><div class="cfg-bar"><span class="muted small">${W.workbenchBy}</span>${who("workbench")}</div>
      ${isAgent(f.workbench) ? opts("workbench") : ""}` : ""}</div>`;

  // Hidden from AI: the paths no agent or prompt reads, the loop's own tools still do (D1159)
  const hidden = M.hiddenOf(state), isDoc = p => /(^|\/)problem\.ya?ml$|\.problem\.ya?ml$/.test(p);
  const setHidden = list => edit(() => { state.hidden = list; });
  const addHidden = () => {
    const p = hideIn.trim().replace(/^\.\//, "");
    if (p && !hidden.includes(p)) setHidden([...hidden, p]);
    setHideIn("");
  };
  const tops = [...new Set(files.map(x => x.path.includes("/") ? `${x.path.split("/")[0]}/` : x.path))].filter(p => !isDoc(p) && !hidden.some(h => bare(h) === bare(p))).sort();
  const hiddenSec = html`<div class="cfg-sub" data-sec="hidden">
    <${Head} title=${W.hidden} control=${hidden.length ? html`<span class="muted small">${`${hidden.length} ${W.hiddenCount}`}</span>` : html`<span />`} />
    <p class="cfg-says">${W.hiddenSays}</p>
    ${hidden.length ? html`<ul class="cfg-hidden" aria-label=${W.hidden}>${hidden.map(p => html`<li key=${p}><${HiddenMark} path=${p} /><code>${p}</code>
        <${Tool} icon="close" label=${`${W.hiddenRemove}: ${p}`} onClick=${() => setHidden(hidden.filter(x => x !== p))} /></li>`)}</ul>`
      : html`<p class="cfg-says muted">${W.hiddenNone}</p>`}
    <div class="row cfg-hide-add"><input class="mono" list="cfg-hide-choices" aria-label=${W.hiddenAdd} placeholder=${W.hiddenAdd} value=${hideIn}
        onInput=${e => setHideIn(e.currentTarget.value)} onKeyDown=${e => { if (e.key === "Enter") { e.preventDefault(); addHidden(); } }} />
      <datalist id="cfg-hide-choices">${tops.map(p => html`<option key=${p} value=${p} />`)}</datalist>
      <button class="small" type="button" disabled=${!hideIn.trim()} onClick=${addHidden}>${W.hiddenPick}</button></div></div>`;

  return html`${skills}${keptKnow ? html`<${Kept} M=${M} state=${state} at="flow.knowledge" direct=${direct} />` : html`${library}${hiddenSec}${lessons}${workbench}`}`;
}
