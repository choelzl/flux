// Flux web, files (D808, D829, D908, D937, D1095): the pieces of the Files tab -- a path as links, what the
// selected item is (kind, size, a folder's size counted when asked, when it changed), its Actions menu, a list.

import { Ago, html } from "../view.js";
import { api } from "../ui.js";
import { bytes, plural } from "../format.js";
import { Act } from "../components/Act.js";
import { useLoad } from "../components/Load.js";
import { usePopover } from "../components/Popover.js";
import { HiddenMark, hiddenBy, hiddenByName } from "./hidden.js";

export const parentOf = p => String(p || "").split("/").slice(0, -1).join("/");
export const baseOf = p => String(p || "").split("/").pop();
export const joinP = (dir, leaf) => [dir, leaf].filter(Boolean).join("/");
const link = (label, onClick) => html`<button type="button" class="link" onClick=${onClick}>${label}</button>`;

/** D808: a path as its folders, each a link that opens it; the loop's own folder first. */
export function PathCrumbs({ name, path, open }) {
  const segs = String(path || "").split("/").filter(Boolean);
  return html`<span class="mono path-crumbs">${link(name, () => open("", true))}${segs.map((s, i) => html`<span class="muted"> / </span>${i < segs.length - 1
    ? link(s, () => open(segs.slice(0, i + 1).join("/"), true)) : html`<strong>${s}</strong>`}`)}</span>`;
}

/** A folder's size in all, asked for when it is selected (bounded on the server), not on a list. */
function FolderSize({ url }) {
  const read = useLoad(() => api(url), url), s = read.got?.d;
  return html`<span class="item-size"> · ${!read.got ? html`<span class="muted">counting…</span>`
    : read.got.error ? html`<${Act} cls="small link" onClick=${read.reload}>Calculate size<//>`
    : html`<span title=${`${s.bytes} bytes; hidden and ignored files counted, links not followed`}>
        ${`${s.partial ? "at least " : ""}${bytes(s.bytes)} in ${plural(s.files, "file")}, ${plural(s.folders, "folder")}${s.links ? `, ${plural(s.links, "link")}` : ""}`}</span>`}</span>`;
}

/** What the item is: its kind, its size (D908: KiB, MiB -- powers of 1024), when it changed. */
export const MetaLine = ({ it, sizeUrl, hidden }) => html`<div class="item-meta muted small"><span class="item-kind">${it.kind === "link" ? `link to ${it.target}` : it.kind}</span>
  ${hiddenBy(it.path, hidden) ? html` · <span class="item-hidden"><${HiddenMark} path=${it.path} by=${hiddenBy(it.path, hidden)} />hidden from AI</span>` : ""}
  ${it.kind === "file" ? html` · <span class="item-size" title=${`${it.size} bytes`}>${bytes(it.size)}</span>` : ""}
  ${sizeUrl ? html`<${FolderSize} url=${sizeUrl} />` : ""}${it.mtime ? html` · changed <${Ago} t=${it.mtime} />` : ""}</div>`;

/** Rename, Move and Delete (D908) on the selected item's head -- D937: the only place; what is not possible
    here disabled, with why. Opened from the keyboard, its first possible action takes the focus. D1159: with `hid`
    (the loop's hidden paths and how to change them), Hide from AI or Show to AI. */
export function ActionsMenu({ label, it, acts, hid }) {
  // D908: open, it closes on a click elsewhere or Escape -- D937: the keyboard's place kept, back on the menu's button
  const { open, setOpen, ref } = usePopover();
  const toggled = e => { const d = e.currentTarget; setOpen(d.open); if (d.open && document.activeElement === d.querySelector("summary")) d.querySelector(".menu-list button:not([disabled])")?.focus(); };
  const item = (text, fn, ok) => html`<button type="button" role="menuitem" disabled=${!ok} title=${ok ? null : it.why}
    onClick=${async e => { e.preventDefault(); setOpen(false); await fn(it); }}>${text}</button>`;
  return html`<details class="actions-menu" ref=${ref} open=${open} onToggle=${toggled}>
    <summary class="btn small" aria-label=${`Actions for ${label}`} title="Rename, move or delete">Actions ▾</summary>
    <div class="menu-list" role="menu">${item("Rename…", acts.rename, it.can.rename)}${item("Move…", acts.move, it.can.move)}${item("Delete…", acts.remove, it.can.delete)}
      ${hid && it.path ? hideItem(it, hid, setOpen) : ""}
      ${it.why ? html`<p class="muted small why">${`Not here: ${it.why}.`}</p>` : ""}</div></details>`;
}

/** D1159: Hide from AI, or Show to AI for a path hidden by its own name; a path a folder or pattern above hides, said. */
function hideItem(it, hid, setOpen) {
  const by = hiddenBy(it.path, hid.list), own = hiddenByName(it.path, hid.list);
  const doc = it.kind === "file" && /(^|\/)problem\.ya?ml$|\.problem\.ya?ml$/.test(it.path);
  const ok = !doc && (own || !by);
  return html`<button type="button" role="menuitem" class="hide-item" disabled=${!ok}
    title=${doc ? "the agents read the document" : by && !own ? `hidden by ${by}` : null}
    onClick=${async e => { e.preventDefault(); setOpen(false); await hid.set(it.path, !own); }}>${own ? "Show to AI" : "Hide from AI"}</button>`;
}

/** A folder's entries (D829: a loop's own parts stand out -- its documents, the folders Flux keeps; D908: a
    link says it is one, never followed here; D937: a row is its name and size, an ignored one greyed). */
export function FileList({ list, open, hidden }) {
  const own = f => !f.path.includes("/") && (f.dir ? ["out", "runs", "workbench", "library"].includes(f.path) : f.path === "problem.yaml" || f.path.endsWith(".problem.yaml"));
  return html`<ul class="files">${list.map(f => html`<li key=${f.path} class=${`${f.ignored ? "ignored" : ""}${own(f) ? " own" : ""}${f.link ? " link" : ""}`}
      title=${f.ignored ? "ignored" : null}>
    <button type="button" class="link file-link" title=${f.path} onClick=${() => open(f.path, f.dir)}><span class="ic">${f.dir ? "▸" : f.link ? "↪" : "·"}</span>${baseOf(f.path) + (f.dir ? "/" : "")}</button>
    ${hiddenBy(f.path, hidden) ? html`<${HiddenMark} path=${f.path} by=${hiddenBy(f.path, hidden)} />` : ""}
    ${f.dir || f.link ? "" : html`<small class="muted">${bytes(f.size)}</small>`}</li>`)}</ul>`;
}
