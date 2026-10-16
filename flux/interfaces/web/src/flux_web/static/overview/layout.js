// Overview layouts belong to the account, across loops and browsers (D692; D1095: the editor is
// overview/LayoutEditor.js).
import { cleanup, me, navSeq } from "../state.js";
import { api, toast } from "../ui.js";
import { html } from "../view.js";
import { openModal } from "../components/Modal.js";

const loadOverviewLayout = () => api("/preferences/overview");

/** The editor in a dialog of its own: true once saved, false when cancelled (or the page left). */
async function editOverviewLayout() {
  if (me?.impersonator) return false;
  const account = me, page = navSeq;
  const [prefs, { LayoutEditor }, { overviewMockData }] = await Promise.all([loadOverviewLayout(), import("./LayoutEditor.js"), import("./mock.js")]);
  if (me !== account || navSeq !== page) return false;
  return new Promise(resolve => {
    let saving = false, m = null;
    const finish = saved => {
      if (!m) return;
      m.close(); m = null;
      const at = cleanup.indexOf(cancel); if (at >= 0) cleanup.splice(at, 1);
      resolve(saved);
    };
    const cancel = () => finish(false);
    const save = async layout => {
      saving = true;
      try { await api("/preferences/overview", { method: "PUT", body: layout }); } finally { saving = false; }
      if (m) { finish(true); toast("Layout saved", "ok"); }
    };
    cleanup.push(cancel);
    m = openModal(html`<${LayoutEditor} prefs=${prefs} example=${overviewMockData()} save=${save} cancel=${cancel} />`,
      { cls: "dlg overview-layout-dialog", labelledby: "overview-layout-title", onCancel: () => { if (!saving) cancel(); },
        opened: el => el.querySelector(".overview-layout-editor select")?.focus() });
  });
}

export { editOverviewLayout, loadOverviewLayout };
