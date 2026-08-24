"""The FP16 WORLD (D519, review step 5.4): what `applications/nlu/nlu.problem.yaml` names once
as its `world:` -- everything about a non-linear unit a document cannot say in prose or
numbers, as the hooks of one object.

    the prototype stage    the toolkit of FP16 blocks, the harness, the exhaustive ULP judge,
                           the family judge, the transpiler as the target (`prototype`)
    the generator's words  the static prefix, the design and repair prompts, the reply
                           shapes, the table oracle and the hygiene pass (`apply_tools`)
    the evaluator          Verilator (`build`), the authored vectors (`fast_check`), all 65536
                           inputs (`judge`), the op mux (`compose`), yosys and OpenROAD
                           (`measure`)
    the record's voice     `versions`, `from_record`, `siblings`, the standing, the report

What the DOCUMENT says: the parts and their order, the objectives (800 MHz first, then
area, then power), the ladder, the stages and what they need on PATH, the knowledge sheet,
the budget, the campaign identity and the `params:` this world is built from (the ULP
budget, the clock, the seed, the test-author rounds). Before D519 this was `NluProblem`,
a `Problem` subclass the demo built from its own flags; a new ask in this world -- other
operators, another ULP budget, a different clock -- is now a document, not a program.
"""

from __future__ import annotations

import functools
import hashlib
import re
import shutil
from typing import Any

import numpy as np

from flux_loop import BuildError, Candidate, LoopState, Scored, Verdict, prototype_digest
from flux_loop.ladder import part_depth
from flux_loop.prototype import verified_operators

from .fp16 import FORMULAS, OPCODES, all_inputs, describe_failures, family_judge, ulp_report
from .vectors import floor_vectors, merge, parse_authored
from .verify import CompileError, build_sim

__all__ = ["World", "library_queries", "mentor_for", "op_stub", "wrap_per_op"]


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


@functools.lru_cache(maxsize=1)
def _versions() -> dict[str, str]:
    import importlib

    def digest(names: tuple[str, ...]) -> str:
        h = hashlib.sha256()
        for n in names:
            mod = importlib.import_module(n)
            with open(mod.__file__, "rb") as f:
                h.update(f.read())
        return h.hexdigest()[:16]

    # the RTL depends on the FP16 transpiler and toolkit AND on the language's vectorizer
    # (`flux_loop.pyint`, D514); the verdict on the reference, the sweep and the vectors
    return {"transpiler": digest(("flux_nlu.transpile", "flux_nlu.blocks", "flux_nlu.hygiene", "flux_loop.pyint.vectorize")),
            "judge": digest(("flux_nlu.fp16", "flux_nlu.verify", "flux_nlu.vectors"))}


#: what the sandbox runs after a prototype (D516): every FP16 pattern through design(), the
#: tables and the audits said, the output as hex on one line
PROTO_HARNESS = "\n_xs = np.arange(65536, dtype=np.int64)\nwith np.errstate(all='ignore'):\n    _ys = np.asarray(design(_xs), dtype=np.int64) & 0xFFFF\n_ys = _ys.astype(np.uint16)\nassert _ys.shape == _xs.shape, f'design returned shape {_ys.shape} for {_xs.shape[0]} inputs'\nfor _k, _v in list(globals().items()):\n    if not _k.startswith('_') and isinstance(_v, np.ndarray) and _v.ndim == 1 and 1 < _v.size <= 1024 and _v.dtype.kind in 'iu' and int(np.abs(_v).max()) >= (1 << 20):\n        _DIAG.append(f'{_k}: its values reach 2^{int(np.abs(_v).max()).bit_length() - 1}; an FP16 operator rarely needs a table value above 2^16 -- if it was built with rom(lambda t: f(t) * 2**K, ...), drop the * 2**K: rom scales by 2^frac_bits itself')\nfor _d in list(dict.fromkeys(_DIAG))[:4]: print('DIAG ' + _d.replace(chr(10), ' '))\nprint('TABLES ' + ';'.join(f'{_k}[{_v.size}]={int(_v.min())}..{int(_v.max())}' for _k, _v in list(globals().items()) if not _k.startswith('_') and isinstance(_v, np.ndarray) and _v.ndim == 1 and 1 < _v.size <= 1024 and _v.dtype.kind in 'iu'))\nprint('OUT ' + _ys.astype('<u2').tobytes().hex())\n"


class World:
    """The FP16 world of a document problem (D519): built from the problem's document --
    `params:` (ulp_budget, clock_period_ps, seed, test_rounds; `ops` when the RTL's operator
    order is not the opcode order), the parts, the objectives -- and bound hook by hook by
    `flux_loop.PromptProblem`."""

    def __init__(self, problem: Any) -> None:
        self.problem = problem
        p = dict(getattr(problem.task, "params", {}) or {})
        parts = tuple(problem.subgoals())
        bad = [o for o in parts if o not in OPCODES]
        if bad:
            raise ValueError(f"unknown operator(s): {', '.join(bad)} (known: {', '.join(OPCODES)})")
        self.ops = tuple(p["ops"]) if p.get("ops") else tuple(o for o in OPCODES if o in parts)
        self.ulp_budget = int(p.get("ulp_budget", 1))
        self.clock_period_ps = float(p.get("clock_period_ps", 1250.0))
        self.test_rounds = int(p.get("test_rounds", 1))
        self.seed = int(p.get("seed", 0))
        self.vectors: dict[str, np.ndarray] = {}
        self.per_op: dict[str, dict[str, dict]] = {}     # composed name -> op -> report
        self.last_fast: dict[str, dict] = {}             # op -> previous fast-check report (D423)
        self.known_tables: dict[str, dict[str, dict]] = {}  # op -> name -> spec placed once (D473)
        self._mentor: Any = None                         # the declared sources (D449)
        self._prototype: Any = None                      # the declared stage (D515)

    # ---- mentor
    def versions(self) -> dict[str, str]:
        """D510: the transpiler (`transpile`, `blocks`, `vectorize`, `hygiene`) and the judge
        (`fp16`, `verify`, `vectors`) as content hashes of their sources -- a reload trusts a
        row made by the same, re-spells once when the transpiler moved, re-judges once when
        the judge did. No version string to forget to bump."""
        return _versions()

    def sheet(self) -> str:
        """The methods sheet, the document's `knowledge: sheet:` (D514) -- read by the loader."""
        return str(getattr(self.problem.task, "knowledge", "") or "")

    def tools_missing(self) -> list[str]:
        from .verify import tools_missing

        return tools_missing() + [t for t in ("yosys",) if shutil.which(t) is None]

    def from_record(self, doc):
        """Both shapes the campaign holds: the loop's (artifact/subgoal/score) and the
        D412-era rows (source/op/over_budget) -- the seeded `recip` and every
        best-so-far design live in the latter."""
        if "artifact" in doc:
            from flux_loop.problem import Problem

            return Problem.from_record(self.problem, doc)     # the loop's own shape
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
            mentor = self.knowledge()
            srcs = [type(s).__name__ for s in getattr(mentor, "sources", [])]
            details["knowledge"] = (f"methods sheet {len(self.sheet())} chars; mentor sources: "
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
                        test_author_prompt(ops=self.ops, human=human)).text
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
            self._mentor = mentor_for(self.ops, self.sheet())
        return self._mentor

    # ---- orchestrator
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

    def design_prompt(self, subgoal, method, state, human, prior, prior_why):
        from .invent import design_op_prompt, design_schema

        return (design_op_prompt(
            subgoal, ulp_budget=self.ulp_budget, knowledge=self.sheet(),
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

    def tools(self, subgoal, state, stage="prototype", checked=None):
        """The loop's tools (D505) plus this world's instruments in the prototype stage (D530):
        `error_map`, `compare`, `quantisation`; the placed path is the loop's `timing`."""
        from flux_loop.tools import loop_tools

        tools = loop_tools(self.problem, subgoal, state, checked=checked, stage=stage)
        if stage == "prototype" and subgoal:
            from .instruments import instrument_tools

            tools = tools + instrument_tools(self, subgoal, state)
        return tools

    def redesign_note(self, part, state, depth, nth):
        """What a DIFFERENT-algorithm pass hears (D499): why the design on record is not fast
        enough, its method's first lines, and what the published units do that the first
        designs here did not."""
        goal = self._fmax_goal()
        proto = (state.prototypes or {}).get(part) or ""
        head = "\n".join(proto.strip().splitlines()[:12])
        alone = state.part(part).alone.get("fmax_mhz") or 0.0
        return (f"ALTERNATIVE ALGORITHM (the {nth}{'st' if nth == 1 else 'nd'} asked for): the design on "
                f"record for {part} passes every input but runs at {alone:.0f} MHz alone against the "
                f"{goal:.0f} MHz asked for, pipelined and after a depth pass; "
                + (self._depth_text(depth) + "; " if depth else "")
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
                + self._reuse_note(part, state))

    def prototype(self):
        """The prototype stage, declared (D515): what `design(x)` computes for a part, what an
        input is, what the gate demands, the FP16 toolkit with its documentation, the check.
        The language's rules, the history, the example and the repair turns are the loop's."""
        if getattr(self, "_prototype", None) is None:
            import inspect

            from flux_loop import Prototype, Toolkit

            from .blocks import BLOCKS, operator_docs, tool_docs
            from .invent import PROTO_CONTRACT

            ref = {"exp": "e^x", "log": "ln(x)", "sigmoid": "1/(1+e^-x)", "tanh": "tanh(x)",
                   "gelu": "x*Phi(x)", "recip": "1/x", "rsqrt": "1/sqrt(x)"}
            sigs = ", ".join(f"{fn.__name__}{inspect.signature(fn)}" for fn in BLOCKS)
            kit = Toolkit(
                docs=tool_docs(),
                reminder=("THE BLOCKS THAT EXIST (call them; nothing else is provided -- any other name "
                          f"must be defined in your prototype): {sigs}; constants BIAS, QNAN, PINF, NINF, "
                          "PZERO, NZERO, ONE, HALF, TWO; table functions exp2, exp, ln, log2, recip, rsqrt, sqrt, "
                          "sigmoid, tanh, erf, gelu (or any lambda). Per-element style (`if`/`return` on one input) is fine."),
                compose=("Constants: BIAS, QNAN, PINF, NINF, PZERO, NZERO, ONE, HALF, TWO. COMPOSE: classify with is_*(), "
                         "normalise with mantissa11()/exponent_unbiased(), convert with to_fixed()/"
                         "signed_fixed(), reduce the argument with integer arithmetic and const_fixed(), "
                         "table it with rom()/slope_rom() + lookup()/interp1() on the REDUCED argument, then "
                         "pack_fp16() (rounding, carry, Inf, subnormals) and specials(). A whole operator is "
                         "10-25 lines this way; spend your effort on the algorithm and the knobs."),
                shape=("    T = 6\n    M = 13\n"
                       "    SPACE = {\"T\": [5, 6, 7], \"M\": [12, 13, 14]}\n"
                       "    TABLE = rom(<function of the REDUCED argument>, lo, hi, 2 ** T, M)   # built once\n"
                       "    def design(x):\n"
                       "        if is_nan(x): return QNAN          # or specials(...) at the end -- either\n"
                       "        s, e, m = fp16_sign(x), exponent_unbiased(x), mantissa11(x)\n"
                       "        ...   # reduce the argument, index TABLE, correct: integer arithmetic only\n"
                       "        # or, for a function of the significand: v = approx_on_mantissa(f, m, T, M)\n"
                       "        y = pack_fp16(s_out, e_out, v, frac_bits)   # round, carry, Inf, subnormals\n"
                       "        return specials(x, QNAN, <+Inf ->>, <-Inf ->>, <+0 ->>, <-0 ->>, y)"),
                operator_docs=lambda ops: ("VERIFIED OPERATORS of this campaign, blocks too (an FP16 pattern in, an FP16 "
                                           "pattern out; compose them with fp16_add/fp16_mul/fp16_neg -- each rounds to "
                                           "FP16, so a composition may need a wider intermediate to stay within 1 ULP):\n"
                                           + operator_docs(ops)))
            from flux_loop import Target

            from .blocks import audit_harness, misuse, prelude_lines, relocate, with_prelude
            from .transpile import cost_estimate, logic_depth, transpile

            kit = Toolkit(**{**kit.__dict__,
                             "prelude": lambda text, ops: with_prelude(text, ops), "prelude_lines": prelude_lines,
                             "misuse": misuse, "audit": audit_harness, "explain": self._explain_error,
                             "relocate": relocate})
            # the check is the loop's skeleton over this judge; a problem that puts its own
            # check on the instance (a test's scripted gate) replaces it whole (`PromptProblem.prototype`)
            self._prototype = Prototype(
                check=None,
                harness=PROTO_HARNESS, judge=self._judge,
                family_judge=lambda part: family_judge(part, self.ulp_budget),
                cost=cost_estimate,
                target=Target(spell=lambda full, part: transpile(full, top=f"nlu_{part}", xs=all_inputs()),
                              depth=lambda full: logic_depth(full, xs=all_inputs()),
                              describe_depth=self._depth_text, faster_advice=self._faster_advice),
                reference=lambda part: f"an integer array of FP16 bit patterns for {ref.get(part or '', part)}",
                domain="a numpy int64 array of FP16 bit patterns (0..65535)",
                gate=(f"within {self.ulp_budget} ULP of the correctly rounded result on ALL 65536 inputs "
                      "(NaN in -> any NaN; Inf handled exactly; subnormals in and out are real)"),
                contract=PROTO_CONTRACT, toolkit=kit, family=True, domain_size=65536,
                score_unit=" inputs beyond the ULP budget")
        return self._prototype


    @staticmethod
    def _rounding_residue(code: str, rep: dict) -> str:
        """The diagnosis a few small, scattered failures in a design that ROUNDS TO FP16 more
        than once deserve (D488: sigmoid sat at 106 over for two passes -- 1 to 3 ULP off,
        across regions, the report calling it "a sign-dependent step"). Each `<op>_fp16(...)`,
        `from_fixed(...)` or extra `pack_fp16(...)` rounds to 11 bits; in series they cannot
        meet a 1-ULP gate, and no edit to the logic will change that. Empty when the
        failures are not that shape."""
        n_fail = int(rep.get("over_budget", 0) or 0)
        max_ulp = rep.get("max_ulp")
        if not (0 < n_fail <= 2000) or not isinstance(max_ulp, int) or max_ulp > 6:
            return ""
        roundings = World._roundings_on_a_path(code)
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
    def _roundings_on_a_path(code: str) -> int:
        """How many times ONE input is rounded to FP16 on its way through `design`: calls that
        round (`<op>_fp16`, `fp16_add/sub/mul/from_int`, `from_fixed`, an extra `pack_fp16`)
        counted along the deepest path -- an `if`/`else` contributes the larger of its two
        branches, not their sum (D506: gelu's two branches each rounded once and were told
        they rounded twice; the model chased a rounding that was not there while the 343
        failures were the interpolation's). Falls back to a plain count of the text when
        `design` cannot be parsed."""
        import ast
        import re

        def textual(src: str) -> int:
            return (len(re.findall(r"\b(?!pack_fp16\b)\w+_fp16\s*\(", src))
                    + len(re.findall(r"\bfp16_(?:add|sub|mul|from_int)\s*\(", src))
                    + len(re.findall(r"\bfrom_fixed\s*\(|\bpack_fp16\s*\(", src)))

        def is_rounding(call: ast.Call) -> bool:
            name = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", "")
            return bool(re.fullmatch(r"(?!pack_fp16$)\w+_fp16|fp16_(?:add|sub|mul|from_int)|from_fixed|pack_fp16", name or ""))

        def in_expr(node: ast.AST) -> int:
            return sum(1 for n in ast.walk(node) if isinstance(n, ast.Call) and is_rounding(n))

        def in_body(stmts: list) -> int:
            total = 0
            for st in stmts:
                if isinstance(st, ast.If):
                    total += in_expr(st.test) + max(in_body(st.body), in_body(st.orelse))
                elif isinstance(st, (ast.For, ast.While, ast.With)):
                    total += in_body(st.body)
                elif isinstance(st, ast.Return):
                    total += in_expr(st.value) if st.value is not None else 0
                else:
                    total += in_expr(st)
            return total

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return textual(code)
        design = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "design"), None)
        if design is None:
            return textual(code)
        # helpers the design calls (other than the toolkit's) round on their own paths too
        helpers = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name != "design"}
        called = {n.func.id for n in ast.walk(design) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        return in_body(design.body) + sum(in_body(helpers[h].body) for h in called if h in helpers)


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
            from flux_loop.pyint.vectorize import relocate_lines

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

    def _judge(self, subgoal, got: bytes, code: str, state, extra: dict):
        """THE FP16 GATE on a prototype's output (D516): the ULP report over all 65536 inputs,
        the precision floor named (D488), the pack audits kept only where they explain it
        (D489), the failure text against the previous check of this part (D423)."""
        from flux_loop import Judgement

        try:
            ys = np.frombuffer(got, dtype="<u2").astype(np.uint16)
        except Exception as exc:  # noqa: BLE001
            return Judgement(False, 1 << 20, f"prototype output unreadable: {exc}")
        xs = all_inputs()
        if ys.shape != xs.shape:
            return Judgement(False, 1 << 20, f"prototype returned {ys.size} values for {xs.size} inputs")
        rep = ulp_report(subgoal, xs, ys, budget=self.ulp_budget)
        rep.update(extra)
        hand = self._hand_rolled(code)
        if hand:
            rep["audit"] = list(rep.get("audit", [])) + hand
        residue = self._rounding_residue(code, rep)
        if residue:
            rep["pattern"] = residue                     # the precision floor, named (D488)
            # the internal pack audits are noise here; a from_fixed one is the floor's cause (D489)
            rep["audit"] = [a for a in rep.get("audit", []) if a.startswith("from_fixed:")]
        previous = self.last_fast.get(f"proto:{subgoal}")
        self.last_fast[f"proto:{subgoal}"] = rep
        if rep["ok"]:
            return Judgement(True, 0, "", rep)
        return Judgement(False, int(rep["over_budget"]),
                         f"{rep['over_budget']} of 65536 beyond {self.ulp_budget} ULP"
                         + (" (the best member of the family). " if extra.get("search") else ". ")
                         + describe_failures(subgoal, rep, previous=previous), rep)

    @staticmethod
    def _optimising(state) -> dict:
        """{op: {"depth": d0, "target": d}} while a part is being made faster (D496)."""
        return {op: p.optimise for op, p in state.parts.items() if p.optimise}

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
            from flux_loop.pyint.vectorize import vectorize

            full = with_prelude(vectorize(prototype)[0], verified_operators(state, subgoal))
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

    def measure(self, cand, stage, state):
        from flux_evaluator_openroad import run_ppa_flow, run_synthesis_flow

        # a single PART measured on its own (D496: an improved part before it is composed
        # again) is wrapped with its one opcode; the composition with all of them
        ops = (cand.subgoal,) if cand.subgoal in self.ops else self.ops
        top = cand.artifact if cand.knobs.get("style") == "shared" else \
            wrap_per_op(cand.artifact, ops)
        latency = int(cand.meta.get("latency", 0))
        kw = dict(clock_port="clk" if latency > 0 else None,
                  clock_period_ps=self.clock_period_ps,
                  timeout_s=self.stage_timeout_s(stage))
        try:
            # the three stages the document declares (D522): the synthesis screen, placement,
            # and full place-and-route -- the goal is judged on the routed number
            rep = (run_synthesis_flow(top, "nlu", **kw) if stage == "screen" else
                   run_ppa_flow(top, "nlu", flow_depth="routed" if stage == "route" else "placement",
                                repair_design=True, **kw))
        except Exception as exc:  # noqa: BLE001
            # D537: the cause goes back as the ABI's `{"error": why}` -- the loop puts it on the
            # trial row and in `state.refused`; None said only "could not measure", three times,
            # while the routed flow of the whole was dying on the flow's own 600 s default
            return {"error": f"{stage} failed: {type(exc).__name__}: {str(exc)[:200]}"}
        out = rep.metrics()
        if getattr(rep, "critical_path", None):
            out["critical_path"] = rep.critical_path          # D526: the worst path as data, beside the numbers
        return out

    def stage_timeout_s(self, stage: str) -> float:
        """The document's `timeout_s` for `stage` (D537): full place-and-route of the whole
        runs over an hour; the flow's own default (600 s) is for a part. A stage the document
        does not declare keeps that default."""
        for spec in getattr(getattr(self.problem, "task", None), "stages", None) or ():
            if getattr(spec, "name", None) == stage:
                return float(getattr(spec, "timeout_s", 600.0) or 600.0)
        return 600.0

    # ---- what the campaign is for, for the results table (D497)
    def standing(self, state):
        goal = self._fmax_goal()
        objs = self.problem.objectives()
        o1 = objs[0] if objs else None
        stages = self.problem.stages()
        out: dict = {
            "goal": (f"an FP16 NLU of {len(self.ops)} operators ({', '.join(self.ops)}): each within "
                     f"{self.ulp_budget} ULP of the reference on ALL 65536 inputs, composed under one op mux, "
                     f"at >= {goal:.0f} MHz (clock {self.clock_period_ps:.0f} ps"
                     + (f"; {o1.stage} with a {o1.margin:.0%} margin on shallower stages" if o1 is not None and o1.margin and o1.stage else "")
                     + ") with the least area and power"),
        }
        # the whole design: its last measured numbers
        last = next((sc for sc in reversed(state.scored or []) if sc.candidate.name.startswith("composed[")), None)
        if last is not None:
            m = last.metrics or {}
            lat = int((last.candidate.meta or {}).get("latency", 0))
            goal_here = (o1.goal_at(last.stage, stages) or goal) if o1 is not None else goal
            out["composed"] = (f"{m.get('area_um2', 0):,.0f} um2 · {m.get('fmax_mhz', 0):.0f} MHz"
                               f" (goal {goal_here:.0f}" + (f" on {last.stage}" if goal_here != goal else "") + ")"
                               f" · {m.get('power_w', 0) * 1000:.0f} mW · latency {lat}"
                               f" · {last.stage} stage"
                               + (" -- MEETS THE GOAL" if (o1.meets(m, last.stage, stages) if o1 is not None else m.get("fmax_mhz", 0) >= goal) else ""))
        # what this pass is doing now
        opt = self._optimising(state)
        trying = state.trying
        known = {o: v for o, p in state.parts.items() if (v := p.alone.get("fmax_mhz")) is not None}
        todo = [op for op in self.ops if op not in state.admitted]
        red = [op for op in self.ops if state.part(op).redesign]
        if red:
            op = red[0]
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
            out["now"] = ("the whole design meets the goal" if (o1.meets(last.metrics, last.stage, stages) if o1 is not None else (last.metrics or {}).get("fmax_mhz", 0) >= goal)
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
                    areas = {o: v for o, p in state.parts.items() if (v := p.alone.get("area_um2")) is not None}
                    bits.append(f"{known[op]:.0f} MHz alone" + (f", {areas[op]:.0f} um2" if op in areas else ""))
                d = part_depth(self, op, state)
                if d:
                    bits.append(f"depth {d['depth']}")
                others = [e for e in state.part(op).shortlist if not e.get("standing")]
                if others:                                       # D528: the rest of the shortlist
                    bits.append("also on record: " + "; ".join(
                        f"{e['name']}" + (f" {e['metrics'].get('fmax_mhz', 0):.0f} MHz" if e.get("metrics", {}).get("fmax_mhz") else "")
                        + (f"/{e['metrics'].get('area_um2', 0):.0f} um2" if e.get("metrics", {}).get("area_um2") else "")
                        for e in others[:3]))
            elif op in (getattr(state, "proto_best", None) or {}):
                bits.append(f"best {state.proto_best[op][0]:g} over")
            parts[op] = " · ".join(bits)
        out["parts"] = parts
        return out

    # ---- the push for fmax (D496)
    def _fmax_goal(self) -> float:
        """The clock asked for, as a frequency: the document's first objective's goal (D511),
        else the period the tools are constrained to."""
        objs = self.problem.objectives()
        goal = objs[0].goal if objs and objs[0].metric == "fmax_mhz" else None
        return float(goal) if goal else 1e6 / float(self.clock_period_ps)

    def _reuse_note(self, op: str, state) -> str:
        """The campaign's other verified operators, as blocks this part may call (D499,
        Cedric: "gelu/tanh/sigmoid might use log/exp ... reuse the IPs we designed"): the
        identities that compose them, and the one rule a composition must respect."""
        ops = verified_operators(state, op)
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

    # ---- decision

    def conclusion(self, pick: Scored, decided_by: str) -> dict[str, Any]:
        return {"decision": pick.name, "decided_by": decided_by,
                "fmax_mhz": round(pick.metrics["fmax_mhz"], 1),
                "area_um2": round(pick.metrics["area_um2"], 1),
                "power_w": pick.metrics.get("power_w", 0.0)}

    def report(self, out) -> list[str]:
        """This world's lines under the task report (D519; the demo's THE ANSWER block, D408):
        the decision's style and latency, its PPA, and the per-operator exhaustive error table."""
        d = out.decision
        if d is None:
            return []
        c, m = d.candidate, d.metrics or {}
        stage = "PLACED (PPA)" if d.stage == "confirm" else "synthesis screen"
        lines = [f"  {c.name}: {c.knobs.get('style', 'shared')}, {c.knobs.get('method', '?')}, "
                 f"latency {int((c.meta or {}).get('latency', 0))}",
                 f"  {m.get('area_um2', 0):>10,.0f} um2   {m.get('fmax_mhz', 0):>6.0f} MHz   "
                 f"{m.get('power_w', 0) * 1e3:>7.2f} mW   [{stage}; {out.decided_by}]"]
        per_op = self.per_op.get(c.name, {})
        if per_op:
            worst = max((r.get("max_ulp", 0) for r in per_op.values()), key=lambda v: (isinstance(v, str), v))
            rate = max(float(r.get("error_rate", 0.0)) for r in per_op.values())
            lines.append(f"  correctness: max {worst} ULP over 65536 inputs x {len(per_op)} op(s); "
                         f"worst op error rate {rate:.3%}")
            lines.append("  per-operator (exhaustive):")
            for op, r in per_op.items():
                lines.append(f"    {op:<8} max {r.get('max_ulp')} ULP   inexact {float(r.get('error_rate', 0.0)):.3%}"
                             f"   mean {float(r.get('mean_ulp', 0.0)):.4f}")
        return lines


# ---- the mentor's sources (D449), once the flow's -------------------------------------
def _attempts_so_far(records) -> list[str]:
    """Refusals are MEASUREMENTS OF ATTEMPTS, not verdicts on approaches (D418m, Cedric:
    "the record might just have had a bad design; refining might reach the target"). So
    they read as how far each attempt got, best first, with the one being refined named
    as such -- never as mistakes to avoid."""
    tried = []
    for c, why in records.refusals(stage="gate", limit=12):
        over = c.get("over_budget")
        if not isinstance(over, int):
            m = re.search(r"(\d+) (?:of \d+ (?:inputs )?beyond|over budget)", why or "")
            over = int(m.group(1)) if m else None
        tried.append((over if over is not None else 10**9, c.get("op") or "",
                      c.get("name", "?"), c.get("method", ""), why))
    tried.sort()
    best_per_op: dict[str, int] = {}
    for over, op, name, method, why in tried:
        if op not in best_per_op:
            best_per_op[op] = over
    lines = []
    for over, op, name, method, why in tried[:6]:
        tag = " -- the current best, being refined" if best_per_op.get(op) == over else ""
        score = f"{over} of 65536 over" if over < 10**9 else "refused"
        lines.append(f"attempt {name}{f' ({method})' if method else ''}: {score}{tag}")
    return lines


def mentor_for(ops: tuple[str, ...], sheet: str):
    """This world's declared knowledge sources (D449): the method sheet (the document's,
    D514), the operator's own papers, and the record's read-back with its duels and its
    refused-attempt lines -- memoised per `ops` and sheet, because neither can change during
    a run and the library lookup is a BM25 pass over the corpus."""
    key = (ops, hashlib.sha256(sheet.encode()).hexdigest()[:16])
    if key not in _MENTORS:
        from flux_knowledge import Corpus, Digest, Library, Mentor, Mined, RecordReadback

        _MENTORS[key] = Mentor([
            Corpus("knowledge: methods sheet", sheet),
            Library(library_queries(ops)),
            Digest(),                       # D576: the library's key points, digested once into the store
            # D529 (review step 12): the AI half of knowledge -- facts mined from this campaign's own
            # record (measured points, refusal patterns, the lessons a model drew) and the record's
            # conclusions; empty on a fresh record, mined once per run
            Mined(),
            RecordReadback(
                stage="screen", metric="fmax_mhz", knobs=("style", "method", "latency"),
                metric_label="MHz at the synthesis screen", top=5,
                conclusion=lambda c: (
                    f"an earlier run decided: {c['decision']} ({c.get('decided_by', '')})"
                    if c.get("decision") else None),
                extra=_attempts_so_far,
                framing="this campaign's earlier runs; directions, not instructions -- a "
                        "refused attempt measures that attempt, not its approach; the best "
                        "one is the one to refine"),
        ])
    return _MENTORS[key]


#: One `Mentor` per (operator set, sheet), for this process: what `mentor_for` memoises.
_MENTORS: dict[tuple, Any] = {}


def library_queries(ops: tuple[str, ...]) -> list[str]:
    """What to ask the operator's own papers (D407's library, read by D408's designer): one
    lookup per operator plus the three method families this study keeps reaching for. The
    `Library` source (D449) runs them once per run, for both of this study's paths."""
    return [
        # the implementation shapes sampled designs get wrong most (D473/D477), FIRST so
        # the block's budget reaches them: proven sources in the library answer these --
        # fpnew's classifier and rounding, the leading-zero normalisation, FloPoCo's IEEE
        # input handling
        "classify subnormal zero infinity nan exponent all ones",
        "leading zero count normalize subnormal mantissa",
        "round to nearest even sticky guard carry into exponent",
        "exponential range reduction table fixed point",
    ] + [f"{op} hardware approximation half precision FP16" for op in ops] + [
        "piecewise polynomial approximation unit ULP",
        "CORDIC transcendental function hardware",
        "lookup table interpolation activation function circuit"]
