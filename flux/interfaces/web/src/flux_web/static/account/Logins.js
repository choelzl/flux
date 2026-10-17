// Flux web, account (D734, D747, D751, D768, D922, D924, D935, D1095): a user's agent logins -- each agent's three
// states apart (installed, connected -- how, from where, never a value -- verified), Test, Test for a loop whose
// variables set it otherwise, the last test's steps; its interactive login run in a small terminal: its output
// (links clickable), a line to type, the keys a menu wants.

import { html, useEffect, useRef, useState } from "../view.js";
import { api, enc, toast, when } from "../ui.js";
import { restoreScroll, scrollState } from "../scroll.js";
import { Act } from "../components/Act.js";
import { AgentStates, MECHANISM, VERIFIED } from "../admin/Agents.js";
import { usePoll } from "../components/Load.js";
import { Pill, Said, Failed } from "../components/Tone.js";

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
  return html`<div class="login-term"><div class="login-head"><strong class="login-what">${!st ? "" : st.running ? `Logging ${st.agent} in…` : st.agent ? `${st.agent}: login ended${st.rc ? ` (exit ${st.rc})` : ""}` : ""}</strong>
      <span class="grow" /><button type="button" class="small danger" hidden=${!running} onClick=${async () => { await api("/logins/session/stop", { method: "POST" }); again.current(300); }}>Stop</button></div>
    <pre class="login-out" aria-live="polite" ref=${out}><${Linked} text=${text} /></pre>
    <div class="row login-row" hidden=${!running}><input placeholder="type, then Send" class="login-in" aria-label="Input to the login" value=${line}
        onInput=${e => setLine(e.currentTarget.value)} onKeyDown=${e => { if (e.key === "Enter") { e.preventDefault(); enter(); } }} />
      <${Act} cls="primary small" onClick=${async () => { await send({ text: line, key: "enter" }); setLine(""); }}>Send<//></div>
    <div class="row login-keys" hidden=${!running}>${key("↑", "up", "Up")}${key("↓", "down", "Down")}${key("Enter", "enter", "Enter")}${key("Esc", "escape", "Escape")}${key("Tab", "tab", "Tab")}${key("Ctrl-C", "ctrl-c", "Interrupt")}</div></div>`;
}

/** D924: three states apart -- installation, connection (how, from where; never a value), verification. */
function AgentLogin({ a, test, login }) {
  const c = a.connection || { mechanism: "none", said: "" }, v = a.verified || { state: "untested" };
  const ver = a.testing ? ["run", "testing"] : v.state === "ready" && v.by === "admin" ? ["ok", "ready (tested by admin)"] : VERIFIED[v.state] || VERIFIED.untested;   // D935
  const pill = s => VERIFIED[s] || VERIFIED.untested;
  return html`<div class="agent-conn" data-agent=${a.id}><${AgentStates}
      install=${{ tone: a.program ? "ok" : "bad", words: a.program ? "installed" : "missing" }}
      conn=${{ tone: c.mechanism === "none" ? "none" : "ok", words: MECHANISM[c.mechanism] || c.mechanism, said: c.said,
        extra: Object.keys(a.fields || {}).length ? html`<span class="muted small agent-sources">${Object.entries(a.fields).map(([f, x]) => `${FIELD_WORD[f] || f}: ${x.source}`).join(" · ")}</span>` : null }}
      verify=${{ tone: ver[0], words: ver[1], title: v.when ? `tested ${when(v.when)}` : "",
        said: v.state === "ready" && v.when ? when(v.when) : v.state === "changed" ? "endpoint, model or credential changed since test" : "",
        extra: v.state === "failed" && v.said ? html`<${Said} tone="bad" cls="small">${v.said}<//>` : null }}
      test=${() => test(a)} testing=${a.testing} />
    ${(a.conflicts || []).length || (a.unused || []).length ? html`<ul class="small hint-line agent-notes">${[...(a.conflicts || []), ...(a.unused || [])].map(x => html`<li>${x}</li>`)}</ul>` : ""}
    ${(a.loops || []).length ? html`<div class="small"><span class="muted">Loops that override it:</span><ul class="agent-loops">${a.loops.map(x => html`<li key=${x.loop}><span class="mono">${x.loop}</span>
      <${Pill} tone=${pill(x.state)[0]}>${pill(x.state)[1]}<//>${a.testing ? "" : html` <${Act} cls="small" title="Configured differently there" onClick=${() => test(a, x.loop)}>Test for this loop<//>`}</li>`)}</ul></div>` : ""}
    ${a.tested?.steps?.length ? html`<details key="tested" class="set-fold"><summary>Last test</summary><ul class="agent-steps small">${a.tested.steps.map(s => html`<li key=${s.step} class=${s.ok ? "" : "bad"}>
      <span class="mono">${s.ok ? "✓ " : "✗ "}</span><strong>${s.step}</strong> ${s.said}</li>`)}</ul></details>` : ""}
    <details class="set-fold agent-login-fold"><summary>Interactive login<span class="muted small">${a.logged_in ? " · logged in" : " · optional"}</span></summary>
      <p class="muted small">Its own login, kept in your Flux home. Not needed with a key or provider config.</p>
      <div class="row"><code class="small" title=${a.command}>${a.command.replace(/^\S*\//, "")}</code><${Act} cls="small" onClick=${() => login(a)}>${a.logged_in ? "Log in again" : "Log in"}<//></div></details></div>`;
}

/** The agents and the terminal: `lg` what /logins said; `placed` the agents drawn in their own tab (their panel). */
export function useLogins() {
  const [lg, setLg] = useState(null), [failed, setFailed] = useState(null), [session, setSession] = useState(0), testing = useRef(new Set());
  const read = async (live = () => true) => {
    let got;
    try { got = await api("/logins"); } catch (x) { if (live() && x.message !== "log in") setFailed(x.message); return null; }
    if (!live()) return null;
    setFailed(null);
    // D751: an agent is used in your loops once its test passed; D768: a login that ended well is tested at once, on the server
    for (const a of got.agents) if (testing.current.has(a.id) && !a.testing && a.tested)
      toast(a.tested.ok ? `${a.label} is ready` : `${a.label} failed its test: see its steps`, a.tested.ok ? "ok" : "warn");
    testing.current = new Set(got.agents.filter(a => a.testing).map(a => a.id));
    setLg(got);
    if (got.session?.running && !session) setSession(n => n + 1);
    return got;
  };
  // read when drawn, and every 2 s while an agent is being tested (D1149)
  const poll = usePoll(async live => (await read(live)) && testing.current.size ? 2000 : null);
  const test = async (a, loop = "") => {
    toast(`Testing ${a.label}${loop ? ` for ${loop}` : ""}…`, "info");
    const got = await api(`/agents/${a.id}/test${loop ? `?loop=${enc(loop)}` : ""}`, { method: "POST" });
    toast(got.ok ? `${a.label} is ready${loop ? ` for ${loop}` : ""}` : `${a.label} is not ready: see its steps`, got.ok ? "ok" : "warn");
    await poll.now();
  };
  const login = async a => { await api(`/logins/${a.id}`, { method: "POST" }); setSession(n => n + 1); };
  const panel = a => html`<${AgentLogin} key=${a.id} a=${a} test=${test} login=${login} />`;
  const terminal = session ? html`<${Terminal} session=${session} onEnded=${poll.now} />` : "";
  const said = failed && !lg ? html`<${Failed} what="Agent logins could not be read" why=${failed} retry=${poll.now} />` : "";
  return { lg, said, panel, terminal };
}
