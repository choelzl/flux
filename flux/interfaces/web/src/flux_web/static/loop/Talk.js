// Flux web, a loop's page (D697, D990, D991, D993, D1009, D1025, D1095): Talk -- conversations with an agent or the
// model about the loop (each keeps its own reply context; the newest, the one replied to and a running one open,
// older ones folded unless chosen), an answer's thinking and commands, Reply, a new chat; while the loop runs, a
// note to it (the agent's open question answered there).

import { Ago, Card, Empty, Skeleton, html, useEffect, useLayoutEffect, useRef, useState } from "../view.js";
import { api, dialog, enc, toast } from "../ui.js";
import { dur, plural } from "../format.js";
import { me } from "../state.js";
import { AgentPick } from "../components/AgentPick.js";
import { restoreScroll, scrollState } from "../scroll.js";
import { Act } from "../components/Act.js";
import { Bin } from "../components/Bin.js";
import { Markdown } from "../components/Markdown.js";
import { Conversation, liveWork } from "../components/Conversation.js";

/** D993: the answering agent's thinking, commands and replies, as Live shows an agent; followed while it answers. */
function AgentTurn({ url }) {
  const [got, setGot] = useState(null), box = useRef(null);
  useEffect(() => {
    let timer = null, live = true;
    const draw = async () => {
      const g = await api(url).catch(x => ({ error: x.message }));
      if (!live) return;
      setGot(g);
      if (g.running) timer = setTimeout(draw, 2000);
      else requestAnimationFrame(() => { if (box.current) box.current.parentElement.scrollTop = box.current.parentElement.scrollHeight; });
    };
    draw();
    return () => { live = false; clearTimeout(timer); };
  }, []);
  if (!got) return html`<${Skeleton} lines=${3} />`;
  const steps = Array.isArray(got.steps) ? got.steps : [], total = Number(got["steps total"] || steps.length), think = got["thinking (live tail)"] || "";
  const stats = [got.agent, got["tokens (reported)"], got.elapsed, got["tool calls"] ? `${String(got["tool calls"]).split("\n").length}+ tool calls` : ""].filter(Boolean);
  return html`<div ref=${box}>${stats.length ? html`<p class="muted small">${stats.join(" · ")}</p>` : ""}
    ${total > steps.length ? html`<p class="muted small">${`${plural(total - steps.length, "earlier step")} not shown`}</p>` : ""}
    ${steps.length ? html`<div class="ask-conversation"><${Conversation} steps=${steps} k=${url} offset=${total - steps.length} live=${!!got.running} /></div>`
      : think ? html`<details class="cv-step cv-think" open><summary>Thinking</summary><pre class="cv-body cv-thought">${think}</pre></details>`
      : html`<p class="muted">${got.error || (got.running ? "Nothing recorded yet." : "No thinking or commands were recorded for this answer.")}</p>`}
    ${got.running && !liveWork(steps, got.running) ? html`<div class="alt-chat-now"><span class="alt-spin" />${`${got.status || "working"}…`}</div>` : ""}
    ${got.stderr ? html`<details class="ask-output"><summary>stderr</summary><pre class="log small">${got.stderr}</pre></details>` : ""}</div>`;
}

/** A question and its answer. */
function Turn({ a, t, open }) {
  return html`<div class="ask-turn" data-ask-turn=${a.id}>
    <div class="ask-message ask-user"><div class="ask-meta"><strong>${a.by}</strong> · <${Ago} t=${a.started} /></div><p>${a.question}</p></div>
    <div class="ask-message ask-agent"><div class="ask-meta"><strong>${a.author}</strong>${a.ended ? ` · ${dur(a.ended - a.started)}` : ""}</div>
      ${a.running ? html`<div class="row"><span class="pill live"><i class="dot" />Reading the loop</span>${t.mine || a.by === me.name ? html`<${Act} cls="small" onClick=${() => t.stop(a)}>Stop<//>` : ""}</div>`
        : a.answer ? html`<${Markdown} text=${a.answer} />` : html`<p class="muted">No answer was returned.</p>`}
      ${a.author && a.author !== "model" ? html`<div class="ask-turn-actions"><button type="button" class="small ask-thinking" data-ask-thinking=${a.id} onClick=${() => t.thinking(a)}>Thinking & commands</button></div>` : ""}
      ${(a.log || []).length ? html`<details class="ask-output" data-ask-output=${a.id} open=${open.get(a.id) || false} onToggle=${e => open.set(a.id, e.currentTarget.open)}><summary>Activity</summary>
        <pre class="log small author-log" data-ask-log=${a.id}>${a.log.join("\n")}</pre></details>` : ""}
      ${t.canAsk && !a.running && a.answer ? html`<div class="ask-turn-actions"><button type="button" class="small ask-reply" disabled=${t.busy} data-reply-to=${a.id} onClick=${() => t.replyTo(a)}>Reply</button></div>` : ""}</div></div>`;
}

/** A conversation: its turns; D990 folded or not; Reply to its latest; removed by whoever may. */
function Thread({ turns, folded, t, open }) {
  const id = turns[0].thread_id || turns[0].id, last = turns.at(-1), running = turns.some(a => a.running), selected = turns.some(a => a.id === t.reply);
  return html`<${Card} cls=${`ask-card has-bin${selected ? " selected-thread" : ""}${folded ? " collapsed" : ""}`}><div class="ask-thread-head"><strong>Conversation</strong>
      ${turns.some(a => a.private) ? html`<span class="pill small ask-private" title="Asked without edit rights on this loop: no one else sees it">only you</span>` : ""}
      ${t.canAsk && !running && last.answer ? html`<button type="button" class="small ask-reply ask-reply-top" disabled=${t.busy} data-reply-to=${last.id} title="Reply to this conversation's latest answer" onClick=${() => t.replyTo(last)}>Reply</button>` : ""}
      <button type="button" class="small ask-collapse" data-ask-toggle=${id} aria-expanded=${String(!folded)} aria-label=${`${folded ? "Expand" : "Collapse"} conversation: ${turns[0].question}`} onClick=${() => t.fold(id, !folded)}>${folded ? "Expand" : "Collapse"}</button>
      ${(t.mine || turns.every(a => a.by === me.name)) && !running ? html`<${Bin} what="conversation" title="Remove this conversation?" said="All its messages, answers and saved reply context are removed for everyone who sees this loop." remove=${() => t.remove(turns[0])} />` : ""}</div>
    ${turns.map(a => html`<${Turn} key=${a.id} a=${a} t=${t} open=${open} />`)}<//>`;
}

/** The drawer's body: `steer` -- {el, asking}: the note to a running loop (its composer), or null --
    above the conversations; `steerOpen` -- {get, set}: whether it is unfolded, kept by the page. */
export function Talk({ name, qs, mine, canAsk, steer, steerOpen, open: shown = true }) {
  const [list, setList] = useState(null), [offline, setOffline] = useState(false), [reply, setReply] = useState(null), [sending, setSending] = useState(false);
  const [folds, setFolds] = useState({ collapsed: new Set(), expanded: new Set() }), [who, setWho] = useState(""), [replyAuthor, setReplyAuthor] = useState(null);
  const history = useRef(null), q = useRef(null), open = useRef(new Map()).current, timer = useRef(null), alive = useRef(true);
  const [draft, setDraft] = useState("");                     // what is being written, kept while the drawer is closed
  const read = async () => {
    clearTimeout(timer.current);
    let got;
    // D1009: the server away (a restart) -- what is shown kept, said, tried again; never an empty list
    try { got = await api(`/apps/${enc(name)}/asks${qs}`); } catch (_) { if (alive.current) { setOffline(true); timer.current = setTimeout(read, 3000); } return; }
    if (!alive.current) return;
    setOffline(false); setList(got);
    if (got.some(a => a.running)) timer.current = setTimeout(read, 3000);
  };
  // read while the drawer is open; closed, what was read stays for its next opening
  useEffect(() => {
    alive.current = shown;
    if (shown) read();
    return () => { alive.current = false; clearTimeout(timer.current); };
  }, [shown]);
  // the scroll and the activity logs as they were, a running log followed at its end
  const place = useRef(null);
  if (history.current) place.current = { at: scrollState(history.current), logs: new Map([...history.current.querySelectorAll("[data-ask-log]")].map(el => [el.dataset.askLog, scrollState(el)])) };
  useLayoutEffect(() => {
    if (!history.current || !place.current) return;
    restoreScroll(history.current, place.current.at);
    for (const el of history.current.querySelectorAll("[data-ask-log]")) restoreScroll(el, place.current.logs.get(el.dataset.askLog), { follow: true });
  });
  if (!list) return html`${offline ? html`<p class="ask-offline muted small">Reconnecting to the server…</p>` : ""}<${Skeleton} lines=${4} />`;
  const busy = list.some(a => a.running), current = list.find(a => a.id === reply) || null;
  const send = async () => {
    const text = draft.trim();
    if (!canAsk || busy || sending) return;
    if (!text) { q.current.focus(); return; }
    if (!who) { toast("No agent or model is offered for Ask on this server: an admin sets it in Admin › Agents and models", "warn"); return; }   // D1025
    setSending(true);
    try {
      const sent = await api(`/apps/${enc(name)}/asks${qs}`, { method: "POST", body: { question: text, author: who, parent_id: current?.id || null } });
      setDraft(d => d.trim() === text ? "" : d);
      setReply(sent.id);
      await read();
      history.current?.querySelector(`[data-ask-turn="${sent.id}"]`)?.scrollIntoView({ block: "nearest" });
    } catch (x) { toast(x.message, "bad"); } finally { setSending(false); }
  };
  const t = { mine, canAsk, reply: current?.id, busy: busy || sending,
    replyTo: a => { setReply(a.id); setReplyAuthor(a.author); requestAnimationFrame(() => q.current?.focus()); },
    fold: (id, on) => setFolds(f => { const c = new Set(f.collapsed), e = new Set(f.expanded); c.delete(id); e.delete(id); (on ? c : e).add(id); return { collapsed: c, expanded: e }; }),
    stop: async a => { toast((await api(`/apps/${enc(name)}/asks/${a.id}/stop${qs}`, { method: "POST" })).ok, "ok"); read(); },
    remove: async a => { await api(`/apps/${enc(name)}/asks/${a.id}${qs}`, { method: "DELETE" }); read(); },
    thinking: a => dialog(`Thinking and commands · ${a.author}`, html`<div class="agent-view ask-agent-view"><${AgentTurn} url=${`/apps/${enc(name)}/asks/${a.id}/agent${qs}`} /></div>`, [["Close", null]]) };
  const threads = new Map();
  for (const a of [...list].sort((x, y) => x.started - y.started || x.id.localeCompare(y.id))) (threads.get(a.thread_id || a.id) || threads.set(a.thread_id || a.id, []).get(a.thread_id || a.id)).push(a);
  const ordered = [...threads.values()].sort((x, y) => y.at(-1).started - x.at(-1).started);
  // D990: the newest conversation, the one replied to and a running one stay open; older ones fold
  const folded = (turns, i) => { const id = turns[0].thread_id || turns[0].id; return folds.collapsed.has(id) || (!folds.expanded.has(id) && !(i === 0 || turns.some(a => a.id === current?.id) || turns.some(a => a.running))); };
  return html`${offline ? html`<p class="ask-offline muted small">Reconnecting to the server…</p>` : ""}
    <div class="ask-history" ref=${history}>${steer ? html`<details class="steer-card card" open=${steerOpen.get() || null} onToggle=${e => steerOpen.set(e.currentTarget.open)}>
        <summary>${steer.asking ? "The loop is waiting for your answer" : "Send a note to the running loop"}</summary><p class="muted small">Notes join the loop’s next prompt.</p>${steer.el}</details>` : ""}
      ${ordered.length ? ordered.map((turns, i) => html`<${Thread} key=${turns[0].thread_id || turns[0].id} turns=${turns} folded=${folded(turns, i)} t=${t} open=${open} />`) : html`<${Card}><${Empty} text="No conversations yet." /><//>`}</div>
    ${canAsk ? html`<div class="ask-compose"><div class="ask-compose-head"><span class="ask-reply-context" title=${current?.question}>${current ? `Reply to: ${current.question}` : "New conversation"}</span>
        ${current ? html`<button type="button" class="small ask-new" disabled=${sending} onClick=${() => { setReply(null); requestAnimationFrame(() => q.current?.focus()); }}>New chat</button>` : ""}</div>
      <textarea rows="2" id="ask-q" aria-label="Message" placeholder="Ask about this loop…" ref=${q} value=${draft} onInput=${e => setDraft(e.currentTarget.value)} onKeyDown=${e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.isComposing) { e.preventDefault(); send(); } }} />
      <div class="ask-compose-actions"><label class="ask-agent-picker">Agent<${AgentPick} id="ask-who" ask value=${replyAuthor} onPick=${setWho} /></label>
        <${Act} cls="primary ask-send" title="Ctrl+Enter or ⌘+Enter to send" disabled=${busy} onClick=${send}>${busy ? "Answering…" : "Send"}<//></div></div>` : ""}`;
}
