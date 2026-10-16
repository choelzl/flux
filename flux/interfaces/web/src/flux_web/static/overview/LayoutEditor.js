// Flux web, overview (D692, D1095): the Overview's layout, the account's -- 3 to 5 small cards left to right, the
// larger ones in two columns top to bottom (moved, removed, added), previewed with sample data as it changes;
// Save, Defaults, Cancel. Applies to every loop of the account, across browsers.

import { html, useLayoutEffect, useRef, useState } from "../view.js";
import { Overview } from "./Overview.js";
import { Select } from "../components/Select.js";
import { Tag } from "../components/Tone.js";
import { LoopContext } from "../loop/context.js";

const MOCK_CTX = { name: "sample", owner: "__overview_mock_data__", qs: "", mine: false, tab: "Overview", still: () => () => true, goTab: () => {}, drawBody: () => {} };

/** One list: `ids` the cards in it, `catalog` id -> label, `column` its index (null: the small cards); `edit(f)`
    changes a copy of the list, `moveOut(id)` sends one to the other column. */
function List({ ids, catalog, column, used, edit, moveOut }) {
  const small = column == null, [toAdd, setToAdd] = useState(null);
  const available = Object.entries(catalog).filter(([id]) => !used.includes(id)), adding = available.some(([id]) => id === toAdd) ? toAdd : available[0]?.[0];
  const btn = (label, title, fn, disabled = false) => html`<button type="button" class=${label === "Add" ? "small overview-layout-add-button" : "small overview-layout-control"}
    title=${title} aria-label=${title} disabled=${disabled} onClick=${fn}>${label}</button>`;
  return html`<div class="overview-layout-list" data-layout-list=${small ? "stats" : column}>
    <ol>${ids.length ? ids.map((id, i) => html`<li key=${id} class="overview-layout-row" data-layout-card=${id}>
        <span class="overview-layout-index" aria-hidden="true">${i + 1}</span>
        <${Select} title=${catalog[id]} aria-label=${`${small ? "Small" : "Large"} card ${i + 1}${small ? "" : ` in column ${column + 1}`}`} value=${id}
          onChange=${v => edit(n => { n[i] = v; })} options=${Object.entries(catalog).filter(([k]) => k === id || !used.includes(k))} />
        <div class="overview-layout-actions">
          ${btn("↑", `Move ${catalog[id]} up`, () => edit(n => { [n[i - 1], n[i]] = [n[i], n[i - 1]]; }), i === 0)}
          ${btn("↓", `Move ${catalog[id]} down`, () => edit(n => { [n[i + 1], n[i]] = [n[i], n[i + 1]]; }), i === ids.length - 1)}
          ${small ? "" : btn(column === 0 ? "→" : "←", `Move ${catalog[id]} to column ${2 - column}`, () => moveOut(id))}
          ${btn("×", `Remove ${catalog[id]}`, () => edit(n => { n.splice(i, 1); }), small && ids.length <= 3)}</div></li>`)
      : html`<li class="overview-layout-empty muted small">No cards in this column.</li>`}</ol>
    <div class="overview-layout-row overview-layout-add"><${Select} aria-label=${small ? "Add small card" : `Add card to column ${column + 1}`} value=${adding} onChange=${setToAdd} options=${available} />
      ${btn("Add", small ? "Add small card" : `Add card to column ${column + 1}`, () => edit(n => { n.push(adding); }), !available.length || (small && ids.length >= 5))}</div></div>`;
}

/** `prefs` what /preferences/overview said; `example` the sample data; `save(layout)` throws what to say. */
export function LayoutEditor({ prefs, example, save, cancel }) {
  const [draft, setDraft] = useState(() => structuredClone(prefs.layout)), [saving, setSaving] = useState(false), [error, setError] = useState("");
  const content = useRef(null), focus = useRef(null);
  // the keyboard's place kept: the control pressed (or, gone, the row's select) has the focus again
  const remember = () => {
    const el = document.activeElement;
    if (!content.current?.contains(el)) { focus.current = null; return; }
    focus.current = { card: el.matches("select") ? el.value : el.closest("[data-layout-card]")?.dataset.layoutCard, small: el.closest("[data-layout-list]")?.dataset.layoutList === "stats", label: el.getAttribute("aria-label") };
  };
  useLayoutEffect(() => {
    const f = focus.current;
    if (!f) return;
    focus.current = null;
    const row = [...content.current.querySelectorAll("[data-layout-card]")].find(el => el.dataset.layoutCard === f.card && (el.closest("[data-layout-list]").dataset.layoutList === "stats") === f.small);
    const action = [...(row || content.current).querySelectorAll("button, select")].find(el => el.getAttribute("aria-label") === f.label && !el.disabled);
    (action || row?.querySelector("select") || content.current.querySelector("select"))?.focus();
  });
  const set = next => { remember(); setDraft(next); };
  const editStats = f => { const n = [...draft.stats]; f(n); set({ ...draft, stats: n }); };
  const editColumn = c => f => set({ ...draft, columns: draft.columns.map((x, i) => { if (i !== c) return x; const n = [...x]; f(n); return n; }) });
  const moveOut = c => id => set({ ...draft, columns: draft.columns.map((x, i) => i === c ? x.filter(y => y !== id) : [...x, id]) });
  const go = async () => {
    setSaving(true); setError("");
    try { await save(draft); } catch (e) { setError(e.message); setSaving(false); }
  };
  const heading = (title, count, sub = false) => html`<div class="overview-layout-heading">${sub ? html`<h4>${title}</h4>` : html`<h3>${title}</h3>`}<span class="overview-layout-count">${count}</span></div>`;
  return html`<h2 id="overview-layout-title">Overview layout</h2><p>Applies to every loop in your account, across browsers.</p>
    <div class="overview-customizer"><div class="overview-layout-editor" ref=${content} inert=${saving}>
        <section class="overview-layout-section" aria-label="Top cards">${heading("Top cards", `${draft.stats.length} / 5`)}
          <p class="muted small">Choose 3–5 cards, ordered left to right.</p><${List} ids=${draft.stats} catalog=${prefs.small_cards} used=${draft.stats} edit=${editStats} /></section>
        <section class="overview-layout-section" aria-label="Main cards">${heading("Main cards", `${draft.columns.flat().length} selected`)}
          <p class="muted small">Order top to bottom. Use ← / → to move between columns.</p>
          <div class="overview-layout-columns">${draft.columns.map((ids, c) => html`<section class="overview-layout-column" aria-label=${`${c === 0 ? "Left" : "Right"} column`}>
            ${heading(c === 0 ? "Left column" : "Right column", ids.length, true)}<${List} ids=${ids} catalog=${prefs.large_cards} column=${c} used=${draft.columns.flat()} edit=${editColumn(c)} moveOut=${moveOut(c)} /></section>`)}</div>
          <p class="muted small overview-layout-mobile-note">On phones, the left column appears first.</p></section></div>
      <section class="overview-preview-panel" aria-label="Overview preview with mock data"><div class="overview-layout-heading"><h3>Preview</h3><${Tag}>Mock Data<//></div>
        <p class="muted small">Sample values illustrate the layout. Your loops are unchanged until you save.</p>
        <div class="overview-layout-preview" inert><${LoopContext.Provider} value=${{ ...MOCK_CTX, st: example.state }}><${Overview} r=${example.results} notes=${example.notes} bench=${example.workbench}
          use=${example.usage} prefs=${{ layout: draft }} ideas=${example.ideas} /><//></div></section></div>
    <p class="err" role="alert">${error}</p>
    <div class="dlg-actions"><button type="button" disabled=${saving} onClick=${() => { remember(); setDraft(structuredClone(prefs.default)); }}>Defaults</button>
      <button type="button" disabled=${saving} onClick=${cancel}>Cancel</button><button type="button" class="primary" disabled=${saving} onClick=${go}>Save</button></div>`;
}
