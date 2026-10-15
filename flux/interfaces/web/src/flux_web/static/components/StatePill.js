// Flux web (D693, D1095): a loop's state -- running (since; a stop asked), how its last start ended, or never run
// -- and its last activity in words.

import { Ago, html } from "../view.js";

export function StatePill({ st }) {
  if (!st) return null;
  if (st.running) return html`<span class="pill live"><i class="dot" />${st.stop_requested ? "stopping" : "running"}</span>`;
  if (st.failed) return html`<span class="pill bad">failed</span>`;
  if (st.stopped) return html`<span class="pill warn">stopped</span>`;
  return html`<span class="pill">${st.last_active ? "idle" : "never run"}</span>`;
}

export const LastSaid = ({ st }) => st.running
  ? html`running since <${Ago} t=${st.since} />${st.baseline ? " · baseline pass 0" : st.passes != null ? ` · pass ${st.passes + (st.at_rest ? 0 : 1)}` : ""}`
  : st.last_active ? html`last active <${Ago} t=${st.last_active} />` : "never run";
