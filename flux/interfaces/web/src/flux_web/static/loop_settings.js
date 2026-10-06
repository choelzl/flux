// Flux web: a loop's Settings tab (but the problem, configure.js's) -- its variables, its sharing,
// the admin's advanced settings, its maintenance, Delete (D892: out of loopPage).

import { act, ago, api, card, confirmDialog, enc, h, skeleton, toast } from "./ui.js";
import { advancedCard, envEditor, envTable } from "./loops.js";
import { route } from "./app.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** Who else sees or edits a loop (D701): the owner shares it with a user to watch (its runs and
    outputs) or to edit (change and run it too); everyone else with it sees the list. */
async function sharingCard(name, isOwner) {
  const sh = await api(`/apps/${enc(name)}/shares`).catch(() => null);
  if (!sh) return "";
  const set = async (user, perm) => { await api(`/apps/${enc(name)}/shares`, { method: "PUT", body: { user, perm } }); toast(perm ? `Shared with ${user}: ${perm}` : `No longer shared with ${user}`, "ok"); route(); };
  // D723: one grid -- who, what they may do, the action -- the row to add in the same columns
  const CAN = { watch: "Can watch", edit: "Can edit" };
  const access = (attrs, cur) => h("select", attrs, Object.entries(CAN).map(([p, label]) => h("option", { value: p, selected: cur === p }, label)));
  const person = (u) => h("div", { class: "share-who" }, h("span", { class: "share-av", "aria-hidden": "true" }, u.slice(0, 1).toUpperCase()), h("span", { class: "strong" }, u));
  const rows = sh.shares.flatMap(x => [person(x.user),
    isOwner ? access({ "aria-label": `What ${x.user} may do`, onchange: (e) => set(x.user, e.target.value) }, x.perm) : h("span", { class: "pill" }, CAN[x.perm] || x.perm),
    isOwner ? h("button", { type: "button", class: "small", onclick: () => set(x.user, null) }, "Remove") : h("span", {})]);
  const none = h("p", { class: "muted share-none" }, isOwner ? "Not shared." : "Shared with nobody else.");
  if (!isOwner) return card("Sharing", sh.shares.length ? h("div", { class: "share-grid" }, rows) : none);
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
      h("strong", {}, "Edit"), ": also change, start and stop it (runs use your keys).")]);
}

/** The loop's settings (D697): its environment variables over the user's and the server's, and
    what only an admin sets -- the sandbox and its limits. */
async function settingsView(ctx) {
  const { name, qs, body, info, mine, isOwner } = ctx;
  const ok = ctx.still();                               // D919
  body.replaceChildren(card(null, skeleton(7)));
  const e = await api(`/apps/${enc(name)}/env${qs}`);
  if (ctx.tab !== "Settings" || !ok()) return;
  const varsCard = card("Environment variables", [
    h("p", { class: "muted" }, isOwner ? "This loop's variables win over yours and the server's."
      : `This loop's variables win over ${info.owner}'s and the server's.`),
    envEditor(e.loop, mine ? async (v) => { await api(`/apps/${enc(name)}/env`, { method: "PUT", body: v }); settingsView(ctx); } : null, "loop"),
    e.user.length || e.server.length ? h("div", { class: "blk" }, h("h3", {}, "Under them"),
      envTable([...e.server.map(x => ({ ...x, from: "the server" })), ...e.user.map(x => ({ ...x, from: isOwner ? "yours (Account)" : `${info.owner}'s (their Account)` }))],
        new Set(e.loop.map(x => x.name)))) : ""]);
  const danger = isOwner ? card("Delete this loop", [h("p", { class: "muted" }, "Its document, files, record and log go. This cannot be undone."),
    h("div", { class: "form-actions" }, ctx.st.running ? h("span", { class: "muted" }, "Stop it first.") : act("Delete", async () => {
      if (!await confirmDialog(`Delete ${name}?`, "Its document, files, record and log go. This cannot be undone.", { ok: "Delete", danger: true })) return;
      await api(`/apps/${enc(name)}`, { method: "DELETE" }); toast(`${name} deleted`, "ok"); location.hash = "#/";
    }, { cls: "danger" }))], { cls: "danger-card" }) : "";
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
  const shares = await sharingCard(name, isOwner);
  if (!ok()) return;
  body.replaceChildren(varsCard, shares, advancedCard(e, async (adv) => {
    await api(`/apps/${enc(name)}/advanced${qs}`, { method: "PUT", body: adv });      // D833: quiet, as it changes
  }), mtCard, danger);
}

export { settingsView, sharingCard };
