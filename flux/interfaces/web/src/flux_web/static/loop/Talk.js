// Flux web, a loop's page (D697, D990, D991, D993, D1009, D1025, D1095): Talk -- conversations with an agent or the
// model about the loop (each keeps its own reply context; the newest, the one replied to and a running one open,
// older ones folded unless chosen), an answer's thinking and commands, Reply, a new chat; the agent's open question
// on its card at the top (D1131); while the loop runs, a note to it.

import { Ago, Card, Empty, html, Notice, Skeleton, useEffect, useLayoutEffect, useRef, useState } from "../view.js";
import { api, loopApi, reportedTokens, toast } from "../ui.js";
import { dur } from "../format.js";
import { me } from "../state.js";
import { AgentPick } from "../components/AgentPick.js";
import { restoreScroll, scrollState } from "../scroll.js";
import { Act } from "../components/Act.js";
import { Bin } from "../components/Bin.js";
import { Markdown } from "../components/Markdown.js";
import { AgentTurn, turnFacts } from "../live/inspector/Chat.js";
import { FullscreenHead, openModal } from "../components/Modal.js";
import { usePoll } from "../components/Load.js";
import { Pill, Tag } from "../components/Tone.js";
import { Composer } from "../components/Composer.js";

/** D993, D1144: the answering agent's thinking, commands and replies, drawn by Live's own view of an agent; read again
    while it answers. `a` the question; `name` its loop. */
function AskAgent({ a, name }) {
  const [got, setGot] = useState(null), url = loopApi(name).at(`/asks/${a.id}/agent`);
  usePoll(async live => {
    const g = await api(url).catch(x => ({ error: x.message }));
    if (!live()) return null;
    setGot(g);
    return g.running ? 2000 : null;
  });
  if (!got) return html`<${Skeleton} lines=${3} />`;
  if (got.error && !got.steps) return html`<${Notice} tone="bad" what="Not read" why=${got.error} />`;
  const read = (file, start, count) => loopApi(name).api(`/steps?file=${encodeURIComponent(file)}&start=${start}&count=${count}`);
  return html`<${AgentTurn} id=${`ask:${name}:${a.id}`} fields=${got} running=${!!got.running} t0=${a.started} t1=${a.ended} steps=${read} facts=${askFacts(got)} />`;
}
/** The answer's facts, as the Agents tab says a turn's (turnFacts): its status, exit, tokens and tool calls -- the agent
    is in the dialog's title. The tool calls are counted from its steps when it kept them all. */
function askFacts(got) {
  const [tin, tout] = reportedTokens(got["tokens (reported)"]) || [null, null], steps = Array.isArray(got.steps) ? got.steps : [];
  const whole = steps.length && Number(got["steps total"] ?? steps.length) === steps.length;
  return turnFacts({ status: got.running ? "answering" : got.status, exit: got.exit, tin, tout,
    tools: whole ? steps.filter(x => x.k !== "text" && x.k !== "think").length : null });
}
/** Thinking & commands: a dialog over most of the screen (a phone's whole screen). */
function showAgent(a, name) {
  const m = openModal(html`<${FullscreenHead} title=${`Thinking & commands · ${a.author}`} close=${() => m.close()} />
    <div class="agent-dlg-body"><${AskAgent} a=${a} name=${name} /></div>`, { cls: "dlg agent-dlg", label: "Thinking & commands", onCancel: () => m.close() });
}

/** A question and its answer; under the answer one row of quiet actions: Thinking & commands, Reply. */
function Turn({ a, t, open }) {
  const reply = t.canAsk && !a.running && a.answer, thinking = a.author && a.author !== "model";
  const think = html`<button type="button" class="small ask-thinking" data-ask-thinking=${a.id} onClick=${() => t.thinking(a)}>Thinking & commands</button>`;
  return html`<div class="ask-turn" data-ask-turn=${a.id}>
    <div class="ask-message ask-user"><div class="ask-meta"><strong>${a.by}</strong> · <${Ago} t=${a.started} /></div><p>${a.question}</p></div>
    <div class="ask-message ask-agent"><div class="ask-meta"><strong>${a.author}</strong>${a.ended ? ` · ${dur(a.ended - a.started)}` : ""}</div>
      ${a.running ? html`<div class="row ask-running"><${Pill} tone="run">Reading<//>${t.mine || a.by === me.name ? html`<${Act} cls="small" onClick=${() => t.stop(a)}>Stop<//>` : ""}${
          thinking ? html`<span class="grow" />${think}` : ""}</div>`
        : a.answer ? html`<${Markdown} text=${a.answer} />` : html`<${Empty} text="No answer." />`}
      ${(a.log || []).length ? html`<details class="ask-output" data-ask-output=${a.id} open=${open.get(a.id) || false} onToggle=${e => open.set(a.id, e.currentTarget.open)}><summary>Activity</summary>
        <pre class="log small author-log" data-ask-log=${a.id}>${a.log.join("\n")}</pre></details>` : ""}
      ${!a.running && (reply || thinking) ? html`<div class="ask-turn-actions">${thinking ? think : ""}${
        reply ? html`<button type="button" class="small ask-reply" disabled=${t.busy} data-reply-to=${a.id}
          title=${t.busy ? "One ask at a time: the running one answers first" : null} onClick=${() => t.replyTo(a)}>Reply</button>` : ""}</div>` : ""}</div></div>`;
}

/** A conversation: its turns; D990 folded or not; Reply to its latest; removed by whoever may. */
function Thread({ turns, folded, t, open }) {
  const id = turns[0].thread_id || turns[0].id, last = turns.at(-1), running = turns.some(a => a.running), selected = turns.some(a => a.id === t.reply);
  return html`<${Card} cls=${`ask-card${selected ? " selected-thread" : ""}${folded ? " collapsed" : ""}`} title="Conversation"
    meta=${turns.some(a => a.private) ? html`<${Tag} cls="ask-private" title="Only you see it">only you<//>` : null}
    actions=${html`${t.canAsk && !running && last.answer ? html`<button type="button" class="small ask-reply ask-reply-top" disabled=${t.busy} data-reply-to=${last.id} title="Reply to the latest answer" onClick=${() => t.replyTo(last)}>Reply</button>` : ""}
      <button type="button" class="small ask-collapse" data-ask-toggle=${id} aria-expanded=${String(!folded)} aria-label=${`${folded ? "Expand" : "Collapse"} conversation: ${turns[0].question}`} onClick=${() => t.fold(id, !folded)}>${folded ? "Expand" : "Collapse"}</button>`}
    tools=${(t.mine || turns.every(a => a.by === me.name)) && !running ? html`<${Bin} what="conversation" title="Remove this conversation?" said="Its messages and answers go, for everyone." remove=${() => t.remove(turns[0])} />` : null}>
    ${turns.map(a => html`<${Turn} key=${a.id} a=${a} t=${t} open=${open} />`)}<//>`;
}

/** The drawer's body: `ask` the agent's question (its card) or null, scrolled to and its answer focused when
    `askAt` changes; `steer` the note to a running loop (its composer) or null -- both above the conversations;
    `steerOpen` -- {get, set}: whether the note is unfolded, kept by the page. */
export function Talk({ name, mine, canAsk, ask = null, askAt = 0, steer, steerOpen, open: shown = true }) {
  const [list, setList] = useState(null), [offline, setOffline] = useState(false), [reply, setReply] = useState(null), [sending, setSending] = useState(false);
  const [folds, setFolds] = useState({ collapsed: new Set(), expanded: new Set() }), [who, setWho] = useState(""), [replyAuthor, setReplyAuthor] = useState(null);
  const history = useRef(null), q = useRef(null), open = useRef(new Map()).current;
  const [draft, setDraft] = useState("");                     // what is being written, kept while the drawer is closed
  const asked = useRef(null);
  useEffect(() => {
    if (!askAt) return;
    requestAnimationFrame(() => { asked.current?.scrollIntoView({ block: "nearest" }); asked.current?.querySelector("input")?.focus({ preventScroll: true }); });
  }, [askAt]);
  const askCard = ask ? html`<div class="talk-ask" ref=${asked}>${ask}</div>` : "";
  // read while the drawer is open, again while an answer runs; closed, what was read stays for its next opening
  const poll = usePoll(async live => {
    if (!shown) return null;
    let got;
    // D1009: the server away (a restart) -- what is shown kept, said, tried again; never an empty list
    try { got = await loopApi(name).api("/asks"); } catch (_) { if (live()) setOffline(true); return 3000; }
    if (!live()) return null;
    setOffline(false); setList(got);
    return got.some(a => a.running) ? 3000 : null;
  }, [shown]), read = poll.now;
  // the scroll and the activity logs as they were, a running log followed at its end
  const place = useRef(null);
  if (history.current) place.current = { at: scrollState(history.current), logs: new Map([...history.current.querySelectorAll("[data-ask-log]")].map(el => [el.dataset.askLog, scrollState(el)])) };
  useLayoutEffect(() => {
    if (!history.current || !place.current) return;
    restoreScroll(history.current, place.current.at);
    for (const el of history.current.querySelectorAll("[data-ask-log]")) restoreScroll(el, place.current.logs.get(el.dataset.askLog), { follow: true });
  });
  if (!list) return html`${offline ? html`<p class="ask-offline muted small">Reconnecting…</p>` : ""}${askCard}<${Skeleton} lines=${4} />`;
  const busy = list.some(a => a.running), current = list.find(a => a.id === reply) || null;
  const send = async () => {
    const text = draft.trim();
    if (!canAsk || busy || sending) return;
    if (!text) { q.current.focus(); return; }
    if (!who) { toast("No agent is offered for Ask: an admin sets it in Admin › Agents", "warn"); return; }   // D1025
    setSending(true);
    try {
      const sent = await loopApi(name).api("/asks", { method: "POST", body: { question: text, author: who, parent_id: current?.id || null } });
      setDraft(d => d.trim() === text ? "" : d);
      setReply(sent.id);
      await read();
      history.current?.querySelector(`[data-ask-turn="${sent.id}"]`)?.scrollIntoView({ block: "nearest" });
    } catch (x) { toast(x.message, "bad"); } finally { setSending(false); }
  };
  const t = { mine, canAsk, reply: current?.id, busy: busy || sending,
    replyTo: a => { setReply(a.id); setReplyAuthor(a.author); requestAnimationFrame(() => q.current?.focus()); },
    fold: (id, on) => setFolds(f => { const c = new Set(f.collapsed), e = new Set(f.expanded); c.delete(id); e.delete(id); (on ? c : e).add(id); return { collapsed: c, expanded: e }; }),
    stop: async a => { toast((await loopApi(name).api(`/asks/${a.id}/stop`, { method: "POST" })).ok, "ok"); read(); },
    remove: async a => { await loopApi(name).api(`/asks/${a.id}`, { method: "DELETE" }); read(); },
    thinking: a => showAgent(a, name) };
  const threads = new Map();
  for (const a of [...list].sort((x, y) => x.started - y.started || x.id.localeCompare(y.id))) (threads.get(a.thread_id || a.id) || threads.set(a.thread_id || a.id, []).get(a.thread_id || a.id)).push(a);
  const ordered = [...threads.values()].sort((x, y) => y.at(-1).started - x.at(-1).started);
  // D990: the newest conversation, the one replied to and a running one stay open; older ones fold
  const folded = (turns, i) => { const id = turns[0].thread_id || turns[0].id; return folds.collapsed.has(id) || (!folds.expanded.has(id) && !(i === 0 || turns.some(a => a.id === current?.id) || turns.some(a => a.running))); };
  return html`${offline ? html`<p class="ask-offline muted small">Reconnecting…</p>` : ""}
    <div class="ask-history" ref=${history}>${askCard}${steer ? html`<details class="steer-card card" open=${steerOpen.get() || null} onToggle=${e => steerOpen.set(e.currentTarget.open)}>
        <summary>Note to the loop</summary><p class="muted small">Notes join the loop’s next prompt.</p>${steer}</details>` : ""}
      ${ordered.length ? ordered.map((turns, i) => html`<${Thread} key=${turns[0].thread_id || turns[0].id} turns=${turns} folded=${folded(turns, i)} t=${t} open=${open} />`) : html`<${Card}><${Empty} text="No conversations yet." /><//>`}</div>
    ${canAsk ? html`<div class="ask-compose"><${Composer} id="ask-q" rows=${2} enter="mod" label="Message" placeholder="Ask about this loop…" taRef=${q} value=${draft} onInput=${setDraft}
      send=${send} busy=${busy} sendCls="ask-send" sendTitle=${busy ? "One ask at a time: the running one answers first" : "Ctrl+Enter or ⌘+Enter to send"}
      head=${html`<span class="ask-reply-context" title=${current?.question}>${current ? `Reply to: ${current.question}` : "New conversation"}</span>
        ${busy ? html`<span class="muted small ask-one">One ask at a time</span>` : ""}
        ${current ? html`<button type="button" class="small ask-new" disabled=${sending} onClick=${() => { setReply(null); requestAnimationFrame(() => q.current?.focus()); }}>New chat</button>` : ""}`}
      actions=${html`<label class="ask-agent-picker">Agent<${AgentPick} id="ask-who" ask value=${replyAuthor} onPick=${setWho} /></label>`} /></div>` : ""}`;
}
