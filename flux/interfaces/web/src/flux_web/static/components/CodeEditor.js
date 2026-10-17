// Flux web (D691, D1095): an editor -- a textarea over a highlighted layer that follows it (its scroll, its
// viewport); the layer drawn from `tokens()` a moment after typing stops. The text is the textarea's own as one
// types: set again only when `text` changes. `onEditor(ed)` handed it (`ed.textarea` its text), null when it goes;
// `onType()` told each time it is typed in.

import { html, useEffect, useLayoutEffect, useMemo, useRef, useState } from "../view.js";
import { tokens } from "../highlight.js";

export function CodeEditor({ text, lang, readonly = false, placeholder = null, onEditor = null, onType = null }) {
  const ta = useRef(null), layer = useRef(null), [shown, setShown] = useState(text), t = useRef(null);
  const sync = () => {
    const a = ta.current, l = layer.current;
    if (!a || !l || !a.clientWidth || !a.clientHeight) return;
    // Match the viewport inside the textarea's scrollbars; a larger layer clamps early at the bottom.
    l.style.width = `${a.clientWidth}px`; l.style.height = `${a.clientHeight}px`;
    l.scrollTop = a.scrollTop; l.scrollLeft = a.scrollLeft;
  };
  useLayoutEffect(() => { ta.current.value = text; setShown(text); }, [text]);
  useLayoutEffect(sync, [shown]);
  useEffect(() => {
    const resize = new ResizeObserver(sync);
    resize.observe(ta.current);
    return () => { resize.disconnect(); clearTimeout(t.current); };
  }, []);
  useLayoutEffect(() => { onEditor?.({ textarea: ta.current }); return () => onEditor?.(null); }, [text, lang, readonly]);   // before a click can read it
  const typed = () => { onType?.(); clearTimeout(t.current); t.current = setTimeout(() => setShown(ta.current.value), ta.current.value.length > 60000 ? 250 : 40); };
  const key = e => {                                                   // Tab indents, as an editor does
    if (e.key !== "Tab" || e.shiftKey || readonly) return;
    e.preventDefault();
    e.currentTarget.setRangeText("  ", e.currentTarget.selectionStart, e.currentTarget.selectionEnd, "end");
    e.currentTarget.dispatchEvent(new Event("input"));
  };
  const spans = useMemo(() => tokens(shown + (shown.endsWith("\n") ? " " : ""), lang).map(([c, s]) => c ? html`<span class=${"tk-" + c}>${s}</span>` : s), [shown, lang]);
  return html`<div class="editor"><pre class="editor-layer hl" aria-hidden="true" ref=${layer}>${spans}</pre>
    <textarea class="editor-input" wrap="off" spellcheck=${false} readOnly=${readonly} placeholder=${placeholder} ref=${ta}
      onInput=${typed} onScroll=${sync} onKeyDown=${key} /></div>`;
}
