/* The Flux problem builder's model (website/docs/guide/loop-crafter.md, the web app's configurator).

   The document side, pure, for a browser and for node alike: `buildYaml(state) -> string`,
   `check(state) -> [{level, text}]`, `fromDoc(raw, normal)` and the vocabulary they draw from. The
   words mirror flux_loop/document/ (DOCUMENT_KEYS, FLOW_BOXES, _FLOW_WORDS, EXTENSIONS),
   flux_loop/boxes.py (DELEGABLE, NEVER) and the registered DSE policies;
   flux/tests/unit/test_loop_crafter.py loads what this writes with the real loader.
   Its views are elsewhere: crafter.js (the docs page, vanilla DOM) and the web app's
   static/configure/ (Preact). A browser finds it as `window.FluxCrafterModel`. */
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
  var DSE_GROUPS = [["preferences", "Guidance"], ["algorithms", "Algorithms"], ["delegated", "AI"]];
  function dseGroup(value) {
    if (value === "none" || DSE_INTENTS.indexOf(value) >= 0) return "preferences";
    return DSE_POLICIES.indexOf(value) >= 0 ? "algorithms" : "delegated";
  }
  function dseChoices(group, current) {
    var choices = BOXES.dse.choices.filter(function (c) { return dseGroup(c.value) === group; });
    if (current && dseGroup(current) === group && !choiceOf("dse", current)) {
      choices.push({ value: current, label: "Own agent settings (as written)", half: "agent" });
    }
    return choices;
  }
  function dseHint(state) {
    var value = state.flow.dse, group = dseGroup(value), space = parameterSearch(state);
    if (value === "none") return "Adaptive: local improvements or a new approach, from the evidence. Pick a policy to search the settings in Extra.";
    if (group === "preferences") return INTENT_SAYS[value] + (space
      ? " The model proposes legal settings from it and the measured history."
      : " Guides the next experiment and the design, repair and prototype prompts.");
    if (group === "delegated") return (value === "model" ? "Needs a model connection. " : "Needs the agent installed and set up. ") + (space
      ? "Proposes points from the settings in Extra; checks and measurements judge them."
      : "Chooses the next job and direction; Design chooses who writes it.");
    var how = { sweep: "Enumerates every distinct combination.", montecarlo: "Samples distinct random points.",
      anneal: "Tries neighboring points, sometimes accepting a worse point as the temperature cools.",
      gradient: "Uses coordinate descent over neighboring choices, rather than calculating derivatives.",
      genetic: "Evolves a population by selection, crossover and mutation.",
      pareto: "Explores trade-offs using a Pareto tree; the algorithm needs at least two objectives." }[value];
    return how + (space ? " Runs over the settings in Extra." : " Without settings in Extra, it only guides the prompts: no search runs.");
  }
  var INTENT_SAYS = { adaptive: "Follows the evidence, takes reasoned risks.", explore: "Tries different algorithms or architectures.",
    improve: "Local or structural changes.", tune: "Favors nearby parameter and implementation changes.",
    finetune: "Favors small, attributable changes.", variations: "Develops distinct alternatives." };
  /** boxes.py: the boxes a coding agent may answer, and the ones that never are. */
  var DELEGABLE = ["validate", "orchestrate", "plan", "dse", "generate", "critique", "lessons", "select"];
  var NEVER = ["test", "calibrate"];
  /** The boxes a document may say a half for, in flow order (flux_loop.document FLOW_BOXES, less the
      ones the loop no longer takes as settings: analytical, simulation, records). */
  var FLOW_BOXES = ["validate", "orchestrate", "plan", "dse", "generate", "test", "critique", "calibrate",
                    "select", "feedback", "knowledge"];
  /** flux_loop.document BUILTIN_SUBS, the same list (a test compares them, D912). */
  var BUILTIN_SUBS = ["artifact", "workdir", "name", "part", "python", "home", "failure", "attempt",
                      "prompt", "prompt_file", "point", "params", "history", "state", "parts", "scope"];

  /** D728: a box whose agent has its own settings, as written (agentRaw) -- "*" is no agent's name, so
      an added agent may be called anything, "custom" too (D946). */
  var INLINE_AGENT = "agent:*";
  /** D934: an added agent's label (the server's), by name. */
  var AGENT_LABELS = {};
  function agentChoices(what) {
    return AGENTS.map(function (a) {
      return { value: "agent:" + a, half: "agent", label: "Agent: " + (AGENT_LABELS[a] ? AGENT_LABELS[a] + " (" + a + ")" : a), hint: what };
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
    validate: { title: "Validate", says: "Reads the document for mistakes before anything runs.",
      choices: [{ value: "rules", half: "rules", label: "Built-in" },
                { value: "model", half: "model", label: "Built-in, then model review" }]
        .concat(agentChoices("Agent reviews the document")) },
    orchestrate: { title: "Search", says: "Decides what to try next. Standard: the model picks the next part, rules pick the kind of work.",
      choices: [{ value: "default", half: "model", label: "Standard" },
                { value: "rules", half: "rules", label: "Rules" },
                { value: "model", half: "model", label: "Model" },
                { value: "tools", half: "model", label: "Model with tools" }]
        .concat(agentChoices("Agent picks"))
        // D799: a search a script runs -- it answers each round with the next candidates
        .concat([{ value: "command", half: "rules", label: "Script" }]) },
    plan: { title: "Plan", says: "Optionally plans the round before work starts.",
      choices: [{ value: "off", half: "rules", label: "Off" },
                { value: "model", half: "model", label: "Model" }]
        .concat(agentChoices("Agent writes the plan")) },
    dse: { title: "Search policy", says: "Guides the experiments. With settings to search, an algorithm walks that space.",
      choices: [{ value: "none", half: "model", label: "Default (adaptive)" }]
        .concat(DSE_INTENTS.map(function (p) {
          return { value: p, half: "model", label: { adaptive: "Adaptive", explore: "Explore", improve: "Improve", tune: "Tune", finetune: "Finetune", variations: "Variations" }[p] };
        }))
        .concat(DSE_POLICIES.map(function (p) {
          return { value: p, half: "rules", label: { sweep: "Sweep", montecarlo: "Monte Carlo", anneal: "Annealing", gradient: "Gradient", genetic: "Genetic",
            pareto: "Pareto front" }[p] };
        }))
        .concat([{ value: "model", half: "model", label: "Model" }])
        .concat(agentChoices("Agent proposes settings")) },
    generate: { title: "Design", says: "Writes each candidate design.",
      choices: [{ value: "model", half: "model", label: "Model" },
                { value: "command", half: "rules", label: "Script" }]
        .concat(agentChoices("Agent writes it"))
        .concat([{ value: "catalog", half: "rules", label: "Existing designs" }]) },
    test: { title: "Check", says: "Runs the checks in order; a failing design goes back for repair.",
      fixed: "Configured in Check & Measure: its checks.",
      choices: [{ value: "gate", half: "fixed", label: "Checks (fixed)" }] },
    critique: { title: "Critic", says: "Optionally judges the split, each passing design and the pick: a rejected design fails, a rejected pick is never chosen.",
      choices: [{ value: "off", half: "off", label: "Off" },
                { value: "model", half: "model", label: "Model" }]
        .concat(agentChoices("Agent critic")) },
    measure: { title: "Measure", says: "Runs the measurements, cheapest first; a design that misses a cutoff is dropped.",
      fixed: "Configured in Check & Measure.",
      choices: [{ value: "stages", half: "fixed", label: "Measurements (fixed)" }] },
    calibrate: { title: "Calibrate", says: "Checks how well the cheap measurement predicts the costly one.",
      choices: [{ value: "on", half: "rules", label: "On" }, { value: "off", half: "off", label: "Off" }] },
    select: { title: "Choose", says: "Picks the winner by the goals.",
      choices: [{ value: "objectives", half: "rules", label: "Goals" }]
        .concat(agentChoices("Goals; agent breaks ties")) },
    feedback: { title: "Notes", says: "Notes you type while it runs steer the next round.",
      choices: [{ value: "human", half: "rules", label: "Notes" }, { value: "off", half: "off", label: "Off" }] },
    knowledge: { title: "Knowledge", says: "What the model reads with every request.",
      choices: [{ value: "default", half: "rules", label: "Library and listed files" },
                { value: "none", half: "off", label: "Off" }] },
    // D784, D791: who sums the papers up, once each, in each pass's Setup -- always, while the
    // library is on; not a flow key of its own: an agent is written as flow.knowledge.agent
    digest: { title: "Digest papers", says: "Sums up each library paper (library/ and the shared one) once, for every prompt.",
      choices: [{ value: "model", half: "model", label: "Model" }]
        .concat(agentChoices("Agent reads each paper, figures too")) },
    // D796: the record's lessons, written into flow.knowledge as `lessons:` -- not a flow key of its own
    lessons: { title: "Lessons", says: "Optionally turns past results into lessons for the next round.",
      choices: [{ value: "off", half: "off", label: "Off" },
                { value: "mined", half: "rules", label: "Mined" }]
        .concat(agentChoices("Agent writes lessons")) },
    records: { title: "Record", says: "Keeps every design, measurement and refusal; read back on resume.",
      fixed: "Always on.",
      choices: [{ value: "on", half: "fixed", label: "On (fixed)" }] },
  };

  var HALVES = { rules: "rules", model: "model", agent: "agent", fixed: "fixed", off: "off" };

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
      critique: { model: "a model critic on the split, each design the checks passed and the pick" },
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
    return !!(parameterSearch(state) && f.generate !== "command" && f.generate !== "catalog");
  }

  /** The design made as parts: listed, or split as the loop decides. */
  function hasParts(state) { return state.partsMode === "decompose" || (state.partsMode === "list" && list(state.parts).length > 0); }

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

  /** objective.py UNITS: the units Flux knows; any other known unit is written as `unit:`. */
  var UNITS = { fmax_mhz: "MHz", area_um2: "um2", area_mm2: "mm2", power_w: "W", power_mw: "mW", time_ms: "ms",
                latency_cycles: "cycles", energy_pj: "pJ", cell_count: "cells" };
  var MORE_UNITS = { storage_bytes: "B", path_ps: "ps" };

  /** A check's type, and the catalog tools that do it. `any`: offered whatever the language
      (a script of yours runs on any file); the others only for the languages they list. "Flux
      presets" (D968) are the catalog's `preset` entries -- lint, the golden test, ChampSim's build,
      and whatever presets Flux has -- chosen from a second list, so the page names none of them. */
  var CHECK_TYPES = [
    { key: "preset", title: "Flux presets", preset: true },
    { key: "test", title: "Test script", tools: ["python-test-script"], any: ["python-test-script"] },
    { key: "custom", title: "Custom", tools: ["custom-check"], any: ["custom-check"] },
  ];

  function isPreset(id, cat) { var t = toolOf(id, cat); return !!(t && t.preset); }

  /** The measuring tools the first list shows: the catalog's own stages, one "Flux presets" entry
      for all its presets (`preset`), custom last, as [id, label]. */
  function stageTools(cat) {
    var all = (cat || CATALOG).filter(function (t) { return t.role === "stage"; });
    var out = all.filter(function (t) { return !t.preset; }).map(function (t) { return [t.id, t.title]; });
    if (all.some(function (t) { return t.preset; })) out.push(["preset", "Flux presets"]);
    out.sort(function (a, b) { return (a[0] === "custom-stage") - (b[0] === "custom-stage"); });
    return out;
  }

  /** The presets a measurement may use for this language, as [id, title]. */
  function stagePresets(lang, cat) {
    return (cat || CATALOG).filter(function (t) { return t.role === "stage" && t.preset && fits(t, lang); })
      .map(function (t) { return [t.id, t.title]; });
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
    var ids = (cat || CATALOG).filter(function (t) {
      return t.role === "stage" && !isCustom(t.id) && lang && (t.languages || []).indexOf(lang) >= 0 && (used || []).indexOf(t.id) < 0;
    });
    ids.sort(function (a, b) { return (a.short === "stat") - (b.short === "stat"); });   // untimed after the timed
    return ids.length ? ids[0].id : "custom-stage";
  }

  function checkType(key) { for (var i = 0; i < CHECK_TYPES.length; i++) if (CHECK_TYPES[i].key === key) return CHECK_TYPES[i]; return null; }

  function fits(t, lang) { return !lang || !(t.languages || []).length || t.languages.indexOf(lang) >= 0; }

  /** The catalog tools a check of this type may use for this language. */
  function toolsFor(type, lang, cat) {
    var ty = checkType(type);
    if (!ty) return [];
    if (ty.preset) {
      return (cat || CATALOG).filter(function (t) { return t.role === "check" && t.preset && fits(t, lang); })
        .map(function (t) { return t.id; });
    }
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
    var t = toolOf(id, cat);
    var base = (t && t.short) || { "bench-script": "bench", "custom-stage": "measure" }[id] || "measure";
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

  /** The clock an RTL stage aims for when none is typed: an "at least N" on fmax_mhz, as ps. */
  function autoClock(state) {
    var lim = (state.objectives || []).filter(function (o) { return o.metric === "fmax_mhz" && o.label === "atleast"; })[0];
    return lim ? clockPs(lim.value) : null;
  }

  /** The numbers a measurement reports. */
  function reports(row, cat) {
    var t = toolOf(row.tool, cat);
    if (!t || isCustom(row.tool)) return list(row.metrics).concat((row.dictMetrics || []).map(function (m) { return String(m.name || "").trim(); }).filter(Boolean));
    return Object.keys(t.metrics || {});
  }

  function hasReport(stage, metric) {
    var parent = stage.metrics.find(function (m) { return m && m.type === "dict" && m.name === metric; });
    if (parent) return parent.aggregate !== "none";
    // D1028: any metric may print a dictionary -- `name.test` is one of its tests
    return stage.reports.indexOf(metric) >= 0 || stage.reports.some(function (n) {
      return n && metric.indexOf(n + ".") === 0 && metric.length > n.length + 1;
    });
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

  function dictionarySpec(state, metric) {
    for (var i = 0; i < (state.stages || []).length; i++) {
      var found = (state.stages[i].dictMetrics || []).find(function (m) {
        return metric === m.name || metric.indexOf(m.name + ".") === 0;
      });
      if (found) return found;
    }
    return null;
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
      var m = o.metric.trim(), dictionary = dictionarySpec(state, m), r;
      if (o.label === "atleast") r = { metric: m, direction: "maximize", goal: num(o.value) };
      else if (o.label === "atmost") r = { metric: m, direction: "minimize", goal: num(o.value) };
      else if (o.label === "min") r = { metric: m, direction: "minimize" };
      else if (o.label === "balance") r = { metric: m, direction: o.direction || (dictionary && dictionary.direction) || naturalDirection(m), balance: true };
      else r = { metric: m, direction: "maximize" };
      if (o.unit && o.unit !== UNITS[m]) r.unit = o.unit;            // a unit the document said (D686)
      else if (dictionary && dictionary.unit) r.unit = dictionary.unit;
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
      flow: defaultFlow(), generateCommand: "", generateCatalog: "", orchestrateCommand: "", orchestrateTimeout: "",
      baseline: { mode: "off", source: "project", file: "", command: "", timeout: "", metrics: [] },
      budget: { steps: "", passes: "", parallel: "", batch: "", repair_attempts: "", finalists: "", workers: "", prototype: "" },
      space: [], partsMode: "none", parts: "", params: [],
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

  /** Any value in YAML's flow style (nested values too). */
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
    var auto = { clock_ps: autoClock(state) };
    var stages = (state.stages || []).map(function (st, i) {
      var t = toolOf(st.tool, cat), custom = !t || isCustom(st.tool), cmd = fillRun(st, cat, auto), rep = reports(st, cat);
      var gates = (st.gates || []).map(gateOf).filter(Boolean);
      var needs = custom ? list(st.needs) : (t.needs || []).slice();
      return { name: String(st.name || "").trim() || "stage" + (i + 1), command: cmd, tool: st.tool, reports: rep,
               metrics: rep.map(function (name) {
                 var dictionary = (st.dictMetrics || []).find(function (m) { return m.name.trim() === name; });
                 if (!dictionary) return (st.metricSpecs || []).find(function (m) { return m.name === name; }) || name;
                 var metric = {name: name, type: "dict", direction: dictionary.direction || "minimize"};
                 if (dictionary.unit) metric.unit = dictionary.unit;
                 metric.aggregate = dictionary.aggregate || "mean";
                 return metric;
               }), needs: needs, gates: gates, estimate: estimateOf(st),
               clock_ps: t && t.params && "clock_ps" in t.params ? paramValue(t, "clock_ps", st.params.clock_ps, auto) : null,
               timeout: String(st.timeout || "").trim() };
    });
    return { checks: checks, stages: stages, objectives: objectives };
  }

  /** A box's value as the document holds it (D775: written into `flow` with its settings). */
  function flowObj(state, box) {
    var v = state.flow[box];
    var raw = state.agentRaw && state.agentRaw[box];
    if (raw && typeof v === "string" && v.indexOf("agent:") === 0) return raw;      // D728: as written
    if (typeof v === "string" && v.indexOf("agent:") === 0) return { agent: v.slice(6) };
    if (box === "generate" && v === "command") return { command: String(state.generateCommand || "").trim() };
    if (box === "generate" && v === "catalog") return { catalog: list(state.generateCatalog) };
    if (box === "orchestrate" && v === "command") {                                 // D799: a search a script runs
      var oc = { command: String(state.orchestrateCommand || "").trim() };
      if (String(state.orchestrateTimeout || "").trim()) oc.timeout_s = typed(state.orchestrateTimeout);
      return oc;
    }
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
    var loopPath = id.charAt(0) === "-" ? "./" + id : id;
    // D786: the document is the folder's problem.yaml; the folder's name is the id, not said here
    var out = "# " + id + "/problem.yaml: made with the Flux problem builder.\n" +
              "#     flux task check " + loopPath + "\n" +
              "#     flux task run " + loopPath + "            # until stopped; --passes N for N\n\n";
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
      if (basepass.source === "metrics") config.metrics = (basepass.metrics || []).map(function (m) {
        var row = { metric: m.metric, value: String(m.value == null ? "" : m.value).trim() ? Number(m.value) : "" };
        if (m.stage) row.stage = m.stage;
        return row;
      });
      if (basepass.mode === "only") config.only = true;
      out += "\nbaseline: " + (Object.keys(config).length ? inline(config, false) : "true") + "\n";
    }
    var chosenLanguage = language(state, true) || impliedLanguage(state, cat);
    if (chosenLanguage) out += "language: " + q(chosenLanguage) + "\n";

    if (!own("parts")) { /* kept */ }
    else if (state.partsMode === "decompose") out += "\nparts: decompose\n";
    else if (state.partsMode === "list" && list(state.parts).length) out += "\nparts: " + flowSeq(list(state.parts)) + "\n";


    if (r.objectives.length && own("objectives")) {
      out += "\nobjectives:                 # limits must hold; the rest decide, in order\n";
      r.objectives.forEach(function (o) {
        var p = [["metric", o.metric], ["direction", o.direction]];
        if (o.goal !== undefined) p.push(["goal", o.goal === null ? "?" : o.goal]);
        if (o.balance) p.push(["balance", true]);
        if (o.unit) p.push(["unit", o.unit]);
        var dict = dictionarySpec(state, o.metric);       // D1028: a dictionary's type and aggregate are its objective's
        if (dict && dict.name.trim() === o.metric && (dict.aggregate || "mean") !== "none")
          p.push(["type", "dict"], ["aggregate", dict.aggregate || "mean"]);
        out += "  - " + flowMap(p) + "\n";
      });
    }

    var b = state.budget || {}, bp = [];
    ["steps", "passes", "parallel", "batch", "repair_attempts", "workers", "exploration_quota"].concat(MORE_BUDGET).forEach(function (key) {
      var v = String(b[key] || "").trim();
      if (v !== "") bp.push([key, typed(v)]);
    });
    if (b.prototype) bp.push(["prototype", typed(b.prototype)]);
    if (bp.length && own("budget")) out += "\nbudget: " + flowMap(bp) + "\n";

    // the document's own parameters: a command reads them from the file {params} names (D799)
    var pr = (state.params || []).filter(function (x) { return String(x.key || "").trim(); });
    if (pr.length && own("params")) out += "\nparams: " + flowMap(pr.map(function (x) { var c = choicesOf(x.value); return [x.key.trim(), c.length === 1 ? c[0] : String(x.value || "").trim()]; })) + "\n";

    // D775: the flow, last -- who works each box, and each box's own settings beside it
    var F = [], kf = state.keptFlow || {}, said = flowSaid(state), boxesKept = !own("flow.boxes");
    var kb = boxesKept ? (kf["flow.boxes"] || {}) : null, SPECIAL = ["dse", "knowledge", "select", "critique"];
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
      if (dv && typeof dv === "object" && !Array.isArray(dv) && dv.policy === "model" && Object.keys(dv).length > 1) {   // D1135: a model's options
        dv = Object.assign({ by: "model" }, dv); delete dv.policy;
      }
      if (dv && typeof dv === "object" && !Array.isArray(dv)) Object.keys(dv).forEach(function (k) { F.push("    " + q(k) + ": " + inline(dv[k], false)); });
      else if (dv !== undefined) F.push("    policy: " + inline(dv, false));
      F = F.concat(D);
    } else if (dv !== undefined && !(state.flow.orchestrate === "command" && !boxesKept)) {   // a search command: no policy beside it
      F = F.filter(function (line) { return !/^  orchestrate:/.test(line); });
      if (typeof dv === "string" && DSE_POLICIES.concat(DSE_INTENTS).indexOf(dv) >= 0) {
        var who = toSurface("orchestrate", flowObj(state, "orchestrate")) || "model";
        var intent = typeof who === "string" ? { by: who === "default" ? "model" : who, dse: dv } : Object.assign({}, who, { dse: dv });
        F.push("  orchestrate: " + inline(intent, false));
      } else F.push("  orchestrate: " + inline(dv, false));
    }

    // D1022: the scope a pass works on, kept as written, under the orchestrator that picks it
    if (kf["flow.orchestrate.scope"] !== undefined) {
      var oi = -1;
      F.forEach(function (line, i) { if (/^  orchestrate: /.test(line)) oi = i; });
      var ov = boxesKept ? kb.orchestrate : toSurface("orchestrate", boxVal("orchestrate"));
      var scoped = ov && typeof ov === "object" && !Array.isArray(ov) ? Object.assign({}, ov) : ov === undefined || ov === "default" ? {} : { by: ov };
      scoped.scope = kf["flow.orchestrate.scope"];
      if (oi >= 0) F[oi] = "  orchestrate: " + inline(scoped, false); else F.push("  orchestrate: " + inline(scoped, false));
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
        L.push("command: " + q(st.command || "(the command)"));
        if (st.metrics.length) L.push("metrics: " + inline(st.metrics.map(function (x) { return typeof x === "string" ? x : x.name; }), false));   // D1028: names
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
    else if (boxesKept && kb.knowledge !== undefined) F.push("  knowledge: " + inline(kb.knowledge, false));   // D1135: kept with the flow
    else {
      var kv = boxVal("knowledge"), K = {}, kfiles = list(state.knowledgeFiles);
      if (kfiles.length) K.files = kfiles;
      var dg = (state.flow || {}).digest;                  // D784, D791: the Digest box -- the model unless an agent
      if (typeof dg === "string" && dg.indexOf("agent:") === 0) K.digest = dg.slice(6);      // D830: by name, as every box
      var ls = (state.flow || {}).lessons, lr = (state.agentRaw || {}).lessons;   // D796: the Learn box
      var lrName = lr && lr.agent && typeof lr.agent === "object" ? lr.agent.preset : null;
      if (ls && ls !== "off") K.lessons = lr && (ls === INLINE_AGENT || (lrName && ls === "agent:" + lrName)) ? toSurface("lessons", lr)   // D1135: its own settings kept
        : typeof ls === "string" && ls.indexOf("agent:") === 0 ? ls.slice(6) : ls;
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

    // D1040: a critic is said on each box it judges -- the Second opinion on the split (orchestrate),
    // each design the checks passed (test) and the pick (select)
    if (boxesKept) {
      var cv = boxVal("critique");
      if (cv !== undefined && cv !== "off" && cv !== "none")
        (state.criticOn || DEFAULT_CRITICS).forEach(function (bx) { F = withCritic(F, bx, inline(cv, false)); });
    } else {
      var cs = criticsOf(state);                         // each box its own critic (or none)
      CRITIC_BOXES.forEach(function (bx) {
        var v = cs[bx];
        if (v === "off") return;
        var cr = (state.criticRaw || {})[bx];                // D1135: a critic agent's own settings, kept while it is chosen
        var val = v === INLINE_AGENT ? (state.agentRaw || {}).critique
          : cr && v === "agent:" + cr.agent.preset ? cr
          : typeof v === "string" && v.indexOf("agent:") === 0 ? { agent: v.slice(6) } : v;
        F = withCritic(F, bx, inline(toSurface("critique", val), false));
      });
    }

    if (F.length) out += "\nflow:\n" + F.join("\n") + "\n";
    return out;
  }

  /** D1040, D1119: the boxes a critic may judge (surface.py CRITIC_BOXES), and those a lone `critique` judges. */
  var CRITIC_BOXES = ["orchestrate", "test", "measure", "select"];
  var DEFAULT_CRITICS = ["orchestrate", "test", "select"];
  /** Each box's critic, a choice of `critique` ("off", "model", "agent:x"): the state's own, else the
      one critic (`flow.critique`) on the boxes it judges (`criticOn`, by default three). */
  function criticsOf(state) {
    var out = {}, flow = (state && state.flow) || {}, c = flow.critique, on = (state && state.criticOn) || DEFAULT_CRITICS;
    CRITIC_BOXES.forEach(function (b) {
      var v = state && state.critics && state.critics[b] !== undefined ? state.critics[b] : on.indexOf(b) >= 0 ? c : "off";
      out[b] = v === undefined || v === null || v === "none" || v === "" ? "off" : v;
    });
    return out;
  }
  /** One box's critic set; `flow.critique` stays the critic the boxes share (the first one on). */
  function setCritic(state, box, value) {
    var all = criticsOf(state);
    all[box] = value || "off";
    state.critics = all;
    state.criticOn = CRITIC_BOXES.filter(function (b) { return all[b] !== "off"; });
    var on = state.criticOn.map(function (b) { return all[b]; });
    state.flow.critique = value && value !== "off" ? value : on[0] || "off";
    if (state.agentRaw && on.indexOf(INLINE_AGENT) < 0) delete state.agentRaw.critique;   // nobody keeps its own settings
    return all;
  }

  /** D1040: the flow's lines with `critic:` under `box` -- beside a block's entries, inside an inline
      map, a word made `{by: word, critic: ...}`; a box not said is said for it (but a test without checks). */
  function withCritic(F, box, crit) {
    var head = "  " + box + ":", at = -1;
    F.forEach(function (line, i) { if (line === head || line.indexOf(head + " ") === 0) at = i; });
    if (at < 0) return box === "test" ? F : F.concat(["  " + box + ": {critic: " + crit + "}"]);
    var rest = F[at].slice(head.length).trim(), out = F.slice();
    if (!rest || rest.charAt(0) === "#") out.splice(at + 1, 0, "    critic: " + crit);
    else if (rest.charAt(0) === "{") out[at] = head + " {critic: " + crit + (rest === "{}" ? "}" : ", " + rest.slice(1));
    else out[at] = head + " {by: " + rest + ", critic: " + crit + "}";
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
    r.stages.forEach(function (st) { cmd(st.command); if (st.estimate && st.estimate.command) cmd(st.estimate.command); });
    if ((state.flow || {}).generate === "command") cmd(state.generateCommand);
    if ((state.flow || {}).generate === "catalog") list(state.generateCatalog).forEach(function (f) { add(f.replace(/^\{home\}\//, "")); });
    if ((state.flow || {}).orchestrate === "command") cmd(state.orchestrateCommand);
    var bp = state.baseline || {};
    if (bp.mode && bp.mode !== "off") {
      if (bp.source === "command") cmd(bp.command);
      if (bp.source === "file") add(String(bp.file || "").trim().replace(/^\{home\}\//, ""));
    }
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
    if (!id) error("Give the problem a name (letters, digits, - and _).", "id");
    else if (!/^[A-Za-z0-9_-]{1,60}$/.test(id)) error("The name \"" + id + "\" should use letters, digits, - and _ (1–60 characters).", "id");
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
        error("Check \"" + nm + "\": there is no " + (ty && ty.preset ? "Flux preset" : (ty ? ty.title.toLowerCase() : "such") + " tool") + " for " + (lang || "this language") + "; use Custom.");
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
      var metricNames = {};
      rs.reports.forEach(function (name) {
        if (metricNames[name]) error("Measurement \"" + nm + "\": metric " + name + " is repeated.");
        metricNames[name] = true;
      });
      (st.dictMetrics || []).forEach(function (m) { if (!String(m.name || "").trim()) error("Measurement \"" + nm + "\": a dictionary needs a name."); });
      (st.gates || []).forEach(function (g) {
        var m = String(g.metric || "").trim();
        if (!m) { error("Measurement \"" + nm + "\": a gate needs the number it looks at."); return; }
        if (!hasReport(rs, m)) error("Measurement \"" + nm + "\" does not report " + m + ", so its gate cannot use it.");
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
      if (r.stages.some(function (st) { return st.metrics.some(function (x) { return x && x.type === "dict" && x.name === m && x.aggregate === "none"; }); }))
        error("The objective uses dictionary " + m + ": choose a named submetric, such as " + m + ".test, or configure a parent aggregate.");
      var by = r.stages.filter(function (st) { return hasReport(st, m); }).length;
      if (!by) error("The objective uses " + m + ", which no measurement reports.");
      else if (by < r.stages.length) {
        var missing = r.stages.filter(function (st) { return !hasReport(st, m); }).map(function (st) { return st.name; });
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
      if (bp.source === "metrics") {
        var baselineSeen = {};
        if (!(bp.metrics || []).length) error("Add at least one baseline metric and value.", "baseline-metrics");
        (bp.metrics || []).forEach(function (m) {
          var st = m.stage || (r.stages.length ? r.stages[r.stages.length - 1].name : "");
          var named = r.stages.find(function (s) { return s.name === st; });
          if (!named) error("Baseline metric " + m.metric + " needs an existing measurement stage.", "baseline-metrics");
          if (!String(m.metric || "").trim() || !r.stages.some(function (s) { return hasReport(s, m.metric); })) error("Choose a reported baseline metric.", "baseline-metrics");
          if (named && named.metrics && named.metrics.length && !hasReport(named, m.metric)) error("Baseline metric " + m.metric + " is not reported by " + st + ".", "baseline-metrics");
          if (!String(m.value == null ? "" : m.value).trim() || !isFinite(Number(m.value))) error("Baseline metric " + m.metric + " needs a finite numeric value.", "baseline-metrics");
          var key = JSON.stringify([st, m.metric]);
          if (baselineSeen[key]) error("Baseline metric " + m.metric + " is repeated on " + st + ".", "baseline-metrics");
          baselineSeen[key] = true;
        });
      }
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
    if (searching && flow.orchestrate && flow.orchestrate !== "default") warn("With a search, the search picks the next job; the Search box's choice is left out.");
    if (flow.generate === "command" && !String(state.generateCommand || "").trim()) error("Say the command that writes each design.");
    if (flow.generate === "catalog" && !list(state.generateCatalog).length) error("Name the designs you already have (their files, separated by commas).");
    if (flow.orchestrate === "command") {
      if (!String(state.orchestrateCommand || "").trim()) error("Say the search command that picks the next designs.");
      if (state.orchestrateTimeout && !(num(state.orchestrateTimeout) > 0)) error("The search command's time limit should be a number of seconds.");
      if (searching) error("A search command and a search over settings both pick the next designs: keep one (Graph › Search, or Extra › Settings to search).");
    }
    var crits = criticsOf(state);
    CRITIC_BOXES.forEach(function (b) {
      var v = crits[b];
      if (v !== "off" && v !== INLINE_AGENT && !choiceOf("critique", v)) error("The critic on \"" + (BOXES[b] || {}).title + "\" cannot be \"" + v + "\".");
    });

    var cmds = r.checks.map(function (c) { return ["check \"" + c.name + "\"", c.run, STEP_OF.checks]; });
    r.stages.forEach(function (st) {
      cmds.push(["measurement \"" + st.name + "\"", st.command, STEP_OF.measurements]);
      if (st.estimate && st.estimate.command) cmds.push(["the estimate of \"" + st.name + "\"", st.estimate.command, STEP_OF.measurements]);
    });
    if (flow.generate === "command") cmds.push(["the design script", state.generateCommand, STEP_OF.flow]);
    if (flow.orchestrate === "command") cmds.push(["the search command", state.orchestrateCommand, STEP_OF.flow]);
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
    if (searching && knobs.length && flow.generate !== "command" && flow.generate !== "catalog") {
      if (!knobsSaid) error("A search's settings become designs only through a script: in Graph › Design choose Script and say {" +
                            knobs[0] + "} in it, or use {" + knobs[0] + "} in a check or a measurement.");
      else note("A parameter-only search: each point's settings reach your commands as {" + knobs[0] + "}; no design is written, Design does not run.");
    }

    at = STEP_OF.more;
    ["steps", "passes", "parallel", "batch", "repair_attempts", "finalists", "workers",
     "explore_every", "explore_after", "cooldown_after", "regress_after", "agent_continues"].forEach(function (key) {
      var v = String((state.budget || {})[key] || "").trim();
      if (v && !/^\d+$/.test(v)) error("Budget \"" + key + "\" should be a whole number.");
    });
    ["budget_s", "agent_grace_s"].forEach(function (key) {
      var v = String((state.budget || {})[key] || "").trim();
      if (v && !(num(v) >= 0)) error("Budget \"" + key + "\" should be a number of seconds.");
    });
    var pt = String((state.budget || {}).patching || "").trim();
    if (pt && pt !== "true" && pt !== "false") error("Budget \"patching\" is true (edits) or false (rewrites).");
    var pseen = {};
    (state.params || []).forEach(function (x) {
      var k = String(x.key || "").trim();
      if (!k) return;
      if (!/^[A-Za-z_][\w-]*$/.test(k)) error("The parameter \"" + k + "\" should be a letter, then letters, digits, _ or -.");
      if (pseen[k]) error("The parameter \"" + k + "\" is named twice.");
      pseen[k] = 1;
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
  var PRESET_ARGV = ["{python}", "-W", "ignore", "-m", "flux_presets"];
  var STATE_KEYS = ["id", "statement", "contract", "language", "knowledge", "parts", "space", "flow", "gate", "stages",
                    "objectives", "budget", "baseline", "params"];
  /** The loop's knobs the configurator's Graph sets beside each box (LoopRequest): when a design explores,
      how a part yields, how many worse edits are tolerated, an agent's turns, how a repair edits. */
  var MORE_BUDGET = ["explore_every", "explore_after", "cooldown_after", "regress_after", "agent_continues", "agent_grace_s", "budget_s", "patching"];
  var BUDGET_KEYS = ["steps", "passes", "parallel", "batch", "repair_attempts", "finalists", "workers", "prototype", "exploration_quota"].concat(MORE_BUDGET);

  function argvOf(run) {
    var a = Array.isArray(run) ? run.map(String) : String(run || "").trim().split(/\s+/).filter(Boolean);
    var flux = FLUX_ARGV.every(function (t, i) { return a[i] === t; });
    if (flux) return ["flux"].concat(a.slice(FLUX_ARGV.length));
    // D1121: a preset as the loader spells it (commands.py) is the catalog's `fluxpreset ...` again
    var preset = PRESET_ARGV.every(function (t, i) { return a[i] === t; });
    return preset ? ["fluxpreset"].concat(a.slice(PRESET_ARGV.length)) : a;
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

  function checkTypeOf(toolId, cat) {
    if (isPreset(toolId, cat)) return "preset";
    if (toolId === "python-test-script") return "test";
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
    var f = {}, critic;
    for (k in fl) {
      var key = k === "orchestrate" && isSearch(fl[k]) ? "dse" : k;            // D797: a search is the orchestrator's
      var val = fl[k];
      if (val && typeof val === "object" && !Array.isArray(val) && "critic" in val) {   // D1040: the Second opinion
        if (critic === undefined) critic = val.critic;
        (out._criticOn = out._criticOn || []).push(k);
        (out._critics = out._critics || {})[k] = toInner("critique", val.critic);
        val = Object.assign({}, val); delete val.critic;
        if (!Object.keys(val).length) continue;
        if (Object.keys(val).length === 1 && "by" in val && k !== "test" && k !== "measure") val = val.by;
      }
      if (k === "orchestrate" && val && typeof val === "object" && "dse" in val) {
        f.dse = val.dse; val = Object.assign({}, val); delete val.dse;
      }
      if (k === "orchestrate" && val && typeof val === "object" && !Array.isArray(val) && "scope" in val) {   // D1022
        out.scope = val.scope; val = Object.assign({}, val); delete val.scope;
        if (!Object.keys(val).length) continue;
      }
      if (k === "knowledge" && val && typeof val === "object" && !Array.isArray(val) && "digest" in val) {   // D830
        val = Object.assign({}, val);
        var dg2 = val.digest; delete val.digest;
        if (dg2 && typeof dg2 === "object") Object.assign(val, dg2); else if (dg2 && dg2 !== "model") val.by = dg2;
      }
      f[key] = ["test", "measure"].indexOf(k) >= 0 ? val : toInner(key, val);   // D795
    }
    if (critic !== undefined) f.critique = toInner("critique", critic);
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
      if ("policy" in d) {                                // D1135: a policy with options keeps them (the flow then kept as written)
        var pol = d.policy; delete d.policy;
        f.dse = Object.keys(d).length ? Object.assign({ policy: pol }, d) : pol;
      } else if (Object.keys(d).length) f.dse = d; else delete f.dse;
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
                  knowledge: "flow.knowledge", flow: "flow.boxes", scope: "flow.orchestrate.scope" };

  /** A kept flow part's value, from the document as written (this layout). */
  function keptValue(doc, at) {
    var fl = (doc && doc.flow) || {}, out, k;
    if (at === "flow.test") return fl.test;
    if (at === "flow.measure") return fl.measure;
    if (at === "flow.knowledge") return fl.knowledge;
    var search = isSearch(fl.orchestrate) ? fl.orchestrate : null;      // D797: the search is orchestrate's
    if (at === "flow.dse.space") return search && search.space;
    if (at === "flow.dse.seeds") return search && search.seeds;
    if (at === "flow.orchestrate.scope") return fl.orchestrate && typeof fl.orchestrate === "object" ? fl.orchestrate.scope : undefined;
    if (at === "flow.boxes") {                      // every box's choice, without what is kept or edited apart
      out = {};
      for (k in fl) {
        var v = fl[k];
        if (v && typeof v === "object" && !Array.isArray(v) && "critic" in v) {   // D1040: the Second opinion, apart
          if (out.critique === undefined) out.critique = v.critic;
          v = Object.assign({}, v); delete v.critic;
          if (!Object.keys(v).length) continue;
          if (Object.keys(v).length === 1 && "by" in v) v = v.by;
        }
        if (["test", "measure"].indexOf(k) >= 0) continue;
        if (k === "orchestrate" && isSearch(v)) k = "dse";
        if (k === "orchestrate" && v && typeof v === "object" && !Array.isArray(v) && "scope" in v) {   // kept apart (D1022)
          v = Object.assign({}, v); delete v.scope;
          if (!Object.keys(v).length) continue;
        }
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
    if (raw._criticOn) s.criticOn = raw._criticOn;          // D1040: the boxes the document's critic judges
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
             Object.keys(bp).every(function (k) { return ["file", "command", "metrics", "only", "timeout_s"].indexOf(k) >= 0; })) {
      s.baseline.mode = bp.only ? "only" : "before";
      if (bp.file !== undefined) { s.baseline.source = "file"; s.baseline.file = String(bp.file); }
      if (bp.command !== undefined) { s.baseline.source = "command"; s.baseline.command = argvOf(bp.command).map(shellWord).join(" "); }
      if (bp.timeout_s !== undefined) s.baseline.timeout = String(bp.timeout_s);
      if (Array.isArray(bp.metrics)) {
        s.baseline.source = "metrics";
        s.baseline.metrics = bp.metrics.map(function (m) { return { metric: m.metric, value: String(m.value), stage: m.stage === "deepest" || m.stage === "last" ? "" : m.stage || "" }; });
      }
    } else if (bp !== undefined && bp !== null && bp !== false) keep("baseline", "custom baseline settings");
    var lang = String(raw.language || normal.language || "");
    if (lang && LANGUAGES.indexOf(lang.toLowerCase()) >= 0) s.language = lang.toLowerCase();
    else if (lang) { s.language = "other"; s.languageOther = lang; }

    // knowledge: the files the model reads (the papers are library/'s, D791)
    var kn = raw.knowledge;
    if (kn && typeof kn === "object" && !Array.isArray(kn) && Object.keys(kn).every(function (k) { return k === "files"; })) {
      if (kn.files) s.knowledgeFiles = (Array.isArray(kn.files) ? kn.files : [kn.files]).join(", ");
    } else if (kn) keep("knowledge", "a methods sheet or inline notes, which the configurator does not edit");
    keep("scope", "the scope each pass works on (`orchestrate.scope`: its list, brief and check), which the configurator does not edit");

    // params: plain values, each its type kept
    var pm = raw.params;
    if (pm && typeof pm === "object" && !Array.isArray(pm) && Object.keys(pm).every(function (k) { return ["string", "number", "boolean"].indexOf(typeof pm[k]) >= 0; }))
      s.params = Object.keys(pm).map(function (k) { return { key: k, value: choiceText(pm[k]) }; });
    else if (pm !== undefined && pm !== null) keep("params", "parameters that are not plain values");

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
      if (box === "generate" && v && typeof v === "object" && Array.isArray(v.catalog) && v.catalog.every(function (x) { return typeof x === "string"; })) {
        s.flow.generate = "catalog"; s.generateCatalog = v.catalog.join(", "); return;
      }
      if (box === "orchestrate" && v && typeof v === "object" && v.command !== undefined) {      // D799: a search command
        var oc = v.command && typeof v.command === "object" && !Array.isArray(v.command) ? v.command : { run: v.command, timeout_s: v.timeout_s };
        var extra = Object.keys(oc).filter(function (k) { return k !== "run" && k !== "timeout_s"; });
        if (!extra.length && oc.run) {
          s.flow.orchestrate = "command"; s.orchestrateCommand = argvOf(oc.run).map(shellWord).join(" ");
          if (oc.timeout_s !== undefined && oc.timeout_s !== null) s.orchestrateTimeout = String(oc.timeout_s);
          return;
        }
      }
      flowOk = false;
    });
    if (!flowOk) { s.flow = defaultFlow(); keep("flow", "some of its choices are not the configurator's (an agent's own settings, a catalog, knowledge sources)"); }
    else if (raw._critics) {                     // D1040: each box's critic, as the configurator holds it
      s.critics = {};
      CRITIC_BOXES.forEach(function (b) {
        var c = raw._critics[b];
        if (c === undefined || c === null || c === false || c === "off" || c === "none") s.critics[b] = "off";
        else if (typeof c === "string" && choiceOf("critique", c)) s.critics[b] = c;
        else if (c && typeof c === "object" && typeof c.agent === "string" && choiceOf("critique", "agent:" + c.agent)) s.critics[b] = "agent:" + c.agent;
        else if (c && typeof c === "object" && c.agent && typeof c.agent === "object" && typeof c.agent.preset === "string"
                 && choiceOf("critique", "agent:" + c.agent.preset)) {          // D1135: an agent with its own settings
          s.critics[b] = "agent:" + c.agent.preset; (s.criticRaw = s.criticRaw || {})[b] = c;
        }
        else s.critics[b] = s.flow.critique;
      });
    }

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
                       : { type: checkTypeOf(m.tool.id, cat), tool: m.tool.id, name: c.name, params: paramsOf(m.tool, m.params),
                           count_re: "", timeout: "" };
      if (c.timeout_s && Number(c.timeout_s) !== CHECK_TIMEOUT) row.timeout = String(c.timeout_s);
      s.checks.push(row);
    });
    if (!gateOk) { s.checks = []; keep("gate", "a failure pattern (`fail_re`)"); }

    // D1028: a dictionary is said on the objective ranking it -- the stage's dictionary row, as the form keeps it
    var dictObj = {};
    (normal.objectives || []).forEach(function (o) {
      if (o && o.type === "dict") dictObj[o.metric] = { name: o.metric, direction: o.direction || "minimize", unit: o.unit || "", aggregate: o.aggregate || "mean" };
    });
    // stages: a catalog tool or a command of one's own
    var stagesOk = true;
    var rawStages = {};                          // what the document wrote: the loader adds patterns of its own
    (Array.isArray(raw.stages) ? raw.stages : []).forEach(function (x) { if (x && x.name) rawStages[x.name] = x; });
    (normal.stages || []).forEach(function (st) {
      var said = rawStages[st.name] || {};
      if (said.metrics_re && Object.keys(said.metrics_re).length) { stagesOk = false; return; }
      var row = null;
      if (st.command) {
        var argv = argvOf(st.command), m = matchTool(argv, "stage", cat);
        // A template must preserve exactly the declared metrics, including their order.
        // A subset or an extended list is an ordinary custom stage, not the catalog default.
        if (m && JSON.stringify(st.metrics || []) !== JSON.stringify(Object.keys(m.tool.metrics || {}))) m = null;
        // D910: likewise a `needs` of its own: matched to the tool, the requirement was dropped
        if (m && said.needs !== undefined) {
          var own = m.tool.needs || [];
          var saidNeeds = Array.isArray(said.needs) ? said.needs : [said.needs];
          if (JSON.stringify(saidNeeds) !== JSON.stringify(own)) m = null;
        }
        if (m) row = { tool: m.tool.id, name: st.name, params: paramsOf(m.tool, m.params), metrics: "", needs: "", gates: [] };
        else row = { tool: "custom-stage", name: st.name, params: { command: argv.map(shellWord).join(" ") },
                     metrics: (st.metrics || []).filter(function (m) { return typeof m === "string" ? !dictObj[m] : m.type !== "dict"; }).map(function (m) { return typeof m === "string" ? m : m.name; }).join(", "),
                     dictMetrics: (st.metrics || []).filter(function (m) { return typeof m === "string" ? !!dictObj[m] : m && m.type === "dict"; })
                       .map(function (m) { return Object.assign({}, typeof m === "string" ? dictObj[m] : m); }),
                     metricSpecs: (st.metrics || []).filter(function (m) { return typeof m === "object" && m.type !== "dict"; }),
                     needs: (st.needs || []).join(", "), gates: [] };
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

    // objectives: the labels the configurator has
    var objOk = true, rawObjs = Array.isArray(raw.objectives) ? raw.objectives : [];
    (normal.objectives || []).forEach(function (o, i) {
      // what the document wrote decides; the loader fills stage, tie, margin and unit of its own
      var said = rawObjs[i] && typeof rawObjs[i] === "object" ? rawObjs[i] : {};
      var extra = Object.keys(said).filter(function (k) { return ["metric", "direction", "goal", "balance", "unit", "type", "aggregate"].indexOf(k) < 0; });
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
      if (STATE_KEYS.indexOf(k) < 0 && k.charAt(0) !== "_") keep(k, k === "seeds" ? "the search's starting points" : "the configurator does not edit it");
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
              CHECK_TYPES: CHECK_TYPES, stageTools: stageTools, stagePresets: stagePresets, nextStageTool: nextStageTool, abbreviate: abbreviate, autoClock: autoClock, LABELS: LABELS,
              BOXES: BOXES, FLOW_BOXES: FLOW_BOXES, DELEGABLE: DELEGABLE, NEVER: NEVER, LANGUAGES: LANGUAGES,
              AGENTS: AGENTS, DSE_POLICIES: DSE_POLICIES, DSE_GROUPS: DSE_GROUPS, dseGroup: dseGroup, dseChoices: dseChoices, dseHint: dseHint,
              halfOf: halfOf, defaultFlow: defaultFlow, base: base, isFixed: isFixed,
              explain: explain, explainEstimate: explainEstimate, choicesOf: choicesOf, choiceText: choiceText, shellSplit: shellSplit,
              namedFiles: namedFiles, BUILTIN_SUBS: BUILTIN_SUBS, STEP_OF: STEP_OF, STEP_IDS: STEP_IDS, stepIndex: stepIndex,
              // the views' own helpers (crafter.js, the web app's configure/)
              catalog: function () { return CATALOG; }, HALVES: HALVES, INLINE_AGENT: INLINE_AGENT, AGENT_LABELS: function () { return AGENT_LABELS; },
              language: language, impliedLanguage: impliedLanguage, checkType: checkType, isPreset: isPreset, isCustom: isCustom, fits: fits,
              uniqueName: uniqueName, unitFor: unitFor, dictionarySpec: dictionarySpec, paramOnly: paramOnly, parameterSearch: parameterSearch,
              knobNames: knobNames, choiceOf: choiceOf, list: list, num: num, shown: shown, paramsOf: paramsOf, hasParts: hasParts,
              CRITIC_BOXES: CRITIC_BOXES, criticsOf: criticsOf, setCritic: setCritic, BUDGET_KEYS: BUDGET_KEYS };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.FluxCrafterModel = api;
})(typeof window !== "undefined" ? window : this);
