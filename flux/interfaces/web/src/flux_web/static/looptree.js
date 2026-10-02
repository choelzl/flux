/* The loop's task tree, built from a run's journal (D739, D742, D747; D752: its own file, pure,
   so node tests it with recorded journals -- flux/tests/unit/test_looptree.py).

   `apply(model, event)` replays one journal line into the model ({nodes, roots, marks,
   standings}); `build(model)` is the tree: this start's Setup, a branch per pass (or per part,
   with the Whole), the End; a leaf is a box of the loop crafter's drawing with the tasks that
   ran in it. The page draws it and selects in it; nothing here touches the page. */
(function (root) {
  "use strict";
  var LOOP_MARKS = new Set(["pass", "ended", "waiting", "outputs"]);

  function model() { return { nodes: new Map(), roots: [], marks: [], standings: new Map() }; }

  /** One event of the journal into the model; "hello" (a new start) begins it afresh. Returns
      what the page may want to know besides: {reset} or {question}. */
  function apply(m, e) {
    if (e.ev === "hello") { m.nodes.clear(); m.roots.length = 0; m.standings.clear(); m.marks.length = 0; m.before = 0; m.cut = false; return { reset: true }; }
    if (e.ev === "window") { m.before = Number(e.before) || 0; m.cut = !!e.cut; return {}; }   // D759: passes left out before the window
    if (e.ev === "start") {
      var n = { id: e.id, name: e.name, why: e.why, params: e.params, t0: e.t, fields: {}, kids: [], parent: null };
      m.nodes.set(e.id, n);
      var p = e.parent != null && m.nodes.get(e.parent);
      if (p) { n.parent = p; p.kids.push(n); } else m.roots.push(n);
    } else if (e.ev === "update") { var u = m.nodes.get(e.id); if (u) Object.assign(u.fields, e.fields); }
    else if (e.ev === "end") {
      var d = m.nodes.get(e.id);
      if (d) {
        d.t1 = e.t; d.seconds = e.seconds; d.output = e.output;
        // D757: a tool that exited with an error failed, though its step went on
        d.failed = e.failed || (String(d.name).indexOf("tool:") === 0 && e.output && e.output.exit != null && e.output.exit !== 0);
      }
    }
    else if (e.ev === "publish") m.standings.set(e.key, e.payload);
    else if (e.ev === "mark" && e.name === "question") { try { return { question: JSON.parse(e.why) }; } catch (_) { return {}; } }
    else if (e.ev === "mark" && LOOP_MARKS.has(e.name)) {
      var w = {}; try { w = JSON.parse(e.why || "{}") || {}; } catch (_) {}
      m.marks.push(Object.assign({}, w, { name: e.name, t: e.t }));
    }
    return {};
  }

  /** The live state (D761): each running phase's latest fields, the standings -- whole, each time. */
  function applyLive(m, doc) {
    var ups = (doc && doc.updates) || {};
    Object.keys(ups).forEach(function (id) { var n = m.nodes.get(Number(id)); if (n && n.t1 == null) n.fields = ups[id] || {}; });
    var pub = (doc && doc.publish) || {};
    Object.keys(pub).forEach(function (k) { m.standings.set(k, pub[k]); });
  }

  var running = function (n) { return n.t1 == null; };
  function failedBelow(n) { return n.failed || n.kids.some(failedBelow); }
  var within = function (v, n) { for (var p = n; p; p = p.parent) if (p === v) return true; return false; };
  var rootOf = function (n) { var p = n; while (p.parent) p = p.parent; return p; };
  var subtree = function (vs) { var all = []; var walk = function (n) { all.push(n); n.kids.forEach(walk); }; vs.forEach(walk); return all; };
  var uniq = function (xs) { return Array.from(new Set(xs.filter(Boolean))); };
  var listed = function (xs) { return xs.length > 3 ? xs.slice(0, 3).join(", ") + " +" + (xs.length - 3) : xs.join(", "); };

  /** The box of the drawing a task belongs to, by its name (D726): an agent or a tool takes its step's. */
  var BOX_BY_WORD = { validate: "validate", propose: "orchestrate", orchestrate: "orchestrate", plan: "plan", dse: "dse",
    generation: "generate", generate: "generate", "llm-gen": "generate", "template-fill": "generate", template: "generate", prototype: "generate",
    patch: "generate", repair: "generate", rewrite: "generate", design: "generate", oracle: "generate", invent: "generate", compute: "generate",
    test: "test", build: "test", gate: "test", judge: "test", admitted: "test", verify: "test",
    simulation: "measure", physical: "measure", analytical: "measure", measure: "measure", screen: "measure", confirm: "measure", estimate: "measure",
    calibrate: "calibrate", decide: "select", frontier: "select", decision: "select", select: "select",
    records: "records", knowledge: "knowledge", feedback: "feedback", extract: "extract" };
  var boxes = new WeakMap();
  function boxOf(n) {
    if (boxes.has(n)) return boxes.get(n);
    var name = String(n.name).toLowerCase(), why = String(n.why || "").toLowerCase();
    var b = null;
    if (name.startsWith("critique")) b = /decision/.test(name + why) ? "crit-decision" : /division|decompos/.test(name + why) ? "crit-division" : "crit-part";
    else if (name.startsWith("tool:")) b = /^generate/.test(why) ? "generate" : /^(stage|estimate)/.test(why) ? "measure" : /^(test|lint|golden|build|compile)/.test(why) ? "test" : null;
    else if (/^knowledge: digest\b/.test(name)) b = "digest";                // D771: the papers digested in the Setup
    else if (/^gate: (tools|the problem)\b/.test(name)) b = "validate";        // D739: the document's own checks
    else if (/^generation: build\b/.test(name)) b = "test";                  // the gate's build of a draft
    else if (/^evaluation\b|: compose\b/.test(name)) b = "parts";            // proven parts composed (drawn when it has parts)
    else if (!name.startsWith("agent:")) b = BOX_BY_WORD[name.split(/[\s:]/)[0]] || null;
    if (!b) b = n.parent ? boxOf(n.parent) : name.startsWith("agent:") ? "generate" : null;
    boxes.set(n, b);
    return b;
  }
  /** The visit a task belongs to: itself or the ancestor that began its box's stretch. */
  function visitOf(n) {
    for (var p = n; p; p = p.parent) { var b = boxOf(p); if (b && !(p.parent && boxOf(p.parent) === b)) return p; }
    return null;
  }
  /** What worked in a visit, for the detail (D728): what runs now, else the agent's or the
      model's turn, else a tool, else the visit itself. Only tasks of the visit's own box. */
  function focusOf(v) {
    var b = boxOf(v), all = [];
    var walk = function (n) { n.kids.forEach(function (k) { if (boxOf(k) === b) { all.push(k); walk(k); } }); };
    walk(v);
    var latest = function (xs) { return xs.reduce(function (a, x) { return (!a || x.t0 >= a.t0 ? x : a); }, null); };
    var live = all.filter(running);
    var worker = function (n) { return /^(agent:|llm|model|tool:)/.test(String(n.name)); };
    return latest(live.filter(worker)) || latest(live) || latest(all.filter(function (n) { return String(n.name).startsWith("agent:"); }))
      || latest(all.filter(function (n) { return /^(llm|model)/.test(String(n.name)); })) || latest(all.filter(worker)) || v;
  }

  var SETUP_BOXES = new Set(["validate", "knowledge", "digest", "records", "feedback"]);
  var WORK = new Set(["orchestrate", "plan", "dse", "generate"]);
  var isDivide = function (n) { return /^propose: decompose/.test(String(n.name)); };
  var isSetup = function (n) { return SETUP_BOXES.has(boxOf(n)) || isDivide(n); };
  var partOf = function (n) { for (var p = n; p; p = p.parent) { var v = p.params && p.params.part; if (v) return String(v); } return ""; };
  var passTag = function (n) { for (var p = n; p; p = p.parent) { var v = p.params && p.params.pass; if (v != null && v !== "") return Number(v); } return null; };
  // D742: a leaf's name is a word; the crafter's box name is its tooltip
  var LEAF_NAME = { validate: "Checks", knowledge: "Reading", digest: "Digest", records: "Record", feedback: "Notes", orchestrate: "Pick", plan: "Plan",
    dse: "Search", generate: "Design", test: "Check", measure: "Measure", calibrate: "Calibrate", select: "Choose", extract: "Lessons",
    "crit-division": "Critic", "crit-part": "Critic", "crit-decision": "Critic", parts: "Compose" };
  function leafTitle(b, v) {
    if (isDivide(v)) return "Divide";
    if (/^propose: finalists/.test(String(v.name))) return "Finalists";
    return LEAF_NAME[b] || b;
  }
  /** A leaf's full name: the crafter's (its `boxTitle`, when given), else the tree's own word. */
  function boxName(it, boxTitle) {
    return it.title === "Divide" ? "Divide into parts" : it.title === "Finalists" ? "Choose the finalists" : it.title === "Compose" ? "Put the parts together"
      : it.title === "Setup" ? "The pass's own setup" : (boxTitle ? boxTitle(it.box) : null) || it.title;
  }
  function leavesOf(vs, key) {
    var out = [];
    vs.forEach(function (v) {
      var b = boxOf(v), title = leafTitle(b, v), last = out[out.length - 1];
      if (last && last.title === title) last.tasks.push(v);
      else out.push({ leaf: true, box: b, title: title, tasks: [v], key: key + "/" + out.length });
    });
    return out;
  }
  function designsOf(vs) {                       // what a pass worked on: the designs its tools made or built
    var names = [];
    var walk = function (n) {
      var m = /^generate (.+)$/.exec(String(n.why || "")) || /^generation: build (.+)$/.exec(String(n.name));
      if (m && /^(tool:|generation: build)/.test(String(n.name)) && names.indexOf(m[1]) < 0) names.push(m[1]);
      n.kids.forEach(walk);
    };
    vs.forEach(walk);
    return names;
  }
  function branch(key, title, why, kids) { return { key: key, title: title, why: why, kids: kids }; }

  function endLeaves(marks) {
    var last = function (name) { for (var i = marks.length - 1; i >= 0; i--) if (marks[i].name === name) return marks[i]; return null; };
    var ended = last("ended"), outs = last("outputs"), waiting = last("waiting"), lastPass = last("pass");
    var at = function (m) { return m && (!lastPass || m.t >= lastPass.t); };
    var leaves = [];
    var pseudo = function (key, title, why, output, t, live) {
      leaves.push({ leaf: true, box: "end", title: title, key: "end/" + key,
        tasks: [{ id: "end:" + key, name: title, why: why, params: {}, fields: {}, output: output, kids: [], parent: null, t0: t, t1: live ? null : t, seconds: 0, pseudo: true }] });
    };
    if (at(waiting) && !ended) pseudo("waiting", "Waiting", waiting.why || "for a note or a stop", {}, waiting.t, true);
    if (ended) pseudo("why", "Reason", ended.why || "", { why: ended.why }, ended.t);
    if (outs) {
      var d = outs.decision;
      var nums = d && d.metrics ? Object.entries(d.metrics).map(function (kv) { return kv[0] + "=" + (typeof kv[1] === "number" ? Number(kv[1].toPrecision(5)) : kv[1]); }).join(", ") : "";
      pseudo("decision", "Decision", d ? d.name + (nums ? " · " + nums : "") : "none",
        { decision: d, "decided by": outs.decided_by, "the front": outs.front, refused: outs.refused, stopped: outs.stopped }, outs.t);
      if (outs.lessons && outs.lessons.length) pseudo("lessons", "Lessons", String(outs.lessons.length), { lessons: outs.lessons }, outs.t);
      var est = outs.established || [], not = outs.not_established || [];
      if (est.length || not.length) pseudo("established", "Established", est.length + (not.length ? " · " + not.length + " not" : ""),
        { established: est, "not established": not }, outs.t);
      var base = function (f) { return String(f).split("/").pop(); };
      if (outs.design) pseudo("design", "Design file", base(outs.design), { file: outs.design }, outs.t);
      if (outs.answer) pseudo("answer", "Answer file", base(outs.answer), { file: outs.answer }, outs.t);
    }
    return leaves;
  }

  /** The tree of this start (D739): [{key, title, why, kids}] branches, leaves {leaf, box, title, tasks, key}. */
  function build(m) {
    var marks = m.marks, passMarks = marks.filter(function (x) { return x.name === "pass"; });
    var passAt = function (t) { var k = 1; passMarks.forEach(function (x) { if (x.t <= t + 1e-3) k = Number(x.n) || k; }); return k; };
    var visits = [];
    m.nodes.forEach(function (n) { var b = boxOf(n); if (b && !(n.parent && boxOf(n.parent) === b)) visits.push(n); });
    visits.sort(function (a, b) { return a.t0 - b.t0 || a.id - b.id; });
    var named = new Set(visits.map(partOf).filter(Boolean));
    var hasParts = named.size > 0;
    // the pick of the next part belongs to the part it picked
    var partOfVisit = function (v) {
      return partOf(v) || (/^propose: what next/.test(String(v.name)) && v.output && named.has(String(v.output.picked)) ? String(v.output.picked) : "");
    };
    var passes = new Map();
    passMarks.forEach(function (x) {
      if (!passes.has(Number(x.n))) passes.set(Number(x.n), { n: Number(x.n), explore: Number(x.explore || 0), conclude: !!x.conclude, visits: [] });
    });
    visits.forEach(function (v) {
      if (!hasParts && boxOf(v) === "parts") return;                    // nothing to put together
      var tag = passTag(v), k = tag != null ? tag : passAt(rootOf(v).t0);  // D747: passes at once say whose they are
      if (!passes.has(k)) passes.set(k, { n: k, explore: 0, visits: [] });
      passes.get(k).visits.push(v);
    });
    var order = Array.from(passes.values()).filter(function (p) { return p.visits.length; }).sort(function (a, b) { return a.n - b.n; });
    // D747: the passes that ran at the same time as each, from their own times
    order.forEach(function (p) {
      var ts = subtree(p.visits);
      p.span = [Math.min.apply(null, ts.map(function (t) { return t.t0; })), Math.max.apply(null, ts.map(function (t) { return t.t1 == null ? Infinity : t.t1; }))];
    });
    order.forEach(function (p) { p.with = order.filter(function (q) { return q !== p && q.span[0] < p.span[1] && p.span[0] < q.span[1]; }).map(function (q) { return q.n; }); });
    var out = [], setupLeaves = [];
    var passBody = function (p, vs, key) {          // a pass's leaves: its own setup as one leaf, then the boxes
      var i = 0;
      while (i < vs.length && isSetup(vs[i])) i++;
      // D755: a resumed pass first re-checks and re-measures what the record holds, then works --
      // all of that before its first pick, search or design is its setup, one leaf
      var w = vs.findIndex(function (v) {             // dividing into parts and choosing finalists are not new work
        return WORK.has(boxOf(v)) && !isDivide(v) && !/^propose: finalists/.test(String(v.name));
      });
      if (w < 0 && p.conclude) {                     // the Conclusion: all before its decision
        for (var k = vs.length - 1; k >= 0; k--) if (boxOf(vs[k]) === "select" && /^decide\b/.test(String(vs[k].name))) { w = k; break; }
      }
      if (w > i) i = w;
      var lead = vs.slice(0, i), rest = vs.slice(i);
      if (p === order[0]) { setupLeaves.push.apply(setupLeaves, leavesOf(lead, "setup")); return leavesOf(rest, key); }
      var pre = lead.length ? [{ leaf: true, box: "validate", title: "Setup", tasks: lead, key: key + "/setup" }] : [];
      return pre.concat(leavesOf(rest, key));
    };
    var passWhy = function (p, vs) {
      var made = designsOf(vs);
      return [made.length ? listed(made) : "", p.explore ? "exploring" : "", p.with && p.with.length ? "with " + p.with.join(", ") : ""]
        .filter(Boolean).join(" · ");
    };
    if (!hasParts) {
      order.forEach(function (p) {
        out.push(branch("pass:" + p.n, p.conclude ? "Conclusion" : "Pass " + p.n, p.conclude ? "over every pass" : passWhy(p, p.visits), passBody(p, p.visits, "pass:" + p.n)));
      });
    } else {
      var byPart = new Map(), whole = [];
      order.forEach(function (p) {
        var own = new Map();
        p.visits.forEach(function (v) { var k = partOfVisit(v); if (!own.has(k)) own.set(k, []); own.get(k).push(v); });
        own.forEach(function (vs, k) {
          if (!k) { var kids = passBody(p, vs, "whole/pass:" + p.n); if (kids.length) whole.push(branch("whole/pass:" + p.n, "Pass " + p.n, passWhy(p, vs), kids)); return; }
          if (!byPart.has(k)) byPart.set(k, []);
          byPart.get(k).push(branch("part:" + k + "/pass:" + p.n, "Pass " + p.n, passWhy(p, vs), leavesOf(vs, "part:" + k + "/pass:" + p.n)));
        });
      });
      var st = m.standings.get("standings") || {};
      var partState = new Map((Array.isArray(st.parts) ? st.parts : []).map(function (x) { return [String(x.part), x.state]; }));
      byPart.forEach(function (kids, k) { out.push(branch("part:" + k, "Part " + k, partState.get(k) || "", kids)); });
      if (whole.length) out.push(branch("whole", "Whole", "", whole));
    }
    if (setupLeaves.length) out.unshift(branch("setup", "Setup", "", setupLeaves));
    if (m.before || m.cut) {                          // D759: a day-long start opens on its last passes
      var left = [m.before ? m.before + " pass(es)" : "", m.cut ? "the start of this pass" : ""].filter(Boolean).join(" and ");   // D762
      var earlier = branch("earlier", "Earlier", left + " not loaded · load them", []);
      earlier.earlier = true;
      out.unshift(earlier);
    }
    out.forEach(function (it) { if (it.why == null) it.why = ""; });
    var end = endLeaves(marks);
    var dec = end.filter(function (l) { return l.title === "Decision"; })[0];
    if (end.length) out.push(branch("end", "End", ((dec || end[0]).tasks[0].why || "").split(" · ")[0], end));
    return out;
  }

  /** A leaf's line (D742): what it produced, short -- not the code's own wording. */
  function leafLine(it) {
    if (it.tasks[0].pseudo) return it.tasks[0].why || "";
    var all = subtree(it.tasks), latest = it.tasks.reduce(function (a, x) { return (x.t0 >= a.t0 ? x : a); });
    var toolWhy = function (re) { return uniq(all.filter(function (n) { return String(n.name).indexOf("tool:") === 0; }).map(function (n) { return (re.exec(String(n.why || "")) || [])[1]; })); };
    var agent = all.filter(function (n) { return String(n.name).indexOf("agent:") === 0; })[0];
    switch (it.box) {
      case "digest": { var o = latest.output || {};                       // D771: what this Setup digested
        if (o.error) return "failed";
        return o["in all"] == null ? "" : (o.digested ? o.digested + " new" + (o.by ? " by " + o.by : "") + " · " : "") + o["in all"] + " paper(s) digested"; }
      case "dse": return listed(designsOf(it.tasks)) || (latest.output && latest.output.candidates != null ? String(latest.output.candidates) : "");
      case "generate": {
        var made = uniq(toolWhy(/^generate (.+)$/).concat(all.map(function (n) { return (/^generation: build (.+)$/.exec(String(n.name)) || [])[1]; })));
        return [agent ? String(agent.name).replace(/^agent:\s*/, "") : "", listed(made)].filter(Boolean).join(" · ");
      }
      case "test": return listed(toolWhy(/^test (.+)$/)) || String(latest.why || "").replace(/candidate\(s\)/, "design(s)");
      case "measure": {
        var stages = uniq(it.tasks.map(function (n) { return String(n.name).replace(/^[^:]+:\s*/, "").replace(/\s*\(.*\)$/, "").replace(/ alone$/, ""); }));
        return [stages.join(", "), listed(toolWhy(/^stage \S+ (.+)$/))].filter(Boolean).join(" · ");
      }
      case "select": { var d = all.map(function (n) { return n.output && n.output.decision; }).filter(Boolean).pop(); return d ? String(d) : String(latest.why || ""); }
      case "crit-decision": case "crit-part": case "crit-division": {
        var v = latest.output && latest.output.verdict;
        return [String(latest.why || ""), v ? (/^no objection/.test(v) ? "no objection" : "objects") : ""].filter(Boolean).join(" · ");
      }
      case "orchestrate": { var p = all.map(function (n) { return n.output && n.output.picked; }).filter(Boolean).pop(); return p ? String(p) : ""; }
      default: return it.title === "Setup" || SETUP_BOXES.has(it.box) ? "" : String(latest.why || "");
    }
  }

  var itemTasks = function (it) { return it.leaf ? it.tasks : it.kids.reduce(function (a, k) { return a.concat(itemTasks(k)); }, []); };
  var itemHas = function (it, n) { return !!n && itemTasks(it).some(function (v) { return within(v, n); }); };

  var api = { LOOP_MARKS: LOOP_MARKS, model: model, apply: apply, applyLive: applyLive, build: build, leafLine: leafLine, boxName: boxName, boxOf: boxOf,
    focusOf: focusOf, visitOf: visitOf, running: running, failedBelow: failedBelow, within: within, subtree: subtree,
    itemTasks: itemTasks, itemHas: itemHas };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.FluxLoopTree = api;
})(typeof window !== "undefined" ? window : this);
