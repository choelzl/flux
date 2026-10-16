// Flux web (D833, D939, D1095): a form that saves as it changes -- a moment after typing stops, or on leaving a field;
// saves never overlap. D939: no "saved" said in words -- a green check at the end of the field just saved, fading
// out; saving, a small spinner in the mark; a refusal, the server's words in red in the mark, kept. The mark
// (aria-live) says "saved" or the error to a screen reader; its `data-state` (saving, saved, error) is what a test
// waits for.

import { Glyph, html, useEffect, useLayoutEffect, useRef, useState } from "../view.js";

/** D939: the check over the field: at a text field's right end, in a text area's corner, beside a box, a number or a
    menu, on the top right corner of a field too narrow to hold it beside its text; fixed over the page, it moves
    nothing (and follows the field as the page scrolls). */
function SavedCheck({ field }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    const c = ref.current, beside = field.tagName === "SELECT" || ["checkbox", "radio", "number"].includes(field.type);
    const place = () => {
      const r = field.getBoundingClientRect(), s = 16, corner = !beside && field.tagName !== "TEXTAREA" && r.width < 120;
      c.style.left = `${beside ? r.right + 4 : corner ? r.right - s + 6 : r.right - s - 6}px`;
      c.style.top = `${field.tagName === "TEXTAREA" ? r.bottom - s - 6 : corner ? r.top - 7 : r.top + (r.height - s) / 2}px`;
    };
    place();
    addEventListener("scroll", place, true);
    return () => removeEventListener("scroll", place, true);
  }, [field]);
  return html`<span class="save-check" aria-hidden="true" ref=${ref} />`;
}

const SaveMark = ({ s }) => html`<span class=${`save-mark small${{ saving: " saving", saved: " ok", error: " bad" }[s.state] || ""}`} role="status" aria-live="polite" data-state=${s.state || null}>
  ${s.state === "saving" ? html`<${Glyph} tone="run" />` : s.state === "saved" && s.shown ? html`<span class="sr-only">saved</span>` : s.state === "error" ? s.error : ""}
  ${s.state === "saved" && s.shown && s.at?.isConnected && s.at.getBoundingClientRect().width ? html`<${SavedCheck} field=${s.at} />` : ""}</span>`;

const FIELD = "input, select, textarea";

/** `run()` saves (it reads the component's latest state); `box` the props of the element holding the fields
    (`...${box}` on it: its fields' events pass through it -- not those of a form inside it saving its own, nor a field
    marked `data-autosave-skip`, which saves itself through `save()`); `mark` where the mark is said; `save()` saves
    now. `opts`: `delay` after typing, `typing` false to save on leaving a field only, `markOf` another form's (one
    mark for both). */
export function useAutosave(run, { delay = 900, typing = true, markOf = null } = {}) {
  const [s, setS] = useState({ state: "" }), el = useRef(null), latest = useRef(run), t = useRef({});
  latest.current = run;
  const say = markOf ? markOf.say : setS;
  const go = (field = null) => {
    const k = t.current;
    clearTimeout(k.typing);
    const at = field || k.last || null;
    k.chain = (k.chain || Promise.resolve()).then(async () => {
      clearTimeout(k.fade);
      say({ state: "saving" });
      try {
        await latest.current();
        say({ state: "saved", shown: true, at });
        k.fade = setTimeout(() => say(x => x.state === "saved" ? { ...x, shown: false } : x), 1500);   // said again next time
      } catch (x) { say({ state: "error", error: x.message === "log in" ? "" : x.message }); }    // a refusal stays said
    });
    return k.chain;
  };
  useEffect(() => () => { clearTimeout(t.current.typing); clearTimeout(t.current.fade); }, []);
  const mine = e => { const f = e.target; return f.matches?.(FIELD) && !f.hasAttribute("data-autosave-skip") && f.closest("[data-autosave]") === el.current ? f : null; };
  // caught on the way down (an event that does not bubble too), saved once the field's own handler has set its state
  const box = { ref: el, "data-autosave": "",
    onInputCapture: e => { const f = typing && mine(e); if (!f) return; t.current.last = f; clearTimeout(t.current.typing); t.current.typing = setTimeout(() => go(f), delay); },
    onChangeCapture: e => { const f = mine(e); if (f) { t.current.last = f; setTimeout(() => go(f)); } } };
  return { box, mark: html`<${SaveMark} s=${s} />`, say: setS, save: () => go() };
}

/** A text field's value and its handlers: typed (input) or set and left (change) -- the state follows both, so the
    save reads what the field holds. `...${bound(v, set)}` on the field. */
export const bound = (value, set) => ({ value, onInput: e => set(e.currentTarget.value), onChange: e => set(e.currentTarget.value) });
