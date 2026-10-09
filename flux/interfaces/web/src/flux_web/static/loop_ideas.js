// A campaign's ideas and the actual trials that evaluated them. All text stays literal.
import { act, api, card, empty, enc, h } from "./ui.js";
import { viewerTools } from "./viewer.js";

export async function ideasView(ctx) {
  const { name, qs, body, still } = ctx;
  const ok = still();
  const data = await api(`/apps/${enc(name)}/ideas${qs}`);
  if (!ok()) return;
  const content = h("div", { class: "ideas-view" });
  const table = (labels, rows, cls) => h("div", { class: "scroll-x" }, h("table", { class: cls },
    h("thead", {}, h("tr", {}, ...labels.map(s => h("th", {}, s)))), h("tbody", {}, ...rows)));
  const evaluations = (rows) => {
    const records = rows.map(r => h("tr", {}, h("td", {}, r.pass ?? "—"), h("td", {}, r.design),
        h("td", {}, r.stage || "—"), h("td", {}, r.status),
        h("td", {}, Object.entries(r.metrics || {}).map(([key, value]) => `${key}=${value}`).join(", "),
          r.error ? h("pre", {}, r.error) : "", h("small", { class: "muted" }, r.at || ""))));
    return h("details", {}, h("summary", {}, `${rows.length} evaluation${rows.length === 1 ? "" : "s"}`),
      table(["Pass", "Design", "Stage", "Outcome", "Measurements / feedback"], records, "idea-evaluations"));
  };
  const rows = data.ideas.map(idea => h("tr", {},
      h("td", {}, h("strong", {}, idea.title), h("small", { class: "muted" }, idea.id),
        idea.part ? h("small", { class: "muted" }, `Part: ${idea.part}`) : ""),
      h("td", {}, h("p", {}, idea.hypothesis), idea.test ? h("p", { class: "muted" }, `Test: ${idea.test}`) : ""),
      h("td", {}, idea.status), h("td", {}, idea.evaluations.length ? evaluations(idea.evaluations) : "Not tested yet")));
  content.append(rows.length ? table(["Idea", "Hypothesis", "State", "Evidence"], rows, "ideas-table")
    : empty("No ideas recorded yet. Agents can save hypotheses and alternatives with their drafts."));
  body.replaceChildren(card("Ideas", [h("div", { class: "bar" },
    h("span", { class: "muted" }, "Measured means evidence is available; it does not mean the idea improved the objectives."),
    act("Refresh", () => ideasView(ctx), { cls: "small" }),
    ...viewerTools(content, { title: "Ideas notebook", rawText: JSON.stringify(data, null, 2) })), content]));
}
