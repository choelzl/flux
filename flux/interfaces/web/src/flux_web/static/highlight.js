// Syntax highlighting for the web page (D691): a small rule-based tokenizer per language that
// gives tokens a component draws as text and spans -- never HTML -- so a file's text cannot inject anything.
// `tokens(text, lang)` -> [class, text]; `langOf(path)` -> a language or "".

const KW = {
  python: "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case",
  sv: "module endmodule input output inout logic wire reg bit byte int integer signed unsigned parameter localparam assign always always_ff always_comb always_latch initial begin end if else case casez casex endcase default for while repeat forever function endfunction task endtask generate endgenerate genvar posedge negedge or and not typedef struct enum packed union interface endinterface modport package endpackage import export return automatic static const",
  vhdl: "entity architecture is of begin end signal port in out inout std_logic std_logic_vector process if then else elsif case when others for loop generate component map library use all downto to constant variable type array record function return procedure",
  c: "auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while bool true false class public private protected template typename namespace using new delete virtual override nullptr constexpr std include define ifdef ifndef endif pragma",
  sh: "if then else elif fi for while do done case esac function return in export local set unset echo exit",
  tcl: "proc set if else elseif for foreach while return puts expr source",
};
const words = (s) => new RegExp(`\\b(?:${s.split(" ").join("|")})\\b`, "y");

/** [class, sticky regex] in order; the first that matches at a position wins. */
const RULES = {
  yaml: [["com", /#.*/y], ["key", /[ \t]*-?[ \t]*[A-Za-z_][\w.-]*(?=[ \t]*:(?:\s|$))/y], ["str", /"(?:[^"\\\n]|\\.)*"|'(?:[^'\n]|'')*'/y],
         ["num", /-?\b\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\b/y], ["kw", /\b(?:true|false|null|yes|no|on|off)\b/y],
         ["sub", /\{[A-Za-z_]\w*\}/y], ["punc", /[:\-[\]{},|>]/y]],
  python: [["com", /#.*/y], ["str", /(?:[rbfRBF]{0,2})(?:"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')/y],
           ["kw", words(KW.python)], ["fn", /\b[A-Za-z_]\w*(?=\()/y], ["num", /\b\d[\d_]*(?:\.\d+)?(?:[eE][-+]?\d+)?\b|\b0[xob][\da-fA-F_]+\b/y],
           ["deco", /@[\w.]+/y]],
  sv: [["com", /\/\/.*|\/\*[\s\S]*?\*\//y], ["str", /"(?:[^"\\\n]|\\.)*"/y], ["num", /\b\d*'[sS]?[bodhBODH][\da-fA-FxXzZ_?]+|\b\d[\d_]*(?:\.\d+)?\b/y],
       ["kw", words(KW.sv)], ["deco", /`\w+|\$\w+/y], ["fn", /\b[A-Za-z_]\w*(?=\s*\()/y]],
  vhdl: [["com", /--.*/y], ["str", /"[^"\n]*"|'[01XZ-]'/y], ["num", /\b\d+\b/y], ["kw", new RegExp(`\\b(?:${KW.vhdl.split(" ").join("|")})\\b`, "yi")]],
  c: [["com", /\/\/.*|\/\*[\s\S]*?\*\//y], ["deco", /#\s*\w+/y], ["str", /"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)'/y],
      ["num", /\b0[xX][\da-fA-F]+[uUlL]*\b|\b\d+(?:\.\d+)?(?:[eE][-+]?\d+)?[fFuUlL]*\b/y], ["kw", words(KW.c)], ["fn", /\b[A-Za-z_]\w*(?=\s*\()/y]],
  json: [["key", /"(?:[^"\\\n]|\\.)*"(?=\s*:)/y], ["str", /"(?:[^"\\\n]|\\.)*"/y], ["num", /-?\b\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\b/y],
         ["kw", /\b(?:true|false|null)\b/y]],
  md: [["key", /^#{1,6} .*/my], ["str", /`[^`\n]+`/y], ["kw", /\*\*[^*\n]+\*\*/y], ["com", /^>.*/my], ["punc", /^\s*(?:[-*+]|\d+\.)\s/my]],
  sh: [["com", /#.*/y], ["str", /"(?:[^"\\]|\\.)*"|'[^']*'/y], ["deco", /\$\{?\w+\}?/y], ["kw", words(KW.sh)], ["num", /\b\d+\b/y]],
  tcl: [["com", /#.*/y], ["str", /"(?:[^"\\]|\\.)*"/y], ["deco", /\$\w+/y], ["kw", words(KW.tcl)], ["num", /\b\d+(?:\.\d+)?\b/y]],
};

const EXT = { yaml: "yaml", yml: "yaml", py: "python", sv: "sv", svh: "sv", v: "sv", vh: "sv", vhd: "vhdl", vhdl: "vhdl",
  c: "c", h: "c", cc: "c", cpp: "c", cxx: "c", hpp: "c", hh: "c", cu: "c", json: "json", md: "md", markdown: "md",
  sh: "sh", bash: "sh", tcl: "tcl", sdc: "tcl", xdc: "tcl" };

export function langOf(path) {
  const name = String(path || "").toLowerCase();
  const ext = name.includes(".") ? name.split(".").pop() : "";
  return EXT[ext] || "";
}

/** A language by the text's own look, for a design without a file name. */
export function guessLang(text) {
  const t = String(text || "").slice(0, 2000);
  if (/^\s*(module|interface|package)\b|\bendmodule\b/m.test(t)) return "sv";
  if (/^\s*(def|import|from|class)\s/m.test(t)) return "python";
  if (/^\s*#include\b|\bint\s+main\s*\(/m.test(t)) return "c";
  if (/^\s*entity\s+\w+\s+is\b/im.test(t)) return "vhdl";
  if (/^\s*[{[]/.test(t)) return "json";
  if (/^[A-Za-z_][\w-]*:\s/m.test(t)) return "yaml";
  return "";
}

const MAX_CHARS = 400000;

/** D1206: a YAML text's lines as tokens, each line's own -- the lines of a block scalar (`key: >-`, `key: |`) are its
    prose, plain text: no word in them is a key, a boolean or a number. */
export function tokenLines(text, lang) {
  const lines = String(text ?? "").split("\n");
  if (lang !== "yaml" || String(text ?? "").length > MAX_CHARS) return lines.map(l => tokens(l, lang));
  let block = null;                                   // the indent a block scalar's lines are deeper than
  return lines.map(line => {
    const indent = line.length - line.trimStart().length;
    if (block !== null && (!line.trim() || indent > block)) return line ? [["", line]] : [];
    block = /^\s*(?:-\s+)?[^\s#][^#]*:\s*[|>][-+0-9]*\s*(?:#.*)?$/.test(line) ? indent : null;
    return tokens(line, lang);
  });
}

/** D1095: the text as tokens -- [class, text] each, class "" for plain text -- drawn as spans by the components
    (components/Markdown.js Code and Prose, components/CodeEditor.js). */
export function tokens(text, lang) {
  const rules = RULES[lang], out = [];
  text = String(text ?? "");
  if (lang === "yaml" && text.includes("\n") && text.length <= MAX_CHARS) {        // D1206: block scalars as prose
    return tokenLines(text, lang).flatMap((l, i) => i ? [["", "\n"], ...l] : l);
  }
  if (!rules || text.length > MAX_CHARS) return text ? [["", text]] : [];
  let i = 0, plain = "";
  const flush = () => { if (plain) { out.push(["", plain]); plain = ""; } };
  while (i < text.length) {
    let hit = null;
    for (const [cls, re] of rules) {
      re.lastIndex = i;
      const m = re.exec(text);
      if (m && m[0].length) { hit = [cls, m[0]]; break; }
    }
    if (hit) { flush(); out.push(hit); i += hit[1].length; }
    else {
      // plain text up to the next character a rule could start on
      const ch = text[i];
      plain += ch; i++;
      if (/\w/.test(ch)) { while (i < text.length && /\w/.test(text[i])) { plain += text[i]; i++; } }
    }
  }
  flush();
  return out;
}
