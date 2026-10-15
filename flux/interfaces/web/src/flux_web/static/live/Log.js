// Live (D692, D697, D699, D754, D759, D816, D917, D1095): the log of the start in view -- the current one as it
// grows over the page's stream (its last 2 MiB first, the earlier on asking), a past one as it was kept; follow,
// wrap, problems only, times, a filter (text or /regex/), Raw, Fullscreen, Download. Only the lines in view are
// drawn: a row has one height, so the scroll says which; wrapped, the last 3000.

import { Card, cx, html, Notice, useEffect, useLayoutEffect, useMemo, useRef, useState } from "../view.js";
import { NARROW, enc, loopApi } from "../ui.js";
import { isProblem, latestStart, lineClass, logAt, logLines, matcher } from "./logmodel.js";
import { ViewerFrame, ViewerTools, useViewer } from "../viewer.js";
import { recall, remember } from "../stored.js";
import { useLoop } from "../loop/context.js";
import { Pill } from "../components/Tone.js";

const WRAPPED = 3000, TAIL = 2 << 20;
const storedTimes = () => recall("flux-log-times") !== "off";

const Line = ({ l, times }) => html`<div class=${`ln ${lineClass(l.text)}`} data-n=${String(l.n)}><span class="no">${String(l.n)}</span>
  ${times ? html`<span class="at" title=${l.at || "written before times were kept"}>${l.at ? logAt(l.at) : ""}</span>` : ""}<span class="tx">${l.text || " "}</span></div>`;

/** The lines: the store's stream for the current start (`store.followLog`), the kept log for a past one. */
function useLog(store, ctx) {
  const { s } = store, model = useMemo(() => logLines(), []);
  const [n, setN] = useState(0), [skipped, setSkipped] = useState(null), [stream, setStream] = useState(""), [cut, setCut] = useState(false), [failed, setFailed] = useState("");
  const tick = useRef(0);
  const bump = () => { if (!tick.current) tick.current = requestAnimationFrame(() => { tick.current = 0; setN(k => k + 1); }); };
  const past = s.startId !== "current", [all, setAll] = useState(0);
  useEffect(() => {
    model.clear(); setSkipped(null); setCut(false); setFailed(""); setN(k => k + 1);
    if (past) {
      let live = true;
      loopApi(ctx.name).request(`/log/raw?run_id=${enc(s.startId)}&download=false&preview=true`).then(async r => {
        const text = await r.text();
        if (!live) return;
        model.add(text); model.end(); setCut(!!r.headers.get("x-flux-truncated")); bump();
      }, x => live && x.message !== "log in" && setFailed(x.message));
      return () => { live = false; };
    }
    store.followLog({ onData: chunk => { model.add(chunk); bump(); }, onState: setStream, onSkipped: setSkipped }, all ? 0 : TAIL);
    return () => store.followLog(null);
  }, [s.startId, s.transport, all]);
  useEffect(() => () => cancelAnimationFrame(tick.current), []);
  return { lines: model.lines, n, past, skipped, stream, cut, failed, loadAll: () => setAll(k => k + 1) };
}

export function Log({ store }) {
  const ctx = useLoop();
  const L = useLog(store, ctx);
  const [o, setO] = useState(() => ({ follow: true, wrap: NARROW.matches, problems: false, times: storedTimes(), filter: "" }));   // D754: on a phone a line wraps
  const set = patch => setO(x => ({ ...x, ...patch }));
  const [scroll, setScroll] = useState(0), box = useRef(null), row = useRef(0), view = useViewer();
  const m = useMemo(() => matcher(o.filter), [o.filter]);
  // the current start's lines (earlier starts are picked above, as past starts); a past start's are its own
  const shown = useMemo(() => (L.past ? L.lines : latestStart(L.lines)).filter(l => (!o.problems || isProblem(l.text)) && (!m || m.test(l.text))), [L.n, o.problems, m]);
  const total = L.past ? L.lines.length : latestStart(L.lines).length;
  // D699: a screen's lines above and below the view; a spacer gives the box the whole log's height
  const r = row.current || 18;
  const first = o.wrap ? Math.max(0, shown.length - WRAPPED) : Math.max(0, Math.floor(scroll / r) - 60);
  const last = o.wrap ? shown.length : Math.min(shown.length, Math.ceil((scroll + (box.current?.clientHeight || 600)) / r) + 60);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    if (!row.current) { const ln = el.querySelector(".win .ln"); if (ln) row.current = ln.getBoundingClientRect().height || 18; }
    if (o.follow) el.scrollTop = el.scrollHeight;
  });
  const onScroll = e => {                                   // scrolling up pauses the follow
    const el = e.currentTarget, atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
    if (!atEnd && o.follow) set({ follow: false });
    if (!o.wrap) setScroll(el.scrollTop);
  };
  const times = on => { remember("flux-log-times", on ? "on" : "off"); row.current = 0; set({ times: on }); };
  const check = (k, label, title, change = v => set({ [k]: v })) => html`<label class="check" title=${title}><input type="checkbox" checked=${o[k]} onChange=${e => change(e.currentTarget.checked)} />${label}</label>`;
  const raw = L.past ? `${ctx.base}/log/raw?run_id=${enc(store.s.startId)}&download=false${ctx.q}` : `${ctx.base}/log/raw${ctx.qs || "?"}${ctx.qs ? "&" : ""}download=false`;
  return html`<${Card} cls="log-card"><${ViewerFrame} v=${view} title="Log"><div onviewerresize=${() => { row.current = 0; setScroll(box.current?.scrollTop || 0); }}>
    <div class="toolbar">${check("follow", "follow", null, v => { set({ follow: v }); })}${check("wrap", "wrap")}${check("problems", "problems only")}${check("times", "times", "Each line's time", times)}
      <input placeholder="filter (text or /regex/)" class=${`filter${m?.bad ? " bad" : ""}`} value=${o.filter} onInput=${e => set({ filter: e.currentTarget.value })} />
      <span class="muted">${`${shown.length === total ? total : `${shown.length} of ${total}`} line(s)`}</span>
      <${ViewerTools} v=${view} title="Log" rawUrl=${raw} />
      ${L.past ? "" : html`<a class="btn small" href=${`${ctx.base}/log/raw${ctx.qs}`}>Download</a>`}
      ${L.skipped ? html`<span class="log-earlier small">${`${(L.skipped.bytes / 1048576).toFixed(1)} MiB of earlier lines not loaded · `}<button type="button" class="small" onClick=${L.loadAll}>Load all</button></span>` : ""}
      ${!L.past && L.stream && L.stream !== "live" ? html`<${Pill} tone="run" small cls="stream" title="The page's connection to the loop">reconnecting<//>` : ""}</div>
    ${L.cut ? html`<p class="muted">Preview: first 1 MiB. Raw opens the complete output.</p>` : ""}${L.failed ? html`<${Notice} tone="bad" what="This start's log could not be read" why=${L.failed} />` : ""}
    <div class=${cx("logview virt", o.wrap && "wrap")} ref=${box} onScroll=${onScroll}>
      ${o.wrap ? "" : html`<div class="spacer" style=${`height:${shown.length * r}px`} />`}
      <div class="win" style=${o.wrap ? "" : `transform:translateY(${first * r}px)`}>
        ${o.wrap && first ? html`<div class="ln more">${`… ${first} earlier line(s): turn wrap off to scroll through all, or download the log`}</div>` : ""}
        ${shown.slice(first, last).map(l => html`<${Line} key=${l.n} l=${l} times=${o.times} />`)}</div></div></div><//><//>`;
}
