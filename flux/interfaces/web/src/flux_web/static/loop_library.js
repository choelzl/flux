// D955: the library's digests a loop's record holds -- each paper, who digested it, the text the prompts read.
import { act, api, card, empty, enc, h } from "./ui.js";
import { viewerTools } from "./viewer.js";

export async function libraryView(ctx) {
  const { name, qs, body, still } = ctx;
  const ok = still();
  const data = await api(`/apps/${enc(name)}/digests${qs}`);
  if (!ok()) return;
  const base = (p) => String(p).split("/").pop();
  const content = h("div", { class: "library-view" }, data.digests.length
    ? data.digests.map(d => h("details", { class: "digest" },
        h("summary", {}, h("strong", {}, base(d.source)), " ",
          h("small", { class: "muted" }, [d.model || "?", `${Number(d.chars || 0).toLocaleString()} chars read`, d.reused ? "kept from before" : ""].filter(Boolean).join(" · "))),
        h("p", { class: "muted small mono" }, d.source),
        h("pre", { class: "digest-text" }, d.digest)))
    : empty("No digests yet: with the library on, each Setup digests up to 8 papers of the loop's library/ folder."));
  body.replaceChildren(card("Library", [h("div", { class: "bar" },
    h("span", { class: "muted" }, `${data.digests.length} paper(s) digested`),
    act("Refresh", () => libraryView(ctx), { cls: "small" }),
    ...viewerTools(content, { title: "Library digests", rawText: JSON.stringify(data, null, 2) })), content]));
}
