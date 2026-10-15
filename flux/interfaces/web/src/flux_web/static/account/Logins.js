// Flux web, account (D734, D747, D751, D768, D922, D924, D935, D1095): a user's agent logins -- each agent's three
// states apart (installed, connected -- how, from where, never a value -- verified), Test, Test for a loop whose
// variables set it otherwise, the last test's steps; its interactive login run in a small terminal: its output
// (links clickable), a line to type, the keys a menu wants.

import { html, useEffect, useRef, useState } from "../view.js";
import { api, enc, toast, when } from "../ui.js";
import { restoreScroll, scrollState } from "../scroll.js";
import { Act } from "../components/Act.js";
import { MECHANISM, VERIFIED } from "../admin/Agents.js";
import { usePoll } from "../components/Load.js";
import { Pill } from "../components/Tone.js";

const FIELD_WORD = { endpoint: "endpoint", key: "key", auth: "token", token: "login token", model: "model" };
/** Links as links, the rest as text. */
function Linked({ text }) {
  const parts = [], re = /https?:\/\/[^\s"'<>]+/g;
  let at = 0, m;
  while ((m = re.exec(text))) { parts.push(text.slice(at, m.index), html`<a href=${m[0]} target="_blank" rel="noopener noreferrer">${m[0]}</a>`); at = m.index + m[0].length; }
  parts.push(text.slice(at));
  return parts;
}

/** The login being run: polled while it runs; when it ends, the list read again (D768: its Test has begun by then). */
function Terminal({ session, onEnded }) {
  const [st, setSt] = useState(null), [text, setText] = useState(""), [line, setLine] = useState("");
  const out = useRef(null), offset = useRef(0), again = useRef(() => {});
  useEffect(() => { offset.current = 0; setText(""); }, [session]);
  const poll = usePoll(async live => {
    let got;
    try { got = await api(`/logins/session?since=${offset.current}`); } catch (_) { return null; }
    if (!live()) return null;
    if (got.text) { offset.current = got.offset; setText(t => (t + got.text).slice(-60000)); }
    setSt(got);
    if (got.running) return 700;
    setTimeout(onEnded, 500);
    return null;
  }, [session]);
  again.current = (ms = 150) => poll.soon(ms);
  // the output follows its end while one reads at its end
  const place = useRef(null);
  if (out.current) place.current = scrollState(out.current);
  useEffect(() => { if (out.current && place.current) restoreScroll(out.current, place.current, { follow: true }); }, [text]);
  const send = async body => { try { await api("/logins/session/input", { method: "POST", body }); } catch (_) { /* said by the toast */ } again.current(); };
  const key = (label, k, title) => html`<button type="button" class="small" title=${title} onClick=${() => send({ key: k })}>${label}</button>`;
  const enter = () => { send({ text: line, key: "enter" }); setLine(""); };
  const running = !!st?.running;
  return html`<div class="login-term"><div class="login-head"><strong class="login-what">${!st ? "" : st.running ? `Logging ${st.agent} in…` : st.agent ? `${st.agent}: the login ended${st.rc ? ` (exit ${st.rc})` : ""}` : ""}</strong>
      <span class="grow" /><button type="button" class="small danger" hidden=${!running} onClick=${async () => { await api("/logins/session/stop", { method: "POST" }); again.current(300); }}>Stop</button></div>
    <pre class="login-out" aria-live="polite" ref=${out}><${Linked} text=${text} /></pre>
    <div class="row login-row" hidden=${!running}><input placeholder="type here, then Send (or a key below)" class="login-in" aria-label="Input to the login" value=${line}
        onInput=${e => setLine(e.currentTarget.value)} onKeyDown=${e => { if (e.key === "Enter") { e.preventDefault(); enter(); } }} />
      <${Act} cls="primary small" onClick=${async () => { await send({ text: line, key: "enter" }); setLine(""); }}>Send<//></div>
    <div class="row login-keys" hidden=${!running}>${key("↑", "up", "Up")}${key("↓", "down", "Down")}${key("Enter", "enter", "Enter")}${key("Esc", "escape", "Escape")}${key("Tab", "tab", "Tab")}${key("Ctrl-C", "ctrl-c", "Interrupt")}</div></div>`;
}

/** D924: three states apart -- installation, connection (how, from where; never a value), verification. */
function AgentLogin({ a, test, login }) {
  const c = a.connection || { mechanism: "none", said: "" }, v = a.verified || { state: "untested" };
  const ver = a.testing ? ["run", "testing"] : v.state === "ready" && v.by === "admin" ? ["ok", "ready (tested by the admin for everyone)"] : VERIFIED[v.state] || VERIFIED.untested;   // D935
  const pill = s => VERIFIED[s] || VERIFIED.untested;
  return html`<div class="agent-conn" data-agent=${a.id}><dl class="agent-states">
      <dt>Installation</dt><dd><${Pill} tone=${a.program ? "ok" : "bad"}>${a.program ? "installed" : "missing"}<//></dd>
      <dt>Connection</dt><dd><span class=${`pill ${c.mechanism === "none" ? "" : "ok"}`}>${MECHANISM[c.mechanism] || c.mechanism}</span><span class="muted small">${c.said}</span>
        ${Object.keys(a.fields || {}).length ? html`<span class="muted small agent-sources">${Object.entries(a.fields).map(([f, x]) => `${FIELD_WORD[f] || f}: ${x.source}`).join(" · ")}</span>` : ""}</dd>
      <dt>Verification</dt><dd><${Pill} tone=${ver[0]} title=${v.when ? `tested ${when(v.when)}` : ""}>${ver[1]}<//>
        ${v.state === "ready" && v.when ? html`<span class="muted small">${when(v.when)}</span>` : ""}${v.state === "failed" && v.said ? html`<span class="bad small">${v.said}</span>` : ""}
        ${v.state === "changed" ? html`<span class="muted small">its endpoint, model or credential is not what was tested</span>` : ""}</dd></dl>
    ${(a.conflicts || []).length || (a.unused || []).length ? html`<ul class="small hint-line agent-notes">${[...(a.conflicts || []), ...(a.unused || [])].map(x => html`<li>${x}</li>`)}</ul>` : ""}
    <div class="actions">${a.testing ? html`<span class="muted small">testing…</span>` : html`<${Act} cls="primary small" title="Ask it one short question, as your loops run it" onClick=${() => test(a)}>Test connection<//>`}</div>
    ${(a.loops || []).length ? html`<div class="small"><span class="muted">Loops whose variables set it otherwise:</span><ul class="agent-loops">${a.loops.map(x => html`<li key=${x.loop}><span class="mono">${x.loop}</span>
      <${Pill} tone=${pill(x.state)[0]}>${pill(x.state)[1]}<//>${a.testing ? "" : html` <${Act} cls="small" title="Its variables give this agent another configuration there" onClick=${() => test(a, x.loop)}>Test for this loop<//>`}</li>`)}</ul></div>` : ""}
    ${a.tested?.steps?.length ? html`<details key="tested" class="set-fold"><summary>Last test's steps</summary><ul class="agent-steps small">${a.tested.steps.map(s => html`<li key=${s.step} class=${s.ok ? "" : "bad"}>
      <span class="mono">${s.ok ? "✓ " : "✗ "}</span><strong>${s.step}</strong> ${s.said}</li>`)}</ul></details>` : ""}
    <details class="set-fold agent-login-fold"><summary>Interactive login<span class="muted small">${a.logged_in ? " · logged in" : " · optional"}</span></summary>
      <p class="muted small">One way to connect: its own login, kept in your Flux home. Not needed with a key or a provider configuration.</p>
      <div class="row"><code class="small" title=${a.command}>${a.command.replace(/^\S*\//, "")}</code><${Act} cls="small" onClick=${() => login(a)}>${a.logged_in ? "Log in again" : "Log in"}<//></div></details></div>`;
}

/** The agents and the terminal: `lg` what /logins said; `placed` the agents drawn in their own tab (their panel). */
export function useLogins() {
  const [lg, setLg] = useState(null), [session, setSession] = useState(0), testing = useRef(new Set());
  const read = async () => {
    const got = await api("/logins").catch(() => null);
    if (!got) return;
    // D751: an agent is used in your loops once its test passed; D768: a login that ended well is tested at once, on the server
    for (const a of got.agents) if (testing.current.has(a.id) && !a.testing && a.tested)
      toast(a.tested.ok ? `${a.label} is ready for your loops` : `${a.label}'s Test failed: see its steps`, a.tested.ok ? "ok" : "warn");
    testing.current = new Set(got.agents.filter(a => a.testing).map(a => a.id));
    setLg(got);
    if (got.session?.running && !session) setSession(n => n + 1);
  };
  useEffect(() => { read(); }, []);
  useEffect(() => { if (!lg || !testing.current.size) return; const t = setTimeout(read, 2000); return () => clearTimeout(t); }, [lg]);
  const test = async (a, loop = "") => {
    toast(`Testing ${a.label}${loop ? ` for ${loop}` : ""}: it is asked one short question…`, "info");
    const got = await api(`/agents/${a.id}/test${loop ? `?loop=${enc(loop)}` : ""}`, { method: "POST" });
    toast(got.ok ? `${a.label} is ready${loop ? ` for ${loop}` : " for your loops"}` : `${a.label} is not ready: see its steps`, got.ok ? "ok" : "warn");
    await read();
  };
  const login = async a => { await api(`/logins/${a.id}`, { method: "POST" }); setSession(n => n + 1); };
  const panel = a => html`<${AgentLogin} key=${a.id} a=${a} test=${test} login=${login} />`;
  const terminal = session ? html`<${Terminal} session=${session} onEnded=${read} />` : "";
  return { lg, panel, terminal };
}
