// Flux web (D688, D689, D702, D917, D1095): the bell -- what happened to one's loops while one looked elsewhere: a
// loop that stopped (finished, failed, stopped), one whose agent asks, a notice the server sent; each also a toast,
// and a desktop notification when the page is hidden. Each user's own, kept in this browser.

import { api, appHref, toast } from "../ui.js";
import { me, pageRefresh } from "../state.js";
import { changes } from "../view.js";
import { recallJson, remember } from "../stored.js";

const bell = { list: [], seen: new Map(), unread: 0, primed: false, user: null };
const rung = changes(), emit = rung.emit;
const key = () => `flux-notes:${bell.user}`;
const keep = () => remember(key(), bell.list);

/** D702: kept under the user's name, started afresh when another logs in here. */
export function bellFor(user) {
  if (bell.user === user) return;
  bell.user = user; bell.seen = new Map(); bell.unread = 0; bell.primed = false;
  bell.list = user ? recallJson(key(), []) : [];
  emit();
}
function notify(text, kind, href) {
  bell.list = [{ text, kind, href, t: Date.now() / 1000 }, ...bell.list].slice(0, 30); bell.unread++;
  keep();
  toast(text, kind, { timeout: 9000, href });
  if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
    try { new Notification("Flux", { body: text, tag: href }); } catch (_) { /* not allowed here */ }
  }
  emit();
}

let polling = false, beat = 0;
async function once() {
  bellFor(me.name);
  let loops;
  try { loops = await api("/loops"); } catch (_) { return; }
  let changed = false;
  try { for (const n of await api("/notices")) notify(n.text, n.kind || "info", n.href || ""); } catch (_) { /* the next poll */ }
  for (const l of loops) {
    const id = `${l.owner || ""}/${l.app}`, label = l.owner ? `${l.owner}'s ${l.app}` : l.app;   // D702: shared loops too
    const was = bell.seen.get(id), asks = l.question ? `q:${l.question.asked}` : "";
    if (!was || was.running !== l.running) changed = true;
    if (bell.primed && was) {
      const href = appHref(l.owner, l.app);
      if (was.running && !l.running) {
        if (l.failed) notify(`${label} failed`, "bad", href);
        else if (l.stopped) notify(`${label} stopped`, "warn", href);
        else notify(`${label} finished its passes`, "ok", href);
      }
      if (asks && asks !== was.key) notify(`${label}: the agent asks a question`, "warn", href);
    }
    bell.seen.set(id, { running: l.running, key: asks });
  }
  if (changed && bell.primed && pageRefresh) pageRefresh().catch(() => {});
  bell.primed = true;
}
/** Every 10 s: one at a time (D917) -- a hidden tab every 30 s, still in time for a desktop notice. */
export async function pollLoops() {
  if (!me || polling) return;
  if (document.hidden && beat++ % 3) return;
  polling = true;
  try { await once(); } finally { polling = false; }
}
setInterval(pollLoops, 10000);

export const bellState = () => bell;
/** What the bell holds changed: for `useChanges`. */
export const bellChanges = rung;
export const seenAll = () => { bell.unread = 0; emit(); };
export const clearBell = () => { bell.list = []; bell.unread = 0; remember(key(), null); emit(); };
