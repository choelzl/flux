// Flux web (D693, D1095): a loop's state -- running (since; a stop asked), how its last start ended, or never run
// -- and its last activity in words.

import { Ago, html } from "../view.js";
import { Pill } from "./Tone.js";

export function StatePill({ st }) {
  if (!st) return null;
  if (st.running) return html`<${Pill} tone="run">${st.restart_requested ? "restarting" : st.stop_requested ? "stopping" : "running"}<//>`;   // D1132: a restart is not a stop
  if (st.failed) return html`<${Pill} tone="bad">failed<//>`;
  if (st.stopped) return html`<${Pill} tone="warn">stopped<//>`;
  return html`<${Pill}>${st.last_active ? "idle" : "never run"}<//>`;
}

/** The pass a running loop is in, numbered as Live numbers this start's passes (the baseline 0); null when unknown --
    the head, Overview's State and Passes cards read it here, one rule. */
export const passOf = st => !st?.running ? null : st.baseline ? 0 : st.passes != null ? st.passes + (st.at_rest ? 0 : 1) : null;

export const LastSaid = ({ st }) => st.running
  ? html`running since <${Ago} t=${st.since} />${st.baseline ? " · baseline pass 0" : passOf(st) != null ? ` · pass ${passOf(st)}` : ""}`
  : st.last_active ? html`last active <${Ago} t=${st.last_active} />` : "never run";

/** D856: the page's connection to a loop, said only while it is not connected -- a green "live" beside an idle loop
    read as running; `state` "" (connecting), "live", "reconnecting" or "off" (a past start reads no stream). */
export const StreamPill = ({ state }) => state === "live" || state === "off" ? null
  : html`<${Pill} tone="run" small cls="stream" title="Connection to the loop">${state ? "reconnecting" : "connecting"}<//>`;
