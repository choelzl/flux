// Flux web (D697, D936, D1095): an admin's choice when a clone or a transfer would otherwise lose a loop's special
// permissions -- the sandbox, mounts and network overrides, listed; `keep` the box, `set(on)` changing it.

import { html } from "../view.js";
import { me } from "../state.js";

/** Whether there is a choice to make: an admin, and an override to keep. */
export const hasPermissions = (settings = {}) => me.role === "admin" && ["sandbox", "mounts", "allow", "raw_network"].some(k => k in settings);

export function PermissionChoice({ settings = {}, id = "keep-permissions", keep = false, set }) {
  if (!hasPermissions(settings)) return null;
  return html`<div class="stack permission-choice wrap-any"><label class="check"><input type="checkbox" id=${id} checked=${keep} onChange=${e => set(e.currentTarget.checked)} />Keep special permissions</label>
    <ul class="small">${"sandbox" in settings ? html`<li>${`Sandbox override: ${settings.sandbox ? "on" : "off (runs on the host)"}`}</li>` : ""}
      ${(settings.mounts || []).map(m => html`<li><code class="wrap-normal">${`${m.host} → ${m.inside}`}</code>${` (${m.mode === "rw" ? "read/write" : "read-only"})`}</li>`)}
      ${"allow" in settings ? html`<li>${`Additional network allowlist: ${(settings.allow || []).join(", ") || "none"}`}</li>` : ""}
      ${"raw_network" in settings ? html`<li>${`Raw TCP/UDP: ${settings.raw_network ? "on" : "off"}`}</li>` : ""}</ul>
    <p class="muted small">If checked, these overrides also apply to the new owner or clone. Other admin settings reset.</p></div>`;
}
