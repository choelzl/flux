// Flux web, admin (D698, D744, D833, D850, D939, D1095): what every sandbox gets -- the network (open, or only the
// hosts allowed), PATH directories, what every home starts with -- and the stderr lines the pages leave out;
// saved as they change, applied from each loop's next start.

import { Card, html, Notice, useEffect, useState } from "../view.js";
import { api } from "../ui.js";
import { bound, useAutosave } from "../components/Autosave.js";
import { Select } from "../components/Select.js";
import { Check } from "../components/Check.js";
import { useLoad } from "../components/Load.js";
import { Failed } from "../components/Tone.js";

const lines = a => (a || []).join("\n");
const list = text => text.split(/[\n,]/).map(x => x.trim()).filter(Boolean);

/** D850: the stderr lines the pages leave out -- here since D896, beside what every sandbox gets. */
function Masks() {
  const [text, setText] = useState(""), read = useLoad(() => api("/admin/masks"), "masks");
  useEffect(() => { if (read.got?.d) setText((read.got.d.masks || []).join("\n")); }, [read.got]);
  const { box, mark } = useAutosave(async () => {
    const got = await api("/admin/masks", { method: "PUT", body: { masks: text.split("\n") } });
    if (document.activeElement?.id !== "stderr-masks") setText(got.masks.join("\n"));
  }, { delay: 1200 });
  return html`<${Card} title="Hidden output"><div ...${box}><p class="muted">Stderr lines to hide, one per line: text or /regex/. The record keeps them.</p>
    ${read.got?.error ? html`<${Failed} what="Hidden lines could not be read" why=${read.got.error} retry=${read.reload} />` : ""}
    <textarea id="stderr-masks" rows="4" class="mono" placeholder=${"failed to clean up stale arg0 temp dirs\n/^WARN .*deprecated/"} ...${bound(text, setText)} />
    <div class="row">${mark}</div></div><//>`;
}

export function Sandbox({ r }) {
  const c = r.config;
  const [v, setV] = useState({ network: c.network === "allowlist" ? "allowlist" : "open", allow: lines(c.allow), endpoints: c.endpoints !== false,
    path: lines(c.path), login_path: !!c.login_path, home_seed: lines(c.home_seed) });
  const set = patch => setV(x => ({ ...x, ...patch }));
  // D833: saved as they change
  const { box, mark } = useAutosave(() => api("/admin/sandbox", { method: "PUT", body: { network: v.network, allow: list(v.allow), endpoints: v.endpoints,
    path: list(v.path), login_path: v.login_path, home_seed: list(v.home_seed) } }));
  const text = (id, k, rows, ph) => html`<textarea id=${id} rows=${rows} placeholder=${ph} class="mono" ...${bound(v[k], x => set({ [k]: x }))} />`;
  const adds = r.login_path.filter(d => !r.path.includes(d));
  return html`<div ...${box}>${r.sandboxed ? "" : html`<${Notice} tone="warn" what="This server runs without the sandbox" why="(--no-sandbox): none of this applies." />`}
    <${Card} title="Network"><p class="muted">Open: any host. Allowlist: the hosts below. A loop's Settings may add hosts.</p>
      <label class="stack">Mode<${Select} id="sb-net" value=${v.network === "allowlist" ? "allowlist" : "open"} onChange=${network => set({ network })}
        options=${[["open", "open"], ["allowlist", "allowlist"]]} /></label>
      <div class="sb-allow" hidden=${v.network !== "allowlist"}><label class="stack">Allowed, one per line: host (with subdomains), *.domain, IP or CIDR
          ${text("sb-allow", "allow", 5, "localai.example.org\n*.anthropic.com\n10.0.0.0/8\n192.168.1.20")}</label>
        <${Check} id="sb-ep" checked=${v.endpoints} onChange=${endpoints => set({ endpoints })}>also the model endpoints' hosts<//></div><//>
    <${Card} title="PATH"><p class="muted">Mounted read-only.</p>
      <details><summary class="muted">${`Server PATH: ${r.path.length} directories`}</summary><pre class="val small">${r.path.join("\n")}</pre></details>
      <${Check} id="sb-login" checked=${v.login_path} onChange=${login_path => set({ login_path })}>${`add ${r.home}'s login PATH`}${adds.length ? `: ${adds.join(", ")}` : " (adds nothing)"}<//>
      <label class="stack">plus these, first${text("sb-path", "path", 3, "/opt/tools/bin")}</label><//>
    <${Masks} />
    <${Card} title="Homes"><p class="muted">Each user has their own HOME, writable and kept: agent settings, logins and sessions. Logins are never shared.</p>
      <label class="stack">${`Homes start with (paths inside ${r.home}, copied only where missing)`}${text("sb-seed", "home_seed", 3, ".config/opencode\n.gitconfig\n.npmrc")}</label><//>
    <div class="form-actions"><span class="muted small">Applies from each loop's next start.</span>${mark}</div></div>`;
}
