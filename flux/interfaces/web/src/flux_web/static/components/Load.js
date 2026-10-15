// Flux web (D1095): what a component reads -- read when drawn and again when its key changes; the answer of another
// key is never this one's (a late answer for a tab left draws nothing). And a read repeated while something runs.

import { Card, Skeleton, html, useEffect, useMemo, useRef, useState } from "../view.js";
import { Failed } from "./Tone.js";

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

/** A read again while there is reason to (something runs): `step(live)` reads (and sets what it read, while
    `live()`), and gives the milliseconds before the next read, or null for none. Run when drawn (after `first` ms;
    `first` null: only when asked) and anew when `deps` change; stopped when the component goes. Returns
    {now(): the step at once (awaitable; the next read as it says), soon(ms): the step in a moment}. */
export function usePoll(step, deps = [], { first = 0 } = {}) {
  const latest = useRef(step), run = useRef(null);
  latest.current = step;
  useEffect(() => {
    let live = true, timer = null;
    const go = async () => {
      clearTimeout(timer);
      const ms = await latest.current(() => live);
      if (live && ms != null) timer = setTimeout(go, ms);
    };
    run.current = { go, soon: ms => { if (live) { clearTimeout(timer); timer = setTimeout(go, ms); } } };
    if (first != null) run.current.soon(first);
    return () => { live = false; clearTimeout(timer); run.current = null; };
  }, deps);
  return useMemo(() => ({ now: () => run.current ? run.current.go() : Promise.resolve(), soon: (ms = 0) => run.current?.soon(ms) }), []);
}

/** What was read, drawn by `children(d)`; meanwhile `wait` (skeleton lines); a failure said with Retry -- in a card
    of its own when `card`. */
export function Loaded({ read, wait = null, failed = "It could not be read", card = false, children }) {
  if (!read.got) return wait || html`<${Skeleton} lines=${6} />`;
  if (!read.got.error) return children(read.got.d);
  const said = html`<${Failed} what=${failed} why=${read.got.error} retry=${read.reload} />`;
  return card ? html`<${Card}>${said}<//>` : said;
}

/** A page's read (a route): a card of skeleton lines meanwhile, a failure said as the page. */
export function PageRead({ load, k = "", children }) {
  const read = useLoad(load, k);
  if (!read.got) return html`<${Card}><${Skeleton} lines=${7} /><//>`;
  return read.got.error ? html`<${Card}><${Failed} what="This page could not be read" why=${read.got.error} retry=${read.reload} /><//>` : children(read.got.d);
}
