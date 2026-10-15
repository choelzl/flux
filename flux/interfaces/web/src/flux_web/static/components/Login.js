// Flux web (D699, D818, D1095): the pages before one is logged in -- the mark, Flux, a line, a form whose
// failure is said under it.

import { html, useEffect, useRef, useState } from "../view.js";
import { Logo } from "./Icons.js";

const head = html`<div class="login-mark"><${Logo} size=${56} /></div><h1>Flux</h1>`;

/** The card: `sub` the line under the name; `submit(form)` throws what to say; `children` the fields and the button, `after` what stands under the failure. */
export function LoginCard({ sub, submit, children, after = null }) {
  const [err, setErr] = useState(""), form = useRef(null);
  useEffect(() => { form.current?.querySelector("input")?.focus(); }, []);
  const go = async e => {
    e.preventDefault(); setErr("");
    try { await submit(e.currentTarget); } catch (x) { setErr(x.message); }
  };
  if (!submit) return html`<div class="card login">${head}${children}</div>`;
  return html`<form class="card login" ref=${form} onSubmit=${go}>${head}${sub ? html`<p class="sub">${sub}</p>` : ""}${children}<p class="err">${err}</p>${after}</form>`;
}
