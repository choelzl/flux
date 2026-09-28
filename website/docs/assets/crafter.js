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
  /** document.py RTL_METRICS: what `flux rtl measure` prints. */
  var RTL_METRICS = ["fmax_mhz", "area_um2", "power_w", "cell_count"];
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

  // ------------------------------------------------------------------ the presets
  function base() {
    return {
      preset: "", id: "", statement: "", contract: "", language: "systemverilog", languageOther: "",
      knowledgeFiles: "",
      gate: { kind: "golden", golden: "golden.py", command: "", timeout: "" },
      stages: [], objectives: [], flow: defaultFlow(), generateCommand: "",
      budget: { steps: "", passes: "", repair_attempts: "", finalists: "", workers: "", prototype: "" },
      space: [], partsMode: "none", parts: "",
    };
  }

  function stage(kind, name, extra) {
    var s = { kind: kind, name: name, clock_ps: "", command: "", metrics: "", needs: "" };
    for (var k in extra || {}) s[k] = extra[k];
    return s;
  }

  function objective(metric, direction, extra) {
    var o = { metric: metric, direction: direction, target: "none", goal: "", keep: "", above: "", unit: "" };
    for (var k in extra || {}) o[k] = extra[k];
    return o;
  }

  var PRESETS = [
    { key: "rtl", title: "An RTL module checked against a Python golden model",
      blurb: "A model writes the hardware; golden.py says what it must compute.",
      files: "golden.py", make: function () {
        var s = base();
        s.id = "adder8";
        s.statement = "An unsigned 8-bit adder with carry out in SystemVerilog: module `adder8`, inputs `a` and " +
          "`b` (8 bits), output `s` (9 bits), the smallest that makes 2000 MHz placed on ASAP7.";
        s.contract = "One module named exactly `adder8`, purely combinational, ports `input logic [7:0] a, b` " +
          "and `output logic [8:0] s`. s must equal a + b for every input.";
        s.stages = [stage("synth", "screen", { clock_ps: "500" }), stage("place", "confirm", { clock_ps: "500" })];
        s.objectives = [objective("fmax_mhz", "maximize", { target: "goal", goal: "2000" }),
                        objective("area_um2", "minimize")];
        s.budget.steps = "3"; s.budget.repair_attempts = "6"; s.budget.prototype = "false"; s.budget.finalists = "2";
        return s;
      } },
    { key: "rtl-sweep", title: "Sweep the variants a generator script writes",
      blurb: "No model: your script writes one design per combination of settings.",
      files: "gen.py, golden.py", make: function () {
        var s = base();
        s.id = "popcount16";
        s.statement = "A 16-bit population count (module `popcount16`, input `a`, 5-bit output `y`): the smallest " +
          "that makes 3000 MHz on the synthesis screen, among the architectures gen.py spells.";
        s.space = [{ knob: "arch", choices: "behavioral, tree, lut" }, { knob: "chunk", choices: "2, 4" }];
        s.flow.dse = "sweep"; s.flow.generate = "command";
        s.generateCommand = "{python} {home}/gen.py {artifact} {arch} {chunk}";
        s.stages = [stage("synth", "screen", { clock_ps: "333" })];
        s.objectives = [objective("fmax_mhz", "maximize", { target: "goal", goal: "3000" }),
                        objective("area_um2", "minimize")];
        s.budget.steps = "1";
        return s;
      } },
    { key: "tune", title: "Tune a program's settings",
      blurb: "Try settings of an existing program and keep the fastest.",
      files: "check.py, bench.py, workload.py", make: function () {
        var s = base();
        s.id = "matmul_tune"; s.language = "text";
        s.statement = "The fastest block size and loop order for the blocked matrix multiply in workload.py.";
        s.space = [{ knob: "block", choices: "16, 32, 64, 128, 256" }, { knob: "order", choices: "ijk, ikj, jki" }];
        s.flow.dse = "sweep";
        s.gate = { kind: "command", golden: "golden.py", command: "{python} {home}/check.py {block} {order}", timeout: "60" };
        s.stages = [stage("command", "bench", { command: "{python} {home}/bench.py {block} {order}", metrics: "time_ms" })];
        s.objectives = [objective("time_ms", "minimize")];
        s.budget.steps = "1"; s.budget.workers = "1";
        return s;
      } },
    { key: "python", title: "A Python function, fastest wins",
      blurb: "A model writes the function; check.py proves it, bench.py times it.",
      files: "check.py, bench.py", make: function () {
        var s = base();
        s.id = "count_primes"; s.language = "python";
        s.statement = "A Python function `count_primes(n: int) -> int` that returns how many primes are below n, " +
          "as fast as possible for n up to 2,000,000.";
        s.contract = "One module that defines `count_primes(n)`. Standard library only; no table of precomputed " +
          "answers. It returns the exact count for every n >= 0.";
        s.gate = { kind: "command", golden: "golden.py", command: "{python} {home}/check.py {artifact}", timeout: "60" };
        s.stages = [stage("command", "bench", { command: "{python} {home}/bench.py {artifact}", metrics: "time_ms" })];
        s.objectives = [objective("time_ms", "minimize")];
        s.budget.steps = "3"; s.budget.repair_attempts = "4";
        return s;
      } },
    { key: "config", title: "Let a model write a config file",
      blurb: "A model writes a settings file; your simulator scores it.",
      files: "bingo.py, knobs.md, bingo_default.ini", make: function () {
        var s = base();
        s.id = "prefetcher"; s.language = "other"; s.languageOther = "ini";
        s.statement = "The L2 prefetcher configuration for 5G baseband workloads (FFT-heavy signal processing, " +
          "channel-estimation matrix work, bursty packet buffers): Bingo, the partners that run beside it and " +
          "their knobs. The fastest configuration by geomean IPC speedup over no prefetcher, then the smallest " +
          "that holds 90% of that gain.";
        s.contract = "One ChampSim knob file, `name = value` per line, starting with `l2c_prefetcher_types = bingo` " +
          "plus any partners. Only the knobs and values knobs.md allows; a knob left out takes its shipped value.";
        s.knowledgeFiles = "knobs.md, bingo_default.ini";
        s.gate = { kind: "command", golden: "golden.py", command: "{python} {home}/bingo.py check {artifact}", timeout: "" };
        s.stages = [
          stage("command", "screen", { command: "{python} {home}/bingo.py measure {artifact} --traces {home}/traces --warmup 10000000 --sim 15000000",
                                       metrics: "geomean_speedup, storage_bytes", needs: "pythia" }),
          stage("command", "confirm", { command: "{python} {home}/bingo.py measure {artifact} --traces {home}/traces --warmup 100000000 --sim 150000000",
                                        metrics: "geomean_speedup, storage_bytes", needs: "pythia" })];
        s.objectives = [objective("geomean_speedup", "maximize", { target: "keep", keep: "0.9", above: "1.0" }),
                        objective("storage_bytes", "minimize", { unit: "B" })];
        s.budget.repair_attempts = "3"; s.budget.finalists = "4";
        return s;
      } },
  ];

  function preset(key) {
    for (var i = 0; i < PRESETS.length; i++) {
      if (PRESETS[i].key === key) { var s = PRESETS[i].make(); s.preset = key; return s; }
    }
    throw new Error("no preset " + key);
  }

  // ------------------------------------------------------------------ YAML spelling
  var RESERVED = /^(true|false|yes|no|on|off|null|y|n|~)$/i;

  /** A string as a YAML scalar: plain when that reads back as the same string, else double
      quoted (a JSON string is a valid YAML double-quoted scalar). `flow`: inside [..] or {..}. */
  function q(s, flow) {
    s = String(s);
    var plain = s.length > 0 && s === s.trim() && !/[\x00-\x1f\x7f"]/.test(s) &&
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

  function stageMetrics(st) {
    return st.kind === "command" ? list(st.metrics) : RTL_METRICS.slice();
  }

  function stageCommand(st) {
    if (st.kind === "command") return String(st.command || "").trim();
    return "flux rtl measure {artifact} --stage " + st.kind + " --clock-ps " + (String(st.clock_ps || "").trim() || "1000");
  }

  function gateCommand(state) {
    var g = state.gate || {};
    if (g.kind === "golden") return "flux rtl test {artifact} --golden {home}/" + (String(g.golden || "").trim() || "golden.py");
    if (g.kind === "command") return String(g.command || "").trim();
    return "";
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
  function buildYaml(state) {
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

    var space = (state.space || []).filter(function (r) { return String(r.knob || "").trim() && list(r.choices).length; });
    if (space.length) {
      out += "\nspace:\n";
      space.forEach(function (r) { out += "  " + q(r.knob.trim()) + ": " + flowSeq(list(r.choices).map(typed)) + "\n"; });
    }

    var said = flowSaid(state);
    if (said.length) {
      out += "\nflow:\n";
      said.forEach(function (b) { out += "  " + b + ": " + flowValue(state, b) + "\n"; });
    }

    var gate = gateCommand(state), timeout = String((state.gate || {}).timeout || "").trim();
    if (gate) {
      out += timeout ? "\ngate:\n  test: " + q(gate) + "\n  timeout_s: " + scalar(typed(timeout)) + "\n"
                     : "\ngate: " + q(gate) + "\n";
    }

    var stages = state.stages || [];
    if (stages.length) {
      out += "\nstages:\n";
      stages.forEach(function (st, i) {
        out += "  - name: " + q(String(st.name || "").trim() || "stage" + (i + 1)) + "\n";
        out += "    command: " + q(stageCommand(st) || "(the command)") + "\n";
        if (st.kind === "command") {
          out += "    metrics: " + flowSeq(list(st.metrics)) + "\n";
          if (list(st.needs).length) out += "    needs: " + flowSeq(list(st.needs)) + "\n";
        }
      });
    }

    var objs = (state.objectives || []).filter(function (o) { return String(o.metric || "").trim(); });
    if (objs.length) {
      out += "\nobjectives:\n";
      objs.forEach(function (o) {
        var p = [["metric", o.metric.trim()], ["direction", o.direction === "minimize" ? "minimize" : "maximize"]];
        if (o.target === "goal" && String(o.goal).trim() !== "") p.push(["goal", typed(o.goal)]);
        if (o.target === "keep" && String(o.keep).trim() !== "") {
          p.push(["keep", typed(o.keep)]);
          if (String(o.above).trim() !== "") p.push(["above", typed(o.above)]);
        }
        if (String(o.unit || "").trim()) p.push(["unit", o.unit.trim()]);
        out += "  - " + flowMap(p) + "\n";
      });
    }

    var b = state.budget || {}, bp = [];
    ["steps", "passes", "repair_attempts", "finalists", "workers"].forEach(function (k) {
      if (String(b[k] || "").trim() !== "") bp.push([k, typed(b[k])]);
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
  function check(state) {
    var msgs = [];
    function error(t) { msgs.push({ level: "error", text: t }); }
    function warn(t) { msgs.push({ level: "warning", text: t }); }
    function note(t) { msgs.push({ level: "note", text: t }); }
    var id = String(state.id || "").trim();
    var lang = language(state), rtl = /^(systemverilog|verilog)$/.test(lang);
    if (!id) error("Give the problem a name (letters, digits and _).");
    else if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(id)) error("The name \"" + id + "\" should be a letter, then letters, digits or _.");
    if (!String(state.statement || "").trim()) error("Say what you want made (the statement is empty).");

    var gate = gateCommand(state);
    if (!gate) error("Say how a design is checked: a golden model or a test command.");
    if (state.gate && state.gate.kind === "golden" && !rtl) warn("The golden-model check tests Verilog or SystemVerilog; the language is " + lang + ".");
    var t = String((state.gate || {}).timeout || "").trim();
    if (t && !(Number(t) > 0)) error("The check's time limit should be a number of seconds.");

    var stages = state.stages || [], measured = {}, names = {};
    if (!stages.length) warn("Nothing is measured: add a measurement so designs can be compared.");
    stages.forEach(function (st, i) {
      var nm = String(st.name || "").trim() || "stage" + (i + 1);
      if (names[nm]) error("Two measurements are named \"" + nm + "\"; names must differ.");
      names[nm] = true;
      if (st.kind === "command") {
        if (!String(st.command || "").trim()) error("Measurement \"" + nm + "\" needs a command.");
        if (!list(st.metrics).length) error("Measurement \"" + nm + "\" needs the names of the numbers it prints (name=value).");
      } else {
        if (!rtl) warn("Measurement \"" + nm + "\" synthesises RTL, but the language is " + lang + ".");
        if (!(Number(st.clock_ps) > 0)) error("Measurement \"" + nm + "\" needs a clock period in picoseconds.");
      }
      stageMetrics(st).forEach(function (m) { measured[m] = (measured[m] || 0) + 1; });
    });

    var objs = (state.objectives || []).filter(function (o) { return String(o.metric || "").trim(); });
    if (!objs.length) warn("No goal yet: add what should be made bigger or smaller.");
    objs.forEach(function (o) {
      var m = o.metric.trim();
      if (!measured[m]) error("The goal \"" + m + "\" is not measured by any measurement.");
      else if (measured[m] < stages.length) warn("Every measurement should report \"" + m + "\"; some do not.");
      if (o.target === "goal" && isNaN(Number(o.goal))) error("The target for \"" + m + "\" should be a number.");
      if (o.target === "keep") {
        var k = Number(o.keep);
        if (!(k > 0 && k <= 1)) error("The share kept for \"" + m + "\" should be between 0 and 1 (0.9 is 90%).");
        if (String(o.above).trim() !== "" && isNaN(Number(o.above))) error("The baseline for \"" + m + "\" should be a number.");
      }
    });

    var flow = state.flow || {};
    FLOW_BOXES.forEach(function (b) {
      var v = flow[b];
      if (typeof v === "string" && v.indexOf("agent:") === 0 && DELEGABLE.indexOf(b) < 0 && b !== "generate") {
        error("\"" + BOXES[b].title + "\" (" + b + ") is never handed to a coding agent: it establishes the facts.");
      } else if (v !== undefined && !choiceOf(b, v)) {
        error("\"" + BOXES[b].title + "\" (" + b + ") cannot be \"" + v + "\".");
      }
    });

    var space = (state.space || []).filter(function (r) { return String(r.knob || "").trim(); });
    var knobs = space.map(function (r) { return r.knob.trim(); });
    space.forEach(function (r) { if (!list(r.choices).length) error("The setting \"" + r.knob.trim() + "\" has no choices."); });
    var searching = flow.dse && flow.dse !== "none";
    if (searching && !space.length) error("A search needs settings to walk: add some under Advanced > Settings to search.");
    if (!searching && space.length) warn("The settings are only searched when \"Search the settings\" is on.");
    if (searching && flow.orchestrate && flow.orchestrate !== "default") warn("With a search, the search picks the next job; \"Pick the next job\" is left out.");
    if (flow.generate === "command" && !String(state.generateCommand || "").trim()) error("Say the command that writes each design.");

    var cmds = [["the check", gate]];
    stages.forEach(function (st) { cmds.push(["measurement \"" + (st.name || "?") + "\"", stageCommand(st)]); });
    if (flow.generate === "command") cmds.push(["the design script", state.generateCommand]);
    cmds.forEach(function (c) {
      placeholders(c[1]).forEach(function (p) {
        if (BUILTIN_SUBS.indexOf(p) < 0 && knobs.indexOf(p) < 0) error("In " + c[0] + ", {" + p + "} is neither a setting to search nor one of Flux's own.");
      });
    });

    ["steps", "passes", "repair_attempts", "finalists", "workers"].forEach(function (k) {
      var v = String((state.budget || {})[k] || "").trim();
      if (v && !/^\d+$/.test(v)) error("Budget \"" + k + "\" should be a whole number.");
    });

    var files = {};
    if (state.gate && state.gate.kind === "golden") files[String(state.gate.golden || "golden.py").trim()] = 1;
    cmds.forEach(function (c) {
      var re = /\{home\}\/([\w.\-\/]+)/g, m;
      while ((m = re.exec(String(c[1] || "")))) files[m[1]] = 1;
    });
    list(state.knowledgeFiles).forEach(function (f) { files[f] = 1; });
    var agents = {};
    flowSaid(state).forEach(function (b) { if (String(flow[b]).indexOf("agent:") === 0) agents[flow[b].slice(6)] = 1; });
    if (Object.keys(agents).length) note("The coding agent " + Object.keys(agents).join(", ") + " must be installed where it runs.");
    var fl = Object.keys(files);
    if (fl.length) note("Put these beside the document: " + fl.join(", ") + ".");
    return msgs;
  }

  var api = { buildYaml: buildYaml, check: check, preset: preset, PRESETS: PRESETS, BOXES: BOXES,
              FLOW_BOXES: FLOW_BOXES, DELEGABLE: DELEGABLE, NEVER: NEVER, LANGUAGES: LANGUAGES,
              RTL_METRICS: RTL_METRICS, AGENTS: AGENTS, DSE_POLICIES: DSE_POLICIES, halfOf: halfOf,
              defaultFlow: defaultFlow, base: base };
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

      var g = state.gate;
      var how = section("How is a design checked?", [
        field("Check", function () { return g.kind; }, function (v) { g.kind = v; },
              { structural: true, options: [["golden", "Against a golden model (golden.py)"], ["command", "With my own test command"]] }),
        g.kind === "golden"
          ? field("Golden model file", function () { return g.golden; }, function (v) { g.golden = v; }, { hint: "Python: the ports and what each output must be" })
          : field("Test command", function () { return g.command; }, function (v) { g.command = v; },
                  { wide: true, placeholder: "{python} {home}/check.py {artifact}", hint: "Prints \"N failing\", or fails. {artifact} is the design's file, {home} this folder." }),
        field("Time limit, seconds (optional)", function () { return g.timeout; }, function (v) { g.timeout = v; }),
      ]);

      var measured = section("What is measured?", [h("div", { class: "fc-rows" }, state.stages.map(function (st, i) {
        var kids = [
          field("Name", function () { return st.name; }, function (v) { st.name = v; }),
          field("How", function () { return st.kind; }, function (v) { st.kind = v; },
                { structural: true, options: [["synth", "RTL synthesis (quick)"], ["place", "RTL placement (slower, closer)"],
                                              ["route", "RTL routing (slowest)"], ["command", "My own command"]] }),
        ];
        if (st.kind === "command") {
          kids.push(field("Command", function () { return st.command; }, function (v) { st.command = v; }, { wide: true, hint: "Prints name=value" }));
          kids.push(field("Numbers it prints", function () { return st.metrics; }, function (v) { st.metrics = v; }, { placeholder: "time_ms, score", structural: false }));
          kids.push(field("Tools it needs (optional)", function () { return st.needs; }, function (v) { st.needs = v; }));
        } else {
          kids.push(field("Clock period, ps", function () { return st.clock_ps; }, function (v) { st.clock_ps = v; }, { hint: "500 ps = 2000 MHz" }));
          kids.push(h("small", { class: "fc-inferred", text: "Measures " + RTL_METRICS.join(", ") }));
        }
        kids.push(button("Remove", function () { state.stages.splice(i, 1); changed(true); }, "fc-small"));
        return h("div", { class: "fc-row" }, kids);
      })), button("+ Add a measurement", function () {
        var rtl = /verilog/.test(language(state));
        state.stages.push(stage(rtl ? "synth" : "command", "stage" + (state.stages.length + 1), { clock_ps: rtl ? "1000" : "" }));
        changed(true);
      })], "");

      var metricsKnown = {};
      state.stages.forEach(function (st) { stageMetrics(st).forEach(function (m) { metricsKnown[m] = 1; }); });
      var dl = h("datalist", { id: "fc-metrics" }, Object.keys(metricsKnown).map(function (m) { return h("option", { value: m }); }));
      var goals = section("What is the goal?", [dl, h("p", { class: "fc-hint", text: "The first goal matters most; the next ones break ties." }),
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
      return h("div", { class: "fc-level" }, [what, how, measured, goals]);
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
        var sub = half === "off" && name === "orchestrate" && state.flow.dse !== "none" ? "the search" : HALVES[half];
        g.appendChild(s("text", { x: r.x + r.w / 2, y: r.y + 35, "text-anchor": "middle", class: "fc-box-half" }, sub));
        function open() { openBox = openBox === name ? null : name; renderDiagram(); }
        g.addEventListener("click", open);
        g.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(); } });
        svg.appendChild(g);
      });
      renderPanel();
      renderPicker();
    }

    /** The steps as a list too: on a phone the drawing's words are small. */
    function renderPicker() {
      var sel = parts.picker;
      if (!sel) return;
      sel.innerHTML = "";
      sel.appendChild(h("option", { value: "", text: "Choose a step..." }));
      FLOW_BOXES.forEach(function (name) {
        var half = halfOf(state, name);
        var op = h("option", { value: name, text: BOXES[name].title + " \u2014 " + (half === "off" && name === "orchestrate" && state.flow.dse !== "none" ? "the search" : HALVES[half]) });
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

  function start() {
    var host = document.getElementById("flux-crafter");
    if (host && !host.dataset.mounted) { host.dataset.mounted = "1"; mount(host); }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
  // Material's instant navigation swaps pages without a reload
  if (root.document$ && root.document$.subscribe) root.document$.subscribe(start);
})(typeof window !== "undefined" ? window : this);
