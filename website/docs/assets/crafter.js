/* The Flux problem builder's page (website/docs/guide/loop-crafter.md): the form, the drawing and the
   document, in plain DOM, over the model (crafter-model.js, loaded first: `window.FluxCrafterModel`;
   under node, required). The web app's configurator is its own (static/configure/, Preact) over the
   same model. On a page with a `#flux-loop-drawing` (guide/loop-shape.md) it draws the loop alone, at
   its defaults. */
(function (root) {
  "use strict";
  var api = typeof module !== "undefined" && module.exports ? require("./crafter-model.js") : root.FluxCrafterModel;
  if (typeof module !== "undefined" && module.exports) module.exports = api;   // node: the model, as before
  if (typeof document === "undefined" || !api) return;
  var BOXES = api.BOXES, CHECK_TYPES = api.CHECK_TYPES, DSE_GROUPS = api.DSE_GROUPS, FLOW_BOXES = api.FLOW_BOXES, HALVES = api.HALVES, LABELS = api.LABELS,
      LANGUAGES = api.LANGUAGES, STEP_IDS = api.STEP_IDS, STEP_OF = api.STEP_OF, abbreviate = api.abbreviate, autoClock = api.autoClock, base = api.base,
      buildYaml = api.buildYaml, check = api.check, checkType = api.checkType, choiceOf = api.choiceOf, clockPs = api.clockPs, defaultFlow = api.defaultFlow,
      describeObjectives = api.describeObjectives, dictionarySpec = api.dictionarySpec, dseChoices = api.dseChoices, dseGroup = api.dseGroup, dseHint = api.dseHint, explain = api.explain,
      explainEstimate = api.explainEstimate, halfOf = api.halfOf, impliedLanguage = api.impliedLanguage, isCustom = api.isCustom, isFixed = api.isFixed, isPreset = api.isPreset,
      knobNames = api.knobNames, language = api.language, list = api.list, namedFiles = api.namedFiles, naturalDirection = api.naturalDirection, newCheck = api.newCheck,
      newObjective = api.newObjective, newStage = api.newStage, nextStageTool = api.nextStageTool, num = api.num, paramOnly = api.paramOnly, parameterSearch = api.parameterSearch,
      reported = api.reported, reports = api.reports, resolve = api.resolve, runsTool = api.runsTool, setCatalog = api.setCatalog, setCheckTool = api.setCheckTool, shown = api.shown,
      stagePresets = api.stagePresets, stageTools = api.stageTools, stepIndex = api.stepIndex, toolOf = api.toolOf, toolsFor = api.toolsFor, uniqueName = api.uniqueName,
      unitFor = api.unitFor;
  var hasParts = api.hasParts;

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
          hint: "The clock the tools aim for" + (auto ? " (empty: from the objective)" : "") + "; tighter = faster and bigger" });
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
        if (!t) line.push(h("span", { class: "fc-warn fc-grow", text: "No " + (ty.preset ? "Flux preset" : ty.title.toLowerCase() + " tool") + " for " + (lang || "this language") + "; choose Custom." }));
        else {
          if (ids.length > 1 || ty.preset) {
            line.push(field(ty.preset ? "Preset" : "Tool", function () { return c.tool; }, function (v) { setCheckTool(state, c, c.type, v); },
                            { compact: true, structural: true, options: ids.map(function (id) { return [id, toolOf(id).title]; }) }));
          }
          if (keys.length) line.push(paramField(t, c, keys[0]));
        }
        line.push(rowButtons(state.checks, i, t ? [moreButton(c)] : []));
        var kids = [h("div", { class: "fc-line" }, line)];
        if (t && moreOpen.has(c)) {
          var more = [h("small", { class: "fc-wide", text: t.what + " Passes " + String(t.pass || "").replace(/^passes /, "") })];
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
          // a preset made for this language not used yet, else a test script, else any command
          var usedTools = state.checks.map(function (c) { return c.tool; }), usedTypes = state.checks.map(function (c) { return c.type; });
          var preset = toolsFor("preset", lang).filter(function (id) { return usedTools.indexOf(id) < 0; })[0];
          var row = preset ? setCheckTool(state, newCheck(state, "preset"), "preset", preset)
                           : newCheck(state, usedTypes.indexOf("test") < 0 && toolsFor("test", lang).length ? "test" : "custom");
          if (preset) row.name = uniqueName((toolOf(preset) || {}).short || "check", state.checks.map(function (c) { return c.name; }));
          state.checks.push(row);
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
          field("Tool", function () { return isPreset(st.tool) ? "preset" : st.tool; }, function (v) {
            if (v === "preset") v = (stagePresets(language(state, true))[0] || stagePresets()[0] || ["custom-stage"])[0];
            var fresh = newStage({ stages: [] }, v); st.tool = v; st.params = fresh.params; st.gates = [];
          }, { compact: true, structural: true, grow: !isPreset(st.tool), options: tools }),
          field("Name", function () { return st.name; }, function (v) { st.name = v; }, { compact: true, narrow: true })];
        if (isPreset(st.tool)) {
          var list = stagePresets(language(state, true));
          if (!list.some(function (x) { return x[0] === st.tool; })) list = list.concat([[st.tool, t.title]]);
          line.splice(2, 0, field("Preset", function () { return st.tool; }, function (v) {
            var fresh = newStage({ stages: [] }, v); st.tool = v; st.params = fresh.params; st.gates = [];
          }, { compact: true, structural: true, grow: true, options: list }));
        }
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
        if (custom) {
          var dictionaries = st.dictMetrics || (st.dictMetrics = []);
          dictionaries.forEach(function (metric, j) {
            kids.push(h("div", { class: "fc-line" }, [h("span", { class: "fc-gate-word", text: "dictionary" }),
              field("Name", function () { return metric.name; }, function (v) { metric.name = v; }, {compact: true, placeholder: "timings"}),
              field("Direction", function () { return metric.direction || "minimize"; }, function (v) { metric.direction = v; }, {compact: true, options: [["minimize", "lower is better"], ["maximize", "higher is better"]]}),
              field("Unit", function () { return metric.unit || ""; }, function (v) { metric.unit = v; }, {compact: true, narrow: true, placeholder: "ms"}),
              field("Aggregate", function () { return metric.aggregate || "mean"; }, function (v) { metric.aggregate = v; },
                {compact: true, options: [["mean", "mean"], ["median", "median"], ["min", "min"], ["max", "max"], ["sum", "sum"], ["none", "none"]]}),
              button("×", function () { dictionaries.splice(j, 1); changed(true); }, "small fc-icon")]));
          });
          kids.push(button("+ Dictionary", function () { dictionaries.push({name: "", direction: "minimize", unit: ""}); changed(true); }, "fc-add-btn"));
          if (dictionaries.length) kids.push(h("small", {text: 'Print timings={"test": 12, "skipped": null} or a JSON object of metrics. Objectives use timings.test. Missing tests stay unmeasured.'}));
        }
        st.gates.forEach(function (g, j) {
          kids.push(h("div", { class: "fc-line fc-gate" }, [h("span", { class: "fc-gate-word", text: j ? "and" : "go on only if" }),
            field("Number", function () { return g.metric; }, function (v) { g.metric = v; },
                  { compact: true, options: (st.dictMetrics || []).length ? null : (rep.indexOf(g.metric) < 0 && g.metric ? [g.metric] : []).concat(rep),
                    hint: "Use a metric name, a parent aggregate or parent.test" }),
            field("Rule", function () { return g.rule; }, function (v) { g.rule = v; },
                  { compact: true, structural: true, options: [["at", "at least"], ["below", "at most"], ["within", "within % of the best"]] }),
            field(g.rule === "within" ? "Percent" : "Value", function () { return g.value; }, function (v) { g.value = v; }, { compact: true, narrow: true }),
            button("×", function () { st.gates.splice(j, 1); changed(true); }, "small fc-icon")]));
        });
        if (moreOpen.has(st)) {
          var more = [];
          var ran = custom ? runsTool(st) : null;                // D1141: a command that runs a preset says the preset
          if (ran) more.push(h("small", { class: "fc-wide", text: "Runs " + ran.title + ": " + ran.what }));
          else if (t) more.push(h("small", { class: "fc-wide", text: t.what + (custom ? "" : " Reports " + Object.keys(t.metrics || {}).map(function (m) {
            return m + (t.metrics[m] ? " (" + t.metrics[m] + ")" : ""); }).join(", ") + ".") }));
          keys.slice(1).forEach(function (k) { more.push(paramField(t, st, k)); });
          if (custom) more.push(field("Tools it needs", function () { return st.needs; }, function (v) { st.needs = v; }, { compact: true }));
          var est = st.estimate = st.estimate || { kind: "off", margin: "5", command: "" };
          more.push(field("Estimate first", function () { return est.kind; }, function (v) { est.kind = v; },
                          { compact: true, structural: true, hint: "Predict it first; a design estimated to miss a cutoff or limit by more than the margin is skipped",
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
        var line = [h("span", { class: "fc-idx", text: String(i + 1) }), dictionarySpec(state, o.metric) ? field("Number", function () { return o.metric; }, function (v) { o.metric = v; },
            {compact: true, hint: "Use the parent aggregate or parent.test"}) : h("code", { class: "fc-obj-metric", text: o.metric }),
          field("How", function () { return o.label; }, function (v) { o.label = v; }, { compact: true, structural: true, options: LABELS })];
        if (o.label === "atleast" || o.label === "atmost") {
          var u = (dictionarySpec(state, o.metric) || {}).unit || unitFor(o.metric);
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
        if (sel.value) {
          var direction = (dictionarySpec(state, sel.value) || {}).direction || naturalDirection(sel.value);
          state.objectives.push(newObjective(sel.value, direction === "maximize" ? "max" : "min")); changed(true);
        }
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
                { compact: true, key: "id", placeholder: opts.namePlaceholder || "my_design", hint: opts.nameHint || "Letters, digits, - and _ (1–60 characters)" }),
          field("Language", function () { return state.language; }, function (v) { state.language = v; },
                { compact: true, options: langs, structural: true, hint: "Optional, when the tools do not tell it" }),
          state.language === "other" ? field("Language name", function () { return state.languageOther; }, function (v) { state.languageOther = v; }, { compact: true, placeholder: "ini" }) : null,
          ]),
        field("What to make (as you would tell an engineer)", function () { return state.statement; },
              function (v) { state.statement = v; }, { area: true, rows: 3, wide: true, key: "statement" }),
        h("div", { class: "fc-line" }, [
          field("Rules for every design (optional)", function () { return state.contract; },
                function (v) { state.contract = v; }, { area: true, rows: 1, grow: true, placeholder: "Names, ports, what is not allowed" }),
          field("Files the model reads (optional)", function () { return state.knowledgeFiles; },
                function (v) { state.knowledgeFiles = v; }, { compact: true, grow: true, placeholder: "spec.md, notes.txt", hint: "Beside the document, separated by commas" })]),
        // D828: who digests library/'s papers is a box of the drawing ("Digest the papers"), not a field here
      ]);
      return what;
    }
    function noCatalog() {
      return api.catalog().length ? null : h("p", { class: "fc-hint", text: "The tool list did not load; only Custom checks and measurements are offered." });
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
      if (hasParts(state)) out.push(at("parts", "parts", L - 44, 4, true));
      return out;
    }


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
        text = hasParts(state) ? "model picks the part" : "default: one design";
      }
      if (name === "generate" && paramOnly(state)) text = "not run: settings only";        // D911
      if (readonly && (name === "test" || name === "measure")) return name === "test" ? "checks" : "measurements";
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
      if (raw) p.appendChild(h("p", { class: "fc-hint fc-now", text: "Own settings, kept as written: " + JSON.stringify(raw.agent) +
        ". Another choice replaces them." }));
      if (openBox === "generate" && paramOnly(state)) {               // D911: what a search runs
        p.appendChild(h("p", { class: "fc-hint fc-now", text: "A search is on: only a script turns a point into a design; " +
          "with a model or agent here, the settings only reach the commands." }));
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
          p.appendChild(field("Design command", function () { return state.generateCommand; },
                              function (v) { state.generateCommand = v; },
                              { wide: true, placeholder: "{python} {home}/gen.py {artifact} {knob}", hint: "Each setting is {its name}" }));
        }
        if (openBox === "generate" && state.flow.generate === "catalog") {
          p.appendChild(field("Existing designs", function () { return state.generateCatalog; },
                              function (v) { state.generateCatalog = v; }, { wide: true, placeholder: "designs/a.sv, designs/b.sv", hint: "Files beside the document, separated by commas" }));
        }
        if (openBox === "orchestrate" && state.flow.orchestrate === "command") {
          p.appendChild(field("Search command", function () { return state.orchestrateCommand; },
                              function (v) { state.orchestrateCommand = v; }, { wide: true, placeholder: "{python} {home}/search.py {history} {state}", hint: "Answers each round with the next candidates" }));
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
        hint: kept ? "Kept as written; change it in Direct edit." :
          dseHint(state) });
      var selectors = h("div", { class: "fc-line fc-dse-selectors" }, [dseGroupField("dse-group"), policy]);
      if (!opts.foldSteps) {
        return h("div", { class: "fc-level" }, [titled("2. Graph", [h("span", { class: "fc-hint fc-inline", text: " click a box to change it" })], [selectors].concat(drawing()))]);
      }
      if (parts.stepsOpen === undefined) parts.stepsOpen = false;
      var body = h("div", {}, drawing());
      body.hidden = !parts.stepsOpen;
      var toggle = h("button", { type: "button", class: "fc-toggle", "aria-expanded": parts.stepsOpen ? "true" : "false", on: { click: function () {
        parts.stepsOpen = !parts.stepsOpen;
        body.hidden = !parts.stepsOpen;
        toggle.setAttribute("aria-expanded", parts.stepsOpen ? "true" : "false");
        if (parts.stepsOpen) renderDiagram();
      } } }, ["2. Graph", h("span", { class: "fc-hint fc-inline", text: " rules, model or agent per step" })]);
      return h("section", { class: "fc-section fc-advanced" }, [h("h3", {}, [toggle]), selectors, body]);
    }

    // -- level 3: the same fields and rows as above, behind one toggle
    function sub(title, note, kids) {
      return h("div", { class: "fc-sub" }, [h("h4", {}, [title, note ? h("span", { class: "fc-hint fc-inline", text: " " + note }) : null])].concat(kids));
    }

    function renderLevel3() {
      var b = state.budget;
      var bp = state.baseline || (state.baseline = { mode: "off", source: "project" });
      function baselineMetrics() {
        var metrics = bp.metrics || (bp.metrics = []), rep = reported(state);
        var stageOptions = [["", "deepest"]].concat(resolve(state).stages.map(function (s) { return [s.name, s.name]; }));
        var rows = metrics.map(function (m, i) {
          return h("div", { class: "fc-row" }, [h("div", { class: "fc-line" }, [
            field("Metric", function () { return m.metric; }, function (v) { m.metric = v; },
              { key: "baseline-metric-" + i, compact: true, structural: true, options: state.stages.some(function (s) { return (s.dictMetrics || []).length; }) ? null : rep.map(function (v) { return [v, v]; }),
                hint: "Use a metric name, a parent aggregate or parent.test" }),
            field("Value" + (((dictionarySpec(state, m.metric) || {}).unit || unitFor(m.metric)) ? " (" + ((dictionarySpec(state, m.metric) || {}).unit || unitFor(m.metric)) + ")" : ""), function () { return m.value; }, function (v) { m.value = v; },
              { key: "baseline-value-" + i, compact: true, narrow: true }),
            field("Stage", function () { return m.stage || ""; }, function (v) { m.stage = v; },
              { key: "baseline-stage-" + i, compact: true, options: stageOptions }), rowButtons(metrics, i)])]);
        });
        return h("div", { class: "fc-rows", "data-fc-field": "baseline-metrics" }, rows.concat([
          button("+ Metric", function () { metrics.push({ metric: rep[0] || "", value: "", stage: "" }); changed(true); }, "fc-add-btn"),
          h("p", { class: "fc-hint", text: "Reference values for graphs and relative measurements; nothing runs for them." })]));
      }
      var baseline = sub("Baseline / pass 0", "checked and measured before any edit", [
        field("Baseline", function () { return bp.mode; }, function (v) { bp.mode = v; },
          { key: "baseline-mode", structural: true, disabled: (state.kept || []).indexOf("baseline") >= 0,
            options: [["off", "off"], ["before", "first"], ["only", "only"]],
            hint: "first: pass 0, then the passes; only: pass 0 alone, to check the tools. Rerun when its inputs, settings or tools change; outside the pass budget." }),
        bp.mode !== "off" ? field("From", function () { return bp.source; }, function (v) { bp.source = v; },
          { key: "baseline-source", structural: true, options: [["project", "project"], ["file", "file"], ["command", "command"], ["metrics", "numbers"]],
            hint: "project: your scripts as written; file: when they expect a design in {artifact}." }) : null,
        bp.mode !== "off" && bp.source === "file" ? field("File", function () { return bp.file; }, function (v) { bp.file = v; },
          { key: "baseline-file", placeholder: "baseline.py", hint: "Relative to the loop folder; a copy is checked and measured." }) : null,
        bp.mode !== "off" && bp.source === "command" ? field("Command", function () { return bp.command; }, function (v) { bp.command = v; },
          { key: "baseline-command", placeholder: "{python} {home}/baseline.py {artifact}",
            hint: "Runs once with the usual placeholders; may write {artifact} or prepare the project." }) : null,
        bp.mode !== "off" && bp.source === "command" ? field("Time limit, s", function () { return bp.timeout; }, function (v) { bp.timeout = v; },
          { placeholder: "600" }) : null,
        bp.mode !== "off" && bp.source === "metrics" ? baselineMetrics() : null]);
      function num(label, key, dflt, hint) {
        return field(label, function () { return b[key]; }, function (v) { b[key] = v; },
                     { compact: true, placeholder: dflt, hint: hint + " (budget." + key + "; empty: " + dflt + ")" });
      }
      var budget = h("div", { class: "fc-grid" }, [
        num("Designs per round", "steps", "24", "Work items in one round"),
        num("Rounds", "passes", "until stopped", "Before the run stops"),
        num("Rounds at once", "parallel", "1", "Side by side; on a server an admin allows it"),
        num("Search designs a round", "batch", "1", "With a search: designs tried side by side per round"),
        num("Repairs per design", "repair_attempts", "12", "Repairs a draft gets after a check fails"),
        num("Exploration quota", "exploration_quota", "0", "Share of tries kept for new approaches, 0 to 1"),
        num("Designs fully measured", "finalists", "3", "How many designs reach the costliest measurement"),
        num("Tool runs at once", "workers", "auto", "Measurements in parallel; 1 for anything timed"),
        field("Prototype first", function () { return b.prototype; }, function (v) { b.prototype = v; },
              { compact: true, hint: "Proves the algorithm first: yes for maths, no for plain logic (budget.prototype; empty: on with a golden model)",
                options: [["", "default"], ["true", "yes"], ["false", "no"], ["python", "yes, in Python"], ["systemc", "yes, in SystemC"]] })]);

      var knobs = state.space.map(function (r, i) {
        return h("div", { class: "fc-row" }, [h("div", { class: "fc-line" }, [
          h("span", { class: "fc-idx", text: String(i + 1) }),
          field("Setting", function () { return r.knob; }, function (v) { r.knob = v; },
                { compact: true, placeholder: "block", hint: "{name} in a command is the value tried" }),
          field("Choices, in order", function () { return r.choices; }, function (v) { r.choices = v; },
                { compact: true, grow: true, placeholder: "16, 32, 64", hint: "Separated by commas; a \"quoted\" choice stays text (\"01\", \"a,b\")" }),
          h("div", { class: "fc-row-buttons" }, [button("\u00d7", function () { state.space.splice(i, 1); changed(true); }, "small fc-icon")])])]);
      });
      var searching = state.flow.dse && state.flow.dse !== "none";
      var space = sub("Settings to search", searching ? "{name} in the commands" : "used once a search policy is picked in the Graph", [
        h("div", { class: "fc-rows" }, knobs),
        button("+ Setting", function () { state.space.push({ knob: "", choices: "" }); changed(true); }, "fc-add-btn")]);

      var split = sub("Parts", "each checked on its own, then composed", [h("div", { class: "fc-line" }, [
        field("Split the design", function () { return state.partsMode; }, function (v) { state.partsMode = v; },
              { compact: true, structural: true, options: [["none", "no"], ["list", "into these parts"], ["decompose", "let it decide"]] }),
        state.partsMode === "list" ? field("Parts", function () { return state.parts; }, function (v) { state.parts = v; },
                                           { compact: true, grow: true, placeholder: "decoder, datapath", hint: "Part names, separated by commas" }) : null])]);

      // closed until asked for, or until something in it is in use
      var inUse = !!(searching || state.space.length || hasParts(state) || bp.mode !== "off");
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
      "What to make, the rules every design follows, the files the model reads.",
      "Checks refuse a wrong design, in order; a failure sends it back for repair. " +
        "Measurements size or time a passing design, cheapest first; a costly one runs only on the best.",
      "Limits a design must meet, then what to push, most important first.",
      "Who works each step: rules, model or agent. Click a box to change it.",
      "Budget, settings to search, parts" + (opts.extra ? ", resources" : "") +
        ". Empty fields keep the default.",
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
      var commandFile = file.charAt(0) === "-" ? "./" + file : file;
      if (parts.next) parts.next.textContent = "flux task check " + commandFile + "\nflux task run " + commandFile + " --passes 1";
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
        h("span", { class: "fc-hint", text: "vs saved" })]), parts.diffBody]);
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
