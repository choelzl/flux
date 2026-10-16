// Flux web, configure (D1116): the Prompt step -- the name, the language (the tools usually tell it), what to make,
// the rules every design follows, and the files that go with it, each said for what it is to the loop: named by a
// command, read by the model (chosen in Graph › Library, D1136), a paper.

import { html } from "../view.js";
import { Tag } from "../components/Tone.js";
import { FilesBody } from "./FilesPanel.js";
import { Pick, Text } from "./fields.js";

export function PromptStep({ M, state, edit, store, opts, invalid }) {
  const implied = M.impliedLanguage(state);              // D832: optional -- the tools usually tell
  const W = M.WORDS.prompt;
  const langs = [["", implied ? `${W.fromTools}: ${implied}` : W.fromTools], ...M.LANGUAGES.map(l => [l, l]), ["other", W.other]];
  // D1136: what the model reads is checked in Graph › Library; here a file says it is read, named or a paper
  const reads = M.list(state.knowledgeFiles).map(p => p.replace(/\/$/, "")), named = new Set(M.namedFiles(state));
  const read = p => reads.some(r => p === r || p.startsWith(`${r}/`));
  const more = f => html`<span class="cfg-file-tags">${f.path.startsWith("library/") ? html`<${Tag} title="a paper: summed up once, the summary in every prompt">library<//>`
    : read(f.path) ? html`<${Tag} title="every prompt reads it (Graph › Library)">read<//>` : named.has(f.path) ? html`<${Tag}>named<//>` : ""}</span>`;
  return html`<div class="cfg-grid2">
      <${Text} label=${opts.nameLabel || "Name"} k="id" invalid=${invalid} value=${state.id} set=${v => edit(() => { state.id = v; })}
        placeholder=${opts.namePlaceholder || "my_design"} hint=${W.nameHint} />
      <${Pick} label="Language" value=${state.language} options=${langs} set=${v => edit(() => { state.language = v; })} />
      ${state.language === "other" ? html`<${Text} label=${W.languageName} value=${state.languageOther} placeholder="ini" set=${v => edit(() => { state.languageOther = v; })} />` : ""}</div>
    <${Text} area rows=${3} label=${W.statement} k="statement" invalid=${invalid} value=${state.statement} set=${v => edit(() => { state.statement = v; })}
      placeholder=${W.statementPlaceholder} />
    <${Text} area rows=${2} label=${W.rules} value=${state.contract} set=${v => edit(() => { state.contract = v; })}
      placeholder=${W.rulesPlaceholder} />
    <section class="cfg-sec"><h3>Files</h3><${FilesBody} store=${store} more=${more} /></section>`;
}
