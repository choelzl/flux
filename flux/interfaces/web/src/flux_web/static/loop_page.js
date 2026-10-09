// Flux web: a loop's page -- its header, tabs and sub-tabs, the agent's question, notes, the Talk
// drawer, the refresh; each tab's drawing in its own module (D892: out of loops.js).

import { cleanup, me, setPageRefresh } from "./state.js";
import { act, ago, api, appHref, card, confirmDialog, crumbs, dialog, dur, empty, enc, h, head, loopStream, pageShow, skeleton, statePill, toast } from "./ui.js";
import { liveTree, logView } from "./live.js";
import { configureInto } from "./configure.js";
import { agentSelect, binButton, lastSaid, markdown, startLoop, stopLoop } from "./loops.js";
import { authorTab, overview } from "./loop_overview.js";
import { resultsView } from "./loop_results.js";
import { timelineTab } from "./loop_timeline.js";
import { agentsView } from "./loop_agents.js";
import { filesTab } from "./loop_files.js";
import { settingsView } from "./loop_settings.js";
import { historyTab } from "./loop_history.js";
import { restoreScroll, scrollState } from "./scroll.js";

async function loopPage(name, owner, path = "") {
  const show = pageShow();
  const qs = owner ? `?owner=${enc(owner)}` : "";
  const q = owner ? `&owner=${enc(owner)}` : "";
  const base = `/api/apps/${enc(name)}`;
  const info = await api(`/apps/${enc(name)}${qs}`);
  if (show.stale()) return;                         // D719: the user went elsewhere while it loaded
  // D701: "owner", "edit" (shared to change and run it), "watch" (shared to see it), "admin"
  const perm = info.perm || (info.mine ? "owner" : "admin");
  const mine = perm === "owner" || perm === "edit" || perm === "admin", isOwner = perm === "owner";   // D812: an admin edits anyone's
  let st = info.state;
  const header = h("div", {}), banner = h("div", {}), body = h("div", {});
  // D713: six tabs; the log and the timeline under Live, the workbench under Files, the problem
  // (the configurator, direct edit, an agent) under Settings, Delete at Settings' end; Ask a panel
  // that opens over any tab. The old addresses lead to their new places.
  const tabs = ["Overview", "Live", "Results", "Agents", "Files", "Settings"];
  const SLUG = { Overview: "", Live: "live", Results: "results", Agents: "agents", Files: "files", Settings: "settings" };
  const TAB_OF = Object.fromEntries(Object.entries(SLUG).map(([t, k]) => [k, t]));
  const ALIAS = { log: "live/log", timeline: "live/timeline", "agent-turns": "agents", workbench: "files/workbench", configure: "settings/problem" };
  let parts = String(path || "").split("/").filter(Boolean);
  if (ALIAS[parts[0]]) parts = [...ALIAS[parts[0]].split("/"), ...parts.slice(1)];
  let askOpen = parts[0] === "ask";
  if (askOpen) parts = [];
  let tab = TAB_OF[parts[0] || ""] || "Overview", sub = parts[1] || "", mode = parts[2] || "";
  const SUBS = { Live: [["", "Tasks"], ["log", "Log"], ["timeline", "Timeline"], ["history", "History"]], Results: [["", "Results"], ["graphs", "Graphs"]], Files: [["", "Loop files"], ["workbench", "Workbench"]],
                 Settings: [["loop", "Preferences"], ["problem", "Problem"]] };
  const subsOf = (t) => (SUBS[t] || []).filter(([k]) => !(t === "Settings" && k === "problem" && !mine));
  const curSub = () => { const o = subsOf(tab); return o.some(([k]) => k === sub) ? sub : (o[0] ? o[0][0] : ""); };
  function setUrl() {
    const segs = [SLUG[tab], tab === "Overview" ? "" : (curSub() === (subsOf(tab)[0] || [""])[0] && !mode ? "" : curSub()), mode].filter(Boolean);
    history.replaceState(null, "", `#/${owner ? `u/${enc(owner)}/` : ""}app/${enc(name)}${segs.length ? "/" + segs.join("/") : ""}`);
  }
  const tabBar = h("div", { class: "tabs", role: "tablist" }), subHolder = h("div", { class: "subrow" });
  let results = null;                                 // D916: the Results tab's views, while it stays open
  let question = st.question || null;
  // D917: the log, the journal and the live state over one stream, open only while a view shows them
  const stream = loopStream(base, qs);
  const log = logView(base, qs, stream);
  const live = liveTree(base, qs, (qq) => { question = qq; drawBanner(); }, stream);
  const runEnd = () => live.ended(st.running ? null : st.last_active || null);      // D928: a run over settles what its journal left open
  runEnd();
  // D917: a poll's request, let go when the page is left
  const leaving = new AbortController();
  cleanup.push(() => { live.close(); stream.close(); leaving.abort(); });

  const crumbBar = h("div", {});
  const leaveBtn = () => act("Leave", async () => {           // D702: a shared loop, left by its guest
    if (!await confirmDialog(`Leave ${info.owner}'s ${name}?`, `${info.owner} is told.`, { ok: "Leave" })) return;
    toast((await api(`/apps/${enc(name)}/shares/me?owner=${enc(info.owner)}`, { method: "DELETE" })).ok, "ok"); location.hash = "#/";
  });
  function drawCrumbs() {
    crumbBar.replaceChildren(crumbs(["Loops", "#/"], owner && owner !== me.name ? [owner, null] : null,
      [name, appHref(owner, name)], tab !== "Overview" ? [tab, null] : null,
      curSub() && curSub() !== (subsOf(tab)[0] || [""])[0] ? [subsOf(tab).find(([k]) => k === curSub())[1], null] : null));
  }
  function drawTabs() {
    drawCrumbs();
    tabBar.replaceChildren(...tabs.map(t => h("button", { role: "tab", class: t === tab ? "on" : "", "aria-selected": t === tab ? "true" : "false",
      onclick: () => { tab = t; sub = ""; mode = ""; results = null; setUrl(); drawTabs(); drawBody(); } }, t)));
  }
  function drawHead() {
    const acts = [];
    if (st.running && perm !== "watch") {
      acts.push(act("Stop after this pass", () => stopLoop(name, false, owner)), act("Stop now", () => stopLoop(name, true, owner), { cls: "danger" }));
    } else if (!st.running && mine && info.document) {
      acts.push(act(st.last_active ? "Start (resume)" : "Start", async () => { if (await startLoop(name, owner)) { await refresh(); goTab("Live"); } }, { cls: "primary" }));
    }
    if (mine) {
      acts.push(act("Check", async () => {
        const out = h("pre", { class: "log small" }, "Checking in the sandbox…");
        const d = dialog("Check the document", out, [["Close", null]]);
        const r = await api(`/apps/${enc(name)}/check`, { method: "POST" });
        out.textContent = (r.ok ? "Ready to run.\n\n" : "NOT READY\n\n") + r.output;
        await d;
      }));
      if (perm === "edit") acts.push(leaveBtn());
    }
    if (perm === "watch") acts.push(leaveBtn());
    const whose = perm === "owner" ? "" : h("span", { class: `pill ${perm === "edit" || perm === "admin" ? "live" : ""}`, title: perm === "edit" ? "Shared with you: you may change and run it"
      : perm === "watch" ? "Shared with you: you may see its runs and outputs" : "An admin: you may change and run it; it runs on its owner's agents and settings" },
      `${info.owner}'s · ${perm === "edit" ? "you may edit" : perm === "watch" ? "watching" : "an admin's edit"}`);
    header.replaceChildren(head(h("span", {}, name, " ", statePill(st), whose),
      h("span", {}, info.document ? h("span", { class: "mono" }, info.document) : "", " · ", lastSaid(st),
        st.container ? h("span", { class: "muted" }, ` · sandbox ${st.container}`) : ""), ...acts));
  }
  // D917: the 5 s poll sends no state request while one is out; only the latest asked is drawn
  let refreshing = null, asked = 0;
  async function refresh() {
    const mine = ++asked, was = st.running;
    const p = refreshing = api(`${base.slice(4)}/state${qs}`, { signal: leaving.signal });
    let got;
    try { got = await p; } finally { if (refreshing === p) refreshing = null; }
    if (mine !== asked || show.stale()) return;
    st = got;
    question = st.question || null;                       // the state says whether the agent still asks
    runEnd();
    drawHead(); drawBanner();
    if (askOpen && was !== st.running) askView();
    if (was !== st.running && ((tab === "Live" && !curSub()) || tab === "Overview")) drawBody();
  }
  // notes and the agent's question
  const noteText = h("textarea", { rows: 3, placeholder: "A note: it joins the next prompt, or answers the agent's open question." });
  const noteList = h("div", { class: "notes" });
  async function sendNote(text) {
    const r = await api(`/apps/${enc(name)}/notes${qs}`, { method: "POST", body: { text } });
    toast(r.ok, "ok"); noteText.value = ""; question = null; asked++; drawBanner(); drawNotes();   // D919: a state asked before it is not drawn
  }
  async function drawNotes() {
    const notes = await api(`/apps/${enc(name)}/notes${qs}`).catch(() => []);
    noteList.replaceChildren(...notes.slice(-20).reverse().map(n => h("div", { class: "note has-bin" }, h("small", { class: "muted" }, n.by, " · ", ago(n.t)), h("div", {}, n.text),
      mine ? binButton("note", "Remove this note?", "It goes from the page, and from the loop if it has not read it yet; what the loop read already stays in its record.",
        async () => { await api(`/apps/${enc(name)}/notes/${enc(n.id)}${qs}`, { method: "DELETE" }); toast("The note is removed", "ok"); drawNotes(); }) : "")));
  }
  // D919: the banner is built once per question (by when it was asked) and kept across the polls --
  // an unsent answer stays until it is sent; only the time left is said again
  let bannerFor = null, bannerLeft = null;
  function drawBanner() {
    composer.update();
    askFab.classList.toggle("asking", !!(question && st.running));   // D758: the agent waits: the button says so
    if (!question || !st.running) { banner.replaceChildren(); bannerFor = null; return; }
    const left = Math.max(0, Math.round(question.asked + question.wait_s - Date.now() / 1000));
    const key = `${question.asked}\n${question.question}`;
    if (bannerFor !== key || !banner.firstChild) {
      bannerFor = key;
      bannerLeft = h("span", { class: "muted" });
      const ans = h("textarea", { rows: 3, placeholder: "Your answer" });
      banner.replaceChildren(h("section", { class: "card ask" }, h("div", { class: "card-head" }, h("h2", {}, "Agent asks"), bannerLeft),
        h("pre", { class: "question" }, question.question), mine ? [ans,
        h("div", { class: "form-actions" }, act("Answer", async () => { if (ans.value.trim()) await sendNote(ans.value.trim()); }, { cls: "primary" }))] : ""));
    }
    bannerLeft.textContent = left ? `${dur(left)} left` : "timed out";
  }
  /** Notes and answers (D697): one line docked under the Live tab, as a chat's. Enter sends,
      Shift+Enter breaks the line. When the agent asks, the line says so and answers it. */
  const composer = (() => {
    const ta = h("textarea", { rows: 1, class: "composer-in", "aria-label": "A note to the loop" });
    const sendBtn = h("button", { class: "primary", type: "button" }, "Send");
    const ask = h("div", { class: "composer-ask" });
    const hist = h("div", { class: "composer-hist", hidden: true }, noteList);
    const grow = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 180) + "px"; };
    async function send() {
      const text = ta.value.trim();
      if (!text) return;
      sendBtn.disabled = true;
      try { await sendNote(text); ta.value = ""; grow(); } catch (x) { toast(x.message, "bad"); } finally { sendBtn.disabled = false; }
    }
    ta.addEventListener("input", grow);
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
    sendBtn.addEventListener("click", send);
    hist.hidden = false;                              // D758: in the drawer, the notes sent so far show under the line
    const el = h("div", { class: "composer" }, ask, h("div", { class: "composer-row" }, ta, sendBtn), hist);
    function update() {
      const open = question && st.running;
      el.classList.toggle("asking", !!open);
      if (open) {
        el.closest("details.steer-card")?.setAttribute("open", "");
        const left = Math.max(0, Math.round(question.asked + question.wait_s - Date.now() / 1000));
        ask.replaceChildren(h("strong", {}, "Agent asks"), h("span", { class: "muted" }, left ? ` · ${dur(left)} left` : " · timed out"),
          h("pre", { class: "question" }, question.question));
        ta.placeholder = "Your answer to the agent (Enter sends)"; sendBtn.textContent = "Answer";
      } else {
        ask.replaceChildren();
        ta.placeholder = "A note to the loop: it joins the next prompt (Enter sends, Shift+Enter for a new line)"; sendBtn.textContent = "Send";
      }
    }
    update();
    return { el, update };
  })();
  /** The log as it grows (D697), under the task: coloured as the Log tab, following its end. */
  const liveLog = (() => {
    const box = h("div", { class: "logview mini wrap" });
    const toEnd = () => requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });   // once laid out
    const N = 80;
    const onlyBad = h("input", { type: "checkbox" });
    const keep = (l) => !onlyBad.checked || log.problem(l.text);
    const fill = () => { box.replaceChildren(...log.recent(4000).filter(keep).slice(-N).map(log.lineEl)); toEnd(); };
    onlyBad.addEventListener("change", fill);
    log.onLines((fresh) => {
      if (!box.isConnected) return;
      const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
      for (const l of fresh) if (keep(l)) box.append(log.lineEl(l));
      while (box.childElementCount > N) box.firstChild.remove();
      if (atEnd) toEnd();
    });
    log.onTimes(fill);
    const liveTimes = h("input", { type: "checkbox", checked: log.times.checked });
    liveTimes.addEventListener("change", () => { log.times.checked = liveTimes.checked; log.times.dispatchEvent(new Event("change")); });
    log.onTimes(() => { liveTimes.checked = log.times.checked; });
    const el = card("Log", box, { cls: "livelog-card", actions: [h("label", { class: "check small" }, onlyBad, "problems only"),
      h("label", { class: "check small" }, liveTimes, "times"),
      h("button", { class: "small", type: "button", onclick: () => goTab("Live", "log") }, "Full log")] });
    return { el, fill };
  })();

  const goTab = (t, s = "") => { tab = t; sub = s; mode = ""; results = null; setUrl(); drawTabs(); drawBody(); };
  // D892: the page as its tabs (loop_*.js) read it, instead of this function's closure: `st` and `tab`
  // are getters, so a tab that awaited reads them as they are now, not as they were when it began
  // D919: each drawing of the body is numbered; `still()` -- taken before a tab's first wait -- says
  // whether it is still the latest after it, so a slow answer never draws over the tab chosen since
  let drawn = 0;
  const still = () => { const mine = drawn; return () => mine === drawn && !show.stale(); };
  const ctx = { name, owner, qs, q, base, info, perm, mine, isOwner, body, curSub, goTab, drawBody, refresh, still,
    get historyId() { return Number(mode); }, selectHistory: (id) => { mode = String(id); setUrl(); },
    get st() { return st; }, get tab() { return tab; } };
  const timelineView = timelineTab(ctx), authorBox = authorTab(ctx), files = filesTab(ctx);
  const past = historyTab(ctx);
  cleanup.push(past.close);
  // Conversations keep their own reply context; loop notes still join the next design prompt.
  let askTimer = null, askWho = null, askReply = null, askBusy = false, askSending = false;
  const collapsedAsks = new Set();
  let notesOpen = false;
  const askQ = h("textarea", { rows: 2, id: "ask-q", "aria-label": "Message", placeholder: "Ask about this loop…" });
  askQ.addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.isComposing) { e.preventDefault(); sendAsk(); }
  });
  cleanup.push(() => clearTimeout(askTimer));
  const askHistory = h("div", { class: "ask-history" });
  const askBox = h("div", { class: "drawer-body" });
  const drawer = h("aside", { class: "drawer", "aria-label": "Talk to this loop" },
    h("div", { class: "drawer-head" }, h("h2", {}, "Talk to this loop"), h("button", { class: "small", type: "button", onclick: () => setAsk(false) }, "Close")), askBox);
  const askFab = h("button", { class: "ask-fab", type: "button", onclick: () => setAsk(!askOpen) }, "Talk");
  function setAsk(open) {
    askOpen = open; drawer.classList.toggle("open", open); askFab.classList.toggle("on", open);
    if (open) askView(); else { clearTimeout(askTimer); askSeq++; }
  }
  const onKey = (e) => { if (e.key === "Escape" && askOpen && !document.querySelector("dialog[open]")) setAsk(false); };
  document.addEventListener("keydown", onKey);
  cleanup.push(() => document.removeEventListener("keydown", onKey));
  let askSeq = 0;
  async function sendAsk() {
    if (!mine || askBusy || askSending) return;
    const text = askQ.value.trim();
    if (!text) { askQ.focus(); return; }
    askSending = true;
    try {
      const sent = await api(`/apps/${enc(name)}/asks${qs}`, { method: "POST", body: { question: text, author: askWho.value, parent_id: askReply } });
      if (askQ.value.trim() === text) askQ.value = "";
      askReply = sent.id; askBusy = true; askSending = false;
      await askView();
      askHistory.querySelector(`[data-ask-turn="${sent.id}"]`)?.scrollIntoView({ block: "nearest" });
    } catch (x) { toast(x.message, "bad"); } finally { askSending = false; }
  }
  async function askView() {
    const my = ++askSeq;
    const list = await api(`/apps/${enc(name)}/asks${qs}`).catch(() => []);
    if (!askOpen || show.stale() || my !== askSeq) return;
    askBusy = list.some(a => a.running);
    clearTimeout(askTimer);
    if (askBusy) askTimer = setTimeout(askView, 3000);
    const reply = list.find(a => a.id === askReply);
    if (!reply) askReply = null;
    if (mine) askWho = askWho || await agentSelect("ask-who");
    if (!askOpen || show.stale() || my !== askSeq) return;
    const place = scrollState(askHistory), active = document.activeElement;
    const selection = active instanceof HTMLTextAreaElement ? [active.selectionStart, active.selectionEnd] : null;
    const logs = new Map([...askHistory.querySelectorAll("[data-ask-log]")].map(el => [el.dataset.askLog, scrollState(el)]));
    const details = new Map([...askHistory.querySelectorAll("details[data-ask-output]")].map(el => [el.dataset.askOutput, el.open]));
    let steer = "";
    if (mine && st.running) {
      composer.update(); drawNotes();
      steer = h("details", { class: "steer-card card", open: notesOpen || !!question,
        ontoggle: e => { notesOpen = e.target.open; } }, h("summary", {}, question ? "The loop is waiting for your answer" : "Send a note to the running loop"),
        h("p", { class: "muted small" }, "Notes join the loop’s next prompt."), composer.el);
    }
    function replyTo(a) {
      askReply = a.id;
      if ([...askWho.querySelectorAll("option")].some(o => o.value === a.author)) askWho.value = a.author;
      askView().then(() => askQ.focus());
    }
    const turn = a => h("div", { class: "ask-turn", "data-ask-turn": a.id },
      h("div", { class: "ask-message ask-user" }, h("div", { class: "ask-meta" }, h("strong", {}, a.by), " · ", ago(a.started)), h("p", {}, a.question)),
      h("div", { class: "ask-message ask-agent" }, h("div", { class: "ask-meta" }, h("strong", {}, a.author), a.ended ? [" · ", dur(a.ended - a.started)] : ""),
        a.running ? h("div", { class: "row" }, h("span", { class: "pill live" }, h("i", { class: "dot" }), "Reading the loop"),
          mine ? act("Stop", async () => { toast((await api(`/apps/${enc(name)}/asks/${a.id}/stop${qs}`, { method: "POST" })).ok, "ok"); askView(); }, { cls: "small" }) : "")
          : a.answer ? markdown(a.answer) : h("p", { class: "muted" }, "No answer was returned."),
        (a.log || []).length ? h("details", { class: "ask-output", "data-ask-output": a.id, open: details.get(a.id) || false }, h("summary", {}, "Activity"),
          h("pre", { class: "log small author-log", "data-ask-log": a.id }, a.log.join("\n"))) : "",
        mine && !a.running && a.answer ? h("div", { class: "ask-turn-actions" }, h("button", { type: "button", class: "small ask-reply", disabled: askBusy || askSending,
          "data-reply-to": a.id, onclick: () => replyTo(a) }, "Reply")) : ""));
    const threads = new Map();
    for (const a of [...list].sort((a, b) => a.started - b.started || a.id.localeCompare(b.id))) {
      const id = a.thread_id || a.id;
      if (!threads.has(id)) threads.set(id, []);
      threads.get(id).push(a);
    }
    const conversations = [...threads.values()].sort((a, b) => b.at(-1).started - a.at(-1).started).map(turns => {
      const selected = turns.some(a => a.id === askReply), running = turns.some(a => a.running);
      const id = turns[0].thread_id || turns[0].id;
      const toggle = h("button", { type: "button", class: "small ask-collapse", "data-ask-toggle": id,
        onclick: () => { if (collapsedAsks.has(id)) collapsedAsks.delete(id); else collapsedAsks.add(id); updateCollapse(); } });
      const conversation = card(null, [h("div", { class: "ask-thread-head" }, h("strong", {}, "Conversation"), toggle,
        mine && !running ? binButton("conversation", "Remove this conversation?", "All its messages, answers and saved reply context are removed for everyone who sees this loop.",
          async () => { await api(`/apps/${enc(name)}/asks/${turns[0].id}${qs}`, { method: "DELETE" }); askView(); }) : ""), ...turns.map(turn)],
        { cls: `ask-card has-bin${selected ? " selected-thread" : ""}` });
      function updateCollapse() {
        const collapsed = collapsedAsks.has(id);
        conversation.classList.toggle("collapsed", collapsed);
        toggle.textContent = collapsed ? "Expand" : "Collapse";
        toggle.setAttribute("aria-expanded", String(!collapsed));
        toggle.setAttribute("aria-label", `${collapsed ? "Expand" : "Collapse"} conversation: ${turns[0].question}`);
      }
      updateCollapse();
      return conversation;
    });
    askHistory.replaceChildren(steer, ...(conversations.length ? conversations : [card(null, empty("No conversations yet."))]));
    let form = "";
    if (mine) {
      const send = act(askBusy ? "Answering…" : "Send", sendAsk, { cls: "primary ask-send", title: "Ctrl+Enter or ⌘+Enter to send" });
      send.disabled = askBusy;
      form = h("div", { class: "ask-compose" },
        h("div", { class: "ask-compose-head" }, h("span", { class: "ask-reply-context", title: reply?.question }, reply ? `Reply to: ${reply.question}` : "New conversation"),
          reply ? h("button", { type: "button", class: "small ask-new", disabled: askSending, onclick: () => { askReply = null; askView().then(() => askQ.focus()); } }, "New chat") : ""),
        askQ, h("div", { class: "ask-compose-actions" }, h("label", { class: "ask-agent-picker" }, "Agent", askWho), send));
    }
    askBox.replaceChildren(askHistory, form);
    restoreScroll(askHistory, place);
    for (const el of askHistory.querySelectorAll("[data-ask-log]")) restoreScroll(el, logs.get(el.dataset.askLog), { follow: true });
    if (active && askBox.contains(active)) {
      active.focus({ preventScroll: true });
      if (selection) active.setSelectionRange(...selection);
    } else if (active?.dataset.askToggle) {
      [...askHistory.querySelectorAll("[data-ask-toggle]")].find(el => el.dataset.askToggle === active.dataset.askToggle)?.focus({ preventScroll: true });
    }
  }

  function drawSubs() {
    const o = subsOf(tab), cur = curSub();
    subHolder.replaceChildren(o.length > 1 ? h("div", { class: "subtabs views", role: "tablist" }, o.map(([k, label]) => h("button", { role: "tab", type: "button",
      class: k === cur ? "on" : "", "aria-selected": k === cur ? "true" : "false", onclick: () => { sub = k; mode = ""; setUrl(); drawCrumbs(); drawBody(); } }, label))) : "");
  }
  async function drawBody() {
    drawn++;
    const ok = still();
    drawBanner(); drawSubs();
    if (tab !== "Live" || curSub() !== "history") past.close();
    // D917: the stream carries what the view shows -- Tasks: the journal, the live state and the
    // log's card; the Log: the log; any other tab: nothing (each part resumes where it was)
    const ran = st.running || st.last_active;
    stream.want(tab !== "Live" ? [] : curSub() === "log" ? ["log"] : !curSub() && ran ? ["events", "live", "log"] : []);
    if (tab === "Settings") {
      if (curSub() === "problem") { configureInto(body, name, owner, mode, `${appHref(owner, name)}/settings/problem`, { small: true, barHost: subHolder }); return; }
      return settingsView(ctx);
    }
    if (tab === "Overview") {
      if (!st.running && !st.last_active) {
        const ab = await authorBox();
        if (!ok()) return;
        body.replaceChildren(ab, card(null, info.document ? empty("This loop has not run yet.", mine ? act("Start", async () => { if (await startLoop(name, owner)) { await refresh(); goTab("Live"); } }, { cls: "primary" }) : "")
          : empty("This loop has no problem document yet.", mine ? h("a", { class: "btn", href: `${appHref(info.owner, name)}/settings/problem/agent` }, "Have an agent write it") : "")));
        return;
      }
      body.replaceChildren(card(null, skeleton(7)));
      await overview(ctx);
      return;
    }
    if (tab === "Live" && !curSub()) {
      if (!st.running && !st.last_active) {
        body.replaceChildren(card(null, empty("This loop has not run yet.", mine ? act("Start", async () => { if (await startLoop(name, owner)) { await refresh(); drawBody(); } }, { cls: "primary" }) : "")));
        return;
      }
      body.replaceChildren(h("div", { class: "live-wrap" },
        h("div", { class: "split" }, card(null, [st.running ? "" : h("p", { class: "muted" }, "The last start."), live.tree], { cls: "tree-card" }),
          h("div", { class: "side-col" }, card(null, live.detail, { cls: "detail-card" }), liveLog.el, card(null, live.stand, { cls: "stand-card" }))),
        ""));
      live.draw(); liveLog.fill(); composer.update();
    } else if (tab === "Live" && curSub() === "history") {
      await past.show();
    } else if (tab === "Live" && curSub() === "timeline") {
      body.replaceChildren(card(null, skeleton(7)));
      await timelineView();
    } else if (tab === "Live" && curSub() === "log") {
      body.replaceChildren(card(null, log.el, { cls: "log-card" }));
      log.render();
    } else if (tab === "Agents") {
      await agentsView(ctx);
    } else if (tab === "Results") {
      // D916: Results and Graphs, two views of one fetch -- switching keeps the selection and the
      // graphs once built; the tab opened again reads the results afresh
      if (!results) {
        body.replaceChildren(card(null, skeleton(7)));
        const r = await api(`/apps/${enc(name)}/results${qs}`);
        if (!ok()) return;                                   // D919: another tab chosen meanwhile
        if (!r.campaign || !r.designs.length) { body.replaceChildren(card(null, empty("No results yet."))); return; }
        results = resultsView(ctx, r);
      }
      body.replaceChildren(results.show(curSub()));
    } else if (tab === "Files" && !curSub()) {
      await files.filesView();
    } else if (tab === "Files" && curSub() === "workbench") {
      await files.workbenchView();
    }
  }
  let beat = 0, busy = false;
  const tick = setInterval(async () => {
    if (document.hidden || refreshing) return;            // D917: a hidden tab asks nothing (it catches up when shown); one at a time
    await refresh().catch(() => {});
    // the Overview and the Timeline follow a running loop (D693, D696): once a minute
    if (++beat % 12 === 0 && (tab === "Overview" || (tab === "Live" && curSub() === "timeline")) && st.running && !document.hidden && !busy) {   // every minute (D696)
      busy = true; try { await (tab === "Overview" ? overview(ctx) : timelineView()); } catch (_) { /* the next beat */ } finally { busy = false; }
    }
  }, 5000);
  const shown = () => { if (!document.hidden) refresh().catch(() => {}); };
  document.addEventListener("visibilitychange", shown);
  cleanup.push(() => { clearInterval(tick); document.removeEventListener("visibilitychange", shown); });
  setPageRefresh(() => refresh());
  drawHead(); drawTabs(); drawBanner(); drawBody();
  show(crumbBar, header, banner, tabBar, subHolder, body, askFab, drawer);
  if (askOpen) setAsk(true);
}

export { loopPage };
