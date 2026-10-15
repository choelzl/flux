// Flux web, results (D690, D1095): the designs as a table -- filtered by their standing, sorted by a header
// (D929: a real button that says its order), grown by pages (D694), two ticked to compare; a row opens its
// design. The decision (★) and the newest first, unless a column is chosen.

import { cx, html, useLayoutEffect, useRef, useState } from "../view.js";
import { dictionaryClasses, measurementLabels, measurementText } from "./preferences.js";
import { GroupRow, MeasurementHeader, VerdictBadge } from "./columns.js";

const PAGE = 200;
export const keyOf = d => JSON.stringify([d.part || "", d.base || d.name, d.key || ""]);

/** `t`: what the tab knows -- r (the results), names (design labels), unit, fmt, comparison, columns (useColumns),
    values (useValuesMode); `selected` a design's key; `onOpen(d)`; `onCompare([a, b])`. */
export function ResultsTable({ t, selected, onOpen, onCompare }) {
  const { r, names, unit, fmt, comparison, columns, values } = t;
  const [filter, setFilter] = useState("all"), [pageN, setPage] = useState(PAGE);
  const [sort, setSort] = useState({ key: null, dir: 1 }), [picked, setPicked] = useState([]);
  const focus = useRef(null), box = useRef(null);
  const metrics = columns.visible(), labels = measurementLabels(metrics, r.metric_groups || {}), dict = dictionaryClasses(metrics, r.metric_groups || {});
  const sortKey = r.metrics.includes(sort.key) && !metrics.includes(sort.key) ? null : sort.key;   // a column hidden: its sort goes
  const valueOf = (d, key) => key === "name" ? names.get(d) : key === "verdict" ? d.verdict : key === "stage" ? (r.stages || []).indexOf(d.shown)
    : key === "when" ? Date.parse(d.last || "") || 0 : values.relative ? comparison(d, key).percent : d.numbers[key];
  const rows = r.designs.filter(d => filter === "all" || d.verdict === filter);
  const all = !sortKey ? rows : rows.slice().sort((a, b) => {
    const x = valueOf(a, sortKey), y = valueOf(b, sortKey);
    if (x == null && y == null) return 0;
    if (x == null) return 1;                            // missing values last, either way
    if (y == null) return -1;
    return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * sort.dir;
  });
  const shown = all.slice(0, pageN);
  useLayoutEffect(() => { if (focus.current) { box.current?.querySelector(`button.th-sort[data-key="${CSS.escape(focus.current)}"]`)?.focus(); focus.current = null; } });
  const by = key => { focus.current = key; setSort(s => s.key === key ? { key, dir: -s.dir } : { key, dir: ["name", "verdict", "stage"].includes(key) ? 1 : -1 }); };
  const sortButton = (k, label) => html`<button type="button" class=${cx("th-sort", sortKey === k && "on")} data-key=${k} aria-label=${label} onClick=${() => by(k)}>
    ${labels.get(k) || label}${k === "name" || k === "verdict" ? html`<span class="th-arrow" aria-hidden="true">${sortKey === k ? (sort.dir > 0 ? "▴" : "▾") : ""}</span>` : ""}</button>`;
  const th = (k, label, cls = "", title = null, extra = null) => html`<th class=${`sortable ${cls}${sortKey === k ? " sorted" : ""}`} aria-sort=${sortKey === k ? (sort.dir > 0 ? "ascending" : "descending") : "none"} data-label=${label} title=${title}>
    ${cls.includes("measurement-head") ? html`<${MeasurementHeader} text=${labels.get(k)} button=${sortButton(k, label)}
        arrow=${html`<span class="th-arrow measurement-sort-arrow" aria-hidden="true" onClick=${() => by(k)}>${sortKey === k ? (sort.dir > 0 ? "▴" : "▾") : ""}</span>`}>${extra}<//>`
      : html`${sortButton(k, label)}${extra}`}</th>`;
  // D694: two ticked to compare -- a third ticked lets the oldest go
  const tick = (d, on) => setPicked(p => on ? [...p.filter(x => keyOf(x) !== keyOf(d)), d].slice(-2) : p.filter(x => keyOf(x) !== keyOf(d)));
  const chip = (key, label) => html`<button class=${cx("chip", filter === key && "on")} onClick=${() => { setFilter(key); setPage(PAGE); }}>${label}</button>`;
  const limitOf = m => (r.limits || []).find(l => l.metric === m);
  const cell = (d, m) => {
    const ok = d.meets[m], hid = columns.hidden(m) ? " hidden-measurement" : "", gone = (d.cleared || []).find(c => c.stage === d.shown && c.metric === m);   // D1043: struck through
    if (gone) return html`<td class=${`mono num cleared${hid}${dict.get(m) || ""}`} title=${`${m} ${fmt(gone.value)}: cleared -- ${d.rejected?.why || "rejected"}`}><s>${fmt(gone.value)}</s></td>`;
    const display = measurementText(d, m, comparison, fmt, values.relative);
    return html`<td class=${`mono num${hid}${dict.get(m) || ""}${ok === true ? " meets" : ok === false ? " misses" : ""}`}
      title=${`${display.title}${unit[m] ? " · " + unit[m] : ""}${ok === true ? " · meets the limit" : ok === false ? " · misses the limit" : ""}`}>${display.text}</td>`;
  };
  return html`<div class="chips">${chip("all", `All ${r.designs.length}`)}${chip("accepted", `Accepted ${r.counts.accepted}`)}${r.counts.pending ? chip("pending", `Pending ${r.counts.pending}`) : ""}
      ${chip("failed", `Failed ${r.counts.failed}`)}${r.counts.rejected ? chip("rejected", `Rejected ${r.counts.rejected}`) : ""}
      <span class="grow" />${columns.controls}${values.toggle}
      <button class="small" disabled=${picked.length !== 2} onClick=${() => onCompare(picked)}>${picked.length ? `Compare ${picked.length}/2` : "Compare"}</button></div>
    <div class="result-table-surface" data-values=${values.mode} ref=${box}>${shown.length ? html`<div class="scroll-x rows-scroll"><table class="list designs">
      <thead><${GroupRow} metrics=${metrics} groups=${r.metric_groups || {}} leading=${3} />
        <tr><th class="pick" title="Tick two to compare"></th>${th("name", "Design")}${th("verdict", "Status", "status-column")}
          ${metrics.map(m => { const l = limitOf(m); return th(m, m, `num measurement-head${columns.hidden(m) ? " hidden-measurement" : ""}${dict.get(m) || ""}`,
            `${m}${unit[m] ? ` (${unit[m]})` : ""}${l ? ` · ${l.direction === "maximize" ? "at least" : "at most"} ${l.goal}` : ""}`,
            l ? html`<div class="lim">${`${l.direction === "maximize" ? "≥" : "≤"} ${l.goal}`}</div>` : null); })}</tr></thead>
      <tbody>${shown.map(d => html`<tr key=${keyOf(d)} class=${`clickable ${d.verdict}${d.decision ? " decided" : ""}${keyOf(d) === selected ? " sel" : ""}`} onClick=${() => onOpen(d)}>
        <td class="pick"><input type="checkbox" title="compare" aria-label=${`Compare ${d.name}`} checked=${picked.some(p => keyOf(p) === keyOf(d))}
          onClick=${e => { e.stopPropagation(); tick(d, e.currentTarget.checked); }} onKeyDown=${e => { if (e.key === "Enter") { e.preventDefault(); e.currentTarget.click(); } }} /></td>
        <td class="mono" title=${`${d.name} · ${d.shown}${d.last ? " · " + d.last : ""}`}>${d.decision ? html`<span class="star" title="decision">★ </span>` : ""}
          <button type="button" class="link mono open-design" title=${`Open ${d.name}`} aria-label=${`Open ${d.name}`} onClick=${e => { e.stopPropagation(); onOpen(d); }}>
            <span class=${`table-design-name${(d.base || d.name).includes("#") ? " design-id" : ""}`}>${names.get(d)}</span></button>
          ${d.part && !(d.base || d.name).includes("#") ? html`<div class="muted small table-part" title=${d.part}>${d.part}</div>` : ""}</td>
        <td class="status-column"><${VerdictBadge} verdict=${d.verdict} reason=${d.why.join("; ")} /></td>
        ${metrics.map(m => cell(d, m))}</tr>`)}</tbody></table></div>
      ${all.length > shown.length ? html`<div class="more"><button class="small" onClick=${() => setPage(n => n + PAGE)}>${`Show ${Math.min(PAGE, all.length - shown.length)} more`}</button>
        <span class="muted">${` ${shown.length} of ${all.length} shown`}</span></div>` : ""}`
      : html`<div class="empty"><p>No design matches.</p></div>`}</div>`;
}

/** Where a design is in the table as it is drawn (its filter and sort aside): the page that holds it. */
export const pageFor = index => Math.ceil((index + 1) / PAGE) * PAGE;
