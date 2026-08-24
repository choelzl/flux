# Loop crafter

Assemble a Flux loop from its blocks: click a block in the palette to drop it on the
canvas, drag to arrange, click one block then another to wire them. The checker below
applies [the shape every loop shares](loop-shape.md) — gates before anything that
costs, fast rungs order but never conclude, every model role behind a gate — and the
skeleton updates as you build. It generates the same structure
[build your own loop](build-your-own.md) walks through by hand.

<div id="crafter" style="border:1px solid var(--md-default-fg-color--lightest);border-radius:6px;overflow:hidden">
  <div id="palette" style="padding:8px 10px;display:flex;flex-direction:column;gap:4px;border-bottom:1px solid var(--md-default-fg-color--lightest)"></div>
  <svg id="canvas" viewBox="0 0 960 470" style="display:block;width:100%;height:auto;background:var(--md-code-bg-color)"></svg>
  <div style="padding:6px 10px;display:flex;gap:14px;align-items:center;border-top:1px solid var(--md-default-fg-color--lightest);font-size:.7rem">
    <span id="verdict"></span>
    <span style="margin-left:auto;opacity:.6">click block → block to wire · double-click removes · ⌫ deletes selection</span>
    <button id="clear" class="md-button" style="padding:2px 10px">clear</button>
    <button id="preset" class="md-button md-button--primary" style="padding:2px 10px">load the canonical shape</button>
  </div>
</div>

## The generated skeleton

<pre style="max-height:420px;overflow:auto"><code id="skeleton"></code></pre>

<script>
(function () {
  // ── five roles, each a small set of real Flux node types ────────────────────
  const ROLES = {
    io:           { label: "IO",           hue: "#1e7a3c" },
    mentor:       { label: "mentor",       hue: "#8a2b76" },
    evaluator:    { label: "evaluator",    hue: "#1668b0" },
    orchestrator: { label: "orchestrator", hue: "#2b3442" },
    generator:    { label: "generator",    hue: "#cf7c1c" },
  };
  const MODEL_HUE = "#5b3ce0";   // model nodes differ in ONE channel: the outline.
                                 // Fill stays the ROLE tint, so the five role hues
                                 // group the canvas and purple borders mark the LLM.
  const KINDS = {
    input:      { role: "io",           label: "input" },
    output:     { role: "io",           label: "output" },

    knowledge:  { role: "mentor",       label: "knowledge" },
    records:    { role: "mentor",       label: "records" },
    feedback:   { role: "mentor",       label: "feedback" },
    extract:    { role: "mentor",       label: "extract" },

    eval_fast:  { role: "evaluator",    label: "analytical (fast)" },
    eval_slow:  { role: "evaluator",    label: "simulation (slow)" },
    eval_test:  { role: "evaluator",    label: "test (correctness)" },
    eval_phys:  { role: "evaluator",    label: "physical (P&R)" },
    calibrate:  { role: "evaluator",    label: "calibrate (CI)" },

    gate:       { role: "orchestrator", label: "gate" },
    dse:        { role: "orchestrator", label: "DSE" },
    propose:    { role: "orchestrator", label: "LLM-propose", model: true },
    frontier:   { role: "orchestrator", label: "frontier" },
    decide:     { role: "orchestrator", label: "decide" },

    template:   { role: "generator",    label: "template-fill" },
    llm_gen:    { role: "generator",    label: "LLM-gen", model: true },
    repair:     { role: "generator",    label: "repair", model: true },
  };
  const hueOf = k => ROLES[KINDS[k].role].hue;
  const blocks = [];   // {id, kind, x, y}
  const wires = [];    // [fromId, toId]
  let nextId = 1, selected = null, drag = null;

  const svg = document.getElementById("canvas");
  const NS = "http://www.w3.org/2000/svg";

  // mouse position in viewBox units, whatever the rendered width
  function toCanvas(e) {
    const k = 960 / svg.clientWidth;
    return [e.offsetX * k, e.offsetY * k];
  }

  function addBlock(kind, x, y) {
    blocks.push({ id: nextId++, kind, x, y });
    render();
  }
  function blockAt(id) { return blocks.find(b => b.id === id); }

  // ── palette: one labelled row per role ─────────────────────────────────────
  const pal = document.getElementById("palette");
  Object.entries(ROLES).forEach(([role, r]) => {
    const row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;gap:6px;flex-wrap:wrap";
    const tag = document.createElement("span");
    tag.textContent = r.label;
    tag.style.cssText = `font-size:.62rem;width:82px;color:${r.hue};font-weight:700;` +
                        `text-transform:uppercase;letter-spacing:.05em`;
    row.appendChild(tag);
    Object.entries(KINDS).filter(([, k]) => k.role === role).forEach(([kind, k]) => {
      const b = document.createElement("button");
      b.textContent = k.label;
      b.style.cssText = `font-size:.66rem;padding:2px 9px;border-radius:12px;cursor:pointer;` +
        `border:1.5px solid ${k.model ? MODEL_HUE : r.hue};color:${r.hue};` +
        `background:transparent`;
      b.onclick = () => addBlock(kind, 40 + (blocks.length % 5) * 170,
                                 40 + ((blocks.length / 5) | 0) * 78);
      row.appendChild(b);
    });
    pal.appendChild(row);
  });

  // ── rendering ──────────────────────────────────────────────────────────────
  function render() {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const defs = document.createElementNS(NS, "defs");
    defs.innerHTML = `<marker id="arr" viewBox="0 0 10 10" refX="9" refY="5"
      markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker>`;
    svg.appendChild(defs);
    wires.forEach(([a, b]) => {
      const A = blockAt(a), B = blockAt(b);
      if (!A || !B) return;
      const l = document.createElementNS(NS, "line");
      l.setAttribute("x1", A.x + 70); l.setAttribute("y1", A.y + 21);
      l.setAttribute("x2", B.x + 70); l.setAttribute("y2", B.y + 21);
      l.setAttribute("stroke", "currentColor");
      l.setAttribute("stroke-width", "1.5");
      l.setAttribute("marker-end", "url(#arr)");
      l.setAttribute("opacity", "0.55");
      svg.appendChild(l);
    });
    blocks.forEach(b => {
      const k = KINDS[b.kind], hue = hueOf(b.kind);
      const g = document.createElementNS(NS, "g");
      g.setAttribute("transform", `translate(${b.x},${b.y})`);
      g.style.cursor = "grab";
      const r = document.createElementNS(NS, "rect");
      r.setAttribute("width", 140); r.setAttribute("height", 42); r.setAttribute("rx", 8);
      r.setAttribute("fill", hue); r.setAttribute("fill-opacity", "0.12");
      r.setAttribute("stroke", k.model ? MODEL_HUE : hue);
      r.setAttribute("stroke-width", selected === b.id ? 3 : 1.5);
      const t = document.createElementNS(NS, "text");
      t.setAttribute("x", 70); t.setAttribute("y", 19);
      t.setAttribute("text-anchor", "middle");
      t.setAttribute("font-size", "11"); t.setAttribute("fill", "currentColor");
      t.textContent = k.label;
      const rr = document.createElementNS(NS, "text");
      rr.setAttribute("x", 70); rr.setAttribute("y", 33);
      rr.setAttribute("text-anchor", "middle");
      rr.setAttribute("font-size", "8.5");
      rr.setAttribute("fill", k.model ? MODEL_HUE : hue);
      rr.textContent = k.model ? KINDS[b.kind].role + " · model" : KINDS[b.kind].role;
      g.append(r, t, rr);
      g.addEventListener("mousedown", e => {
        const [mx, my] = toCanvas(e);
        drag = { id: b.id, dx: mx - b.x, dy: my - b.y, moved: false };
        e.preventDefault();
      });
      g.addEventListener("dblclick", () => {
        const i = blocks.findIndex(x => x.id === b.id);
        blocks.splice(i, 1);
        for (let w = wires.length - 1; w >= 0; w--)
          if (wires[w][0] === b.id || wires[w][1] === b.id) wires.splice(w, 1);
        if (selected === b.id) selected = null;
        render();
      });
      svg.appendChild(g);
    });
    check();
  }
  svg.addEventListener("mousemove", e => {
    if (!drag) return;
    const b = blockAt(drag.id);
    const [mx, my] = toCanvas(e);
    b.x = mx - drag.dx; b.y = my - drag.dy;
    drag.moved = true;
    render();
  });
  svg.addEventListener("mouseup", () => {
    if (drag && !drag.moved) {           // a click, not a drag: wire or select
      if (selected && selected !== drag.id) {
        const pair = [selected, drag.id];
        const i = wires.findIndex(w => w[0] === pair[0] && w[1] === pair[1]);
        if (i >= 0) wires.splice(i, 1); else wires.push(pair);
        selected = null;
      } else selected = selected === drag.id ? null : drag.id;
      render();
    }
    drag = null;
  });
  document.addEventListener("keydown", e => {
    if ((e.key === "Backspace" || e.key === "Delete") && selected) {
      const i = blocks.findIndex(x => x.id === selected);
      if (i >= 0) blocks.splice(i, 1);
      for (let w = wires.length - 1; w >= 0; w--)
        if (wires[w][0] === selected || wires[w][1] === selected) wires.splice(w, 1);
      selected = null; render(); e.preventDefault();
    }
  });

  // ── the shape checker: the loop discipline as live rules ───────────────────
  function have(kind) { return blocks.some(b => b.kind === kind); }
  function anyModel() { return blocks.some(b => KINDS[b.kind].model); }
  function wired(fromKind, toKind) {
    return wires.some(([a, b]) =>
      blockAt(a)?.kind === fromKind && blockAt(b)?.kind === toKind);
  }
  function check() {
    if (!blocks.length) {
      verdictEl.textContent = "empty canvas — add blocks or load the preset";
      verdictEl.style.color = ""; skeleton([]); return;
    }
    const notes = [];
    if (!have("gate")) notes.push("no gate: nothing refuses for free before the tools spend seconds");
    if (anyModel() && !have("gate")) notes.push("a model node without a gate: proposals and generated code pass the same checks as everything else");
    if (have("eval_fast") && !(have("eval_slow") || have("eval_phys"))) notes.push("analytical without a slow rung: fast numbers ORDER, they must never be quoted");
    if (have("calibrate") && !(have("eval_fast") && have("eval_slow"))) notes.push("calibrate bridges the rungs: slow-rung residuals widen the fast rung's intervals, so it needs both");
    if ((have("eval_slow") || have("eval_phys")) && have("frontier") && !(wired("frontier","eval_slow") || wired("frontier","eval_phys"))) notes.push("the slow rung should take its finalists FROM the frontier");
    if ((have("llm_gen") || have("template")) && !have("eval_test")) notes.push("generated artifacts without test (correctness): nothing counts before its golden vectors pass");
    if (have("records") && have("eval_fast") && !wired("eval_fast","records")) notes.push("measurements should land in records (resume = the record, read back)");
    if (have("extract") && !(wired("records","extract") && wired("extract","propose"))) notes.push("extract mines records into the next prompt: wire records → extract → LLM-propose");
    if (have("feedback") && have("dse") && !wired("feedback","dse")) notes.push("feedback reaches the loop through DSE (advisory; every candidate still gated)");
    if (have("repair") && !(wired("eval_test","repair") || wired("eval_slow","repair"))) notes.push("repair feeds on tool failures: wire a failing evaluator into it");
    if (!have("output")) notes.push("no output: a loop that does not conclude did not happen");
    verdictEl.textContent = notes.length
      ? "⚠ " + notes[0] + (notes.length > 1 ? `  (+${notes.length - 1} more)` : "")
      : "✓ shape holds: gate → fast rung → frontier → slow rung → decide → output, flywheel wired";
    verdictEl.style.color = notes.length ? "#cf7c1c" : "#1e7a3c";
    skeleton(notes);
  }

  // ── skeleton generation ────────────────────────────────────────────────────
  function skeleton(notes) {
    const L = [];
    L.push("# generated by the loop crafter — the scaffold guide/build-your-own.md fills in");
    L.push("def run_study(request, feedback=None, propose=None):");
    if (have("input"))     L.push("    problem = load_ir(request)                      # IO: the stated problem");
    if (have("records"))   L.push("    records = CampaignStore(request.db)             # mentor: resume = the record, read back");
    if (have("knowledge")) L.push("    guidance = knowledge_lookup(problem)            # mentor: licensed, fitted to budget");
    if (have("extract"))   L.push("    facts = records.reflect()                       # mentor: measured facts, as arithmetic");
    if (have("gate")) {
      L.push("    def gated(candidates):                          # gate: refuse in microseconds,");
      L.push("        return [c for c in candidates if admit(c, refused)]   # reasons recorded");
    }
    if (have("dse"))       L.push("    pool = gated(dse_strategy(problem))             # DSE: enumerate / climb / anneal");
    if (have("propose")) {
      L.push("    if propose is not None:                         # LLM-propose: a voice, not an oracle");
      L.push("        pool += gated(propose(guidance, facts, refused))");
    }
    if (have("feedback"))  L.push("    pool = with_notes(pool, feedback.drain())       # mentor: advisory, still gated");
    if (have("template"))  L.push("    artifacts = fill_templates(pool)                # generator: knobs -> configs/RTL");
    if (have("llm_gen"))   L.push("    artifacts += gated(llm_generate(pool))          # generator: invented, never trusted");
    if (have("eval_test")) L.push("    artifacts = [a for a in artifacts if passes_vectors(a)]   # test: correctness first");
    if (have("repair"))    L.push("    artifacts += llm_repair(failures)               # generator: fed the failing inputs");
    if (have("eval_fast")) L.push("    scored = eval_fast(artifacts or pool)           # analytical rung ORDERS, never quoted");
    if (have("calibrate")) L.push("    scored = widen_intervals(scored, residuals(records))  # calibrate: the slow rung teaches the fast one its error");
    if (have("records") && have("eval_fast")) L.push("    records.add(scored)");
    if (have("frontier"))  L.push("    finalists = frontier(scored)                    # both axes, whole");
    if (have("eval_slow")) L.push("    confirmed = eval_slow(finalists + [incumbent])  # the only numbers the report quotes");
    if (have("eval_phys")) L.push("    placed = eval_physical(top(confirmed))          # P&R: area/fmax truth for the picks");
    if (have("decide"))    L.push("    pick = decide(confirmed, rule=request.target)   # knee / target / preserved incumbent");
    if (have("output")) {
      L.push("    return decision_first_report(pick, confirmed,   # IO: measured vs estimated,");
      L.push("                                 refused=refused,   # established vs refused,");
      L.push("                                 lessons=records.lessons() if records else [])");
    }
    if (notes && notes.length) { L.push(""); notes.forEach(n => L.push("# TODO (shape): " + n)); }
    skeletonEl.textContent = L.join("\n");
  }

  const verdictEl = document.getElementById("verdict");
  const skeletonEl = document.getElementById("skeleton");
  document.getElementById("clear").onclick = () => { blocks.length = 0; wires.length = 0; selected = null; render(); };
  document.getElementById("preset").onclick = () => {
    blocks.length = 0; wires.length = 0; nextId = 1;
    // Layout arranged by hand on the canvas and read back -- rows tell the story:
    // inputs and gates on top, the model row, the generator row, the record row,
    // then the evaluation ladder left to right into decide -> output.
    const P = [["input",20,15],["gate",210,15],["feedback",400,15],
               ["knowledge",20,105],["propose",210,105],["dse",400,105],
               ["extract",20,180],["llm_gen",210,180],["template",400,180],
               ["records",20,255],["eval_test",305,255],["repair",495,255],
               ["eval_fast",160,355],["frontier",355,355],["eval_slow",550,355],
               ["calibrate",355,418],
               ["decide",745,355],["output",745,413]];
    P.forEach(([k,x,y]) => addBlock(k,x,y));
    const byKind = k => blocks.find(b => b.kind === k).id;
    [["input","gate"],["gate","dse"],["knowledge","propose"],["records","extract"],
     ["extract","propose"],["feedback","dse"],["dse","template"],
     ["eval_slow","calibrate"],["calibrate","eval_fast"],
     ["propose","llm_gen"],["propose","dse"],["template","eval_test"],["llm_gen","eval_test"],
     ["eval_test","eval_fast"],["eval_test","repair"],["repair","eval_test"],
     ["eval_fast","frontier"],["frontier","eval_slow"],
     ["eval_fast","records"],["eval_slow","records"],["eval_slow","decide"],
     ["decide","output"]].forEach(([a,b]) => wires.push([byKind(a), byKind(b)]));
    render();
  };
  render();
})();
</script>

!!! note "What this is, and is not"
    The crafter teaches the *shape* — which blocks exist, which orders the discipline
    allows, where knowledge and human feedback enter. The skeleton it emits is the same
    scaffold [build your own loop](build-your-own.md) fills in with a real space, real
    gates, and a real evaluator; the crafter will not invent those for you.
