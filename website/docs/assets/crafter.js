/* The Flux problem builder (website/docs/guide/loop-crafter.md).

   A form that writes a `problem.yaml`. The document side is pure and runs under node too:
   `buildYaml(state) -> string` and `check(state) -> [{level, text}]`, with the vocabulary they draw
   from. The words mirror flux_loop/document/ (DOCUMENT_KEYS, FLOW_BOXES,
   _FLOW_WORDS, EXTENSIONS), flux_loop/boxes.py (DELEGABLE, NEVER) and the registered DSE
   policies; flux/tests/unit/test_loop_crafter.py loads what this writes with the real loader.
   The page wiring (`mount`) is at the bottom and only runs in a browser; on a page with a
   `#flux-loop-drawing` (guide/loop-shape.md) it draws the loop alone, at its defaults. */
(function (root) {
  "use strict";

  // ------------------------------------------------------------------ the vocabulary
  /** flux_loop.document EXTENSIONS: the languages whose file extension Flux knows. */
  var LANGUAGES = ["systemverilog", "verilog", "vhdl", "chisel", "python", "c", "cpp", "cuda", "opencl",
                   "rust", "scala", "shell", "bash", "text", "yaml", "json", "markdown"];
  var AGENTS = ["opencode", "claude", "codex"];
  /** The registered DSE policies a document names by word (dse.py), and the model's half. */
  var DSE_POLICIES = ["sweep", "montecarlo", "anneal", "gradient", "genetic", "pareto"];
  var DSE_INTENTS = ["adaptive", "explore", "improve", "tune", "finetune", "variations"];
  // These groups filter the UI; the document still has one search choice.
  var DSE_GROUPS = [["preferences", "Exploration and tuning"], ["algorithms", "Search algorithms"], ["delegated", "Model or agent"]];
  function dseGroup(value) {
    if (value === "none" || DSE_INTENTS.indexOf(value) >= 0) return "preferences";
    return DSE_POLICIES.indexOf(value) >= 0 ? "algorithms" : "delegated";
  }
  function dseChoices(group, current) {
    var choices = BOXES.dse.choices.filter(function (c) { return dseGroup(c.value) === group; });
    if (current && dseGroup(current) === group && !choiceOf("dse", current)) {
      choices.push({ value: current, label: "Custom agent settings (kept as written)", half: "agent" });
    }
    return choices;
  }
  function dseHint(state) {
    var value = state.flow.dse, group = dseGroup(value), space = parameterSearch(state);
    if (value === "none") return "Adaptive guidance by default: choose local improvements or a new approach from the evidence. Select a policy to search settings defined in Extra.";
    if (group === "preferences") return space
      ? "A configured model proposes legal settings using this emphasis and the measured history. No model means no model-proposed points."
      : "Guides the orchestrator's next experiment and the design, repair and prototype prompts. Reasoned risks remain allowed; checks and objectives still apply.";
    if (group === "delegated") return (value === "model" ? "Requires a configured model connection. " : "Requires the selected agent to be installed and configured. ") + (space
      ? "Proposes legal points from the settings in Extra and the measured history; checks and measurements judge them."
      : "Chooses the next job and search direction. The Make a design setting separately chooses who writes the design.");
    var how = { sweep: "Enumerates every distinct combination.", montecarlo: "Samples distinct random points.",
      anneal: "Tries neighboring points, sometimes accepting a worse point as the temperature cools.",
      gradient: "Uses coordinate descent over neighboring choices, rather than calculating derivatives.",
      genetic: "Evolves a population by selection, crossover and mutation.",
      pareto: "Explores trade-offs using a Pareto tree; the algorithm needs at least two objectives." }[value];
    return how + (space ? " Runs over the settings in Extra; checks and measurements judge each point."
      : " Without settings in Extra, this guides reasoning and prompts; it does not run a finite-space search algorithm.");
  }
  /** boxes.py: the boxes a coding agent may answer, and the ones that never are. */
  var DELEGABLE = ["validate", "orchestrate", "plan", "dse", "generate", "critique", "lessons", "select"];
  var NEVER = ["test", "calibrate"];
  /** The boxes a document may say a half for, in flow order (flux_loop.document FLOW_BOXES, less the
      ones the loop no longer takes as settings: analytical, simulation, records). */
  var FLOW_BOXES = ["validate", "orchestrate", "plan", "dse", "generate", "test", "critique", "calibrate",
                    "select", "feedback", "knowledge"];
  /** flux_loop.document BUILTIN_SUBS, the same list (a test compares them, D912). */
  var BUILTIN_SUBS = ["artifact", "workdir", "name", "part", "python", "home", "failure", "attempt",
                      "prompt", "prompt_file", "point", "params", "history", "state", "parts"];

  /** D728: a box whose agent has its own settings, as written (agentRaw) -- "*" is no agent's name, so
      an added agent may be called anything, "custom" too (D946). */
  var INLINE_AGENT = "agent:*";
  /** D934: an added agent's label (the server's), by name. */
  var AGENT_LABELS = {};
  function agentChoices(what) {
    return AGENTS.map(function (a) {
      return { value: "agent:" + a, half: "agent", label: "Coding agent: " + (AGENT_LABELS[a] ? AGENT_LABELS[a] + " (" + a + ")" : a), hint: what };
    });
  }
  /** D934: the server's agents ([{name, label}], the built-in three and those an admin added):
      every box's agent choices, and the names a document's `by:` may give, become these. */
  function setAgents(list) {
    var names = ["opencode", "claude", "codex"];
    AGENT_LABELS = {};
    (Array.isArray(list) ? list : []).forEach(function (a) {
      if (!a || typeof a.name !== "string" || names.indexOf(a.name) >= 0) return;
      names.push(a.name);
      if (a.label && a.label !== a.name) AGENT_LABELS[a.name] = String(a.label);
    });
    AGENTS.length = 0; Array.prototype.push.apply(AGENTS, names);
    PRESETS.length = 0; Array.prototype.push.apply(PRESETS, names);
    Object.keys(BOXES).forEach(function (b) {
      var cs = BOXES[b].choices || [], at = -1, hint;
      var kept = cs.filter(function (c, i) {
        var mine = typeof c.value === "string" && c.value.indexOf("agent:") === 0;
        if (mine && at < 0) { at = i; hint = c.hint; }
        return !mine;
      });
      if (at < 0) return;
      BOXES[b].choices = kept.slice(0, at).concat(agentChoices(hint), kept.slice(at));
    });
    return AGENTS;
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
    dse: { title: "Search policy", says: "Guides design and prototype experiments. With settings to search, existing algorithms still walk that space. Reasoned risks are allowed by default.",
      choices: [{ value: "none", half: "model", label: "Default: adaptive, reasoned risks welcome" }]
        .concat(DSE_INTENTS.map(function (p) {
          return { value: p, half: "model", label: { adaptive: "Adaptive: follow the evidence, take reasoned risks",
            explore: "Explore: try different algorithms or architectures", improve: "Improve: local or structural changes",
            tune: "Tune: favor nearby parameter and implementation changes", finetune: "Finetune: favor small, attributable changes",
            variations: "Variations: develop distinct alternatives" }[p] };
        }))
        .concat(DSE_POLICIES.map(function (p) {
          return { value: p, half: "rules", label: { sweep: "Try every combination", montecarlo: "Random samples",
            anneal: "Annealing", gradient: "Step towards better", genetic: "Genetic (breed the best)",
            pareto: "Trade-off front" }[p] + " (" + p + ")" };
        }))
        .concat([{ value: "model", half: "model", label: "Model: choose the next experiment" }])
        .concat(agentChoices("A coding agent proposes settings")) },
    generate: { title: "Make a design", says: "Writes each candidate design.",
      choices: [{ value: "model", half: "model", label: "A model writes it" },
                { value: "command", half: "rules", label: "My script writes it" }]
        .concat(agentChoices("A coding agent writes it")) },
    test: { title: "Check it works", says: "Runs your checks in order; a design that fails goes back to be repaired. Always yours, never a model's.",
      fixed: "Configured in Check & Measure: its checks.",
      choices: [{ value: "gate", half: "fixed", label: "Your checks (fixed)" }] },
    critique: { title: "Second opinion", says: "Optionally, a critic questions the parts, each admitted part and the final choice.",
      choices: [{ value: "off", half: "off", label: "No critic" },
                { value: "model", half: "model", label: "A model critic" }]
        .concat(agentChoices("A coding agent critic")) },
    measure: { title: "Measure", says: "Runs your measurements, cheapest first; a design that fails a gate is dropped.",
      fixed: "Configured in Check & Measure: its measurements (each may estimate first).",
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
    digest: { title: "Digest the papers", says: "Each paper of the library (library/ beside the document, and the shared one) is summed up once; the summaries reach every prompt.",
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
      (flux_loop.document describe_flow: the parenthesis of the box's line); null where the line has
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

  /** D911: a search whose points no script writes -- the settings alone are each candidate, and
      "Make a design" (a model, a coding agent) never runs: Flux instantiates a point only through
      `flow.generate: {command}`. */
  function paramOnly(state) {
    var f = (state && state.flow) || {};
    return !!(parameterSearch(state) && f.generate !== "command");
  }

  function parameterSearch(state) {
    return !!(state.flow && state.flow.dse && state.flow.dse !== "none" && (state.space || []).some(function (r) {
      return String(r.knob || "").trim() && choicesOf(r.choices).length;
    }));
  }

  /** The half a box is in for this state (for the drawing's colour). */
  function halfOf(state, box) {
    var v = (state.flow || {})[box];
    if (box === "orchestrate" && parameterSearch(state)) return "off";
    if (box === "generate" && paramOnly(state)) return "off";                    // D911: not run in a search
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

  /** flux_loop.document RTL_METRICS: what the loader infers for a `flux rtl measure` stage. */
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

  function fillText(text, t, row, auto, argv) {
    return String(text).replace(/\{([A-Za-z_]\w*)\}/g, function (m, name, at, all) {
      if (!(t.params && Object.prototype.hasOwnProperty.call(t.params, name))) return m;
      var v = paramValue(t, name, (row.params || {})[name], auto);
      // D910: in a command, a value that is a whole word stays one argument ("{home}/my bench.py")
      var whole = argv && !/\S/.test(all.charAt(at - 1) || " ") && !/\S/.test(all.charAt(at + m.length) || " ");
      return whole && name !== "command" && /[\s'"\\]/.test(v) ? shellWord(v) : v;
    }).trim();
  }

  /** A row's command, its params filled (`auto`: values that stand in for an empty param). */
  function fillRun(row, cat, auto) {
    var t = toolOf(row.tool, cat);
    if (!t) return String((row.params || {}).command || "").trim();
    if (t.run === undefined || t.run === null) return "";
    return fillText(t.run, t, row, auto, true);
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
      baseline: { mode: "off", source: "project", file: "", command: "", timeout: "" },
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
    if (/^-?\d+(\.\d+)?([eE][-+]?\d+)?$/.test(v)) return Number(v);
    if (v === "true" || v === "false") return v === "true";
    return v;
  }

  function scalar(v, flow) {
    if (typeof v === "number" && /e/.test(String(v)) && !/\./.test(String(v))) return String(v).replace("e", ".0e");   // YAML 1.1's float (D910)
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    return q(v, flow);
  }

  /** D910: a setting's choices as typed in its box -- separated by commas, a "quoted" choice
      kept as text (so "01", "true" and "fast,wide" stay strings), the rest read as typed. */
  function choicesOf(text) {
    var out = [], re = /\s*("(?:[^"\\]|\\.)*"|[^,]*)\s*(,|$)/g, m, src = String(text || "");
    while ((m = re.exec(src))) {
      var tok = m[1];
      if (/^".*"$/.test(tok)) { try { out.push(JSON.parse(tok)); } catch (e) { out.push(tok); } }
      else if (tok.trim()) out.push(typed(tok));
      if (!m[2] || re.lastIndex >= src.length) break;
    }
    return out;
  }
  /** A choice as its box shows it: quoted when, typed bare, it would read back as another value. */
  function choiceText(v) {
    if (typeof v !== "string") return String(v);
    return v === "" || typed(v) !== v || /[,"]/.test(v) || v !== v.trim() ? JSON.stringify(v) : v;
  }

  /** D910: a command split as `shlex.split` splits it (the loader's reading of a string). */
  function shellSplit(cmd) {
    var out = [], cur = null, s = String(cmd || ""), c, i, j;
    for (i = 0; i < s.length; i++) {
      c = s.charAt(i);
      if (/\s/.test(c)) { if (cur !== null) { out.push(cur); cur = null; } continue; }
      cur = cur || "";
      if (c === "'") { j = s.indexOf("'", i + 1); if (j < 0) j = s.length; cur += s.slice(i + 1, j); i = j; }
      else if (c === '"') {
        for (i++; i < s.length && s.charAt(i) !== '"'; i++) {
          if (s.charAt(i) === "\\" && /["\\$`]/.test(s.charAt(i + 1))) i++;
          cur += s.charAt(i);
        }
      } else if (c === "\\" && i + 1 < s.length) cur += s.charAt(++i);
      else cur += c;
    }
    if (cur !== null) out.push(cur);
    return out;
  }

  /** The files beside the document a command names, `{home}/...` words (D912: one word each, spaces and all). */
  function homeFiles(cmd) {
    return shellSplit(cmd).map(function (w) { var m = /^\{home\}\/(.+)$/.exec(w); return m ? m[1] : null; }).filter(Boolean);
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
    return (state.space || []).filter(function (r) { return String(r.knob || "").trim() && choicesOf(r.choices).length; })
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
        for (var key in d) if ((state.kept || []).indexOf(key) < 0) (docKeys[key] = docKeys[key] || []).push({ value: d[key], stage: String(st.name || "").trim() || "stage" + (i + 1) });
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
      if (b === "orchestrate" && parameterSearch(state)) return false;
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
    var basepass = state.baseline || {};
    if (own("baseline") && basepass.mode && basepass.mode !== "off") {
      var config = {};
      if (basepass.source === "file") config.file = String(basepass.file || "").trim();
      if (basepass.source === "command") {
        config.command = String(basepass.command || "").trim();
        if (String(basepass.timeout || "").trim()) config.timeout_s = typed(basepass.timeout);
      }
      if (basepass.mode === "only") config.only = true;
      out += "\nbaseline: " + (Object.keys(config).length ? inline(config, false) : "true") + "\n";
    }
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
    ["steps", "passes", "parallel", "batch", "repair_attempts", "workers", "exploration_quota"].forEach(function (key) {
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
    var space = (state.space || []).filter(function (x) { return String(x.knob || "").trim() && choicesOf(x.choices).length; });
    if (!own("flow.dse.space")) { if (kf["flow.dse.space"] !== undefined) D.push("    space: " + inline(kf["flow.dse.space"], false)); }
    else if (space.length) {
      D.push("    space:");
      space.forEach(function (x) { D.push("      " + q(x.knob.trim()) + ": " + flowSeq(choicesOf(x.choices))); });
    }
    if (kf["flow.dse.seeds"] !== undefined) D.push("    seeds: " + inline(kf["flow.dse.seeds"], false));
    if (D.length) {
      F.push("  orchestrate:                # a search: the points it tries (D797)");
      if (dv && typeof dv === "object" && !Array.isArray(dv)) Object.keys(dv).forEach(function (k) { F.push("    " + q(k) + ": " + inline(dv[k], false)); });
      else if (dv !== undefined) F.push("    policy: " + inline(dv, false));
      F = F.concat(D);
    } else if (dv !== undefined) {
      F = F.filter(function (line) { return !/^  orchestrate:/.test(line); });
      if (typeof dv === "string" && DSE_POLICIES.concat(DSE_INTENTS).indexOf(dv) >= 0) {
        var who = toSurface("orchestrate", flowObj(state, "orchestrate")) || "model";
        var intent = typeof who === "string" ? { by: who === "default" ? "model" : who, dse: dv } : Object.assign({}, who, { dse: dv });
        F.push("  orchestrate: " + inline(intent, false));
      } else F.push("  orchestrate: " + inline(dv, false));
    }

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

  /** The files beside the document the state names (`{home}/...` words, knowledge files), in order (D912). */
  function namedFiles(state, cat) {
    var r = resolve(state, cat || CATALOG), files = [];
    function add(f) { if (f && files.indexOf(f) < 0) files.push(f); }
    function cmd(c) { homeFiles(c).forEach(add); }
    r.checks.forEach(function (c) { cmd(c.run); });
    r.stages.forEach(function (st) { if (!st.shape) cmd(st.command); if (st.estimate && st.estimate.command) cmd(st.estimate.command); });
    if ((state.flow || {}).generate === "command") cmd(state.generateCommand);
    var bp = state.baseline || {};
    if (bp.mode && bp.mode !== "off") {
      if (bp.source === "command") cmd(bp.command);
      if (bp.source === "file") add(String(bp.file || "").trim().replace(/^\{home\}\//, ""));
    }
    for (var dkey in r.document) r.document[dkey].forEach(function (x) {
      var m = /^\{home\}\/(.+)$/.exec(typeof x.value === "string" ? x.value : ""); if (m) add(m[1]);
    });
    list(state.knowledgeFiles).forEach(add);
    return files;
  }

  /** The step of the web's wizard each part of the state is edited on (D912). D941: six steps, by id;
      the old seven's names land on the step that holds them now (checks and measurements are one). */
  var STEP_IDS = ["prompt", "measure", "objective", "graph", "extra", "save"];
  var STEP_OF = { prompt: 0, measure: 1, objective: 2, graph: 3, extra: 4, save: 5,
                  problem: 0, checks: 1, measurements: 1, objectives: 2, flow: 3, more: 4, review: 5 };
  /** D941: D826's seven steps (0-based) on the six. */
  var OLD_STEPS = [0, 1, 1, 2, 3, 4, 5];
  /** A step as an index of the six: an index, an id or an old step's name; `old`: a number is one of the seven. */
  function stepIndex(x, old) {
    if (typeof x === "string" && /^\d+$/.test(x)) x = Number(x);
    if (typeof x === "number" && isFinite(x)) {
      if (old) return OLD_STEPS[Math.max(0, Math.min(OLD_STEPS.length - 1, Math.floor(x)))];
      return Math.max(0, Math.min(STEP_IDS.length - 1, Math.floor(x)));
    }
    return Object.prototype.hasOwnProperty.call(STEP_OF, x) ? STEP_OF[x] : 0;
  }

  /** What is wrong or missing, as `{level: "error"|"warning"|"note", text, step, field}`, plain
      words; `step`: the wizard's step that fixes it, `field`: the box at fault (D912). */
  function check(state, cat) {
    cat = cat || CATALOG;
    var msgs = [], at = STEP_OF.problem;
    function say(level, t, field) { var m = { level: level, text: t, step: at }; if (field) m.field = field; msgs.push(m); }
    function error(t, field) { say("error", t, field); }
    function warn(t) { say("warning", t); }
    function note(t) { say("note", t); }
    var id = String(state.id || "").trim(), lang = language(state, true);
    var r = resolve(state, cat), flow = state.flow || {}, knobs = knobNames(state);
    if (!id) error("Give the problem a name (letters, digits and _).", "id");
    else if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(id)) error("The name \"" + id + "\" should be a letter, then letters, digits or _.", "id");
    if (!String(state.statement || "").trim()) error("Say what you want made (the statement is empty).", "statement");
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
    at = STEP_OF.checks;
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
    at = STEP_OF.measurements;
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
    at = STEP_OF.objectives;
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
    at = STEP_OF.flow;
    FLOW_BOXES.forEach(function (b) {
      var v = flow[b];
      if (typeof v === "string" && v.indexOf("agent:") === 0 && DELEGABLE.indexOf(b) < 0 && b !== "generate" && b !== "knowledge" && b !== "digest") {
        error("\"" + BOXES[b].title + "\" (" + b + ") is never handed to a coding agent: it establishes the facts.");
      } else if (v !== undefined && !choiceOf(b, v)) {
        error("\"" + BOXES[b].title + "\" (" + b + ") cannot be \"" + v + "\".");
      }
    });

    at = STEP_OF.more;
    var bp = state.baseline || {};
    if (bp.mode && bp.mode !== "off") {
      if (bp.source === "file" && !String(bp.file || "").trim()) error("Say the unchanged baseline design's file.", "baseline-file");
      if (bp.source === "command" && !String(bp.command || "").trim()) error("Say the baseline command.", "baseline-command");
      if (bp.source === "command" && bp.timeout && !(isFinite(Number(bp.timeout)) && Number(bp.timeout) > 0)) error("The baseline timeout must be a positive number.");
    }
    (state.space || []).forEach(function (x) {
      if (String(x.knob || "").trim() && !choicesOf(x.choices).length) error("The setting \"" + x.knob.trim() + "\" has no choices.");
    });
    var searching = parameterSearch(state);
    if (flow.dse && flow.dse !== "none" && !knobs.length) note("The DSE policy guides code and prototype experiments; no parameter space is required.");
    at = STEP_OF.flow;
    if (searching && flow.dse === "pareto" && r.objectives.length < 2) error("The trade-off front (pareto) needs two objectives or more.");
    if (state.budget.exploration_quota && !(Number(state.budget.exploration_quota) >= 0 && Number(state.budget.exploration_quota) <= 1)) error("Exploration quota must be a fraction between 0 and 1.");
    if (!searching && knobs.length) warn("The settings are only searched when a search policy is selected.");
    if (searching && flow.orchestrate && flow.orchestrate !== "default") warn("With a search, the search picks the next job; \"Pick the next job\" is left out.");
    if (flow.generate === "command" && !String(state.generateCommand || "").trim()) error("Say the command that writes each design.");

    var cmds = r.checks.map(function (c) { return ["check \"" + c.name + "\"", c.run, STEP_OF.checks]; });
    r.stages.forEach(function (st) {
      cmds.push(["measurement \"" + st.name + "\"", st.shape ? JSON.stringify(st.shape).replace(/[",:{}\[\]]/g, " ") : st.command, STEP_OF.measurements]);
      if (st.estimate && st.estimate.command) cmds.push(["the estimate of \"" + st.name + "\"", st.estimate.command, STEP_OF.measurements]);
    });
    if (flow.generate === "command") cmds.push(["the design script", state.generateCommand, STEP_OF.flow]);
    if (bp.mode && bp.mode !== "off" && bp.source === "command") cmds.push(["the baseline command", bp.command, STEP_OF.more]);
    var knobsSaid = false;
    cmds.forEach(function (c) {
      at = c[2];
      placeholders(c[1]).forEach(function (p) {
        if (knobs.indexOf(p) >= 0 || p === "point") knobsSaid = true;
        if (BUILTIN_SUBS.indexOf(p) < 0 && knobs.indexOf(p) < 0) error("In " + c[0] + ", {" + p + "} is neither a setting to search nor one of Flux's own.");
      });
    });
    // D911: a search's point becomes a design only through a script (Flux runs the generate command
    // once per point); a model or a coding agent is never asked, so the drawing does not say one
    at = STEP_OF.flow;
    if (searching && knobs.length && flow.generate !== "command") {
      if (!knobsSaid) error("A search's settings become designs only through a script: in \"Make a design\" choose \"My script writes it\" and say {" +
                            knobs[0] + "} in it, or use {" + knobs[0] + "} in a check or a measurement. A model or a coding agent is not asked for a search's points.");
      else note("A parameter-only search: each point's settings reach your commands as {" + knobs[0] + "}; no design is written, \"Make a design\" does not run.");
    }

    at = STEP_OF.more;
    ["steps", "passes", "parallel", "batch", "repair_attempts", "finalists", "workers"].forEach(function (key) {
      var v = String((state.budget || {})[key] || "").trim();
      if (v && !/^\d+$/.test(v)) error("Budget \"" + key + "\" should be a whole number.");
    });

    at = STEP_OF.flow;
    var agents = {};
    flowSaid(state).forEach(function (b) { if (String(flow[b]).indexOf("agent:") === 0 && flow[b] !== INLINE_AGENT) agents[flow[b].slice(6)] = 1; });
    if (Object.keys(agents).length) note("The coding agent " + Object.keys(agents).join(", ") + " must be installed where it runs.");
    var fl = namedFiles(state, cat);
    if (fl.length) { note("Put these beside the document: " + fl.join(", ") + "."); msgs[msgs.length - 1].files = fl; }
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
                    "objectives", "budget", "baseline"];
  var BUDGET_KEYS = ["steps", "passes", "parallel", "batch", "repair_attempts", "finalists", "workers", "prototype", "exploration_quota"];

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
    if (typeof v === "string") return DSE_POLICIES.concat(DSE_INTENTS).indexOf(v) >= 0 || ["control", "phases"].indexOf(v) >= 0;
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
      if (k === "orchestrate" && val && typeof val === "object" && "dse" in val) {
        f.dse = val.dse; val = Object.assign({}, val); delete val.dse;
      }
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
    var bp = raw.baseline;
    if (bp === true) s.baseline.mode = "before";
    else if (bp && typeof bp === "object" && !Array.isArray(bp) &&
             Object.keys(bp).every(function (k) { return ["file", "command", "only", "timeout_s"].indexOf(k) >= 0; })) {
      s.baseline.mode = bp.only ? "only" : "before";
      if (bp.file !== undefined) { s.baseline.source = "file"; s.baseline.file = String(bp.file); }
      if (bp.command !== undefined) { s.baseline.source = "command"; s.baseline.command = argvOf(bp.command).map(shellWord).join(" "); }
      if (bp.timeout_s !== undefined) s.baseline.timeout = String(bp.timeout_s);
    } else if (bp !== undefined && bp !== null && bp !== false) keep("baseline", "custom baseline settings");
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
      var plain = Object.keys(sp).every(function (k) {          // D910: plain values, each its type kept
        return Array.isArray(sp[k]) && sp[k].every(function (v) { return ["string", "number", "boolean"].indexOf(typeof v) >= 0; });
      });
      if (plain) s.space = Object.keys(sp).map(function (k) { return { knob: k, choices: sp[k].map(choiceText).join(", ") }; });
      else keep("space", "knobs that move with others (`when`), or choices that are not plain values");
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
        s.flow[box] = pre ? "agent:" + pre : INLINE_AGENT;
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
        // D880: a document that reads more numbers than its tool's entry reports keeps them all --
        // matched to the tool, the stage's other metrics were dropped on the way back
        if (m && (st.metrics || []).some(function (x) { return !(m.tool.metrics && x in m.tool.metrics); })) m = null;
        // D910: likewise a `needs` of its own: matched to the tool, the requirement was dropped
        if (m && said.needs !== undefined) {
          var own = /^flux rtl\s/.test(fillRun({ tool: m.tool.id, params: m.params }, cat)) ? [] : (m.tool.needs || []);
          var saidNeeds = Array.isArray(said.needs) ? said.needs : [said.needs];
          if (JSON.stringify(saidNeeds) !== JSON.stringify(own)) m = null;
        }
        if (m) row = { tool: m.tool.id, name: st.name, params: paramsOf(m.tool, m.params), metrics: "", needs: "", gates: [] };
        else row = { tool: "custom-stage", name: st.name, params: { command: argv.map(shellWord).join(" ") },
                     metrics: (st.metrics || []).join(", "), needs: (st.needs || []).join(", "), gates: [] };
      } else if (st.evaluator) {
        var ev = (cat || CATALOG).filter(function (t) { return t.role === "stage" && t.stage && t.stage.evaluator === st.evaluator; })[0];
        if (!ev) { stagesOk = false; return; }
        var p = {};
        for (var dk in ev.document || {}) {
          var mm = /^\{([A-Za-z_]\w*)\}$/.exec(String(ev.document[dk]));
          if (mm && typeof raw[dk] === "string") p[mm[1]] = shown(raw[dk]);
          // D910: an inline value (a Workload IR mapping) is no file name: kept as written, never "[object Object]"
          else if (mm && raw[dk] !== undefined && raw[dk] !== null) keep(dk, "an inline value, which the form names as a file");
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

  var api = { boxTitle: boxTitle, buildYaml: buildYaml, check: check, fromDoc: fromDoc, argvOf: argvOf, resolve: resolve, setCatalog: setCatalog, setAgents: setAgents, toolOf: toolOf,
              toolsFor: toolsFor, newCheck: newCheck, setCheckTool: setCheckTool, newStage: newStage,
              newObjective: newObjective, reports: reports, reported: reported, fillRun: fillRun,
              describeObjectives: describeObjectives, naturalDirection: naturalDirection, clockPs: clockPs,
              CHECK_TYPES: CHECK_TYPES, stageTools: stageTools, nextStageTool: nextStageTool, abbreviate: abbreviate, autoClock: autoClock, LABELS: LABELS,
              BOXES: BOXES, FLOW_BOXES: FLOW_BOXES, DELEGABLE: DELEGABLE, NEVER: NEVER, LANGUAGES: LANGUAGES,
              AGENTS: AGENTS, DSE_POLICIES: DSE_POLICIES, DSE_GROUPS: DSE_GROUPS, dseGroup: dseGroup, dseChoices: dseChoices, dseHint: dseHint,
              halfOf: halfOf, defaultFlow: defaultFlow, base: base, isFixed: isFixed,
              explain: explain, explainEstimate: explainEstimate, choicesOf: choicesOf, choiceText: choiceText, shellSplit: shellSplit,
              namedFiles: namedFiles, BUILTIN_SUBS: BUILTIN_SUBS, STEP_OF: STEP_OF, STEP_IDS: STEP_IDS, stepIndex: stepIndex };

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
    var parts = { step: stepIndex(opts.step || 0), touched: !!opts.touched, visited: {} };   // D941: an index or a step's id
    parts.visited[parts.step] = true;

    function changed(structural) {
      parts.touched = true;
      if (structural) renderForm();
      renderDiagram();
      renderOutput();
      if (openBox === "dse") { fillPopover(); placePopover(); }
      if (opts.onChange) opts.onChange(state, !!structural);    // D912: every edit, a button's too (the files panel follows)
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
        input.addEventListener("input", function () { input.removeAttribute("aria-invalid"); set(input.value); changed(false); });
      }
      // compact: the hint is the input's tooltip, not a line of its own
      if (opts.compact && opts.hint) input.setAttribute("title", opts.hint);
      if (opts.key) input.setAttribute("data-fc-field", opts.key);     // D912: a save's error focuses its box
      if (opts.disabled) input.disabled = true;
      // D941: the app's field -- a label.stack, its words above the box (flux.css; crafter.css on the docs page)
      return h("label", { class: "stack fc-field" + (opts.wide ? " fc-wide" : "") + (opts.narrow ? " fc-narrow" : "") + (opts.grow ? " fc-grow" : "") },
               [h("span", { class: "fc-label", text: label }), input, opts.hint && !opts.compact ? h("small", { class: "muted", text: opts.hint }) : null]);
    }

    function section(title, kids, cls) {
      return h("section", { class: "fc-section " + (cls || "") }, [h("h3", { text: title })].concat(kids));
    }

    /** A section whose title carries a short note on the same line. */
    function titled(title, note, kids) {
      return h("section", { class: "fc-section" }, [h("h3", {}, [title].concat(note))].concat(kids));
    }

    function button(text, fn, cls) {
      return h("button", { type: "button", class: "fc-btn " + (cls || ""), text: text, on: { click: fn } });   // D941: styled as the app's (small, primary)
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
        button("↑", function () { move(listOf, i, -1); }, "small fc-icon" + (i === 0 ? " fc-hidden" : "")),
        button("↓", function () { move(listOf, i, 1); }, "small fc-icon" + (i === listOf.length - 1 ? " fc-hidden" : "")),
        button("×", function () { listOf.splice(i, 1); changed(true); }, "small fc-icon")]));
    }

    function moreButton(row) {
      var open = moreOpen.has(row);
      var b = button(open ? "less" : "more", function () {
        if (moreOpen.has(row)) moreOpen.delete(row); else moreOpen.add(row);
        changed(true);
      }, "small fc-more-btn");
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
        }, "small"), moreButton(st)]));
        var kids = [h("div", { class: "fc-line" }, line)];
        st.gates.forEach(function (g, j) {
          kids.push(h("div", { class: "fc-line fc-gate" }, [h("span", { class: "fc-gate-word", text: j ? "and" : "go on only if" }),
            field("Number", function () { return g.metric; }, function (v) { g.metric = v; },
                  { compact: true, options: (rep.indexOf(g.metric) < 0 && g.metric ? [g.metric] : []).concat(rep) }),
            field("Rule", function () { return g.rule; }, function (v) { g.rule = v; },
                  { compact: true, structural: true, options: [["at", "at least"], ["below", "at most"], ["within", "within % of the best"]] }),
            field(g.rule === "within" ? "Percent" : "Value", function () { return g.value; }, function (v) { g.value = v; }, { compact: true, narrow: true }),
            button("×", function () { st.gates.splice(j, 1); changed(true); }, "small fc-icon")]));
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

    // -- level 1: the prompt (D941: step 1), then the checks, the measurements and the objective
    function renderPrompt() {
      var implied = impliedLanguage(state);                // D832: optional -- the tools usually tell
      var langs = [["", implied ? "from the tools: " + implied : "from the tools (none tells yet)"]].concat(LANGUAGES.map(function (l) { return [l, l]; })).concat([["other", "other..."]]);
      var what = titled("1. What do you want?", [], [
        h("div", { class: "fc-line" }, [
          field(opts.nameLabel || "Name", function () { return state.id; }, function (v) { state.id = v; },
                { compact: true, key: "id", placeholder: opts.namePlaceholder || "my_design", hint: opts.nameHint || "Letters, digits and _" }),
          field("Language", function () { return state.language; }, function (v) { state.language = v; },
                { compact: true, options: langs, structural: true, hint: "Optional: the language the designs are written in, when the checks' tools do not tell it" }),
          state.language === "other" ? field("Which language?", function () { return state.languageOther; }, function (v) { state.languageOther = v; }, { compact: true, placeholder: "ini" }) : null,
          ]),
        field("What should be made? Say it as you would to an engineer.", function () { return state.statement; },
              function (v) { state.statement = v; }, { area: true, rows: 3, wide: true, key: "statement" }),
        h("div", { class: "fc-line" }, [
          field("Rules every design must follow (optional)", function () { return state.contract; },
                function (v) { state.contract = v; }, { area: true, rows: 1, grow: true, placeholder: "Names, ports, what is not allowed" }),
          field("Files the model reads (optional)", function () { return state.knowledgeFiles; },
                function (v) { state.knowledgeFiles = v; }, { compact: true, grow: true, placeholder: "spec.md, notes.txt", hint: "Beside the document, separated by commas" })]),
        // D828: who digests library/'s papers is a box of the drawing ("Digest the papers"), not a field here
      ]);
      return what;
    }
    function noCatalog() {
      return CATALOG.length ? null : h("p", { class: "fc-hint", text: "The tool list did not load; only Custom checks and measurements are offered." });
    }
    function renderLevel1() {
      return h("div", { class: "fc-level" }, [renderPrompt(), noCatalog(), renderChecks(), renderStages(), renderObjective()]);
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
        var box = BOXES[n.box] || { title: "Parts", fixed: "Set under Extra > Parts." };
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
      if (name === "orchestrate" && state.flow.orchestrate === "default" && !parameterSearch(state)) {
        text = hasParts() ? "model picks the part" : "default: one design";
      }
      if (name === "generate" && paramOnly(state)) text = "not run: settings only";        // D911
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
        button("\u00d7", function () { openPopover(null, true); }, "small fc-icon fc-pop-close")]));
      p.appendChild(h("p", { text: box.says }));
      var now = explain(openBox, state);
      if (now) p.appendChild(h("p", { class: "fc-hint fc-now", text: "As set: " + now + "." }));
      var raw = state.agentRaw && state.agentRaw[openBox];
      if (raw) p.appendChild(h("p", { class: "fc-hint fc-now", text: "A coding agent with its own settings, kept as written: " + JSON.stringify(raw.agent) +
        ". Choosing another replaces them." }));
      if (openBox === "generate" && paramOnly(state)) {               // D911: what a search runs
        p.appendChild(h("p", { class: "fc-hint fc-now", text: "A search is on: only a script turns a point's settings into a design. " +
          "With a model or a coding agent here, nothing writes a design -- the settings alone reach your commands (a parameter-only search)." }));
      }
      if (openBox === "orchestrate" && parameterSearch(state)) {
        p.appendChild(h("p", { class: "fc-hint", text: "A search is on, so the search picks the next job." }));
      } else if (box.choices.length === 1) {
        p.appendChild(h("p", { class: "fc-hint", text: "This step is fixed: " + box.choices[0].label + "." }));
      } else {
        var group = h("div", { class: "fc-choices", role: "radiogroup", "aria-label": box.title });
        if (openBox === "dse") {
          p.appendChild(dseGroupField("dse-group-popover"));
          p.appendChild(h("p", { class: "fc-hint", text: dseHint(state) }));
        }
        (openBox === "dse" ? dseChoices(dseGroup(state.flow.dse), state.flow.dse) : box.choices).forEach(function (c) {
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

    function dseGroupField(key) {
      return field("DSE policy category", function () { return dseGroup(state.flow.dse); }, function (v) {
        // Selecting a category selects its default policy. The category itself is never saved.
        state.flow.dse = dseChoices(v)[0].value;
        if (state.agentRaw) delete state.agentRaw.dse;
      }, { key: key, options: DSE_GROUPS, structural: true, grow: true,
        disabled: (state.kept || []).indexOf("flow.boxes") >= 0 });
    }

    function renderLevel2() {
      var kept = (state.kept || []).indexOf("flow.boxes") >= 0;
      var choices = dseChoices(dseGroup(state.flow.dse), state.flow.dse).map(function (c) { return [c.value, c.label]; });
      var policy = field("DSE search policy", function () { return state.flow.dse; }, function (v) {
        state.flow.dse = v;
        if (state.agentRaw) delete state.agentRaw.dse;
      }, { key: "dse", options: choices, structural: true, disabled: kept, grow: true,
        hint: kept ? "The flow has custom settings kept as written; edit its DSE setting in Direct edit." :
          dseHint(state) });
      var selectors = h("div", { class: "fc-line fc-dse-selectors" }, [dseGroupField("dse-group"), policy]);
      if (!opts.foldSteps) {
        return h("div", { class: "fc-level" }, [titled("2. Who does each step?", [h("span", { class: "fc-hint fc-inline", text: " click a box to change it; the defaults are usually right" })], [selectors].concat(drawing()))]);
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
      return h("section", { class: "fc-section fc-advanced" }, [h("h3", {}, [toggle]), selectors, body]);
    }

    // -- level 3: the same fields and rows as above, behind one toggle
    function sub(title, note, kids) {
      return h("div", { class: "fc-sub" }, [h("h4", {}, [title, note ? h("span", { class: "fc-hint fc-inline", text: " " + note }) : null])].concat(kids));
    }

    function renderLevel3() {
      var b = state.budget;
      var bp = state.baseline || (state.baseline = { mode: "off", source: "project" });
      var baseline = sub("Baseline / pass 0", "check and measure before any agent edits or repairs", [
        field("Baseline pass", function () { return bp.mode; }, function (v) { bp.mode = v; },
          { key: "baseline-mode", structural: true, disabled: (state.kept || []).indexOf("baseline") >= 0,
            options: [["off", "Off (default)"], ["before", "Pass 0, then normal passes"], ["only", "Pass 0 only: check tools and measure"]],
            hint: "Runs before parallel passes only when no baseline is recorded or its inputs, settings or tools changed. Unchanged restarts reuse its results. Pass 0 does not use the normal pass budget; new measurements bypass caches and estimators." }),
        bp.mode !== "off" ? field("Baseline source", function () { return bp.source; }, function (v) { bp.source = v; },
          { key: "baseline-source", structural: true, options: [["project", "Current project: run checks and measurements as written"],
            ["file", "Existing design file (unchanged)"], ["command", "A baseline preparation command"]],
            hint: "Current project uses your scripts as written. Select a file when scripts expect a design in {artifact}." }) : null,
        bp.mode !== "off" && bp.source === "file" ? field("Baseline design file", function () { return bp.file; }, function (v) { bp.file = v; },
          { key: "baseline-file", placeholder: "baseline.py", hint: "Relative to the loop folder. Flux checks and measures a copy, leaving the original untouched." }) : null,
        bp.mode !== "off" && bp.source === "command" ? field("Baseline command", function () { return bp.command; }, function (v) { bp.command = v; },
          { key: "baseline-command", placeholder: "{python} {home}/baseline.py {artifact}",
            hint: "Runs once with the usual placeholders and first seed or default knob values. May write {artifact}, or prepare the project for your scripts." }) : null,
        bp.mode !== "off" && bp.source === "command" ? field("Baseline command timeout (seconds)", function () { return bp.timeout; }, function (v) { bp.timeout = v; },
          { placeholder: "600" }) : null]);
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
        num("Exploration quota", "exploration_quota", "0", "Minimum fraction of attempted improvements reserved for new approaches, 0 to 1; 0 leaves the choice to the model"),
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
                { compact: true, grow: true, placeholder: "16, 32, 64", hint: "Separated by commas; a \"quoted\" choice stays text (\"01\", \"a,b\")" }),
          h("div", { class: "fc-row-buttons" }, [button("\u00d7", function () { state.space.splice(i, 1); changed(true); }, "small fc-icon")])])]);
      });
      var searching = state.flow.dse && state.flow.dse !== "none";
      var space = sub("Settings to search", searching ? "each is {its name} in the commands" : "used once a search policy is selected in the Graph", [
        h("div", { class: "fc-rows" }, knobs),
        button("+ Add a setting", function () { state.space.push({ knob: "", choices: "" }); changed(true); }, "fc-add-btn")]);

      var split = sub("Parts", "one design made as several, each checked on its own, then composed", [h("div", { class: "fc-line" }, [
        field("Split the design", function () { return state.partsMode; }, function (v) { state.partsMode = v; },
              { compact: true, structural: true, options: [["none", "no"], ["list", "into these parts"], ["decompose", "let it decide"]] }),
        state.partsMode === "list" ? field("Parts", function () { return state.parts; }, function (v) { state.parts = v; },
                                           { compact: true, grow: true, placeholder: "decoder, datapath", hint: "Part names, separated by commas" }) : null])]);

      // closed until asked for, or until something in it is in use
      var inUse = !!(searching || state.space.length || hasParts() || bp.mode !== "off");
      if (inUse && !parts.advancedUsed) parts.advancedOpen = true;
      parts.advancedUsed = inUse;
      var body = h("div", { class: "fc-advanced-body" }, [baseline, sub("Budget", "empty = the loop's default, shown greyed", [budget]), space, split]);
      body.hidden = !parts.advancedOpen;
      var toggle = h("button", { type: "button", class: "fc-toggle", "aria-expanded": parts.advancedOpen ? "true" : "false", on: { click: function () {
        parts.advancedOpen = !parts.advancedOpen;
        body.hidden = !parts.advancedOpen;
        toggle.setAttribute("aria-expanded", parts.advancedOpen ? "true" : "false");
      } } }, ["3. Advanced", h("span", { class: "fc-hint fc-inline", text: " budget, settings to search, parts" })]);
      return h("section", { class: "fc-section fc-advanced" }, [h("h3", {}, [toggle]), body]);
    }

    // D826: steps instead of one long form -- a step bar, one step at a time, Back and Next; the
    // document, its checklist and the save on the last step (and Save on every step when editing).
    // D941: six -- the prompt, the checks with the measurements, the objective, the graph, the rest, the save
    var STEPS = ["Prompt", "Check & Measure", "Objective", "Graph", "Extra", "Save"];
    // D828: what each step is for, in a line -- in place of the long form's numbered titles
    var STEP_SAYS = [
      "What the loop designs, in your words: what to make, the rules every design must respect, the files the model reads.",
      "Checks first: each refuses a wrong design, in order, and the first that fails sends it back to be repaired. " +
        "Then the measurements: each sizes or times a design that passed, cheapest first; a costly one runs only on the best of the cheaper.",
      "What makes one design better: limits it must meet, then what to push, most important first.",
      "Who works each step: built-in rules, a model, or a coding agent. Click a box to change it; the defaults are usually right.",
      "The budget, settings to search over, splitting one design into parts" + (opts.extra ? ", the loop's resources" : "") +
        ". An empty field keeps the default, shown greyed.",
      ""];
    function renderForm() {
      parts.form.innerHTML = "";
      if (!stepped) {
        parts.form.appendChild(renderLevel1());
        parts.form.appendChild(renderLevel2());
        parts.form.appendChild(renderLevel3());
        return;
      }
      var step = parts.step || 0, id = STEP_IDS[step];
      // D941: the step bar is the app's subtabs, each step a button
      parts.stepItems = STEPS.map(function (t, i) {
        return h("button", { type: "button", role: "tab", class: i === step ? "on" : "", "data-step": STEP_IDS[i],
          "aria-selected": i === step ? "true" : "false", on: { click: function () { go(i); } } }, [h("span", { class: "fc-num", text: String(i + 1) }), t]);
      });
      var bar = h("div", { class: "subtabs fc-stepbar", role: "tablist", "aria-label": "Steps" }, parts.stepItems);
      var body;
      if (id === "prompt") body = [renderPrompt()];
      else if (id === "measure") body = [noCatalog(), renderChecks(), renderStages()];
      else if (id === "objective") body = [renderObjective()];
      else if (id === "graph") {
        var was = opts.foldSteps; opts.foldSteps = false; body = [renderLevel2()]; opts.foldSteps = was;
      } else if (id === "extra") {
        parts.advancedOpen = true; body = [renderLevel3()].concat(opts.extra ? [opts.extra] : []);
      } else body = [];
      body = body.filter(Boolean);
      // D913: on a phone, "Step 1 of 6" and a menu of the steps in place of the bar
      var menu = h("select", { "aria-label": "Step", class: "fc-stepmenu" }, STEPS.map(function (t, i) {
        var o = h("option", { value: String(i), text: t }); if (i === step) o.selected = true; return o;
      }));
      menu.addEventListener("change", function () { go(Number(menu.value)); });
      var stephead = h("div", { class: "fc-stephead" }, [h("span", { class: "fc-stepof", text: "Step " + (step + 1) + " of " + STEPS.length }), menu]);
      var last = step === STEPS.length - 1;
      var back = step > 0 ? button("Back", function () { go(step - 1); }, "fc-back") : h("span", { class: "fc-back" });
      var next = !last ? h("button", { type: "button", class: "fc-btn primary fc-next", on: { click: function () { go(step + 1); } } },
                           ["Next", h("span", { class: "fc-next-what", text: ": " + STEPS[step + 1] })]) : null;
      // D912: the save's status beside the button pressed, said aloud, on every step; D913: one bar,
      // Back, the save, Next -- the save the primary action on the last step
      parts.navStatus = h("span", { class: "fc-status", role: "status", "aria-live": "polite" });
      parts.navSave = opts.save ? button(opts.saveLabel || "Save", function () { save(parts.navSave); }, "fc-save" + (last ? " primary" : "")) : null;
      // D941: a new loop's draft kept in this browser, from Save
      var draftBtn = last && opts.saveDraft ? button("Save draft", saveDraft, "fc-draft") : null;
      var nav = h("div", { class: "fc-stepnav" + (opts.save ? "" : " fc-nosave") + (draftBtn ? " fc-has-draft" : "") },
                  [back, h("span", { class: "fc-grow" }), parts.navStatus, draftBtn, parts.navSave, next]);
      body.forEach(function (el) {                       // the long form's titles: the step bar says them
        if (!el.querySelectorAll) return;
        var t = el.querySelector("h3");
        if (t && id !== "measure") t.parentNode.removeChild(t);       // two sections: each keeps its title
        Array.prototype.forEach.call(el.querySelectorAll(".fc-advanced-body"), function (b) { b.hidden = false; });
      });
      if (STEP_SAYS[step]) body.unshift(h("p", { class: "fc-step-says", text: STEP_SAYS[step] }));
      if (parts.out) {                                   // the document is the last step's own, inside it
        parts.out.hidden = !last;
        if (last) body.push(parts.out);
        else if (parts.bodyEl && parts.out.parentNode !== parts.bodyEl) parts.bodyEl.appendChild(parts.out);
      }
      parts.form.appendChild(bar);
      parts.form.appendChild(stephead);
      parts.form.appendChild(h("div", { class: "fc-step" + (last ? " fc-step-last" : ""), "data-step": id }, body));
      // D941: on Save, the actions after the summary and what blocks it, before the folded document
      if (last && parts.navSlot) parts.navSlot.replaceChildren(nav);
      else { if (parts.navSlot) parts.navSlot.replaceChildren(); parts.form.appendChild(nav); }
      showStatus();
      if (id === "graph") setTimeout(renderDiagram, 0);
    }
    function go(i) {
      parts.visited[parts.step || 0] = true;
      parts.step = Math.max(0, Math.min(STEPS.length - 1, i));
      parts.visited[parts.step] = true;
      if (opts.onStep) opts.onStep(parts.step, STEP_IDS[parts.step]);
      renderForm();
      renderOutput();
      if (parts.form.scrollIntoView && parts.form.getBoundingClientRect && parts.form.getBoundingClientRect().top < 0) parts.form.scrollIntoView();
    }

    /** D912: a step is green when it was visited and nothing on it is to fix, red when something is. */
    function markSteps(msgs) {
      (parts.stepItems || []).forEach(function (li, i) {
        var bad = msgs.some(function (m) { return m.level === "error" && m.step === i; });
        var seen = parts.visited[i] && i !== parts.step && i < STEPS.length - 1;
        li.classList.toggle("fc-done", !!(seen && !bad));
        li.classList.toggle("fc-bad", !!(seen && bad && !(opts.calmChecks && !parts.touched)));
      });
    }

    /** D941: the draft kept where the page keeps it (`opts.saveDraft(state, step)` returns a line to say). */
    function saveDraft() {
      try {
        parts.status = { kind: "ok", text: opts.saveDraft(state, STEP_IDS[parts.step || 0]) || "Draft saved." };
      } catch (e) {
        parts.status = { kind: "err", text: (e && e.message) || String(e) };
      }
      showStatus();
    }
    /** D912: the save's state, pending, done or failed -- beside both save buttons. */
    function showStatus() {
      var st = parts.status || { kind: "", text: "" };
      [parts.navStatus, parts.saved].forEach(function (el) {
        if (!el) return;
        el.textContent = st.text;
        el.className = "fc-status" + (st.kind ? " fc-" + st.kind : "");
      });
      [parts.navSave, parts.saveBtn].forEach(function (b) { if (b) b.disabled = st.kind === "pending"; });
    }

    /** Save (or create): one at a time; an error says itself beside `btn` and focuses its box. */
    function save(btn) {
      if (!opts.save || (parts.status && parts.status.kind === "pending")) return;
      parts.status = { kind: "pending", text: "Saving…" };
      showStatus();
      Promise.resolve().then(function () { return opts.save(buildYaml(state), state); }).then(function (said) {
        parts.status = { kind: "ok", text: said || "Saved." };
        showStatus();
      }, function (e) {
        parts.status = { kind: "err", text: (e && e.message) || String(e) };
        var key = e && e.field;
        if (key && stepped) {
          var at = key === "id" || key === "statement" ? STEP_OF.problem : null;
          if (at !== null && at !== parts.step) go(at);
        }
        showStatus();
        var box = key && parts.form.querySelector('[data-fc-field="' + key + '"]');
        if (box) { box.setAttribute("aria-invalid", "true"); box.focus(); }
        else if (btn && btn.isConnected) btn.focus();
      });
    }

    /** D912: where the draft stands -- the document, its files, the check, a start -- apart. */
    function readiness(msgs) {
      var errors = msgs.filter(function (m) { return m.level === "error"; }).length;
      var calm = opts.calmChecks && !parts.touched;
      var rows = [[errors ? (calm ? "todo" : "error") : "ok", "Document", errors ? errors + " thing(s) to do, below" : "complete"]];
      var named = namedFiles(state), files = opts.files ? opts.files(named) : null;
      if (files) {
        var missing = files.missing || [];
        rows.push([missing.length ? "error" : "ok", "Files", missing.length ? "missing " + missing.join(", ") + " (Files that go with it)"
          : named.length ? "all " + named.length + " named file(s) present" : "none named"]);
      } else if (named.length) rows.push(["note", "Files", "put " + named.join(", ") + " beside the document"]);
      if (opts.save) {
        // D913b: the loop's own check result, as the start dialog reads it -- [kind, words], or null while unknown
        var ck = (typeof opts.checked === "function" ? opts.checked() : opts.checked) || ["note", "not checked yet: once saved, Check runs it where it will run"];
        rows.push([ck[0], "Checked", ck[1]]);
        var go = !errors && !(files && (files.missing || []).length) && ck[0] === "ok";
        rows.push([go ? "ok" : "note", "Ready to run", go ? "yes: Start it" : ck[0] === "error" ? "not until the check passes" : "after a Check passes"]);
      }
      return rows;
    }

    function renderOutput() {
      renderGoalWords();
      var yaml = buildYaml(state), id = String(state.id || "").trim() || "my_problem";
      var file = id + "/problem.yaml";
      parts.code.textContent = yaml;
      parts.file.textContent = file;
      var msgs = check(state);
      markSteps(msgs);
      parts.ready.innerHTML = "";
      readiness(msgs).forEach(function (r) {
        parts.ready.appendChild(h("li", { class: "fc-" + r[0] }, [h("strong", { text: r[1] + ": " }), r[2]]));
      });
      parts.checks.innerHTML = "";
      var todo = msgs.filter(function (m) { return !m.files; });      // the files: in Where it stands
      var calm = opts.calmChecks && !parts.touched;   // nothing typed yet: what is left to do, not errors
      // D941: what blocks it first, then what to look at, then the notes; nothing when nothing is left
      var rank = { error: 0, warning: 1, note: 2 };
      todo.slice().sort(function (a, b) { return rank[a.level] - rank[b.level]; }).forEach(function (m) {
        parts.checks.appendChild(h("li", { class: "fc-" + (calm && m.level !== "note" ? "todo" : m.level), text: m.text }));
      });
      renderSummary();
      if (parts.diffFold && parts.diffFold.open) { clearTimeout(parts.diffTimer); parts.diffTimer = setTimeout(showChanges, 500); }
      if (parts.next) parts.next.textContent = "flux task check " + file + "\nflux task run " + file + " --passes 1";
    }

    /** D913: what runs, in words. D941: short -- what it makes, its checks and measurements, the goal,
        and only what differs from the defaults (who works, the budget); the steps show the rest. */
    function renderSummary() {
      if (!parts.summary) return;
      var r = resolve(state), rows = [];
      var what = String(state.statement || "").trim().replace(/\s+/g, " ");
      rows.push(["Makes", what ? (what.length > 160 ? what.slice(0, 159) + "…" : what) : "not said yet"]);
      var line = function (xs) { return xs.length ? xs.map(function (x) { return x.name; }).join(" → ") : "none yet"; };
      rows.push(["Checks, then measures", line(r.checks) + " · " + line(r.stages)]);
      var words = describeObjectives(r.objectives);
      rows.push(["Goal", words.length ? words[0] : "none yet"]);
      var dflt = defaultFlow();
      var who = FLOW_BOXES.concat(["digest", "lessons"]).filter(function (b) { return BOXES[b] && !isFixed(b) && state.flow[b] !== dflt[b]; }).map(function (b) {
        var c = choiceOf(b, state.flow[b]);
        return BOXES[b].title + ": " + (b === "generate" && paramOnly(state) ? "not run (settings only)" : c ? c.label : state.flow[b]);
      });
      rows.push(["Who works", who.length ? who.join("; ") : "the defaults"]);
      var bu = state.budget || {}, said = Object.keys(bu).filter(function (k) { return String(bu[k] || "").trim(); });
      if (said.length) rows.push(["Budget", said.map(function (k) { return k + " " + bu[k]; }).join(", ")]);
      var kn = knobNames(state);
      if (kn.length) rows.push(["Searches", kn.join(", ")]);
      var bp = state.baseline || {};
      if (bp.mode && bp.mode !== "off") rows.push(["Baseline", "pass 0" + (bp.mode === "only" ? " only" : ", then normal passes") + ": " +
        (bp.source === "file" ? bp.file : bp.source === "command" ? bp.command : "current project")]);
      parts.summary.innerHTML = "";
      rows.forEach(function (x) { parts.summary.appendChild(h("div", {}, [h("dt", { text: x[0] }), h("dd", { text: x[1] })])); });
    }

    /** D941: what the save would change, folded until asked (`opts.changes(yaml)`: a node, or its promise). */
    function showChanges() {
      if (!parts.diffBody) return;
      var asked = parts.diffAsked = (parts.diffAsked || 0) + 1;
      Promise.resolve().then(function () { return opts.changes(buildYaml(state), state); }).then(function (node) {
        if (asked === parts.diffAsked) parts.diffBody.replaceChildren(node || h("p", { class: "fc-hint", text: "No change." }));
      }, function (e) {
        if (asked === parts.diffAsked) parts.diffBody.replaceChildren(h("p", { class: "fc-hint", text: "The changes could not be read: " + ((e && e.message) || e) }));
      });
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
    parts.ready = h("ul", { class: "fc-checks fc-ready" });            // D912: document, files, check, start -- apart
    parts.next = opts.nextSteps === false ? null : h("code", {});
    parts.copyBtn = button("Copy", copy, opts.save ? "" : "primary");
    parts.saved = h("span", { class: "fc-status", role: "status", "aria-live": "polite" });
    // stepped, the save is the step bar's (D913); the whole form keeps its own beside the document
    var saveBtn = opts.save && !stepped ? button(opts.saveLabel || "Save", function () { save(saveBtn); }, "primary") : null;
    var keptNotes = (opts.notes || []).length ? [h("details", { class: "fc-fold fc-kept-fold" }, [
      h("summary", {}, [h("strong", { text: "Kept as written" }), " ", h("span", { class: "fc-hint", text: "(" + opts.notes.length + ")" })]),
      h("ul", { class: "fc-checks" }, opts.notes.map(function (n) { return h("li", { class: "fc-note", text: n }); }))])] : [];
    parts.saveBtn = saveBtn;
    parts.summary = h("dl", { class: "fc-summary" });
    parts.navSlot = stepped ? h("div", { class: "fc-navslot" }) : null;
    // D913: Review reads top down. D941: Save -- the summary, what blocks it, the actions; the changes,
    // what is kept as written and the document folded below
    var changes = [];
    if (opts.changes) {
      parts.diffBody = h("div", { class: "fc-diff" });
      parts.diffFold = h("details", { class: "fc-fold fc-diff-fold" }, [h("summary", {}, [h("strong", { text: "Changes" }), " ",
        h("span", { class: "fc-hint", text: "against the saved document" })]), parts.diffBody]);
      parts.diffFold.addEventListener("toggle", function () { if (parts.diffFold.open) showChanges(); });
      changes.push(parts.diffFold);
    }
    var yamlBox = h("details", { class: "fc-fold fc-yaml-fold" }, [h("summary", {}, [h("strong", { text: "Document" }), " ", parts.file]),
      h("div", { class: "fc-yaml-acts" }, [parts.copyBtn, button("Download", download)]),
      h("pre", { class: "fc-yaml" }, [parts.code])]);
    if (!opts.save) yamlBox.open = true;             // the docs' page: the document is what one takes away
    var out = parts.out = h("div", { class: "fc-output" }, [
      saveBtn ? h("div", { class: "fc-output-head" }, [h("span", { class: "fc-grow" }), parts.saved, saveBtn]) : null,
      parts.summary, parts.ready, parts.checks, parts.navSlot].concat(changes).concat(keptNotes).concat([yamlBox]).concat(opts.nextSteps === false ? [] : [
      h("h3", { text: "Next steps" }),
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
    // D912: the page asks again where it stands (its files changed), and reads the state it edits
    return { state: state, refresh: renderOutput, step: function () { return parts.step; } };
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
