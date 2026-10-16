// Flux web (D691, D696, D702, D856, D1095): the bar over every page -- the mark, where one can go, the bell and its
// list, the theme, one's name and Log out (on a phone one menu), and while an admin views as a user, a banner.

import { Ago, cx, html, useChanges, useEffect, useState } from "../view.js";
import { api, toast } from "../ui.js";
import { me, setMe } from "../state.js";
import { ICONS } from "../components/Icons.js";
import { Tag } from "../components/Tone.js";
import { bellChanges, bellState, clearBell, pollLoops, seenAll } from "./bell.js";
import { usePoll } from "../components/Load.js";
import { recall, remember } from "../stored.js";

/** Where one can go; the tab of the page one is on, marked. */
export function Nav({ here }) {
  if (!me) return null;
  const link = (href, text, on) => html`<a href=${href} class=${on ? "on" : ""}>${text}</a>`;
  return html`${link("#/", "Loops", here === "#/" || here.startsWith("#/app") || here.startsWith("#/u/"))}
    ${link("#/configure", "New loop", here.startsWith("#/configure") || here === "#/new")}${me.role === "admin" ? link("#/admin", "Admin", here.startsWith("#/admin")) : ""}`;
}

function Bell() {
  const [open, setOpen] = useState(false), [perm, setPerm] = useState(() => "Notification" in window ? Notification.permission : null);
  useChanges(bellChanges);
  usePoll(async () => { await pollLoops(); return document.hidden ? 30000 : 10000; }, [], { first: 10000 });
  useEffect(() => { const close = () => setOpen(false); document.addEventListener("click", close); return () => document.removeEventListener("click", close); }, []);
  const b = bellState(), canAsk = perm === "default";
  return html`<div class="bell-wrap"><button class="bell" title="Notifications" aria-label="Notifications" onClick=${e => { e.stopPropagation(); setOpen(!open); seenAll(); }}>
      ${ICONS.bell()}${b.unread ? html`<${Tag} count k="var(--bad)" cls="unread">${String(b.unread)}<//>` : ""}</button>
    <div class="bell-menu" hidden=${!open} onClick=${e => e.stopPropagation()}><div class="bell-head"><strong>Notifications</strong>
        ${canAsk ? html`<button class="link" onClick=${async () => setPerm(await Notification.requestPermission())}>Allow desktop notifications</button>` : ""}
        ${b.list.length ? html`<button class="link" onClick=${clearBell}>Clear</button>` : ""}</div>
      ${b.list.length ? b.list.map(n => html`<a key=${`${n.t}|${n.text}`} class=${`bell-item ${n.kind}`} href=${n.href || "#/"} onClick=${() => setOpen(false)}><span>${n.text}</span><small><${Ago} t=${n.t} /></small></a>`)
        : html`<p class="muted">No notifications.</p>`}</div></div>`;
}

// ---- the theme: system, light or dark, remembered in this browser (D691)
const THEMES = { system: "◐ System", light: "☀ Light", dark: "☾ Dark" };
export const theme = () => recall("flux-theme", "system");
export function applyTheme(t) {
  remember("flux-theme", t);
  // D1107: always named -- the chosen one, or the system's (index.html follows the system's as it changes)
  document.documentElement.dataset.theme = t !== "system" ? t : matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** D856: on a phone the theme, the name and Log out fold into one menu; the bell stays in the bar. */
export function Who() {
  const [open, setOpen] = useState(false), [t, setT] = useState(theme);
  useEffect(() => { const close = e => { if (!e.target.closest("#who")) setOpen(false); }; document.addEventListener("click", close); return () => document.removeEventListener("click", close); }, []);
  const next = () => { const order = ["system", "light", "dark"], n = order[(order.indexOf(theme()) + 1) % 3]; applyTheme(n); setT(n); };
  const logout = async () => { await api("/logout", { method: "POST" }).catch(() => {}); setMe(null); location.hash = "#/login"; };
  return html`${me ? html`<${Bell} />` : ""}
    <button class="small acct-btn" type="button" aria-label="Account menu" aria-expanded=${String(open)} onClick=${e => { e.stopPropagation(); setOpen(!open); }}>☰</button>
    <div class=${cx("who-items", open && "open")} onClick=${e => { if (e.target.closest("a, button")) setOpen(false); }}>
      <button class="small theme" title="Theme: system, light or dark" onClick=${next}>${THEMES[t]}</button>
      ${me ? html`<a href="#/account" class="me" title=${me.name}>${me.name}</a><button class="small" onClick=${logout}>Log out</button>` : ""}</div>`;
}

/** While an admin views as a user: who, read-only, the way back. */
export function Impersonation({ view }) {
  if (!view) return null;
  const back = async () => {
    try { await api("/impersonation", { method: "DELETE" }); location.hash = "#/admin/users"; location.reload(); }
    catch (x) { if (x.message !== "log in") toast(x.message, "bad"); }
  };
  return html`<span>Viewing as <strong>${view.name}</strong> · Read-only</span><button type="button" class="small" onClick=${back}>Return to admin</button>`;
}
