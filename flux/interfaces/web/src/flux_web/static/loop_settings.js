// Flux web: a loop's Settings tab (but the problem, configure.js's) -- its variables, its sharing,
// the admin's advanced settings, its maintenance, Reset and Delete (D892: out of loopPage).

import { act, ago, api, card, confirmDialog, dialog, enc, h, skeleton, toast } from "./ui.js";
import { advancedCard, envEditor, envTable, permissionChoice } from "./loops.js";
import { route } from "./app.js";
import { measurementPreferences } from "./result_table.js";
import { me } from "./state.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** Who else sees or edits a loop (D701): the owner or admin shares it with a user to watch (its runs and
    outputs) or to edit (change and run it too); everyone else with it sees the list. */
async function sharingCard(name, qs = "") {
  const sh = await api(`/apps/${enc(name)}/shares${qs}`).catch(() => null);
  if (!sh) return "";
  const canShare = sh.can_share;
  const set = async (user, perm) => { await api(`/apps/${enc(name)}/shares${qs}`, { method: "PUT", body: { user, perm } }); toast(perm ? `Shared with ${user}: ${perm}` : `No longer shared with ${user}`, "ok"); route(); };
  // D723: one grid -- who, what they may do, the action -- the row to add in the same columns
  const CAN = { watch: "Can watch", edit: "Can edit" };
  const access = (attrs, cur) => h("select", attrs, Object.entries(CAN).map(([p, label]) => h("option", { value: p, selected: cur === p }, label)));
  const person = (u) => h("div", { class: "share-who" }, h("span", { class: "share-av", "aria-hidden": "true" }, u.slice(0, 1).toUpperCase()), h("span", { class: "strong" }, u));
  const rows = sh.shares.flatMap(x => [person(x.user),
    canShare ? access({ "aria-label": `What ${x.user} may do`, onchange: (e) => set(x.user, e.target.value) }, x.perm) : h("span", { class: "pill" }, CAN[x.perm] || x.perm),
    canShare ? h("button", { type: "button", class: "small", onclick: () => set(x.user, null) }, "Remove") : h("span", {})]);
  const none = h("p", { class: "muted share-none" }, canShare ? "Not shared." : "Shared with nobody else.");
  if (!canShare) return card("Sharing", sh.shares.length ? h("div", { class: "share-grid" }, rows) : none, { cls: "loop-sharing" });
  const free = sh.users.filter(u => !sh.shares.some(x => x.user === u));
  const who = h("select", { id: "share-user", "aria-label": "Share with" }, h("option", { value: "" }, free.length ? "Choose a user…" : "No other user"), free.map(u => h("option", { value: u }, u)));
  const how = access({ id: "share-perm", "aria-label": "What they may do" }, "watch");
  if (!free.length) who.disabled = how.disabled = true;
  const add = act("Share", async () => { if (!who.value) { toast("Choose a user.", "warn"); return; } await set(who.value, how.value); }, { cls: "primary small" });
  if (!free.length) add.disabled = true;
  return card("Sharing", [
    sh.shares.length ? "" : none,
    h("div", { class: "share-grid" }, rows, h("div", { class: "share-add-sep" }), who, how, add),
    h("p", { class: "muted small share-note" }, h("strong", {}, "Watch"), ": view only. ",
      h("strong", {}, "Edit"), ": change it; also start/stop when allowed to run (uses the owner's keys).")], { cls: "loop-sharing" });
}

/** The loop's settings (D697): its environment variables over the user's and the server's, and
    what only an admin sets -- the sandbox and its limits. */
async function settingsView(ctx) {
  const { name, qs, body, info, mine, isOwner } = ctx;
  const ok = ctx.still();                               // D919
  body.replaceChildren(card(null, skeleton(7)));
  const [e, results] = await Promise.all([api(`/apps/${enc(name)}/env${qs}`), api(`/apps/${enc(name)}/results${qs}`)]);
  if (ctx.tab !== "Settings" || !ok()) return;
  const varsCard = card("Environment variables", [
    h("p", { class: "muted" }, isOwner ? "This loop's variables win over yours and the server's."
      : `This loop's variables win over ${info.owner}'s and the server's.`),
    envEditor(e.loop, mine ? async (v) => { await api(`/apps/${enc(name)}/env`, { method: "PUT", body: v }); settingsView(ctx); } : null, "loop"),
    e.user.length || e.server.length ? h("div", { class: "blk" }, h("h3", {}, "Under them"),
      envTable([...e.server.map(x => ({ ...x, from: "the server" })), ...e.user.map(x => ({ ...x, from: isOwner ? "yours (Account)" : `${info.owner}'s (their Account)` }))],
        new Set(e.loop.map(x => x.name)))) : ""]);
  const danger = isOwner ? card("Reset or delete this loop", [
    h("p", { class: "muted" }, "Reset clears generated data; choose what to keep in the confirmation. Source files, settings and sharing stay. Delete removes the entire loop. Both are permanent."),
    h("div", { class: "form-actions" }, ctx.st.running ? h("span", { class: "muted" }, "Stop it first.") : [act("Reset", async () => {
      const plan = await api(`/apps/${enc(name)}/reset`);
      const keeps = plan.keep_options.map(o => h("label", { class: "check" },
        h("input", { type: "checkbox", "data-reset-keep": o.key }), o.label));
      const selected = () => keeps.map(l => l.querySelector("input")).filter(i => i.checked).map(i => i.dataset.resetKeep);
      const folders = h("ul", { class: "reset-folders" }), history = h("p", {});
      const draw = () => {
        const keep = selected(), removed = plan.folders.filter(f => !keep.includes(f.key));
        folders.replaceChildren(...(removed.length ? removed.map(f => h("li", {}, h("code", {}, f.path), h("div", { class: "muted small" }, f.what)))
          : [h("li", { class: "muted" }, "No folders will be removed.")]));
        history.textContent = keep.includes("history") ? "Results, logs and saved run history stay. The last check status will be cleared."
          : plan.history + " will also be cleared.";
      };
      keeps.forEach(l => l.querySelector("input").addEventListener("change", draw));
      draw();
      const warning = h("div", {},
        h("p", {}, h("strong", {}, "This cannot be undone."), " Data listed below will be permanently removed."),
        h("h3", {}, "Keep"), h("div", { class: "stack" }, keeps),
        h("p", {}, "These folders and everything inside them will be removed:"),
        folders, history,
        h("p", {}, "Your problem document, source files, library, settings and sharing stay. Stop any running agents first."));
      if (!await dialog(`Reset ${name}?`, warning, [["Cancel", false], ["Reset", true, "danger solid"]])) return;
      await api(`/apps/${enc(name)}/reset`, { method: "POST", body: { keep: selected() } });
      toast(`${name} reset`, "ok"); route();
    }, { cls: "danger" }), act("Delete", async () => {
      if (!await confirmDialog(`Delete ${name}?`, "Its document, files, record and log go. This cannot be undone.", { ok: "Delete", danger: true })) return;
      await api(`/apps/${enc(name)}`, { method: "DELETE" }); toast(`${name} deleted`, "ok"); location.hash = "#/";
    }, { cls: "danger" })])], { cls: "danger-card" }) : "";
  // D885: the loop's own clean-up, for whoever may change it; what each did last on this loop
  const mt = await api(`/apps/${enc(name)}/maintenance${qs}`).catch(() => null);
  const mtCard = mt && mt.tasks.length ? card("Maintenance", [h("p", { class: "muted" }, ctx.st.running ? "Stop the loop first: a running loop is never touched." : "Run on this loop now; the admin's schedule runs them on every loop."),
    // D896: a table, as Admin › Maintenance's
    h("div", { class: "scroll-x" }, h("table", { class: "list compact mt-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Task"), h("th", {}, "Last run on this loop"), h("th", {}, ""))),
      h("tbody", {}, mt.tasks.map(t => {
        const last = h("td", { "data-label": "Last run on this loop", class: "mt-said" });
        const said = (x) => last.replaceChildren(x ? h("span", { class: x.ok ? "" : "bad", title: x.said }, ago(x.t), " · ", x.said) : h("span", { class: "muted" }, "never"));
        said(t.last);
        const run = act("Run", async () => {
          const got = await api(`/apps/${enc(name)}/maintenance/${t.key}${qs}`, { method: "POST" });
          said(got); toast(`${t.title}: ${got.said}`, got.ok ? "ok" : "bad");
        }, { cls: "small" });
        run.disabled = !!ctx.st.running;
        return h("tr", {}, h("td", { "data-label": "Task", title: t.what }, h("strong", {}, t.title)), last, h("td", { class: "right mt-acts" }, run));
      }))))]) : "";
  const shares = await sharingCard(name, qs);
  if (!ok()) return;
  const ownership = isOwner || me.role === "admin" ? await ownershipCard(ctx) : "";
  if (!ok()) return;
  const measurements = card("Measurements", [h("p", { class: "muted" }, "Visible controls table columns. Main selects metrics for loop lists, decision summaries and default charts. % formats summaries as percent change from the matching baseline, otherwise P90 performance of accepted designs. Tables have their own Absolute/Relative toggle. Saved with this project and shared across browsers; calculations still use every metric. Only owners, editors and admins can save changes."),
    results.metrics?.length ? measurementPreferences(ctx, results.metrics, results.metric_groups || {}) : h("p", { class: "muted" }, "No measurements yet.")], { cls: "measurement-preferences" });
  body.replaceChildren(measurements, varsCard, shares, ownership, advancedCard(e, async (adv) => {
    await api(`/apps/${enc(name)}/advanced${qs}`, { method: "PUT", body: adv });      // D833: quiet, as it changes
  }), mtCard, danger);
}

async function ownershipCard(ctx) {
  const { name, qs, info } = ctx;
  const plan = await api(`/apps/${enc(name)}/ownership${qs}`);
  if (!plan.can_manage) return "";
  const rename = act("Rename…", async () => {
    const input = h("input", { id: "loop-rename-to", value: name, maxlength: 60, autocomplete: "off", required: true });
    const to = await dialog(`Rename ${name}`, h("div", { class: "stack" },
      h("label", { class: "stack" }, "New loop name", input),
      h("p", { class: "muted" }, "Files, results, history, variables, sharing and admin settings are kept. Links to the old name will change.")),
    [["Cancel", null], ["Rename", () => input.value.trim(), "primary"]]);
    if (!to) return;
    const got = await api(`/apps/${enc(name)}/rename${qs}`, { method: "POST", body: { to } });
    toast(`${name} renamed to ${got.name}`, "ok");
    location.hash = `${got.owner === me.name ? "#" : `#/u/${enc(got.owner)}`}/app/${enc(got.name)}/settings`;
  }, { cls: "small" });
  rename.id = "loop-rename";
  const transfer = act("Transfer…", async () => {
    const current = me.role === "admin" ? await api(`/apps/${enc(name)}/ownership${qs}`) : plan;
    const keep = permissionChoice(current.permissions, "transfer-keep-permissions");
    const who = h("select", { id: "loop-transfer-user", "aria-label": "New owner" },
      h("option", { value: "" }, "Choose a user…"), current.users.map(u => h("option", { value: u }, u)));
    const input = h("input", { id: "loop-transfer-to", value: name, maxlength: 60, autocomplete: "off", required: true });
    const got = await dialog(`Transfer ${name}`, h("div", { class: "stack" },
      h("label", { class: "stack" }, "New owner", who), h("label", { class: "stack" }, "Loop name for the new owner", input),
      h("p", {}, "Files, results, full run history, caches and loop variables (including secrets) move to the new owner. Your account's model keys and agent logins stay with your account."),
      h("p", {}, keep.input ? "Sharing and other admin settings are cleared. Choose below whether to keep the mount, sandbox and network overrides. The recipient uses their own account settings; the former owner loses access unless it is shared back. Admins retain access."
        : "Sharing and admin overrides are cleared, including special mounts and sandbox exemptions. The new owner uses their own account settings and the server's sandbox defaults. You lose access unless they share it back; admins retain access."), keep.el),
    [["Cancel", null], ["Transfer", () => ({ user: who.value, to: input.value.trim(), keep_permissions: !!keep.input?.checked }), "primary"]]);
    if (!got) return;
    if (!got.user || !got.to) { toast("Choose a user and a loop name.", "warn"); return; }
    const moved = await api(`/apps/${enc(name)}/transfer${qs}`, { method: "POST", body: got });
    toast(`${name} transferred to ${moved.owner}`, "ok");
    location.hash = me.role === "admin" ? `#/u/${enc(moved.owner)}/app/${enc(moved.name)}/settings` : "#/";
  }, { cls: "small" });
  transfer.id = "loop-transfer";
  rename.disabled = !!ctx.st.running;
  transfer.disabled = !!ctx.st.running || !plan.users.length;
  return card("Name & ownership", [h("p", { class: "muted" }, `Owned by ${info.owner}. Stop the loop and its agents before renaming or transferring it.`),
    h("div", { class: "form-actions" }, rename, transfer)], { cls: "loop-ownership" });
}

export { settingsView, sharingCard };
