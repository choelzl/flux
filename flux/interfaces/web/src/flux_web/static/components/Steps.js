// Flux web (D1130): a numbered track of steps -- the configurator's rail, New loop › Agent's stages. Each its number
// in a ring (✓ once done, red when something is to fix), its title, a muted line under it when it has one; a step
// not reachable yet is off. A tablist: the current step is the one selected.

import { cx, html } from "../view.js";

/** `items` [{key, title, state: "done" | "bad" | "", disabled, sum}], `at` the current step's key, `go(key)`; `label`
    names the track; `cls` its look -- "row" (a track in a line) or "rail" (down the side) -- and its own classes. */
export const Steps = ({ items, at, go, label = "Steps", cls = "row", ...rest }) => html`<div ...${rest} class=${cx("steps", cls)} role="tablist" aria-label=${label}>${
  items.map((s, i) => html`<button key=${s.key} type="button" role="tab" data-step=${s.key} class=${cx(s.key === at && "on", s.state)}
    aria-selected=${String(s.key === at)} disabled=${!!s.disabled} onClick=${() => go(s.key)}><span class="n">${s.state === "done" ? "✓" : i + 1}</span><span class="t">${s.title}</span>${
    s.sum != null ? html`<span class="sum">${s.sum}</span>` : ""}</button>`)}</div>`;
