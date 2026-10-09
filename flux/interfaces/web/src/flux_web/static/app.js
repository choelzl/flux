// Flux web (D683-D689): hash-routed pages over /api. A loop -- an application -- is running or not;
// a start resumes it from its record. Every node is built with h() -- text goes in as text, never as
// HTML -- so nothing a run prints can inject script.
//
// D889: the pages are modules -- ui.js (building blocks), loops.js, live.js, configure.js, admin.js,
// account.js, charts.js; state.js holds what they share. Here: the bell, the routing, the theme and the bar.

import { cleanup, me, navSeq, pageOwner, pageRefresh, setMe, setNavSeq, setPageOwner, setPageRefresh } from "./state.js";
import { ago, api, appHref, card, h, show, toast } from "./ui.js";
import { bellIcon, logo } from "./charts.js";
import { appsPage, newPage } from "./loops.js";
import { loopPage } from "./loop_page.js";
import { configurePage } from "./configure.js";
import { adminPage } from "./admin.js";
import { accountPage, invitePage, loginPage } from "./account.js";

// ================================================================ notifications (D688, D689)
const bell = { list: [], seen: new Map(), unread: 0, primed: false, user: null };
// D702: the bell is each user's -- kept under their name, started afresh when another logs in here
const bellKey = () => `flux-notes:${bell.user}`;
function bellFor(user) {
  if (bell.user === user) return;
  bell.user = user; bell.seen = new Map(); bell.unread = 0; bell.primed = false;
  try { bell.list = user ? JSON.parse(localStorage.getItem(bellKey()) || "[]") : []; } catch (_) { bell.list = []; }
  try { localStorage.removeItem("flux-notes"); } catch (_) { /* the old, shared key */ }
}
function notify(text, kind, href) {
  bell.list.unshift({ text, kind, href, t: Date.now() / 1000 });
  bell.list = bell.list.slice(0, 30); bell.unread++;
  try { localStorage.setItem(bellKey(), JSON.stringify(bell.list)); } catch (_) {}
  toast(text, kind, { timeout: 9000, href });
  if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
    try { new Notification("Flux", { body: text, tag: href }); } catch (_) {}
  }
  drawBell();
}
/** Every 10 s: a loop that stopped (finished, failed, stopped) or whose agent asks. */
let polling = false, pollBeat = 0;
async function pollLoops() {
  if (!me || polling) return;                       // D917: one at a time -- a slow answer is not asked again
  if (document.hidden && pollBeat++ % 3) return;    // a hidden tab: every 30 s, still in time for a desktop notice
  polling = true;
  try { await pollOnce(); } finally { polling = false; }
}
async function pollOnce() {
  bellFor(me.name);
  let loops;
  try { loops = await api("/loops"); } catch (_) { return; }
  let changed = false;
  try { for (const n of await api("/notices")) notify(n.text, n.kind || "info", n.href || ""); } catch (_) { /* the next poll */ }
  for (const l of loops) {
    const id = `${l.owner || ""}/${l.app}`, label = l.owner ? `${l.owner}'s ${l.app}` : l.app;   // D702: shared loops too
    const was = bell.seen.get(id);
    const key = l.question ? `q:${l.question.asked}` : "";
    if (!was || was.running !== l.running) changed = true;
    if (bell.primed && was) {
      const href = appHref(l.owner, l.app);
      if (was.running && !l.running) {
        if (l.failed) notify(`${label} failed`, "bad", href);
        else if (l.stopped) notify(`${label} stopped`, "warn", href);
        else notify(`${label} finished its passes`, "ok", href);
      }
      if (key && key !== was.key) notify(`${label}: the agent asks a question`, "warn", href);
    }
    bell.seen.set(id, { running: l.running, key });
  }
  if (changed && bell.primed && pageRefresh) pageRefresh().catch(() => {});
  bell.primed = true;
}
setInterval(pollLoops, 10000);
const bellBtn = h("button", { class: "bell", title: "Notifications", "aria-label": "Notifications" });
const bellMenu = h("div", { class: "bell-menu", hidden: true });
function drawBell() {
  bellBtn.replaceChildren(bellIcon(), bell.unread ? h("span", { class: "badge" }, String(bell.unread)) : "");
  const canAsk = "Notification" in window && Notification.permission === "default";
  bellMenu.replaceChildren(
    h("div", { class: "bell-head" }, h("strong", {}, "Notifications"),
      canAsk ? h("button", { class: "link", onclick: async () => { await Notification.requestPermission(); drawBell(); } }, "Allow desktop notifications") : "",
      bell.list.length ? h("button", { class: "link", onclick: () => { bell.list = []; bell.unread = 0; localStorage.removeItem(bellKey()); drawBell(); } }, "Clear") : ""),
    ...(bell.list.length ? bell.list.map(n => h("a", { class: `bell-item ${n.kind}`, href: n.href || "#/", onclick: () => { bellMenu.hidden = true; } },
      h("span", {}, n.text), h("small", {}, ago(n.t)))) : [h("p", { class: "muted" }, "No notifications.")]));
}
bellBtn.addEventListener("click", (e) => { e.stopPropagation(); bellMenu.hidden = !bellMenu.hidden; bell.unread = 0; drawBell(); });
document.addEventListener("click", (e) => { if (!bellMenu.hidden && !bellMenu.contains(e.target)) bellMenu.hidden = true; });

// ================================================================ routing
// ---- D754: on a phone nothing scrolls sideways -- a list's rows stack, each value under its column's name
function labelTables(root) {
  for (const t of root.querySelectorAll("table.list")) {
    const heads = [...t.querySelectorAll(":scope > thead > tr:last-child > th")].map(th => th.dataset.label || th.textContent.trim());   // D926: not a sort arrow
    if (!heads.some(Boolean)) continue;
    for (const tr of t.querySelectorAll(":scope > tbody > tr")) {
      [...tr.children].forEach((td, i) => { if (heads[i] && td.dataset.label !== heads[i]) td.dataset.label = heads[i]; });
    }
  }
}
{
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; labelTables(document.body); });
  }).observe(document.body, { childList: true, subtree: true });
}

async function route() {
  setNavSeq(navSeq + 1);
  for (const f of cleanup.splice(0)) f();
  setPageRefresh(null);
  for (const d of document.querySelectorAll("dialog.dlg")) d.dispatchEvent(new Event("cancel"));   // a dialog belongs to its page
  const hash = location.hash || "#/";
  if (hash === "#/login") {
    drawNav(); loginPage();
    const view = await api("/impersonation").catch(() => null);
    if (location.hash === hash) drawImpersonation(view);
    return;
  }
  { const m = hash.match(/^#\/invite\/([A-Za-z0-9_-]+)$/); if (m) { drawNav(); return invitePage(m[1]); } }   // D818: before any login
  if (!me) { try { setMe(await api("/me")); pollLoops(); } catch (_) { return; } }
  drawNav();
  try {
    let m;
    setPageOwner(null);
    // D713: a loop's address is its tab and what is under it: /live/log, /files/workbench, /settings/problem/edit
    if ((m = hash.match(/^#\/app\/([^/]+)((?:\/[a-z0-9-]+)*)$/))) return await loopPage(decodeURIComponent(m[1]), null, m[2].slice(1));
    if ((m = hash.match(/^#\/u\/([^/]+)\/app\/([^/]+)((?:\/[a-z0-9-]+)*)$/))) { setPageOwner(decodeURIComponent(m[1])); return await loopPage(decodeURIComponent(m[2]), pageOwner, m[3].slice(1)); }
    if (hash === "#/new") return await newPage();
    if ((m = hash.match(/^#\/configure(?:\/([a-z]+))?$/))) return await configurePage(null, null, m[1]);
    if ((m = hash.match(/^#\/admin(?:\/([a-z]+))?$/)) && me.role === "admin") return await adminPage(m[1] || "");
    if (hash === "#/account") return await accountPage();
    return await appsPage();
  } catch (x) { if (x.message !== "log in") show(card(null, h("p", { class: "err" }, x.message))); }
}
// ---- the theme: system, light or dark, remembered in this browser (D691)
const THEMES = { system: "◐ System", light: "☀ Light", dark: "☾ Dark" };
function theme() { try { return localStorage.getItem("flux-theme") || "system"; } catch (_) { return "system"; } }
function applyTheme(t) {
  if (t === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  try { localStorage.setItem("flux-theme", t); } catch (_) {}
}
applyTheme(theme());
document.querySelector(".brand").replaceChildren(logo(24), h("span", {}, "flux"));    // D696: the mark
const themeBtn = h("button", { class: "small theme", title: "Theme: system, light or dark" });
themeBtn.addEventListener("click", () => { const order = ["system", "light", "dark"]; applyTheme(order[(order.indexOf(theme()) + 1) % 3]); themeBtn.textContent = THEMES[theme()]; });
themeBtn.textContent = THEMES[theme()];

function drawImpersonation(view) {
  const box = document.getElementById("impersonation");
  box.hidden = !view;
  box.replaceChildren(...(view ? [h("span", {}, "Viewing as ", h("strong", {}, view.name), " · Read-only"),
    h("button", { type: "button", class: "small", onclick: async () => {
      try {
        await api("/impersonation", { method: "DELETE" });
        location.hash = "#/admin/users"; location.reload();
      } catch (x) { if (x.message !== "log in") toast(x.message, "bad"); }
    } }, "Return to admin")] : []));
}
function drawNav() {
  drawImpersonation(me?.impersonator ? me : null);
  bellFor(me ? me.name : null);
  const here = location.hash || "#/";
  const link = (href, text, on) => h("a", { href, class: on ? "on" : "" }, text);
  document.getElementById("nav").replaceChildren(...(me ? [
    link("#/", "Loops", here === "#/" || here.startsWith("#/app") || here.startsWith("#/u/")),
    link("#/configure", "New loop", here.startsWith("#/configure") || here === "#/new"),
    me.role === "admin" ? link("#/admin", "Admin", here.startsWith("#/admin")) : ""] : []));
  drawBell();
  // D856: on a phone the theme, the name and Log out fold into one menu; the bell stays in the bar
  const items = h("div", { class: "who-items" }, themeBtn, ...(me ? [h("a", { href: "#/account", class: "me", title: me.name }, me.name),
    h("button", { class: "small", onclick: async () => { await api("/logout", { method: "POST" }).catch(() => {}); setMe(null); location.hash = "#/login"; } }, "Log out")] : []));
  const menuBtn = h("button", { class: "small acct-btn", type: "button", "aria-label": "Account menu", "aria-expanded": "false",
    onclick: (e) => { e.stopPropagation(); const open = !items.classList.contains("open"); items.classList.toggle("open", open); menuBtn.setAttribute("aria-expanded", String(open)); } }, "☰");
  items.addEventListener("click", (e) => { if (e.target.closest("a, button")) { items.classList.remove("open"); menuBtn.setAttribute("aria-expanded", "false"); } });
  document.getElementById("who").replaceChildren(...(me ? [h("div", { class: "bell-wrap" }, bellBtn, bellMenu)] : []), menuBtn, items);
}
document.addEventListener("click", (e) => {                 // D856: a tap elsewhere closes the phone menu
  const open = document.querySelector("#who .who-items.open");
  if (open && !e.target.closest("#who")) { open.classList.remove("open"); const b = document.querySelector("#who .acct-btn"); if (b) b.setAttribute("aria-expanded", "false"); }
});
window.addEventListener("hashchange", route);
route();

export { route };
