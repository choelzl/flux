// Flux web (D1095): highlight.js's editor in a component -- made once per text, language and mode (it keeps
// its own state as one types), `onEditor(ed)` handed it (`ed.textarea` its text), null when it goes.

import { Dom, html, useEffect, useMemo } from "../view.js";
import { codeEditor } from "../highlight.js";

export function CodeEditor({ text, lang, readonly = false, onEditor = null }) {
  const ed = useMemo(() => codeEditor(text, lang, { readonly }), [text, lang, readonly]);
  useEffect(() => { onEditor?.(ed); return () => onEditor?.(null); }, [ed]);
  return html`<${Dom} node=${ed.el} />`;
}
