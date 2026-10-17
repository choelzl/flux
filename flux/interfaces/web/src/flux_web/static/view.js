// Flux web: pages as Preact components (D1095) -- written with htm's tagged templates, no build step.
// Preact and htm are vendored (static/vendor), their imports made relative.

import { html, render } from "./vendor/htm-preact.module.js";
import { options } from "./vendor/preact.module.js";
import { useEffect, useLayoutEffect, useRef, useState } from "./vendor/hooks.module.js";
import { ageText, agoText, when } from "./format.js";
import { Head } from "./components/Head.js";

export { html, render };

// A state changed is drawn at once: what a click changed is there when
// the click's handler returns (a keyboard's next key, a test's next look) -- not a microtask later. Not while
// Preact runs the effects of a draw: an effect that sets a state would draw again inside that run, and the draw
// runs the same effects a second time (a file read twice) -- then a microtask later.
let effects = 0;
function afterNextFrame(run) {                // as preact/hooks schedules them: the next frame, or 35 ms
  let frame = 0;
  const go = () => { clearTimeout(timer); cancelAnimationFrame(frame); setTimeout(run); };
  const timer = setTimeout(go, 35);
  frame = requestAnimationFrame(go);
}
options.requestAnimationFrame = flush => afterNextFrame(() => { effects++; try { flush(); } finally { effects--; } });
options.debounceRendering = draw => effects ? Promise.resolve().then(draw) : draw();
export { useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "./vendor/hooks.module.js";
export { createContext } from "./vendor/preact.module.js";

/** A class list from its parts: strings kept, false, null and "" left out -- `cx("chip", on && "on")` (not `cls`:
    the name of many components' class prop). */
export const cx = (...parts) => parts.filter(Boolean).join(" ");

/** Something that changes and says so (a store's list, the notices, the bell): `subscribe(f)` (returns its own
    undoing), `emit()` telling each. */
export function changes() {
  const subs = new Set();
  return { subscribe: f => { subs.add(f); return () => subs.delete(f); }, emit: () => { for (const f of [...subs]) f(); } };
}
/** The component drawn again each time `source` (anything with `subscribe`) says it changed. */
export function useChanges(source) {
  const [, redraw] = useState(0);
  useLayoutEffect(() => source.subscribe(() => redraw(n => n + 1)), [source]);
}

/** A card: its head (D1106: the one `Head` -- the title, `meta` muted after it, `actions`, `tools`) when it has one,
    then what it holds. */
export function Card({ title, meta, actions, tools, cls = "", children, ...rest }) {
  return html`<section ...${rest} class=${"card " + cls}>
    ${title || actions || tools ? html`<${Head} title=${title} actions=${actions} tools=${tools}>${meta ? html`<span class="muted">${meta}</span>` : ""}<//>` : ""}
    ${children}</section>`;
}

// One clock for every "5 min ago" on the page: a minute's tick redraws those still on it; one that has left the
// page without being unmounted (a node replaced under it) is dropped at the tick, not kept.
const ticking = new Set();
setInterval(() => { for (const s of ticking) { if (s.ref.current?.isConnected) s.tick(); else ticking.delete(s); } }, 30000);
/** When, in words ("5 min ago", the date on hover), kept current while it is on the page; `short` the age alone
    ("5 min", format.js ageText), its date said by the cell it sits in. */
export function Ago({ t, short = false }) {
  const ref = useRef(null), [, setN] = useState(0);
  useEffect(() => { const s = { ref, tick: () => setN(n => n + 1) }; ticking.add(s); return () => ticking.delete(s); }, []);
  return !t ? null : short ? html`<time ref=${ref}>${ageText(t)}</time>` : html`<time ref=${ref} title=${when(t)}>${agoText(t)}</time>`;
}

/** D1102: a tone's glyph -- the turning ring for run, ✓ ! ✗ ○ for ok, warn, bad, none (drawn by the stylesheet, so
    a state's text is its words alone); `label` when the glyph is all there is to say it. D1110: the ring is the
    small spinner, for a line, a field or a button; the other is the Live graph's dashed ring. */
export const Glyph = ({ tone = "none", label }) => label
  ? html`<i class=${tone === "run" ? "g spin" : `g g-${tone}`} role="img" aria-label=${label} title=${label} />`
  : html`<i class=${tone === "run" ? "g spin" : `g g-${tone}`} aria-hidden="true" />`;

/** D1102: a state in a block -- reading, failed, out of date, empty, a dialog's consequence: its glyph, what happened
    (in its tone), why (muted, after a colon), then `children`, and its one action at the end; `quiet` (dashed) for nothing yet. */
export const Notice = ({ tone = "none", what, why, action, quiet = false, cls = "", children, ...rest }) => html`<div ...${rest}
  class=${cx("notice", `t-${tone}`, quiet && "quiet", cls)}><${Glyph} tone=${tone} /><div class="notice-text">${what ? html`<b>${what}</b>` : ""}${
  why ? html`<span class="why">${what ? `${joint(what)}${why}` : why}</span>` : ""}${children}</div>${action || ""}</div>`;
/** Between a notice's title and its why: a colon, or a space after a title its own stop ends (".", "?", "…"). */
const joint = what => typeof what === "string" && /[.:;?!…]$/.test(what.trim()) ? " " : ": ";

/** Nothing yet: a quiet notice, its one action (a start, a link) at the end. */
export const Empty = ({ text, action, children }) => html`<${Notice} quiet cls="empty" action=${action}>${text}${children}<//>`;

const WIDTHS = [92, 76, 84, 60, 70, 88, 54];
export const Skeleton = ({ lines = 5 }) => html`<div class="skeleton" aria-busy="true" aria-label="Loading">
  ${Array.from({ length: lines }, (_, i) => html`<div class="sk-line" style=${`width:${WIDTHS[i % 7]}%`}></div>`)}</div>`;
