// Flux web (D683-D689): hash-routed pages over /api. A loop -- an application -- is running or not;
// a start resumes it from its record. Text goes in as text, never as HTML -- so nothing a run prints can
// inject script.
//
// D889: the pages are modules; state.js holds what they share. D1095: they are Preact components (view.js);
// the bar is shell/Bar.js, the bell shell/bell.js. Here: the routing.

import { cleanup, me, navSeq, pageOwner, setMe, setNavSeq, setPageOwner, setPageRefresh } from "./state.js";
import { api, show } from "./ui.js";
import { Card, html, node, render } from "./view.js";
import { Logo } from "./components/Icons.js";
import { Impersonation, Nav, Who, applyTheme, theme } from "./shell/Bar.js";
import { bellFor, pollLoops } from "./shell/bell.js";
import { appsPage, newPage } from "./loops.js";
import { loopPage } from "./loop_page.js";
import { configurePage } from "./configure.js";
import { adminPage } from "./admin.js";
import { accountPage, invitePage, loginPage } from "./account.js";

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
  const seq = navSeq + 1;
  setNavSeq(seq);
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
  // D1081: a navigation made while this one waited for /me is the page -- this one draws nothing (its page,
  // started after the wait, would count as the latest and draw over the one gone to: the loops over Results)
  if (seq !== navSeq) return;
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
  } catch (x) { if (x.message !== "log in") show(node(html`<${Card}><p class="err">${x.message}</p><//>`)); }
}
applyTheme(theme());
render(html`<${Logo} size=${24} /><span>flux</span>`, document.querySelector(".brand"));    // D696: the mark

/** The bar over every page (shell/Bar.js): drawn again with each page -- who is logged in, where one is. */
function drawNav() {
  const view = me?.impersonator ? me : null, box = document.getElementById("impersonation");
  if (box) { box.hidden = !view; render(html`<${Impersonation} view=${view} />`, box); }    // a page without the bar (one replaced, as the phone checks do)
  bellFor(me ? me.name : null);
  render(html`<${Nav} here=${location.hash || "#/"} />`, document.getElementById("nav"));
  render(html`<${Who} />`, document.getElementById("who"));
}
function drawImpersonation(view) {
  const box = document.getElementById("impersonation");
  if (box) { box.hidden = !view; render(html`<${Impersonation} view=${view} />`, box); }
}
window.addEventListener("hashchange", route);
route();

export { route };
