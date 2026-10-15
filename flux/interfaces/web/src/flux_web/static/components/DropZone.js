// Flux web (D693, D1095): a drop target for files and folders -- a folder keeps its paths ("rtl/top.sv");
// `onFiles(files, folder)`, `folder` the name of a single folder dropped.

import { cx, html, useRef, useState } from "../view.js";
import { droppedFiles } from "../uploads.js";

export function DropZone({ label, onFiles }) {
  const [over, setOver] = useState(false), depth = useRef(0);
  const drop = async e => {
    e.preventDefault(); depth.current = 0; setOver(false);
    const { files, folder } = await droppedFiles(e);
    if (files.length) onFiles(files, folder);
  };
  return html`<div class=${cx("dropzone", over && "over")} tabindex="-1"
      onDragEnter=${e => { e.preventDefault(); depth.current++; setOver(true); }} onDragOver=${e => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; }}
      onDragLeave=${() => { if (--depth.current <= 0) { depth.current = 0; setOver(false); } }} onDrop=${drop}>
    <span class="dz-ic">⇪</span><span>${label}</span></div>`;
}
