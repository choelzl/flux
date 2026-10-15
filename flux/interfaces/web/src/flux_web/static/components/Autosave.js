// Flux web (D833, D939, D1095): a form that saves as it changes -- ui.js `autosave` over the fields a component
// draws: a moment after typing stops, or on leaving a field; the mark (a spinner, a check over the field, the
// server's refusal kept) where the component puts it.

import { Dom, html, useEffect, useMemo, useRef } from "../view.js";
import { autosave, saveMark } from "../ui.js";

/** `run()` saves (it reads the component's latest state); `box` goes on the element holding the fields,
    `mark` where the mark is said (`opts.mark`: another's `markNode`, one mark for both), `save()` saves now (a field marked `data-autosave-skip` saves itself so, once asked).
    The fields are taken once, when it is first drawn -- not those of a form inside it. */
export function useAutosave(run, opts = {}) {
  const mark = useMemo(() => opts.mark || saveMark(), []), box = useRef(null), latest = useRef(run), go = useRef(null);
  latest.current = run;
  useEffect(() => {
    const el = box.current;
    el.setAttribute("data-autosave", "");             // a form inside it saves its own fields (its effect ran first)
    const fields = [...el.querySelectorAll("input, select, textarea")].filter(f => f.closest("[data-autosave]") === el && !f.hasAttribute("data-autosave-skip"));
    go.current = autosave(fields, () => latest.current(), mark, opts);
  }, []);
  return { box, mark: html`<${Dom} node=${mark} />`, markNode: mark, save: () => go.current?.() };
}

/** A text field's value and its handlers: typed (input) or set and left (change) -- the state follows both, so
    the save reads what the field holds. `...${bound(v, set)}` on the field. */
export const bound = (value, set) => ({ value, onInput: e => set(e.currentTarget.value), onChange: e => set(e.currentTarget.value) });
