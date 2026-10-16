// Flux web: a scrolled box's place -- kept across a redraw, and its end followed while one reads it (D1144: the one
// test and the one hook for following an end, wherever a text or a chat grows).

import { useLayoutEffect, useRef } from "./vendor/hooks.module.js";

/** Whether `el` stands at its end (within `slack` px). */
export const atEnd = (el, slack = 24) => el.scrollHeight - el.scrollTop - el.clientHeight < slack;
/** Whether `el` is drawn: on the page, not inside a closed fold. */
export const shown = el => el.getClientRects().length > 0 && !el.closest("details:not([open])");

// Save before replacing content: removing children can clamp both scroll offsets to zero.
export function scrollState(el) {
  return { top: el.scrollTop, left: el.scrollLeft, scrollable: el.scrollHeight > el.clientHeight + 8, atEnd: atEnd(el, 8) };
}

export function restoreScroll(el, state, { follow = false } = {}) {
  if (!state) return;
  el.scrollLeft = state.left;
  el.scrollTop = follow && state.atEnd ? el.scrollHeight : state.top;
}

/** D1144: the box `ref` follows its end -- read before each draw (while it is drawn), kept there after it; a box in a
    closed fold goes to its end once the fold opens. `on` false: it stays where it is. Returns the ref saying whether
    it stood at its end (a caller that places it itself sets it). */
export function useFollow(ref, on = true) {
  const end = useRef(true), waiting = useRef(false);
  if (ref.current && shown(ref.current)) end.current = atEnd(ref.current);       // as it stood before this draw
  useLayoutEffect(() => {
    const el = ref.current;
    if (!on || !el || !end.current) return;
    if (shown(el)) { el.scrollTop = el.scrollHeight; return; }
    const fold = el.closest("details:not([open])");
    if (!fold || waiting.current) return;
    waiting.current = true;
    fold.addEventListener("toggle", () => { waiting.current = false; if (end.current && shown(el)) el.scrollTop = el.scrollHeight; }, { once: true });
  });
  return end;
}
