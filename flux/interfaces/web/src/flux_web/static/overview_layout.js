// Overview layouts belong to the account, across loops and browsers (D692; D1095: the editor is
// overview/LayoutEditor.js).
import { cleanup, me, navSeq } from "./state.js";
import { api, h, toast, toasts } from "./ui.js";
import { html, render } from "./view.js";

const loadOverviewLayout = () => api("/preferences/overview");

/** The editor in a dialog of its own: true once saved, false when cancelled (or the page left). */
async function editOverviewLayout() {
  if (me?.impersonator) return false;
  const account = me, page = navSeq;
  const [prefs, { LayoutEditor }, { overviewMockData }] = await Promise.all([loadOverviewLayout(), import("./overview/LayoutEditor.js"), import("./overview_mock.js")]);
  if (me !== account || navSeq !== page) return false;
  return new Promise(resolve => {
    const d = h("dialog", { class: "dlg overview-layout-dialog", "aria-labelledby": "overview-layout-title" });
    let saving = false;
    const finish = saved => {
      if (!d.isConnected) return;
      render(null, d); d.close(); if (toasts.parentNode === d) document.body.append(toasts); d.remove();
      const at = cleanup.indexOf(cancel); if (at >= 0) cleanup.splice(at, 1);
      resolve(saved);
    };
    const cancel = () => finish(false);
    const save = async layout => {
      saving = true;
      try { await api("/preferences/overview", { method: "PUT", body: layout }); } finally { saving = false; }
      if (d.isConnected) { finish(true); toast("Overview layout saved for your account", "ok"); }
    };
    cleanup.push(cancel);
    d.addEventListener("cancel", e => { e.preventDefault(); if (!saving) cancel(); });
    document.body.append(d);
    render(html`<${LayoutEditor} prefs=${prefs} example=${overviewMockData()} save=${save} cancel=${cancel} />`, d);
    d.showModal();
    d.querySelector(".overview-layout-editor select")?.focus();
  });
}

export { editOverviewLayout, loadOverviewLayout };
