// Flux web: logging in, an invite's page, one's account and agent logins (D889: split out of app.js).

import { cleanup, me, setMe } from "./state.js";
import { act, api, card, dur, enc, fmtTok, h, head, pageShow, toast, when } from "./ui.js";
import { logo } from "./charts.js";
import { envEditor, envTable } from "./loops.js";
import { MECHANISM, VERIFIED, settingsForm } from "./admin.js";
import { route } from "./app.js";

// ================================================================ pages
async function loginPage() {
  const show = pageShow();
  // D699: a phone's keyboard neither capitalises nor corrects a name
  const name = h("input", { autocomplete: "username", autocapitalize: "none", autocorrect: "off", spellcheck: "false", required: true });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const err = h("p", { class: "err" });
  const form = h("form", { class: "card login", onsubmit: async (e) => {
      e.preventDefault(); err.textContent = "";
      try { setMe(await api("/login", { method: "POST", body: { name: name.value, password: pw.value } })); location.hash = "#/"; route(); }
      catch (x) { err.textContent = x.message; }
    } },
    h("div", { class: "login-mark" }, logo(56)), h("h1", {}, "Flux"), h("p", { class: "sub" }, "Log in to your loops."),
    h("label", { class: "stack" }, "Name", name), h("label", { class: "stack" }, "Password", pw),
    h("button", { class: "primary wide", type: "submit" }, "Log in"), err);
  show(form);
  name.focus();
}

/** D818: a link to set one's password -- for an invited user, or after a reset; one use, a week. */
async function invitePage(token) {
  const show = pageShow();
  let got;
  try { got = await api(`/invite/${enc(token)}`); }
  catch (x) { show(h("div", { class: "card login" }, h("div", { class: "login-mark" }, logo(56)), h("h1", {}, "Flux"), h("p", { class: "err" }, x.message),
    h("a", { class: "btn", href: "#/login" }, "Log in"))); return; }
  const pw = h("input", { type: "password", autocomplete: "new-password", required: true, minlength: 10, id: "inv-pw" });
  const pw2 = h("input", { type: "password", autocomplete: "new-password", required: true, id: "inv-pw2" });
  const err = h("p", { class: "err" });
  const form = h("form", { class: "card login", onsubmit: async (e) => {
      e.preventDefault(); err.textContent = "";
      if (pw.value.length < 10) { err.textContent = "At least 10 characters."; return; }
      if (pw.value !== pw2.value) { err.textContent = "The two are not the same."; return; }
      try { setMe(await api(`/invite/${enc(token)}`, { method: "POST", body: { text: pw.value } })); location.hash = "#/"; route(); }
      catch (x) { err.textContent = x.message; }
    } },
    h("div", { class: "login-mark" }, logo(56)), h("h1", {}, "Flux"),
    h("p", { class: "sub" }, got.kind === "invite" ? `Welcome, ${got.name}: choose your password.` : `${got.name}: choose a new password.`),
    h("label", { class: "stack" }, "Password (10 or more characters)", pw), h("label", { class: "stack" }, "Again", pw2),
    h("button", { class: "primary wide", type: "submit" }, got.kind === "invite" ? "Set it and log in" : "Change it and log in"), err,
    h("p", { class: "muted small" }, `Valid once, until ${new Date(got.expires * 1000).toLocaleString()}.`));
  show(form);
  pw.focus();
}

async function accountPage() {
  const show = pageShow();
  const [st, myEnv] = await Promise.all([api("/settings"), api("/env")]);
  async function save(values) { await api("/settings", { method: "PUT", body: { values } }); }   // D833: quiet, field by field
  const pw = h("input", { type: "password", autocomplete: "new-password" });
  const mine = await api("/usage").catch(() => null);
  const holders = Object.fromEntries(st.groups.filter(g => g.agent).map(g => [g.agent, h("div", { class: "agent-login" })]));
  const lg = await loginsCard(holders);
  const logins = { box: lg.box, term: lg.term, panels: Object.fromEntries(Object.entries(holders).map(([a, el]) =>
    [a, { el: h("div", { class: "agent-panel" }, h("h4", { class: "set-sub first" }, "Connection"), el) }])) };   // D924: connection first
  show(head("Account", `Logged in as ${me.name}`),
    mine && mine.turns ? card("My usage", h("p", {}, `${mine.turns} model and agent turn(s) over ${mine.loops} loop(s), ${dur(mine.seconds)}`,
      mine.counted ? `, ${fmtTok(mine.tokens_in)} tokens in and ${fmtTok(mine.tokens_out)} out` : "",
      mine.cost_usd ? `, $${mine.cost_usd.toFixed(2)} at the prices set` : "", ".")) : "",
    // D814: one card, a tab per tool -- each agent's login and Test, its model, its own variables; Flux's
    // model; the variables every agent of yours gets
    card("My agents and models", [
      h("p", { class: "muted" }, st.external ? "Your own settings only. Keys are encrypted and never shown."
        : "Empty: the server's (grey). Your own endpoint uses only your values. Keys are encrypted and never shown."),
      ...settingsForm(st, { server: st.server, save, scope: "me", panels: logins.panels,
        extraTabs: [{ tab: "Every agent", noSave: true, el: h("fieldset", { class: "set-group" }, h("legend", {}, "Variables for every run and every agent of yours"),
          h("p", { class: "muted small" }, "Every run and agent of yours gets these; a loop's own win."),
          envEditor(myEnv.mine, async (v) => { await api("/env", { method: "PUT", body: v }); route(); }, "me"),
          myEnv.server.length ? h("div", { class: "blk" }, h("h4", {}, "The server's"), envTable(myEnv.server.map(x => ({ ...x, from: "the server" })), new Set(myEnv.mine.map(x => x.name)))) : "") }],
        agentEnv: (a) => { const e = (st.agent_env || {})[a] || { mine: [], server: [] };
          return { rows: e.mine, server: e.server, save: async (v) => { await api(`/agents/${a}/env`, { method: "PUT", body: v }); route(); } }; } }),
      logins.box, logins.term]),
    h("div", { class: "grid-2" },
      card("Password", [h("label", { class: "stack" }, "New password (10+)", pw),
        h("div", { class: "form-actions" }, act("Change", async () => { await api("/password", { method: "POST", body: { text: pw.value } }); pw.value = ""; toast("Password changed", "ok"); }))]),
      card("Notifications", [        "Notification" in window ? (Notification.permission === "granted" ? h("p", {}, "Desktop notifications are on.")
          : Notification.permission === "denied" ? h("p", { class: "muted" }, "Desktop notifications are blocked in this browser's settings.")
          : act("Allow desktop notifications", async () => { await Notification.requestPermission(); route(); })) : ""])));
}

/** A user's agent logins (D734; every user's, D747): each agent, logged in or not, and its login run in a
    small terminal -- its output (links clickable), a line to type, the keys a menu wants. */
async function loginsCard(holders = null) {           // D814: `holders[agent]`: where its row goes (its tab)
  const box = h("div", {});
  const out = h("pre", { class: "login-out", "aria-live": "polite" });
  const line = h("input", { placeholder: "type here, then Send (or a key below)", class: "login-in", "aria-label": "Input to the login" });
  let offset = 0, text = "", timer = null, testTimer = null, wasTesting = new Set();
  const linkify = (t) => {                                  // links as links, the rest as text nodes
    const parts = [], re = /https?:\/\/[^\s"'<>]+/g; let at = 0, m;
    while ((m = re.exec(t))) { parts.push(t.slice(at, m.index), h("a", { href: m[0], target: "_blank", rel: "noopener noreferrer" }, m[0])); at = m.index + m[0].length; }
    parts.push(t.slice(at));
    return parts;
  };
  const send = async (body) => { try { await api("/logins/session/input", { method: "POST", body }); } catch (_) { /* said by the toast */ } setTimeout(poll, 150); };
  const keyBtn = (label, key, title) => h("button", { type: "button", class: "small", title, onclick: () => send({ key }) }, label);
  const term = h("div", { class: "login-term", hidden: true },
    h("div", { class: "login-head" }, h("strong", { class: "login-what" }), h("span", { class: "grow" }),
      h("button", { type: "button", class: "small danger", onclick: async () => { await api("/logins/session/stop", { method: "POST" }); setTimeout(poll, 300); } }, "Stop")),
    out,
    h("div", { class: "row login-row" }, line, act("Send", async () => { await send({ text: line.value, key: "enter" }); line.value = ""; }, { cls: "primary small" })),
    h("div", { class: "row login-keys" }, keyBtn("↑", "up", "Up"), keyBtn("↓", "down", "Down"), keyBtn("Enter", "enter", "Enter"),
      keyBtn("Esc", "escape", "Escape"), keyBtn("Tab", "tab", "Tab"), keyBtn("Ctrl-C", "ctrl-c", "Interrupt")));
  line.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); send({ text: line.value, key: "enter" }); line.value = ""; } });
  async function poll() {
    clearTimeout(timer);
    let st;
    try { st = await api(`/logins/session?since=${offset}`); } catch (_) { return; }
    if (!box.isConnected) return;
    if (st.text) { text += st.text; offset = st.offset; out.replaceChildren(...linkify(text.slice(-60000))); out.scrollTop = out.scrollHeight; }
    term.querySelector(".login-what").textContent = st.running ? `Logging ${st.agent} in…` : st.agent ? `${st.agent}: the login ended${st.rc ? ` (exit ${st.rc})` : ""}` : "";
    for (const el of term.querySelectorAll(".login-row, .login-keys, .login-head button")) el.hidden = !st.running;
    if (st.running) timer = setTimeout(poll, 700);
    else setTimeout(drawList, 500);                         // D768: its Test has begun by then
  }
  async function drawList() {
    const lg = await api("/logins").catch(() => null);
    if (!lg || !box.isConnected && box.parentNode) return;
    // D751: an agent is used in your loops once its test passed -- the program, its connection, one short answer
    // D768: a login that ended well is tested at once, on the server -- said here when it is done
    for (const a of lg.agents) if (wasTesting.has(a.id) && !a.testing && a.tested)
      toast(a.tested.ok ? `${a.label} is ready for your loops` : `${a.label}'s Test failed: see its steps`, a.tested.ok ? "ok" : "warn");
    wasTesting = new Set(lg.agents.filter(a => a.testing).map(a => a.id));
    clearTimeout(testTimer);
    if (wasTesting.size) testTimer = setTimeout(drawList, 2000);
    const steps = (t) => h("ul", { class: "agent-steps small" }, (t.steps || []).map(st =>
      h("li", { class: st.ok ? "" : "bad" }, h("span", { class: "mono" }, st.ok ? "✓ " : "✗ "), h("strong", {}, st.step), " ", st.said)));
    const test = (a, loop = "") => async () => {
      toast(`Testing ${a.label}${loop ? ` for ${loop}` : ""}: it is asked one short question…`, "info");
      const got = await api(`/agents/${a.id}/test${loop ? `?loop=${enc(loop)}` : ""}`, { method: "POST" });
      toast(got.ok ? `${a.label} is ready${loop ? ` for ${loop}` : " for your loops"}` : `${a.label} is not ready: see its steps`, got.ok ? "ok" : "warn");
      await drawList();
    };
    // D924: three states apart -- installation, connection (how, from where; never a value), verification
    const panelOf = (a) => {
      const c = a.connection || { mechanism: "none", said: "" }, v = a.verified || { state: "untested" };
      const ver = a.testing ? ["live", "testing…"] : VERIFIED[v.state] || VERIFIED.untested;
      const line = (dt, ...dd) => [h("dt", {}, dt), h("dd", {}, ...dd)];
      const loops = (a.loops || []).map(x => h("li", {}, h("span", { class: "mono" }, x.loop), " ",
        h("span", { class: `pill ${(VERIFIED[x.state] || VERIFIED.untested)[0]}` }, (VERIFIED[x.state] || VERIFIED.untested)[1]), " ",
        a.testing ? "" : act("Test for this loop", test(a, x.loop), { cls: "small", title: "Its variables give this agent another configuration there" })));
      return h("div", { class: "agent-conn", "data-agent": a.id },
        h("dl", { class: "agent-states" },
          ...line("Installation", h("span", { class: `pill ${a.program ? "ok" : "bad"}` }, a.program ? "installed" : "missing")),
          ...line("Connection", h("span", { class: `pill ${c.mechanism === "none" ? "" : "ok"}` }, MECHANISM[c.mechanism] || c.mechanism),
            h("span", { class: "muted small" }, c.said)),
          ...line("Verification", h("span", { class: `pill ${ver[0]}`, title: v.when ? `tested ${when(v.when)}` : "" }, ver[1]),
            v.state === "ready" && v.when ? h("span", { class: "muted small" }, when(v.when)) : "",
            v.state === "failed" && v.said ? h("span", { class: "bad small" }, v.said) : "",
            v.state === "changed" ? h("span", { class: "muted small" }, "its endpoint, model or credential is not what was tested") : "")),
        (a.conflicts || []).length || (a.unused || []).length ? h("ul", { class: "small hint-line agent-notes" },
          [...(a.conflicts || []), ...(a.unused || [])].map(x => h("li", {}, x))) : "",
        h("div", { class: "actions" }, a.testing ? h("span", { class: "muted small" }, "testing…")
          : act("Test connection", test(a), { cls: "primary small", title: "Ask it one short question, as your loops run it" })),
        loops.length ? h("div", { class: "small" }, h("span", { class: "muted" }, "Loops whose variables set it otherwise:"), h("ul", { class: "agent-loops" }, loops)) : "",
        a.tested && a.tested.steps && a.tested.steps.length ? h("details", { class: "set-fold" }, h("summary", {}, "Last test's steps"), steps(a.tested)) : "",
        h("details", { class: "set-fold agent-login-fold" }, h("summary", {}, "Interactive login",
            h("span", { class: "muted small" }, a.logged_in ? " · logged in" : " · optional")),
          h("p", { class: "muted small" }, "One way to connect: its own login, kept in your Flux home. Not needed with a key or a provider configuration."),
          h("div", { class: "row" }, h("code", { class: "small", title: a.command }, a.command.replace(/^\S*\//, "")),
            act(a.logged_in ? "Log in again" : "Log in", async () => {
              await api(`/logins/${a.id}`, { method: "POST" });
              text = ""; offset = 0; out.replaceChildren(); term.hidden = false; poll();
            }, { cls: "small" }))));
    };
    const placed = holders ? lg.agents.filter(a => holders[a.id]) : [];
    for (const a of placed) holders[a.id].replaceChildren(panelOf(a));
    const rest = lg.agents.filter(a => !placed.includes(a));
    box.replaceChildren(...rest.map(a => h("div", { class: "agent-panel" }, h("h4", { class: "agent-panel-name" }, a.label), panelOf(a))));
    if (lg.session && lg.session.running && term.hidden) { term.hidden = false; poll(); }
  }
  cleanup.push(() => { clearTimeout(timer); clearTimeout(testTimer); });
  await drawList();
  if (holders) return { box, term };
  return card("Agent logins", [h("p", { class: "muted" }, "Your loops run on your logins, also when someone you share one with starts it."), box, term]);
}

export { accountPage, invitePage, loginPage };
