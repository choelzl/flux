// Flux web (D1095): Raw and Fullscreen for a view drawn by a component -- viewer.js's buttons, `box` the ref of
// what goes fullscreen (moved there as it is, kept live), the raw text or address beside it.

import { Dom, html, useMemo } from "../view.js";
import { viewerTools } from "../viewer.js";

export function ViewerTools({ box, title, rawUrl, rawText }) {
  const tools = useMemo(() => viewerTools(() => box.current, { title, rawUrl, rawText }), [title, rawUrl, rawText]);
  return html`<${Dom} node=${tools} />`;
}
