// Historical starts: complete task journals, logs and agent conversations; retained campaign results.
import { api, card, empty, enc, h, request, skeleton, when } from "./ui.js";
import { liveTree } from "./live.js";
import { resultsView } from "./loop_results.js";
import { agentsView } from "./loop_agents.js";
import { viewerTools } from "./viewer.js";

export function historyTab(ctx) {
  let cancel = null, tree = null, seq = 0, selected = "tasks", campaignId = "";
  const close = () => { seq++; if (cancel) cancel.abort(); if (tree) tree.close(); cancel = tree = null; };
  async function show() {
    close();
    const valid = ctx.still();
    ctx.body.replaceChildren(card(null, skeleton(6)));
    let history;
    try { history = await api(`/apps/${enc(ctx.name)}/runs${ctx.qs}`); }
    catch (error) {
      if (valid() && error.message !== "log in") ctx.body.replaceChildren(card(null, empty(`Run history is unavailable: ${error.message}`)));
      return;
    }
    if (!valid()) return;
    if (!history.starts.length) { ctx.body.replaceChildren(card(null, empty("No starts recorded yet."))); return; }
    let start = history.starts.find(s => s.id === ctx.historyId) || history.starts[0];
    const panel = h("div", {}), controls = h("div", { class: "history-controls" }), tabs = h("div", { class: "subtabs" });
    const startSel = h("select", { "aria-label": "Historical start", onchange: () => {
      start = history.starts.find(s => s.id === Number(startSel.value));
      ctx.selectHistory(start.id); drawControls(); load();
    } }, history.starts.map(s => h("option", { value: s.id, selected: s.id === start.id },
      `${when(s.started)} · ${s.running ? "running" : s.rc == null ? "ended" : "exit " + s.rc}`)));
    const campaignSel = h("select", { "aria-label": "Recorded campaign", onchange: () => { campaignId = campaignSel.value; load(); } });
    function drawControls() {
      const campaigns = history.campaigns.filter(c => c.run_id === start.record_id);
      if (!campaigns.some(c => c.campaign_id === campaignId)) {
        const older = campaigns.find(c => Date.parse(c.created_at) / 1000 <= start.started + 5);
        campaignId = (older || campaigns[0] || {}).campaign_id || "";
      }
      campaignSel.replaceChildren(...campaigns.map(c => h("option", { value: c.campaign_id, selected: c.campaign_id === campaignId },
        `${c.created_at.replace("T", " ").slice(0, 19)} · ${c.campaign_id.slice(0, 12)} · ${c.status}`)));
      controls.replaceChildren(h("label", { class: "stack" }, "Start", startSel),
        campaigns.length ? h("label", { class: "stack" }, "Recorded campaign", campaignSel) : "");
    }
    async function load() {
      close(); const my = seq;
      cancel = new AbortController(); const signal = cancel.signal;
      const current = () => valid() && my === seq;
      tabs.replaceChildren(...[["tasks", "Tasks"], ["log", "Log"], ["agents", "Agents"], ["results", "Results"]].map(([k, label]) =>
        h("button", { type: "button", class: selected === k ? "on" : "", onclick: () => { selected = k; load(); } }, label)));
      panel.replaceChildren(card(null, skeleton(6)));
      const query = new URLSearchParams({ run_id: String(start.record_id), campaign: campaignId, start_id: String(start.id) });
      if (ctx.owner) query.set("owner", ctx.owner);
      const qs = "?" + query.toString(), q = "&" + query.toString();
      const trace = `/apps/${enc(ctx.name)}/run-data${qs}&kind=${selected === "agents" ? "turns" : "events"}`;
      try {
        if (selected === "log") {
          const raw = `/apps/${enc(ctx.name)}/log/raw?run_id=${start.id}&download=false${ctx.q}`;
          const response = await request(raw + "&preview=true", { signal });
          const text = await response.text();
          if (!current()) return;
          const view = h("div", {}, h("pre", { class: "raw-content history-log" }, text));
          view.prepend(h("div", { class: "actions" }, ...viewerTools(view, { title: `Log · ${when(start.started)}`, rawUrl: "/api" + raw })),
            response.headers.get("x-flux-truncated") ? h("p", { class: "muted" }, "Preview: first 1 MiB. Raw opens the complete output.") : "");
          panel.replaceChildren(card(null, view));
        } else if (!campaignId) {
          panel.replaceChildren(card(null, empty("No retained campaign data for this start. Its text output is in Log.")));
        } else if (selected === "results") {
          const r = await api(`/apps/${enc(ctx.name)}/results${qs}`, { signal });
          if (!current()) return;
          panel.replaceChildren(h("p", { class: "muted small" }, "Campaign results through this start, including earlier measured designs when resumed. Verdicts use the recorded objectives."),
            r.campaign && r.designs.length ? resultsView({ ...ctx, qs, q }, r).show("results") : card(null, empty("No results retained for this campaign.")));
        } else if (selected === "agents") {
          await agentsView({ ...ctx, body: panel, qs, q, history: true, still: () => current });
          if (current()) panel.prepend(h("div", { class: "actions" }, h("a", { class: "btn small", href: "/api" + trace, target: "_blank", rel: "noopener" }, "Raw transcript")));
        } else {
          const listeners = {};
          const replay = { on: (kind, callbacks) => { listeners[kind] = callbacks; }, restart: () => load() };
          tree = liveTree(ctx.base, qs, () => {}, replay);
          const following = history.starts.slice().reverse().find(s => s.id > start.id);
          tree.ended(start.ended || (start.running ? null : (following || {}).started || Date.now() / 1000));
          const view = h("div", { class: "history-tasks" }, h("div", { class: "split" },
            card(null, tree.tree, { cls: "tree-card" }), h("div", { class: "side-col" }, card(null, tree.detail, { cls: "detail-card" }), card(null, tree.stand))));
          view.prepend(h("div", { class: "actions" }, ...viewerTools(view, { title: `Tasks · ${when(start.started)}`, rawUrl: "/api" + trace })));
          panel.replaceChildren(view);
          const response = await request(trace, { signal });
          const reader = response.body.getReader(), decoder = new TextDecoder(); let pending = "", count = 0;
          while (current()) {
            const { value, done } = await reader.read();
            pending += decoder.decode(value || new Uint8Array(), { stream: !done });
            const lines = pending.split("\n"); pending = lines.pop();
            if (done && pending) { lines.push(pending); pending = ""; }
            for (const line of lines) {
              let row; try { row = JSON.parse(line); } catch (_) { continue; }
              if (!current()) break;
              listeners.events.onData(row); count++;
            }
            if (done) break;
          }
          if (!current()) { await reader.cancel(); return; }
          listeners.events.onState("live"); listeners.events.onReady(); tree.draw();
          if (!count) panel.replaceChildren(card(null, empty("No task journal retained for this start. Its text output is in Log.")));
        }
      } catch (error) {
        if (error.name === "AbortError" || !current() || error.message === "log in") return;
        if (tree) { tree.close(); tree = null; }
        panel.replaceChildren(card(null, empty(`This historical view is unavailable: ${error.message}`)));
      }
    }
    drawControls();
    ctx.body.replaceChildren(card("Run history", [controls, h("p", { class: "muted small" }, "Recorded output, including every retained pass, tool and agent turn."), tabs]), panel);
    await load();
  }
  return { show, close };
}
