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
  // D1042: an evaluation's numbers as chips that wrap (the first six, the rest on demand), its error one
  // line until opened -- a long reading neither widens the table nor leaves a tall gap between rows
  const metrics = (m) => {
    const chips = Object.entries(m || {}).map(([key, value]) => h("span", { class: "idea-metric", title: `${key} ${value}` }, `${key} ${value}`));
    return chips.length ? h("div", { class: "idea-metrics" }, ...chips.slice(0, 6),
      chips.length > 6 ? h("details", { class: "idea-more" }, h("summary", {}, `+${chips.length - 6} more`), h("div", { class: "idea-metrics" }, ...chips.slice(6))) : "") : "";
  };
  const failure = (text) => {
    const [first, ...rest] = String(text).split("\n");
    return rest.some(x => x.trim()) ? h("details", { class: "idea-error" }, h("summary", {}, first), h("pre", {}, rest.join("\n")))
      : h("div", { class: "idea-error" }, first);
  };
  const evaluations = (rows) => {
    const records = rows.map(r => h("tr", {}, h("td", {}, r.pass ?? "—"), h("td", {}, r.design),
        h("td", {}, r.stage || "—"), h("td", {}, r.status),
        h("td", {}, metrics(r.metrics), r.error ? failure(r.error) : "", h("small", { class: "muted" }, r.at || ""))));
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
