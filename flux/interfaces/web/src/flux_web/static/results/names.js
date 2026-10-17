// Flux web, results: a design's name as a loop's pages say it -- "#3", "#1.26", "decoder#2" (the loop's own
// prefix taken off, the whole's "whole") -- and drawn cut in the middle when it does not fit, so the end that
// tells two designs apart ("=0", "chunk=4") stays in sight, the whole name on hover.

import { cx, html } from "../view.js";

/** "adder8#3" in adder8's pages: "#3"; the loop itself: "whole"; anything else as it is. */
export const shortName = (loop, x) => String(x) === loop ? "whole" : String(x).startsWith(`${loop}#`) ? String(x).slice(loop.length) : String(x);

/** The end kept when a name is cut: from its last "-", "_", "." or "#" (a short last word), else its last 8 letters. */
export function nameTail(text) {
  const s = String(text);
  if (s.length <= 12) return "";
  const at = Math.max(s.lastIndexOf("-"), s.lastIndexOf("_"), s.lastIndexOf("."), s.lastIndexOf("#"));
  return at > 0 && s.length - at <= 10 ? s.slice(at) : s.slice(-8);
}

/** `text` in one line, cut in the middle (CSS: .mid-name) when its box is narrower; `title` on hover. */
export function MidName({ text, title = null, cls = "" }) {
  const s = String(text), tail = nameTail(s);
  return html`<span class=${cx("mid-name", cls)} title=${title || s}><span class="mid-h">${tail ? s.slice(0, s.length - tail.length) : s}</span>${tail ? html`<span class="mid-t">${tail}</span>` : ""}</span>`;
}
