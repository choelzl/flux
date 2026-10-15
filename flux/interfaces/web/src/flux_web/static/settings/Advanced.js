// Flux web, settings (D697, D833, D936, D939, D945, D1094, D1095): a loop's advanced settings -- the sandbox or the
// host, its limits, parallel work, the hosts it may reach, raw TCP/UDP, host folders mounted, packages; only an
// admin changes them (saved as they change; the host asked first), everyone sees what they are.

import { Card, html, Notice, useState } from "../view.js";
import { confirmDialog } from "../ui.js";
import { bound, useAutosave } from "../components/Autosave.js";

const names = text => [...new Set(text.split(/[\n,]/).map(x => x.trim()).filter(Boolean))];
// D936: host folders in the sandbox, one per line: host path:path inside:ro|rw
const mountsOf = text => text.split("\n").map(x => x.trim()).filter(Boolean).map(x => {
  const [host, inside, mode] = x.split(":").map(y => (y || "").trim());
  return { host, inside: inside || "", mode: mode === "rw" ? "rw" : "ro" };
});

/** What it is, in a line. */
export function advancedSaid(e) {
  const a = e.advanced || {};
  return [a.sandbox === false ? "runs on the host, without the sandbox" : "runs in the sandbox",
    ...["memory", "cpus", "pids", "tmp_size"].filter(k => a[k] != null).map(k => `${e.advanced_said[k].split(" (")[0]}: ${a[k]}`),
    ...(a.allow && a.allow.length ? [`may reach ${a.allow.join(", ")}`] : []),
    a.raw_network ? "raw TCP/UDP allowed under an allowlist" : "HTTP(S) proxy only under an allowlist",
    ...(a.mounts || []).map(m => `${m.host} in it at ${m.inside} (${m.mode === "rw" ? "read-write" : "read-only"})`),
    ...(a.nix_packages?.length ? [`Nixpkgs packages: ${a.nix_packages.join(", ")}`] : []),
    ...(a.nixchip_packages?.length ? [`Nixchip packages: ${a.nixchip_packages.join(", ")}`] : []),
    a.parallel ? "parallel work allowed" : "one at a time"].join(" · ");
}

function AdvancedForm({ a, sandboxedServer, save }) {
  const [v, setV] = useState({ sandbox: a.sandbox !== false, memory: a.memory ?? "", cpus: a.cpus ?? "", pids: a.pids ?? "", tmp_size: a.tmp_size ?? "",
    parallel: !!a.parallel, raw_network: !!a.raw_network, allow: (a.allow || []).join("\n"), nix: (a.nix_packages || []).join("\n"), chip: (a.nixchip_packages || []).join("\n"),
    mounts: (a.mounts || []).map(m => `${m.host}:${m.inside}:${m.mode}`).join("\n") });
  const set = patch => setV(x => ({ ...x, ...patch }));
  const opt = x => String(x).trim() || null;
  const { box, mark, save: saveNow } = useAutosave(() => save({ sandbox: v.sandbox, memory: opt(v.memory), cpus: opt(v.cpus), pids: String(v.pids).trim() ? Number(v.pids) : null,
    tmp_size: opt(v.tmp_size), parallel: v.parallel, allow: v.allow.split(/[\n,]/).map(x => x.trim()).filter(Boolean), raw_network: v.raw_network,
    mounts: mountsOf(v.mounts), nix_packages: names(v.nix), nixchip_packages: names(v.chip) }));
  // turning the sandbox off asks first
  const sandbox = async e => {
    const on = e.currentTarget.checked;
    if (!on && !await confirmDialog("Run this loop on the host?", ["Outside the sandbox", "its document's commands and its agents run on this machine, as the server's user"], { ok: "Run on the host", danger: true })) {
      e.currentTarget.checked = true; return;
    }
    set({ sandbox: on });
    saveNow();
  };
  const num = (k, label, ph) => html`<label class="stack">${label}<input id=${`adv-${k}`} placeholder=${ph} class="w-120" ...${bound(v[k], x => set({ [k]: x }))} /></label>`;
  const check = (k, id, label) => html`<label class="check"><input type="checkbox" id=${id} checked=${v[k]} onChange=${e => set({ [k]: e.currentTarget.checked })} />${label}</label>`;
  const area = (k, id, label, ph) => html`<label class="stack">${label}<textarea id=${id} rows="2" class="mono" placeholder=${ph} ...${bound(v[k], x => set({ [k]: x }))} /></label>`;
  return html`<div ...${box}><p class="muted">From the next start.</p>
    <label class="check"><input type="checkbox" id="adv-sandbox" data-autosave-skip checked=${v.sandbox} onChange=${sandbox} />Run in the sandbox (off: trusted code only)</label>
    ${sandboxedServer ? "" : html`<${Notice} tone="warn" what="This server runs every loop without a sandbox" why="this choice and the settings below apply once it runs with one." />`}
    <div class="row">${num("memory", "Memory", "no limit")}${num("cpus", "CPUs", "no limit")}${num("pids", "Processes", "4096")}${num("tmp_size", "Scratch /tmp", "no limit")}</div>
    ${check("parallel", "adv-parallel", "Allow parallel work")}
    ${area("allow", "adv-allow", "Hosts this loop may reach as well, under a network allowlist (one per line)", "e.g. huggingface.co\n10.1.2.0/24")}
    ${check("raw_network", "adv-raw-network", "Allow raw TCP/UDP")}
    <p class="muted small">Under an allowlist, enable for protocols that need direct TCP/UDP. Off: HTTP(S) uses the proxy without an extra network container. Open networks keep their existing access.</p>
    ${area("mounts", "adv-mounts", "Host folders in its sandbox, for its runs, Check and agent Tests (host path:path inside:ro or rw, one per line)", "e.g. /srv/datasets:/mnt/datasets:ro\n/srv/scratch:/mnt/scratch:rw")}
    ${area("nix", "adv-nix-packages", "Nixpkgs packages (attribute names, one per line)", "e.g. jq\nripgrep")}
    ${area("chip", "adv-nixchip-packages", "Nixchip packages (attribute names, one per line)", "e.g. verilator\nsystemc")}
    <p class="muted small">Uses Flux's locked package versions. Packages are fetched or built before the next sandbox start and cached. Available to runs, Check and agents; applies while sandboxing is enabled.</p>
    <div class="form-actions">${mark}</div></div>`;
}

/** `e` the loop's settings, `save(advanced)`. */
export const Advanced = ({ e, save }) => e.can_advance
  ? html`<${Card} title="Advanced (admins)"><${AdvancedForm} a=${e.advanced || {}} sandboxedServer=${e.sandboxed_server} save=${save} /><//>`
  : html`<${Card} title="Advanced"><p class="muted">${advancedSaid(e)} (admin only).</p><//>`;
