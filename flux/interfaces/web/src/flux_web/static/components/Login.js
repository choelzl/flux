// Flux web (D699, D818, D1095): the pages before one is logged in -- the mark, Flux, a line, a form whose
// failure is said under it.

import { html, useEffect, useRef, useState, Notice } from "../view.js";
import { Logo } from "./Icons.js";

const head = html`<div class="login-mark"><${Logo} size=${56} /></div><h1>Flux</h1>`;

const cap = s => s ? `${s[0].toUpperCase()}${s.slice(1)}${/[.?!]$/.test(s) ? "" : "."}` : "";

/** What the server refused, one notice on every page before logging in: its words as a title and a sentence --
    "wrong name or password (five failures …)" or "this link has been used or has expired: ask an admin for a new one". */
export function Refusal({ text }) {
  const t = String(text || "").trim(), m = /^(.*?)(?::\s+|\s+\((.*)\)$)(.*)$/.exec(t);
  const [what, why] = m ? [m[1], m[2] || m[3]] : [t, ""];
  return html`<${Notice} tone="bad" role="alert" what=${cap(what)} why=${cap(why)} />`;
}

/** The card: `sub` the line under the name; `submit(form)` throws what to say; `children` the fields and the button, `after` what stands under the failure. */
export function LoginCard({ sub, submit, children, after = null }) {
  const [err, setErr] = useState(""), form = useRef(null);
  useEffect(() => { form.current?.querySelector("input")?.focus(); }, []);
  const go = async e => {
    e.preventDefault(); setErr("");
    try { await submit(e.currentTarget); } catch (x) { setErr(x.message); }
  };
  if (!submit) return html`<div class="card login">${head}${children}</div>`;
  return html`<form class="card login" ref=${form} onSubmit=${go}>${head}${sub ? html`<p class="sub">${sub}</p>` : ""}${children}${err ? html`<${Refusal} text=${err} />` : ""}${after}</form>`;
}
