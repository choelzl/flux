// Flux web (D1095): an answer's Markdown (D705), built as components -- never HTML: headings, lists, tables,
// code blocks, quotes, paragraphs with **bold**, *italics* and `code`. A code block, highlighted; prose with fenced
// code in it.

import { html } from "../view.js";
import { guessLang, tokens } from "../highlight.js";

/** A <pre> with the text highlighted (read-only views): its tokens as spans. */
export function Code({ text, lang, cls = "val tall code", k }) {
  return html`<pre class=${cls + " hl"} data-k=${k || null}>${spans(text, lang || guessLang(text))}</pre>`;
}

const FENCE = { systemverilog: "sv", verilog: "sv", py: "python", python: "python", yaml: "yaml", yml: "yaml", json: "json",
  c: "c", cpp: "c", "c++": "c", vhdl: "vhdl", bash: "sh", sh: "sh" };
const spans = (text, lang) => tokens(text, lang).map(([c, t]) => c ? html`<span class=${"tk-" + c}>${t}</span>` : t);

/** Prose with fenced code blocks (a prompt, a reply): the prose as text, each ```lang block highlighted. */
export function Prose({ text, cls = "val tall" }) {
  return html`<pre class=${cls + " hl"}>${String(text ?? "").split(/(```[\w+-]*\n[\s\S]*?```)/g).map(part => {
    const m = /^```([\w+-]*)\n([\s\S]*?)```$/.exec(part);
    return !m ? part : [html`<span class="tk-punc">${"```" + m[1] + "\n"}</span>`, spans(m[2], FENCE[(m[1] || "").toLowerCase()] || guessLang(m[2])), html`<span class="tk-punc">${"```"}</span>`];
  })}</pre>`;
}

const Inline = ({ t }) => {
  const out = [], re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g;
  let at = 0, m;
  while ((m = re.exec(t))) {
    if (m.index > at) out.push(t.slice(at, m.index));
    const x = m[0];
    out.push(x.startsWith("`") ? html`<code>${x.slice(1, -1)}</code>` : x.startsWith("**") ? html`<strong>${x.slice(2, -2)}</strong>` : html`<em>${x.slice(1, -1)}</em>`);
    at = m.index + x.length;
  }
  if (at < t.length) out.push(t.slice(at));
  return out;
};

export function Markdown({ text }) {
  const lines = String(text || "").split("\n"), out = [];
  for (let i = 0; i < lines.length;) {
    const l = lines[i];
    if (/^```/.test(l)) {
      const lang = l.slice(3).trim(), body = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]); i++) body.push(lines[i]);
      i++; out.push(html`<${Code} text=${body.join("\n")} lang=${lang === "systemverilog" || lang === "verilog" ? "sv" : lang} cls="val code" />`);
    } else if (/^#{1,6}\s/.test(l)) {
      const n = l.match(/^#+/)[0].length, t = l.replace(/^#+\s*/, "");
      out.push(n <= 2 ? html`<h3><${Inline} t=${t} /></h3>` : html`<h4><${Inline} t=${t} /></h4>`); i++;
    } else if (/^\s*\|.*\|\s*$/.test(l)) {
      const rows = [];
      for (; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++) rows.push(lines[i].trim().slice(1, -1).split("|").map(c => c.trim()));
      const body = rows.filter(r => !r.every(c => /^:?-{2,}:?$/.test(c)));
      const label = j => (body[0][j] || "").replace(/[*_`]/g, "");      // D1149: each cell its column's name, for a phone (as Table's)
      out.push(html`<div class="scroll-x"><table class="list compact md"><thead><tr>${body[0].map(c => html`<th><${Inline} t=${c} /></th>`)}</tr></thead>
        <tbody>${body.slice(1).map(r => html`<tr>${r.map((c, j) => html`<td data-label=${label(j)}><${Inline} t=${c} /></td>`)}</tr>`)}</tbody></table></div>`);
    } else if (/^\s*([-*+]|\d+\.)\s+/.test(l)) {
      const ordered = /^\s*\d+\./.test(l), items = [];
      for (; i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i]); i++) items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ""));
      const lis = items.map(x => html`<li><${Inline} t=${x} /></li>`);
      out.push(ordered ? html`<ol>${lis}</ol>` : html`<ul>${lis}</ul>`);
    } else if (/^>\s?/.test(l)) {
      const q = [];
      for (; i < lines.length && /^>\s?/.test(lines[i]); i++) q.push(lines[i].replace(/^>\s?/, ""));
      out.push(html`<blockquote><${Inline} t=${q.join(" ")} /></blockquote>`);
    } else if (!l.trim()) { i++; } else {
      // a line that looks like an unfinished block (e.g. "| ...") is a paragraph's
      const para = [l]; i++;
      for (; i < lines.length && lines[i].trim() && !/^(```|#{1,6}\s|\s*\||\s*([-*+]|\d+\.)\s|>)/.test(lines[i]); i++) para.push(lines[i]);
      out.push(html`<p><${Inline} t=${para.join(" ")} /></p>`);
    }
  }
  return html`<div class="md">${out}</div>`;
}
