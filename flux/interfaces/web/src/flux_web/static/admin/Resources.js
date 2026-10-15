// Flux web, admin (D695, D699, D838, D921, D989, D1095): what the machine holds up -- its CPUs, load, memory and
// disks; the sandbox containers (one left behind killed); each loop's disk, its caches cleared; the caches no
// loop owns; the machine and every loop's tokens over time.

import { Ago, Card, Empty, Skeleton, html, useEffect, useState } from "../view.js";
import { api, appHref, confirmDialog, enc, toast } from "../ui.js";
import { bytes, dur } from "../format.js";
import { stopLoop } from "../loops.js";
import { TimeChart } from "../charts/TimeChart.js";
import { Act } from "../components/Act.js";
import { Meter, Stat } from "../components/Stat.js";
import { SortableTable } from "../components/SortableTable.js";

const LoopLink = ({ u, a }) => u ? html`<a href=${appHref(u, a)}>${`${u} / ${a}`}</a>` : html`<span class="muted">no loop</span>`;
const clean = async (key, what) => { const x = await api(`/admin/caches/${enc(key)}/clean`, { method: "POST", body: { what } }); toast(`${bytes(x.freed)} freed`, "ok"); };

function Machine({ m }) {
  const mem = m.memory || {}, load = (m.load || [0])[0] / m.cpus;
  return html`<${Card} title="Machine"><div class="stats five">
    <${Stat} label="CPUs" value=${String(m.cpus)} />
    <${Stat} label="Load (1 · 5 · 15 min)" value=${(m.load || []).map(x => x.toFixed(1)).join(" · ")} sub=${`${Math.round(load * 100)}% of the CPUs`}><${Meter} frac=${load} /><//>
    <${Stat} label="Memory used" value=${mem.total ? bytes(mem.total - mem.available) : "?"} sub=${mem.total ? `of ${bytes(mem.total)}` : ""}>${mem.total ? html`<${Meter} frac=${1 - mem.available / mem.total} />` : ""}<//>
    ${m.disks.filter(d => !d.same_as).slice(0, 2).map(d => { const also = m.disks.filter(x => x.same_as === d.label).map(x => x.label);
      return html`<${Stat} label=${`Disk: ${d.label}`} value=${`${bytes(d.free)} free`} title=${d.path} sub=${`of ${bytes(d.total)}${also.length ? " · also " + also.join(", ") : ""}`}><${Meter} frac=${d.used / d.total} /><//>`; })}</div><//>`;
}

function Containers({ r, reload }) {
  const cs = r.containers || [];
  const state = c => c.orphan && c.state === "running" ? "left behind" : c.activity && c.activity !== "loop" ? c.activity : c.state;
  const kill = async c => {
    if (!await confirmDialog(`Kill ${c.name}?`, "No active task owns it; it is removed.", { ok: "Kill", danger: true })) return;
    toast((await api(`/admin/containers/${enc(c.name)}/kill`, { method: "POST" })).ok, "ok"); reload();
  };
  // D921: a sample, said with its time
  return html`<${Card} title=${`Sandbox containers (${r.engine || "none"})`} actions=${r.containers_at ? html`<span class="muted small">asked <${Ago} t=${r.containers_at} /></span>` : null}>
    ${r.error ? html`<p class="callout bad">${r.error}</p>` : !cs.length ? html`<${Empty} text="No sandbox container." />`
    : html`<${SortableTable} memo="flux-sort-containers" first=${3} cls="list" scroll keyOf=${c => c.name} rows=${cs} cols=${[
        { label: "Container", key: c => c.name, asc: true }, { label: "Loop", key: c => c.user ? `${c.user}/${c.loop}` : "", asc: true }, { label: "State", key: state, asc: true },
        { label: "CPU", key: c => c.cpu, num: true }, { label: "Memory", key: c => c.mem, num: true }, { label: "PIDs", key: c => c.pids, num: true }, { label: "" }]}
      row=${c => html`<tr><td class="mono">${c.name}</td><td><${LoopLink} u=${c.user} a=${c.loop} /></td>
        <td><span class=${`pill ${c.state === "running" ? (c.orphan ? "warn" : "live") : ""}`}>${state(c)}</span> <small class="muted">${c.status}</small></td>
        <td class="num mono">${c.cpu != null ? `${c.cpu.toFixed(1)}%` : ""}</td><td class="num mono">${c.mem != null ? bytes(Math.round(c.mem)) : ""}</td><td class="num mono">${c.pids != null ? String(c.pids) : ""}</td>
        <td class="right">${c.orphan ? html`<${Act} cls="small danger" onClick=${() => kill(c)}>Kill<//>`
          : c.activity && c.activity !== "loop" ? (c.user ? html`<a class="btn small" href=${appHref(c.user, c.loop)}>View</a>` : null)
          : html`<${Act} cls="small" title="Stop the loop after this pass" onClick=${async () => { await stopLoop(c.loop, false, c.user); reload(); }}>Stop<//>`}</td></tr>`} />`}<//>`;
}

const SIZES = [["inputs", "Inputs"], ["record", "Record"], ["log", "Log"], ["workbench", "Workbench"], ["cache", "Sandbox cache"], ["total", "Total"]];
function LoopDisk({ loops, reload }) {
  const total = k => loops.reduce((s, l) => s + (l[k] || 0), 0);
  const clear = (l, what, label, text) => html`<${Act} cls="small" onClick=${async () => {
    if (!await confirmDialog(`${label}: ${l.user} / ${l.app}?`, text, { ok: label, danger: what === "all" })) return;
    await clean(l.cache_key || l.key, what); reload(); }}>${label}<//>`;
  return html`<${Card} title="Disk per loop"><${SortableTable} memo="flux-sort-loop-disk" first=${7} scroll keyOf=${l => `${l.user}/${l.app}`} rows=${loops}
    cols=${[{ label: "Loop", key: l => `${l.user}/${l.app}`, asc: true }, { label: "" }, ...SIZES.map(([k, label]) => ({ label, key: l => l[k] || 0, num: true })), { label: "" }]}
    row=${l => html`<tr><td><${LoopLink} u=${l.user} a=${l.app} /></td><td>${l.running ? html`<span class="pill live">running</span>` : ""}</td>
      ${SIZES.map(([k]) => html`<td class=${`num mono${k === "total" ? " strong" : ""}`}>${bytes(l[k])}</td>`)}
      <td class="right">${l.running || !l.cache ? "" : html`<div class="actions end">
        ${clear(l, "tools", "Clear tools' cache", "The tools' own cache in the sandbox (XDG_CACHE_HOME) is emptied; they rebuild what they need.")}
        ${clear(l, "scratch", "Clear past scratch", "The agents' working folders of past passes go. The journal, the transcript, the record and the workbench stay.")}</div>`}</td></tr>`}
    foot=${html`<tfoot><tr class="sum"><td>All loops</td><td />${SIZES.map(([k]) => html`<td class="num mono strong">${bytes(total(k))}</td>`)}<td /></tr></tfoot>`} /><//>`;
}

function OtherCaches({ caches, reload }) {
  if (!caches.length) return null;
  const remove = async c => {
    if (!await confirmDialog(`Delete the cache ${c.key}?`, `${bytes(c.size)}: its scratch, the agents' sessions and the tools' cache.`, { ok: "Delete", danger: true })) return;
    await clean(c.key, "all"); reload();
  };
  return html`<${Card} title="Caches no loop owns"><p class="muted">Deleting one frees its space; it is rebuilt when needed.</p>
    <${SortableTable} memo="flux-sort-caches" first=${2} scroll keyOf=${c => c.key} rows=${caches}
      cols=${[{ label: "Cache", key: c => c.key, asc: true }, { label: "Whose", key: c => c.kind === "gone" ? `${c.user}/${c.app}` : "", asc: true },
        { label: "Size", key: c => c.size, num: true }, { label: "Last touched", key: c => c.touched }, { label: "" }]}
      row=${c => html`<tr><td class="mono">${c.key}</td>
        <td>${c.kind === "gone" ? html`<span>${`${c.user}'s ${c.app}, `}<span class="pill warn">deleted</span></span>` : html`<span class="muted">not the web's</span>`}</td>
        <td class="num mono">${bytes(c.size)}</td><td class="muted">${c.touched ? html`<${Ago} t=${c.touched} />` : ""}</td>
        <td class="right"><${Act} cls="small danger" onClick=${() => remove(c)}>Delete<//></td></tr>`} /><//>`;
}

let historyHours = 24;
const RANGES = [[1, "1 h"], [6, "6 h"], [24, "24 h"], [168, "7 d"], [720, "30 d"]];
/** D699: the machine over time, a sample a minute while `flux serve` runs; D838: every loop's tokens per second,
    read (in) and written (out), the agents' and Flux's model's. */
function OverTime({ n }) {
  const [hours, setHours] = useState(historyHours), [got, setGot] = useState(null);
  useEffect(() => {
    let live = true;
    Promise.all([api(`/admin/history?hours=${hours}`).catch(() => null), api(`/admin/token-rate?hours=${hours}`).catch(() => null)])
      .then(([hx, tr]) => { if (live && hx) setGot({ hx, tr }); });
    return () => { live = false; };
  }, [hours, n]);
  const chips = html`<div class="chips">${RANGES.map(([hrs, label]) => html`<button class=${`chip${hours === hrs ? " on" : ""}`} onClick=${() => { historyHours = hrs; setHours(hrs); }}>${label}</button>`)}</div>`;
  if (!got) return html`<${Card} title="Over time" actions=${chips}><${Skeleton} lines=${4} /><//>`;
  const { hx, tr } = got, ts = tr ? tr.samples : [], ss = hx.samples, last = ss.at(-1), cpus = last ? last.cpus : null;
  const rate = v => v >= 1000 ? `${(v / 1000).toFixed(1)}k/s` : `${v > 0 && v < 1 ? Number(v.toPrecision(2)) : v >= 10 ? Math.round(v) : v.toFixed(1)}/s`;
  const sum = (s, d) => s[`${d}_agent`] + s[`${d}_model`], pct = v => `${Math.round(v * 100)}%`;
  const tokens = d => [{ label: "all", get: s => sum(s, d) }, { label: "agents", get: s => s[`${d}_agent`] }, { label: "Flux's model", get: s => s[`${d}_model`] }];
  return html`<${Card} title="Over time" actions=${chips}>
    ${hx.sampling ? "" : html`<p class="muted small">Not sampling here: older samples.</p>`}
    ${tr ? html`<p class="muted small token-rate-note">${`Token rates: ${dur(tr.bucket_seconds)} averages, including cached inputs. An agent that reports its tokens as it works counts them as they come (read every 10 s); Flux's model and other agents count when a turn ends, spread over the turn.`}</p>` : ""}
    <div class="tcharts">
      <${TimeChart} samples=${ts} series=${tokens("in")} title="Tokens in per second, every loop" fmt=${rate} intervals />
      <${TimeChart} samples=${ts} series=${tokens("out")} title="Tokens out (generated) per second, every loop" fmt=${rate} intervals />
      <${TimeChart} samples=${ss} series=${[{ label: "load", get: s => s.load1 }]} title="Load" level=${cpus} levelLabel=${cpus ? `${cpus} CPUs` : ""} fmt=${v => v.toFixed(1)} />
      <${TimeChart} samples=${ss} series=${[{ label: "used", get: s => s.mem_total ? s.mem_used / s.mem_total : null }]} title="Memory" top=${1} fmt=${pct} />
      <${TimeChart} samples=${ss} series=${Object.keys(last?.disks || {}).map(k => ({ label: k, get: s => (s.disks || {})[k] }))} title="Disks" top=${1} fmt=${pct} />
      <${TimeChart} samples=${ss} series=${[{ label: "CPU", get: s => s.cpu }]} title="Containers' CPU (100% = one core)" top=${Math.max(100, ...ss.map(s => s.cpu || 0)) * 1.05} fmt=${v => `${Math.round(v)}%`} />
      <${TimeChart} samples=${ss} series=${[{ label: "memory", get: s => s.cmem }]} title="Containers' memory" fmt=${v => bytes(Math.round(v))} />
      <${TimeChart} samples=${ss} series=${[{ label: "loops", get: s => s.loops }, { label: "containers", get: s => s.containers }]} title="Running"
        top=${2 * Math.ceil((Math.max(1, ...ss.map(s => Math.max(s.loops || 0, s.containers || 0))) + 1) / 2)} fmt=${v => String(Math.round(v))} /></div><//>`;
}

/** Read again every 15 s while no dialog is open over it. */
export function Resources() {
  const [r, setR] = useState(null), [n, setN] = useState(0);
  const reload = () => api("/admin/resources").then(x => { setR(x); setN(k => k + 1); });
  useEffect(() => {
    reload();
    const t = setInterval(() => { if (!document.hidden && !document.querySelector("dialog.dlg[open]")) reload().catch(() => {}); }, 15000);
    return () => clearInterval(t);
  }, []);
  if (!r) return html`<p class="muted">Measuring…</p>`;
  return html`<${Machine} m=${r.machine} /><${OverTime} n=${n} /><${Containers} r=${r} reload=${reload} />
    <${LoopDisk} loops=${r.loops.slice().sort((a, b) => b.total - a.total)} reload=${reload} /><${OtherCaches} caches=${r.caches} reload=${reload} />`;
}
