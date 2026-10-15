// Flux web: logging in, an invite's page, one's account and agent logins (D889: split out of app.js).

import { setMe } from "./state.js";
import { api, enc, pageShow } from "./ui.js";
import { html, node } from "./view.js";
import { LoginCard } from "./components/Login.js";
import { Account } from "./account/Account.js";
import { route } from "./app.js";

// ================================================================ pages
async function loginPage() {
  const show = pageShow();
  const submit = async f => { setMe(await api("/login", { method: "POST", body: { name: f.elements.name.value, password: f.elements.password.value } })); location.hash = "#/"; route(); };
  // D699: a phone's keyboard neither capitalises nor corrects a name
  show(node(html`<${LoginCard} sub="Log in to your loops." submit=${submit}>
    <label class="stack">Name<input name="name" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" required /></label>
    <label class="stack">Password<input name="password" type="password" autocomplete="current-password" required /></label>
    <button class="primary wide" type="submit">Log in</button><//>`));
}

/** D818: a link to set one's password -- for an invited user, or after a reset; one use, a week. */
async function invitePage(token) {
  const show = pageShow();
  let got;
  try { got = await api(`/invite/${enc(token)}`); }
  catch (x) { show(node(html`<${LoginCard}><p class="err">${x.message}</p><a class="btn" href="#/login">Log in</a><//>`)); return; }
  const submit = async f => {
    const pw = f.elements.pw.value;
    if (pw.length < 10) throw new Error("At least 10 characters.");
    if (pw !== f.elements.pw2.value) throw new Error("The two are not the same.");
    setMe(await api(`/invite/${enc(token)}`, { method: "POST", body: { text: pw } })); location.hash = "#/"; route();
  };
  const invite = got.kind === "invite";
  show(node(html`<${LoginCard} sub=${invite ? `Welcome, ${got.name}: choose your password.` : `${got.name}: choose a new password.`} submit=${submit}
      after=${html`<p class="muted small">${`Valid once, until ${new Date(got.expires * 1000).toLocaleString()}.`}</p>`}>
    <label class="stack">Password (10 or more characters)<input name="pw" id="inv-pw" type="password" autocomplete="new-password" required minlength="10" /></label>
    <label class="stack">Again<input name="pw2" id="inv-pw2" type="password" autocomplete="new-password" required /></label>
    <button class="primary wide" type="submit">${invite ? "Set it and log in" : "Change it and log in"}</button><//>`));
}

async function accountPage() {
  const show = pageShow();
  const [st, myEnv, mine] = await Promise.all([api("/settings"), api("/env"), api("/usage").catch(() => null)]);
  show(node(html`<${Account} st=${st} myEnv=${myEnv} mine=${mine} />`));
}

export { accountPage, invitePage, loginPage };
