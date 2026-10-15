// Flux web (D1095): what a component reads -- read when drawn and again when its key changes; the answer of another
// key is never this one's (a late answer for a tab left draws nothing).

import { Card, Skeleton, html, useEffect, useRef, useState } from "../view.js";
import { Act } from "./Act.js";

/** What `load()` answers, read when drawn and again when `key` or `tick` changes or `reload()` is called: `got` {d}
    once read, {error} when it failed, null meanwhile. Read again for the same key (a tick, a reload), the last answer
    stays until the next -- and stays when the next fails: `reload()`'s promise says the failure (Refresh's toast). */
export function useLoad(load, key, tick = 0) {
  const [got, setGot] = useState(null), [n, setN] = useState(0), last = useRef(null), asked = useRef(null);
  useEffect(() => {
    let live = true;
    const done = asked.current; asked.current = null;
    Promise.resolve().then(load).then(d => { if (!live) return; setGot(last.current = { key, d }); done?.ok(); },
      x => {
        if (!live || x.message === "log in") return;
        if (last.current?.key === key && "d" in last.current) done?.bad(x);        // what was shown stays
        else { setGot(last.current = { key, error: x.message }); done?.ok(); }
      });
    return () => { live = false; };
  }, [key, tick, n]);
  const reload = () => new Promise((ok, bad) => { asked.current = { ok, bad }; setN(k => k + 1); });
  return { got: got && got.key === key ? got : null, reload };
}

/** What was read, drawn by `children(d)`; meanwhile `wait` (skeleton lines); a failure said with Retry -- in a card
    of its own when `card`. */
export function Loaded({ read, wait = null, failed = "It could not be read", card = false, children }) {
  if (!read.got) return wait || html`<${Skeleton} lines=${6} />`;
  if (!read.got.error) return children(read.got.d);
  const said = html`<div class="empty"><p>${`${failed}: ${read.got.error}`}</p><${Act} cls="small" onClick=${read.reload}>Retry<//></div>`;
  return card ? html`<${Card}>${said}<//>` : said;
}
