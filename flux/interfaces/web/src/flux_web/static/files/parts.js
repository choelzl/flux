// Flux web, files (D808, D829, D908, D937, D1095): the pieces of the Files tab -- a path as links, what the
// selected item is (kind, size, a folder's size counted when asked, when it changed), its Actions menu, a list.

import { Ago, html, useEffect, useRef, useState } from "../view.js";
import { api } from "../ui.js";
import { bytes, plural } from "../format.js";
import { Act } from "../components/Act.js";

export const parentOf = p => String(p || "").split("/").slice(0, -1).join("/");
export const baseOf = p => String(p || "").split("/").pop();
export const joinP = (dir, leaf) => [dir, leaf].filter(Boolean).join("/");
const link = (label, onClick) => html`<a href="javascript:void 0" onClick=${onClick}>${label}</a>`;

/** D808: a path as its folders, each a link that opens it; the loop's own folder first. */
export function PathCrumbs({ name, path, open }) {
  const segs = String(path || "").split("/").filter(Boolean);
  return html`<span class="mono path-crumbs">${link(name, () => open("", true))}${segs.map((s, i) => html`<span class="muted"> / </span>${i < segs.length - 1
    ? link(s, () => open(segs.slice(0, i + 1).join("/"), true)) : html`<strong>${s}</strong>`}`)}</span>`;
}

/** A folder's size in all, asked for when it is selected (bounded on the server), not on a list. */
function FolderSize({ url }) {
  const [s, setS] = useState(null), [n, setN] = useState(0);
  useEffect(() => {
    let live = true;
    setS(null);
    api(url).then(got => { if (live) setS(got); }).catch(() => { if (live) setS(false); });
    return () => { live = false; };
  }, [url, n]);
  return html`<span class="item-size"> · ${s === null ? html`<span class="muted">counting…</span>`
    : s === false ? html`<${Act} cls="small link" onClick=${() => setN(n + 1)}>Calculate size<//>`
    : html`<span title=${`${s.bytes} bytes; hidden and ignored files counted, links not followed`}>
        ${`${s.partial ? "at least " : ""}${bytes(s.bytes)} in ${plural(s.files, "file")}, ${plural(s.folders, "folder")}${s.links ? `, ${plural(s.links, "link")}` : ""}`}</span>`}</span>`;
}

/** What the item is: its kind, its size (D908: KiB, MiB -- powers of 1024), when it changed. */
export const MetaLine = ({ it, sizeUrl }) => html`<div class="item-meta muted small"><span class="item-kind">${it.kind === "link" ? `link to ${it.target}` : it.kind}</span>
  ${it.kind === "file" ? html` · <span class="item-size" title=${`${it.size} bytes`}>${bytes(it.size)}</span>` : ""}
  ${sizeUrl ? html`<${FolderSize} url=${sizeUrl} />` : ""}${it.mtime ? html` · changed <${Ago} t=${it.mtime} />` : ""}</div>`;

/** Rename, Move and Delete (D908) on the selected item's head -- D937: the only place; what is not possible
    here disabled, with why. Opened from the keyboard, its first possible action takes the focus. */
export function ActionsMenu({ label, it, acts }) {
  const menu = useRef(null);
  const toggled = e => { const d = e.currentTarget; if (d.open && document.activeElement === d.querySelector("summary")) d.querySelector(".menu-list button:not([disabled])")?.focus(); };
  const item = (text, fn, ok) => html`<button type="button" role="menuitem" disabled=${!ok} title=${ok ? null : it.why}
    onClick=${async e => { e.preventDefault(); menu.current.open = false; await fn(it); }}>${text}</button>`;
  return html`<details class="actions-menu" ref=${menu} onToggle=${toggled}>
    <summary class="btn small" aria-label=${`Actions for ${label}`} title="Rename, move or delete">Actions ▾</summary>
    <div class="menu-list" role="menu">${item("Rename…", acts.rename, it.can.rename)}${item("Move…", acts.move, it.can.move)}${item("Delete…", acts.remove, it.can.delete)}
      ${it.why ? html`<p class="muted small why">${`Not here: ${it.why}.`}</p>` : ""}</div></details>`;
}

/** A folder's entries (D829: a loop's own parts stand out -- its documents, the folders Flux keeps; D908: a
    link says it is one, never followed here; D937: a row is its name and size, an ignored one greyed). */
export function FileList({ list, open }) {
  const own = f => !f.path.includes("/") && (f.dir ? ["out", "runs", "workbench", "library"].includes(f.path) : f.path === "problem.yaml" || f.path.endsWith(".problem.yaml"));
  return html`<ul class="files">${list.map(f => html`<li key=${f.path} class=${`${f.ignored ? "ignored" : ""}${own(f) ? " own" : ""}${f.link ? " link" : ""}`}
      title=${f.ignored ? "ignored (.gitignore, or a name starting with .)" : null}>
    <a href="javascript:void 0" title=${f.path} onClick=${() => open(f.path, f.dir)}><span class="ic">${f.dir ? "▸" : f.link ? "↪" : "·"}</span>${baseOf(f.path) + (f.dir ? "/" : "")}</a>
    ${f.dir || f.link ? "" : html`<small class="muted">${bytes(f.size)}</small>`}</li>`)}</ul>`;
}
