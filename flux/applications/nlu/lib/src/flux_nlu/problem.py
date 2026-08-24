"""The NLU study as a `flux_loop.Problem` (D421): the first client of the shared loop.

Everything problem-specific lives here and nowhere else -- what an operator module
is, how the model is asked for one, the compile-check, the unit-test vectors the
generator iterates against, the exhaustive ULP gate, the table oracle, the composed
`nlu` top, the yosys/OpenROAD stages, the area/fmax frontier and the decision rule.
Everything else -- planning with cooldowns, the generation<->test inner loop with
patching and revert, the record and its read-back, resuming from proven and best
attempts, the cached chain, the conclusion -- is `flux_loop.run_loop`'s, shared
with every other problem.
"""

from __future__ import annotations

import hashlib
import math
import re
import shutil
from typing import Any

import numpy as np

from flux_loop import (BuildError, Candidate, Improve, LoopRequest, LoopState, Problem, Scored,
                       Verdict, prototype_digest)
from flux_loop.observe import _phase

from .fp16 import FORMULAS, OPCODES, all_inputs, describe_failures, ulp_report
from .knowledge import knowledge_text
from .vectors import floor_vectors, merge, parse_authored
from .verify import CompileError, build_sim

__all__ = ["DIFFICULTY", "NluProblem", "op_stub", "wrap_per_op"]

#: Shallowest arithmetic first: the planner's default order and the cooldown menu's.
DIFFICULTY = ("recip", "rsqrt", "sigmoid", "tanh", "gelu", "log", "exp")


def _looks_like_verilog(code: str) -> bool:
    """A prototype reply that is RTL (D491: `module nlu_tanh`, `assign`, `logic [15:0]` in
    the `prototype` field) -- named as such instead of "does not parse"."""
    import re

    return not re.search(r"^\s*def\s+design\s*\(", code, re.M) and bool(
        re.search(r"^\s*(module\s+\w+|assign\s+\w+\s*=|logic\s*\[|always\s*@|endmodule)", code, re.M))


def wrap_per_op(source: str, ops: tuple[str, ...], latencies: dict[str, int] | None = None) -> str:
    """The framework's op mux around per-op modules, so BOTH styles present the one
    `nlu` top to simulation and synthesis. The mux is real hardware an NLU serving
    several operators pays either way; charging it to per-op designs keeps the
    area comparison honest. Operators of different latencies (D496: pipelined parts)
    are EQUALISED to the longest with a delay line on each shorter one's output, so the
    top answers every opcode after the same number of cycles."""
    lat = {op: int((latencies or {}).get(op, 0)) for op in ops}
    top_lat = max(lat.values(), default=0)
    insts, regs = [], []
    for op in ops:
        insts.append(f"  wire [15:0] y_{op}; nlu_{op} u_{op}(.clk(clk), .x(x), .y(y_{op}));")
        d = top_lat - lat[op]
        if d > 0:
            insts.append("  " + " ".join(f"reg [15:0] y_{op}_d{i};" for i in range(1, d + 1)))
            regs += [f"    y_{op}_d{i} <= {f'y_{op}' if i == 1 else f'y_{op}_d{i - 1}'};" for i in range(1, d + 1)]
    cases = "\n".join(f"      3'd{OPCODES[op]}: y = {f'y_{op}_d{top_lat - lat[op]}' if top_lat > lat[op] else f'y_{op}'};"
                       for op in ops)
    return (source + "\n\nmodule nlu(input wire clk, input wire [15:0] x,\n"
            "           input wire [2:0] op, output reg [15:0] y);\n"
            + "\n".join(insts)
            + (("\n  always @(posedge clk) begin\n" + "\n".join(regs) + "\n  end") if regs else "")
            + "\n  always @* begin\n    case (op)\n" + cases +
            "\n      default: y = 16'h7e00;\n    endcase\n  end\nendmodule\n")


def op_stub(op: str) -> str:
    """A NaN-returning placeholder for an operator not yet proven, so the composed
    top still builds and the proven modules can be measured."""
    return (f"module nlu_{op}(input wire clk, input wire [15:0] x, "
            "output wire [15:0] y); assign y = 16'h7e00; endmodule\n")


class NluProblem(Problem):
    name = "nlu"

    def __init__(self, *, ops: tuple[str, ...], ulp_budget: int = 1,
                 clock_period_ps: float = 1250.0, target_mhz: float | None = None,
                 test_rounds: int = 1, seed: int = 0) -> None:
        self.ops = tuple(ops)
        self.ulp_budget = int(ulp_budget)
        self.clock_period_ps = float(clock_period_ps)
        self.target_mhz = target_mhz
        self.test_rounds = int(test_rounds)
        self.seed = int(seed)
        self.vectors: dict[str, np.ndarray] = {}
        self.per_op: dict[str, dict[str, dict]] = {}     # composed name -> op -> report
        self.last_fast: dict[str, dict] = {}             # op -> previous fast-check report (D423)
        self.known_tables: dict[str, dict[str, dict]] = {}  # op -> name -> spec placed once (D473)
        self._floors: dict[str, list] = {}                  # op -> the family search, once (D475)
        self._mentor: Any = None                         # the declared sources (D449)

    # ---- mentor
    def objective(self, request: LoopRequest) -> dict[str, Any]:
        return {"study": "nlu", "ops": list(self.ops), "ulp_budget": self.ulp_budget,
                "clock_period_ps": self.clock_period_ps}

    def tools_missing(self) -> list[str]:
        from .verify import tools_missing

        return tools_missing() + [t for t in ("yosys",) if shutil.which(t) is None]

    def cache_suffix(self) -> str:
        return "nlu.json"

    def from_record(self, doc):
        """Both shapes the campaign holds: the loop's (artifact/subgoal/score) and the
        D412-era rows (source/op/over_budget) -- the seeded `recip` and every
        best-so-far design live in the latter."""
        if "artifact" in doc:
            return super().from_record(doc)
        src, op = doc.get("source"), doc.get("op")
        if not src or op not in self.ops:
            return None
        cand = Candidate(
            name=str(doc.get("name") or op)[:40], artifact=str(src),
            knobs={"style": str(doc.get("style") or "per-op"),
                   "method": str(doc.get("method") or "?")[:40],
                   "latency": int(doc.get("latency") or 0), "op": op},
            meta={"latency": int(doc.get("latency") or 0),
                  "methods": dict(doc.get("methods") or {})},
            subgoal=op)
        over = doc.get("over_budget")
        score = float(over) if isinstance(over, (int, float)) else None
        return cand, score, str(doc.get("why") or "")

    def prepare(self, state: LoopState) -> None:
        """The model's TEST-AUTHOR role: adversarial vectors merged over the coverage
        floor, persisted as campaign events so the suite accumulates across runs."""
        base = floor_vectors(self.seed)
        for op in self.ops:
            self.vectors[op] = base
        rounds_on_record = 0
        if state.records is not None:
            try:
                for e in state.records.store.events(state.records.campaign_id):
                    if e.get("kind") == "authored_vectors":
                        rounds_on_record += 1
                        for op, vals in (e.get("detail") or {}).get("vectors", {}).items():
                            if op in self.vectors:
                                extra = np.array([int(v, 16) for v in vals], dtype=np.uint16)
                                self.vectors[op] = merge(self.vectors[op], extra)
            except Exception:  # noqa: BLE001
                pass
        # what this pass stands on, for the task pane (D487, Cedric: "more details to the
        # knowledge tasks"; D489: "could use more data/text/content" -- the content itself,
        # not counts of it: the vectors, the sheet, the excerpts, the read-back, the blocks)
        import inspect

        from .blocks import BLOCKS

        verified = sorted(k for k in (state.prototypes or {}) if k != "*")
        hexes = lambda vec: " ".join(f"0x{int(v):04x}" for v in vec)  # noqa: E731
        details = {
            "operators": ", ".join(self.ops),
            "reference": "; ".join(f"{op}: y = {FORMULAS.get(op, op)}" for op in self.ops)
                         + " -- float64 from the FP16 input, rounded to nearest-even FP16; "
                         "specials judged by class",
            "gate": f"every one of the 65536 FP16 inputs, {self.ulp_budget} ULP faithful; "
                    f"clock {self.clock_period_ps:.0f} ps",
            "fast-check suite": "; ".join(f"{op}: {self.vectors[op].size} vectors" for op in self.ops)
                                + f" (floor {base.size} from seed {self.seed}"
                                + (f" + {rounds_on_record} authored round(s) on record)" if rounds_on_record else ")"),
            "suite floor": f"{base.size} vectors -- +/-0, the subnormal and normal limits, Inf, NaN, "
                           f"and every exponent both signs with several mantissas each: {hexes(base)}",
            "verified prototypes": ", ".join(verified) or "(none yet)",
            "admitted": ", ".join(sorted(k for k in state.admitted if k != "*")) or "(none yet)",
            "toolkit": f"{len(BLOCKS)} blocks"
                       + (f" + verified operators {', '.join(verified)} as blocks" if verified else "")
                       + ": " + ", ".join(f"{fn.__name__}{inspect.signature(fn)}" for fn in BLOCKS),
        }
        for op in self.ops:                            # what the author added on top of the floor
            extra = np.setdiff1d(self.vectors[op], base)
            if extra.size:
                details[f"suite {op}, authored"] = f"{extra.size} on record: {hexes(extra)}"
        try:
            from .knowledge import knowledge_text

            mentor = self.knowledge()
            srcs = [type(s).__name__ for s in getattr(mentor, "sources", [])]
            details["knowledge"] = (f"methods sheet {len(knowledge_text())} chars; mentor sources: "
                                    + ", ".join(srcs) + " (the sheet, the operator's papers, the "
                                    "record's read-back -- read once per run)")
            for key, title in (("sheet", "knowledge: methods sheet"),
                               ("library", "knowledge: library excerpts"),
                               ("record", "knowledge: record read-back")):
                text = mentor.text(key, state)
                details[title] = text if text.strip() else "(nothing to say)"
        except Exception:  # noqa: BLE001
            pass
        if state.proposer is None or self.test_rounds <= 0:
            details["test author"] = "off (no model or no rounds)"
            return details
        from flux_profile import phase

        from .invent import test_author_prompt

        for round_ in range(self.test_rounds):
            human = state.drain()
            with phase("author: adversarial vectors", why=f"round {round_ + 1}") as out:
                try:
                    reply = state.proposer.propose(
                        test_author_prompt(ops=self.ops, human=human))
                except Exception as exc:  # noqa: BLE001
                    state.not_established.append(f"test-author round did not run ({exc})")
                    return
                authored, bad = parse_authored(reply)
                for b in bad:
                    state.refused.append(("test-author", b))
                added = 0
                for op, vec in authored.items():
                    if op in self.vectors:
                        before = self.vectors[op].size
                        self.vectors[op] = merge(self.vectors[op], vec)
                        added += self.vectors[op].size - before
                out["authored"] = ", ".join(f"{op}: {vec.size}" for op, vec in authored.items()) or "(none)"
                out["merged"] = f"{added} new vector(s) over the floor"
                if bad:
                    out["refused"] = "; ".join(bad)[:2000]
            if authored and state.records is not None:
                try:
                    state.records.store.append_event(
                        state.records.campaign_id, "authored_vectors",
                        {"vectors": {op: [f"0x{int(v):04x}" for v in vec]
                                     for op, vec in authored.items()}})
                except Exception:  # noqa: BLE001
                    pass
            state.say(f"test author: {added} new adversarial vector(s) joined the suite")
            state.lessons.append(f"[author] the model added {added} adversarial "
                                 "vector(s) to the unit-test suite (kept in the record)")
        details["test author"] = f"{self.test_rounds} round(s) this pass; suite now " + "; ".join(f"{op}: {self.vectors[op].size}" for op in self.ops)
        return details

    def knowledge(self):
        """The mentor's three sources, declared once (D449): the method sheet, the operator's
        own papers, and the record's read-back with its duels. `mentor_sections` (the tab) and
        `prompt_prefix` (the static block) are both assembled from these by the loop, and the
        sheet and the library are read ONCE per run instead of once per generation turn."""
        if self._mentor is None:
            from .flow import mentor_for

            self._mentor = mentor_for(self.ops)
        return self._mentor

    # ---- orchestrator
    def subgoals(self) -> list[str]:
        return [o for o in DIFFICULTY if o in self.ops] + \
               [o for o in self.ops if o not in DIFFICULTY]

    def plan_prompt(self, menu, state, human):
        from .invent import plan_prompt, plan_schema

        partials = {sg: why[:80] for sg, (_s, _c, why) in state.best.items() if sg in menu}
        return (plan_prompt(todo=list(menu), admitted=sorted(state.admitted),
                            partials=partials, human=human,
                            prototype=state.request.prototype), plan_schema(list(menu)))

    # ---- generator
    def prompt_prefix(self, subgoal, state):
        """Knowledge sheet, library, contract, reply shape -- static per operator, so
        the loop sends it first and the server reuses its cache (D422). The sheet and the
        library come from the declared sources (D449), read once for the run."""
        from .invent import op_static_prefix

        mentor = self.knowledge()
        return op_static_prefix(subgoal, knowledge=mentor.text("sheet", state),
                                library=mentor.text("library", state))

    def prototype_prefix(self, subgoal, state):
        """The prototype stage's static prefix: sheet, library, the PROTOTYPE contract --
        no RTL interface, no Verilog reply shape (D491)."""
        from .invent import op_prototype_prefix

        mentor = self.knowledge()
        return op_prototype_prefix(subgoal, knowledge=mentor.text("sheet", state),
                                   library=mentor.text("library", state))

    def design_prompt(self, subgoal, method, state, human, prior, prior_why):
        from .invent import design_op_prompt, design_schema

        return (design_op_prompt(
            subgoal, ulp_budget=self.ulp_budget, knowledge=knowledge_text(),
            method=method, human=human,
            prior_source=(prior.artifact if prior else ""), prior_why=prior_why,
            with_static=False),
            design_schema(subgoal))

    def parse_design(self, reply, subgoal):
        from .invent import parse_design, parse_structured_design

        cand, why = parse_structured_design(reply, subgoal)
        if cand is None:
            cand, why2 = parse_design(reply, ops=(subgoal,))
            why = why if cand is None else None
        if cand is None:
            return None, why or "unparseable"
        return self._to_candidate(cand, subgoal), ""

    @staticmethod
    def _to_candidate(cand: dict[str, Any], subgoal: str) -> Candidate:
        return Candidate(
            name=str(cand.get("name") or f"{subgoal}_design")[:40],
            artifact=str(cand["source"]),
            knobs={"style": "per-op", "method": str(cand.get("method", "?"))[:40],
                   "latency": int(cand.get("latency", 0)), "op": subgoal},
            meta={"latency": int(cand.get("latency", 0)),
                  "tables": list(cand.get("tables") or [])},
            subgoal=subgoal)

    # ---- generator: the floor (D475) for an operator that has a family, the model otherwise
    def generator(self, subgoal, state):
        """`Template` from `flux_nlu.floor` when the request asks for floors and the operator
        has a family: the configuration search runs once per operator (seconds), the
        cheapest member at 0 over is the draft, the next ones are tried if the gate ever
        disagrees with the numpy face. Everything else is the default (the rig's slot)."""
        from flux_loop.sources import Template

        from .floor import FAMILIES, search

        if not state.request.params.get("floor") or subgoal not in FAMILIES:
            return super().generator(subgoal, state)
        op = subgoal

        def render(attempt):
            found = self._floors.get(op)
            if found is None:
                from flux_profile import phase

                with phase(f"generate: floor search {op}", why="every configuration, all inputs") as out:
                    found = search(op, budget=self.ulp_budget)
                    out["result"] = (f"{len(found)} of {found[0]['tried'] if found else '?'} "
                                     "configurations at 0 over")
                self._floors[op] = found
                state.say(f"  floor {op}: {len(found)} of {found[0]['tried'] if found else '?'} "
                          f"configurations reach 0 over; cheapest {found[0]['config'] if found else '-'}")
            if attempt.index >= len(found):
                return None, (f"the {op} family has {len(found)} configuration(s) at 0 over "
                              "and every one was tried")
            pick = found[attempt.index]
            cfg = pick["config"]
            src = FAMILIES[op]["rtl"](cfg)
            label = ",".join(f"{k}={v}" for k, v in cfg.__dict__.items())
            return Candidate(
                name=f"floor_{op}[{label}]"[:40], artifact=src,
                knobs={"style": "per-op", "method": "floor: reduce+table+linear", "latency": 0,
                       "op": op},
                meta={"latency": 0, "tables": [], "floor": dict(cfg.__dict__),
                      "floor_cost": pick["cost"]},
                subgoal=op)

        return Template(render=render, name="floor")

    def patch_prompt(self, subgoal, cand, failure, state):
        from flux_loop import focus_window

        from .invent import patch_prompt, patch_schema

        view = focus_window(cand.artifact, self.locate(failure, cand.artifact),
                            state.request.patch_context_lines)
        return patch_prompt(subgoal, cand.artifact, failure, view=view), patch_schema()

    def rewrite_prompt(self, subgoal, cand, failure, state):
        from .invent import design_schema, op_repair_prompt

        return (op_repair_prompt(subgoal, cand.artifact, failure, with_static=False),
                design_schema(subgoal))

    def apply_tools(self, subgoal, cand, reply, state):
        """The table oracle (D420) and the hygiene pass (D473), after every design and
        every patch: render every valid table request in the reply and place it inside
        the module; re-place a table this operator already had when the source calls it
        and no longer defines it; then `wire`/`reg` -> `logic` and the `clk` port. What
        was adjusted or refused goes back to the model as failure text."""
        from .hygiene import ensure_clk_port, normalize_rtl
        from .invent import parse_reply_tables
        from .tables import inject_tables, parse_table_specs, render_table

        source = cand.artifact
        module = f"nlu_{subgoal}"
        raw = parse_reply_tables(reply) or list(cand.meta.get("tables") or [])
        raw = [t for t in raw if isinstance(t, dict) and "func" in t]
        specs, refused = parse_table_specs(raw) if raw else ([], [])
        # ORACLE MEMORY (D473): a rewrite that dropped its tables used to cost one compile
        # round per name ("use of undeclared identifier 'TWO_POW_F'"); the harness has
        # the spec and the exact values, so a call to a known table is a request.
        known = self.known_tables.setdefault(subgoal, {})
        asked = {sp.name for sp in specs}
        recalled = []
        for name, doc in known.items():
            if (name not in asked and re.search(rf"\b{name}\s*\(", source)
                    and not re.search(rf"\bfunction\b[^\n]*\b{name}\s*\(", source)):
                got, _bad = parse_table_specs([doc])
                if got:
                    specs.append(got[0])
                    recalled.append(name)
        rendered = []
        for spec in specs:
            try:
                rendered.append(render_table(spec))
            except ValueError as exc:
                refused.append(f"table {spec.name}: {exc}")
        if rendered:
            try:
                source = inject_tables(source, module, rendered)
            except ValueError as exc:
                refused.append(str(exc))
                rendered = []
        placed = specs[:len(rendered)]
        for sp in placed:
            known[sp.name] = sp.to_dict()
        if rendered:
            state.say(f"  oracle: {len(rendered)} table(s) computed and placed in "
                      f"{module}: " + ", ".join(sp.name for sp in placed)
                      + (f" (re-placed from an earlier attempt: {', '.join(recalled)})"
                         if recalled else ""))
        source, hygiene = normalize_rtl(source)
        source, clk_note = ensure_clk_port(source, module)
        if clk_note:
            hygiene.append(clk_note)
        if hygiene:
            state.say("  hygiene: " + "; ".join(hygiene))
        adjusted = [a for sp in placed for a in sp.adjusted]
        notes = []
        if adjusted:
            notes.append("table requests ADJUSTED by the harness (the table is placed as "
                         "described in its header; fix the receiving signal): " + "; ".join(adjusted))
        if refused:
            notes.append("table requests refused: " + "; ".join(refused))
        note = "\n".join(notes)
        for n in notes:
            state.say(f"  {n}")
        if source == cand.artifact and not note:
            return cand, ""
        return cand.with_artifact(source, tables=[sp.to_dict() for sp in placed]), note

    # ---- prototype (D424): the algorithm in numpy, judged by the gate's own ULP report
    _PROTO_HARNESS = (
        "\n_xs = np.arange(65536, dtype=np.int64)\n"
        "with np.errstate(all='ignore'):\n    _ys = np.asarray(design(_xs), dtype=np.int64) & 0xFFFF\n"
        "_ys = _ys.astype(np.uint16)\n"
        "assert _ys.shape == _xs.shape, f'design returned shape {_ys.shape} for {_xs.shape[0]} inputs'\n"
        "for _k, _v in list(globals().items()):\n"
        "    if not _k.startswith('_') and isinstance(_v, np.ndarray) and _v.ndim == 1 and 1 < _v.size <= 1024 and _v.dtype.kind in 'iu' and int(np.abs(_v).max()) >= (1 << 20):\n"
        "        _DIAG.append(f'{_k}: its values reach 2^{int(np.abs(_v).max()).bit_length() - 1}; an FP16 operator rarely needs a table value above 2^16 -- if it was built with rom(lambda t: f(t) * 2**K, ...), drop the * 2**K: rom scales by 2^frac_bits itself')\n"
        "for _d in list(dict.fromkeys(_DIAG))[:4]: print('DIAG ' + _d.replace(chr(10), ' '))\n"
        "print('TABLES ' + ';'.join(f'{_k}[{_v.size}]={int(_v.min())}..{int(_v.max())}' for _k, _v in list(globals().items()) if not _k.startswith('_') and isinstance(_v, np.ndarray) and _v.ndim == 1 and 1 < _v.size <= 1024 and _v.dtype.kind in 'iu'))\n"
        "print('OUT ' + _ys.astype('<u2').tobytes().hex())\n")

    def prototype_spec(self, subgoal, state):
        from .blocks import operator_docs, tool_docs

        ops = self._operators(subgoal, state)

        ref = {"exp": "e^x", "log": "ln(x)", "sigmoid": "1/(1+e^-x)", "tanh": "tanh(x)",
               "gelu": "x*Phi(x)", "recip": "1/x", "rsqrt": "1/sqrt(x)"}[subgoal]
        prompt = (
            f"PROTOTYPE FIRST. Before any Verilog, write the `{subgoal}` algorithm as a "
            f"Python function `design(x)` that takes a numpy int64 array of FP16 bit "
            f"patterns (0..65535) and returns an integer array of FP16 bit patterns for {ref}, "
            f"within {self.ulp_budget} ULP of the correctly rounded result on ALL 65536 "
            "inputs (NaN in -> any NaN; Inf handled exactly; subnormals in and out are real).\n"
            "RULES so it transcribes to hardware 1:1: use only integer and bit operations "
            "on numpy integer arrays -- shifts, masks, integer multiply/add, comparisons, "
            "np.where, small lookup tables you build from integer arithmetic (you may compute "
            "table constants with float math ONCE at definition time, as a ROM would be "
            "generated). No float arithmetic in the data path, no np.exp/np.log on the inputs. "
            "Decide the fixed-point formats (bit widths, Q positions) explicitly and keep them "
            "consistent; rounding must be round-to-nearest-even where it matters.\n"
            "The harness calls design() on all 65536 inputs and judges it exactly as the RTL "
            "gate does; you will get the failures by input region with examples, and a "
            "PROGRESS line each turn. Use the compute tool to check pieces in isolation.\n"
            "YOU NEVER WRITE THE VERILOG: a prototype at 0 over is TRANSPILED to SystemVerilog "
            "by the harness, every signal width measured over all inputs. For that it must "
            "stay in the subset: integers only; `for` only over a constant range; helper "
            "functions are inlined; tables are 1-D constant arrays indexed by an integer "
            "expression; division only by a constant, and `//` of a value that can be negative "
            "only by a power of two. WRITE IT FOR ONE INPUT if you like: `if`/`elif`/`else`, an "
            "early `return`, `and`/`or`/`not`, `min`/`max`/`abs` on values are all fine -- the "
            "harness converts them to array form itself (only a `while` on data cannot be: use "
            "a fixed `for _ in range(N)`). np.where and the array idioms are equally fine. A "
            "prototype outside the subset is refused with the construct named; keep its "
            "numerics and rewrite that construct.\n"
            "TOOLS -- verified blocks ALREADY DEFINED in your prototype's namespace; call them. "
            "Do not paste, redefine or re-derive any of them (a `def` with a block's name is "
            "refused), do not test your design at module level (the harness does), and put no "
            "print in it. The transpiler inlines the blocks and the gate checks the whole:\n"
            + tool_docs() + "\n"
            + (("VERIFIED OPERATORS of this campaign, blocks too (an FP16 pattern in, an FP16 "
                "pattern out; compose them with fp16_add/fp16_mul/fp16_neg -- each rounds to "
                "FP16, so a composition may need a wider intermediate to stay within 1 ULP):\n"
                + operator_docs(ops) + "\n") if ops else "")
            + "Constants: BIAS, QNAN, PINF, NINF, PZERO, NZERO, ONE, HALF, TWO. COMPOSE: classify with is_*(), "
            "normalise with mantissa11()/exponent_unbiased(), convert with to_fixed()/"
            "signed_fixed(), reduce the argument with integer arithmetic and const_fixed(), "
            "table it with rom()/slope_rom() + lookup()/interp1() on the REDUCED argument, then "
            "pack_fp16() (rounding, carry, Inf, subnormals) and specials(). A whole operator is "
            "10-25 lines this way; spend your effort on the algorithm and the knobs.\n"
            "THE SHAPE every prototype takes (fill in the algorithm; nothing else is needed):\n"
            "    T = 6\n    M = 13\n"
            "    SPACE = {\"T\": [5, 6, 7], \"M\": [12, 13, 14]}\n"
            "    TABLE = rom(<function of the REDUCED argument>, lo, hi, 2 ** T, M)   # built once\n"
            "    def design(x):\n"
            "        if is_nan(x): return QNAN          # or specials(...) at the end -- either\n"
            "        s, e, m = fp16_sign(x), exponent_unbiased(x), mantissa11(x)\n"
            "        ...   # reduce the argument, index TABLE, correct: integer arithmetic only\n"
            "        # or, for a function of the significand: v = approx_on_mantissa(f, m, T, M)\n"
            "        y = pack_fp16(s_out, e_out, v, frac_bits)   # round, carry, Inf, subnormals\n"
            "        return specials(x, QNAN, <+Inf ->>, <-Inf ->>, <+0 ->>, <-0 ->>, y)\n"
            "DO NOT GUESS SIZES -- DECLARE KNOBS. Anything you would otherwise pick by feel "
            "(table index bits, table value bits, fixed-point widths, correction bits, a "
            "constant's precision) goes in module-level integer constants with a SPACE of "
            "choices, e.g. `T = 6`, `M = 13`, `CB = 6` and "
            "`SPACE = {\"T\": [5, 6, 7], \"M\": [12, 13, 14], \"CB\": [0, 4, 6]}`; read them "
            "inside design() or in helpers it calls. The harness runs EVERY member (up to 256) "
            "on all 65536 inputs, reports the search, and SELECTS the cheapest member at 0 "
            "over by measured widths -- that member is the prototype from then on. List the "
            "likeliest choices first.")
        redesign = (getattr(state, "_redesign", None) or {}).get(subgoal)
        if redesign:
            prompt += "\n\n" + redesign                       # D499: a DIFFERENT algorithm, asked for
        hist = self.prototype_history(subgoal, state)
        if hist:
            prompt += "\n\n" + hist                           # D500
        prompt += self._example_prototype(subgoal, state)     # D501
        from flux_loop import prototype_schema

        return prompt, prototype_schema()

    @staticmethod
    def _rounding_residue(code: str, rep: dict) -> str:
        """The diagnosis a few small, scattered failures in a design that ROUNDS TO FP16 more
        than once deserve (D488: sigmoid sat at 106 over for two passes -- 1 to 3 ULP off,
        across regions, the report calling it "a sign-dependent step"). Each `<op>_fp16(...)`,
        `from_fixed(...)` or extra `pack_fp16(...)` rounds to 11 bits; in series they cannot
        meet a 1-ULP gate, and no edit to the logic will change that. Empty when the
        failures are not that shape."""
        import re

        n_fail = int(rep.get("over_budget", 0) or 0)
        max_ulp = rep.get("max_ulp")
        if not (0 < n_fail <= 2000) or not isinstance(max_ulp, int) or max_ulp > 6:
            return ""
        roundings = (len(re.findall(r"\b(?!pack_fp16\b)\w+_fp16\s*\(", code))       # exp_fp16(...) ...
                     + len(re.findall(r"\bfp16_(?:add|sub|mul|from_int)\s*\(", code))
                     + len(re.findall(r"\bfrom_fixed\s*\(|\bpack_fp16\s*\(", code)))
        if roundings < 2:
            return ""
        return (f"pattern: {n_fail} failures, none more than {max_ulp} ULP off, spread across regions "
                f"-- the PRECISION FLOOR of a design that rounds to FP16 {roundings} times "
                "(each *_fp16(...), from_fixed(...) or pack_fp16(...) rounds to 11 bits; in series "
                "the errors add and no edit to the logic removes them). To reach 0 over keep the "
                "intermediate values in FIXED POINT (a table value, a sum or product, a reciprocal "
                "via recip_fixed(v, frac_bits, out_frac_bits), a root via approx_on_mantissa on the "
                "normalised value) with enough fraction bits that the SMALLEST result still holds "
                "12+ significant bits (a result down to 2^-k needs k+12 of them; 16 is not enough "
                "for outputs near 2^-14 -- a small output must not be a small integer), and pack "
                "to FP16 exactly ONCE at the end with from_fixed(v, frac_bits). The LOGIC is right "
                f"(only {n_fail} inputs fail, by <= {max_ulp} ULP): keep the range reduction, the "
                "tables and the saturation exactly as they are and EDIT only the steps that round "
                "-- a rewrite starts over from thousands of failures")

    @staticmethod
    def _group_sites(items: list[str]) -> list[str]:
        """"line 35: X", "line 36: X" -> "lines 35, 36: X" (D482: a scalar-style design broke
        one rule eight times; eight copies of the sentence teach less than one with the sites)."""
        import re

        groups: dict[str, list[int]] = {}
        order: list[str] = []
        for it in items:
            m = re.match(r"line (\d+): (.*)", it, re.S)
            if not m:
                groups.setdefault(it, []); order.append(it) if it not in groups or not groups[it] else None
                continue
            msg = m.group(2)
            if msg not in groups:
                groups[msg] = []
                order.append(msg)
            groups[msg].append(int(m.group(1)))
        out = []
        for msg in order:
            lines = sorted(set(groups[msg]))
            if not lines:
                out.append(msg)
            elif len(lines) == 1:
                out.append(f"line {lines[0]}: {msg}")
            else:
                out.append(f"lines {', '.join(map(str, lines))}: {msg}")
        return out

    @staticmethod
    def _hand_rolled(code: str) -> list[str]:
        """Advice, not a refusal: a function that finds the leading one, shifts and calls
        pack_fp16 has re-derived `from_fixed` by hand with its own exponent constant (D494:
        gelu's 3,310 design carried `e_out = l - 28`; raising the table's M to 16 -- the right
        move -- turned it into 34,751 because the constant did not follow). Named in the
        report's audit section, next to what the blocks found on the data."""
        import ast

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []
        out = []
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            called = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            if {"leading_one", "pack_fp16"} <= called and "from_fixed" not in called:
                out.append(f"{fn.name}(): leading_one + a shift + pack_fp16 is from_fixed re-derived by hand, "
                           "with an exponent constant that breaks whenever a Q format changes (M, the "
                           "fixed-point width of x, the product's) -- call from_fixed(v, frac_bits, sign) "
                           "with the value's fraction bits (a product's is the sum of its factors') and "
                           "the Q arithmetic follows every knob")
        return out

    @staticmethod
    def _explain_error(text: str, offset: int = 0, linemap: dict[int, int] | None = None,
                       n_pre: int | None = None, code: str | None = None) -> str:
        """A sandbox error in the model's coordinates with the idiom it needs (D482): apex's
        thinking attempts died on `if m == 0:`, `max(0, min(30, e))`, a bare name -- each a
        one-line fix once named; a raw traceback with prelude line numbers named nothing."""
        import difflib
        import re

        from .blocks import BLOCKS, relocate

        if offset:                                     # the sandbox's own preamble lines
            text = re.sub(r"\bline (\d+)", lambda m: f"line {int(m.group(1)) - offset}", text)
        text = relocate(text, n_pre)
        if linemap:                                    # array form -> the model's own lines
            from .vectorize import relocate_lines

            text = relocate_lines(text, linemap)
        hints = []
        if "truth value of an array" in text:
            hints.append("design() runs on ALL 65536 inputs at once as one integer array: no `if`, "
                         "`and`, `or`, `min`, `max`, `abs` on data -- np.where(cond, a, b) or "
                         "select(), `&`/`|`, np.minimum/np.maximum/np.abs, and comparisons give "
                         "arrays")
        if ("not supported for the input types" in text and any(
                k in text for k in ("shift", "bitwise", "invert", "remainder", "floor_divide"))) \
                or ("unsupported operand type" in text and "'float'" in text
                    and any(op in text for op in ("<<", ">>", "&", "|", "^", "%", "//"))):
            hints.append("a FLOAT reached a shift or a mask: a table built as a Python list of "
                         "floats (build it with rom(...) -- integers), a `/` (use `//`), or a bare "
                         "float constant (wrap it with const_fixed(value, bits)); everything on the "
                         "data path must be an integer array")
        if "unsupported operand type" in text and "tuple" in text:
            hints.append("a block that returns TWO values was used as one -- "
                         "normalize_significand(v, frac_bits) returns (v_normalised, shift): "
                         "unpack it, `v, sh = normalize_significand(...)`, and SUBTRACT sh from "
                         "the exponent")
        if "0-dimensional arrays can be converted" in text or "only length-1 arrays" in text:
            hints.append("a `math.` function was applied to a whole array (a table builder's "
                         "lambda) -- the table functions are vectorised: rom(gelu, ...), "
                         "rom(erf, ...), rom(lambda t: 0.5 * t * (1 + erf(t / 1.4142135623730951)), "
                         "...), or np.vectorize(math.erf) once at module level")
        m = re.search(r"'numpy\.ndarray' object has no attribute '(\w+)'", text)
        if m:
            alt = {"bit_length": "leading_one(v) + 1 (the position of the leading one, per element)",
                   "is_integer": "nothing: the data path is integers already"}.get(
                       m.group(1), "the toolkit's blocks (a Python int method has no per-element form)")
            hints.append(f"`.{m.group(1)}()` is a Python scalar method; on the data array use {alt}")
        if "too many values to unpack" in text or "not enough values to unpack" in text:
            hints.append("a block returns ONE array unless its doc says otherwise: rom(...) is the table, "
                         "slope_rom(...) is the slopes (two calls, two names); only "
                         "normalize_significand(v, frac_bits) returns a pair")
        if "Integers to negative integer powers are not allowed" in text:
            hints.append("`2 ** -k` on integers -- a negative power is a right SHIFT: `v >> k` (or "
                         "`const_fixed(2.0 ** -k, bits)` for a constant scale); nothing on the data path is "
                         "a fraction")
        if "arrays used as indices must be of integer" in text:
            hints.append("a FLOAT array reached a table index -- a `/` (use `//`), a float constant "
                         "(const_fixed(value, bits)), or a table built from floats upstream of the index; "
                         "the index is an integer array from shifts and masks")
        if "only integer scalar arrays can be converted to a scalar index" in text:
            hints.append("a Python LIST was indexed with the data array -- tables are numpy arrays "
                         "built ONCE at module level, rom(func, lo, hi, entries, frac_bits) / "
                         "slope_rom(...) (or np.array([...])), indexed with an integer array; a "
                         "list comprehension inside design() is not hardware")
        if "unhashable type" in text:
            hints.append("a dict or set was indexed or tested with data (`TABLES[e]`, `e in "
                         "TABLES`) -- hardware has no dict of ROMs: concatenate the tables into "
                         "one ROM and index it with (case << T) | idx, or look the value up in "
                         "each table and select() between the VALUES")
        if "could not be broadcast" in text or "design returned shape" in text:
            hints.append("every intermediate must be one value per input (shape (65536,)); a "
                         "Python list, tuple, a table used as a value instead of indexed, or a table "
                         "indexed with a 2-D index slipped in -- print .shape of the suspects in a "
                         "compute snippet")
        m = re.search(r"(\w+)\(\) (?:got an unexpected keyword argument|takes|missing) ", text)
        if m and m.group(1) in {fn.__name__ for fn in BLOCKS}:            # a block miscalled
            import inspect

            fn = next(f for f in BLOCKS if f.__name__ == m.group(1))
            hints.append(f"the block's signature is {fn.__name__}{inspect.signature(fn)}")
        m = re.search(r"name '(\w+)' is not defined", text)
        if m:
            names = [fn.__name__ for fn in BLOCKS] + ["BIAS", "QNAN", "PINF", "NINF", "PZERO", "NZERO",
                                                       "exp2", "exp", "ln", "log2", "recip", "rsqrt",
                                                       "sqrt", "sigmoid", "tanh"]
            close = difflib.get_close_matches(m.group(1), names, n=2)
            inside = None
            if code:                                     # D494: `TABLE = ...` indented under a helper
                for i, ln in enumerate(code.splitlines(), 1):
                    if re.match(rf"\s+{re.escape(m.group(1))}\s*=", ln):
                        inside = i
                        break
            if inside:
                hints.append(f"`{m.group(1)}` IS assigned, on line {inside} -- but indented, inside a "
                             "function, so design() cannot see it: dedent that line to module level")
            else:
                hints.append(f"`{m.group(1)}` is neither a block nor defined in your prototype"
                             + (f"; did you mean {' or '.join(close)}?" if close else
                                "; define it at module level or use a block"))
        m = re.search(r"index (-?\d+) is out of bounds for axis 0 with size (\d+)", text)
        if m:
            neg = m.group(1).startswith("-")
            edge = m.group(1) == m.group(2)
            hints.append(f"a table of {m.group(2)} entries was indexed with {m.group(1)}: "
                         + (f"the input at the range's UPPER edge lands one past the last entry -- clip the "
                            f"index, np.clip(idx, 0, {int(m.group(2)) - 1}), or saturate before the table "
                            "(the edge belongs to the last segment)"
                            if edge else
                            "EVERY path runs on EVERY input in array form, so an index computed for "
                            "inputs another branch handles (a small-x shortcut, a saturation) must "
                            "still be in range -- clip it, np.clip(idx, 0, len(TABLE) - 1), before "
                            "indexing; the select() that discards those results comes after"
                            if neg else
                            f"mask the index to its {max(1, int(m.group(2)) - 1).bit_length()} bits, "
                            "or the argument reduction is not producing the range the table was "
                            "built for"))
        return text + ("".join("\n  -> " + h for h in hints) if hints else "")

    def _operators(self, subgoal, state) -> dict[str, str]:
        """The campaign's VERIFIED prototypes of the OTHER parts, in array form, as blocks the
        part being designed may call (D487: sigmoid = recip(1 + exp(-x)))."""
        from .vectorize import vectorize

        out: dict[str, str] = {}
        for op, code in (getattr(state, "prototypes", None) or {}).items():
            if op == subgoal or op == "*" or not isinstance(code, str) or not code.strip():
                continue
            try:
                out[op] = vectorize(code)[0]
            except Exception:  # noqa: BLE001
                continue
        return out

    def describe_tests(self, subgoal) -> str:
        """The tests a part is judged by, for the results tab (D487): the exhaustive gate,
        the ULP rule, and the authored fast-check vectors with their expected outputs."""
        from .fp16 import reference

        op = subgoal or ""
        lines = [f"gate: every one of the 65536 FP16 inputs; y within {self.ulp_budget} ULP of "
                 f"the correctly rounded reference (faithful rounding); NaN in -> any NaN; Inf "
                 "and zero exact; subnormals real"]
        vec = self.vectors.get(op)
        if vec is not None and len(vec):
            xs = np.asarray(vec, dtype=np.uint16)
            want = reference(op, xs)
            lines.append(f"fast-check vectors ({len(xs)}; the model authored the adversarial ones):")
            for x, w in zip(xs[:64], want[:64]):
                lines.append(f"  x=0x{int(x):04x} ({float(np.uint16(x).view(np.float16)):+.4g}) -> "
                             f"0x{int(w):04x} ({float(np.uint16(w).view(np.float16)):+.4g})")
            if len(xs) > 64:
                lines.append(f"  ... {len(xs) - 64} more")
        return "\n".join(lines)

    def prototype_history(self, subgoal, state, limit: int = 8) -> str:
        """What this part's earlier passes reached, from the record (D500: context that was
        missing while 7,000 characters of library excerpts were not): each recorded
        prototype's score and the first line of its report, best first, deduplicated --
        directions, not instructions."""
        rec = getattr(state, "records", None)
        if rec is None or getattr(rec, "store", None) is None:
            return ""
        seen: dict[float, str] = {}
        try:
            for t in rec.store.trials(rec.campaign_id):
                c = t.candidate or {}
                if t.stage != "prototype" or (c.get("meta") or {}).get("kind") != "prototype":
                    continue
                if (c.get("subgoal") or "*") != (subgoal or "*"):
                    continue
                sc = c.get("score")
                if not isinstance(sc, (int, float)):
                    continue
                text = str(c.get("why") or "")
                why = text.splitlines()
                first = next((ln for ln in why if ln.startswith(("pattern:", "0 over;"))), None)
                if first is None:
                    first = next((ln for ln in why if "pattern:" in ln), None)
                if first is None:
                    first = next((ln for ln in why if not ln.startswith(("PROGRESS", "FAMILY", "  "))), why[0] if why else "")
                first = first[first.find("pattern:"):] if "pattern:" in first else first
                seen.setdefault(float(sc), first[:140])
        except Exception:  # noqa: BLE001
            return ""
        if not seen:
            return ""
        rows = sorted(seen.items())[:limit]

        def label(sc: float, why: str) -> str:
            if why.startswith("0 over"):
                return f"  0 over, logic depth {sc:g}: {why[len('0 over;'):].strip()}"
            return f"  {sc:g} over: {why}" if why else f"  {sc:g} over"
        return ("HISTORY of this part on record, best first (\"N over\" = inputs beyond the ULP budget; "
                "\"0 over, logic depth D\" = a passing design and its depth proxy):\n"
                + "\n".join(label(sc, why) for sc, why in rows))

    def prototype_reminder(self, subgoal, state):
        from .blocks import BLOCKS

        import inspect

        sigs = ", ".join(f"{fn.__name__}{inspect.signature(fn)}" for fn in BLOCKS)
        ops = self._operators(subgoal, state)
        hist = self.prototype_history(subgoal, state)
        return ("THE BLOCKS THAT EXIST (call them; nothing else is provided -- any other name "
                f"must be defined in your prototype): {sigs}; constants BIAS, QNAN, PINF, NINF, "
                "PZERO, NZERO, ONE, HALF, TWO; table functions exp2, exp, ln, log2, recip, rsqrt, sqrt, "
                "sigmoid, tanh, erf, gelu (or any lambda)"
                + (f"; VERIFIED OPERATORS: {', '.join(f'{op}_fp16(x)' for op in sorted(ops))}" if ops else "")
                + ". Per-element style (`if`/`return` on one input) is fine."
                + (f"\n\n{hist}" if hist else ""))

    def prototype_check(self, code, subgoal, state):
        from flux_loop import Verdict, run_compute

        from .prototype_rules import hardware_subset_violations

        # The subset the prompt asks for, CHECKED (D468): a prototype that calls the reference
        # function passes 0 over and transcribes to nothing -- the live run spent three days
        # transcribing `np.exp(x.view(np.float16))`. Refused before it runs, with each site
        # named and what to do instead, so the refusal teaches.
        from .blocks import misuse, prelude_lines, relocate, with_prelude

        ops = self._operators(subgoal, state)
        n_pre = prelude_lines(ops)

        # COMPOSE, DON'T RE-DERIVE (D482): a block defined again by hand, or a self-test at
        # module level, is refused before the subset rules -- named with the lines to delete.
        fought = misuse(code, {f"{op}_fp16" for op in ops})
        if fought:
            return Verdict(False, float("inf"),
                           "prototype fights the toolkit instead of composing it:\n  "
                           + "\n  ".join(fought[:8])
                           + (f"\n  ... {len(fought) - 8} more" if len(fought) > 8 else ""),
                           {"toolkit_misuse": fought})
        # ARRAY FORM (D483): per-element `if`/`return`/`and`/`min` become np.where and friends
        # before anything runs or judges; every message below is mapped back to the model's
        # lines. The model's text stays the prototype it edits.
        from .vectorize import VectorizeError, relocate_lines, vectorize

        try:
            vec, linemap = vectorize(code)
        except VectorizeError as exc:
            return Verdict(False, float("inf"), f"prototype cannot be made array form: {exc}")
        except SyntaxError:
            vec, linemap = code, {}                   # the rules report the parse error
        loc = lambda text: relocate_lines(relocate(text, n_pre), linemap)   # noqa: E731
        full = with_prelude(vec, ops)                  # the toolkit's blocks are the helpers
        if _looks_like_verilog(code):
            return Verdict(False, float("inf"),
                           "this is SystemVerilog -- the prototype stage wants PYTHON: a "
                           "`design(x)` on integer arrays of FP16 bit patterns, built from the "
                           "blocks; the RTL module is transpiled from it by the harness once it "
                           "passes. Send the algorithm as Python.")
        broken = [r for r in (loc(b) for b in hardware_subset_violations(full))
                  if "toolkit line" not in r]         # the toolkit's own lines are not the model's
        broken = self._group_sites(broken)             # one line per rule, every site listed
        if broken:
            return Verdict(False, float("inf"),
                           "prototype is not hardware-implementable (it must transcribe to "
                           "masks, shifts, integer arithmetic and tables built once at "
                           "module level):\n  " + "\n  ".join(broken[:8])
                           + (f"\n  ... {len(broken) - 8} more" if len(broken) > 8 else ""),
                           {"hardware_subset": broken})
        # THE FAMILY SEARCH (D479): knobs declared as module-level constants with a SPACE of
        # choices are searched exhaustively in one sandboxed pass; the cheapest member at 0
        # over (by the transpiler's measured widths) is bound and becomes the prototype.
        search_note = ""
        payload_extra: dict = {}
        from .family import (bind, configurations, describe_search, family_harness,
                             parse_family_output, space_of)

        space = space_of(code)
        if space:
            from flux_loop import screen_snippet

            refused = screen_snippet(code)                 # the model's text, screened first
            if refused:
                return Verdict(False, float("inf"), f"prototype refused: {refused}")
            members, cap_note = configurations(space)
            (name, out), = run_compute(
                [{"name": "family", "code": family_harness(full, subgoal, members, self.ulp_budget,
                                                           prelude_lines=n_pre)}],
                timeout_s=max(120.0, state.request.compute_timeout_s * 6 * max(1, len(members) // 8)),
                max_chars=600_000, trusted=True)           # the harness is the framework's
            overs, best, hexout = parse_family_output(out)
            measured = {i: v for i, v in overs.items() if isinstance(v, int)}
            if not measured:
                return Verdict(False, float("inf"),
                               "the family produced no result: "
                               + self._explain_error(describe_search(members, overs, None, cap_note),
                                                     linemap=linemap, n_pre=n_pre))
            zero = sorted(i for i, v in measured.items() if v == 0)
            cost: dict[int, int] = {}
            if zero:
                from .transpile import TranspileError, cost_estimate

                for i in zero[:24]:
                    try:
                        cost[i] = cost_estimate(with_prelude(vectorize(bind(code, members[i]))[0], ops))
                    except (TranspileError, VectorizeError):
                        cost[i] = 1 << 30                      # unspellable: refused below
                picked = min(zero[:24], key=lambda i: cost[i])
            else:
                picked = min(measured, key=lambda i: measured[i])
            search_note = describe_search(members, overs, picked, cap_note, cost)
            state.say("  " + search_note.replace("\n", "\n  "))
            code = bind(code, members[picked])
            vec, linemap = vectorize(code)
            loc = lambda text: relocate_lines(relocate(text, n_pre), linemap)   # noqa: E731
            full = with_prelude(vec, ops)
            payload_extra = {"prototype": code, "search": search_note, "member": members[picked]}
        from flux_loop import screen_snippet

        refused = screen_snippet(code)                     # the model's text is screened ...
        if refused:
            return Verdict(False, float("inf"), f"prototype refused: {refused}")
        from .blocks import audit_harness

        (name, out), = run_compute([{"name": "prototype", "code": full + audit_harness() + self._PROTO_HARNESS}],
                                   timeout_s=max(30.0, state.request.compute_timeout_s * 6),
                                   max_chars=600_000, trusted=True)   # ... the harness is ours
        line = next((ln for ln in out.splitlines() if ln.startswith("OUT ")), None)
        if line is None:
            from flux_loop import preamble_lines

            tail = out[-1000:]
            if len(out) > 1000:                        # cut at a line, not mid-word
                tail = tail[tail.find("\n") + 1:]
            return Verdict(False, float("inf"), "prototype did not produce output: "
                           + self._explain_error(tail, offset=preamble_lines(), linemap=linemap, n_pre=n_pre, code=code))
        tables = next((ln[7:] for ln in out.splitlines() if ln.startswith("TABLES ")), "")
        if tables:
            payload_extra["tables"] = tables
        diag = [ln[5:] for ln in out.splitlines() if ln.startswith("DIAG ")]
        diag += self._hand_rolled(code)
        if diag:
            payload_extra["audit"] = diag
        try:
            got = np.frombuffer(bytes.fromhex(line[4:].strip()), dtype="<u2").astype(np.uint16)
        except Exception as exc:  # noqa: BLE001
            return Verdict(False, float("inf"), f"prototype output unreadable: {exc}")
        xs = all_inputs()
        if got.shape != xs.shape:
            return Verdict(False, float("inf"), f"prototype returned {got.size} values for {xs.size} inputs")
        rep = ulp_report(subgoal, xs, got, budget=self.ulp_budget)
        rep.update(payload_extra)
        residue = self._rounding_residue(code, rep)
        if residue:
            rep["pattern"] = residue                     # the precision floor, named (D488)
            # the internal pack audits are noise here; a from_fixed one is the floor's cause (D489)
            rep["audit"] = [a for a in rep.get("audit", []) if a.startswith("from_fixed:")]
        previous = self.last_fast.get(f"proto:{subgoal}")
        self.last_fast[f"proto:{subgoal}"] = rep
        if rep["ok"]:
            # 0 over -- and TRANSPILABLE (D478): a prototype the transpiler cannot spell is
            # refused here, naming the construct, so the prototype stage fixes it instead of
            # the RTL stage falling back to transcription
            from .transpile import TranspileError, logic_depth, transpile

            try:
                transpile(full, top=f"nlu_{subgoal}", xs=xs)
                depth = logic_depth(full, xs=xs)
            except TranspileError as exc:
                return Verdict(False, float("inf"),
                               (search_note + "\n" if search_note else "")
                               + f"the prototype passes 0 over but the transpiler cannot spell it: "
                               f"{loc(str(exc))}. Rewrite that construct in the subset (masks, shifts, integer "
                               f"arithmetic, comparisons, np.where, np.minimum/maximum/clip, tables "
                               f"indexed by an integer, division only by a constant); keep the "
                               f"numerics exactly as they are.", rep)
            rep["depth"] = depth
            depth_txt = self._depth_text(depth)
            goal = self._optimising(state).get(subgoal)
            if goal:
                # OPTIMISE mode (D496): 0 over is the gate, the logic depth is the score
                d, d0, tgt = depth["depth"], goal["depth"], goal["target"]
                ok = d <= tgt
                why = (f"0 over; {depth_txt} -- was {d0} when this optimisation began, the goal is "
                       f"<= {tgt} ({'REACHED' if ok else 'not yet'}). " + self._faster_advice())
                return Verdict(ok, float(d), (search_note + "\n" if search_note else "") + why, rep)
            return Verdict(True, 0.0, (search_note + "\n" if search_note else "") + depth_txt, rep)
        goal = self._optimising(state).get(subgoal)
        return Verdict(False, float(rep["over_budget"]) + (1e6 if goal else 0.0),
                       (search_note + "\n" if search_note else "")
                       + f"{rep['over_budget']} of 65536 beyond {self.ulp_budget} ULP"
                       + (" (the best member of the family)." if search_note else ". ")
                       + describe_failures(subgoal, rep, previous=previous)
                       + ("\nOPTIMISING for depth: 0 over stays the gate -- this edit broke the numerics; "
                          "the depth counts only once every input passes again" if goal else ""), rep)

    def score_unit(self, subgoal, state) -> str:
        """What a prototype score IS in the trend line (D503: four replies in a day began
        "0 over (meaning 0 failures? No, wait...)"): inputs beyond the budget, or, while a part
        is being made faster, levels of logic depth (a broken edit is 1e6 + its failures)."""
        return (" levels of logic depth (0 over; a score above 1e6 is 1e6 + failing inputs)"
                if self._optimising(state).get(subgoal) else " inputs beyond the ULP budget")

    @staticmethod
    def _optimising(state) -> dict:
        """{op: {"depth": d0, "target": d}} while a part is being made faster (D496)."""
        return getattr(state, "_optimise", None) or {}

    @staticmethod
    def _depth_text(depth: dict) -> str:
        from .transpile import logic_depth_note

        path = ", ".join(f"{b} {n}" for b, n in depth.get("path", [])[:6])
        note = logic_depth_note(depth)
        return (f"logic depth ~{depth['depth']} levels on the critical path ({path}) -- the "
                "proxy synthesis is measured against; fewer levels, higher fmax"
                + (f"; {note}" if note else ""))

    @staticmethod
    def _faster_advice() -> str:
        return ("Make it SHALLOWER with the same numerics: a while loop unrolled into a chain of "
                "steps runs in series -- replace it with a binary search of constant shifts and "
                "selects; a shift by a data-dependent amount is a barrel shifter -- a constant shift "
                "when the amount is known; a multiply is ~3 log2(W) levels -- narrower factors, or a "
                "table; a normalisation by hand is deeper than from_fixed; a division by a signal is "
                "a divider -- a reciprocal table; the blocks are already shallow. ONE construct per "
                "EDIT: replace the deepest thing the path names, keep everything else, re-verify, "
                "then the next -- a rewrite loses the 0 over that took a campaign to reach.")

    def transpile(self, prototype, subgoal, state, pipeline: int | None = None):
        """The verified prototype as `nlu_<op>` RTL, widths measured over every input
        (D478); None when the transpiler refuses -- `prototype_check` already required
        it, so that is a defect to say, not a path. `pipeline` (D496) is the register count
        the transpiler cuts the design into -- the prototype's own `PIPELINE` when None."""
        from .transpile import TranspileError, pipeline_stages, transpile

        from .blocks import with_prelude

        try:
            from .vectorize import vectorize

            full = with_prelude(vectorize(prototype)[0], self._operators(subgoal, state))
            k = pipeline_stages(full) if pipeline is None else int(pipeline)
            if pipeline is None and not k:
                # an ADMITTED part keeps its register count (D504): a shallower prototype for
                # it, spelled unpipelined, measured slower than the design it improves on
                # until the sweep ran again; the sweep is what changes the count
                admitted = state.admitted.get(subgoal)
                k = int(((admitted.meta or {}) if admitted is not None else {}).get("pipeline", 0) or 0)
            src = transpile(full, top=f"nlu_{subgoal}", xs=all_inputs(), pipeline=k,
                            note="the model's prototype composed from the toolkit; nothing here "
                                 "was written by hand")
        except TranspileError as exc:
            state.say(f"  transpile {subgoal}: refused after the prototype passed ({exc}) -- "
                      "the check and the transpiler disagree; a defect")
            return None
        return Candidate(
            name=f"transpiled_{subgoal}" + (f"_p{k}" if k else ""), artifact=src,
            knobs={"style": "per-op", "method": "transpiled prototype" + (f", {k} pipeline registers" if k else ""),
                   "latency": k, "op": subgoal},
            meta={"latency": k, "tables": [], "transpiled": True, "pipeline": k,
                  "prototype_sha": prototype_digest(prototype)}, subgoal=subgoal)   # ITS prototype (D504)

    # ---- evaluator
    def build(self, cand, subgoal, state):
        from .hygiene import explain_compile_error

        try:
            return build_sim(cand.artifact, top=f"nlu_{subgoal}",
                             latency=int(cand.meta.get("latency", 0)), opcode=None,
                             workdir=state.workdir)
        except CompileError as exc:
            raise BuildError(explain_compile_error(str(exc), cand.artifact)) from exc

    def fast_check(self, built, cand, subgoal, state):
        xs = self.vectors.get(subgoal)
        if xs is None or not len(xs):
            xs = floor_vectors(self.seed)
        from flux_profile import phase

        with phase(f"test: fast vectors ({subgoal})", why=f"{len(xs)} vectors",
                   design=cand.name) as out:
            got = built.run(xs)
            rep = ulp_report(subgoal, xs, got, budget=self.ulp_budget)
            previous = self.last_fast.get(subgoal)
            self.last_fast[subgoal] = rep
            # The task pane shows the verdict beside the call (D470): the counts, the trend
            # against the previous check of this operator, and the failure text the model
            # gets -- regions and decoded worst cases.
            out["verdict"] = (f"{'PASSES' if rep['ok'] else 'FAILS'}: {rep['over_budget']} of "
                              f"{rep['n']} beyond {self.ulp_budget} ULP, max {rep['max_ulp']}, "
                              f"{rep['pct_exact']:.1%} exact")
            if previous is not None:
                out["trend"] = (f"previous check {previous['over_budget']} over -> now "
                                f"{rep['over_budget']}")
            if not rep["ok"]:
                out["failures"] = describe_failures(subgoal, rep, previous=previous)
        if rep["ok"]:
            return 0, ""
        return rep["over_budget"], ("it COMPILES but fails the unit-test vectors. "
                                    + out["failures"])

    def judge(self, built, cand, subgoal, state):
        xs = all_inputs()
        from flux_profile import phase

        with phase(f"prove: exhaustive ULP ({subgoal})", why=cand.name, inputs=len(xs)) as out:
            got = built.run(xs)
            rep = ulp_report(subgoal, xs, got, budget=self.ulp_budget)
            out["verdict"] = (f"{'PROVEN' if rep['ok'] else 'REFUSED'}: {rep['over_budget']} of "
                              f"{rep['n']} beyond {self.ulp_budget} ULP, max {rep['max_ulp']}, "
                              f"{rep['pct_exact']:.1%} exact, mean {rep['mean_ulp']:.3f} ULP")
            if not rep["ok"]:
                out["failures"] = describe_failures(subgoal, rep)
        if rep["ok"]:
            return Verdict(True, 0.0, "", rep)
        why = (f"{rep['over_budget']} of {rep['n']} beyond {self.ulp_budget} ULP. "
               + out["failures"])
        return Verdict(False, float(rep["over_budget"]), why, rep)

    def compose(self, admitted, state):
        """Proven per-op modules plus NaN stubs for the rest, under the framework's
        mux -- re-proved through that top, so a latency mismatch cannot slip past."""
        if not admitted:
            return None
        modules = [admitted[op].artifact if op in admitted else op_stub(op)
                   for op in self.ops]
        lats = {o: int(admitted[o].meta.get("latency", 0)) for o in admitted}
        latency = max(lats.values(), default=0)
        top = wrap_per_op("\n\n".join(modules), self.ops, lats)
        name = f"composed[{', '.join(sorted(admitted))}]"
        per_op: dict[str, dict] = {}
        xs = all_inputs()
        from flux_profile import phase

        for op in sorted(admitted):
            with phase(f"prove: composed top ({op})", why=name, inputs=len(xs)) as out:
                try:
                    sim = build_sim(top, top="nlu", latency=latency, opcode=OPCODES[op],
                                    workdir=state.workdir)
                    rep = ulp_report(op, xs, sim.run(xs), budget=self.ulp_budget)
                except Exception as exc:  # noqa: BLE001
                    out["error"] = str(exc)[:2000]
                    state.refused.append((name, f"composed top failed on {op}: {exc}"))
                    return None
                out["verdict"] = (f"{'ok' if rep['ok'] else 'FAILS'}: {rep['over_budget']} of "
                                  f"{rep['n']} beyond {self.ulp_budget} ULP through the mux, "
                                  f"max {rep['max_ulp']}")
            per_op[op] = rep
            if not rep["ok"]:
                state.refused.append((name, f"{op} fails through the composed top: "
                                            f"{rep['over_budget']} over budget"))
                return None
        self.per_op[name] = per_op
        return Candidate(name, top, knobs={"style": "shared", "method": "per-op agentic",
                                           "latency": latency},
                         meta={"latency": latency,
                               "methods": {op: admitted[op].knobs.get("method", "?")
                                           for op in admitted}})

    def stages(self) -> list[str]:
        return ["screen"] + (["confirm"] if shutil.which("openroad") else [])

    def measure(self, cand, stage, state):
        from flux_evaluator_openroad import run_ppa_flow, run_synthesis_flow

        # a single PART measured on its own (D496: an improved part before it is composed
        # again) is wrapped with its one opcode; the composition with all of them
        ops = (cand.subgoal,) if cand.subgoal in self.ops else self.ops
        top = cand.artifact if cand.knobs.get("style") == "shared" else \
            wrap_per_op(cand.artifact, ops)
        latency = int(cand.meta.get("latency", 0))
        kw = dict(clock_port="clk" if latency > 0 else None,
                  clock_period_ps=self.clock_period_ps)
        try:
            rep = (run_synthesis_flow(top, "nlu", **kw) if stage == "screen" else
                   run_ppa_flow(top, "nlu", flow_depth="placement", repair_design=True,
                                **kw))
        except Exception as exc:  # noqa: BLE001
            state.refused.append((cand.name, f"{stage} failed: {type(exc).__name__}: "
                                            f"{str(exc)[:200]}"))
            return None
        return rep.metrics()

    def prefers_edits(self, subgoal, state, best_score, new_text):
        """A rewrite is refused (D501) when the text in hand fails fewer than 5% of the inputs
        or is a 0-over design being made faster -- unless a DIFFERENT algorithm was asked for
        (D499's redesign), or the rewrite declares the approach wrong in its `why`."""
        if (getattr(state, "_redesign", None) or {}).get(subgoal):
            return None
        if not (best_score == best_score) or best_score == float("inf"):       # nothing in hand
            return None
        opt = self._optimising(state).get(subgoal)
        near = (best_score < 0.05 * 65536) if not opt else (best_score < 1e6)
        if not near:
            return None
        return (f"the text in hand {'passes every input' if opt else f'fails only {best_score:g} of 65536 inputs'}; "
                "a rewrite starts over from tens of thousands of failures (measured: rewrites were the "
                "regressions) -- send {\"edits\": [...]} that change the one thing the report names. "
                "If the approach itself is wrong, say so in `why` with the word REWRITE and send the "
                "prototype again")

    def _example_prototype(self, subgoal, state) -> str:
        """The campaign's SHORTEST verified prototype of another part, verbatim, as the example
        of the shape and the idioms (D501): the flow's own product, not a hand-written one --
        what a passing design looks like beats a page of rules."""
        others = {op: code for op, code in (state.prototypes or {}).items() if op != subgoal and op in self.ops}
        if not others:
            return ""
        op, code = min(others.items(), key=lambda kv: len(kv[1]))
        if len(code) > 2500:
            return ""
        return (f"\n\nA VERIFIED PROTOTYPE OF THIS CAMPAIGN ({op}, 0 over on every input, transpiled and admitted) "
                "-- the shape and the idioms, not the algorithm:\n" + code.strip())

    # ---- what the campaign is for, for the results table (D497)
    def objectives(self, state):
        goal = self._fmax_goal()
        out: dict = {
            "goal": (f"an FP16 NLU of {len(self.ops)} operators ({', '.join(self.ops)}): each within "
                     f"{self.ulp_budget} ULP of the reference on ALL 65536 inputs, composed under one op mux, "
                     f"at >= {goal:.0f} MHz (clock {self.clock_period_ps:.0f} ps) with the least area and power"),
        }
        # the whole design: its last measured numbers
        last = next((sc for sc in reversed(state.scored or []) if sc.candidate.name.startswith("composed[")), None)
        if last is not None:
            m = last.metrics or {}
            lat = int((last.candidate.meta or {}).get("latency", 0))
            out["composed"] = (f"{m.get('area_um2', 0):,.0f} um2 · {m.get('fmax_mhz', 0):.0f} MHz"
                               f" (goal {goal:.0f}) · {m.get('power_w', 0) * 1000:.0f} mW · latency {lat}"
                               f" · {last.stage} stage" + (" -- MEETS THE GOAL" if m.get("fmax_mhz", 0) >= goal else ""))
        # what this pass is doing now
        opt = self._optimising(state)
        trying = getattr(state, "_trying", None)
        known = getattr(state, "_fmax", None) or {}
        todo = [op for op in self.ops if op not in state.admitted]
        red = getattr(state, "_redesign", None) or {}
        if red:
            op = next(iter(red))
            out["now"] = (f"pushing fmax: {op} redesigned with a DIFFERENT algorithm"
                          + (f" (the record's runs at {known[op]:.0f} MHz alone)" if op in known else "") + "; 0 over is the gate")
        elif opt:
            op, g = next(iter(opt.items()))
            out["now"] = (f"pushing fmax: {op} to the model for its logic depth {g['depth']} -> goal <= {g['target']}"
                          + (f" ({known[op]:.0f} MHz alone)" if op in known else "") + "; 0 over stays the gate")
        elif trying and trying[0] in todo:
            out["now"] = f"proving {trying[0]}: a prototype within {self.ulp_budget} ULP on every input" + (f" via {trying[1]}" if trying[1] else "")
        elif todo:
            out["now"] = f"proving the parts: {len(state.admitted)}/{len(self.ops)} proven, {', '.join(todo)} to go"
        elif state.improve:
            out["now"] = "pushing fmax: " + ", ".join(f"{it.subgoal}" for it in state.improve[:3]) + " queued for the pipeline sweep and the model"
        elif last is None:
            out["now"] = "all parts proven; composing and measuring the whole design"
        else:
            out["now"] = ("the whole design meets the goal" if (last.metrics or {}).get("fmax_mhz", 0) >= goal
                          else "all parts proven and measured; the slowest go back for fmax when the pass climbs")
        # each part: its constraint and numbers
        parts: dict[str, str] = {}
        for op in self.ops:
            bits = [f"y = {FORMULAS.get(op, op)}, {self.ulp_budget} ULP"]
            cand = state.admitted.get(op)
            if cand is not None:
                k = int((cand.meta or {}).get("pipeline", 0))
                bits.append(f"{k} registers" if k else "combinational")
                if op in known:
                    areas = getattr(state, "_area", None) or {}
                    bits.append(f"{known[op]:.0f} MHz alone" + (f", {areas[op]:.0f} um2" if op in areas else ""))
                d = self._part_depth(op, state)
                if d:
                    bits.append(f"depth {d['depth']}")
            elif op in (getattr(state, "proto_best", None) or {}):
                bits.append(f"best {state.proto_best[op][0]:g} over")
            parts[op] = " · ".join(bits)
        out["parts"] = parts
        return out

    # ---- the push for fmax (D496)
    def _fmax_goal(self) -> float:
        """The clock the request asks for, as a frequency: `--target-mhz`, else the period."""
        return float(self.target_mhz) if self.target_mhz else 1e6 / float(self.clock_period_ps)

    def _part_depth(self, op: str, state) -> dict | None:
        """The logic-depth proxy of a part's verified prototype (cached on the state by text)."""
        proto = (state.prototypes or {}).get(op)
        if not proto:
            return None
        cache = getattr(state, "_depths", None)
        if cache is None:
            cache = {}
            try:
                state._depths = cache                   # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        key = (op, hashlib.sha256(proto.encode()).hexdigest()[:16])
        if key not in cache:
            from .blocks import with_prelude
            from .transpile import TranspileError, logic_depth
            from .vectorize import vectorize

            try:
                cache[key] = logic_depth(with_prelude(vectorize(proto)[0], self._operators(op, state)),
                                         xs=all_inputs())
            except (TranspileError, Exception):  # noqa: BLE001
                cache[key] = None
        return cache[key]

    def route(self, stage, scored, state):
        """Once every part is proven and the composition measured below the clock it was
        asked for (D496, Cedric: "once the base loop completed we want to push for better
        fmax"), the DEEPEST parts go back to the generator with the numbers: the
        composition's fmax against the goal, this part's logic depth and where its critical
        path runs. An improved part, measured on its own and still below the goal, goes back
        again -- the chain of improvements the loop's improve edge exists for."""
        goal = self._fmax_goal()
        back: list[Improve] = []
        for sc in scored:
            fmax = (sc.metrics or {}).get("fmax_mhz")
            if fmax is None or fmax >= goal:
                continue
            name = sc.candidate.name
            if name.startswith("composed["):
                from flux_profile import phase

                known = getattr(state, "_fmax", None) or {}            # per-part fmax measured alone
                for op in self.ops:                                     # D498: every part on its own, cached
                    if op in known or state.admitted.get(op) is None:
                        continue
                    with phase(f"screen: {op} alone", why="its own fmax and area, cached by artifact") as out:
                        m = self._screen_alone(state.admitted[op], state)
                        out["measured"] = (f"{m['fmax_mhz']:.0f} MHz, {m.get('area_um2', 0):.0f} um2"
                                           if m else "could not be synthesised")
                known = getattr(state, "_fmax", None) or {}
                depths = {op: d for op in self.ops if (d := self._part_depth(op, state))}
                queued = {it.subgoal for it in (state.improve or [])}          # once in the queue is enough
                todo = [op for op in depths if known.get(op, 0.0) < goal and op not in queued]
                # the slowest first: by its own measured fmax when known, else by depth
                todo.sort(key=lambda o: (known.get(o, 0.0) if o in known else -1e9,
                                         -depths[o]["depth"] / (1 + int((state.admitted.get(o).meta if state.admitted.get(o) else {}).get("pipeline", 0)))))
                for op in todo[:2]:
                    cand = state.admitted.get(op)
                    if cand is None:
                        continue
                    back.append(Improve(cand, subgoal=op, stage=stage, why=(
                        f"the composition of all {len(self.ops)} operators runs at {fmax:.0f} MHz on the "
                        f"{stage} stage against the {goal:.0f} MHz asked for (clock {self.clock_period_ps:.0f} ps); "
                        f"the slowest part sets the clock and {op} is among the slowest"
                        + (f" ({known[op]:.0f} MHz alone)" if op in known else "") + ": "
                        + self._depth_text(depths[op]))))
            elif sc.candidate.subgoal in self.ops:
                op = sc.candidate.subgoal
                d = self._part_depth(op, state)
                back.append(Improve(sc.candidate, subgoal=op, stage=stage, why=(
                    f"{op} alone runs at {fmax:.0f} MHz on the {stage} stage against the {goal:.0f} MHz "
                    f"asked for" + (f"; {self._depth_text(d)}" if d else ""))))
        return back

    PIPELINE_SWEEP = (2, 4, 8, 16)

    def _screen_alone(self, cand: Candidate, state) -> dict | None:
        """A PART synthesised on its own through the loop's measurement cache (D498: the same
        artifact is never synthesised twice across runs) -- its fmax and area, remembered on
        the state for the route's order and the results table."""
        from flux_loop.measure import cached_measure

        try:
            m = cached_measure(self, state, cand, "screen")
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(m, dict) or "fmax_mhz" not in m:
            return None
        known = getattr(state, "_fmax", None) or {}
        known[cand.subgoal] = float(m["fmax_mhz"])
        state._fmax = known                                  # type: ignore[attr-defined]
        areas = getattr(state, "_area", None) or {}
        areas[cand.subgoal] = float(m.get("area_um2", 0.0))
        state._area = areas                                  # type: ignore[attr-defined]
        return m

    def _pipeline_sweep(self, op: str, proto: str, state):
        """The mechanical half of the frequency (D496): the verified prototype transpiled
        with 2, 4, 8, 16 register stages, each judged bit-exact and synthesised alone; the
        first that reaches the goal wins, else the fastest. Nothing the model wrote changes."""
        from flux_profile import phase

        goal = self._fmax_goal()
        best: tuple[float, Candidate, Any] | None = None
        tried: dict[int, tuple[float, float]] = {}
        with phase(f"pipeline: {op}", why="register stages by logic level, each synthesised") as out:
            def one(k: int):
                cand = self.transpile(proto, op, state, pipeline=k)
                if cand is None:
                    return None
                try:
                    built = self.build(cand, op, state)
                    v = self.judge(built, cand, op, state)
                except Exception as exc:  # noqa: BLE001
                    out[f"{k} registers"] = f"could not be judged: {exc!s:.120}"
                    return None
                if not v.ok:
                    out[f"{k} registers"] = f"NOT bit-exact ({(v.why or '')[:100]}) -- a transpiler defect, not a design"
                    return None
                m = self._screen_alone(cand, state) or {}
                fmax, area = m.get("fmax_mhz"), m.get("area_um2")
                if fmax is None:
                    out[f"{k} registers"] = "bit-exact; could not be synthesised"
                    return None
                tried[k] = (fmax, area or 0.0)
                out[f"{k} registers"] = f"bit-exact; {fmax:.0f} MHz, {area:.0f} um2"
                return fmax, cand, built

            lo_k = 0
            for k in self.PIPELINE_SWEEP:
                got = one(k)
                if got is None:
                    continue
                if best is None or got[0] > best[0]:
                    best = got
                if got[0] >= goal:
                    # the LEAST registers that meet the goal (D498: least area at the clock):
                    # bisect between the last count below it and this one
                    hi_k = k
                    while hi_k - lo_k > 1:
                        mid = (lo_k + hi_k) // 2
                        g2 = one(mid)
                        if g2 is not None and g2[0] >= goal:
                            hi_k, best = mid, g2
                        else:
                            lo_k = mid
                    break
                lo_k = k
            if best is not None:
                out["picked"] = f"{best[1].name}: {best[0]:.0f} MHz" + (" (the least registers that reach the goal)" if best[0] >= goal else " (the fastest; below the goal)")
                known = getattr(state, "_fmax", None) or {}
                known[op] = best[0]
                state._fmax = known                              # type: ignore[attr-defined]
        return best

    def improve(self, item, state):
        """A part sent back for speed (D496). First the mechanical half: pipeline registers
        by logic level, swept and synthesised -- when a register count reaches the goal the
        part is done without a model. Then, for a part already pipelined and still slow, the
        algorithmic half: its verified prototype is the seed, 0 over stays the gate, and the
        prototype stage's score is the LOGIC DEPTH -- the pass succeeds when the depth falls
        by a fifth. The improved prototype is transpiled, judged bit-exact and replaces the
        admitted design; the old one stands when nothing shallower is found."""
        op = item.subgoal
        proto = (state.prototypes or {}).get(op)
        if op not in self.ops or not proto:
            return None, None, f"{op} has no verified prototype to make faster"
        admitted = state.admitted.get(op)
        if admitted is None or not int((admitted.meta or {}).get("pipeline", 0)):
            best = self._pipeline_sweep(op, proto, state)
            if best is not None:
                fmax, cand, built = best
                state.say(f"  pipeline {op}: {cand.name} at {fmax:.0f} MHz"
                          + (" reaches the goal" if fmax >= self._fmax_goal() else " (the fastest register count; the model takes the depth next)"))
                return cand, built, ""
        # D504: a shallower 0-over design the record already holds -- a depth pass that stopped
        # short of its goal (tanh's 177 levels, found four times over while the 196 stood) --
        # is taken before another pass is spent on finding it again
        taken = self._take_shallower_on_record(op, proto, state, item.why)
        if taken is not None:
            return taken
        # the ladder's rung counts live in the RECORD (D503: they were per run, and every
        # relaunch handed tanh another depth pass instead of the redesign it was due)
        digest = hashlib.sha256(proto.encode()).hexdigest()[:16]
        if self._passes_on_record(state, "depth_pass", op, digest) >= 1:
            # D499 (Cedric: "the tool should also consider ... rewrite/change the RTL/Python
            # as there are multiple algorithms and maybe the current one is a bad pick"): the
            # current algorithm has been pipelined and has had a depth pass and is still
            # slow -- ask for a DIFFERENT one, from scratch, under the same 0-over gate
            return self._redesign(op, proto, state, item.why)
        self._note_pass(state, "depth_pass", op, digest)
        d = self._part_depth(op, state)
        if not d:
            return None, None, f"{op}: its depth could not be measured"
        d0 = int(d["depth"])
        goal = {"depth": d0, "target": max(1, int(d0 * 0.8))}
        opt = getattr(state, "_optimise", None) or {}
        opt[op] = goal
        state._optimise = opt                                 # type: ignore[attr-defined]
        keep = state.prototypes.pop(op)
        keep_seed = state.proto_best.pop(op, None)
        state.proto_best[op] = (float(d0), proto, item.why)     # the seed, with the numbers
        state.say(f"  optimise {op}: logic depth {d0} -> goal <= {goal['target']} (0 over stays the gate)")
        cand = None
        try:
            cand, built, reason = self.generate(op, f"shallower than {d0} levels", state, item.why)
            if cand is None:
                # D504: the goal is where the PASS may stop, not the price of admission. A
                # 0-over design shallower than the seed is a win the run paid for (tanh's
                # depth pass found 177 levels from 196 and the 196 stood); it is transpiled
                # and gated like one that reached the goal, and the next pass starts from it
                best = state.proto_best.get(op)
                if best and math.isfinite(best[0]) and best[0] < d0 and best[1].strip() and best[1] != proto:
                    state.say(f"  optimise {op}: the pass stopped short of {goal['target']} but found "
                              f"{best[0]:g} levels at 0 over (from {d0}) -- taking it")
                    cand, built, reason = self._take(op, best[1], best[0], d0, state,
                                                     f"the pass's goal was <= {goal['target']}")
            return cand, built, reason
        finally:
            opt.pop(op, None)
            if keep_seed is not None:
                state.proto_best[op] = keep_seed
            else:
                state.proto_best.pop(op, None)
            if cand is None:
                state.prototypes[op] = keep                   # the old design stands

    def _take_shallower_on_record(self, op: str, proto: str, state, why: str):
        """The shallowest 0-over prototype on record for `op` below the admitted design's
        depth (D504): a depth pass's refused best, recorded with "0 over; logic depth ~N"
        as its report. Re-checked under today's rules, then taken as the part's verified
        prototype and transpiled like a pass that reached its goal; None when there is none."""
        rec = getattr(state, "records", None)
        if rec is None or getattr(rec, "store", None) is None:
            return None
        d = self._part_depth(op, state) or {}
        d0 = int(d.get("depth") or 0)
        if not d0:
            return None
        best: tuple[float, str] | None = None
        try:
            for t in rec.store.trials(rec.campaign_id):
                c = t.candidate or {}
                if t.stage != "prototype" or (c.get("meta") or {}).get("kind") != "prototype" or c.get("subgoal") != op:
                    continue
                sc, report = c.get("score"), str(c.get("why") or "")
                if t.status == "ok" or not isinstance(sc, (int, float)) or not (0 < sc < d0):
                    continue
                if "logic depth" not in report or "0 over" not in report:
                    continue                                  # an over-count, not a depth
                code = str(c.get("artifact") or "")
                if code.strip() and code != proto and (best is None or sc < best[0]):
                    best = (float(sc), code)
        except Exception:  # noqa: BLE001
            return None
        if best is None:
            return None
        v = self.prototype_check(best[1], op, state)          # today's rules: 0 over on every input
        if not v.ok:
            state.say(f"  optimise {op}: the {best[0]:g}-level design on record no longer passes today's check; not taken")
            return None
        bound = (v.payload or {}).get("prototype") if isinstance(v.payload, dict) else None
        code = bound if isinstance(bound, str) and bound.strip() else best[1]
        state.say(f"  optimise {op}: the record holds a {best[0]:g}-level design at 0 over (the admitted one is {d0}) -- taking it")
        cand, built, reason = self._take(op, code, best[0], d0, state, "taken from the record")
        if cand is None:
            state.prototypes[op] = proto                      # the old design stands
            return None
        return cand, built, reason

    def _take(self, op: str, code: str, depth: float, d0: int, state, how: str):
        """A shallower 0-over prototype becomes the part (D504): recorded as its VERIFIED
        prototype (the reload re-spells the admitted RTL from it, D495 -- left as "refused"
        the old design would come back at the relaunch), transpiled at the ADMITTED design's
        register count so the standings compare like with like (unpipelined it would measure
        slower than the design it improves on until the sweep ran again), built and
        fast-checked. A transpiled text that fails its check is a transpiler defect, said as
        one; the old design stands and no model turn is spent transcribing."""
        from flux_loop.prototype import _record_prototype

        state.prototypes[op] = code
        _record_prototype(state, op, code, Verdict(True, depth, f"0 over, logic depth {depth:g} (from {d0}; {how})"), ok=True)
        with _phase(f"generate: transpile {op}", why="the shallower prototype, at the part's register count") as out:
            out["prototype"] = code[:6000]
            cand = self.transpile(code, op, state)             # the admitted count, see `transpile`
            if cand is not None:
                out["artifact"] = (f"{cand.name}: {(cand.artifact or '').count(chr(10))} lines\n" + (cand.artifact or "")[:6000])
        k = int((cand.meta or {}).get("pipeline", 0) or 0) if cand is not None else 0
        if cand is None:
            state.prototypes.pop(op, None)
            return None, None, f"{op}: the {depth:g}-level prototype could not be transpiled"
        try:
            built = self.build(cand, op, state)
        except BuildError as exc:
            state.prototypes.pop(op, None)
            return None, None, f"{op}: the transpiled {depth:g}-level design did not build: {str(exc)[:200]}"
        fails, summary = self.fast_check(built, cand, op, state)
        if fails:
            state.say(f"  {op}: the TRANSPILED {depth:g}-level prototype fails {fails} fast-check vector(s) -- a transpiler defect; the old design stands")
            state.prototypes.pop(op, None)
            return None, None, f"{op}: transpiled {depth:g}-level design fails {fails} fast-check vector(s): {summary[:200]}"
        state.say(f"  {op}: transpiled from the {depth:g}-level prototype" + (f" at {k} pipeline registers" if k else "") + "; passes the fast check")
        return cand, built, ""

    @staticmethod
    def _passes_on_record(state, kind: str, op: str, digest: str) -> int:
        """How many `kind` passes the record holds for this part's CURRENT prototype (its
        digest) -- the ladder's memory across relaunches (D503)."""
        rec = getattr(state, "records", None)
        if rec is None or getattr(rec, "store", None) is None:
            return 0
        try:
            return sum(1 for e in rec.store.events(rec.campaign_id)
                       if e.get("kind") == kind and (e.get("detail") or {}).get("op") == op
                       and (e.get("detail") or {}).get("digest") == digest)
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _note_pass(state, kind: str, op: str, digest: str) -> None:
        rec = getattr(state, "records", None)
        if rec is None or getattr(rec, "store", None) is None:
            return
        try:
            rec.store.append_event(rec.campaign_id, kind, {"op": op, "digest": digest})
        except Exception:  # noqa: BLE001
            pass

    def _redesign(self, op: str, proto: str, state, why: str):
        """A different ALGORITHM for a part whose current one cannot be made fast enough
        (D499): a fresh prototype pass -- no seed, the spec prompt plus what the design on
        record is and why it is slow, and the sheet's other methods to choose from -- under
        the normal 0-over gate. What passes is pipelined by the sweep and measured; the
        better of the two designs (the least area at the goal, else the fastest) is the one
        that stands, and both stay on record."""
        goal = self._fmax_goal()
        known = getattr(state, "_fmax", None) or {}
        d = self._part_depth(op, state) or {}
        counts = getattr(state, "_redesigns", None) or {}
        if counts.get(op, 0) >= 2:
            return None, None, f"{op}: two alternative algorithms were already tried this run"
        counts[op] = counts.get(op, 0) + 1
        state._redesigns = counts                                # type: ignore[attr-defined]
        head = "\n".join(proto.strip().splitlines()[:12])
        note = (f"ALTERNATIVE ALGORITHM (the {counts[op]}{'st' if counts[op] == 1 else 'nd'} asked for): the design on "
                f"record for {op} passes every input but runs at {known.get(op, 0):.0f} MHz alone against the "
                f"{goal:.0f} MHz asked for, pipelined and after a depth pass; "
                + (self._depth_text(d) + "; " if d else "")
                + "its method, from its first lines:\n" + head + "\n\n"
                "Do NOT refine it -- write a DIFFERENT algorithm for the same function from the METHODS "
                "sheet above (another range reduction, a table where it computes, a shallower "
                "recurrence, no divider on the data path), verified to 0 over the same way; the "
                "tool then pipelines and measures it and keeps whichever is faster at less area. "
                "What the published units do that the first designs here did not: a power-of-two "
                "tabled range so the segment index is a bit-field of the argument (no multiply to "
                "address a table); non-uniform segments, dense where the function bends; degree 2 "
                "with 16-32 segments instead of degree 1 with hundreds; a per-segment exponent for "
                "an output that spans binades instead of a 32-bit table and a 64-bit product; one "
                "datapath with the symmetry folded in."
                + self._reuse_note(op, state))
        red = getattr(state, "_redesign", None) or {}
        red[op] = note
        state._redesign = red                                    # type: ignore[attr-defined]
        keep_proto = state.prototypes.pop(op, None)
        keep_seed = state.proto_best.pop(op, None)
        # the alternative RESUMES from where its last pass left it (D499: gelu's first
        # alternative pass ended at 34,845 over -- a design from scratch takes more than one
        # pass here, and the admitted design's own rows are not its seeds)
        prior = self._redesign_seed(op, state)
        if prior is not None:
            state.proto_best[op] = prior
            state.say(f"  redesign {op}: resuming the alternative from its best on record ({prior[0]:g} over)")
        old_cand = state.admitted.get(op)
        old_fmax, old_area = known.get(op), (getattr(state, "_area", None) or {}).get(op)
        state.say(f"  redesign {op}: a different algorithm, from scratch (the record's runs at {known.get(op, 0):.0f} MHz alone)")
        cand = None
        try:
            cand, built, reason = self.generate(op, "a different algorithm", state, why)
            if cand is None:
                return None, None, reason
            # the new design, pipelined and measured like any part
            new_proto = state.prototypes.get(op)
            best = self._pipeline_sweep(op, new_proto, state) if new_proto else None
            if best is not None:
                fmax, cand, built = best
            else:
                m = self._screen_alone(cand, state) or {}
                fmax = m.get("fmax_mhz", 0.0)
            new_area = (getattr(state, "_area", None) or {}).get(op)
            better = (old_fmax is None or (fmax >= goal and (old_fmax < goal or (new_area or 1e12) <= (old_area or 1e12)))
                      or (fmax < goal and fmax > old_fmax))
            state.say(f"  redesign {op}: the alternative runs at {fmax:.0f} MHz alone"
                      + (f" ({new_area:.0f} um2)" if new_area else "")
                      + f" against the record's {old_fmax or 0:.0f}" + (f" ({old_area:.0f} um2)" if old_area else "")
                      + (" -- it stands" if better else " -- the record's stands; the alternative stays on record"))
            if not better and old_cand is not None:
                state.prototypes[op] = keep_proto                # the record's design stays the part
                known[op] = old_fmax
                return old_cand, self.build(old_cand, op, state), ""
            return cand, built, ""
        finally:
            red.pop(op, None)
            if cand is None:
                pb = state.proto_best.get(op)
                if pb and pb[1].strip() and (prior is None or pb[0] < prior[0]):
                    self._remember_redesign(op, state, pb)      # its best refused attempt, for next time
                if keep_proto is not None:
                    state.prototypes[op] = keep_proto
                if keep_seed is not None:
                    state.proto_best[op] = keep_seed
                else:
                    state.proto_best.pop(op, None)

    @staticmethod
    def _remember_redesign(op: str, state, best: tuple) -> None:
        rec = getattr(state, "records", None)
        if rec is None or getattr(rec, "store", None) is None:
            return
        try:
            rec.store.append_event(rec.campaign_id, "redesign",
                                   {"op": op, "score": float(best[0]), "artifact": best[1], "why": (best[2] or "")[:2000]})
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _redesign_seed(op: str, state) -> tuple | None:
        """The best refused alternative for `op` from earlier redesign passes, as a seed."""
        rec = getattr(state, "records", None)
        if rec is None or getattr(rec, "store", None) is None:
            return None
        best = None
        try:
            for e in rec.store.events(rec.campaign_id):
                if e.get("kind") != "redesign":
                    continue
                d = e.get("detail") or {}
                if d.get("op") != op or not d.get("artifact"):
                    continue
                if best is None or float(d.get("score", 1e18)) < best[0]:
                    best = (float(d["score"]), str(d["artifact"]), str(d.get("why") or ""))
        except Exception:  # noqa: BLE001
            return None
        return best

    def _reuse_note(self, op: str, state) -> str:
        """The campaign's other verified operators, as blocks this part may call (D499,
        Cedric: "gelu/tanh/sigmoid might use log/exp ... reuse the IPs we designed"): the
        identities that compose them, and the one rule a composition must respect."""
        ops = self._operators(op, state)
        if not ops:
            return ""
        ident = {"sigmoid": "sigmoid(x) = 1 / (1 + exp(-x)) -- exp then a reciprocal",
                 "tanh": "tanh(x) = 2 * sigmoid(2x) - 1 = (exp(2x) - 1) / (exp(2x) + 1)",
                 "gelu": "gelu(x) = x * Phi(x), Phi(x) = 0.5 * (1 + erf(x / sqrt 2)) ~ sigmoid(1.702 x) is NOT within 1 ULP; "
                         "tanh-form gelu 0.5 x (1 + tanh(0.7979 (x + 0.044715 x^3))) is not either -- a composition "
                         "helps for the REDUCTION (exp of a reduced argument), the last digits need a fit",
                 "recip": "recip(x) = 2^-e * recip(m); rsqrt(x) = 2^(-e/2) * rsqrt(m)",
                 "rsqrt": "rsqrt(x) = recip(sqrt(x)) rounds twice; exp(-0.5 * ln(x)) too",
                 "log": "ln(x) = e * ln 2 + ln(m)", "exp": "exp(x) = 2^(x log2 e)"}
        return ("\n\nREUSE: this campaign's verified operators are blocks you may call -- "
                + ", ".join(f"{o}_fp16(x)" for o in sorted(ops))
                + " (an FP16 pattern in and out, each verified 0 over on every input; the transpiler inlines "
                "them, so an instance is their logic and their latency). "
                + ident.get(op, "") + ". Each rounds to FP16: chaining two of them stacks two roundings "
                "(a sigmoid written as recip_fp16(fp16_add(ONE, exp_fp16(fp16_neg(x)))) measured 925 "
                "over); keep the intermediate in fixed point (recip_fixed, a table value, a product) "
                "and pack once, or use a verified operator only where its rounding is the last one.")

    def good_enough(self, state):
        goal = self._fmax_goal()
        for sc in reversed(state.scored or []):
            if sc.candidate.name.startswith("composed[") and (sc.metrics or {}).get("fmax_mhz", 0) >= goal:
                return f"the composition runs at {sc.metrics['fmax_mhz']:.0f} MHz, the {goal:.0f} MHz asked for"
        return None

    # ---- decision
    def frontier_axes(self):
        return (lambda s: s.metrics["fmax_mhz"], lambda s: s.metrics["area_um2"])

    def decide(self, pool, state):
        if not pool:
            return None, "nothing measured"
        from flux_frontier import cheapest_meeting, knee_ranked

        if self.target_mhz is not None:
            pick, rule = cheapest_meeting(pool, cost=lambda p: p.metrics["area_um2"],
                                          value=lambda p: p.metrics["fmax_mhz"],
                                          floor=self.target_mhz)
            return pick, {"cheapest-meeting": f"smallest area at >= {self.target_mhz:.0f} MHz",
                          "fallback-best-value":
                          f"nothing reaches {self.target_mhz:.0f} MHz; the fastest",
                          "best-value": "the fastest measured",
                          "nothing": "nothing measured"}[rule]
        ranked = knee_ranked(pool, [lambda p: p.metrics["area_um2"],
                                    lambda p: -p.metrics["fmax_mhz"],
                                    lambda p: p.metrics.get("power_w", 0.0)])
        return (ranked[0] if ranked else None), "the knee of area / fmax / power"

    def conclusion(self, pick: Scored, decided_by: str) -> dict[str, Any]:
        return {"decision": pick.name, "decided_by": decided_by,
                "fmax_mhz": round(pick.metrics["fmax_mhz"], 1),
                "area_um2": round(pick.metrics["area_um2"], 1),
                "power_w": pick.metrics.get("power_w", 0.0)}

    def source_key(self, cand: Candidate) -> str:
        return hashlib.sha256(cand.artifact.encode()).hexdigest()[:16]
