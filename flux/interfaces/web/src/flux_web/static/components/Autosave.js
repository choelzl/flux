// Flux web (D833, D939, D1095): a form that saves as it changes -- a moment after typing stops, or on leaving a field;
// saves never overlap. D939: no "saved" said in words -- a green check at the end of the field just saved, fading
// out; saving, a small spinner in the mark; a refusal, the server's words in red in the mark, kept. The mark
// (aria-live) says "saved" or the error to a screen reader; its `data-state` (saving, saved, error) is what a test
// waits for.

import { html, useEffect, useRef, useState } from "../view.js";

/** D939: the check over the field: at a text field's right end, in a text area's corner, beside a box, a number or a
    menu, on the top right corner of a field too narrow to hold it beside its text; fixed over the page (a dialog's
    own layer when in one), it moves nothing. */
function savedCheck(el) {
  if (!el || !el.isConnected || !el.getBoundingClientRect().width) return;
  const host = el.closest("dialog[open]") || document.body;
  const beside = el.tagName === "SELECT" || ["checkbox", "radio", "number"].includes(el.type);
  const c = Object.assign(document.createElement("span"), { className: "save-check" });
  c.setAttribute("aria-hidden", "true");
  const place = () => {
    const r = el.getBoundingClientRect(), s = 16, corner = !beside && el.tagName !== "TEXTAREA" && r.width < 120;
    c.style.left = `${beside ? r.right + 4 : corner ? r.right - s + 6 : r.right - s - 6}px`;
    c.style.top = `${el.tagName === "TEXTAREA" ? r.bottom - s - 6 : corner ? r.top - 7 : r.top + (r.height - s) / 2}px`;
  };
  place(); host.append(c);
  addEventListener("scroll", place, true);
  setTimeout(() => { removeEventListener("scroll", place, true); c.remove(); }, 1600);
}

const SaveMark = ({ s }) => html`<span class=${`save-mark small${{ saving: " saving", saved: " ok", error: " bad" }[s.state] || ""}`} role="status" aria-live="polite" data-state=${s.state || null}>
  ${s.state === "saving" ? html`<span class="spin" aria-hidden="true" />` : s.state === "saved" && s.shown ? html`<span class="sr-only">saved</span>` : s.state === "error" ? s.error : ""}</span>`;

/** `run()` saves (it reads the component's latest state); `box` goes on the element holding the fields, `mark` where
    the mark is said, `save()` saves now (a field marked `data-autosave-skip` saves itself so, once asked). The fields
    are taken once, when it is first drawn -- not those of a form inside it. `opts`: `delay` after typing, `typing`
    false to save on leaving a field only, `markOf` another form's (one mark for both). */
export function useAutosave(run, { delay = 900, typing = true, markOf = null } = {}) {
  const [s, setS] = useState({ state: "" }), box = useRef(null), latest = useRef(run), go = useRef(null);
  latest.current = run;
  const say = markOf ? markOf.say : setS;
  useEffect(() => {
    const el = box.current, fields = [];
    el.setAttribute("data-autosave", "");             // a form inside it saves its own fields (its effect ran first)
    for (const f of el.querySelectorAll("input, select, textarea")) if (f.closest("[data-autosave]") === el && !f.hasAttribute("data-autosave-skip")) fields.push(f);
    let t = null, chain = Promise.resolve(), last = null, fade = null;
    go.current = field => {
      clearTimeout(t);
      const at = field instanceof Element ? field : last || fields.find(f => f === document.activeElement) || null;
      chain = chain.then(async () => {
        clearTimeout(fade);
        say({ state: "saving" });
        try {
          await latest.current();
          say({ state: "saved", shown: true });
          savedCheck(at);
          fade = setTimeout(() => say(x => x.state === "saved" ? { ...x, shown: false } : x), 1500);   // said again next time
        } catch (x) { say({ state: "error", error: x.message === "log in" ? "" : x.message }); }    // a refusal stays said
      });
      return chain;
    };
    const off = [];
    for (const f of fields) {
      const input = () => { last = f; clearTimeout(t); t = setTimeout(() => go.current(f), delay); }, change = () => { last = f; go.current(f); };
      if (typing) f.addEventListener("input", input);
      f.addEventListener("change", change);
      off.push(() => { f.removeEventListener("input", input); f.removeEventListener("change", change); });
    }
    return () => { clearTimeout(t); clearTimeout(fade); for (const f of off) f(); };
  }, []);
  return { box, mark: html`<${SaveMark} s=${s} />`, say: setS, save: () => go.current?.() };
}

/** A text field's value and its handlers: typed (input) or set and left (change) -- the state follows both, so the
    save reads what the field holds. `...${bound(v, set)}` on the field. */
export const bound = (value, set) => ({ value, onInput: e => set(e.currentTarget.value), onChange: e => set(e.currentTarget.value) });
