// Flux web, files (D1159): paths hidden from the loop's AI agents -- the document's `flow.knowledge.hidden`. A file or
// folder it names (or a folder or pattern above it hides) wears a lock; Hide and Show change the document one path
// at a time on the server, which keeps the rest of it as written.

import { cx, html, useEffect, useState } from "../view.js";
import { loopApi, toast } from "../ui.js";
import { ICONS } from "../components/Icons.js";

const bare = p => String(p || "").trim().replace(/^\/+|\/+$/g, "");
const asRegex = p => new RegExp(`^${bare(p).replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*+/g, ".*").replace(/\?/g, ".")}$`);

/** The pattern of `list` that hides `path` (the path itself, a folder above it, a glob), else null. */
export function hiddenBy(path, list) {
  const segs = bare(path).split("/").filter(Boolean);
  if (!segs.length) return null;
  for (const p of list || []) {
    const re = asRegex(p);
    for (let i = segs.length; i > 0; i--) if (re.test(segs.slice(0, i).join("/"))) return p;
  }
  return null;
}

/** Whether `path` is hidden by its own name (so Show undoes it), not by a folder or pattern above it. */
export const hiddenByName = (path, list) => (list || []).some(p => bare(p) === bare(path));

/** The loop's hidden paths, read once; `set(path, on)` hides or shows one and reads them again. */
export function useHidden(name) {
  const [list, setList] = useState([]);
  const load = () => loopApi(name).api("/hidden").then(r => setList(r.hidden || [])).catch(() => setList([]));
  useEffect(() => { load(); }, [name]);
  const set = async (path, on, quiet = false) => {
    try {
      const r = await loopApi(name).api("/hidden", { method: "PUT", body: { path, hidden: on } });
      setList(r.hidden || []);
      if (!quiet) toast(on ? `${path} hidden from AI` : `${path} shown to AI`, "ok");
      return true;
    } catch (x) { if (x.message !== "log in") toast(x.message, x.status === 409 ? "warn" : "bad", { timeout: 9000 }); return false; }
  };
  return { list, set, reload: load };
}

/** The lock a hidden file or folder wears; `by` the pattern that hides it, when not its own name. */
export const HiddenMark = ({ path, by, cls = "" }) => html`<span class=${cx("hidden-mark", cls)} role="img"
  aria-label="hidden from AI" title=${by && bare(by) !== bare(path) ? `hidden from AI by ${by}` : "hidden from AI"}>${ICONS.lock()}</span>`;
