// Flux web (D1129): one place to add files -- a drop zone with Add files and Add folder in it -- and a row for each file
// or folder added, with where it goes in the loop: the loop's folder, library/, skills/, or a folder one names. A folder
// with a problem.yaml at its top is the loop itself (its files at the top); a .zip on its own is unpacked as the loop.
// D1159: `hideable` -- each row may be marked hidden from the loop's AI agents (`hiddenRows` the paths it makes).

import { cx, html, useRef, useState } from "../view.js";
import { bytes, plural } from "../format.js";
import { droppedFiles } from "../uploads.js";
import { Tool } from "./Head.js";
import { Select } from "./Select.js";
import { Check } from "./Check.js";

const PAPER = /\.(pdf|docx?|pptx?|xlsx?|odt|odp|ods|rtf|epub|html?|tex|bib)$/i;
const PLACES = [["", "loop folder"], ["library", "library/"], ["skills", "skills/"], ["custom", "custom…"]];

/** Rows from files as picked or dropped: [{file, path}] where a path may start with its folder. */
function rowsOf(list, n0) {
  const by = new Map();
  for (const x of list) {
    const top = x.path.includes("/") ? x.path.split("/")[0] : null, key = top ? `${top}/` : x.path;
    if (!by.has(key)) by.set(key, { key: `${n0}:${key}`, label: key, folder: top, files: [] });
    by.get(key).files.push(x);
  }
  return [...by.values()].map(r => {
    const loop = r.folder && r.files.some(f => /^[^/]+\/problem\.ya?ml$/i.test(f.path));
    const zip = !r.folder && /\.zip$/i.test(r.label);
    return { ...r, loop, zip, dest: loop || zip ? "" : !r.folder && PAPER.test(r.label) && !/^readme/i.test(r.label) ? "library" : "", custom: "" };
  });
}

/** Where each file goes: [{file, path}] in the loop. */
export function placedFiles(rows) {
  return rows.flatMap(r => {
    const into = r.dest === "custom" ? r.custom.trim().replace(/^\/+|\/+$/g, "") : r.dest;
    return r.files.map(x => ({ file: x.file, path: r.loop ? x.path.split("/").slice(1).join("/") : (into ? `${into}/${x.path}` : x.path) }));
  });
}

/** D1159: the paths of the rows marked hidden from AI, as they land in the loop: a folder's own, a file's. */
export function hiddenRows(rows) {
  return rows.filter(r => r.hidden && !r.loop && !r.zip).map(r => {
    const into = r.dest === "custom" ? r.custom.trim().replace(/^\/+|\/+$/g, "") : r.dest, own = r.folder || r.label;
    return into ? `${into}/${own}` : own;
  });
}

/** `rows` what was added, `onChange(next)`; `places` false: no destination (they all go where the caller puts them);
    `hideable` a "hidden from AI" switch on each row. */
export function Uploader({ rows, onChange, places = true, hideable = false, label = "Drop files or folders" }) {
  const [over, setOver] = useState(false), depth = useRef(0), n = useRef(0), filesIn = useRef(null), dirIn = useRef(null);
  const add = list => { if (list.length) onChange([...rows, ...rowsOf(list, ++n.current)]); };
  const drop = async e => {
    e.preventDefault(); depth.current = 0; setOver(false);
    const { files, folder } = await droppedFiles(e);
    add(folder ? files.map(f => ({ ...f, path: `${folder}/${f.path}` })) : files);
  };
  const set = (r, patch) => onChange(rows.map(x => x === r ? { ...x, ...patch } : x));
  return html`<div class="uploader">
    <div class=${cx("dropzone", over && "over")} tabindex="-1"
        onDragEnter=${e => { e.preventDefault(); depth.current++; setOver(true); }} onDragOver=${e => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; }}
        onDragLeave=${() => { if (--depth.current <= 0) { depth.current = 0; setOver(false); } }} onDrop=${drop}>
      <span class="dz-ic">⇪</span><span>${label}</span>
      <button type="button" class="small" onClick=${() => filesIn.current.click()}>Add files</button>
      <button type="button" class="small" onClick=${() => dirIn.current.click()}>Add folder</button>
      <input type="file" multiple ref=${filesIn} class="up-input up-files" tabindex="-1" aria-hidden="true" onChange=${e => { add([...e.currentTarget.files].map(f => ({ file: f, path: f.name }))); e.currentTarget.value = ""; }} />
      <input type="file" webkitdirectory multiple ref=${dirIn} class="up-input up-folder" tabindex="-1" aria-hidden="true" onChange=${e => { add([...e.currentTarget.files].map(f => ({ file: f, path: f.webkitRelativePath || f.name }))); e.currentTarget.value = ""; }} /></div>
    ${rows.length ? html`<ul class="files flist up-rows">${rows.map(r => html`<li key=${r.key}>
        <span class="mono up-name">${r.label}</span><small class="muted up-size">${r.folder ? plural(r.files.length, "file") : bytes(r.files[0].file.size)}</small>
        ${r.loop ? html`<small class="muted">loop itself</small>` : r.zip && places ? html`<small class="muted">unpacked as loop</small>`
          : places ? html`<span class="up-place"><${Select} aria-label=${`Where ${r.label} goes`} value=${r.dest} options=${PLACES} onChange=${v => set(r, { dest: v })} />
            ${r.dest === "custom" ? html`<input class="narrow-in" aria-label="Folder" placeholder="folder" value=${r.custom} onInput=${e => set(r, { custom: e.currentTarget.value })} />` : ""}</span>` : ""}
        ${hideable && !r.loop && !r.zip ? html`<${Check} cls="small up-hide" checked=${r.hidden} aria-label=${`Hide ${r.label} from AI`}
          title="Agents cannot read it; the loop's checks still use it" onChange=${on => set(r, { hidden: on })}>hidden from AI<//>` : ""}
        <${Tool} icon="close" danger label=${`Remove ${r.label}`} onClick=${() => onChange(rows.filter(x => x !== r))} /></li>`)}</ul>` : ""}</div>`;
}
