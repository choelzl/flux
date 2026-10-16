// The loop crafter's page (guide/loop-crafter.md, D1166): the web app's own configurator -- its six steps, the Graph's
// circle and Knowledge, the document with Copy and Download -- with no server behind it. Its modules are the app's
// (flux_web/static), published at assets/app/ by the site's hook (website/hooks.py); here they get a host
// (configure/host.js): the model and the tool catalog beside this script, the agents a fixed list, no shared library.
// Files typed or dropped stay in this page; Save downloads problem.yaml. It is drawn in a shadow root, so the app's
// stylesheet (flux.css, its :root and body read as the root's host) and Material's never meet; the docs' light or dark
// scheme is the app's data-theme.

import { Notice, Skeleton, html, render, useEffect, useMemo, useRef, useState } from "./app/view.js";
import { confirmDialog } from "./app/ui.js";
import { Toasts } from "./app/notices.js";
import { Dialogs } from "./app/components/Modal.js";
import { recallJson, remember } from "./app/stored.js";
import { host } from "./app/configure/host.js";
import { useModel } from "./app/configure/model.js";
import { filesStore } from "./app/configure/files.js";
import { download } from "./app/configure/DocView.js";
import { Configurator } from "./app/configure/Configurator.js";

const HERE = new URL(".", import.meta.url).href.replace(/\/$/, "");
const DRAFT = "flux-docs-crafter-draft";
/** the agents a box may name: the built-in coding agents (and Flux's own model, which is not one) */
const AGENTS = [{ id: "claude", label: "Claude Code" }, { id: "codex", label: "Codex" }, { id: "opencode", label: "OpenCode" }, { id: "model", label: "Model" }];
/** Flux's own skill, every loop agent's (D1152); no shared library without a server */
const USES = { papers: null, loop_skills: [{ name: "flux-loop-commands", description: "What a loop's workers can read and run, and how to read a loop from outside." }] };

Object.assign(host, { assets: HERE, agents: async () => AGENTS, uses: async () => USES });

const stepOfHash = () => (/(?:^#|[?&])step=(\w+)/.exec(location.hash) || [])[1] || "";
const used = s => !!(s && (String(s.id || "").trim() || String(s.statement || "").trim() || (s.checks || []).length || (s.stages || []).length));

/** The configurator over a draft: kept in this browser by Save draft, its files in this page. */
function Crafter({ M, draft, restart }) {
  const staged = useRef(new Map()).current;
  const state = useMemo(() => draft?.state || M.base(), [draft]);
  const store = useMemo(() => filesStore(null, () => M.buildYaml(state), { staged, namedOf: () => M.namedFiles(state) }), [state]);
  useEffect(() => { store.draw(); }, [store]);
  const save = async yaml => {
    download(yaml, "problem.yaml");
    return "Downloaded problem.yaml: put it in your loop's folder with the files it names, then run flux task check FOLDER.";
  };
  const saveDraft = (s, step) => {
    remember(DRAFT, { state: s, step });
    return staged.size ? `${M.WORDS.save.draftSaved} The files added stay with this page only.` : M.WORDS.save.draftSaved;
  };
  return html`<div class="draft-line muted small">${`Draft: ${String(state.id || "").trim() || "unnamed"} · `}<button type="button" class="link" onClick=${restart}>Start over</button></div>
    <${Configurator} M=${M} state=${state} step=${stepOfHash() || draft?.step || "prompt"} touched=${used(draft?.state)} calm isNew
      onStep=${id => history.replaceState(null, "", `#step=${id}`)} onChange=${() => store.watch()}
      save=${save} saveDraft=${saveDraft} saveLabel="Download problem.yaml" store=${store}
      checked=${() => ["note", "run flux task check FOLDER on your loop's folder"]} nameLabel="Loop name" namePlaceholder="my_loop" />`;
}

function Page() {
  const M = useModel(), [draft, setDraft] = useState(() => recallJson(DRAFT)), [gen, setGen] = useState(0);
  const restart = async () => {
    if (!await confirmDialog("Start over?", ["The form is cleared:", "name, statement, checks, measurements, the graph's choices and the files added"], { ok: "Start over", danger: true })) return;
    remember(DRAFT, null); setDraft(null); setGen(g => g + 1);
  };
  const body = M === null ? html`<${Notice} tone="bad" what="The crafter did not load." why="Reload the page." />`
    : !M ? html`<div class="card"><${Skeleton} lines=${6} /></div>` : html`<${Crafter} key=${gen} M=${M} draft=${draft} restart=${restart} />`;
  return html`${body}<${Toasts} /><${Dialogs} />`;
}

/** flux.css read inside a shadow root: its :root and body are the root's host, a rem the app's 16px (Material's is larger). */
const forShadow = css => css.replace(/(\d*\.?\d+)rem\b/g, (_, n) => `${+n * 16}px`).replace(/:root\[([^\]]+)\]/g, ":host([$1])").replace(/:root\b/g, ":host").replace(/(^|[},]\s*)body(\s*[{,])/g, "$1:host$2");

/** The docs' scheme (Material's, on the body) as the app's theme, followed as it changes. */
function followTheme(el) {
  const set = () => { el.dataset.theme = document.body.getAttribute("data-md-color-scheme") === "slate" ? "dark" : "light"; };
  set();
  new MutationObserver(set).observe(document.body, { attributes: true, attributeFilter: ["data-md-color-scheme"] });
}

async function mount() {
  const el = document.getElementById("flux-crafter");
  if (!el || el.shadowRoot) return;
  followTheme(el);
  const root = el.attachShadow({ mode: "open" });
  const [app, page] = await Promise.all([`${HERE}/app/flux.css`, `${HERE}/crafter.css`].map(u => fetch(u).then(r => r.ok ? r.text() : "", () => "")));
  root.innerHTML = "";
  const style = document.createElement("style");
  style.textContent = forShadow(app) + "\n" + page;
  const at = document.createElement("div");
  at.className = "crafter-root";
  root.append(style, at);
  render(html`<${Page} />`, at);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
else mount();
// Material's instant navigation (when on) swaps pages without a reload
if (window.document$?.subscribe) window.document$.subscribe(mount);
