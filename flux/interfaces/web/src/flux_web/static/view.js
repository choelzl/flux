// Flux web: pages as Preact components (D1095) -- written with htm's tagged templates, no build step.
// Preact and htm are vendored (static/vendor), their imports made relative.
// Every page is a component; a component goes where a node is wanted (the configurator's crafter) through `node`.

import { html, render } from "./vendor/htm-preact.module.js";
import { options } from "./vendor/preact.module.js";
import { useEffect, useLayoutEffect, useRef, useState } from "./vendor/hooks.module.js";
import { cleanup } from "./state.js";
import { agoText, when } from "./format.js";

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
export { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "./vendor/hooks.module.js";

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

/** A card: its head when it has a title or actions, then what it holds. */
export function Card({ title, actions, cls = "", children, ...rest }) {
  return html`<section ...${rest} class=${"card " + cls}>
    ${title || actions ? html`<div class="card-head">${title ? html`<h2>${title}</h2>` : ""}${actions ? html`<div class="actions">${actions}</div>` : ""}</div>` : ""}
    ${children}</section>`;
}

// One clock for every "5 min ago" on the page: a minute's tick redraws those still on it; one that has left the
// page without being unmounted (a node replaced under it) is dropped at the tick, not kept.
const ticking = new Set();
setInterval(() => { for (const s of ticking) { if (s.ref.current?.isConnected) s.tick(); else ticking.delete(s); } }, 30000);
/** When, in words ("5 min ago", the date on hover), kept current while it is on the page. */
export function Ago({ t }) {
  const ref = useRef(null), [, setN] = useState(0);
  useEffect(() => { const s = { ref, tick: () => setN(n => n + 1) }; ticking.add(s); return () => ticking.delete(s); }, []);
  return t ? html`<time ref=${ref} title=${when(t)}>${agoText(t)}</time>` : null;
}

export const Empty = ({ text }) => html`<div class="empty"><p>${text}</p></div>`;

const WIDTHS = [92, 76, 84, 60, 70, 88, 54];
export const Skeleton = ({ lines = 5 }) => html`<div class="skeleton" aria-busy="true" aria-label="Loading">
  ${Array.from({ length: lines }, (_, i) => html`<div class="sk-line" style=${`width:${WIDTHS[i % 7]}%`}></div>`)}</div>`;

const mounted = new Map();       // host -> seen on the page once
let sweeping = 0;
/** Unmount the hosts that were on the page and have left it -- after the task that mounted, so a host taken off
    and put back in the same step (a toolbar under redrawn sub-tabs) is kept. */
function sweep() {
  sweeping = 0;
  for (const [host, seen] of mounted) {
    if (host.isConnected) mounted.set(host, true);
    else if (seen) { render(null, host); mounted.delete(host); }
  }
}
/** A component drawn into `host`, a node of a page: unmounted when the page goes (the route's cleanup), or once
    `host` has left the page (a tab drawn over it) -- one not placed yet stays. */
export function mount(host, vnode) {
  render(vnode, host);
  if (!mounted.has(host)) cleanup.push(() => { if (mounted.delete(host)) render(null, host); });
  mounted.set(host, host.isConnected || !!mounted.get(host));
  queueMicrotask(() => { if (host.isConnected && mounted.has(host)) mounted.set(host, true); });    // placed by its caller
  if (!sweeping) sweeping = setTimeout(sweep);
  return host;
}

/** A component in a node of its own, for what places nodes (the crafter): unmounted with the route. */
export function node(vnode) {
  const host = document.createElement("div");
  host.style.display = "contents";
  return mount(host, vnode);
}
