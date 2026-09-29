/* The Flux problem builder (website/docs/guide/loop-crafter.md).

   A form that writes a `problem.yaml`. The document side is pure and runs under node too:
   `buildYaml(state) -> string` and `check(state) -> [{level, text}]`, with the presets and the
   vocabulary they draw from. The words mirror flux_loop/document.py (DOCUMENT_KEYS, FLOW_BOXES,
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
     `setCatalog`, or passes it to buildYaml/check/resolve/preset as their last argument. */
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
  /** objective.py UNITS: the units Flux knows; any other unit is written as `unit:`. */
  var UNITS = { fmax_mhz: "MHz", area_um2: "um2", area_mm2: "mm2", power_w: "W", power_mw: "mW", time_ms: "ms",
                latency_cycles: "cycles", energy_pj: "pJ", cell_count: "cells" };
  var MORE_UNITS = { storage_bytes: "B", path_ps: "ps" };

  /** The numbers a goal sentence can use, by preference: the first a design's stages all report. */
  var SPEED = [{ metric: "fmax_mhz", direction: "maximize", word: "speed", keepAbove: 0 },
               { metric: "geomean_speedup", direction: "maximize", word: "speed-up", keepAbove: 1 },
               { metric: "time_ms", direction: "minimize", word: "time" },
               { metric: "latency_cycles", direction: "minimize", word: "latency" }];
  var SIZE = [{ metric: "area_um2", direction: "minimize", word: "area" }, { metric: "area_mm2", direction: "minimize", word: "area" },
              { metric: "storage_bytes", direction: "minimize", word: "storage" }, { metric: "cell_count", direction: "minimize", word: "cells" }];
  var POWER = [{ metric: "power_w", direction: "minimize", word: "power" }, { metric: "power_mw", direction: "minimize", word: "power" },
               { metric: "energy_pj", direction: "minimize", word: "energy" }];

  // ------------------------------------------------------------------ rows: checks and stages
  function homeRelative(def) { return typeof def === "string" && def.indexOf("{home}/") === 0; }

  /** A param as the form shows it: a file beside the document without `{home}/`. */
  function shown(def) { return homeRelative(def) ? def.slice(7) : def === undefined || def === null ? "" : String(def); }

  function defaultName(id, taken) {
    var base = { "rtl-lint": "lint", "rtl-golden": "golden", "champsim-build": "build", "champsim-check": "smoke",
                 "python-test-script": "test", "custom-check": "check", "bench-script": "bench", "zigzag-model": "model",
                 "custom-stage": "measure" }[id] || String(id || "step").replace(/^rtl-|^champsim-/, "");
    var name = base, i = 2;
    while (taken.indexOf(name) >= 0) name = base + i++;
    return name;
  }

  /** A new check row for a catalog tool. */
  function checkRow(id, name, params, cat) {
    var t = toolOf(id, cat) || { params: {} }, p = {};
    for (var k in t.params || {}) p[k] = shown(t.params[k].default);
    for (var k2 in params || {}) p[k2] = String(params[k2]);
    return { tool: id, name: name, params: p, count_re: "", timeout: "" };
  }

  /** A new stage row for a catalog tool. */
  function stageRow(id, name, params, cat) {
    var t = toolOf(id, cat) || { params: {} }, p = {};
    for (var k in t.params || {}) p[k] = shown(t.params[k].default);
    for (var k2 in params || {}) p[k2] = String(params[k2]);
    return { tool: id, name: name, params: p, metrics: "", needs: "", cutoff: { metric: "", rule: "none", value: "" } };
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

  /** The numbers a stage row reports. */
  function reports(row, cat) {
    var t = toolOf(row.tool, cat);
    if (!t || isCustom(row.tool)) return list(row.metrics);
    return Object.keys(t.metrics || {});
  }

  function unitFor(metric, cat) {
    if (UNITS[metric]) return UNITS[metric];
    if (MORE_UNITS[metric]) return MORE_UNITS[metric];
    var cs = cat || CATALOG;
    for (var i = 0; i < cs.length; i++) if (cs[i].metrics && cs[i].metrics[metric]) return cs[i].metrics[metric];
    return "";
  }

  // ------------------------------------------------------------------ the kits
  /* A kit is a kind of problem Flux has tools for. It only PRE-FILLS the two lists (checks,
     measurements) and the language; everything stays editable. The rows follow the shipped
     documents: rtl = templates/rtl + a lint first; program/python = templates/python;
     champsim = applications/prefetcher/invent.problem.yaml; zigzag = applications/npu_gemm. */
  var KITS = {
    rtl: { title: "Hardware (RTL) on ASAP7", blurb: "Lint, a golden model, then synthesis and placement.", language: "systemverilog",
      checks: [["rtl-lint"], ["rtl-golden"]], stages: [["rtl-synth", "screen"], ["rtl-place", "confirm"]] },
    program: { title: "Program speed", blurb: "A test script checks it, a bench script times it.", language: "text", workers: "1",
      checks: [["python-test-script", "test", null, "60"]], stages: [["bench-script", "bench"]] },
    python: { title: "Python function speed", blurb: "A model writes the function; check.py proves it, bench.py times it.", language: "python",
      checks: [["python-test-script", "test", null, "60"]], stages: [["bench-script", "bench"]] },
    champsim: { title: "CPU cache simulation (ChampSim)", blurb: "A prefetcher built into ChampSim and run on your traces.", language: "cpp",
      checks: [["champsim-build"], ["champsim-check"]],
      stages: [["champsim-run", "screen"], ["champsim-run", "confirm", { warmup: 100000000, sim: 150000000 }]] },
    zigzag: { title: "Accelerator model (ZigZag)", blurb: "An architecture file, costed by ZigZag on your workload.", language: "yaml",
      checks: [["python-test-script", "check"]], stages: [["zigzag-model", "model"]] },
    own: { title: "My own tool", blurb: "Start from one command to check and one to measure.", language: "",
      checks: [["custom-check"]], stages: [["custom-stage"]] },
  };
  var KIT_ORDER = ["rtl", "program", "python", "champsim", "zigzag", "own"];

  /** Switch the kind of problem: its language and its two lists, pre-filled. */
  function setKit(state, kit, cat) {
    var k = KITS[kit];
    state.kit = kit;
    if (k.language) {
      if (LANGUAGES.indexOf(k.language) >= 0) state.language = k.language;
      else { state.language = "other"; state.languageOther = k.language; }
    }
    var taken = [];
    state.checks = k.checks.map(function (c) {
      var name = c[1] || defaultName(c[0], taken); taken.push(name);
      var row = checkRow(c[0], name, c[2], cat);
      if (c[3]) row.timeout = c[3];
      return row;
    });
    taken = [];
    state.stages = k.stages.map(function (s) {
      var name = s[1] || defaultName(s[0], taken); taken.push(name);
      return stageRow(s[0], name, s[2], cat);
    });
    if (k.workers && !String(state.budget.workers || "").trim()) state.budget.workers = k.workers;
    var avail = goalsFor(state, cat);
    if (!avail.some(function (g) { return g.key === state.goal.sentence; })) state.goal = { sentence: avail[0].key, number: "" };
    return state;
  }

  // ------------------------------------------------------------------ the goal sentences
  /** The speed, size and power numbers every stage reports (a goal is judged on each stage). */
  function numbersOf(state, cat) {
    var stages = state.stages || [];
    if (!stages.length) return null;
    var common = reports(stages[0], cat).filter(function (m) {
      return stages.every(function (st) { return reports(st, cat).indexOf(m) >= 0; });
    });
    function pick(prefs) { for (var i = 0; i < prefs.length; i++) if (common.indexOf(prefs[i].metric) >= 0) return prefs[i]; return null; }
    var speed = pick(SPEED);
    return speed ? { speed: speed, size: pick(SIZE), power: pick(POWER), common: common } : null;
  }

  var GOALS = [
    { key: "fastest", needs: ["speed"], label: function (n) { return n.speed.direction === "maximize" ? "As fast as possible" : "As quick as possible (least " + n.speed.word + ")"; } },
    { key: "smallest", needs: ["size"], label: function () { return "As small as possible"; } },
    { key: "power", needs: ["power"], label: function (n) { return "Lowest " + n.power.word; } },
    { key: "reach", needs: ["speed", "size"], number: "target",
      label: function (n, cat) { return (n.speed.direction === "maximize" ? "Reach " : "Within ") + "[N] " + (unitFor(n.speed.metric, cat) || n.speed.word) + ", then the smallest"; } },
    { key: "keep", needs: ["speed", "size", "keep"], number: "percent",
      label: function () { return "Fastest, then the smallest within [90]% of it"; } },
    { key: "knee", needs: ["speed", "size"], label: function (n) { return "Best balance of " + n.speed.word + " and " + n.size.word + " (the knee)"; } },
    { key: "custom", needs: [], label: function () { return "My own goals"; } },
  ];

  /** The goal sentences the chosen measurements can say; "My own goals" always. */
  function goalsFor(state, cat) {
    var n = numbersOf(state, cat);
    return GOALS.filter(function (g) {
      if (g.key === "custom") return true;
      if (!n) return false;
      return g.needs.every(function (w) { return w === "keep" ? n.speed.keepAbove !== undefined : !!n[w]; });
    }).map(function (g) { return { key: g.key, number: g.number || "", label: g.label(n, cat) }; });
  }

  function num(v) { var s = String(v === undefined || v === null ? "" : v).trim(); var x = Number(s); return s !== "" && isFinite(x) ? x : null; }
  function clockPs(mhz) { var m = num(mhz); return m && m > 0 ? Math.round(1e6 / m) : null; }

  /** The speed the deepest RTL stage is clocked at, in MHz: the default number to reach. */
  function clockedMhz(state) {
    var st = (state.stages || []).filter(function (s) { return /^rtl-/.test(s.tool) && num(s.params.clock_ps); });
    return st.length ? Math.round(1e6 / num(st[st.length - 1].params.clock_ps)) : null;
  }

  function goalNumber(state, cat) {
    var n = num((state.goal || {}).number);
    if (n !== null) return n;
    var nb = numbersOf(state, cat);
    return nb && nb.speed.metric === "fmax_mhz" ? clockedMhz(state) : null;
  }

  function objOf(m, extra, cat) {
    var o = { metric: m.metric, direction: m.direction };
    for (var k in extra || {}) if (extra[k] !== null && extra[k] !== undefined) o[k] = extra[k];
    if (!UNITS[m.metric] && unitFor(m.metric, cat)) o.unit = unitFor(m.metric, cat);
    return o;
  }

  /** The objectives this state says: from its goal sentence, or its own rows. */
  function objectivesOf(state, cat) {
    var g = state.goal || {}, avail = goalsFor(state, cat);
    var sentence = avail.some(function (x) { return x.key === g.sentence; }) ? g.sentence : avail[0].key;
    if (sentence === "custom") {
      return (state.objectives || []).filter(function (o) { return String(o.metric || "").trim(); }).map(function (o) {
        var r = { metric: o.metric.trim(), direction: o.direction === "minimize" ? "minimize" : "maximize" };
        if (o.target === "goal" && String(o.goal).trim() !== "") r.goal = typed(o.goal);
        if (o.target === "keep" && String(o.keep).trim() !== "") {
          r.keep = typed(o.keep);
          if (String(o.above).trim() !== "") r.above = typed(o.above);
        }
        if (String(o.unit || "").trim()) r.unit = o.unit.trim();
        return r;
      });
    }
    var n = numbersOf(state, cat);
    if (sentence === "smallest") return [objOf(n.size, null, cat)];
    if (sentence === "power") return [objOf(n.power, null, cat)];
    if (sentence === "knee") return [objOf(n.speed, null, cat), objOf(n.size, null, cat)];
    if (sentence === "reach") return [objOf(n.speed, { goal: goalNumber(state, cat) }, cat), objOf(n.size, null, cat)];
    if (sentence === "keep") {
      var pct = num(String(g.number || "").trim() === "" ? "90" : g.number);
      return [objOf(n.speed, { keep: pct === null ? null : Math.round(pct * 1000) / 100000, above: n.speed.keepAbove || null }, cat),
              objOf(n.size, null, cat)];
    }
    return [objOf(n.speed, null, cat)];
  }

  /** The objectives in plain words, one line each. */
  function describeObjectives(objs, cat) {
    return objs.map(function (o, i) {
      var head = i === 0 ? "" : "then ", u = unitFor(o.metric, cat) || o.unit || "";
      if (o.keep !== undefined && o.keep !== null) {
        return head + o.metric + " at least " + Math.round(o.keep * 100) + "% of the best measured" + (o.above ? " (of its gain over " + o.above + ")" : "");
      }
      if (o.goal !== undefined && o.goal !== null && o.goal !== "") {
        return head + o.metric + " " + (o.direction === "maximize" ? "at least " : "at most ") + o.goal + (u ? " " + u : "");
      }
      return head + o.metric + " as " + (o.direction === "maximize" ? "high" : "low") + " as possible";
    });
  }

  // ------------------------------------------------------------------ the state and presets
  function base() {
    return {
      preset: "", id: "", statement: "", contract: "", language: "systemverilog", languageOther: "",
      knowledgeFiles: "", kit: "own", checks: [], stages: [], goal: { sentence: "custom", number: "" }, objectives: [],
      flow: defaultFlow(), generateCommand: "",
      budget: { steps: "", passes: "", repair_attempts: "", finalists: "", workers: "", prototype: "" },
      space: [], partsMode: "none", parts: "",
    };
  }

  function objective(metric, direction, extra) {
    var o = { metric: metric, direction: direction, target: "none", goal: "", keep: "", above: "", unit: "" };
    for (var k in extra || {}) o[k] = extra[k];
    return o;
  }

  var PRESETS = [
    { key: "rtl", title: "An RTL module checked against a Python golden model",
      blurb: "A model writes the hardware; lint and golden.py check it.",
      files: "golden.py", make: function (cat) {
        var s = base();
        s.id = "adder8";
        s.statement = "An unsigned 8-bit adder with carry out in SystemVerilog: module `adder8`, inputs `a` and " +
          "`b` (8 bits), output `s` (9 bits), the smallest that makes 2000 MHz placed on ASAP7.";
        s.contract = "One module named exactly `adder8`, purely combinational, ports `input logic [7:0] a, b` " +
          "and `output logic [8:0] s`. s must equal a + b for every input.";
        setKit(s, "rtl", cat);
        s.stages.forEach(function (st) { st.params.clock_ps = "500"; });
        s.goal = { sentence: "reach", number: "2000" };
        s.budget.steps = "3"; s.budget.repair_attempts = "6"; s.budget.prototype = "false"; s.budget.finalists = "2";
        return s;
      } },
    { key: "rtl-sweep", title: "Sweep the variants a generator script writes",
      blurb: "No model: your script writes one design per combination of settings.",
      files: "gen.py, golden.py", make: function (cat) {
        var s = base();
        s.id = "popcount16";
        s.statement = "A 16-bit population count (module `popcount16`, input `a`, 5-bit output `y`): the smallest " +
          "that makes 3000 MHz on the synthesis screen, among the architectures gen.py spells.";
        setKit(s, "rtl", cat);
        s.checks = [checkRow("rtl-golden", "golden", null, cat)];
        s.stages = [stageRow("rtl-synth", "screen", { clock_ps: 333 }, cat)];
        s.goal = { sentence: "reach", number: "3000" };
        s.space = [{ knob: "arch", choices: "behavioral, tree, lut" }, { knob: "chunk", choices: "2, 4" }];
        s.flow.dse = "sweep"; s.flow.generate = "command";
        s.generateCommand = "{python} {home}/gen.py {artifact} {arch} {chunk}";
        s.budget.steps = "1";
        return s;
      } },
    { key: "tune", title: "Tune a program's settings",
      blurb: "Try settings of an existing program and keep the fastest.",
      files: "check.py, bench.py, workload.py", make: function (cat) {
        var s = base();
        s.id = "matmul_tune";
        s.statement = "The fastest block size and loop order for the blocked matrix multiply in workload.py.";
        setKit(s, "program", cat);
        s.checks = [checkRow("custom-check", "test", { command: "{python} {home}/check.py {block} {order}" }, cat)];
        s.checks[0].timeout = "60";
        s.stages = [stageRow("custom-stage", "bench", { command: "{python} {home}/bench.py {block} {order}" }, cat)];
        s.stages[0].metrics = "time_ms";
        s.goal = { sentence: "fastest", number: "" };
        s.space = [{ knob: "block", choices: "16, 32, 64, 128, 256" }, { knob: "order", choices: "ijk, ikj, jki" }];
        s.flow.dse = "sweep";
        s.budget.steps = "1";
        return s;
      } },
    { key: "python", title: "A Python function, fastest wins",
      blurb: "A model writes the function; check.py proves it, bench.py times it.",
      files: "check.py, bench.py", make: function (cat) {
        var s = base();
        s.id = "count_primes";
        s.statement = "A Python function `count_primes(n: int) -> int` that returns how many primes are below n, " +
          "as fast as possible for n up to 2,000,000.";
        s.contract = "One module that defines `count_primes(n)`. Standard library only; no table of precomputed " +
          "answers. It returns the exact count for every n >= 0.";
        setKit(s, "python", cat);
        s.goal = { sentence: "fastest", number: "" };
        s.budget.steps = "3"; s.budget.repair_attempts = "4";
        return s;
      } },
    { key: "config", title: "Let a model write a config file",
      blurb: "A model writes a settings file; your simulator scores it.",
      files: "bingo.py, knobs.md, bingo_default.ini", make: function (cat) {
        var s = base();
        s.id = "prefetcher";
        s.statement = "The L2 prefetcher configuration for 5G baseband workloads (FFT-heavy signal processing, " +
          "channel-estimation matrix work, bursty packet buffers): Bingo, the partners that run beside it and " +
          "their knobs. The fastest configuration by geomean IPC speedup over no prefetcher, then the smallest " +
          "that holds 90% of that gain.";
        s.contract = "One ChampSim knob file, `name = value` per line, starting with `l2c_prefetcher_types = bingo` " +
          "plus any partners. Only the knobs and values knobs.md allows; a knob left out takes its shipped value.";
        s.knowledgeFiles = "knobs.md, bingo_default.ini";
        setKit(s, "champsim", cat);
        s.language = "other"; s.languageOther = "ini";
        s.checks = [checkRow("custom-check", "check", { command: "{python} {home}/bingo.py check {artifact}" }, cat)];
        s.stages = [["screen", "10000000", "15000000"], ["confirm", "100000000", "150000000"]].map(function (r) {
          var st = stageRow("custom-stage", r[0], { command: "{python} {home}/bingo.py measure {artifact} --traces {home}/traces --warmup " + r[1] + " --sim " + r[2] }, cat);
          st.metrics = "geomean_speedup, storage_bytes"; st.needs = "pythia";
          return st;
        });
        s.goal = { sentence: "keep", number: "90" };
        s.budget.repair_attempts = "3"; s.budget.finalists = "4";
        return s;
      } },
    { key: "zigzag", title: "Size an accelerator with ZigZag",
      blurb: "No model: a script writes each architecture; ZigZag costs it.",
      files: "render.py, check.py, measure.py, workload.yaml", make: function (cat) {
        var s = base();
        s.id = "npu_gemm";
        s.statement = "An accelerator for the feed-forward block in workload.yaml: a 1-D PE array and a global " +
          "buffer. The fewest cycles first (at most 500), then the least area.";
        setKit(s, "zigzag", cat);
        s.goal = { sentence: "reach", number: "500" };
        s.space = [{ knob: "pe_x", choices: "4, 8, 16, 32, 64" }, { knob: "gbuf_kb", choices: "16, 64, 256" }];
        s.flow.dse = "sweep"; s.flow.generate = "command";
        s.generateCommand = "{python} {home}/render.py {artifact} {pe_x} {gbuf_kb}";
        s.budget.steps = "2";
        return s;
      } },
  ];

  function preset(key, cat) {
    for (var i = 0; i < PRESETS.length; i++) {
      if (PRESETS[i].key === key) { var s = PRESETS[i].make(cat); s.preset = key; return s; }
    }
    throw new Error("no preset " + key);
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

  function language(state) {
    return state.language === "other" ? String(state.languageOther || "").trim() || "text" : state.language;
  }

  function knobNames(state) {
    return (state.space || []).filter(function (r) { return String(r.knob || "").trim() && list(r.choices).length; })
      .map(function (r) { return r.knob.trim(); });
  }

  /** What the document says for the checks, the measurements and the goals:
      `{checks: [{name, run, count_re, timeout}], stages: [{name, command, reports, metrics
      (written), needs (written), cutoff}], objectives}`. */
  function resolve(state, cat) {
    cat = cat || CATALOG;
    var objectives = objectivesOf(state, cat);
    var checks = (state.checks || []).map(function (c, i) {
      return { name: String(c.name || "").trim() || "check" + (i + 1), run: fillRun(c, cat), tool: c.tool,
               count_re: isCustom(c.tool) || !toolOf(c.tool, cat) ? String(c.count_re || "").trim() : "",
               timeout: String(c.timeout || "").trim() };
    });
    var stages = (state.stages || []).map(function (st, i) {
      var t = toolOf(st.tool, cat), custom = !t || isCustom(st.tool), cmd = fillRun(st, cat);
      var rep = reports(st, cat), cut = st.cutoff || {}, cutoff = null;
      if (cut.rule && cut.rule !== "none" && String(cut.metric || "").trim()) {
        var v = num(cut.value);
        cutoff = { metric: cut.metric.trim() };
        if (cut.rule === "within") cutoff.within = v === null ? null : Math.round(v * 1000) / 100000;
        else cutoff[cut.rule] = v;
      }
      var rtlMeasure = /^flux rtl measure\s/.test(cmd);
      var used = objectives.map(function (o) { return o.metric; }).concat(cutoff ? [cutoff.metric] : []);
      var write = custom || !rtlMeasure || used.some(function (m) { return LOADER_RTL.indexOf(m) < 0 && rep.indexOf(m) >= 0; });
      var needs = custom ? list(st.needs) : /^flux rtl\s/.test(cmd) ? [] : (t.needs || []).slice();
      return { name: String(st.name || "").trim() || "stage" + (i + 1), command: cmd, tool: st.tool, reports: rep,
               metrics: write ? rep : [], needs: needs, cutoff: cutoff };
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
    out += "language: " + q(language(state)) + "\n";

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
    if (checks.length === 1 && !checks[0].count_re && !checks[0].timeout) {
      out += "\ngate: " + q(checks[0].run) + "\n";               // one check: the short form
    } else if (checks.length) {
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
        if (st.cutoff) {
          var cp = [["metric", st.cutoff.metric]];
          ["at", "below", "within"].forEach(function (k) { if (k in st.cutoff) cp.push([k, st.cutoff[k] === null ? "?" : st.cutoff[k]]); });
          out += "    cutoff: " + flowMap(cp) + "\n";
        }
      });
    }

    if (r.objectives.length) {
      out += "\nobjectives:\n";
      r.objectives.forEach(function (o) {
        var p = [["metric", o.metric], ["direction", o.direction]];
        ["goal", "keep", "above", "unit"].forEach(function (key) {
          if (o[key] !== undefined && o[key] !== null && o[key] !== "") p.push([key, o[key]]);
        });
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
    var id = String(state.id || "").trim(), lang = language(state);
    var r = resolve(state, cat), flow = state.flow || {}, knobs = knobNames(state);
    if (!id) error("Give the problem a name (letters, digits and _).");
    else if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(id)) error("The name \"" + id + "\" should be a letter, then letters, digits or _.");
    if (!String(state.statement || "").trim()) error("Say what you want made (the statement is empty).");

    function rowProblems(row, what, i) {
      var t = toolOf(row.tool, cat), nm = String(row.name || "").trim() || what + " " + (i + 1);
      if (!t && !isCustom(row.tool)) { error(what + " \"" + nm + "\": the tool " + row.tool + " is not in the catalog."); return; }
      if (t && (t.languages || []).length && t.languages.indexOf(lang) < 0) {
        warn(what + " \"" + nm + "\" (" + t.title + ") is for " + t.languages.join(", ") + "; the design is " + lang + ".");
      }
      for (var k in (t && t.params) || {}) {
        var v = String((row.params || {})[k] === undefined ? "" : row.params[k]).trim(), def = t.params[k].default;
        if (!v && (def === "" || def === undefined || def === null)) error(what + " \"" + nm + "\" needs its " + t.params[k].label.toLowerCase() + ".");
        else if (v && typeof def === "number" && !(num(v) > 0)) error(what + " \"" + nm + "\": " + t.params[k].label.toLowerCase() + " should be a number above 0.");
      }
    }

    // the checks
    if (!r.checks.length) error("Add at least one check: a design that fails it goes no further.");
    var seen = {};
    (state.checks || []).forEach(function (c, i) {
      var nm = r.checks[i].name;
      if (seen[nm]) error("Two checks are named \"" + nm + "\"; names must differ.");
      seen[nm] = 1;
      if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(nm)) error("The check name \"" + nm + "\" should be a letter, then letters, digits, _ or -.");
      rowProblems(c, "Check", i);
      if (c.timeout && !(num(c.timeout) > 0)) error("Check \"" + nm + "\": the time limit should be a number of seconds.");
      if (r.checks[i].count_re) {
        var ok = true;
        try { new RegExp(r.checks[i].count_re); ok = /\((?!\?)/.test(r.checks[i].count_re); } catch (e) { ok = false; }
        if (!ok) error("Check \"" + nm + "\": the count pattern needs one group around the number, like (\\d+) failing.");
      }
    });

    // the measurements
    if (!r.stages.length) warn("Nothing is measured: add a measurement so designs can be compared.");
    seen = {};
    (state.stages || []).forEach(function (st, i) {
      var rs = r.stages[i], nm = rs.name;
      if (seen[nm]) error("Two measurements are named \"" + nm + "\"; names must differ.");
      seen[nm] = 1;
      rowProblems(st, "Measurement", i);
      if (isCustom(st.tool) && !rs.reports.length) error("Measurement \"" + nm + "\" needs the names of the numbers it prints (name=value).");
      var cut = st.cutoff || {};
      if (cut.rule && cut.rule !== "none") {
        if (!String(cut.metric || "").trim()) error("Measurement \"" + nm + "\": say which number its gate looks at.");
        else if (rs.reports.indexOf(cut.metric.trim()) < 0) error("Measurement \"" + nm + "\" does not report " + cut.metric.trim() + ", so its gate cannot use it.");
        var v = num(cut.value);
        if (v === null) error("Measurement \"" + nm + "\": its gate needs a number.");
        else if (cut.rule === "within" && !(v > 0 && v <= 100)) error("Measurement \"" + nm + "\": \"within\" is a percentage between 1 and 100.");
        if (i === (state.stages || []).length - 1) warn("The last measurement's gate has nothing after it to hold back.");
      }
    });

    // the goals
    var g = state.goal || {}, avail = goalsFor(state, cat);
    var chosen = avail.filter(function (x) { return x.key === g.sentence; })[0];
    if (!chosen) warn("That goal does not fit the measurements chosen; \"" + avail[0].label + "\" is used.");
    else if (chosen.number === "target" && goalNumber(state, cat) === null) error("Type the number to reach.");
    else if (chosen.number === "percent") {
      var p = num(String(g.number || "").trim() === "" ? "90" : g.number);
      if (p === null || !(p > 0 && p <= 100)) error("The share to keep is a percentage between 1 and 100.");
    }
    if (!r.objectives.length) warn("No goal yet: say what should be made bigger or smaller.");
    r.objectives.forEach(function (o) {
      var by = r.stages.filter(function (st) { return st.reports.indexOf(o.metric) >= 0; }).length;
      if (!by) error("The goal \"" + o.metric + "\" is not reported by any measurement.");
      else if (by < r.stages.length) warn("Every measurement should report \"" + o.metric + "\"; some do not.");
      if ("goal" in o && typeof o.goal !== "number") error("The target for \"" + o.metric + "\" should be a number.");
      if ("keep" in o && !(typeof o.keep === "number" && o.keep > 0 && o.keep <= 1)) error("The share kept for \"" + o.metric + "\" should be between 0 and 1 (0.9 is 90%).");
    });

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

  var api = { buildYaml: buildYaml, check: check, resolve: resolve, preset: preset, setKit: setKit, goalsFor: goalsFor,
              describeObjectives: describeObjectives, setCatalog: setCatalog, toolOf: toolOf, checkRow: checkRow,
              stageRow: stageRow, fillRun: fillRun, reports: reports, defaultName: defaultName, clockPs: clockPs,
              PRESETS: PRESETS, KITS: KITS, KIT_ORDER: KIT_ORDER, GOALS: GOALS,
              BOXES: BOXES, FLOW_BOXES: FLOW_BOXES, DELEGABLE: DELEGABLE, NEVER: NEVER, LANGUAGES: LANGUAGES,
              AGENTS: AGENTS, DSE_POLICIES: DSE_POLICIES, halfOf: halfOf, defaultFlow: defaultFlow, base: base,
              objective: objective };

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
    var state = preset("rtl");
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

    // -- presets
    function renderPresets() {
      var row = h("div", { class: "fc-presets", role: "list" });
      PRESETS.forEach(function (p) {
        var card = h("button", { type: "button", role: "listitem", class: "fc-card" + (state.preset === p.key ? " fc-on" : ""),
                                 "aria-pressed": state.preset === p.key ? "true" : "false" },
                     [h("strong", { text: p.title }), h("span", { text: p.blurb }),
                      h("small", { text: "Files it uses: " + p.files })]);
        card.addEventListener("click", function () {
          state = preset(p.key); openBox = null;
          Array.prototype.forEach.call(row.children, function (c) { c.classList.remove("fc-on"); c.setAttribute("aria-pressed", "false"); });
          card.classList.add("fc-on"); card.setAttribute("aria-pressed", "true");
          changed(true);
        });
        row.appendChild(card);
      });
      return row;
    }

    // -- the kind of problem: it pre-fills the checks and the measurements
    function renderKitPicker() {
      var row = h("div", { class: "fc-presets fc-kits", role: "radiogroup", "aria-label": "Kind of problem" });
      KIT_ORDER.forEach(function (key) {
        var kit = KITS[key], on = state.kit === key;
        var card = h("button", { type: "button", role: "radio", "aria-checked": on ? "true" : "false", class: "fc-card" + (on ? " fc-on" : "") },
                     [h("strong", { text: kit.title }), h("span", { text: kit.blurb })]);
        card.addEventListener("click", function () { setKit(state, key); changed(true); });
        row.appendChild(card);
      });
      return row;
    }

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

    function addPicker(role, label, onAdd) {
      var sel = h("select", { "aria-label": label, class: "fc-add" });
      sel.appendChild(h("option", { value: "", text: label }));
      var mine = CATALOG.filter(function (t) { return t.role === role; });
      mine.sort(function (a, b) {
        var ka = (a.kinds || []).indexOf(state.kit) >= 0 ? 0 : 1, kb = (b.kinds || []).indexOf(state.kit) >= 0 ? 0 : 1;
        return ka - kb;
      });
      mine.forEach(function (t) { sel.appendChild(h("option", { value: t.id, text: t.title + " — " + t.what })); });
      sel.addEventListener("change", function () { if (sel.value) onAdd(sel.value); });
      return sel;
    }

    function renderChecks() {
      var rows = state.checks.map(function (c, i) {
        var t = toolOf(c.tool) || { title: "Any command", params: { command: { label: "Command", default: "" } }, pass: "" };
        var kids = [h("div", { class: "fc-row-head fc-wide" }, [h("strong", { text: (i + 1) + ". " + t.title }), rowButtons(state.checks, i)]),
                    t.pass ? h("small", { class: "fc-wide", text: "Passes " + String(t.pass).replace(/^passes /, "") }) : null,
                    field("Name", function () { return c.name; }, function (v) { c.name = v; }, { structural: true })];
        Object.keys(t.params || {}).forEach(function (k) { kids.push(paramField(t, c, k)); });
        if (isCustom(c.tool) || !toolOf(c.tool)) {
          kids.push(field("Count pattern (optional)", function () { return c.count_re; }, function (v) { c.count_re = v; },
                          { placeholder: "(\\d+) failing", hint: "When it prints failures another way" }));
        }
        kids.push(field("Time limit, s (optional)", function () { return c.timeout; }, function (v) { c.timeout = v; }));
        return h("div", { class: "fc-row" }, kids);
      });
      return section("Checks (each must pass, in order)", [h("div", { class: "fc-rows" }, rows),
        addPicker("check", "+ Add a check...", function (id) {
          state.checks.push(checkRow(id, defaultName(id, state.checks.map(function (c) { return c.name; }))));
          changed(true);
        })]);
    }

    function renderStages() {
      var rows = state.stages.map(function (st, i) {
        var t = toolOf(st.tool) || { title: "Any command", params: { command: { label: "Command", default: "" } }, metrics: {} };
        var custom = isCustom(st.tool) || !toolOf(st.tool);
        var kids = [h("div", { class: "fc-row-head fc-wide" }, [h("strong", { text: (i + 1) + ". " + t.title }), rowButtons(state.stages, i)]),
                    field("Name", function () { return st.name; }, function (v) { st.name = v; }, { structural: true })];
        Object.keys(t.params || {}).forEach(function (k) { kids.push(paramField(t, st, k)); });
        if (custom) {
          kids.push(field("Numbers it prints", function () { return st.metrics; }, function (v) { st.metrics = v; },
                          { placeholder: "time_ms, score", hint: "Printed as name=value" }));
          kids.push(field("Tools it needs (optional)", function () { return st.needs; }, function (v) { st.needs = v; }));
        } else {
          kids.push(h("small", { class: "fc-wide", text: "Reports " + Object.keys(t.metrics || {}).map(function (m) {
            return m + (t.metrics[m] ? " (" + t.metrics[m] + ")" : ""); }).join(", ") }));
        }
        var cut = st.cutoff;
        var gate = [h("span", { class: "fc-label", text: "Go on only if" }),
          field("Number", function () { return cut.rule === "none" ? "" : cut.metric; }, function (v) {
            if (v) { cut.metric = v; if (cut.rule === "none") cut.rule = "at"; } else cut.rule = "none"; },
            { structural: true, options: [["", "always (no gate)"]].concat(reports(st).map(function (m) { return [m, m]; })) })];
        if (cut.rule !== "none") {
          gate.push(field("Rule", function () { return cut.rule; }, function (v) { cut.rule = v; },
                          { structural: true, options: [["at", "at least"], ["below", "at most"], ["within", "within % of the best"]] }));
          gate.push(field(cut.rule === "within" ? "Percent" : "Value", function () { return cut.value; }, function (v) { cut.value = v; }));
        }
        kids.push(h("div", { class: "fc-cutoff fc-wide" }, gate));
        return h("div", { class: "fc-row" }, kids);
      });
      return section("Measurements (cheapest first)", [h("div", { class: "fc-rows" }, rows),
        addPicker("stage", "+ Add a measurement...", function (id) {
          state.stages.push(stageRow(id, defaultName(id, state.stages.map(function (s) { return s.name; }))));
          changed(true);
        })]);
    }

    function renderGoal() {
      var goals = goalsFor(state), g = state.goal;
      if (!goals.some(function (x) { return x.key === g.sentence; })) g.sentence = goals[0].key;
      var group = h("div", { class: "fc-choices fc-goals", role: "radiogroup", "aria-label": "Goal" });
      goals.forEach(function (x) {
        var id = "fc-goal-" + x.key;
        var input = h("input", { type: "radio", name: "fc-goal", id: id, value: x.key });
        input.checked = g.sentence === x.key;
        input.addEventListener("change", function () {
          g.sentence = x.key;
          g.number = x.number === "percent" ? "90" : "";
          changed(true);
        });
        var label = h("label", { for: id, class: "fc-choice fc-rules" }, [input]);
        if (x.key === g.sentence && x.number) {
          var bits = x.label.split(/\[(?:N|90)\]/);
          var dflt = x.number === "target" ? goalNumberHint() : "90";
          var box = h("input", { type: "text", class: "fc-num", "aria-label": x.number === "percent" ? "Percent" : "Number",
                                  inputmode: "decimal", placeholder: dflt || "N" });
          box.value = g.number || "";
          box.addEventListener("input", function () { g.number = box.value; changed(false); });
          label.appendChild(document.createTextNode(" " + bits[0]));
          label.appendChild(box);
          label.appendChild(document.createTextNode(bits[1] || ""));
        } else {
          label.appendChild(document.createTextNode(" " + x.label.replace(/\[(N|90)\]/, x.number === "percent" ? "90" : "N")));
        }
        group.appendChild(label);
      });
      var kids = [group];
      if (g.sentence === "custom") kids.push(renderOwnGoals());
      parts.goalWords = h("ol", { class: "fc-goal-words" });
      renderGoalWords();
      return kids.concat([h("p", { class: "fc-hint", text: "In the document:" }), parts.goalWords]);
    }

    function goalNumberHint() {
      var st = state.stages.filter(function (s) { return /^rtl-/.test(s.tool) && num(s.params.clock_ps); });
      return st.length ? String(Math.round(1e6 / num(st[st.length - 1].params.clock_ps))) : "";
    }

    function renderGoalWords() {
      if (!parts.goalWords) return;
      parts.goalWords.innerHTML = "";
      describeObjectives(resolve(state).objectives).forEach(function (t) { parts.goalWords.appendChild(h("li", { text: t })); });
    }

    function renderOwnGoals() {
      var known = {};
      state.stages.forEach(function (st) { reports(st).forEach(function (m) { known[m] = 1; }); });
      var dl = h("datalist", { id: "fc-metrics" }, Object.keys(known).map(function (m) { return h("option", { value: m }); }));
      return h("div", {}, [dl, h("p", { class: "fc-hint", text: "The first goal matters most; the next ones break ties." }),
        h("div", { class: "fc-rows" }, state.objectives.map(function (o, i) {
          var metric = field("Number", function () { return o.metric; }, function (v) { o.metric = v; });
          metric.querySelector("input").setAttribute("list", "fc-metrics");
          var kids = [metric,
            field("Better is", function () { return o.direction; }, function (v) { o.direction = v; }, { options: [["maximize", "bigger"], ["minimize", "smaller"]] }),
            field("Target", function () { return o.target; }, function (v) { o.target = v; },
                  { structural: true, options: [["none", "as good as possible"], ["goal", "reach a number"], ["keep", "stay near the best"]] })];
          if (o.target === "goal") kids.push(field("Number to reach", function () { return o.goal; }, function (v) { o.goal = v; }));
          if (o.target === "keep") {
            kids.push(field("Share of the best to keep", function () { return o.keep; }, function (v) { o.keep = v; }, { hint: "0.9 = 90%" }));
            kids.push(field("Measured from (optional)", function () { return o.above; }, function (v) { o.above = v; }, { hint: "1.0 for a speed-up" }));
          }
          kids.push(field("Unit (optional)", function () { return o.unit; }, function (v) { o.unit = v; }));
          kids.push(button("Remove", function () { state.objectives.splice(i, 1); changed(true); }, "fc-small"));
          return h("div", { class: "fc-row" }, kids);
        })),
        button("+ Add a goal", function () { state.objectives.push(objective("", "minimize")); changed(true); })]);
    }

    // -- level 1
    function renderLevel1() {
      var langs = LANGUAGES.map(function (l) { return l; }).concat([["other", "other..."]]);
      var what = section("1. What do you want?", [
        h("div", { class: "fc-grid" }, [
          field("Name", function () { return state.id; }, function (v) { state.id = v; }, { placeholder: "my_design", hint: "Letters, digits and _" }),
          field("Language of the design", function () { return state.language; }, function (v) { state.language = v; }, { options: langs, structural: true }),
          state.language === "other" ? field("Which language?", function () { return state.languageOther; }, function (v) { state.languageOther = v; }, { placeholder: "ini" }) : null,
        ]),
        field("What should be made? Say it as you would to an engineer.", function () { return state.statement; },
              function (v) { state.statement = v; }, { area: true, rows: 4, wide: true }),
        field("Rules every design must follow (optional)", function () { return state.contract; },
              function (v) { state.contract = v; }, { area: true, rows: 2, wide: true, placeholder: "Names, ports, what is not allowed" }),
        field("Files the model should read (optional)", function () { return state.knowledgeFiles; },
              function (v) { state.knowledgeFiles = v; }, { wide: true, placeholder: "spec.md, notes.txt", hint: "Beside the document, separated by commas" }),
      ]);
      var kind = section("What kind of problem?", [h("p", { class: "fc-hint", text: "Picking one fills the checks and measurements below; change them freely." }),
                                                    renderKitPicker()]);
      if (!CATALOG.length) kind.appendChild(h("p", { class: "fc-hint", text: "The tool list did not load; custom commands only." }));
      return h("div", { class: "fc-level" }, [what, kind, renderChecks(), renderStages(), section("What is the goal?", renderGoal())]);
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
      parts.presets.parentNode.replaceChild(renderPresets(), parts.presets);
      parts.presets = host.querySelector(".fc-presets");
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
    parts.presets = renderPresets();
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
    host.appendChild(h("h3", { class: "fc-start", text: "Start from" }));
    host.appendChild(parts.presets);
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
