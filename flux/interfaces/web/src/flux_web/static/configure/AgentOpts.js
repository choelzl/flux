// Flux web, configure (D1142): an agent's own settings, wherever an agent works a box, judges one or draws the lessons --
// its time limit, its session (fresh each turn or one per pass; Design keeps one per part, D669), who answers its
// questions (how many, how long one waits), folded under More; what else the document gave it (bin, args, allow, ...) said, kept as written. And
// Design's probe (D1137): what of the gate and the measurements its agent may run while it designs.

import { html } from "../view.js";
import { Choice } from "../components/Tabs.js";
import { Pick, Text } from "./fields.js";

/** `slot` a box ("generate", "select", "lessons") or "critic:<box>"; nothing when no agent works it. `disabled`: the flow
    is kept as written. */
export function AgentOpts({ M, state, slot, edit, disabled = false }) {
  const o = M.agentOpts(state, slot);
  if (!o) return "";
  const set = k => v => edit(() => M.setAgentOpt(state, slot, k, v));
  const said = ["timeout_s", "session", "questions", "max_questions", "wait_s"].filter(k => o[k] !== undefined).length, rest = M.agentRest(state, slot);
  return html`<details class="cfg-more" data-agent-opts=${slot}><summary>More${said ? html` <span class="muted">· ${said} set</span>` : ""}</summary>
    <div class="cfg-grid2">
      <${Text} label="Time limit, s" value=${o.timeout_s ?? ""} placeholder="default" disabled=${disabled} set=${set("timeout_s")} />
      ${slot === "generate" ? "" : html`<${Pick} label="Session" value=${o.session || ""} disabled=${disabled} set=${set("session")}
        options=${[["", "fresh each turn"], ["pass", "one per pass"]]} />`}
      <${Pick} label="Questions" hint=${o.questions === "operator" ? "it waits for an answer in Talk" : ""} value=${o.questions || ""} disabled=${disabled}
        set=${set("questions")} options=${[["", "it decides"], ["model", "model answers"], ["operator", "ask me"]]} />
      ${o.questions && o.questions !== "decide" ? html`<${Text} label="Most questions" value=${o.max_questions ?? ""} placeholder="2" disabled=${disabled} set=${set("max_questions")} />
        ${o.questions === "operator" ? html`<${Text} label="Wait, s" value=${o.wait_s ?? ""} placeholder="300" disabled=${disabled} set=${set("wait_s")} />` : ""}` : ""}</div>
    ${Object.keys(rest).length ? html`<p class="cfg-says">Also, as written: <code>${JSON.stringify(rest)}</code></p>` : ""}</details>`;
}

/** Design's probe: on or off, the gate's checks it may run (every one by default), how often, and the measurements' runs. */
export function ProbeFields({ M, state, edit, disabled = false }) {
  const p = M.probeOf(state), names = state.checks.map(c => c.name).filter(Boolean);
  const put = ch => edit(() => M.setProbe(state, { ...p, ...ch }));
  const on = n => p.checks === null || p.checks.includes(n);
  const toggle = n => {
    const next = names.filter(x => x === n ? !on(n) : on(x));
    put({ checks: next.length === names.length ? null : next });
  };
  return html`<div class="cfg-sub" data-probe><div class="cfg-bar"><h4>Probe <span class="muted">what it may run while designing</span></h4><span class="grow" />
    <${Choice} small label="Probe" value=${p.off ? "off" : "on"} onChange=${v => { if (!disabled) put({ off: v === "off" }); }} options=${[["on", "on"], ["off", "off"]]} /></div>
    ${p.off ? "" : html`${names.length ? html`<ul class="cfg-lib-reads" aria-label="Gate checks it may run">${names.map(n => html`<li key=${n}><label>
        <input type="checkbox" data-probe-check=${n} checked=${on(n)} disabled=${disabled} onChange=${() => toggle(n)} /><code>${n}</code></label></li>`)}</ul>` : ""}
      <div class="cfg-grid2"><${Text} label="Gate runs" value=${p.runs} placeholder="20" disabled=${disabled || (p.checks && !p.checks.length)} set=${v => put({ runs: v })} />
        <${Text} label="Measure runs" value=${p.stages} placeholder="3" disabled=${disabled} set=${v => put({ stages: v })} /></div>`}</div>`;
}
