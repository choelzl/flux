// Flux web, configure (D1116): the Prompt step -- the name, the language (the tools usually tell it), what to make,
// the rules every design follows, and the files that go with it, each said for what it is to the loop: named by a
// command, read by the model.

import { cx, html } from "../view.js";
import { Tag } from "../components/Tone.js";
import { FilesBody } from "./FilesPanel.js";
import { Pick, Text } from "./fields.js";

export function PromptStep({ M, state, edit, store, opts, invalid }) {
  const implied = M.impliedLanguage(state);              // D832: optional -- the tools usually tell
  const langs = [["", implied ? `from the tools: ${implied}` : "from the tools (none tells yet)"], ...M.LANGUAGES.map(l => [l, l]), ["other", "other…"]];
  const named = new Set(M.namedFiles(state)), reads = M.list(state.knowledgeFiles);
  const toggle = path => edit(() => {
    const now = M.list(state.knowledgeFiles);
    state.knowledgeFiles = (now.includes(path) ? now.filter(p => p !== path) : [...now, path]).join(", ");
  });
  const more = f => html`<span class="cfg-file-tags">${named.has(f.path) && !reads.includes(f.path) ? html`<${Tag}>named by a command<//>` : ""}${
    f.path.startsWith("library/") ? html`<${Tag} title="a paper: summed up once, the summary in every prompt">library<//>`
    : html`<button type="button" class=${cx("tag cfg-reads", reads.includes(f.path) && "on")} aria-pressed=${String(reads.includes(f.path))}
      title="The model reads it with every request (knowledge.files)" onClick=${() => toggle(f.path)}>the model reads it</button>`}</span>`;
  return html`<div class="cfg-grid2">
      <${Text} label=${opts.nameLabel || "Name"} k="id" invalid=${invalid} value=${state.id} set=${v => edit(() => { state.id = v; })}
        placeholder=${opts.namePlaceholder || "my_design"} hint="Letters, digits, - and _" />
      <${Pick} label="Language" value=${state.language} options=${langs} set=${v => edit(() => { state.language = v; })} />
      ${state.language === "other" ? html`<${Text} label="Which language?" value=${state.languageOther} placeholder="ini" set=${v => edit(() => { state.languageOther = v; })} />` : ""}</div>
    <${Text} area rows=${3} label="What to make" k="statement" invalid=${invalid} value=${state.statement} set=${v => edit(() => { state.statement = v; })}
      placeholder="Say it as you would to an engineer: what it does, what matters." />
    <${Text} area rows=${2} label="Rules every design must follow" value=${state.contract} set=${v => edit(() => { state.contract = v; })}
      placeholder="Names, ports, what is not allowed" />
    <section class="cfg-sec"><h3>Files</h3><${FilesBody} store=${store} more=${more} /></section>`;
}
