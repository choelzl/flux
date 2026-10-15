// LiveAlt (D1095): the quiet steps a pass wrote as notes (a scope list, a scripted search), said in words,
// and a document's setting as the person wrote it. Plain functions.

/** D1066: which scope a pass took, from its design's own record of it. */
export const scopeOf = (model, membership, p) => {
  for (const x of model.nodes.values()) if ((membership.get(x.id) ?? null) === p && x.output?.scope) return String(x.output.scope);
  return "";
};
const what = n => String(n.name).replace(/^\s*\w+\s*:\s*/, "");
/** D1067: a line's few words for a note -- "whole design", "took TSUB". */
export const noteShort = (n, took) => {
  const k = n.output?.scopes;
  return /^scope list$/i.test(what(n)) && k != null ? (Number(k) ? (took ? `took ${took}` : `${k} scopes`) : "whole design") : what(n);
};
/** D1064: a note said as what it means -- a scope list of none is the whole design, not "found nothing". */
export const noteSaid = (n, took) => {
  const k = n.output?.scopes;
  if (/^scope list$/i.test(what(n)) && k != null) return Number(k) ? `${k} scope${Number(k) === 1 ? "" : "s"} listed; the pass ${took ? `took ${took}` : "picks one"}.`
    : "The pass works on the whole design.";   // D1094: a loop without scopes is not told of them
  return what(n);
};
export const noteName = what;

/** D1094: a document's value as YAML lines -- the words the person wrote, not JSON. */
export function asYaml(v, pad = "") {
  const one = x => typeof x === "string" ? (/^[\w./{} -]*$/.test(x) && x.trim() === x && x ? x : JSON.stringify(x)) : String(x);
  if (Array.isArray(v)) return v.map(x => x !== null && typeof x === "object" ? `${pad}-\n${asYaml(x, pad + "  ")}` : `${pad}- ${one(x)}`).join("\n");
  if (v !== null && typeof v === "object") return Object.entries(v).map(([k, x]) => x !== null && typeof x === "object"
    ? `${pad}${k}:\n${asYaml(x, pad + "  ")}` : `${pad}${k}: ${one(x)}`).join("\n");
  return pad + one(v);
}
