// Overview layouts belong to the account, across loops and browsers.
import { cleanup, me, navSeq } from "./state.js";
import { api, h, toast } from "./ui.js";

const loadOverviewLayout = () => api("/preferences/overview");

async function editOverviewLayout() {
  if (me?.impersonator) return false;
  const account = me, page = navSeq;
  const [prefs, { renderOverview }, { overviewMockData }] = await Promise.all([
    loadOverviewLayout(), import("./loop_overview.js"), import("./overview_mock.js"),
  ]);
  if (me !== account || navSeq !== page) return false;
  let draft = structuredClone(prefs.layout), saving = false;
  return new Promise(resolve => {
    const content = h("div", { class: "overview-layout-editor" });
    const preview = h("div", { class: "overview-layout-preview", inert: true });
    const example = overviewMockData();
    const error = h("p", { class: "err", role: "alert" });
    const d = h("dialog", { class: "dlg overview-layout-dialog", "aria-labelledby": "overview-layout-title" });
    const finish = saved => {
      if (!d.isConnected) return;
      d.close(); d.remove();
      const at = cleanup.indexOf(cancel); if (at >= 0) cleanup.splice(at, 1);
      resolve(saved);
    };
    const cancel = () => finish(false);
    cleanup.push(cancel);
    const button = (label, title, fn, disabled = false) => h("button", {
      type: "button", class: label === "Add" ? "small overview-layout-add-button" : "small overview-layout-control", title, "aria-label": title, disabled,
      onclick: () => { fn(); draw(); },
    }, label);
    const shift = (list, index, offset) => {
      [list[index], list[index + offset]] = [list[index + offset], list[index]];
    };
    function list(ids, catalog, column = null) {
      const small = column == null, used = small ? draft.stats : draft.columns.flat();
      const rows = ids.map((id, index) => {
        const select = h("select", { title: catalog[id], "aria-label": `${small ? "Small" : "Large"} card ${index + 1}${small ? "" : ` in column ${column + 1}`}` },
          Object.entries(catalog).filter(([key]) => key === id || !used.includes(key)).map(([key, label]) => h("option", { value: key }, label)));
        select.value = id;
        select.addEventListener("change", () => { ids[index] = select.value; draw(); });
        return h("li", { class: "overview-layout-row", "data-layout-card": id },
          h("span", { class: "overview-layout-index", "aria-hidden": true }, index + 1), select,
          h("div", { class: "overview-layout-actions" },
            button("↑", `Move ${catalog[id]} up`, () => shift(ids, index, -1), index === 0),
            button("↓", `Move ${catalog[id]} down`, () => shift(ids, index, 1), index === ids.length - 1),
            !small ? button(column === 0 ? "→" : "←", `Move ${catalog[id]} to column ${2 - column}`, () => { ids.splice(index, 1); draft.columns[1 - column].push(id); }) : "",
            button("×", `Remove ${catalog[id]}`, () => ids.splice(index, 1), small && ids.length <= 3)));
      });
      const available = Object.entries(catalog).filter(([id]) => !used.includes(id));
      const add = h("select", { "aria-label": small ? "Add small card" : `Add card to column ${column + 1}` }, available.map(([id, label]) => h("option", { value: id }, label)));
      return h("div", { class: "overview-layout-list", "data-layout-list": small ? "stats" : column },
        h("ol", {}, rows.length ? rows : h("li", { class: "overview-layout-empty muted small" }, "No cards in this column.")),
        h("div", { class: "overview-layout-row overview-layout-add" }, add,
          button("Add", small ? "Add small card" : `Add card to column ${column + 1}`, () => ids.push(add.value), !available.length || small && ids.length >= 5)));
    }
    function draw() {
      const focused = content.contains(document.activeElement) ? document.activeElement : null;
      const card = focused?.matches("select") ? focused.value : focused?.closest("[data-layout-card]")?.dataset.layoutCard;
      const small = focused?.closest("[data-layout-list]")?.dataset.layoutList === "stats";
      const label = focused?.getAttribute("aria-label");
      const heading = (title, count) => h("div", { class: "overview-layout-heading" }, h("h3", {}, title), h("span", { class: "overview-layout-count" }, count));
      content.replaceChildren(
        h("section", { class: "overview-layout-section", "aria-label": "Top cards" },
          heading("Top cards", `${draft.stats.length} / 5`),
          h("p", { class: "muted small" }, "Choose 3–5 cards, ordered left to right."), list(draft.stats, prefs.small_cards)),
        h("section", { class: "overview-layout-section", "aria-label": "Main cards" },
          heading("Main cards", `${draft.columns.flat().length} selected`),
          h("p", { class: "muted small" }, "Order top to bottom. Use ← / → to move between columns."),
          h("div", { class: "overview-layout-columns" }, draft.columns.map((ids, column) =>
            h("section", { class: "overview-layout-column", "aria-label": `${column === 0 ? "Left" : "Right"} column` },
              h("div", { class: "overview-layout-heading" }, h("h4", {}, column === 0 ? "Left column" : "Right column"),
                h("span", { class: "overview-layout-count" }, ids.length)), list(ids, prefs.large_cards, column)))),
          h("p", { class: "muted small overview-layout-mobile-note" }, "On phones, the left column appears first.")));
      renderOverview({ name: "sample", owner: "__overview_mock_data__", qs: "", body: preview, mine: false,
        st: example.state, tab: "Overview", still: () => () => true, goTab: () => {}, drawBody: () => {} },
        example.results, example.notes, example.workbench, example.usage, { layout: draft });
      if (focused) {
        const row = [...content.querySelectorAll("[data-layout-card]")].find(el => el.dataset.layoutCard === card && (el.closest("[data-layout-list]").dataset.layoutList === "stats") === small);
        const action = [...(row || content).querySelectorAll("button, select")].find(el => el.getAttribute("aria-label") === label && !el.disabled);
        (action || row?.querySelector("select") || content.querySelector("select")).focus();
      }
    }
    const save = h("button", { type: "button", class: "primary", onclick: async () => {
      if (saving) return;
      saving = true; save.disabled = true; reset.disabled = true; cancelButton.disabled = true; content.inert = true; error.textContent = "";
      try {
        await api("/preferences/overview", { method: "PUT", body: draft });
        if (d.isConnected) { finish(true); toast("Overview layout saved for your account", "ok"); }
      } catch (e) { error.textContent = e.message; }
      finally { saving = false; save.disabled = false; reset.disabled = false; cancelButton.disabled = false; content.inert = false; }
    } }, "Save");
    const reset = h("button", { type: "button", onclick: () => { draft = structuredClone(prefs.default); draw(); } }, "Defaults");
    const cancelButton = h("button", { type: "button", onclick: cancel }, "Cancel");
    d.append(h("h2", { id: "overview-layout-title" }, "Overview layout"),
      h("p", {}, "Applies to every loop in your account, across browsers."),
      h("div", { class: "overview-customizer" }, content,
        h("section", { class: "overview-preview-panel", "aria-label": "Overview preview with mock data" },
          h("div", { class: "overview-preview-head" }, h("h3", {}, "Preview"), h("span", { class: "pill" }, "Mock Data")),
          h("p", { class: "muted small" }, "Sample values illustrate the layout. Your loops are unchanged until you save."), preview)), error,
      h("div", { class: "dlg-actions" }, reset, cancelButton, save));
    d.addEventListener("cancel", e => { e.preventDefault(); if (!saving) cancel(); });
    draw(); document.body.append(d); d.showModal(); content.querySelector("select").focus();
  });
}

export { editOverviewLayout, loadOverviewLayout };
