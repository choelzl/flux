/* The Flux problem builder (website/docs/guide/loop-crafter.md).

   A form that writes a `problem.yaml`. The document side is pure and runs under node too:
   `buildYaml(state) -> string` and `check(state) -> [{level, text}]`, with the vocabulary they draw
   from. The words mirror flux_loop/document.py (DOCUMENT_KEYS, FLOW_BOXES,
   _FLOW_WORDS, EXTENSIONS), flux_loop/boxes.py (DELEGABLE, NEVER) and the registered DSE
   policies; flux/tests/unit/test_loop_crafter.py loads what this writes with the real loader.
   The page wiring (`mount`) is at the bottom and only runs in a browser; on a page with a
   `#flux-loop-drawing` (guide/loop-shape.md) it draws the loop alone, at its defaults. */
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
  var DELEGABLE = ["validate", "orchestrate", "plan", "dse", "generate", "critique", "lessons", "select"];
  var NEVER = ["test", "calibrate"];
  /** The boxes a document may say a half for, in flow order (document.py FLOW_BOXES, less the
      ones the loop no longer takes as settings: analytical, simulation, records). */
  var FLOW_BOXES = ["validate", "orchestrate", "plan", "dse", "generate", "test", "critique", "calibrate",
                    "select", "feedback", "knowledge"];
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
                { value: "model", half: "model", label: "Built-in checks, then a model reads it and objects" }]
        .concat(agentChoices("A coding agent reads the document and objects")) },
    orchestrate: { title: "Pick the next job", says: "Decides what to work on next.",
      choices: [{ value: "default", half: "model", label: "Standard: the model picks the next part, rules pick the kind of work" },
                { value: "rules", half: "rules", label: "Rules only" },
                { value: "model", half: "model", label: "A model picks" },
                { value: "tools", half: "model", label: "A model with tools picks" }]
        .concat(agentChoices("A coding agent picks")) },
    plan: { title: "Plan the round", says: "Optionally writes a plan for the round before any work starts.",
      choices: [{ value: "off", half: "rules", label: "No plan: step by step" },
                { value: "model", half: "model", label: "A model writes the plan" }]
        .concat(agentChoices("A coding agent writes the plan")) },
    dse: { title: "Search the settings", says: "Walks the list of settings (the space) to choose which to try.",
      choices: [{ value: "none", half: "off", label: "No search" }]
        .concat(DSE_POLICIES.map(function (p) {
          return { value: p, half: "rules", label: { sweep: "Try every combination", montecarlo: "Random samples",
            anneal: "Annealing", gradient: "Step towards better", genetic: "Genetic (breed the best)",
            pareto: "Trade-off front" }[p] + " (" + p + ")" };
        }))
        .concat([{ value: "model", half: "model", label: "A model proposes settings" }])
        .concat(agentChoices("A coding agent proposes settings")) },
    generate: { title: "Make a design", says: "Writes each candidate design.",
      choices: [{ value: "model", half: "model", label: "A model writes it" },
                { value: "command", half: "rules", label: "My script writes it" }]
        .concat(agentChoices("A coding agent writes it")) },
    test: { title: "Check it works", says: "Runs your checks in order; a design that fails goes back to be repaired. Always yours, never a model's.",
      fixed: "Configured in the Checks list above.",
      choices: [{ value: "gate", half: "fixed", label: "Your checks (fixed)" }] },
    critique: { title: "Second opinion", says: "Optionally, a critic questions the division into parts, each admitted part (sending it back) and the final choice.",
      choices: [{ value: "off", half: "off", label: "No critic" },
                { value: "model", half: "model", label: "A model critic" }]
        .concat(agentChoices("A coding agent critic")) },
    measure: { title: "Measure", says: "Runs your measurements, cheapest first; a design that fails a gate is dropped.",
      fixed: "Configured in the Measurements list above (each may estimate first).",
      choices: [{ value: "stages", half: "fixed", label: "Your measurements (fixed)" }] },
    calibrate: { title: "Compare measures", says: "Checks how well the cheap measurement predicts the costly one.",
      choices: [{ value: "on", half: "rules", label: "On" }, { value: "off", half: "off", label: "Off" }] },
    select: { title: "Choose the best", says: "Picks the winner by your goals.",
      choices: [{ value: "objectives", half: "rules", label: "By the goals" }]
        .concat(agentChoices("By the goals; a coding agent breaks ties")) },
    feedback: { title: "Your notes", says: "Notes you type while it runs steer the next round.",
      choices: [{ value: "human", half: "rules", label: "Take my notes" }, { value: "off", half: "off", label: "No notes" }] },
    knowledge: { title: "Background reading", says: "What the model reads with every request.",
      choices: [{ value: "default", half: "rules", label: "The library (on), and the files I list" },
                { value: "none", half: "off", label: "None: no library" }] },
    // D784, D791: who sums the papers up, once each, in each pass's Setup -- always, while the
    // library is on; not a flow key of its own: an agent is written as flow.knowledge.agent
    digest: { title: "Digest the papers", says: "Each paper of the library (library/ beside the document, and the shared one) is summed up once, in the Setup, and the summaries reach every prompt.",
      choices: [{ value: "model", half: "model", label: "The model sums up each paper" }]
        .concat(agentChoices("A coding agent reads each paper (its tables and figures too) and sums it up")) },
    // D796: the record's lessons, written into flow.knowledge as `lessons:` -- not a flow key of its own
    lessons: { title: "Learn from results", says: "Optionally turns past results into lessons for the next round, read with the library.",
      choices: [{ value: "off", half: "off", label: "No lessons" },
                { value: "mined", half: "rules", label: "Lessons mined from the results" }]
        .concat(agentChoices("A coding agent writes lessons from the results")) },
    records: { title: "Keep a record", says: "Every design, measurement and refusal is kept, and read back when you resume.",
      fixed: "Always on: every design, measurement and refusal is kept.",
      choices: [{ value: "on", half: "fixed", label: "On (fixed)" }] },
  };

  var HALVES = { rules: "rules", model: "a model", agent: "a coding agent", fixed: "fixed", off: "off" };

  function defaultFlow() {
    var out = {};
    FLOW_BOXES.forEach(function (b) { out[b] = BOXES[b].choices[0].value; });
    out.digest = "model";                                // D784: a box of the drawing, written into knowledge
    out.lessons = "off";                                 // D796: likewise, as knowledge.lessons
    return out;
  }

  /** What the loop does for a box as this state leaves it, in `flux task check`'s own words
      (document.py describe_flow: the parenthesis of the box's line); null where the line has
      none. The test compares these with describe_flow for the same document. */
  var KIND_OF_WORK = "rules pick the kind of work: a design sent back is improved first, then the parts, then the search";
  function explain(box, state) {
    var v = ((state || {}).flow || {})[box];
    var parts = state && (state.partsMode === "decompose" || (state.partsMode === "list" && list(state.parts).length > 0));
    var words = {
      validate: { rules: "the loader's checks", model: "the loader's checks, then the model reads the document and objects, D556" },
      orchestrate: { "default": parts ? "the model picks the next part, the first one waiting without a model; " + KIND_OF_WORK
                                      : "one design, no part to pick; " + KIND_OF_WORK,
                     rules: "the first part waiting, no model; " + KIND_OF_WORK,
                     model: "the model picks the next part; " + KIND_OF_WORK,
                     tools: "the model with tools picks the next part and the kind of work, its reasons on the record, D505" },
      plan: { off: "the orchestrator picks step by step" },
      dse: { none: "the world's own search, if it has one" },
      generate: { model: "the prototype stage, transpile, repair" },
      critique: { model: "a model adversary on the division, each admitted part and the decision" },
      calibrate: { on: "between every pair of stages, on the record" },
      feedback: { human: "the operator's notes, when a terminal is attached", off: "no notes are read, reloaded or waited for" },
      knowledge: { "default": "on by default, its papers digested; `flow.knowledge: off` turns it off", none: "the library is off" },
      lessons: { off: "nothing is mined from the record", mined: "facts mined from the record reach the prompts" },
      records: { on: "every candidate, measurement and refusal, read back on resume" },
    }[box] || {};
    if (box === "records") v = "on";
    if (v === undefined && BOXES[box]) v = BOXES[box].choices[0].value;
    return words[v] || null;
  }

  /** A measurement's estimator in `flux task check`'s words (estimate.py describe). */
  var ESTIMATE_MIN_ROWS = 3;
  function explainEstimate(est) {
    if (!est || !est.kind || est.kind === "off") return "none (the tool runs on every design)";
    var how = { surrogate: "a fit over the record's rows on this stage, from " + ESTIMATE_MIN_ROWS + " rows",
                command: "the estimate command", model: "the model, from the design and the stage's rows" }[est.kind];
    var m = num(est.margin);
    return est.kind + " (" + how + "); skipped when it fails a cutoff or limit by more than " + (m === null ? "?" : Math.round(m)) + "%";
  }

  /** A box with one choice is fixed: drawn grey, not clickable. */
  function isFixed(box) { return !BOXES[box] || BOXES[box].choices.length === 1; }

  function choiceOf(box, value) {
    var cs = BOXES[box].choices;
    for (var i = 0; i < cs.length; i++) if (cs[i].value === value) return cs[i];
    return null;
  }

  /** The half a box is in for this state (for the drawing's colour). */
  function halfOf(state, box) {
    var v = (state.flow || {})[box];
    if (box === "orchestrate" && state.flow && state.flow.dse && state.flow.dse !== "none") return "off";
    if (!BOXES[box] || isFixed(box)) return "fixed";
    var c = choiceOf(box, v);
    if (!c && typeof v === "string" && v.indexOf("agent:") === 0) return "agent";          // a custom agent (D728)
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

  /** Labels by tool for the measuring tools the page knows; any other catalog stage shows its title. */
  var STAGE_LABELS = { "rtl-synth": "Yosys synthesis (timed by OpenSTA)", "rtl-place": "OpenROAD placement",
                       "rtl-route": "OpenROAD routing", "champsim-run": "ChampSim simulation", "zigzag-model": "ZigZag model",
                       "bench-script": "Benchmark script", "custom-stage": "Custom command",
                       "rtl-stat": "Yosys area (no timing)", "prog-size": "Program size (size)",
                       "prog-time": "Program run time", "prog-count": "Instruction count (Valgrind)",
                       "zigzag-eval": "ZigZag evaluator", "timeloop-eval": "Timeloop evaluator" };

  /** The measuring tools: every stage the catalog lists, custom last, as [id, label]. */
  function stageTools(cat) {
    var out = (cat || CATALOG).filter(function (t) { return t.role === "stage"; })
      .map(function (t) { return [t.id, STAGE_LABELS[t.id] || t.title]; });
    out.sort(function (a, b) { return (a[0] === "custom-stage") - (b[0] === "custom-stage"); });
    return out;
  }

  /** `names` joined by arrows within `max` characters: as many as fit, then "+N". */
  function abbreviate(names, max) {
    var full = names.join(" \u2192 ");
    if (full.length <= max) return full;
    for (var k = names.length - 1; k >= 1; k--) {
      var t = names.slice(0, k).join(" \u2192 ") + " \u2192 +" + (names.length - k);
      if (t.length <= max) return t;
    }
    var first = names[0].length > max - 5 ? names[0].slice(0, max - 6) + "\u2026" : names[0];
    return names.length > 1 ? first + " +" + (names.length - 1) : first;
  }

  /** The tool a new measurement starts with: the first made for this language that is not used
      yet (Yosys alone reports no fmax, so it comes after the tools that time), else custom. */
  function nextStageTool(lang, used, cat) {
    var ids = stageTools(cat).map(function (x) { return x[0]; }).filter(function (id) {
      var t = toolOf(id, cat);
      return t && !isCustom(id) && lang && (t.languages || []).indexOf(lang) >= 0 && (used || []).indexOf(id) < 0;
    });
    ids.sort(function (a, b) { return (a === "rtl-stat") - (b === "rtl-stat"); });
    return ids[0] || "custom-stage";
  }

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
                 "zigzag-model": "model", "bench-script": "bench", "custom-stage": "measure", "rtl-stat": "stat",
                 "prog-size": "size", "prog-time": "time", "prog-count": "count", "zigzag-eval": "zigzag",
                 "timeloop-eval": "timeloop" }[id] || "measure";
    var params = paramsOf(toolOf(id, cat));
    if ("clock_ps" in params) params.clock_ps = "";           // empty: from an fmax limit, else the tool's default
    return { tool: id, name: uniqueName(base, taken), params: params, metrics: "", needs: "", gates: [],
             estimate: { kind: "off", margin: "5", command: "" } };
  }

  /** The value a param takes in the command: `{home}/` put back on a bare file name. */
  function paramValue(t, name, v, auto) {
    var def = t && t.params && t.params[name] ? t.params[name].default : "";
    v = String(v === undefined || v === null ? "" : v).trim();
    if (v === "" && auto && auto[name] !== undefined && auto[name] !== null) v = String(auto[name]);
    else if (v === "") v = def === undefined || def === null ? "" : String(def);
    else if (homeRelative(def) && !/^[\/{]/.test(v)) v = "{home}/" + v;
    return v;
  }

  function fillText(text, t, row, auto) {
    return String(text).replace(/\{([A-Za-z_]\w*)\}/g, function (m, name) {
      return t.params && Object.prototype.hasOwnProperty.call(t.params, name) ? paramValue(t, name, (row.params || {})[name], auto) : m;
    }).trim();
  }

  /** A row's command, its params filled (`auto`: values that stand in for an empty param). */
  function fillRun(row, cat, auto) {
    var t = toolOf(row.tool, cat);
    if (!t) return String((row.params || {}).command || "").trim();
    if (t.run === undefined || t.run === null) return "";
    return fillText(t.run, t, row, auto);
  }

  /** A catalog stage without `run` says its stage shape instead (e.g. `{evaluator: zigzag}`):
      that shape, its strings filled like a command's. */
  function fillShape(value, t, row, auto) {
    if (typeof value === "string") return fillText(value, t, row, auto);
    if (Array.isArray(value)) return value.map(function (v) { return fillShape(v, t, row, auto); });
    if (value && typeof value === "object") {
      var o = {};
      for (var k in value) o[k] = fillShape(value[k], t, row, auto);
      return o;
    }
    return value;
  }

  /** The clock an RTL stage aims for when none is typed: an "at least N" on fmax_mhz, as ps. */
  function autoClock(state) {
    var lim = (state.objectives || []).filter(function (o) { return o.metric === "fmax_mhz" && o.label === "atleast"; })[0];
    return lim ? clockPs(lim.value) : null;
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
      else if (o.label === "balance") r = { metric: m, direction: o.direction || naturalDirection(m), balance: true };
      else r = { metric: m, direction: "maximize" };
      if (o.unit && o.unit !== UNITS[m]) r.unit = o.unit;            // a unit the document said (D686)
      else if (!UNITS[m] && unitFor(m, cat)) r.unit = unitFor(m, cat);
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
      budget: { steps: "", passes: "", parallel: "", batch: "", repair_attempts: "", finalists: "", workers: "", prototype: "" },
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

  /** Any value in YAML's flow style (a stage shape's nested values). */
  function inline(v, flow) {
    if (Array.isArray(v)) return "[" + v.map(function (x) { return inline(x, true); }).join(", ") + "]";
    if (v && typeof v === "object") return "{" + Object.keys(v).map(function (k) { return q(k, true) + ": " + inline(v[k], true); }).join(", ") + "}";
    if (v === null || v === undefined) return "null";
    return scalar(v, flow);
  }

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

  /** D832: the language the chosen checks' and measurements' tools take, as the loader infers it
      when the document does not say one ("" when none tells: a script of one's own, a command). */
  function impliedLanguage(state, cat) {
    var hdl = ["systemverilog", "verilog"], found = null;
    (state.checks || []).concat(state.stages || []).forEach(function (row) {
      var t = toolOf(row.tool, cat), langs = t ? (t.languages || []) : [];
      if (!langs.length) return;
      var onlyHdl = langs.every(function (l) { return hdl.indexOf(l) >= 0; });
      if (langs.length !== 1 && !onlyHdl) return;
      found = found === null ? langs.slice() : (found.filter(function (l) { return langs.indexOf(l) >= 0; }).length
        ? found.filter(function (l) { return langs.indexOf(l) >= 0; }) : found);
    });
    if (!found || !found.length) return "";
    if (found.indexOf("systemverilog") >= 0 && found.every(function (l) { return hdl.indexOf(l) >= 0; })) return "systemverilog";
    return found.length === 1 ? found[0] : "";
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
    var auto = { clock_ps: autoClock(state) }, docKeys = {};
    var stages = (state.stages || []).map(function (st, i) {
      var t = toolOf(st.tool, cat), custom = !t || isCustom(st.tool), cmd = fillRun(st, cat, auto), rep = reports(st, cat);
      var shape = t && !custom && (t.run === undefined || t.run === null) && t.stage ? fillShape(t.stage, t, st, auto) : null;
      var gates = (st.gates || []).map(gateOf).filter(Boolean);
      var used = objectives.map(function (o) { return o.metric; }).concat(gates.map(function (g) { return g.metric; }));
      var rtlMeasure = /^flux rtl measure\s/.test(cmd);
      var write = custom || !rtlMeasure || used.some(function (m) { return LOADER_RTL.indexOf(m) < 0 && rep.indexOf(m) >= 0; });
      var needs = custom ? list(st.needs) : /^flux rtl\s/.test(cmd) ? [] : (t.needs || []).slice();
      if (shape) { write = true; if ("needs" in shape) needs = []; }       // the shape says its own needs
      if (t && !custom && t.document) {
        var d = fillShape(t.document, t, st, auto);
        for (var key in d) (docKeys[key] = docKeys[key] || []).push({ value: d[key], stage: String(st.name || "").trim() || "stage" + (i + 1) });
      }
      return { name: String(st.name || "").trim() || "stage" + (i + 1), command: cmd, shape: shape, tool: st.tool, reports: rep,
               metrics: write ? rep : [], needs: needs, gates: gates, estimate: estimateOf(st),
               clock_ps: t && t.params && "clock_ps" in t.params ? paramValue(t, "clock_ps", st.params.clock_ps, auto) : null,
               timeout: String(st.timeout || "").trim() };
    });
    return { checks: checks, stages: stages, objectives: objectives, document: docKeys };
  }

  /** A box's value as the document holds it (D775: written into `flow` with its settings). */
  function flowObj(state, box) {
    var v = state.flow[box];
    var raw = state.agentRaw && state.agentRaw[box];
    if (raw && typeof v === "string" && v.indexOf("agent:") === 0) return raw;      // D728: as written
    if (typeof v === "string" && v.indexOf("agent:") === 0) return { agent: v.slice(6) };
    if (box === "generate" && v === "command") return { command: String(state.generateCommand || "").trim() };
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
      if (state.agentRaw && state.agentRaw[b] && String(v).indexOf("agent:") === 0) return true;   // its own settings (D728)
      return !!choiceOf(b, v);
    });
  }

  /** A measurement's estimator, as the document writes it; null when off. */
  function estimateOf(st) {
    var e = st.estimate || {};
    if (!e.kind || e.kind === "off") return null;
    var out = { kind: e.kind }, m = num(e.margin);
    out.margin = m === null ? null : Math.round(m * 1000) / 100000;
    if (e.kind === "command") out.command = String(e.command || "").trim();
    return out;
  }

  function gateMap(g) {
    var p = [["metric", g.metric]];
    ["at", "below", "within"].forEach(function (k) { if (k in g) p.push([k, g[k] === null ? "?" : g[k]]); });
    return flowMap(p);
  }

  /** The problem document for `state`, as the text of a `problem.yaml` (D786: in a folder named by its id). */
  function buildYaml(state, cat) {
    var r = resolve(state, cat);
    var kept = state.kept || [];                     // kept as written: the server appends them (D686)
    function own(key) { return kept.indexOf(key) < 0; }
    var id = String(state.id || "").trim() || "my_problem";
    // D786: the document is the folder's problem.yaml; the folder's name is the id, not said here
    var out = "# " + id + "/problem.yaml: made with the Flux problem builder.\n" +
              "#     flux task check " + id + "\n" +
              "#     flux task run " + id + "            # until stopped; --passes N for N\n\n";
    out += prose("statement", String(state.statement || "").trim() || "(say what you want made)");
    if (String(state.contract || "").trim()) out += prose("contract", state.contract);
    if (language(state, true)) out += "language: " + q(language(state, true)) + "\n";

    if (!own("parts")) { /* kept */ }
    else if (state.partsMode === "decompose") out += "\nparts: decompose\n";
    else if (state.partsMode === "list" && list(state.parts).length) out += "\nparts: " + flowSeq(list(state.parts)) + "\n";

    for (var dk in r.document) if (own(dk)) out += "\n" + q(dk) + ": " + inline(r.document[dk][0].value, false) + "\n";   // an evaluator's own keys

    if (r.objectives.length && own("objectives")) {
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
    ["steps", "passes", "parallel", "batch", "repair_attempts", "workers"].forEach(function (key) {
      var v = String(b[key] || "").trim();
      if (v !== "") bp.push([key, typed(v)]);
    });
    if (b.prototype) bp.push(["prototype", typed(b.prototype)]);
    if (bp.length && own("budget")) out += "\nbudget: " + flowMap(bp) + "\n";

    // D775: the flow, last -- who works each box, and each box's own settings beside it
    var F = [], kf = state.keptFlow || {}, said = flowSaid(state), boxesKept = !own("flow.boxes");
    var kb = boxesKept ? (kf["flow.boxes"] || {}) : null, SPECIAL = ["dse", "knowledge", "select"];
    var boxVal = function (bx) { return boxesKept ? kb[bx] : said.indexOf(bx) >= 0 ? flowObj(state, bx) : undefined; };
    (boxesKept ? Object.keys(kb) : said).forEach(function (bx) {
      if (SPECIAL.indexOf(bx) < 0) F.push("  " + q(bx) + ": " + inline(boxesKept ? boxVal(bx) : toSurface(bx, boxVal(bx)), false));
    });

    // dse: its policy or agent, the space it searches, where it starts
    var dv = boxesKept ? boxVal("dse") : toSurface("dse", boxVal("dse")), D = [];
    var space = (state.space || []).filter(function (x) { return String(x.knob || "").trim() && list(x.choices).length; });
    if (!own("flow.dse.space")) { if (kf["flow.dse.space"] !== undefined) D.push("    space: " + inline(kf["flow.dse.space"], false)); }
    else if (space.length) {
      D.push("    space:");
      space.forEach(function (x) { D.push("      " + q(x.knob.trim()) + ": " + flowSeq(list(x.choices).map(typed))); });
    }
    if (kf["flow.dse.seeds"] !== undefined) D.push("    seeds: " + inline(kf["flow.dse.seeds"], false));
    if (D.length) {
      F.push("  orchestrate:                # a search: the points it tries (D797)");
      if (dv && typeof dv === "object" && !Array.isArray(dv)) Object.keys(dv).forEach(function (k) { F.push("    " + q(k) + ": " + inline(dv[k], false)); });
      else if (dv !== undefined) F.push("    policy: " + inline(dv, false));
      F = F.concat(D);
    } else if (dv !== undefined) F.push("  orchestrate: " + inline(dv, false));

    // test: the checks, in order
    var checks = r.checks.filter(function (c) { return c.run; });
    if (!own("flow.test")) { if (kf["flow.test"] !== undefined) F.push("  test: " + inline(kf["flow.test"], false)); }
    else if (checks.length) {
      F.push("  test:                     # by name; each must pass, in order");
      checks.forEach(function (c) {
        var p = [["run", c.run]];
        if (c.count_re) p.push(["count_re", c.count_re]);
        if (c.timeout) p.push(["timeout_s", typed(c.timeout)]);
        F.push("    " + q(String(c.name || "test").trim() || "test") + ": " + (p.length === 1 ? q(c.run) : flowMap(p)));
      });
    }

    // measure: each stage by its name, cheapest first
    if (!own("flow.measure")) {
      var km = kf["flow.measure"];
      if (km && typeof km === "object") { F.push("  measure:"); Object.keys(km).forEach(function (n) { F.push("    " + q(n) + ": " + inline(km[n], false)); }); }
    } else if (r.stages.length) {
      F.push("  measure:                  # cheapest first");
      r.stages.forEach(function (st) {
        var L = [];
        if (st.shape) for (var key in st.shape) L.push(q(key) + ": " + inline(st.shape[key], false));
        else L.push("command: " + q(st.command || "(the command)"));
        if (st.metrics.length) L.push("metrics: " + flowSeq(st.metrics));
        if (st.needs.length) L.push("needs: " + flowSeq(st.needs));
        if (st.estimate) {
          var ep = [["kind", st.estimate.kind], ["margin", st.estimate.margin === null ? "?" : st.estimate.margin]];
          if (st.estimate.kind === "command") ep.push(["command", st.estimate.command || "(the command)"]);
          L.push("estimate: " + flowMap(ep));                // estimated first; a likely failure is skipped
        }
        if (st.timeout) L.push("timeout_s: " + scalar(typed(st.timeout)));
        if (st.gates.length === 1) L.push("cutoff: " + gateMap(st.gates[0]));   // go on only if
        else if (st.gates.length > 1) L.push("cutoff: [" + st.gates.map(gateMap).join(", ") + "]");
        if (L.length === 1 && L[0].indexOf("command: ") === 0) F.push("    " + q(st.name) + ": " + L[0].slice(9));
        else { F.push("    " + q(st.name) + ":"); L.forEach(function (l) { F.push("      " + l); }); }
      });
    }

    // knowledge: what is read, and who digests it
    if (!own("flow.knowledge")) { if (kf["flow.knowledge"] !== undefined) F.push("  knowledge: " + inline(kf["flow.knowledge"], false)); }
    else {
      var kv = boxVal("knowledge"), K = {}, kfiles = list(state.knowledgeFiles);
      if (kfiles.length) K.files = kfiles;
      var dg = (state.flow || {}).digest;                  // D784, D791: the Digest box -- the model unless an agent
      if (typeof dg === "string" && dg.indexOf("agent:") === 0) K.digest = dg.slice(6);      // D830: by name, as every box
      var ls = (state.flow || {}).lessons;                 // D796: the Learn box
      if (ls && ls !== "off") K.lessons = typeof ls === "string" && ls.indexOf("agent:") === 0 ? ls.slice(6) : ls;
      if (kv === "none" || kv === "off") K = K.lessons ? { off: true, lessons: K.lessons } : { off: true };
      if (Object.keys(K).length === 1 && K.off) F.push("  knowledge: off");
      else if (Object.keys(K).length) F.push("  knowledge: " + inline(K, false));
    }

    // select: how many reach the last stage, and who chooses
    var sv = boxVal("select"), S = sv && typeof sv === "object" && !Array.isArray(sv) ? Object.assign({}, sv) : {};
    var fin = String(b.finalists || "").trim();
    if (fin !== "") S.finalists = typed(fin);
    if (!boxesKept) S = toSurface("select", S);                          // D795: {by: claude, finalists: 2}
    if (typeof S === "string" || Object.keys(S).length) F.push("  select: " + inline(S, false));
    else if (sv !== undefined) F.push("  select: " + inline(boxesKept ? sv : toSurface("select", sv), false));

    if (F.length) out += "\nflow:\n" + F.join("\n") + "\n";
    return out;
  }

  // ------------------------------------------------------------------ the checklist
  function placeholders(cmd) {
    var out = [], re = /\{([A-Za-z_]\w*)\}/g, m;
    String(cmd || "").replace(/"[^"]*"|'[^']*'/g, " ").split(/\s+/).forEach(function (tok) {
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
    var implied = impliedLanguage(state, cat);
    if (!lang && !implied) note("The language is not said and no chosen tool tells it: the design is a .txt file -- choose one if the checks need another kind.");
    if (!lang) lang = implied;                         // D832: what the loader will take

    function params(row, what, nm) {
      var t = toolOf(row.tool, cat);
      if (t && lang && !fits(t, lang)) warn(what + " \"" + nm + "\" (" + t.title + ") is made for " + t.languages.join(", ") + "; the design is " + lang + ".");
      for (var k in (t && t.params) || {}) {
        var v = String((row.params || {})[k] === undefined ? "" : row.params[k]).trim(), def = t.params[k].default;
        var optional = /\(empty/i.test(t.params[k].label || "");
        if (!v && !optional && (def === "" || def === undefined || def === null)) error(what + " \"" + nm + "\" needs its " + t.params[k].label.toLowerCase() + ".");
        else if (v && typeof def === "number" && !(num(v) > 0) && !/^\{\w+\}$/.test(v)) error(what + " \"" + nm + "\": " + t.params[k].label.toLowerCase() + " should be a number above 0.");
      }
    }

    // the checks
    var kept = state.kept || [];
    if (!r.checks.length && kept.indexOf("flow.test") < 0) error("Add a check: a design that fails it goes no further.");
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
    if (!r.stages.length && kept.indexOf("flow.measure") < 0) error("Add a measurement: designs are compared on what it reports.");
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
      if (rs.estimate) {
        var em = num((st.estimate || {}).margin);
        if (em === null || em < 0 || em > 100) error("Measurement \"" + nm + "\": the estimate's margin is a percentage from 0 to 100.");
        if (rs.estimate.kind === "command" && !rs.estimate.command) error("Measurement \"" + nm + "\": say the command that estimates it.");
      }
      if (last && (st.gates || []).length) warn("The last measurement's gate has nothing after it to hold back.");
    });

    // an evaluator's document keys (its workload): one value per document
    for (var key in r.document) {
      var vals = r.document[key];
      var differ = vals.filter(function (x) { return JSON.stringify(x.value) !== JSON.stringify(vals[0].value); });
      if (differ.length) error("Measurements " + vals.map(function (x) { return "\"" + x.stage + "\""; }).join(", ") + " name different " + key + " files: one " + key + " per document.");
    }

    // the clock an RTL measurement aims for
    var clocked = r.stages.filter(function (st) { return st.clock_ps !== null; });
    var fmax = (state.objectives || []).filter(function (o) { return o.metric === "fmax_mhz"; });
    var searched = clocked.some(function (st) { return /^\{\w+\}$/.test(String(st.clock_ps)); });
    if (clocked.length && !searched && fmax.some(function (o) { return o.label === "max"; }) &&
        !fmax.some(function (o) { return o.label === "atleast"; })) {
      warn("A fixed clock biases 'as fast as possible': set it tight, or search it (add clock_ps to the settings to search and use {clock_ps}).");
    }

    // the objective
    if (!r.objectives.length && kept.indexOf("objectives") < 0) error("Add an objective: which reported numbers matter, and how.");
    var metricsSeen = {}, balanced = 0;
    (state.objectives || []).forEach(function (o, i) {
      var m = String(o.metric || "").trim(), ro = r.objectives[i];
      if (!m) return;
      if (metricsSeen[m]) warn("The objective names " + m + " twice.");
      metricsSeen[m] = 1;
      var by = r.stages.filter(function (st) { return st.reports.indexOf(m) >= 0; }).length;
      if (!by) error("The objective uses " + m + ", which no measurement reports.");
      else if (by < r.stages.length) {
        var missing = r.stages.filter(function (st) { return st.reports.indexOf(m) < 0; }).map(function (st) { return st.name; });
        error("The objective uses " + m + ", which " + missing.join(", ") + " does not report: every measurement must report every objective's number; put different tools in separate problems or pick metrics they all report.");
      }
      if ((o.label === "atleast" || o.label === "atmost") && num(o.value) === null) error("Say the number " + m + " must be " + (o.label === "atleast" ? "at least." : "at most."));
      if (ro && ro.balance) balanced++;
    });
    if (balanced === 1) warn("Balance needs two numbers or more: one alone is just maximise or minimise.");

    // the flow
    FLOW_BOXES.forEach(function (b) {
      var v = flow[b];
      if (typeof v === "string" && v.indexOf("agent:") === 0 && DELEGABLE.indexOf(b) < 0 && b !== "generate" && b !== "knowledge" && b !== "digest") {
        error("\"" + BOXES[b].title + "\" (" + b + ") is never handed to a coding agent: it establishes the facts.");
      } else if (v !== undefined && !choiceOf(b, v)) {
        error("\"" + BOXES[b].title + "\" (" + b + ") cannot be \"" + v + "\".");
      }
    });

    (state.space || []).forEach(function (x) {
      if (String(x.knob || "").trim() && !list(x.choices).length) error("The setting \"" + x.knob.trim() + "\" has no choices.");
    });
    var searching = flow.dse && flow.dse !== "none";
    if (flow.dse === "pareto" && r.objectives.length < 2) error("The trade-off front (pareto) needs two objectives or more.");
    if (searching && !knobs.length) error("A search needs settings to walk: add some under Advanced > Settings to search.");
    if (!searching && knobs.length) warn("The settings are only searched when \"Search the settings\" is on.");
    if (searching && flow.orchestrate && flow.orchestrate !== "default") warn("With a search, the search picks the next job; \"Pick the next job\" is left out.");
    if (flow.generate === "command" && !String(state.generateCommand || "").trim()) error("Say the command that writes each design.");

    var cmds = r.checks.map(function (c) { return ["check \"" + c.name + "\"", c.run]; });
    r.stages.forEach(function (st) {
      cmds.push(["measurement \"" + st.name + "\"", st.shape ? JSON.stringify(st.shape).replace(/[",:{}\[\]]/g, " ") : st.command]);
      if (st.estimate && st.estimate.command) cmds.push(["the estimate of \"" + st.name + "\"", st.estimate.command]);
    });
    if (flow.generate === "command") cmds.push(["the design script", state.generateCommand]);
    cmds.forEach(function (c) {
      placeholders(c[1]).forEach(function (p) {
        if (BUILTIN_SUBS.indexOf(p) < 0 && knobs.indexOf(p) < 0) error("In " + c[0] + ", {" + p + "} is neither a setting to search nor one of Flux's own.");
      });
    });

    ["steps", "passes", "parallel", "batch", "repair_attempts", "finalists", "workers"].forEach(function (key) {
      var v = String((state.budget || {})[key] || "").trim();
      if (v && !/^\d+$/.test(v)) error("Budget \"" + key + "\" should be a whole number.");
    });

    var agents = {};
    flowSaid(state).forEach(function (b) { if (String(flow[b]).indexOf("agent:") === 0 && flow[b] !== "agent:custom") agents[flow[b].slice(6)] = 1; });
    if (Object.keys(agents).length) note("The coding agent " + Object.keys(agents).join(", ") + " must be installed where it runs.");
    var files = {};
    cmds.forEach(function (c) {
      var re = /\{home\}\/([\w.\-\/]+)/g, m;
      while ((m = re.exec(String(c[1] || "")))) files[m[1]] = 1;
    });
    for (var dkey in r.document) r.document[dkey].forEach(function (x) {
      var m2 = /^\{home\}\/([\w.\-\/]+)$/.exec(String(x.value)); if (m2) files[m2[1]] = 1;
    });
    list(state.knowledgeFiles).forEach(function (f) { files[f] = 1; });
    var fl = Object.keys(files);
    if (fl.length) note("Put these beside the document: " + fl.join(", ") + ".");
    return msgs;
  }

  // ------------------------------------------------------------------ a document read back (D686)
  /* `fromDoc(raw, normal, cat)`: an existing document as a state, for the web configurator.
     `raw` is the document as written (yaml.safe_load), `normal` the loader's form of it
     (TaskSpec.to_dict: gate and stages as lists, commands as argv, `flux` spelled
     `{python} -W ignore -m flux_cli.main`). A command is matched against the catalog's
     templates; what matches none is a custom row with the command itself. Whatever the state
     cannot say is `kept`: those top-level keys are carried over exactly as written, and each
     is named in `notes`. Nothing is dropped silently. */
  var CHECK_TIMEOUT = 120, STAGE_TIMEOUT = 600;
  var FLUX_ARGV = ["{python}", "-W", "ignore", "-m", "flux_cli.main"];
  var STATE_KEYS = ["id", "statement", "contract", "language", "knowledge", "parts", "space", "flow", "gate", "stages",
                    "objectives", "budget"];
  var BUDGET_KEYS = ["steps", "passes", "parallel", "batch", "repair_attempts", "finalists", "workers", "prototype"];

  function argvOf(run) {
    var a = Array.isArray(run) ? run.map(String) : String(run || "").trim().split(/\s+/).filter(Boolean);
    var flux = FLUX_ARGV.every(function (t, i) { return a[i] === t; });
    return flux ? ["flux"].concat(a.slice(FLUX_ARGV.length)) : a;
  }

  /** A shell word as `shlex.split` reads it back. */
  function shellWord(t) {
    return /^[A-Za-z0-9_@%+=:,.\/{}-]+$/.test(t) ? t : "'" + String(t).replace(/'/g, "'\"'\"'") + "'";
  }

  /** The catalog tool of `role` whose command template this argv fills, and its params. */
  function matchTool(argv, role, cat) {
    var tools = (cat || CATALOG).filter(function (t) { return t.role === role && t.run && !isCustom(t.id); });
    for (var i = 0; i < tools.length; i++) {
      var t = tools[i], tmpl = String(t.run).trim().split(/\s+/), params = {}, ok = tmpl.length === argv.length;
      for (var k = 0; ok && k < tmpl.length; k++) {
        var m = /^\{([A-Za-z_]\w*)\}$/.exec(tmpl[k]);
        if (m && t.params && Object.prototype.hasOwnProperty.call(t.params, m[1])) params[m[1]] = shown(argv[k]);
        else if (tmpl[k] !== argv[k]) ok = false;
      }
      if (ok) return { tool: t, params: params };
    }
    return null;
  }

  function checkTypeOf(toolId, name) {
    if (toolId === "rtl-lint") return name === "compile" ? "compile" : "lint";
    if (toolId === "champsim-build") return "compile";
    if (toolId === "rtl-golden") return "golden";
    if (toolId === "python-test-script" || toolId === "champsim-check") return "test";
    return "custom";
  }

  /** D775: a document of this layout in the shape the reader below reads: each box's settings
      taken out of `flow` -- test the gate, measure the stages (a list), dse's space and seeds,
      knowledge's files, select's finalists. */
  /** D795: who works a box, as a document says it (`model`, `off`, `{by: claude, ...}`) and as
      the configurator holds it (the loop's inside words: llm, none, {agent: ...}). */
  var BY_WORDS = {};                     // the configurator holds the document's own words; only agents differ
  var PRESETS = ["opencode", "claude", "codex"];
  var AGENT_OPTS = ["session", "timeout_s", "questions", "max_questions", "wait_s", "bin", "args", "probe", "allow", "output", "resume", "name"];
  function toInner(box, v) {
    var words = BY_WORDS[box] || {};
    if (v === false) v = "off";
    if (typeof v === "string") {
      if (box === "dse") return v;
      if (PRESETS.indexOf(v) >= 0) return { agent: v };
      return words[v] !== undefined ? words[v] : v;
    }
    if (!v || typeof v !== "object" || Array.isArray(v) || !("by" in v)) return v;
    var rest = {}, opts = {}, k;
    for (k in v) if (k !== "by") (AGENT_OPTS.indexOf(k) >= 0 ? opts : rest)[k] = v[k];
    if (v.by === "model") {
      if (box === "dse") return Object.keys(rest).length ? Object.assign(rest, { policy: "model" }) : "model";
      return Object.keys(rest).length ? rest : (words.model || "model");
    }
    var spec = typeof v.by === "object" ? Object.assign({}, v.by, opts) : Object.keys(opts).length ? Object.assign({ preset: v.by }, opts) : v.by;
    return Object.assign(rest, { agent: spec });
  }
  function toSurface(box, v) {
    var words = BY_WORDS[box] || {}, back = {}, k;
    for (k in words) back[words[k]] = k;
    if (typeof v === "string") return box === "dse" ? (v === "llm" ? { by: "model" } : v) : (back[v] !== undefined ? back[v] : v);
    if (!v || typeof v !== "object" || Array.isArray(v) || !("agent" in v)) return v;
    var rest = {}, a = v.agent;
    for (k in v) if (k !== "agent") rest[k] = v[k];
    var who = a && typeof a === "object" && a.preset ? Object.assign({ by: a.preset }, a) : { by: a };
    delete who.preset;
    var plain = Object.keys(who).length === 1 && typeof who.by === "string" && !Object.keys(rest).length;
    return plain && box !== "dse" ? who.by : Object.assign(who, rest);
  }

  /** D797: whether an `orchestrate` value is a search -- a policy's word, phases, or a space. */
  function isSearch(v) {
    if (Array.isArray(v)) return true;
    if (typeof v === "string") return DSE_POLICIES.indexOf(v) >= 0 || ["control", "phases"].indexOf(v) >= 0;
    return !!v && typeof v === "object" && ("space" in v || "seeds" in v || "policy" in v);
  }

  function unlifted(doc) {
    if (!doc || typeof doc !== "object") return doc;
    var out = {}, k;
    for (k in doc) if (k !== "flow") out[k] = doc[k];
    var fl = doc.flow && typeof doc.flow === "object" && !Array.isArray(doc.flow) ? doc.flow : null;
    if (!fl) return out;
    var f = {};
    for (k in fl) {
      var key = k === "orchestrate" && isSearch(fl[k]) ? "dse" : k;            // D797: a search is the orchestrator's
      var val = fl[k];
      if (k === "knowledge" && val && typeof val === "object" && !Array.isArray(val) && "digest" in val) {   // D830
        val = Object.assign({}, val);
        var dg2 = val.digest; delete val.digest;
        if (dg2 && typeof dg2 === "object") Object.assign(val, dg2); else if (dg2 && dg2 !== "model") val.by = dg2;
      }
      f[key] = ["test", "measure"].indexOf(k) >= 0 ? val : toInner(key, val);   // D795
    }
    if ("test" in f) { out.gate = f.test; delete f.test; }
    if ("measure" in f) {
      var m = f.measure || {};
      out.stages = Object.keys(m).map(function (n) {
        var v = m[n];
        return typeof v === "string" || Array.isArray(v) ? { name: n, command: v } : Object.assign({ name: n }, v || {});
      });
      delete f.measure;
    }
    if (f.dse && typeof f.dse === "object" && !Array.isArray(f.dse) && ("space" in f.dse || "seeds" in f.dse || "policy" in f.dse)) {
      var d = Object.assign({}, f.dse);
      if ("space" in d) { out.space = d.space; delete d.space; }
      if ("seeds" in d) { out.seeds = d.seeds; delete d.seeds; }
      if ("policy" in d) f.dse = d.policy; else if (Object.keys(d).length) f.dse = d; else delete f.dse;
    }
    if (f.knowledge && typeof f.knowledge === "object" && !Array.isArray(f.knowledge) && "lessons" in f.knowledge) {
      f.lessons = toInner("lessons", f.knowledge.lessons);                  // D796: the Learn box
      f.knowledge = Object.assign({}, f.knowledge); delete f.knowledge.lessons;
      if (!Object.keys(f.knowledge).length) delete f.knowledge;
    }
    if ("knowledge" in f) {
      var kn = f.knowledge;
      if (kn === "off" || kn === false) f.knowledge = ["none"];          // YAML reads a bare `off` as false
      else if (kn && typeof kn === "object" && !Array.isArray(kn)) {
        var read = {};
        ["files", "sheet", "text"].forEach(function (x) { if (x in kn) read[x] = kn[x]; });
        if (Object.keys(read).length) out.knowledge = read;
        if (kn.agent !== undefined) f.knowledge = { agent: kn.agent };
        else if (kn.off) f.knowledge = ["none"];
        else delete f.knowledge;
      }
    }
    if (f.select && typeof f.select === "object" && !Array.isArray(f.select) && "finalists" in f.select) {
      var sl = Object.assign({}, f.select);
      out.budget = Object.assign({}, out.budget || {}, { finalists: sl.finalists });
      delete sl.finalists;
      if (Object.keys(sl).length) f.select = sl; else delete f.select;
    }
    if (Object.keys(f).length) out.flow = f;
    return out;
  }

  /** D775: where a part the configurator keeps as written lives now. */
  var KEPT_AT = { gate: "flow.test", stages: "flow.measure", space: "flow.dse.space", seeds: "flow.dse.seeds",
                  knowledge: "flow.knowledge", flow: "flow.boxes" };

  /** A kept flow part's value, from the document as written (this layout). */
  function keptValue(doc, at) {
    var fl = (doc && doc.flow) || {}, out, k;
    if (at === "flow.test") return fl.test;
    if (at === "flow.measure") return fl.measure;
    if (at === "flow.knowledge") return fl.knowledge;
    var search = isSearch(fl.orchestrate) ? fl.orchestrate : null;      // D797: the search is orchestrate's
    if (at === "flow.dse.space") return search && search.space;
    if (at === "flow.dse.seeds") return search && search.seeds;
    if (at === "flow.boxes") {                      // every box's choice, without what is kept or edited apart
      out = {};
      for (k in fl) {
        if (["test", "measure"].indexOf(k) >= 0) continue;
        var v = fl[k];
        if (k === "orchestrate" && isSearch(v)) k = "dse";
        if (k === "knowledge") {                     // its choice (off, an agent, a digest); its files apart
          if (v && typeof v === "object" && !Array.isArray(v)) {
            v = Object.assign({}, v); ["files", "sheet", "text", "library"].forEach(function (x) { delete v[x]; });
            if (!Object.keys(v).length) continue;
          }
        }
        if (k === "dse" && v && typeof v === "object" && !Array.isArray(v)) {
          v = Object.assign({}, v); delete v.space; delete v.seeds;
          if (!Object.keys(v).length) continue;
          if ("policy" in v && Object.keys(v).length === 1) v = v.policy;
        }
        if (k === "select" && v && typeof v === "object" && !Array.isArray(v)) {
          v = Object.assign({}, v); delete v.finalists;
          if (!Object.keys(v).length) continue;
        }
        out[k] = v;
      }
      return out;
    }
    return undefined;
  }

  function fromDoc(raw, normal, cat) {
    var asWritten = raw || {};
    raw = unlifted(raw || {}); normal = unlifted(normal) || raw;
    var s = base(), kept = [], notes = [];
    s.keptFlow = {};
    function keep(key, why) {
      var at = KEPT_AT[key] || key;
      if (kept.indexOf(at) < 0 && raw[key] !== undefined) {
        kept.push(at); notes.push("`" + at + "` is kept as written: " + why + ".");
        if (at.indexOf("flow.") === 0) s.keptFlow[at] = keptValue(asWritten, at);
      }
    }
    s.id = String(raw.id || normal.id || "");
    s.statement = String(raw.statement || normal.statement || "").trim();
    s.contract = String(raw.contract || "").trim();
    var lang = String(raw.language || normal.language || "");
    if (lang && LANGUAGES.indexOf(lang.toLowerCase()) >= 0) s.language = lang.toLowerCase();
    else if (lang) { s.language = "other"; s.languageOther = lang; }

    // knowledge: the files the model reads (the papers are library/'s, D791)
    var kn = raw.knowledge;
    if (kn && typeof kn === "object" && !Array.isArray(kn) && Object.keys(kn).every(function (k) { return k === "files"; })) {
      if (kn.files) s.knowledgeFiles = (Array.isArray(kn.files) ? kn.files : [kn.files]).join(", ");
    } else if (kn) keep("knowledge", "a methods sheet or inline notes, which the configurator does not edit");

    // parts: decompose, or names alone
    var pa = raw.parts;
    if (pa === "decompose") s.partsMode = "decompose";
    else if (Array.isArray(pa) && pa.every(function (x) { return typeof x === "string"; })) { s.partsMode = "list"; s.parts = pa.join(", "); }
    else if (pa !== undefined && pa !== null) keep("parts", "parts with their own statements");

    // space: knob -> choices
    var sp = raw.space;
    if (sp && typeof sp === "object" && !Array.isArray(sp)) {
      var plain = Object.keys(sp).every(function (k) { return Array.isArray(sp[k]); });
      if (plain) s.space = Object.keys(sp).map(function (k) { return { knob: k, choices: sp[k].join(", ") }; });
      else keep("space", "knobs that move with others (`when`)");
    }

    // flow: each box as one of its choices
    var fl = normal.flow || raw.flow || {}, flowOk = true;
    Object.keys(fl).forEach(function (box) {
      var v = fl[box];
      if (["test", "measure", "records"].indexOf(box) >= 0) return;
      if (!BOXES[box]) { flowOk = false; return; }
      if (box === "knowledge") {
        var ls = Array.isArray(v) ? v : [v];
        if (v && typeof v === "object" && !Array.isArray(v) && typeof v.agent === "string" && choiceOf("digest", "agent:" + v.agent)) { s.flow.digest = "agent:" + v.agent; return; }   // D773, D784
        if (ls.length === 1 && ls[0] === "none") s.flow.knowledge = "none";
        else if (!(ls.length === 0 || (ls.length === 1 && ls[0] === "library"))) flowOk = false;
        return;
      }
      if (typeof v === "string" && choiceOf(box, v)) { s.flow[box] = v; return; }
      if (v && typeof v === "object" && typeof v.agent === "string" && choiceOf(box, "agent:" + v.agent)) { s.flow[box] = "agent:" + v.agent; return; }
      if (v && typeof v === "object" && v.agent && typeof v.agent === "object") {      // D728: its own settings, kept as written
        var pre = typeof v.agent.preset === "string" && choiceOf(box, "agent:" + v.agent.preset) ? v.agent.preset : null;
        if (!pre && DELEGABLE.indexOf(box) < 0 && box !== "generate") { flowOk = false; return; }
        s.flow[box] = "agent:" + (pre || "custom");
        (s.agentRaw = s.agentRaw || {})[box] = v;
        return;
      }
      if (box === "generate" && v && typeof v === "object" && v.command !== undefined) {
        s.flow.generate = "command"; s.generateCommand = argvOf(v.command).map(shellWord).join(" "); return;
      }
      flowOk = false;
    });
    if (!flowOk) { s.flow = defaultFlow(); keep("flow", "some of its choices are not the configurator's (an agent's own settings, a catalog, knowledge sources)"); }

    // gate: checks in order
    var gate = normal.gate;
    var gateOk = true;
    if (typeof gate === "string" || Array.isArray(gate)) gate = { test: gate };
    if (gate && typeof gate === "object") {             // D789: a map by name, in order
      gate = Object.keys(gate).map(function (n) {
        var c = gate[n];
        return (c && typeof c === "object" && !Array.isArray(c)) ? Object.assign({ name: n }, c) : { name: n, run: c };
      });
    }
    (gate || []).forEach(function (c) {
      if (c.fail_re) gateOk = false;
      var argv = argvOf(c.run), m = matchTool(argv, "check", cat);
      var custom = !m || (c.count_re && c.count_re !== "(\\d+) failing");
      var row = custom ? { type: "custom", tool: "custom-check", name: c.name, params: { command: argv.map(shellWord).join(" ") },
                           count_re: c.count_re && c.count_re !== "(\\d+) failing" ? c.count_re : "", timeout: "" }
                       : { type: checkTypeOf(m.tool.id, c.name), tool: m.tool.id, name: c.name, params: paramsOf(m.tool, m.params),
                           count_re: "", timeout: "" };
      if (c.timeout_s && Number(c.timeout_s) !== CHECK_TIMEOUT) row.timeout = String(c.timeout_s);
      s.checks.push(row);
    });
    if (!gateOk) { s.checks = []; keep("gate", "a failure pattern (`fail_re`)"); }

    // stages: a catalog tool, an evaluator, or a command of one's own
    var stagesOk = true;
    var rawStages = {};                          // what the document wrote: the loader adds patterns of its own
    (Array.isArray(raw.stages) ? raw.stages : []).forEach(function (x) { if (x && x.name) rawStages[x.name] = x; });
    (normal.stages || []).forEach(function (st) {
      var said = rawStages[st.name] || {};
      if (said.metrics_re && Object.keys(said.metrics_re).length) { stagesOk = false; return; }
      var row = null;
      if (st.command) {
        var argv = argvOf(st.command), m = matchTool(argv, "stage", cat);
        if (m) row = { tool: m.tool.id, name: st.name, params: paramsOf(m.tool, m.params), metrics: "", needs: "", gates: [] };
        else row = { tool: "custom-stage", name: st.name, params: { command: argv.map(shellWord).join(" ") },
                     metrics: (st.metrics || []).join(", "), needs: (st.needs || []).join(", "), gates: [] };
      } else if (st.evaluator) {
        var ev = (cat || CATALOG).filter(function (t) { return t.role === "stage" && t.stage && t.stage.evaluator === st.evaluator; })[0];
        if (!ev) { stagesOk = false; return; }
        var p = {};
        for (var dk in ev.document || {}) {
          var mm = /^\{([A-Za-z_]\w*)\}$/.exec(String(ev.document[dk]));
          if (mm && raw[dk] !== undefined) p[mm[1]] = shown(String(raw[dk]));
        }
        row = { tool: ev.id, name: st.name, params: paramsOf(ev, p), metrics: "", needs: "", gates: [] };
      } else { stagesOk = false; return; }                         // measured by the world's own code
      var cuts = st.cutoff ? (Array.isArray(st.cutoff) ? st.cutoff : [st.cutoff]) : [];
      row.gates = cuts.map(function (c) {
        if ("within" in c) return { metric: c.metric, rule: "within", value: String(Math.round(Number(c.within) * 100000) / 1000) };
        if ("below" in c) return { metric: c.metric, rule: "below", value: String(c.below) };
        return { metric: c.metric, rule: "at", value: String(c.at) };
      });
      var e = st.estimate;
      row.estimate = e ? { kind: e.kind, margin: String(Math.round(Number(e.margin || 0) * 100000) / 1000), command: e.command ? argvOf(e.command).map(shellWord).join(" ") : "" }
                       : { kind: "off", margin: "5", command: "" };
      if (st.timeout_s && Number(st.timeout_s) !== STAGE_TIMEOUT) row.timeout = String(st.timeout_s);
      s.stages.push(row);
    });
    if (!stagesOk) { s.stages = []; keep("stages", "a stage measured by the world's code or read with its own patterns (`metrics_re`)"); }
    if (raw.workload !== undefined && !s.stages.some(function (r) { var t = toolOf(r.tool, cat); return t && t.document && "workload" in t.document; }))
      keep("workload", "no evaluator stage here writes it");

    // objectives: the labels the configurator has
    var objOk = true, rawObjs = Array.isArray(raw.objectives) ? raw.objectives : [];
    (normal.objectives || []).forEach(function (o, i) {
      // what the document wrote decides; the loader fills stage, tie, margin and unit of its own
      var said = rawObjs[i] && typeof rawObjs[i] === "object" ? rawObjs[i] : {};
      var extra = Object.keys(said).filter(function (k) { return ["metric", "direction", "goal", "balance", "unit"].indexOf(k) < 0; });
      if (extra.length) objOk = false;
      var label = o.balance ? "balance" : o.goal !== undefined && o.goal !== null ? (o.direction === "minimize" ? "atmost" : "atleast")
                : o.direction === "minimize" ? "min" : "max";
      s.objectives.push({ metric: o.metric, label: label, value: o.goal !== undefined && o.goal !== null ? String(o.goal) : "",
                          unit: o.unit || "", direction: o.balance ? o.direction : "" });
    });
    if (!objOk) { s.objectives = []; keep("objectives", "objectives with a stage, a tie or a margin of their own"); }

    // budget
    var bu = raw.budget || {};
    if (Object.keys(bu).every(function (k) { return BUDGET_KEYS.indexOf(k) >= 0; }))
      BUDGET_KEYS.forEach(function (k) { if (bu[k] !== undefined && bu[k] !== null) s.budget[k] = String(bu[k]); });
    else {
      keep("budget", "it sets " + Object.keys(bu).filter(function (k) { return BUDGET_KEYS.indexOf(k) < 0; }).join(", "));
      if (bu.finalists !== undefined && bu.finalists !== null) s.budget.finalists = String(bu.finalists);   // D775: flow.select's own
    }

    Object.keys(raw).forEach(function (k) {
      if (STATE_KEYS.indexOf(k) < 0 && k !== "workload") keep(k, k === "seeds" ? "the search's starting points" : "the configurator does not edit it");
    });
    s.kept = kept;
    return { state: s, kept: kept, notes: notes };
  }

  /** A drawing node's title, as the box shows it (D727: the web's step bar names its box). */
  function boxTitle(id) {
    return { "crit-division": "Critic: division", "crit-part": "Critic: each part", "crit-decision": "Critic: decision",
             parts: "Parts" }[id] || (BOXES[id] || {}).title || id;
  }

  var api = { boxTitle: boxTitle, buildYaml: buildYaml, check: check, fromDoc: fromDoc, argvOf: argvOf, resolve: resolve, setCatalog: setCatalog, toolOf: toolOf,
              toolsFor: toolsFor, newCheck: newCheck, setCheckTool: setCheckTool, newStage: newStage,
              newObjective: newObjective, reports: reports, reported: reported, fillRun: fillRun,
              describeObjectives: describeObjectives, naturalDirection: naturalDirection, clockPs: clockPs,
              CHECK_TYPES: CHECK_TYPES, stageTools: stageTools, nextStageTool: nextStageTool, abbreviate: abbreviate, autoClock: autoClock, LABELS: LABELS,
              BOXES: BOXES, FLOW_BOXES: FLOW_BOXES, DELEGABLE: DELEGABLE, NEVER: NEVER, LANGUAGES: LANGUAGES,
              AGENTS: AGENTS, DSE_POLICIES: DSE_POLICIES, halfOf: halfOf, defaultFlow: defaultFlow, base: base, isFixed: isFixed,
              explain: explain, explainEstimate: explainEstimate };

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

  /** `opts` (the web configurator, D686): `state` to start from, `save(yaml, state)` returning a
      promise of a line to show, `notes` on what is kept as written. */
  function mount(host, readonly, opts) {
    opts = opts || {};
    var stepped = opts.stepped !== false;              // D826: steps unless a page asks for the whole form
    var state = opts.state || base();
    var openBox = null, openNode = null;
    var parts = {};

    function changed(structural) {
      parts.touched = true;
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
      // compact: the hint is the input's tooltip, not a line of its own
      if (opts.compact && opts.hint) input.setAttribute("title", opts.hint);
      return h("label", { class: "fc-field" + (opts.wide ? " fc-wide" : "") + (opts.narrow ? " fc-narrow" : "") + (opts.grow ? " fc-grow" : "") },
               [h("span", { class: "fc-label", text: label }), input, opts.hint && !opts.compact ? h("small", { text: opts.hint }) : null]);
    }

    function section(title, kids, cls) {
      return h("section", { class: "fc-section " + (cls || "") }, [h("h3", { text: title })].concat(kids));
    }

    /** A section whose title carries a short note on the same line. */
    function titled(title, note, kids) {
      return h("section", { class: "fc-section" }, [h("h3", {}, [title].concat(note))].concat(kids));
    }

    function button(text, fn, cls) {
      return h("button", { type: "button", class: "fc-btn " + (cls || ""), text: text, on: { click: fn } });
    }

    // -- ordered boxes: up, down, remove, and a "more" drawer
    var moreOpen = typeof WeakSet === "function" ? new WeakSet() : { has: function () { return false; }, add: function () {}, delete: function () {} };

    function move(listOf, i, d) {
      var j = i + d;
      if (j < 0 || j >= listOf.length) return;
      var x = listOf[i]; listOf[i] = listOf[j]; listOf[j] = x;
      changed(true);
    }

    function rowButtons(listOf, i, extra) {
      return h("div", { class: "fc-row-buttons" }, (extra || []).concat([
        button("↑", function () { move(listOf, i, -1); }, "fc-small fc-icon" + (i === 0 ? " fc-hidden" : "")),
        button("↓", function () { move(listOf, i, 1); }, "fc-small fc-icon" + (i === listOf.length - 1 ? " fc-hidden" : "")),
        button("×", function () { listOf.splice(i, 1); changed(true); }, "fc-small fc-icon")]));
    }

    function moreButton(row) {
      var open = moreOpen.has(row);
      var b = button(open ? "less" : "more", function () {
        if (moreOpen.has(row)) moreOpen.delete(row); else moreOpen.add(row);
        changed(true);
      }, "fc-small fc-more-btn");
      b.setAttribute("aria-expanded", open ? "true" : "false");
      return b;
    }

    /** A catalog param's field; a clock is typed as the MHz the tools aim for. */
    function paramField(t, row, k) {
      var p = t.params[k];
      if (k === "clock_ps") {
        var auto = autoClock(state), dflt = auto || num(p.default) || 1000;
        return field("Clock (MHz)", function () {
          var v = String(row.params.clock_ps || ""), ps = num(v);
          return ps ? String(Math.round(1e6 / ps)) : v;
        }, function (v) { var ps = clockPs(v); row.params.clock_ps = ps ? String(ps) : v; },
        { compact: true, narrow: true, placeholder: String(Math.round(1e6 / dflt)),
          hint: "The clock the tools aim for" + (auto ? " (empty: from the objective)" : "") + ". fmax is measured; this steers synthesis: tighter = faster and bigger" });
      }
      return field(p.label + (p.unit ? " (" + p.unit + ")" : ""), function () { return row.params[k]; }, function (v) { row.params[k] = v; },
                   { compact: true, placeholder: shown(p.default), grow: typeof p.default !== "number" });
    }

    // -- checks
    function renderChecks() {
      var lang = language(state, true);
      var rows = state.checks.map(function (c, i) {
        var ty = checkType(c.type) || CHECK_TYPES[CHECK_TYPES.length - 1], t = toolOf(c.tool), ids = toolsFor(c.type, lang);
        var keys = Object.keys((t && t.params) || {});
        var line = [h("span", { class: "fc-idx", text: String(i + 1) }),
          field("Type", function () { return c.type; }, function (v) {
            // a name that was only the old type follows the new one
            if (new RegExp("^" + c.type + "\\d*$").test(c.name)) {
              c.name = uniqueName(v, state.checks.filter(function (x) { return x !== c; }).map(function (x) { return x.name; }));
            }
            setCheckTool(state, c, v);
          }, { compact: true, structural: true, options: CHECK_TYPES.map(function (x) { return [x.key, x.title]; }) }),
          field("Name", function () { return c.name; }, function (v) { c.name = v; }, { compact: true, narrow: true })];
        if (!t) line.push(h("span", { class: "fc-warn fc-grow", text: "No " + ty.title.toLowerCase() + " tool for " + (lang || "this language") + "; choose Custom." }));
        else {
          if (ids.length > 1) {
            line.push(field("Tool", function () { return c.tool; }, function (v) { setCheckTool(state, c, c.type, v); },
                            { compact: true, structural: true, options: ids.map(function (id) { return [id, toolOf(id).title]; }) }));
          }
          if (keys.length) line.push(paramField(t, c, keys[0]));
        }
        line.push(rowButtons(state.checks, i, t ? [moreButton(c)] : []));
        var kids = [h("div", { class: "fc-line" }, line)];
        if (t && moreOpen.has(c)) {
          var more = [h("small", { class: "fc-wide", text: t.what + " Passes " + String(t.pass || "").replace(/^passes /, "") +
                                     ((ty.note || {})[c.tool] ? " " + ty.note[c.tool] : "") })];
          keys.slice(1).forEach(function (k) { more.push(paramField(t, c, k)); });
          if (isCustom(c.tool)) {
            more.push(field("Count pattern (optional)", function () { return c.count_re; }, function (v) { c.count_re = v; },
                            { compact: true, placeholder: "(\\d+) failing", hint: "When it prints failures another way" }));
          }
          more.push(field("Time limit, s", function () { return c.timeout; }, function (v) { c.timeout = v; }, { compact: true, narrow: true, placeholder: "120" }));
          kids.push(h("div", { class: "fc-line fc-more" }, more));
        }
        return h("div", { class: "fc-row" }, kids);
      });
      return titled("Checks", [h("span", { class: "fc-hint fc-inline", text: " each must pass, in order" })], [h("div", { class: "fc-rows" }, rows),
        button("+ Add a check", function () {
          var used = state.checks.map(function (c) { return c.type; });
          var type = ["lint", "golden", "test", "custom"].filter(function (x) {
            return used.indexOf(x) < 0 && toolsFor(x, lang).length; })[0] || "custom";
          state.checks.push(newCheck(state, type));
          changed(true);
        }, "fc-add-btn")]);
    }

    // -- measurements
    function renderStages() {
      var tools = stageTools();
      var rows = state.stages.map(function (st, i) {
        var t = toolOf(st.tool), custom = isCustom(st.tool) || !t, keys = Object.keys((t && t.params) || {});
        var estimated = st.estimate && st.estimate.kind && st.estimate.kind !== "off";
        var line = [h("span", { class: "fc-idx", text: (estimated ? "~" : "") + String(i + 1), title: estimated ? "estimated first" : null }),
          field("Tool", function () { return st.tool; }, function (v) {
            var fresh = newStage({ stages: [] }, v); st.tool = v; st.params = fresh.params; st.gates = [];
          }, { compact: true, structural: true, grow: true, options: tools }),
          field("Name", function () { return st.name; }, function (v) { st.name = v; }, { compact: true, narrow: true })];
        if (keys.length) line.push(paramField(t, st, keys[0]));
        if (custom) {
          line.push(field("Numbers it prints", function () { return st.metrics; }, function (v) { st.metrics = v; },
                          { compact: true, placeholder: "time_ms, score", hint: "Printed as name=value" }));
        }
        var rep = reports(st);
        line.push(rowButtons(state.stages, i, [button("+ gate", function () {
          st.gates.push({ metric: rep[0] || "", rule: "at", value: "" }); changed(true);
        }, "fc-small"), moreButton(st)]));
        var kids = [h("div", { class: "fc-line" }, line)];
        st.gates.forEach(function (g, j) {
          kids.push(h("div", { class: "fc-line fc-gate" }, [h("span", { class: "fc-gate-word", text: j ? "and" : "go on only if" }),
            field("Number", function () { return g.metric; }, function (v) { g.metric = v; },
                  { compact: true, options: (rep.indexOf(g.metric) < 0 && g.metric ? [g.metric] : []).concat(rep) }),
            field("Rule", function () { return g.rule; }, function (v) { g.rule = v; },
                  { compact: true, structural: true, options: [["at", "at least"], ["below", "at most"], ["within", "within % of the best"]] }),
            field(g.rule === "within" ? "Percent" : "Value", function () { return g.value; }, function (v) { g.value = v; }, { compact: true, narrow: true }),
            button("×", function () { st.gates.splice(j, 1); changed(true); }, "fc-small fc-icon")]));
        });
        if (moreOpen.has(st)) {
          var more = [];
          if (t) more.push(h("small", { class: "fc-wide", text: t.what + (custom ? "" : " Reports " + Object.keys(t.metrics || {}).map(function (m) {
            return m + (t.metrics[m] ? " (" + t.metrics[m] + ")" : ""); }).join(", ") + ".") }));
          keys.slice(1).forEach(function (k) { more.push(paramField(t, st, k)); });
          if (custom) more.push(field("Tools it needs", function () { return st.needs; }, function (v) { st.needs = v; }, { compact: true }));
          var est = st.estimate = st.estimate || { kind: "off", margin: "5", command: "" };
          more.push(field("Estimate first", function () { return est.kind; }, function (v) { est.kind = v; },
                          { compact: true, structural: true, hint: "Predict this measurement before running it; a design estimated to fail a gate or a limit by more than the margin is skipped here",
                            options: [["off", "off"], ["surrogate", "fitted from past runs"], ["command", "my model script"], ["model", "the AI model"]] }));
          more.push(h("small", { class: "fc-wide fc-now", text: "Estimate: " + explainEstimate(est) }));
          if (est.kind !== "off") {
            more.push(field("Margin %", function () { return est.margin; }, function (v) { est.margin = v; }, { compact: true, narrow: true }));
            if (est.kind === "command") {
              more.push(field("Estimate command", function () { return est.command; }, function (v) { est.command = v; },
                              { compact: true, grow: true, placeholder: "{python} {home}/estimate.py {artifact}", hint: "Prints the same name=value numbers" }));
            }
          }
          kids.push(h("div", { class: "fc-line fc-more" }, more));
        }
        return h("div", { class: "fc-row" }, kids);
      });
      var add = button("+ Add a measurement", function () {
        // the first tool made for this language that is not used yet, else a custom command
        var lang = language(state, true), used = state.stages.map(function (x) { return x.tool; });
        state.stages.push(newStage(state, nextStageTool(lang, used)));
        changed(true);
      }, "fc-add-btn");
      return titled("Measurements", [h("span", { class: "fc-hint fc-inline", text: " cheapest first" })], [h("div", { class: "fc-rows" }, rows), add]);
    }

    // -- the objective
    function renderObjective() {
      var rep = reported(state);
      var rows = state.objectives.map(function (o, i) {
        var line = [h("span", { class: "fc-idx", text: String(i + 1) }), h("code", { class: "fc-obj-metric", text: o.metric }),
          field("How", function () { return o.label; }, function (v) { o.label = v; }, { compact: true, structural: true, options: LABELS })];
        if (o.label === "atleast" || o.label === "atmost") {
          var u = unitFor(o.metric);
          line.push(field("Value" + (u ? " (" + u + ")" : ""), function () { return o.value; }, function (v) { o.value = v; }, { compact: true, narrow: true }));
        }
        line.push(rowButtons(state.objectives, i));
        return h("div", { class: "fc-row" }, [h("div", { class: "fc-line" }, line)]);
      });
      var used = state.objectives.map(function (o) { return o.metric; });
      var sel = h("select", { "aria-label": "Add to the objective", class: "fc-add" });
      sel.appendChild(h("option", { value: "", text: rep.length ? "+ Add a number..." : "(add a measurement first)" }));
      rep.forEach(function (m) {
        if (used.indexOf(m) < 0) sel.appendChild(h("option", { value: m, text: m + (unitFor(m) ? " (" + unitFor(m) + ")" : "") }));
      });
      sel.addEventListener("change", function () {
        if (sel.value) { state.objectives.push(newObjective(sel.value, naturalDirection(sel.value) === "maximize" ? "max" : "min")); changed(true); }
      });
      parts.goalWords = h("p", { class: "fc-goal-words" });
      renderGoalWords();
      return titled("Objective", [h("span", { class: "fc-hint fc-inline", text: " limits must hold; the rest decide, top first; balance = best trade-off" })],
                     [h("div", { class: "fc-rows" }, rows), sel, parts.goalWords]);
    }

    function renderGoalWords() {
      if (!parts.goalWords) return;
      var words = describeObjectives(resolve(state).objectives);
      parts.goalWords.textContent = words.length ? "In words: " + words[0] + "." : "";
    }

    // -- level 1
    function renderLevel1() {
      var implied = impliedLanguage(state);                // D832: optional -- the tools usually tell
      var langs = [["", implied ? "from the tools: " + implied : "from the tools (none tells yet)"]].concat(LANGUAGES.map(function (l) { return [l, l]; })).concat([["other", "other..."]]);
      var what = titled("1. What do you want?", [], [
        h("div", { class: "fc-line" }, [
          field(opts.nameLabel || "Name", function () { return state.id; }, function (v) { state.id = v; },
                { compact: true, placeholder: opts.namePlaceholder || "my_design", hint: opts.nameHint || "Letters, digits and _" }),
          field("Language", function () { return state.language; }, function (v) { state.language = v; },
                { compact: true, options: langs, structural: true, hint: "Optional: the language the designs are written in, when the checks' tools do not tell it" }),
          state.language === "other" ? field("Which language?", function () { return state.languageOther; }, function (v) { state.languageOther = v; }, { compact: true, placeholder: "ini" }) : null,
          ]),
        field("What should be made? Say it as you would to an engineer.", function () { return state.statement; },
              function (v) { state.statement = v; }, { area: true, rows: 3, wide: true }),
        h("div", { class: "fc-line" }, [
          field("Rules every design must follow (optional)", function () { return state.contract; },
                function (v) { state.contract = v; }, { area: true, rows: 1, grow: true, placeholder: "Names, ports, what is not allowed" }),
          field("Files the model reads (optional)", function () { return state.knowledgeFiles; },
                function (v) { state.knowledgeFiles = v; }, { compact: true, grow: true, placeholder: "spec.md, notes.txt", hint: "Beside the document, separated by commas" })]),
        // D828: who digests library/'s papers is a box of the drawing ("Digest the papers"), not a field here
      ]);
      var kids = [what];
      if (!CATALOG.length) kids.push(h("p", { class: "fc-hint", text: "The tool list did not load; only Custom checks and measurements are offered." }));
      return h("div", { class: "fc-level" }, kids.concat([renderChecks(), renderStages(), renderObjective()]));
    }

    // -- level 2: the drawing
    /* The loop as it runs, top to bottom. Grey boxes are fixed (set elsewhere, or always on);
       red dotted arrows are the ways a design is refused: a check fails (repair), the part critic
       objects (sent back), a measurement asks for better (improve), an estimate or a gate fails
       (dropped). The critic, when on, sits at its three points. */
    var W = 150, H = 44, SW = 132, SH = 30, L = 70, R = 248, C = 159, S = 452;
    var ROWS = [16, 86, 156, 226, 290, 350, 420, 484, 548];

    function nodes() {
      function at(id, box, x, r, small) {
        return { id: id, box: box, x: x, y: ROWS[r] + (small ? (H - SH) / 2 : 0), w: small ? SW : W, h: small ? SH : H, small: !!small };
      }
      var out = [at("validate", "validate", C, 0), at("crit-division", "critique", S + (W - SW) / 2, 0, true),
        at("plan", "plan", L, 1), at("orchestrate", "orchestrate", R, 1), at("feedback", "feedback", S, 1),
        at("dse", "dse", L, 2), at("generate", "generate", R, 2), at("knowledge", "knowledge", S, 2),
        at("test", "test", C, 3), at("crit-part", "critique", C + (W - SW) / 2, 4, true),
        at("measure", "measure", C, 5), at("calibrate", "calibrate", C, 6),
        at("select", "select", C, 7), at("crit-decision", "critique", S + (W - SW) / 2, 7, true),
        at("records", "records", C, 8), at("lessons", "lessons", S, 8), at("digest", "digest", S, 3)];
      if (hasParts()) out.push(at("parts", "parts", L - 44, 4, true));
      return out;
    }

    function hasParts() { return state.partsMode === "decompose" || (state.partsMode === "list" && list(state.parts).length > 0); }

    var LAYOUT = { width: 640, height: 606 };

    function edges() {
      var b = {};
      nodes().forEach(function (n) { b[n.id] = n; });
      function cx(n) { return b[n].x + b[n].w / 2; }
      function top(n) { return b[n].y; }
      function bot(n) { return b[n].y + b[n].h; }
      function cy(n) { return b[n].y + b[n].h / 2; }
      function left(n) { return b[n].x; }
      function right(n) { return b[n].x + b[n].w; }
      function elbow(a, z) { var m = (bot(a) + top(z)) / 2; return "M" + cx(a) + " " + bot(a) + " V" + m + " H" + cx(z) + " V" + top(z); }
      function down(a, z) { return "M" + cx(a) + " " + bot(a) + " V" + top(z); }
      var bus = 428, genIn = cy("generate") + 8;           // the red bus back into "Make a design"
      var out = [
        { d: elbow("validate", "plan") }, { d: elbow("validate", "orchestrate") },
        { d: "M" + right("plan") + " " + cy("plan") + " H" + left("orchestrate") },
        { d: "M" + cx("crit-division") + " " + bot("crit-division") + " V" + (top("orchestrate") - 12) + " H" + (right("orchestrate") - 20) + " V" + top("orchestrate"), side: true },
        { d: "M" + left("feedback") + " " + cy("feedback") + " H" + right("orchestrate"), side: true },
        { d: down("orchestrate", "generate") },
        { d: "M" + right("dse") + " " + cy("dse") + " H" + left("generate") },
        { d: "M" + left("knowledge") + " " + (cy("knowledge") - 8) + " H" + right("generate"), side: true },
        { d: "M" + cx("digest") + " " + top("digest") + " V" + bot("knowledge"), side: true },          // D784: the papers summed up
        { d: elbow("generate", "test") },
        { d: down("test", "crit-part") },
        { d: down("crit-part", "measure") },
        { d: down("measure", "calibrate") },
        { d: down("calibrate", "select") },
        { d: "M" + right("select") + " " + cy("select") + " H" + left("crit-decision") },
        { d: down("select", "records") },
        { d: "M" + right("records") + " " + cy("records") + " H" + left("lessons"), side: true },
        { d: "M" + right("lessons") + " " + cy("lessons") + " H" + (LAYOUT.width - 12) + " V" + (cy("knowledge") + 6) + " H" + right("knowledge"), side: true },
        { d: "M" + left("records") + " " + cy("records") + " H22 V" + cy("plan") + " H" + left("plan"), back: true,
          label: { x: 14, y: (cy("plan") + cy("records")) / 2, text: "at rest → explore", rotate: true } },
        // the refusals, red and dotted
        { d: "M" + right("test") + " " + cy("test") + " H" + bus, red: true, label: { x: right("test") + 6, y: cy("test") - 4, text: "repair" } },
        { d: "M" + right("crit-part") + " " + cy("crit-part") + " H" + bus, red: true, label: { x: right("crit-part") + 6, y: cy("crit-part") - 4, text: "sent back" } },
        { d: "M" + right("measure") + " " + cy("measure") + " H" + bus, red: true, label: { x: right("measure") + 6, y: cy("measure") - 4, text: "improve" } },
        { d: "M" + bus + " " + cy("measure") + " V" + genIn + " H" + right("generate"), red: true, arrow: true },
        { d: "M" + left("measure") + " " + (cy("measure") + 6) + " H" + (left("measure") - 44), red: true, drop: { x: left("measure") - 50, y: cy("measure") + 6 },
          label: { x: left("measure") - 60, y: cy("measure") - 4, text: "dropped", anchor: "end" } },
      ];
      if (b.parts) out.push({ d: "M" + cx("parts") + " " + bot("parts") + " V" + (top("measure") - 8) + " H" + (left("measure") + 20) + " V" + top("measure") });
      return out;
    }

    function renderDiagram() {
      var svg = parts.svg;
      if (!svg) return;
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      var defs = s("defs", {});
      [["fc-arrow", "fc-arrowhead"], ["fc-arrow-red", "fc-arrowhead-red"]].forEach(function (m) {
        var marker = s("marker", { id: m[0], viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" });
        marker.appendChild(s("path", { d: "M0 0 L10 5 L0 10 z", class: m[1] }));
        defs.appendChild(marker);
      });
      svg.appendChild(defs);
      edges().forEach(function (e) {
        var cls = "fc-edge" + (e.side ? " fc-side" : "") + (e.back ? " fc-back" : "") + (e.red ? " fc-red" : "");
        var attrs = { d: e.d, class: cls };
        if (!e.red || e.arrow) attrs["marker-end"] = e.red ? "url(#fc-arrow-red)" : "url(#fc-arrow)";
        svg.appendChild(s("path", attrs));
        if (e.drop) svg.appendChild(s("text", { x: e.drop.x, y: e.drop.y + 4, class: "fc-drop", "text-anchor": "middle" }, "×"));
        if (e.label) {
          var la = { x: e.label.x, y: e.label.y, class: "fc-edge-label" + (e.red ? " fc-red-label" : ""), "text-anchor": e.label.anchor || (e.label.rotate ? "middle" : "start") };
          if (e.label.rotate) la.transform = "rotate(-90 " + e.label.x + " " + e.label.y + ")";
          svg.appendChild(s("text", la, e.label.text));
        }
      });
      nodes().forEach(function (n) {
        var fixed = n.box === "parts" || isFixed(n.box), half = fixed ? "fixed" : halfOf(state, n.box);
        var box = BOXES[n.box] || { title: "Parts", fixed: "Set under Advanced > Parts." };
        var title = n.id === "crit-division" ? "Critic: division" : n.id === "crit-part" ? "Critic: each part"
                  : n.id === "crit-decision" ? "Critic: decision" : n.id === "parts" ? "parts: sub-loops, composed" : box.title;
        var live = !fixed && !readonly;
        var act = readonly && parts.activity ? parts.activity[n.id] : null;      // D726
        var picks = readonly && !!opts.onBox;
        var attrs = { class: "fc-box fc-" + half + (fixed ? " fc-static" : "") + (live ? "" : " fc-inert") + (openNode === n.id ? " fc-open" : "") + (n.small ? " fc-smallbox" : "")
                      + (parts.activity && readonly && opts.onBox ? (act ? " fc-act fc-act-" + act.state : " fc-act-idle") : "") + (picks && act ? " fc-pick" : "") + (act && act.sel ? " fc-sel" : ""),
                      "data-box": n.box, "data-node": n.id };
        if (live) {
          attrs.tabindex = "0"; attrs.role = "button"; attrs["aria-haspopup"] = "dialog";
          attrs["aria-expanded"] = openNode === n.id ? "true" : "false";
          attrs["aria-label"] = title + ": " + (choiceOf(n.box, state.flow[n.box]) || {}).label;
        } else {
          attrs.tabindex = "-1";
        }
        var g = s("g", attrs);
        var full = stepNames(n.box);
        g.appendChild(s("title", {}, act && act.title ? title + ": " + act.title : readonly ? title + " (" + n.box + "): " + (box.says || box.fixed)
                                   : title + (full && full.length ? ": " + full.join(" → ") : "") + (fixed ? " — " + (box.fixed || "fixed") : "")));
        if (n.id === "parts") {                       // a stack: two shadows behind
          [6, 3].forEach(function (d) { g.appendChild(s("rect", { x: n.x + d, y: n.y - d, width: n.w, height: n.h, rx: 6, class: "fc-stack" })); });
        }
        g.appendChild(s("rect", { x: n.x, y: n.y, width: n.w, height: n.h, rx: n.small ? 6 : 7 }));
        if (n.small) {
          g.appendChild(s("text", { x: n.x + n.w / 2, y: n.y + 19, "text-anchor": "middle", class: "fc-box-small" + (n.id === "parts" ? " fc-tiny" : "") }, title));
        } else {
          g.appendChild(s("text", { x: n.x + n.w / 2, y: n.y + 19, "text-anchor": "middle", class: "fc-box-name" }, title));
          g.appendChild(s("text", { x: n.x + n.w / 2, y: n.y + 35, "text-anchor": "middle", class: "fc-box-half" }, act && act.label ? act.label : subtitle(n.box, half)));
        }
        if (fixed && !n.small) lock(g, n.x + n.w - 11, n.y + 5);
        if (picks && act) {                            // D726: a box with activity opens its latest task
          g.setAttribute("tabindex", "0"); g.setAttribute("role", "button");
          g.addEventListener("click", function () { opts.onBox(n.id); });
          g.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); opts.onBox(n.id); } });
        }
        if (live) {
          g.addEventListener("click", function () { openPopover(openNode === n.id ? null : n.box, false, n.id); });
          g.addEventListener("keydown", function (ev) {
            if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openPopover(openNode === n.id ? null : n.box, true, n.id); }
          });
        }
        svg.appendChild(g);
      });
      placePopover();
      if (parts.lists) {
        var c = stepNames("test"), m = stepNames("measure"), cut = [];
        if (c.length && abbreviate(c, 24) !== c.join(" → ")) cut.push("Checks: " + c.join(" → "));
        if (m.length && abbreviate(m, 24) !== m.join(" → ")) cut.push("Measurements: " + m.join(" → "));
        parts.lists.textContent = cut.join(" · ");
      }
    }

    /** A small padlock: this box is not a setting. */
    function lock(g, x, y) {
      g.appendChild(s("path", { d: "M" + (x + 1.5) + " " + (y + 4) + " v-1.5 a2.5 2.5 0 0 1 5 0 v1.5", class: "fc-lock-shackle" }));
      g.appendChild(s("rect", { x: x, y: y + 4, width: 8, height: 6, rx: 1, class: "fc-lock" }));
    }

    /** The checks' or the measurements' names ("~" before one estimated first). */
    function stepNames(name) {
      if (name === "test") return state.checks.map(function (x) { return String(x.name || "?").trim() || "?"; });
      if (name === "measure") return state.stages.map(function (x) {
        return (x.estimate && x.estimate.kind && x.estimate.kind !== "off" ? "~" : "") + (String(x.name || "?").trim() || "?");
      });
      return null;
    }

    function subtitle(name, half, max) {
      var text = half === "off" && name === "orchestrate" && state.flow.dse !== "none" ? "the search" : HALVES[half];
      if (name === "orchestrate" && state.flow.orchestrate === "default" && !(state.flow.dse && state.flow.dse !== "none")) {
        text = hasParts() ? "model picks the part" : "default: one design";
      }
      if (readonly && (name === "test" || name === "measure")) return name === "test" ? "your checks" : "your measurements";
      var names = stepNames(name);
      max = max || 24;
      if (names) return names.length ? abbreviate(names, max) : "none yet";
      return text.length > max ? text.slice(0, max - 1) + "…" : text;
    }

    // -- the popover: a chosen box's choices, anchored to it (a bottom sheet on a narrow screen)
    function boxEl() { return parts.svg && openNode && parts.svg.querySelector('[data-node="' + openNode + '"]'); }

    /** Open `name`'s popover (null closes it); `focus`: move the keyboard into it. */
    function openPopover(name, focus, node) {
      var was = openNode;
      openBox = name;
      openNode = name ? node || name : null;
      renderDiagram();
      fillPopover();
      placePopover();
      if (name && focus) {
        var first = parts.pop.querySelector("input:checked") || parts.pop.querySelector("input, button");
        if (first) first.focus();
      }
      if (!name && was && focus !== false) { var g = parts.svg.querySelector('[data-node="' + was + '"]'); if (g) g.focus(); }
    }

    function fillPopover() {
      var p = parts.pop;
      p.innerHTML = "";
      p.hidden = !openBox;
      if (!openBox) return;
      var box = BOXES[openBox];
      p.setAttribute("aria-label", box.title);
      p.appendChild(h("div", { class: "fc-pop-head" }, [h("strong", { text: box.title }), h("code", { text: openBox }),
        button("\u00d7", function () { openPopover(null, true); }, "fc-small fc-icon fc-pop-close")]));
      p.appendChild(h("p", { text: box.says }));
      var now = explain(openBox, state);
      if (now) p.appendChild(h("p", { class: "fc-hint fc-now", text: "As set: " + now + "." }));
      var raw = state.agentRaw && state.agentRaw[openBox];
      if (raw) p.appendChild(h("p", { class: "fc-hint fc-now", text: "A coding agent with its own settings, kept as written: " + JSON.stringify(raw.agent) +
        ". Choosing another replaces them." }));
      if (openBox === "orchestrate" && state.flow.dse !== "none") {
        p.appendChild(h("p", { class: "fc-hint", text: "A search is on, so the search picks the next job." }));
      } else if (box.choices.length === 1) {
        p.appendChild(h("p", { class: "fc-hint", text: "This step is fixed: " + box.choices[0].label + "." }));
      } else {
        var group = h("div", { class: "fc-choices", role: "radiogroup", "aria-label": box.title });
        box.choices.forEach(function (c) {
          var id = "fc-" + openBox + "-" + c.value.replace(":", "-");
          var input = h("input", { type: "radio", name: "fc-choice", id: id, value: c.value });
          input.checked = state.flow[openBox] === c.value;
          input.addEventListener("change", function () {
            var name = openBox;
            state.flow[name] = c.value;
            if (state.agentRaw) delete state.agentRaw[name];          // another choice: its own settings go
            changed(name === "dse" || name === "generate");
            fillPopover(); placePopover();
            var again = document.getElementById(id); if (again) again.focus();
          });
          group.appendChild(h("label", { for: id, class: "fc-choice fc-" + c.half }, [input, " " + c.label]));
        });
        p.appendChild(group);
        if (openBox === "generate" && state.flow.generate === "command") {
          p.appendChild(field("Command that writes each design", function () { return state.generateCommand; },
                              function (v) { state.generateCommand = v; },
                              { wide: true, placeholder: "{python} {home}/gen.py {artifact} {knob}", hint: "Each setting to search is {its name}" }));
        }
      }
    }

    /** Beside the box (right, else left, else below), inside the viewport; a sheet when narrow. */
    function placePopover() {
      var p = parts.pop;
      if (!p || !openBox) return;
      var g = boxEl();
      p.classList.remove("fc-sheet", "fc-right", "fc-left", "fc-below");
      if (!g || window.innerWidth < 700) { p.classList.add("fc-sheet"); p.style.left = p.style.top = ""; return; }
      var r = g.getBoundingClientRect(), w = p.offsetWidth, hgt = p.offsetHeight, gap = 12, vw = window.innerWidth, vh = window.innerHeight;
      var left, top, side;
      if (r.right + gap + w <= vw - 8) { side = "fc-right"; left = r.right + gap; }
      else if (r.left - gap - w >= 8) { side = "fc-left"; left = r.left - gap - w; }
      else { side = "fc-below"; left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), vw - w - 8); }
      if (side === "fc-below") top = Math.min(r.bottom + gap, vh - hgt - 8);
      else top = Math.min(Math.max(8, r.top + r.height / 2 - hgt / 2), Math.max(8, vh - hgt - 8));
      p.classList.add(side);
      p.style.left = Math.round(left) + "px";
      p.style.top = Math.round(top) + "px";
      // the arrow points at the box's middle
      p.style.setProperty("--fc-arrow-y", Math.round(Math.min(Math.max(14, r.top + r.height / 2 - top), hgt - 14)) + "px");
      p.style.setProperty("--fc-arrow-x", Math.round(Math.min(Math.max(14, r.left + r.width / 2 - left), w - 14)) + "px");
    }

    /** The legend and the drawing. */
    function drawing() {
      parts.svg = document.createElementNS(SVGNS, "svg");
      parts.svg.setAttribute("viewBox", "0 0 " + LAYOUT.width + " " + LAYOUT.height);
      parts.svg.setAttribute("class", "fc-diagram");
      parts.svg.setAttribute("role", "group");
      parts.svg.setAttribute("aria-label", "The loop: each box is one step");
      var legend = h("div", { class: "fc-legend" }, ["rules", "model", "agent", "fixed", "off"].map(function (k) {
        return h("span", { class: "fc-key fc-" + k }, [h("i"), HALVES[k]]);
      }));
      parts.lists = h("p", { class: "fc-hint fc-lists" });
      return [legend, h("div", { class: "fc-drawing" }, [parts.svg, parts.lists])];
    }

    function renderLevel2() {
      if (!opts.foldSteps) {
        return h("div", { class: "fc-level" }, [titled("2. Who does each step?", [h("span", { class: "fc-hint fc-inline", text: " click a box to change it; the defaults are usually right" })], drawing())]);
      }
      if (parts.stepsOpen === undefined) parts.stepsOpen = false;
      var body = h("div", {}, drawing());
      body.hidden = !parts.stepsOpen;
      var toggle = h("button", { type: "button", class: "fc-toggle", "aria-expanded": parts.stepsOpen ? "true" : "false", on: { click: function () {
        parts.stepsOpen = !parts.stepsOpen;
        body.hidden = !parts.stepsOpen;
        toggle.setAttribute("aria-expanded", parts.stepsOpen ? "true" : "false");
        if (parts.stepsOpen) renderDiagram();
      } } }, ["2. Who does each step?", h("span", { class: "fc-hint fc-inline", text: " the defaults are usually right: open to choose a model, an agent or rules per step" })]);
      return h("section", { class: "fc-section fc-advanced" }, [h("h3", {}, [toggle]), body]);
    }

    // -- level 3: the same fields and rows as above, behind one toggle
    function sub(title, note, kids) {
      return h("div", { class: "fc-sub" }, [h("h4", {}, [title, note ? h("span", { class: "fc-hint fc-inline", text: " " + note }) : null])].concat(kids));
    }

    function renderLevel3() {
      var b = state.budget;
      function num(label, key, dflt, hint) {
        return field(label, function () { return b[key]; }, function (v) { b[key] = v; },
                     { compact: true, placeholder: dflt, hint: hint + " (budget." + key + "; empty: " + dflt + ")" });
      }
      var budget = h("div", { class: "fc-grid" }, [
        num("Designs per round", "steps", "24", "Work items in one round"),
        num("Rounds", "passes", "until stopped", "How many rounds before the run stops"),
        num("Rounds at once", "parallel", "1", "Rounds run side by side, each its own design; on a server an admin allows it"),
        num("Search designs a round", "batch", "1", "With a search: designs one round tries side by side; 1 picks each round's design from the last ones' numbers"),
        num("Repairs per design", "repair_attempts", "12", "Repairs a draft gets after a check fails"),
        num("Designs fully measured", "finalists", "3", "How many designs reach the costliest measurement"),
        num("Tool runs at once", "workers", "auto", "Measurements in parallel; 1 for anything timed"),
        field("Prototype first", function () { return b.prototype; }, function (v) { b.prototype = v; },
              { compact: true, hint: "The model proves the algorithm before the design is written: yes for maths, no for plain logic (budget.prototype; empty: on with a golden model)",
                options: [["", "default"], ["true", "yes"], ["false", "no"], ["python", "yes, in Python"], ["systemc", "yes, in SystemC"]] })]);

      var knobs = state.space.map(function (r, i) {
        return h("div", { class: "fc-row" }, [h("div", { class: "fc-line" }, [
          h("span", { class: "fc-idx", text: String(i + 1) }),
          field("Setting", function () { return r.knob; }, function (v) { r.knob = v; },
                { compact: true, placeholder: "block", hint: "Its name; {name} in a command is the value tried" }),
          field("Its choices, in order", function () { return r.choices; }, function (v) { r.choices = v; },
                { compact: true, grow: true, placeholder: "16, 32, 64", hint: "Separated by commas" }),
          h("div", { class: "fc-row-buttons" }, [button("\u00d7", function () { state.space.splice(i, 1); changed(true); }, "fc-small fc-icon")])])]);
      });
      var searching = state.flow.dse && state.flow.dse !== "none";
      var space = sub("Settings to search", searching ? "each is {its name} in the commands" : "used once \"Search the settings\" is on, in the drawing (who does each step)", [
        h("div", { class: "fc-rows" }, knobs),
        button("+ Add a setting", function () { state.space.push({ knob: "", choices: "" }); changed(true); }, "fc-add-btn")]);

      var split = sub("Parts", "one design made as several, each checked on its own, then composed", [h("div", { class: "fc-line" }, [
        field("Split the design", function () { return state.partsMode; }, function (v) { state.partsMode = v; },
              { compact: true, structural: true, options: [["none", "no"], ["list", "into these parts"], ["decompose", "let it decide"]] }),
        state.partsMode === "list" ? field("Parts", function () { return state.parts; }, function (v) { state.parts = v; },
                                           { compact: true, grow: true, placeholder: "decoder, datapath", hint: "Part names, separated by commas" }) : null])]);

      // closed until asked for, or until something in it is in use
      var inUse = !!(searching || state.space.length || hasParts());
      if (inUse && !parts.advancedUsed) parts.advancedOpen = true;
      parts.advancedUsed = inUse;
      var body = h("div", { class: "fc-advanced-body" }, [sub("Budget", "empty = the loop's default, shown greyed", [budget]), space, split]);
      body.hidden = !parts.advancedOpen;
      var toggle = h("button", { type: "button", class: "fc-toggle", "aria-expanded": parts.advancedOpen ? "true" : "false", on: { click: function () {
        parts.advancedOpen = !parts.advancedOpen;
        body.hidden = !parts.advancedOpen;
        toggle.setAttribute("aria-expanded", parts.advancedOpen ? "true" : "false");
      } } }, ["3. Advanced", h("span", { class: "fc-hint fc-inline", text: " budget, settings to search, parts" })]);
      return h("section", { class: "fc-section fc-advanced" }, [h("h3", {}, [toggle]), body]);
    }

    // D826: steps instead of one long form -- a step bar, one step at a time, Back and Next; the
    // document, its checklist and the save on the last step (and Save on every step when editing)
    var STEPS = ["The problem", "Checks", "Measurements", "Objectives", "Who does each step", "More", "Review and save"];
    // D828: what each step is for, in a line -- in place of the long form's numbered titles
    var STEP_SAYS = [
      "What the loop designs, in your words, and what every design must respect.",
      "Each check refuses a wrong design. They run in order; the first that fails sends the design back to be repaired.",
      "Each measurement sizes or times a design that passed the checks, cheapest first; a costly one runs only on the best of the cheaper.",
      "What makes one design better: limits it must meet, then what to push, most important first.",
      "Who works each step: built-in rules, a model, or a coding agent. Click a box to change it; the defaults are usually right.",
      "The budget, settings to search over, and splitting one design into parts. An empty field keeps the default, shown greyed.",
      ""];
    function renderForm() {
      parts.form.innerHTML = "";
      if (!stepped) {
        parts.form.appendChild(renderLevel1());
        parts.form.appendChild(renderLevel2());
        parts.form.appendChild(renderLevel3());
        return;
      }
      var step = parts.step || 0;
      var bar = h("ol", { class: "fc-stepbar", role: "tablist" }, STEPS.map(function (t, i) {
        return h("li", { class: (i === step ? "fc-on" : "") + (i < step ? " fc-done" : "") }, [h("button", { type: "button", role: "tab",
          "aria-selected": i === step ? "true" : "false", on: { click: function () { go(i); } } }, [h("span", { class: "fc-num", text: String(i + 1) }), t])]);
      }));
      var body;
      if (step <= 3) {
        var kids = Array.prototype.slice.call(renderLevel1().childNodes), n = kids.length;
        body = step === 0 ? kids.slice(0, n - 3) : [kids[n - 4 + step]];
      } else if (step === 4) {
        var was = opts.foldSteps; opts.foldSteps = false; body = [renderLevel2()]; opts.foldSteps = was;
      } else if (step === 5) {
        parts.advancedOpen = true; body = [renderLevel3()];
      } else {
        body = [h("p", { class: "fc-hint", text: opts.save ? "The document as the steps say it, what is left to do, and the save." :
          "The document as the steps say it, and what is left to do. Copy or download it." })];
      }
      var back = step > 0 ? button("Back", function () { go(step - 1); }) : null;
      var next = step < STEPS.length - 1 ? button("Next: " + STEPS[step + 1], function () { go(step + 1); }, "fc-primary") : null;
      var nav = h("div", { class: "fc-stepnav" }, [back, h("span", { class: "fc-grow" }), opts.save && step < STEPS.length - 1 ? button(opts.saveLabel || "Save", function () { parts.saveBtn.click(); }) : null, next]);
      body.forEach(function (el) {                       // the long form's titles: the step bar says them
        if (!el.querySelectorAll) return;
        var t = el.querySelector("h3");
        if (t && step !== 6) t.parentNode.removeChild(t);
        Array.prototype.forEach.call(el.querySelectorAll(".fc-advanced-body"), function (b) { b.hidden = false; });
      });
      if (STEP_SAYS[step]) body.unshift(h("p", { class: "fc-step-says", text: STEP_SAYS[step] }));
      var last = step === STEPS.length - 1;
      if (parts.out) {                                   // the document is the last step's own, inside it
        parts.out.hidden = !last;
        if (last) body.push(parts.out);
        else if (parts.bodyEl && parts.out.parentNode !== parts.bodyEl) parts.bodyEl.appendChild(parts.out);
      }
      parts.form.appendChild(bar);
      parts.form.appendChild(h("div", { class: "fc-step" + (last ? " fc-step-last" : "") }, body));
      parts.form.appendChild(nav);
      if (step === 4) setTimeout(renderDiagram, 0);
    }
    function go(i) {
      parts.step = Math.max(0, Math.min(STEPS.length - 1, i));
      renderForm();
      renderOutput();
      if (parts.form.scrollIntoView && parts.form.getBoundingClientRect && parts.form.getBoundingClientRect().top < 0) parts.form.scrollIntoView();
    }

    function renderOutput() {
      renderGoalWords();
      var yaml = buildYaml(state), id = String(state.id || "").trim() || "my_problem";
      var file = id + "/problem.yaml";
      parts.code.textContent = yaml;
      parts.file.textContent = file;
      var msgs = check(state);
      parts.checks.innerHTML = "";
      if (!msgs.some(function (m) { return m.level !== "note"; })) parts.checks.appendChild(h("li", { class: "fc-ok", text: "Looks complete." }));
      var calm = opts.calmChecks && !parts.touched;   // nothing typed yet: what is left to do, not errors
      msgs.forEach(function (m) { parts.checks.appendChild(h("li", { class: "fc-" + (calm && m.level !== "note" ? "todo" : m.level), text: m.text })); });
      if (parts.next) parts.next.textContent = "flux task check " + file + "\nflux task run " + file + " --passes 1";
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
    if (readonly) {                                    // the loop at its defaults, nothing to click
      drawing().forEach(function (el) { host.appendChild(el); });
      // D726: a running loop's activity over the drawing -- per node id, {state: running|done|
      // failed, label, title}; `opts.onBox(nodeId)` when a box is clicked
      parts.activity = opts.activity || {};
      renderDiagram();
      return { setActivity: function (m) { parts.activity = m || {}; renderDiagram(); } };
    }
    parts.form = h("div", { class: "fc-form" });
    parts.code = h("code", {});
    parts.file = h("span", { class: "fc-file" });
    parts.checks = h("ul", { class: "fc-checks" });
    parts.next = opts.nextSteps === false ? null : h("code", {});
    parts.copyBtn = button("Copy", copy, opts.save ? "" : "fc-primary");
    parts.saved = h("span", { class: "fc-hint" });
    var saveBtn = opts.save ? button(opts.saveLabel || "Save", function () {
      parts.saved.textContent = "Saving...";
      Promise.resolve(opts.save(buildYaml(state), state)).then(function (said) { parts.saved.textContent = said || "Saved."; },
        function (e) { parts.saved.textContent = (e && e.message) || String(e); });
    }, "fc-primary") : null;
    var keptNotes = (opts.notes || []).length ? [h("h4", { text: "Kept as written" }),
      h("ul", { class: "fc-checks" }, opts.notes.map(function (n) { return h("li", { class: "fc-note", text: n }); }))] : [];
    parts.saveBtn = saveBtn;
    var out = parts.out = h("div", { class: "fc-output" }, [
      h("div", { class: "fc-output-head" }, [parts.file, saveBtn, parts.copyBtn, button("Download", download), parts.saved]),
      h("pre", { class: "fc-yaml" }, [parts.code]),
      h("h4", { text: "Checklist" }), parts.checks].concat(keptNotes).concat(opts.nextSteps === false ? [] : [
      h("h4", { text: "Next steps" }),
      h("p", { class: "fc-hint", text: "Save the file with the files it names, then:" }),
      h("pre", {}, [parts.next])]));
    parts.bodyEl = h("div", { class: "fc-body" + (stepped ? " fc-stepped" : "") }, [parts.form, out]);
    host.appendChild(parts.bodyEl);
    if (stepped) drawing();                            // the drawing's parts exist before its step is shown
    parts.pop = h("div", { class: "fc-pop", role: "dialog", hidden: "hidden" });
    host.appendChild(parts.pop);
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && openBox) { ev.preventDefault(); openPopover(null, true); }
    });
    document.addEventListener("mousedown", function (ev) {       // a click outside closes it; on a box, the box decides
      if (!openBox || parts.pop.contains(ev.target)) return;
      var box = ev.target.closest && ev.target.closest(".fc-box:not(.fc-static)");
      if (!box) openPopover(null, false);
    });
    window.addEventListener("resize", placePopover);
    window.addEventListener("scroll", placePopover, true);
    renderForm();
    renderDiagram();
    renderOutput();
  }

  var SCRIPT_SRC = document.currentScript && document.currentScript.src;

  /** The drawing alone where a page asks for it; the tool catalog beside this script
      (tools.json), then the builder. */
  function start() {
    var still = document.getElementById("flux-loop-drawing");
    if (still && !still.dataset.mounted) { still.dataset.mounted = "1"; mount(still, true); }
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
  root.FluxCrafter = Object.assign({ mount: mount }, api);      // the web configurator mounts it itself (D686)
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
  // Material's instant navigation swaps pages without a reload
  if (root.document$ && root.document$.subscribe) root.document$.subscribe(start);
})(typeof window !== "undefined" ? window : this);
