// Flux web: logging in, an invite's page, one's account and agent logins (D889: split out of app.js; D1095: each a
// route component).

import { setMe } from "./state.js";
import { api, enc } from "./ui.js";
import { html, Notice } from "./view.js";
import { PageRead, useLoad } from "./components/Load.js";
import { LoginCard } from "./components/Login.js";
import { Account } from "./account/Account.js";
import { route } from "./app.js";

function LoginRoute() {
  const submit = async f => { setMe(await api("/login", { method: "POST", body: { name: f.elements.name.value, password: f.elements.password.value } })); location.hash = "#/"; route(); };
  // D699: a phone's keyboard neither capitalises nor corrects a name
  return html`<${LoginCard} sub="Log in." submit=${submit}>
    <label class="stack">Name<input name="name" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" required /></label>
    <label class="stack">Password<input name="password" type="password" autocomplete="current-password" required /></label>
    <button class="primary wide" type="submit">Log in</button><//>`;
}

/** D818: a link to set one's password -- for an invited user, or after a reset; one use, a week. */
function InviteRoute({ token }) {
  const read = useLoad(() => api(`/invite/${enc(token)}`), token);
  if (!read.got) return null;
  if (read.got.error) return html`<${LoginCard}><${Notice} tone="bad" what="This link does not work" why=${read.got.error} action=${html`<a class="btn small" href="#/login">Log in</a>`} /><//>`;
  const got = read.got.d, invite = got.kind === "invite";
  const submit = async f => {
    const pw = f.elements.pw.value;
    if (pw.length < 10) throw new Error("At least 10 characters.");
    if (pw !== f.elements.pw2.value) throw new Error("Passwords differ.");
    setMe(await api(`/invite/${enc(token)}`, { method: "POST", body: { text: pw } })); location.hash = "#/"; route();
  };
  return html`<${LoginCard} sub=${invite ? `Welcome, ${got.name}: choose a password.` : `${got.name}: choose a new password.`} submit=${submit}
      after=${html`<p class="muted small">${`Valid once, until ${new Date(got.expires * 1000).toLocaleString()}.`}</p>`}>
    <label class="stack">Password (10+ characters)<input name="pw" id="inv-pw" type="password" autocomplete="new-password" required minlength="10" /></label>
    <label class="stack">Again<input name="pw2" id="inv-pw2" type="password" autocomplete="new-password" required /></label>
    <button class="primary wide" type="submit">${invite ? "Set and log in" : "Change and log in"}</button><//>`;
}

const AccountRoute = () => html`<${PageRead} load=${() => Promise.all([api("/settings"), api("/env"), api("/usage").catch(() => null)])}>
  ${([st, myEnv, mine]) => html`<${Account} st=${st} myEnv=${myEnv} mine=${mine} />`}<//>`;

export { AccountRoute, InviteRoute, LoginRoute };
