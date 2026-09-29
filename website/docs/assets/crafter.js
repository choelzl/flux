/* The Flux problem builder (website/docs/guide/loop-crafter.md).

   A form that writes a `problem.yaml`. The document side is pure and runs under node too:
   `buildYaml(state) -> string` and `check(state) -> [{level, text}]`, with the vocabulary they draw
   from. The words mirror flux_loop/document.py (DOCUMENT_KEYS, FLOW_BOXES,
   _FLOW_WORDS, EXTENSIONS), flux_loop/boxes.py (DELEGABLE, NEVER) and the registered DSE
   policies; flux/tests/unit/test_loop_crafter.py loads what this writes with the real loader.
   The page wiring (`mount`) is at the bottom and only runs in a browser. */
(function (root) {
  "use strict";

  // ------------------------------------------------------------------ the vocabulary
  /** document.py EXTENSIONS: the languages whose file extension Flux knows. */
  var LANGUAGES = ["systemverilog", "verilog", "vhdl", "chisel", "python", "c", "cpp", "cuda", "opencl",
                   "rust", "scala", "shell", "bash", "text", "yaml", "json", "markdown"];
  var AGENTS = ["opencode", "claude", "codex"];
  /** The registered DSE policies a document names by word (dse.py), and the model's half. */
  var DSE_POLICIES = ["sweep", "montecarlo", "anneal", "gradient", "genetic", "pareto"];
  /** boxes.py: the boxes a coding agent may answer, and the ones that never are. */
  var DELEGABLE = ["validate", "orchestrate", "plan", "dse", "generate", "critique", "extract", "select"];
  var NEVER = ["test", "analytical", "simulation", "calibrate", "records"];
  /** document.py FLOW_BOXES, in flow order. */
  var FLOW_BOXES = ["validate", "orchestrate", "plan", "dse", "generate", "test", "critique", "analytical",
                    "simulation", "calibrate", "select", "feedback", "knowledge", "extract", "records"];
  var BUILTIN_SUBS = ["artifact", "workdir", "name", "part", "python", "home", "failure", "attempt",
                      "prompt", "prompt_file", "point"];

  function agentChoices(what) {
    return AGENTS.map(function (a) {
      return { value: "agent:" + a, half: "agent", label: "Coding agent: " + a, hint: what };
    });
  }

  /** Each box of the drawing: a plain line, its choices (the first is the default, never
      written), and each choice's half: rules, model, agent or fixed. */
  var BOXES = {
    validate: { title: "Check the document", says: "Before anything runs, the document is read for mistakes.",
      choices: [{ value: "rules", half: "rules", label: "Built-in checks" },
                { value: "llm", half: "model", label: "Built-in checks, then a model reads it and objects" }]
        .concat(agentChoices("A coding agent reads the document and objects")) },
    orchestrate: { title: "Pick the next job", says: "Decides what to work on next.",
      choices: [{ value: "default", half: "rules", label: "Standard (rules pick the work)" },
                { value: "rules", half: "rules", label: "Rules only" },
                { value: "llm", half: "model", label: "A model picks" },
                { value: "agent", half: "model", label: "A model with tools picks" }]
        .concat(agentChoices("A coding agent picks")) },
    plan: { title: "Plan the round", says: "Optionally writes a plan for the round before any work starts.",
      choices: [{ value: "none", half: "rules", label: "No plan: step by step" },
                { value: "llm", half: "model", label: "A model writes the plan" }]
        .concat(agentChoices("A coding agent writes the plan")) },
    dse: { title: "Search the settings", says: "Walks the list of settings (the space) to choose which to try.",
      choices: [{ value: "none", half: "off", label: "No search" }]
        .concat(DSE_POLICIES.map(function (p) {
          return { value: p, half: "rules", label: { sweep: "Try every combination", montecarlo: "Random samples",
            anneal: "Annealing", gradient: "Step towards better", genetic: "Genetic (breed the best)",
            pareto: "Trade-off front" }[p] + " (" + p + ")" };
        }))
        .concat([{ value: "llm", half: "model", label: "A model proposes settings" }])
        .concat(agentChoices("A coding agent proposes settings")) },
    generate: { title: "Make a design", says: "Writes each candidate design.",
      choices: [{ value: "model", half: "model", label: "A model writes it" },
                { value: "command", half: "rules", label: "My script writes it" }]
        .concat(agentChoices("A coding agent writes it")) },
    test: { title: "Check it works", says: "Runs your check; a design that fails never goes further. Always yours, never a model's.",
      choices: [{ value: "gate", half: "fixed", label: "Your check (fixed)" }] },
    critique: { title: "Second opinion", says: "Optionally, a critic questions the work and the final choice.",
      choices: [{ value: "none", half: "off", label: "No critic" },
                { value: "llm", half: "model", label: "A model critic" }]
        .concat(agentChoices("A coding agent critic")) },
    analytical: { title: "Quick estimate", says: "Optionally predicts the costly measurement from past ones, to try fewer.",
      choices: [{ value: "none", half: "off", label: "No estimate" },
                { value: "surrogate", half: "model", label: "A learned estimate (surrogate)" }] },
    simulation: { title: "Measure", says: "Runs your measurements, cheapest first. Always the real tools.",
      choices: [{ value: "stages", half: "fixed", label: "Your measurements (fixed)" }] },
    calibrate: { title: "Compare measures", says: "Checks how well the cheap measurement predicts the costly one.",
      choices: [{ value: "on", half: "fixed", label: "On" }, { value: "off", half: "off", label: "Off" }] },
    select: { title: "Choose the best", says: "Picks the winner by your goals.",
      choices: [{ value: "objectives", half: "rules", label: "By the goals" }]
        .concat(agentChoices("By the goals; a coding agent breaks ties")) },
    feedback: { title: "Your notes", says: "Notes you type while it runs steer the next round.",
      choices: [{ value: "human", half: "rules", label: "Take my notes" }, { value: "none", half: "off", label: "No notes" }] },
    knowledge: { title: "Background reading", says: "What the model reads with every request.",
      choices: [{ value: "default", half: "rules", label: "The files I list" },
                { value: "digest", half: "model", label: "Plus Flux's library digest" }] },
    extract: { title: "Learn from results", says: "Optionally turns past results into lessons for the next round.",
      choices: [{ value: "none", half: "off", label: "No lessons" },
                { value: "mined", half: "rules", label: "Lessons mined from the results" }]
        .concat(agentChoices("A coding agent writes lessons from the results")) },
    records: { title: "Keep a record", says: "Every design, measurement and refusal is kept, and read back when you resume.",
      choices: [{ value: "on", half: "fixed", label: "On (fixed)" }] },
  };

  var HALVES = { rules: "rules", model: "a model", agent: "a coding agent", fixed: "fixed", off: "off" };

  function defaultFlow() {
    var out = {};
    FLOW_BOXES.forEach(function (b) { out[b] = BOXES[b].choices[0].value; });
    return out;
  }

  function choiceOf(box, value) {
    var cs = BOXES[box].choices;
    for (var i = 0; i < cs.length; i++) if (cs[i].value === value) return cs[i];
    return null;
  }

  /** The half a box is in for this state (for the drawing's colour). */
  function halfOf(state, box) {
    var v = (state.flow || {})[box];
    if (box === "orchestrate" && state.flow && state.flow.dse && state.flow.dse !== "none") return "off";
    var c = choiceOf(box, v);
    return c ? c.half : "rules";
  }

  // ------------------------------------------------------------------ the tool catalog
  /* The integrated tools (website/docs/assets/tools.json = `flux tools --json`, D654): checks a
     gate may run and stages a document may measure with, each `{id, role, title, what, run,
     params, metrics, needs, pass, languages, kinds}`. The page fetches it; node sets it with
     `setCatalog`, or passes it to buildYaml/check/resolve and the row makers as their last
     argument. Nothing here invents a command: every row is a catalog entry or the author's own. */
  var CATALOG = [];
  function setCatalog(list) { CATALOG = Array.isArray(list) ? list : []; return CATALOG; }
  function toolOf(id, cat) {
    cat = cat || CATALOG;
    for (var i = 0; i < cat.length; i++) if (cat[i].id === id) return cat[i];
    return null;
  }
  function isCustom(id) { return id === "custom-check" || id === "custom-stage"; }

  /** document.py RTL_METRICS: what the loader infers for a `flux rtl measure` stage. */
  var LOADER_RTL = ["fmax_mhz", "area_um2", "power_w", "cell_count"];
  /** objective.py UNITS: the units Flux knows; any other known unit is written as `unit:`. */
  var UNITS = { fmax_mhz: "MHz", area_um2: "um2", area_mm2: "mm2", power_w: "W", power_mw: "mW", time_ms: "ms",
                latency_cycles: "cycles", energy_pj: "pJ", cell_count: "cells" };
  var MORE_UNITS = { storage_bytes: "B", path_ps: "ps" };

  /** A check's type, and the catalog tools that do it. `any`: offered whatever the language
      (a script of yours runs on any file); the others only for the languages they list. For
      HDL, "Compile" is the lint's parse step (`flux rtl lint` exits 3 when the source does not
      parse): Flux has no separate compile command for RTL. */
  var CHECK_TYPES = [
    { key: "lint", title: "Lint", tools: ["rtl-lint"] },
    { key: "compile", title: "Compile", tools: ["rtl-lint", "champsim-build"],
      note: { "rtl-lint": "For RTL, compiling is the lint's parse step: exit 3 means it does not parse." } },
    { key: "golden", title: "Golden model", tools: ["rtl-golden"] },
    { key: "test", title: "Test script", tools: ["python-test-script", "champsim-check"], any: ["python-test-script"] },
    { key: "custom", title: "Custom", tools: ["custom-check"], any: ["custom-check"] },
  ];

  /** The measuring tools, labelled by the tool. */
  var STAGE_TOOLS = [
    ["rtl-synth", "Yosys synthesis (timed by OpenSTA)"], ["rtl-place", "OpenROAD placement"], ["rtl-route", "OpenROAD routing"],
    ["champsim-run", "ChampSim simulation"], ["zigzag-model", "ZigZag model"], ["bench-script", "Benchmark script"],
    ["custom-stage", "Custom command"]];

  function checkType(key) { for (var i = 0; i < CHECK_TYPES.length; i++) if (CHECK_TYPES[i].key === key) return CHECK_TYPES[i]; return null; }

  function fits(t, lang) { return !lang || !(t.languages || []).length || t.languages.indexOf(lang) >= 0; }

  /** The catalog tools a check of this type may use for this language. */
  function toolsFor(type, lang, cat) {
    var ty = checkType(type);
    if (!ty) return [];
    return ty.tools.filter(function (id) {
      var t = toolOf(id, cat);
      return t && ((ty.any || []).indexOf(id) >= 0 || fits(t, lang));
    });
  }

  function homeRelative(def) { return typeof def === "string" && def.indexOf("{home}/") === 0; }
  /** A param as the form shows it: a file beside the document without `{home}/`. */
  function shown(def) { return homeRelative(def) ? def.slice(7) : def === undefined || def === null ? "" : String(def); }

  function uniqueName(base, taken) {
    var name = base, i = 2;
    while (taken.indexOf(name) >= 0) name = base + i++;
    return name;
  }

  function paramsOf(t, given) {
    var p = {};
    for (var k in (t && t.params) || {}) p[k] = shown(t.params[k].default);
    for (var k2 in given || {}) p[k2] = String(given[k2]);
    return p;
  }

  /** A new check of `type` for this state's language: its first fitting tool, or none. */
  function newCheck(state, type, cat) {
    var ids = toolsFor(type, language(state, true), cat), id = ids[0] || "";
    var taken = (state.checks || []).map(function (c) { return c.name; });
    return { type: type, tool: id, name: uniqueName(type, taken), params: paramsOf(toolOf(id, cat)), count_re: "", timeout: "" };
  }

  /** Switch a check's type or tool: its params start from the tool's defaults. */
  function setCheckTool(state, row, type, id, cat) {
    row.type = type;
    var ids = toolsFor(type, language(state, true), cat);
    row.tool = id && ids.indexOf(id) >= 0 ? id : ids[0] || "";
    row.params = paramsOf(toolOf(row.tool, cat));
    return row;
  }

  /** A new measurement with a catalog tool. */
  function newStage(state, id, cat) {
    var taken = (state.stages || []).map(function (s) { return s.name; });
    var base = { "rtl-synth": "synth", "rtl-place": "place", "rtl-route": "route", "champsim-run": "sim",
                 "zigzag-model": "model", "bench-script": "bench", "custom-stage": "measure" }[id] || "measure";
    return { tool: id, name: uniqueName(base, taken), params: paramsOf(toolOf(id, cat)), metrics: "", needs: "", gates: [] };
  }

  /** The value a param takes in the command: `{home}/` put back on a bare file name. */
  function paramValue(t, name, v) {
    var def = t && t.params && t.params[name] ? t.params[name].default : "";
    v = String(v === undefined || v === null ? "" : v).trim();
    if (v === "") v = def === undefined || def === null ? "" : String(def);
    else if (homeRelative(def) && !/^[\/{]/.test(v)) v = "{home}/" + v;
    return v;
  }

  function fillRun(row, cat) {
    var t = toolOf(row.tool, cat);
    if (!t) return String((row.params || {}).command || "").trim();
    return String(t.run).replace(/\{([A-Za-z_]\w*)\}/g, function (m, name) {
      return t.params && Object.prototype.hasOwnProperty.call(t.params, name) ? paramValue(t, name, (row.params || {})[name]) : m;
    }).trim();
  }

  /** The numbers a measurement reports. */
  function reports(row, cat) {
    var t = toolOf(row.tool, cat);
    if (!t || isCustom(row.tool)) return list(row.metrics);
    return Object.keys(t.metrics || {});
  }

  /** Every number some measurement reports, in order of first appearance. */
  function reported(state, cat) {
    var out = [];
    (state.stages || []).forEach(function (st) { reports(st, cat).forEach(function (m) { if (out.indexOf(m) < 0) out.push(m); }); });
    return out;
  }

  function unitFor(metric, cat) {
    if (UNITS[metric]) return UNITS[metric];
    if (MORE_UNITS[metric]) return MORE_UNITS[metric];
    var cs = cat || CATALOG;
    for (var i = 0; i < cs.length; i++) if (cs[i].metrics && cs[i].metrics[metric]) return cs[i].metrics[metric];
    return "";
  }

  /** Which way a number is better when the author only asks to balance it. */
  function naturalDirection(metric) {
    return /fmax|speedup|ipc|throughput|mhz|gain|score|accuracy|bandwidth/i.test(metric) ? "maximize" : "minimize";
  }

  // ------------------------------------------------------------------ the objective
  var LABELS = [["atleast", "at least"], ["atmost", "at most"], ["max", "maximise"], ["min", "minimise"], ["balance", "balance"]];

  function newObjective(metric, label) { return { metric: metric, label: label || "max", value: "" }; }

  function num(v) { var s = String(v === undefined || v === null ? "" : v).trim(); var x = Number(s); return s !== "" && isFinite(x) ? x : null; }
  function clockPs(mhz) { var m = num(mhz); return m && m > 0 ? Math.round(1e6 / m) : null; }

  /** The objectives as the document writes them, in the rows' order. */
  function objectivesOf(state, cat) {
    return (state.objectives || []).filter(function (o) { return String(o.metric || "").trim(); }).map(function (o) {
      var m = o.metric.trim(), r;
      if (o.label === "atleast") r = { metric: m, direction: "maximize", goal: num(o.value) };
      else if (o.label === "atmost") r = { metric: m, direction: "minimize", goal: num(o.value) };
      else if (o.label === "min") r = { metric: m, direction: "minimize" };
      else if (o.label === "balance") r = { metric: m, direction: naturalDirection(m), balance: true };
      else r = { metric: m, direction: "maximize" };
      if (!UNITS[m] && unitFor(m, cat)) r.unit = unitFor(m, cat);
      return r;
    });
  }

  /** The objective in plain words: the limits, then what decides among designs within them. */
  function describeObjectives(objs, cat) {
    var limits = [], order = [], balance = [];
    objs.forEach(function (o) {
      var u = unitFor(o.metric, cat) || o.unit || "";
      if (o.goal !== undefined) limits.push(o.metric + (o.direction === "maximize" ? " at least " : " at most ") + (o.goal === null ? "?" : o.goal) + (u ? " " + u : ""));
      else if (o.balance) {
        if (!balance.length) order.push(null);          // the group decides where its first row is
        balance.push(o.metric);
      } else order.push((o.direction === "maximize" ? "most " : "least ") + o.metric);
    });
    var decide = order.map(function (x) { return x === null ? "the best balance of " + balance.join(" and ") : x; });
    var text = limits.join(", ");
    if (decide.length) text += (text ? ", then " : "") + decide.join(", then ");
    return text ? [text] : [];
  }

  // ------------------------------------------------------------------ the state
  function base() {
    return {
      id: "", statement: "", contract: "", language: "", languageOther: "", knowledgeFiles: "",
      checks: [], stages: [], objectives: [],
      flow: defaultFlow(), generateCommand: "",
      budget: { steps: "", passes: "", repair_attempts: "", finalists: "", workers: "", prototype: "" },
      space: [], partsMode: "none", parts: "",
    };
  }

  // ------------------------------------------------------------------ YAML spelling
  var RESERVED = /^(true|false|yes|no|on|off|null|y|n|~)$/i;

  /** A string as a YAML scalar: plain when that reads back as the same string, else double
      quoted (a JSON string is a valid YAML double-quoted scalar). `flow`: inside [..] or {..}. */
  function q(s, flow) {
    s = String(s);
    var plain = s.length > 0 && s === s.trim() && !/[\x00-\x1f\x7f"\\]/.test(s) &&
      !/^[-?:,\[\]{}#&*!|>'%@`]/.test(s) && !/: |:$| #/.test(s) && !RESERVED.test(s) &&
      !/^[-+.]?[0-9]/.test(s) && !/^\.(inf|nan)$/i.test(s) && !(flow && /[,\[\]{}]/.test(s));
    return plain ? s : JSON.stringify(s);
  }

  /** A value typed in a box: a number or true/false when it reads as one, else the text. */
  function typed(v) {
    v = String(v).trim();
    if (/^-?\d+(\.\d+)?$/.test(v)) return Number(v);
    if (v === "true" || v === "false") return v === "true";
    return v;
  }

  function scalar(v, flow) {
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    return q(v, flow);
  }

  function list(text) {
    return String(text || "").split(",").map(function (t) { return t.trim(); }).filter(Boolean);
  }

  function flowSeq(items) { return "[" + items.map(function (v) { return scalar(v, true); }).join(", ") + "]"; }

  function flowMap(pairs) {
    return "{" + pairs.map(function (p) { return q(p[0], true) + ": " + scalar(p[1], true); }).join(", ") + "}";
  }

  /** Prose under `key:`: folded and wrapped, or literal when it has line breaks of its own. */
  function prose(key, text) {
    text = String(text).trim();
    if (/\n|\t| {2}/.test(text)) {
      return key + ": |-\n" + text.split("\n").map(function (l) { return l ? "  " + l : ""; }).join("\n") + "\n";
    }
    var lines = [], line = "";
    text.split(" ").forEach(function (w) {
      if (line && (line + " " + w).length > 94) { lines.push(line); line = w; }
      else line = line ? line + " " + w : w;
    });
    if (line) lines.push(line);
    return key + ": >-\n" + lines.map(function (l) { return "  " + l; }).join("\n") + "\n";
  }

  /** The design language; "" when none is chosen yet (`bare`) or "text" (what the loader assumes). */
  function language(state, bare) {
    var l = state.language === "other" ? String(state.languageOther || "").trim() : String(state.language || "");
    return l || (bare ? "" : "text");
  }

  function knobNames(state) {
    return (state.space || []).filter(function (r) { return String(r.knob || "").trim() && list(r.choices).length; })
      .map(function (r) { return r.knob.trim(); });
  }

  function gateOf(g) {
    if (!g || !String(g.metric || "").trim()) return null;
    var v = num(g.value), out = { metric: g.metric.trim() };
    if (g.rule === "within") out.within = v === null ? null : Math.round(v * 1000) / 100000;
    else out[g.rule === "below" ? "below" : "at"] = v;
    return out;
  }

  /** What the document says for the checks, the measurements and the objective. */
  function resolve(state, cat) {
    cat = cat || CATALOG;
    var objectives = objectivesOf(state, cat);
    var checks = (state.checks || []).map(function (c, i) {
      return { name: String(c.name || "").trim() || "check" + (i + 1), run: fillRun(c, cat), tool: c.tool,
               count_re: isCustom(c.tool) ? String(c.count_re || "").trim() : "", timeout: String(c.timeout || "").trim() };
    });
    var stages = (state.stages || []).map(function (st, i) {
      var t = toolOf(st.tool, cat), custom = !t || isCustom(st.tool), cmd = fillRun(st, cat), rep = reports(st, cat);
      var gates = (st.gates || []).map(gateOf).filter(Boolean);
      var used = objectives.map(function (o) { return o.metric; }).concat(gates.map(function (g) { return g.metric; }));
      var rtlMeasure = /^flux rtl measure\s/.test(cmd);
      var write = custom || !rtlMeasure || used.some(function (m) { return LOADER_RTL.indexOf(m) < 0 && rep.indexOf(m) >= 0; });
      var needs = custom ? list(st.needs) : /^flux rtl\s/.test(cmd) ? [] : (t.needs || []).slice();
      return { name: String(st.name || "").trim() || "stage" + (i + 1), command: cmd, tool: st.tool, reports: rep,
               metrics: write ? rep : [], needs: needs, gates: gates };
    });
    return { checks: checks, stages: stages, objectives: objectives };
  }

  function flowValue(state, box) {
    var v = state.flow[box];
    if (typeof v === "string" && v.indexOf("agent:") === 0) return "{agent: " + v.slice(6) + "}";
    if (box === "generate" && v === "command") return "{command: " + JSON.stringify(String(state.generateCommand || "").trim()) + "}";
    if (box === "analytical" || box === "knowledge") return "[" + v + "]";
    return v;
  }

  /** The boxes this state says, in flow order: a box at its default is not written (a
      document says only what is its own), nor `orchestrate` when a search policy leads. */
  function flowSaid(state) {
    var flow = state.flow || {};
    return FLOW_BOXES.filter(function (b) {
      var v = flow[b];
      if (v === undefined || v === BOXES[b].choices[0].value) return false;
      if (b === "orchestrate" && flow.dse && flow.dse !== "none") return false;
      if (NEVER.indexOf(b) >= 0 && String(v).indexOf("agent:") === 0) return false;
      return !!choiceOf(b, v);
    });
  }

  function gateMap(g) {
    var p = [["metric", g.metric]];
    ["at", "below", "within"].forEach(function (k) { if (k in g) p.push([k, g[k] === null ? "?" : g[k]]); });
    return flowMap(p);
  }

  /** The problem document for `state`, as the text of a `.problem.yaml`. */
  function buildYaml(state, cat) {
    var r = resolve(state, cat);
    var id = String(state.id || "").trim() || "my_problem";
    var out = "# " + id + ": made with the Flux problem builder.\n" +
              "#     flux task check " + id + ".problem.yaml\n" +
              "#     flux task run " + id + ".problem.yaml --passes 1\n\n";
    out += "id: " + q(id) + "\n";
    out += prose("statement", String(state.statement || "").trim() || "(say what you want made)");
    if (String(state.contract || "").trim()) out += prose("contract", state.contract);
    if (language(state, true)) out += "language: " + q(language(state, true)) + "\n";

    var kfiles = list(state.knowledgeFiles);
    if (kfiles.length) out += "\nknowledge:\n  files: " + flowSeq(kfiles) + "\n";

    if (state.partsMode === "decompose") out += "\nparts: decompose\n";
    else if (state.partsMode === "list" && list(state.parts).length) out += "\nparts: " + flowSeq(list(state.parts)) + "\n";

    var space = (state.space || []).filter(function (x) { return String(x.knob || "").trim() && list(x.choices).length; });
    if (space.length) {
      out += "\nspace:\n";
      space.forEach(function (x) { out += "  " + q(x.knob.trim()) + ": " + flowSeq(list(x.choices).map(typed)) + "\n"; });
    }

    var said = flowSaid(state);
    if (said.length) {
      out += "\nflow:\n";
      said.forEach(function (b) { out += "  " + b + ": " + flowValue(state, b) + "\n"; });
    }

    var checks = r.checks.filter(function (c) { return c.run; });
    if (checks.length) {
      out += "\ngate:                       # each must pass, in order\n";
      checks.forEach(function (c) {
        var p = [["name", c.name], ["run", c.run]];
        if (c.count_re) p.push(["count_re", c.count_re]);
        if (c.timeout) p.push(["timeout_s", typed(c.timeout)]);
        out += "  - " + flowMap(p) + "\n";
      });
    }

    if (r.stages.length) {
      out += "\nstages:                     # cheapest first\n";
      r.stages.forEach(function (st) {
        out += "  - name: " + q(st.name) + "\n";
        out += "    command: " + q(st.command || "(the command)") + "\n";
        if (st.metrics.length) out += "    metrics: " + flowSeq(st.metrics) + "\n";
        if (st.needs.length) out += "    needs: " + flowSeq(st.needs) + "\n";
        if (st.gates.length === 1) out += "    cutoff: " + gateMap(st.gates[0]) + "\n";   // go on only if
        else if (st.gates.length > 1) out += "    cutoff: [" + st.gates.map(gateMap).join(", ") + "]\n";
      });
    }

    if (r.objectives.length) {
      out += "\nobjectives:                 # limits must hold; the rest decide, in order\n";
      r.objectives.forEach(function (o) {
        var p = [["metric", o.metric], ["direction", o.direction]];
        if (o.goal !== undefined) p.push(["goal", o.goal === null ? "?" : o.goal]);
        if (o.balance) p.push(["balance", true]);
        if (o.unit) p.push(["unit", o.unit]);
        out += "  - " + flowMap(p) + "\n";
      });
    }

    var b = state.budget || {}, bp = [];
    ["steps", "passes", "repair_attempts", "finalists", "workers"].forEach(function (key) {
      var v = String(b[key] || "").trim();
      if (v !== "") bp.push([key, typed(v)]);
    });
    if (b.prototype) bp.push(["prototype", typed(b.prototype)]);
    if (bp.length) out += "\nbudget: " + flowMap(bp) + "\n";
    return out;
  }

  // ------------------------------------------------------------------ the checklist
  function placeholders(cmd) {
    var out = [], re = /\{([A-Za-z_]\w*)\}/g, m;
    String(cmd || "").split(/\s+/).forEach(function (tok) {
      while ((m = re.exec(tok))) out.push(m[1]);
    });
    return out;
  }

  /** What is wrong or missing, as `{level: "error"|"warning"|"note", text}`, plain words. */
  function check(state, cat) {
    cat = cat || CATALOG;
    var msgs = [];
    function error(t) { msgs.push({ level: "error", text: t }); }
    function warn(t) { msgs.push({ level: "warning", text: t }); }
    function note(t) { msgs.push({ level: "note", text: t }); }
    var id = String(state.id || "").trim(), lang = language(state, true);
    var r = resolve(state, cat), flow = state.flow || {}, knobs = knobNames(state);
    if (!id) error("Give the problem a name (letters, digits and _).");
    else if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(id)) error("The name \"" + id + "\" should be a letter, then letters, digits or _.");
    if (!String(state.statement || "").trim()) error("Say what you want made (the statement is empty).");
    if (!lang) warn("Choose the design's language.");

    function params(row, what, nm) {
      var t = toolOf(row.tool, cat);
      if (t && lang && !fits(t, lang)) warn(what + " \"" + nm + "\" (" + t.title + ") is made for " + t.languages.join(", ") + "; the design is " + lang + ".");
      for (var k in (t && t.params) || {}) {
        var v = String((row.params || {})[k] === undefined ? "" : row.params[k]).trim(), def = t.params[k].default;
        if (!v && (def === "" || def === undefined || def === null)) error(what + " \"" + nm + "\" needs its " + t.params[k].label.toLowerCase() + ".");
        else if (v && typeof def === "number" && !(num(v) > 0)) error(what + " \"" + nm + "\": " + t.params[k].label.toLowerCase() + " should be a number above 0.");
      }
    }

    // the checks
    if (!r.checks.length) error("Add a check: a design that fails it goes no further.");
    var seen = {};
    (state.checks || []).forEach(function (c, i) {
      var nm = r.checks[i].name, ty = checkType(c.type);
      if (seen[nm]) error("Two checks are named \"" + nm + "\"; names must differ.");
      seen[nm] = 1;
      if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(nm)) error("The check name \"" + nm + "\" should be a letter, then letters, digits, _ or -.");
      if (!c.tool || !toolOf(c.tool, cat)) {
        error("Check \"" + nm + "\": there is no " + (ty ? ty.title.toLowerCase() : "such") + " tool for " + (lang || "this language") + "; use Custom.");
        return;
      }
      params(c, "Check", nm);
      if (c.timeout && !(num(c.timeout) > 0)) error("Check \"" + nm + "\": the time limit should be a number of seconds.");
      if (r.checks[i].count_re) {
        var ok = true;
        try { new RegExp(r.checks[i].count_re); ok = /\((?!\?)/.test(r.checks[i].count_re); } catch (e) { ok = false; }
        if (!ok) error("Check \"" + nm + "\": the count pattern needs one group around the number, like (\\d+) failing.");
      }
    });
    var runs = {};
    r.checks.forEach(function (c) {
      if (c.run && runs[c.run]) warn("Checks \"" + runs[c.run] + "\" and \"" + c.name + "\" run the same command.");
      runs[c.run] = runs[c.run] || c.name;
    });

    // the measurements
    if (!r.stages.length) error("Add a measurement: designs are compared on what it reports.");
    seen = {};
    var all = reported(state, cat);
    (state.stages || []).forEach(function (st, i) {
      var rs = r.stages[i], nm = rs.name, last = i === state.stages.length - 1;
      if (seen[nm]) error("Two measurements are named \"" + nm + "\"; names must differ.");
      seen[nm] = 1;
      if (!toolOf(st.tool, cat) && !isCustom(st.tool)) error("Measurement \"" + nm + "\": the tool " + st.tool + " is not in the catalog.");
      params(st, "Measurement", nm);
      if (isCustom(st.tool) && !rs.reports.length) error("Measurement \"" + nm + "\" needs the names of the numbers it prints (name=value).");
      (st.gates || []).forEach(function (g) {
        var m = String(g.metric || "").trim();
        if (!m) { error("Measurement \"" + nm + "\": a gate needs the number it looks at."); return; }
        if (rs.reports.indexOf(m) < 0) error("Measurement \"" + nm + "\" does not report " + m + ", so its gate cannot use it.");
        var v = num(g.value);
        if (v === null) error("Measurement \"" + nm + "\": the gate on " + m + " needs a number.");
        else if (g.rule === "within" && !(v > 0 && v <= 100)) error("Measurement \"" + nm + "\": \"within\" is a percentage between 1 and 100.");
      });
      if (last && (st.gates || []).length) warn("The last measurement's gate has nothing after it to hold back.");
    });

    // the objective
    if (!r.objectives.length) error("Add an objective: which reported numbers matter, and how.");
    var metricsSeen = {}, balanced = 0;
    (state.objectives || []).forEach(function (o, i) {
      var m = String(o.metric || "").trim(), ro = r.objectives[i];
      if (!m) return;
      if (metricsSeen[m]) warn("The objective names " + m + " twice.");
      metricsSeen[m] = 1;
      var by = r.stages.filter(function (st) { return st.reports.indexOf(m) >= 0; }).length;
      if (!by) error("The objective uses " + m + ", which no measurement reports.");
      else if (by < r.stages.length) warn("Every measurement should report " + m + " (the objective uses it); some do not.");
      if ((o.label === "atleast" || o.label === "atmost") && num(o.value) === null) error("Say the number " + m + " must be " + (o.label === "atleast" ? "at least." : "at most."));
      if (ro && ro.balance) balanced++;
    });
    if (balanced === 1) warn("Balance needs two numbers or more: one alone is just maximise or minimise.");

    // the flow
    FLOW_BOXES.forEach(function (b) {
      var v = flow[b];
      if (typeof v === "string" && v.indexOf("agent:") === 0 && DELEGABLE.indexOf(b) < 0 && b !== "generate") {
        error("\"" + BOXES[b].title + "\" (" + b + ") is never handed to a coding agent: it establishes the facts.");
      } else if (v !== undefined && !choiceOf(b, v)) {
        error("\"" + BOXES[b].title + "\" (" + b + ") cannot be \"" + v + "\".");
      }
    });

    (state.space || []).forEach(function (x) {
      if (String(x.knob || "").trim() && !list(x.choices).length) error("The setting \"" + x.knob.trim() + "\" has no choices.");
    });
    var searching = flow.dse && flow.dse !== "none";
    if (searching && !knobs.length) error("A search needs settings to walk: add some under Advanced > Settings to search.");
    if (!searching && knobs.length) warn("The settings are only searched when \"Search the settings\" is on.");
    if (searching && flow.orchestrate && flow.orchestrate !== "default") warn("With a search, the search picks the next job; \"Pick the next job\" is left out.");
    if (flow.generate === "command" && !String(state.generateCommand || "").trim()) error("Say the command that writes each design.");

    var cmds = r.checks.map(function (c) { return ["check \"" + c.name + "\"", c.run]; });
    r.stages.forEach(function (st) { cmds.push(["measurement \"" + st.name + "\"", st.command]); });
    if (flow.generate === "command") cmds.push(["the design script", state.generateCommand]);
    cmds.forEach(function (c) {
      placeholders(c[1]).forEach(function (p) {
        if (BUILTIN_SUBS.indexOf(p) < 0 && knobs.indexOf(p) < 0) error("In " + c[0] + ", {" + p + "} is neither a setting to search nor one of Flux's own.");
      });
    });

    ["steps", "passes", "repair_attempts", "finalists", "workers"].forEach(function (key) {
      var v = String((state.budget || {})[key] || "").trim();
      if (v && !/^\d+$/.test(v)) error("Budget \"" + key + "\" should be a whole number.");
    });

    var agents = {};
    flowSaid(state).forEach(function (b) { if (String(flow[b]).indexOf("agent:") === 0) agents[flow[b].slice(6)] = 1; });
    if (Object.keys(agents).length) note("The coding agent " + Object.keys(agents).join(", ") + " must be installed where it runs.");
    var files = {};
    cmds.forEach(function (c) {
      var re = /\{home\}\/([\w.\-\/]+)/g, m;
      while ((m = re.exec(String(c[1] || "")))) files[m[1]] = 1;
    });
    list(state.knowledgeFiles).forEach(function (f) { files[f] = 1; });
    var fl = Object.keys(files);
    if (fl.length) note("Put these beside the document: " + fl.join(", ") + ".");
    return msgs;
  }

  var api = { buildYaml: buildYaml, check: check, resolve: resolve, setCatalog: setCatalog, toolOf: toolOf,
              toolsFor: toolsFor, newCheck: newCheck, setCheckTool: setCheckTool, newStage: newStage,
              newObjective: newObjective, reports: reports, reported: reported, fillRun: fillRun,
              describeObjectives: describeObjectives, naturalDirection: naturalDirection, clockPs: clockPs,
              CHECK_TYPES: CHECK_TYPES, STAGE_TOOLS: STAGE_TOOLS, LABELS: LABELS,
              BOXES: BOXES, FLOW_BOXES: FLOW_BOXES, DELEGABLE: DELEGABLE, NEVER: NEVER, LANGUAGES: LANGUAGES,
              AGENTS: AGENTS, DSE_POLICIES: DSE_POLICIES, halfOf: halfOf, defaultFlow: defaultFlow, base: base };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined") return;

  // ================================================================== the page
  function h(tag, attrs, kids) {
    var el = document.createElement(tag);
    for (var k in attrs || {}) {
      if (k === "on") for (var ev in attrs.on) el.addEventListener(ev, attrs.on[ev]);
      else if (k === "text") el.textContent = attrs[k];
      else if (attrs[k] !== undefined && attrs[k] !== null && attrs[k] !== false) el.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) { if (c) el.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return el;
  }

  var SVGNS = "http://www.w3.org/2000/svg";
  function s(tag, attrs, text) {
    var el = document.createElementNS(SVGNS, tag);
    for (var k in attrs) el.setAttribute(k, attrs[k]);
    if (text) el.textContent = text;
    return el;
  }

  function mount(host) {
    var state = base();
    var openBox = null;
    var parts = {};

    function changed(structural) {
      if (structural) renderForm();
      renderDiagram();
      renderOutput();
    }

    // -- inputs bound to a path of the state
    function field(label, get, set, opts) {
      opts = opts || {};
      var input;
      if (opts.options) {
        input = h("select", { "aria-label": label });
        opts.options.forEach(function (o) {
          var v = typeof o === "string" ? o : o[0], t = typeof o === "string" ? o : o[1];
          var op = h("option", { value: v, text: t });
          if (String(get()) === v) op.selected = true;
          input.appendChild(op);
        });
        input.addEventListener("change", function () { set(input.value); changed(!!opts.structural); });
      } else {
        input = h(opts.area ? "textarea" : "input", { type: opts.area ? null : "text", placeholder: opts.placeholder || "",
                                                        "aria-label": label, rows: opts.area ? (opts.rows || 3) : null });
        input.value = get() || "";
        input.addEventListener("input", function () { set(input.value); changed(false); });
      }
      return h("label", { class: "fc-field" + (opts.wide ? " fc-wide" : "") },
               [h("span", { class: "fc-label", text: label }), input, opts.hint ? h("small", { text: opts.hint }) : null]);
    }

    function section(title, kids, cls) {
      return h("section", { class: "fc-section " + (cls || "") }, [h("h3", { text: title })].concat(kids));
    }

    function button(text, fn, cls) {
      return h("button", { type: "button", class: "fc-btn " + (cls || ""), text: text, on: { click: fn } });
    }

    // -- ordered boxes: up, down, remove
    function move(listOf, i, d) {
      var j = i + d;
      if (j < 0 || j >= listOf.length) return;
      var x = listOf[i]; listOf[i] = listOf[j]; listOf[j] = x;
      changed(true);
    }

    function rowButtons(listOf, i) {
      return h("div", { class: "fc-row-buttons" }, [
        button("↑", function () { move(listOf, i, -1); }, "fc-small" + (i === 0 ? " fc-hidden" : "")),
        button("↓", function () { move(listOf, i, 1); }, "fc-small" + (i === listOf.length - 1 ? " fc-hidden" : "")),
        button("Remove", function () { listOf.splice(i, 1); changed(true); }, "fc-small")]);
    }

    /** A catalog param's field; a clock period is typed as a target speed in MHz. */
    function paramField(t, row, k) {
      var p = t.params[k];
      if (k === "clock_ps") {
        return field("Target speed, MHz", function () { var ps = num(row.params.clock_ps); return ps ? String(Math.round(1e6 / ps)) : ""; },
                     function (v) { var ps = clockPs(v); row.params.clock_ps = ps ? String(ps) : v; },
                     { hint: "Sets the clock it is timed at" });
      }
      return field(p.label + (p.unit ? " (" + p.unit + ")" : ""), function () { return row.params[k]; }, function (v) { row.params[k] = v; },
                   { placeholder: shown(p.default), wide: k === "command" });
    }

    // -- checks
    function renderChecks() {
      var lang = language(state, true);
      var rows = state.checks.map(function (c, i) {
        var ty = checkType(c.type) || CHECK_TYPES[CHECK_TYPES.length - 1], t = toolOf(c.tool);
        var kids = [h("div", { class: "fc-row-head fc-wide" }, [h("strong", { text: "Check " + (i + 1) }), rowButtons(state.checks, i)]),
          field("Type", function () { return c.type; }, function (v) {
            // a name that was only the old type follows the new one
            if (new RegExp("^" + c.type + "\\d*$").test(c.name)) {
              c.name = uniqueName(v, state.checks.filter(function (x) { return x !== c; }).map(function (x) { return x.name; }));
            }
            setCheckTool(state, c, v);
          },
                { structural: true, options: CHECK_TYPES.map(function (x) { return [x.key, x.title]; }) }),
          field("Name", function () { return c.name; }, function (v) { c.name = v; })];
        var ids = toolsFor(c.type, lang);
        if (!t) {
          kids.push(h("p", { class: "fc-wide fc-warn", text: "No " + ty.title.toLowerCase() + " tool for " + (lang || "this language") + "; choose Custom." }));
        } else {
          if (ids.length > 1) {
            kids.push(field("Tool", function () { return c.tool; }, function (v) { setCheckTool(state, c, c.type, v); },
                            { structural: true, options: ids.map(function (id) { return [id, toolOf(id).title]; }) }));
          }
          kids.push(h("small", { class: "fc-wide", text: t.what + " Passes " + String(t.pass || "").replace(/^passes /, "") +
                                   ((ty.note || {})[c.tool] ? " " + ty.note[c.tool] : "") }));
          Object.keys(t.params || {}).forEach(function (k) { kids.push(paramField(t, c, k)); });
          if (isCustom(c.tool)) {
            kids.push(field("Count pattern (optional)", function () { return c.count_re; }, function (v) { c.count_re = v; },
                            { placeholder: "(\\d+) failing", hint: "When it prints failures another way" }));
          }
          kids.push(field("Time limit, s (optional)", function () { return c.timeout; }, function (v) { c.timeout = v; }));
        }
        return h("div", { class: "fc-row" }, kids);
      });
      return section("Checks (each must pass, in order)", [h("div", { class: "fc-rows" }, rows),
        button("+ Add a check", function () {
          var used = state.checks.map(function (c) { return c.type; });
          var type = ["lint", "golden", "test", "custom"].filter(function (x) {
            return used.indexOf(x) < 0 && toolsFor(x, lang).length; })[0] || "custom";
          state.checks.push(newCheck(state, type));
          changed(true);
        })]);
    }

    // -- measurements
    function renderStages() {
      var rows = state.stages.map(function (st, i) {
        var t = toolOf(st.tool), custom = isCustom(st.tool) || !t;
        var kids = [h("div", { class: "fc-row-head fc-wide" }, [h("strong", { text: "Measurement " + (i + 1) }), rowButtons(state.stages, i)]),
          field("Tool", function () { return st.tool; }, function (v) {
            var fresh = newStage({ stages: [] }, v); st.tool = v; st.params = fresh.params; st.gates = [];
          }, { structural: true, options: STAGE_TOOLS.filter(function (x) { return toolOf(x[0]); }) }),
          field("Name", function () { return st.name; }, function (v) { st.name = v; })];
        if (t) kids.push(h("small", { class: "fc-wide", text: t.what }));
        Object.keys((t && t.params) || {}).forEach(function (k) { kids.push(paramField(t, st, k)); });
        if (custom) {
          kids.push(field("Numbers it prints", function () { return st.metrics; }, function (v) { st.metrics = v; },
                          { placeholder: "time_ms, score", hint: "Printed as name=value" }));
          kids.push(field("Tools it needs (optional)", function () { return st.needs; }, function (v) { st.needs = v; }));
        } else {
          kids.push(h("small", { class: "fc-wide", text: "Reports " + Object.keys(t.metrics || {}).map(function (m) {
            return m + (t.metrics[m] ? " (" + t.metrics[m] + ")" : ""); }).join(", ") }));
        }
        var rep = reports(st);
        var gates = st.gates.map(function (g, j) {
          var bits = [h("span", { class: "fc-label", text: j ? "and" : "Go on only if" }),
            field("Number", function () { return g.metric; }, function (v) { g.metric = v; },
                  { options: (rep.indexOf(g.metric) < 0 && g.metric ? [g.metric] : []).concat(rep) }),
            field("Rule", function () { return g.rule; }, function (v) { g.rule = v; },
                  { structural: true, options: [["at", "at least"], ["below", "at most"], ["within", "within % of the best"]] }),
            field(g.rule === "within" ? "Percent" : "Value", function () { return g.value; }, function (v) { g.value = v; }),
            button("×", function () { st.gates.splice(j, 1); changed(true); }, "fc-small")];
          return h("div", { class: "fc-cutoff fc-wide" }, bits);
        });
        kids = kids.concat(gates);
        kids.push(h("div", { class: "fc-wide" }, [button("+ gate", function () {
          st.gates.push({ metric: rep[0] || "", rule: "at", value: "" }); changed(true);
        }, "fc-small")]));
        return h("div", { class: "fc-row" }, kids);
      });
      var add = button("+ Add a measurement", function () {
        // the first tool made for this language that is not used yet, else a custom command
        var lang = language(state, true), used = state.stages.map(function (x) { return x.tool; });
        var pick = STAGE_TOOLS.map(function (x) { return x[0]; }).filter(function (id) {
          var t = toolOf(id); return t && !isCustom(id) && lang && (t.languages || []).indexOf(lang) >= 0 && used.indexOf(id) < 0;
        })[0] || "custom-stage";
        state.stages.push(newStage(state, pick));
        changed(true);
      });
      return section("Measurements (cheapest first)", [h("div", { class: "fc-rows" }, rows), add]);
    }

    // -- the objective
    function renderObjective() {
      var rep = reported(state);
      var rows = state.objectives.map(function (o, i) {
        var kids = [h("strong", { class: "fc-obj-metric", text: (i + 1) + ". " + o.metric }),
          field("How", function () { return o.label; }, function (v) { o.label = v; }, { structural: true, options: LABELS })];
        if (o.label === "atleast" || o.label === "atmost") {
          var u = unitFor(o.metric);
          kids.push(field("Value" + (u ? " (" + u + ")" : ""), function () { return o.value; }, function (v) { o.value = v; }));
        }
        kids.push(rowButtons(state.objectives, i));
        return h("div", { class: "fc-row fc-obj" }, kids);
      });
      var used = state.objectives.map(function (o) { return o.metric; });
      var sel = h("select", { "aria-label": "Add to the objective", class: "fc-add" });
      sel.appendChild(h("option", { value: "", text: rep.length ? "+ Add a number to the objective..." : "(add a measurement first)" }));
      rep.forEach(function (m) {
        if (used.indexOf(m) < 0) sel.appendChild(h("option", { value: m, text: m + (unitFor(m) ? " (" + unitFor(m) + ")" : "") }));
      });
      sel.addEventListener("change", function () {
        if (sel.value) { state.objectives.push(newObjective(sel.value, naturalDirection(sel.value) === "maximize" ? "max" : "min")); changed(true); }
      });
      parts.goalWords = h("p", { class: "fc-goal-words" });
      renderGoalWords();
      return section("Objective", [h("p", { class: "fc-hint", text: "Limits (at least, at most) must hold; the others decide among the designs that meet them, top first. Balance finds the best trade-off between the numbers marked so." }),
        h("div", { class: "fc-rows" }, rows), sel, parts.goalWords]);
    }

    function renderGoalWords() {
      if (!parts.goalWords) return;
      var words = describeObjectives(resolve(state).objectives);
      parts.goalWords.textContent = words.length ? "In words: " + words[0] + "." : "";
    }

    // -- level 1
    function renderLevel1() {
      var langs = [["", "Choose..."]].concat(LANGUAGES.map(function (l) { return [l, l]; })).concat([["other", "other..."]]);
      var what = section("1. What do you want?", [
        h("div", { class: "fc-grid" }, [
          field("Name", function () { return state.id; }, function (v) { state.id = v; }, { placeholder: "my_design", hint: "Letters, digits and _" }),
          field("Language of the design", function () { return state.language; }, function (v) { state.language = v; },
                { options: langs, structural: true, hint: "One design language per problem; a Python or SystemC prototype can be set in Advanced." }),
          state.language === "other" ? field("Which language?", function () { return state.languageOther; }, function (v) { state.languageOther = v; }, { placeholder: "ini" }) : null,
        ]),
        field("What should be made? Say it as you would to an engineer.", function () { return state.statement; },
              function (v) { state.statement = v; }, { area: true, rows: 4, wide: true }),
        field("Rules every design must follow (optional)", function () { return state.contract; },
              function (v) { state.contract = v; }, { area: true, rows: 2, wide: true, placeholder: "Names, ports, what is not allowed" }),
        field("Files the model should read (optional)", function () { return state.knowledgeFiles; },
              function (v) { state.knowledgeFiles = v; }, { wide: true, placeholder: "spec.md, notes.txt", hint: "Beside the document, separated by commas" }),
      ]);
      var kids = [what];
      if (!CATALOG.length) kids.push(h("p", { class: "fc-hint", text: "The tool list did not load; only Custom checks and measurements are offered." }));
      return h("div", { class: "fc-level" }, kids.concat([renderChecks(), renderStages(), renderObjective()]));
    }

    // -- level 2: the drawing
    var LAYOUT = (function () {
      var W = 150, H = 44, L = 78, R = 248, C = 163, S = 448, rows = [16, 86, 156, 226, 296, 366, 436, 506];
      function at(x, r) { return { x: x, y: rows[r], w: W, h: H }; }
      return {
        width: 624, height: 566,
        box: { validate: at(C, 0), plan: at(L, 1), orchestrate: at(R, 1), feedback: at(S, 1),
               dse: at(L, 2), generate: at(R, 2), knowledge: at(S, 2), test: at(C, 3),
               analytical: at(L, 4), simulation: at(R, 4), calibrate: at(C, 5), critique: at(S, 5),
               select: at(C, 6), records: at(C, 7), extract: at(S, 7) },
      };
    })();

    function edges() {
      var b = LAYOUT.box;
      function cx(n) { return b[n].x + b[n].w / 2; }
      function top(n) { return b[n].y; }
      function bot(n) { return b[n].y + b[n].h; }
      function cy(n) { return b[n].y + b[n].h / 2; }
      function left(n) { return b[n].x; }
      function right(n) { return b[n].x + b[n].w; }
      function elbow(a, z) { var m = (bot(a) + top(z)) / 2; return "M" + cx(a) + " " + bot(a) + " V" + m + " H" + cx(z) + " V" + top(z); }
      return [
        { d: elbow("validate", "plan") }, { d: elbow("validate", "orchestrate") },
        { d: "M" + right("plan") + " " + cy("plan") + " H" + left("orchestrate") },
        { d: "M" + left("feedback") + " " + cy("feedback") + " H" + right("orchestrate"), side: true },
        { d: "M" + cx("orchestrate") + " " + bot("orchestrate") + " V" + top("generate") },
        { d: "M" + right("dse") + " " + cy("dse") + " H" + left("generate") },
        { d: "M" + left("knowledge") + " " + cy("knowledge") + " H" + right("generate"), side: true },
        { d: elbow("generate", "test") },
        { d: elbow("test", "analytical") },
        { d: "M" + right("analytical") + " " + cy("analytical") + " H" + left("simulation") },
        { d: elbow("simulation", "calibrate") },
        { d: "M" + cx("calibrate") + " " + bot("calibrate") + " V" + top("select") },
        { d: "M" + left("critique") + " " + cy("critique") + " H424 V" + cy("select") + " H" + right("select"), side: true },
        { d: "M" + cx("select") + " " + bot("select") + " V" + top("records") },
        { d: "M" + right("records") + " " + cy("records") + " H" + left("extract"), side: true },
        { d: "M" + right("extract") + " " + cy("extract") + " H612 V" + cy("knowledge") + " H" + right("knowledge"), side: true },
        { d: "M" + left("records") + " " + cy("records") + " H34 V" + cy("plan") + " H" + left("plan"), back: true,
          label: { x: 26, y: cy("test"), text: "next round" } },
      ];
    }

    function renderDiagram() {
      var svg = parts.svg;
      if (!svg) return;
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      var defs = s("defs", {});
      var marker = s("marker", { id: "fc-arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" });
      marker.appendChild(s("path", { d: "M0 0 L10 5 L0 10 z", class: "fc-arrowhead" }));
      defs.appendChild(marker);
      svg.appendChild(defs);
      edges().forEach(function (e) {
        svg.appendChild(s("path", { d: e.d, class: "fc-edge" + (e.side ? " fc-side" : "") + (e.back ? " fc-back" : ""), "marker-end": "url(#fc-arrow)" }));
        if (e.label) svg.appendChild(s("text", { x: e.label.x, y: e.label.y, class: "fc-edge-label", transform: "rotate(-90 " + e.label.x + " " + e.label.y + ")", "text-anchor": "middle" }, e.label.text));
      });
      FLOW_BOXES.forEach(function (name) {
        var r = LAYOUT.box[name], half = halfOf(state, name);
        var g = s("g", { class: "fc-box fc-" + half + (openBox === name ? " fc-open" : ""), tabindex: "0", role: "button",
                         "aria-label": BOXES[name].title + ": " + (choiceOf(name, state.flow[name]) || {}).label });
        g.appendChild(s("rect", { x: r.x, y: r.y, width: r.w, height: r.h, rx: 7 }));
        g.appendChild(s("text", { x: r.x + r.w / 2, y: r.y + 19, "text-anchor": "middle", class: "fc-box-name" }, BOXES[name].title));
        var sub = subtitle(name, half);
        g.appendChild(s("text", { x: r.x + r.w / 2, y: r.y + 35, "text-anchor": "middle", class: "fc-box-half" }, sub));
        function open() { openBox = openBox === name ? null : name; renderDiagram(); }
        g.addEventListener("click", open);
        g.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(); } });
        svg.appendChild(g);
      });
      renderPanel();
      renderPicker();
    }

    /** What a box says under its name: who does it, or for the check and the measurements,
        the chosen ones in order. */
    function subtitle(name, half, max) {
      var text = half === "off" && name === "orchestrate" && state.flow.dse !== "none" ? "the search" : HALVES[half];
      var names = name === "test" ? state.checks : name === "simulation" ? state.stages : null;
      if (names) text = names.length ? names.map(function (x) { return String(x.name || "?").trim(); }).join(" \u2192 ") : "none yet";
      max = max || 24;
      return text.length > max ? text.slice(0, max - 1) + "\u2026" : text;
    }

    /** The steps as a list too: on a phone the drawing's words are small. */
    function renderPicker() {
      var sel = parts.picker;
      if (!sel) return;
      sel.innerHTML = "";
      sel.appendChild(h("option", { value: "", text: "Choose a step..." }));
      FLOW_BOXES.forEach(function (name) {
        var half = halfOf(state, name);
        var op = h("option", { value: name, text: BOXES[name].title + " \u2014 " + subtitle(name, half, 60) });
        if (openBox === name) op.selected = true;
        sel.appendChild(op);
      });
    }

    function renderPanel() {
      var p = parts.panel;
      p.innerHTML = "";
      if (!openBox) {
        p.appendChild(h("p", { class: "fc-hint", text: "Click a box to choose who does that step." }));
        return;
      }
      var box = BOXES[openBox];
      p.appendChild(h("h4", { text: box.title + " (" + openBox + ")" }));
      p.appendChild(h("p", { text: box.says }));
      if (openBox === "orchestrate" && state.flow.dse !== "none") {
        p.appendChild(h("p", { class: "fc-hint", text: "A search is on, so the search picks the next job." }));
        return;
      }
      if (box.choices.length === 1) {
        p.appendChild(h("p", { class: "fc-hint", text: "This step is fixed: " + box.choices[0].label + "." }));
        return;
      }
      var group = h("div", { class: "fc-choices", role: "radiogroup" });
      box.choices.forEach(function (c) {
        var id = "fc-" + openBox + "-" + c.value.replace(":", "-");
        var input = h("input", { type: "radio", name: "fc-choice", id: id, value: c.value });
        input.checked = state.flow[openBox] === c.value;
        input.addEventListener("change", function () { state.flow[openBox] = c.value; changed(openBox === "dse" || openBox === "generate"); });
        group.appendChild(h("label", { for: id, class: "fc-choice fc-" + c.half }, [input, " " + c.label]));
      });
      p.appendChild(group);
      if (openBox === "generate" && state.flow.generate === "command") {
        p.appendChild(field("Command that writes each design", function () { return state.generateCommand; },
                            function (v) { state.generateCommand = v; },
                            { wide: true, placeholder: "{python} {home}/gen.py {artifact} {knob}", hint: "Each setting to search is {its name}" }));
      }
    }

    function renderLevel2() {
      parts.svg = document.createElementNS(SVGNS, "svg");
      parts.svg.setAttribute("viewBox", "0 0 " + LAYOUT.width + " " + LAYOUT.height);
      parts.svg.setAttribute("class", "fc-diagram");
      parts.svg.setAttribute("role", "group");
      parts.svg.setAttribute("aria-label", "The loop: each box is one step");
      parts.panel = h("div", { class: "fc-panel", "aria-live": "polite" });
      parts.picker = h("select", { "aria-label": "Step" });
      parts.picker.addEventListener("change", function () { openBox = parts.picker.value || null; renderDiagram(); });
      var legend = h("div", { class: "fc-legend" }, ["rules", "model", "agent", "fixed", "off"].map(function (k) {
        return h("span", { class: "fc-key fc-" + k }, [h("i"), HALVES[k]]);
      }));
      return h("div", { class: "fc-level" }, [section("2. Who does each step?", [
        h("p", { class: "fc-hint", text: "The loop runs top to bottom, round after round. Colours say who does each step; the defaults are usually right." }),
        legend, h("div", { class: "fc-drawing" }, [parts.svg,
          h("label", { class: "fc-field fc-picker" }, [h("span", { class: "fc-label", text: "Step" }), parts.picker]), parts.panel])])]);
    }

    // -- level 3
    function renderLevel3() {
      var b = state.budget;
      var budget = h("div", { class: "fc-grid" }, [
        field("Designs per round", function () { return b.steps; }, function (v) { b.steps = v; }, { hint: "steps" }),
        field("Rounds (empty: until stopped)", function () { return b.passes; }, function (v) { b.passes = v; }, { hint: "passes" }),
        field("Fix attempts per design", function () { return b.repair_attempts; }, function (v) { b.repair_attempts = v; }, { hint: "repair_attempts" }),
        field("Designs that reach the last measurement", function () { return b.finalists; }, function (v) { b.finalists = v; }, { hint: "finalists" }),
        field("Measurements at once", function () { return b.workers; }, function (v) { b.workers = v; }, { hint: "workers; 1 for anything timed" }),
        field("Prove the idea in Python first", function () { return b.prototype; }, function (v) { b.prototype = v; },
              { options: [["", "default"], ["true", "yes"], ["false", "no"], ["python", "yes, in Python"], ["systemc", "yes, in SystemC"]],
                hint: "prototype: yes for maths, no for plain logic" }),
      ]);
      var space = h("div", {}, [h("div", { class: "fc-rows" }, state.space.map(function (r, i) {
        return h("div", { class: "fc-row" }, [
          field("Setting", function () { return r.knob; }, function (v) { r.knob = v; }),
          field("Its choices, in order", function () { return r.choices; }, function (v) { r.choices = v; }, { wide: true, placeholder: "16, 32, 64" }),
          button("Remove", function () { state.space.splice(i, 1); changed(true); }, "fc-small")]);
      })), button("+ Add a setting", function () { state.space.push({ knob: "", choices: "" }); changed(true); })]);
      var partsBox = h("div", { class: "fc-grid" }, [
        field("Split the design into parts", function () { return state.partsMode; }, function (v) { state.partsMode = v; },
              { structural: true, options: [["none", "no"], ["list", "these parts"], ["decompose", "let it decide"]] }),
        state.partsMode === "list" ? field("Parts", function () { return state.parts; }, function (v) { state.parts = v; }, { placeholder: "decoder, datapath" }) : null]);
      var details = h("details", { class: "fc-advanced" }, [h("summary", { text: "3. Advanced" }),
        section("Budget", [budget]),
        section("Settings to search" + (state.flow.dse === "none" ? " (turn on \"Search the settings\" above)" : ""), [space]),
        section("Parts", [partsBox])]);
      if (parts.advancedOpen) details.open = true;
      details.addEventListener("toggle", function () { parts.advancedOpen = details.open; });
      return details;
    }

    function renderForm() {
      parts.form.innerHTML = "";
      parts.form.appendChild(renderLevel1());
      parts.form.appendChild(renderLevel2());
      parts.form.appendChild(renderLevel3());
    }

    function renderOutput() {
      renderGoalWords();
      var yaml = buildYaml(state), id = String(state.id || "").trim() || "my_problem";
      var file = id + ".problem.yaml";
      parts.code.textContent = yaml;
      parts.file.textContent = file;
      var msgs = check(state);
      parts.checks.innerHTML = "";
      if (!msgs.some(function (m) { return m.level !== "note"; })) parts.checks.appendChild(h("li", { class: "fc-ok", text: "Looks complete." }));
      msgs.forEach(function (m) { parts.checks.appendChild(h("li", { class: "fc-" + m.level, text: m.text })); });
      parts.next.textContent = "flux task check " + file + "\nflux task run " + file + " --passes 1";
    }

    function copy() {
      var text = parts.code.textContent, btn = parts.copyBtn;
      function done() { btn.textContent = "Copied"; setTimeout(function () { btn.textContent = "Copy"; }, 1500); }
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
      else fallback();
      function fallback() {
        var ta = h("textarea", {}); ta.value = text; document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); done(); } catch (e) { /* the text is on the page to select */ }
        document.body.removeChild(ta);
      }
    }

    function download() {
      var blob = new Blob([parts.code.textContent], { type: "text/yaml" });
      var a = h("a", { href: URL.createObjectURL(blob), download: parts.file.textContent });
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    }

    host.innerHTML = "";
    parts.form = h("div", { class: "fc-form" });
    parts.code = h("code", {});
    parts.file = h("span", { class: "fc-file" });
    parts.checks = h("ul", { class: "fc-checks" });
    parts.next = h("code", {});
    parts.copyBtn = button("Copy", copy, "fc-primary");
    var out = h("div", { class: "fc-output" }, [
      h("div", { class: "fc-output-head" }, [parts.file, parts.copyBtn, button("Download", download)]),
      h("pre", { class: "fc-yaml" }, [parts.code]),
      h("h4", { text: "Checklist" }), parts.checks,
      h("h4", { text: "Next steps" }),
      h("p", { class: "fc-hint", text: "Save the file with the files it names, then:" }),
      h("pre", {}, [parts.next])]);
    host.appendChild(h("div", { class: "fc-body" }, [parts.form, out]));
    renderForm();
    renderDiagram();
    renderOutput();
  }

  var SCRIPT_SRC = document.currentScript && document.currentScript.src;

  /** The tool catalog beside this script (tools.json), then the builder. */
  function start() {
    var host = document.getElementById("flux-crafter");
    if (!host || host.dataset.mounted) return;
    host.dataset.mounted = "1";
    host.textContent = "Loading the tools...";
    var url = SCRIPT_SRC ? new URL("tools.json", SCRIPT_SRC).href : "tools.json";
    function go() { host.textContent = ""; mount(host); }
    if (typeof fetch !== "function") { go(); return; }
    fetch(url).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (list) { setCatalog(list); go(); }, function () { go(); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
  // Material's instant navigation swaps pages without a reload
  if (root.document$ && root.document$.subscribe) root.document$.subscribe(start);
})(typeof window !== "undefined" ? window : this);
