"""The NLU study's model roles (D408): test author, designer, repairer.

The framework's half of the bargain: it states the interface contract, hands over the
curated method knowledge, the record's read-back, the operator table and the standing
refusals -- and then the MODEL decides everything the study is about: which method
implements which operator, shared datapath or per-op units, combinational or how
deeply pipelined. Every reply is judged by tools (Verilator, the exhaustive ULP
check, yosys, OpenROAD); nothing a prompt says is ever quoted as a result.
"""

from __future__ import annotations

from pathlib import Path

import json
import re
from typing import Any

from .fp16 import OPCODES
from .tables import table_schema as _table_schema

__all__ = ["design_op_prompt", "design_prompt", "design_schema", "improve_prompt",
           "op_static_prefix",
           "op_repair_prompt", "parse_design", "parse_plan",
           "parse_reply_tables", "parse_structured_design", "plan_prompt",
           "plan_schema", "repair_prompt",
           "test_author_prompt"]

#: The prompts' prose as FILES beside the application (D532, review step 5.1): edited like the
#: methods sheet (D514), read once at import, `{part}`-style placeholders filled by the
#: functions below.
_PROMPTS = Path(__file__).resolve().parents[3] / "prompts"


def _prompt(name: str) -> str:
    return (_PROMPTS / name).read_text()


_CONTRACT = _prompt("contract.md")

_DESIGN_SHAPE = _prompt("design-shape.md")


def design_prompt(*, ops: tuple[str, ...], ulp_budget: int, knowledge: str,
                  record_ctx: str = "", human: str | None = None,
                  standings: str = "", refusals: list[str] = (),
                  authored_note: str = "") -> str:
    ref = "\n".join(f"  * {r}" for r in list(refusals)[-6:]) or "  (none yet)"
    parts = [p for p in (
        human,
        record_ctx,
        f"Design a hardware Non-Linear Unit for FP16. Operators required: "
        f"{', '.join(ops)}. HARD GATE: every operator must be within "
        f"{ulp_budget} ULP of the FP16 reference on ALL 65536 inputs -- the "
        "harness checks exhaustively, so a corner case is not a corner, it is a "
        "refusal with the failing input attached. Objectives after the gate: "
        "small area, high fmax (both measured on ASAP7 by real tools).",
        "You choose everything: method per operator (or one shared method), shared "
        "datapath vs per-op units, combinational vs pipelined and how deep. "
        "Different choices land on different points of the area/fmax frontier -- "
        "propose the point you believe in, and say so in the name.",
        knowledge,
        _CONTRACT.format(opcodes=", ".join(f"{k}={v}" for k, v in OPCODES.items())),
        standings,
        f"REFUSED so far (each measures one attempt, not its approach -- refine the "
        f"best, and only avoid repeating the same failure):\n{ref}",
        authored_note,
        _DESIGN_SHAPE,
    ) if p]
    return "\n\n".join(parts)


def parse_design(reply: str, *, ops: tuple[str, ...]) -> tuple[dict[str, Any] | None, str | None]:
    """(candidate, None) or (None, refusal reason). The candidate carries the model's
    declared knobs plus the source; the harness trusts the STRUCTURE (it can check
    it) and none of the claims (the tools check those)."""
    # The header may be pretty-printed across lines (thinking models do), so a
    # single-line match is only the first try; the fallback brace-matches from the
    # first "DESIGN" occurrence (D410 follow-up: three rounds died on formatting).
    head = None
    m = re.search(r"DESIGN:\s*(\{.*?\})\s*$", reply, re.M)
    if m:
        try:
            head = json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            head = None
    if head is None:
        k = reply.find("DESIGN")
        b0 = reply.find("{", k) if k >= 0 else -1
        if b0 >= 0:
            depth = 0
            for i in range(b0, min(len(reply), b0 + 4000)):
                if reply[i] == "{":
                    depth += 1
                elif reply[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            head = json.loads(reply[b0:i + 1])
                        except Exception:  # noqa: BLE001
                            head = None
                        break
    if head is None:
        tail = reply.strip()[-160:].replace("\n", " ")
        return None, f"no parseable DESIGN header (reply ended: ...{tail!r})"
    fence = re.search(r"```(?:system)?verilog\s*\n(.*?)```", reply, re.S | re.I)
    if not fence:
        return None, "no ```verilog fence"
    source = fence.group(1)
    style = head.get("style")
    if style not in ("shared", "per-op"):
        return None, f"style must be shared|per-op, got {style!r}"
    try:
        latency = int(head.get("latency"))
    except (TypeError, ValueError):
        return None, "latency must be an integer"
    if not 0 <= latency <= 32:
        return None, f"latency {latency} outside 0..32"
    needed = (["nlu"] if style == "shared" else [f"nlu_{op}" for op in ops])
    missing = [n for n in needed
               if not re.search(rf"\bmodule\s+{n}\b", source)]
    if missing:
        return None, f"source is missing module(s): {', '.join(missing)}"
    return {
        "name": str(head.get("name") or "unnamed")[:40],
        "style": style, "latency": latency,
        "method": str(head.get("method") or "unstated")[:40],
        "methods": {str(k): str(v)[:40]
                    for k, v in dict(head.get("methods") or {}).items()},
        "source": source,
    }, None


_OP_CONTRACT = _prompt("op-contract.md")


#: Constrained-decoding schemas (D413). With these the server CANNOT emit a reply
#: missing the header or the source -- the failure class that cost this campaign more
#: rounds than any real design error. `source` is plain SystemVerilog text, no fence.
def design_schema(op: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "latency": {"type": "integer", "minimum": 0, "maximum": 32},
            "method": {"type": "string"},
            "source": {"type": "string",
                       "description": f"complete SystemVerilog module nlu_{op}"},
            "tables": _table_schema(),
        },
        "required": ["name", "latency", "method", "source"],
    }


def plan_schema(todo: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "next": {"type": "string", "enum": list(todo)},
            "method": {"type": "string"},
            "why": {"type": "string"},
        },
        "required": ["next", "method"],
    }


def parse_structured_design(reply: str, op: str) -> tuple[dict | None, str | None]:
    """A schema-constrained reply -> candidate. The shape is guaranteed by decoding,
    so the only checks left are the ones about CONTENT: the module must actually be
    named nlu_<op>, and latency must be sane."""
    from flux_llm import strip_markdown_fence

    # Raw JSON first: the source field may itself contain a ```verilog fence, and
    # stripping fences before parsing would hijack the reply (caught by the tests).
    doc = None
    for text in (reply, strip_markdown_fence(reply)):
        try:
            doc = json.loads(text)
            break
        except Exception:  # noqa: BLE001
            continue
    if not isinstance(doc, dict):
        return None, "structured reply was not a JSON object"
    source = str(doc.get("source", ""))
    # a model may still wrap its source in a fence inside the JSON string
    fence = re.search(r"```(?:system)?verilog\s*\n(.*?)```", source, re.S | re.I)
    if fence:
        source = fence.group(1)
    if not re.search(rf"\bmodule\s+nlu_{op}\b", source):
        return None, f"source does not define module nlu_{op}"
    try:
        latency = int(doc.get("latency", 0))
    except (TypeError, ValueError):
        return None, "latency must be an integer"
    if not 0 <= latency <= 32:
        return None, f"latency {latency} outside 0..32"
    return {"tables": list(doc.get("tables") or []),
            "name": str(doc.get("name") or f"{op}_design")[:40], "style": "per-op",
            "latency": latency, "method": str(doc.get("method") or "unstated")[:40],
            "methods": {op: str(doc.get("method") or "unstated")[:40]},
            "source": source}, None


def patch_schema() -> dict:
    """Edits, not a rewrite (D414). Each edit is an exact find/replace pair, the way
    a coding agent edits a file -- so a fix touches three lines and cannot corrupt the
    other three hundred."""
    return {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "find": {"type": "string"},
                        "replace": {"type": "string"},
                        "nth": {"type": "integer", "minimum": 1,
                                "description": "which occurrence, when the anchor "
                                               "is not unique"},
                    },
                    "required": ["find", "replace"],
                },
            },
            "why": {"type": "string"},
            "tables": _table_schema(),
        },
        "required": ["edits"],
    }


def patch_prompt(op: str, source: str, failure: str, view: str | None = None) -> str:
    """Ask for the SMALLEST edit that fixes the failure, against numbered source
    (or a pre-rendered window of it, D422)."""
    numbered = view if view is not None else "\n".join(
        f"{i + 1:4d} | {ln}" for i, ln in enumerate(source.splitlines()))
    return (
        f"Your `nlu_{op}` module was refused:\n\n{failure}\n\n"
        "Fix it with the SMALLEST possible EDITS -- do not rewrite the module. Each "
        "edit is an exact find/replace on the source text: `find` must appear EXACTLY "
        "ONCE (include enough surrounding text to be unique) and is replaced verbatim "
        "by `replace`. Change only what the error points at.\n\n"
        + ("If the failures say a whole region is ALL FAIL, the core is missing, not "
         "mis-tuned: build it, and if it needs a table of constants REQUEST the table "
         "in \"tables\" (the harness computes it exactly and inserts NAME(idx) as a "
         "function) instead of typing values.\n\n" if "ALL FAIL" in failure else "")
        + 'Reply with ONLY JSON: {"edits": [{"find": "...", "replace": "..."}], '
        '"tables": [], "why": "<one sentence>"}\n\n'
        f"Current source of nlu_{op} (line numbers are for reading only -- never "
        f"include them in find/replace):\n\n{numbered}\n")


def parse_reply_tables(reply: str) -> list[dict]:
    """The `tables` requests on any structured reply (design or patch), or []."""
    from flux_llm import strip_markdown_fence

    for text in (reply, strip_markdown_fence(reply)):
        try:
            doc = json.loads(text)
            if isinstance(doc, dict):
                return list(doc.get("tables") or [])
        except Exception:  # noqa: BLE001
            continue
    return []


def parse_patch(reply: str) -> tuple[list[dict] | None, str]:
    """(edits, why) from a patch reply, or (None, reason)."""
    from flux_llm import strip_markdown_fence

    doc = None
    for text in (reply, strip_markdown_fence(reply)):
        try:
            doc = json.loads(text)
            break
        except Exception:  # noqa: BLE001
            continue
    if not isinstance(doc, dict):
        return None, "patch reply was not a JSON object"
    edits = doc.get("edits")
    if not isinstance(edits, list) or not edits:
        return None, "patch reply carried no edits"
    out = []
    for e in edits:
        if not isinstance(e, dict) or "find" not in e or "replace" not in e:
            return None, "an edit lacked find/replace"
        edit = {"find": str(e["find"]), "replace": str(e["replace"])}
        if isinstance(e.get("nth"), int):
            edit["nth"] = e["nth"]
        out.append(edit)
    return out, str(doc.get("why", ""))[:120]


def _find_span(source: str, find: str, nth: int | None = None
               ) -> tuple[tuple[int, int] | None, str | None]:
    """Locate `find` in `source`: exactly first, then WHITESPACE-TOLERANT (D414
    refinement). The measured failure was the model reconstructing an anchor with
    different spacing or line breaks -- a formatting difference, not a wrong
    intent. Tolerance never weakens the uniqueness rule: an anchor matching more
    than once is still refused."""
    def _lines(spans):
        return ", ".join(str(source.count("\n", 0, a) + 1) for a, _b in spans)

    exact = []
    start = source.find(find)
    while start >= 0:
        exact.append((start, start + len(find)))
        start = source.find(find, start + 1)
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        if nth is not None and 1 <= nth <= len(exact):
            return exact[nth - 1], None
        return None, (f"appears {len(exact)} times (lines {_lines(exact)}), must be "
                      "unique -- add surrounding lines to the anchor, or set \"nth\" "
                      "to the occurrence you mean")
    tokens = find.split()
    if not tokens:
        return None, "empty `find`"
    pattern = re.compile(r"\s+".join(re.escape(t) for t in tokens))
    hits = [(m.start(), m.end()) for m in pattern.finditer(source)]
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        if nth is not None and 1 <= nth <= len(hits):
            return hits[nth - 1], None
        return None, (f"appears {len(hits)} times ignoring whitespace (lines "
                      f"{_lines(hits)}), must be unique -- add surrounding lines, or "
                      "set \"nth\" to the occurrence you mean")
    return None, "text is not in the source"


def apply_patch(source: str, edits: list[dict]) -> tuple[str | None, str | None]:
    """Apply find/replace edits in order. An edit whose `find` is absent or ambiguous
    is REFUSED with the reason (ambiguity is a bug, not something to guess at), and
    the caller hands that reason back to the model."""
    out = source
    for i, e in enumerate(edits, 1):
        find, repl = e["find"], e["replace"]
        if not find:
            return None, f"edit {i}: empty `find`"
        nth = e.get("nth")
        span, err = _find_span(out, find, int(nth) if isinstance(nth, int) else None)
        if span is None:
            return None, f"edit {i}: `find` {err}: {find[:70]!r}"
        out = out[:span[0]] + repl + out[span[1]:]
    if out == source:
        return None, "the edits changed nothing"
    return out, None


def plan_prompt(*, todo: list[str], admitted: list[str],
                partials: dict[str, str], human: str | None = None,
                prototype: bool = True) -> str:
    """The model PLANS the next step (D412: agentic, step-by-step -- the way a Claude
    or GPT agent works a problem). It sees what is proven, what is left, and how close
    each unproven operator is, and chooses ONE operator to attempt next. Advisory: the
    loop enforces the exhaustive gate regardless, and falls back to difficulty order
    if the choice is unparseable. `prototype` says whether the generator will prove the
    method in Python first (D424) or write the RTL directly (D472): the plan is told
    which, so it names an algorithm in the first case and a design in the second."""
    done = ", ".join(admitted) or "(none yet)"
    stage = ("The generator will first PROVE your method as a Python prototype against the "
             "full domain (seconds per attempt) and only then transcribe it to Verilog, so "
             "name the algorithm -- reduction, table, reconstruction, rounding -- not the "
             "Verilog." if prototype else
             "The generator writes the SystemVerilog directly and iterates against the unit "
             "tests, so name the method AND the datapath shape -- reduction, table, "
             "reconstruction, rounding, and how the widths fall out.")
    left = "\n".join(
        f"  * {op}" + (f" -- closest so far: {partials[op]}" if op in partials else
                       " -- not attempted")
        for op in todo)
    parts = [p for p in (
        human,
        "You are designing an FP16 non-linear unit ONE OPERATOR AT A TIME. Each "
        "operator you get within 1 ULP on all 65536 inputs is FROZEN and kept; you "
        "never redo it. Work the tractable ones first to build momentum and a "
        "datapath you can reuse (recip/rsqrt are one Newton iteration from a seed "
        "table; exp/log need range reduction; sigmoid/tanh/gelu can reuse exp/tanh). "
        + stage,
        f"PROVEN and frozen: {done}\nSTILL TO DO:\n{left}",
        ("Every PROVEN operator is a block you can call in the next one -- "
         + ", ".join(f"{op}_fp16(x)" for op in admitted)
         + " -- with fp16_add/fp16_sub/fp16_mul/fp16_neg between them (each rounds to FP16; "
         "a composition may need a wider intermediate to stay within 1 ULP). Say so in the "
         "method when you mean to compose: e.g. \"recip of (1 + exp of -x)\"." if admitted else ""),
        'Reply with ONLY JSON: {"next": "<operator>", "method": "<how you will do '
        'it>", "why": "<one sentence>"}',
    ) if p]
    return "\n\n".join(parts)


def parse_plan(reply: str, todo: list[str]) -> tuple[str | None, str]:
    """(chosen operator, method) from a plan reply; operator must be in `todo`."""
    from flux_llm import strip_markdown_fence

    try:
        doc = json.loads(strip_markdown_fence(reply))
        op = str(doc.get("next", "")).strip()
        if op in todo:
            return op, str(doc.get("method", ""))[:300]
    except Exception:  # noqa: BLE001
        pass
    return None, ""


_REPLY_SHAPE = (
    'Reply with ONLY a JSON object: {{"name": "<short>", "latency": <int>, '
    '"method": "<method>", "tables": [{{"name": "TWO_POW_F", "func": "exp2", "lo": 0, '
    '"hi": 1, "entries": 64, "format": "ufixed", "frac_bits": 12}}], '
    '"source": "<the complete SystemVerilog module nlu_{op}, as a JSON string, calling '
    'TWO_POW_F(idx) where it needs the table>"}} -- "tables" may be [] when the design '
    'needs none.')


def op_static_prefix(op: str, *, knowledge: str, library: str = "", example: str = "") -> str:
    """The part of every `nlu_<op>` prompt that never changes between turns (D422):
    the knowledge sheet, the library, the interface contract, a worked example (a proven
    floor, D477) and the reply shape. The loop puts it FIRST so the model server's prefix
    cache is reused."""
    return "\n\n".join(p for p in (knowledge, library, _OP_CONTRACT.format(op=op), example,
                                    _REPLY_SHAPE.format(op=op)) if p)


PROTO_CONTRACT = _prompt("proto-contract.md")


def design_op_prompt(op: str, *, ulp_budget: int, knowledge: str, method: str = "",
                     human: str | None = None, prior_source: str = "",
                     prior_why: str = "", with_static: bool = True) -> str:
    """Design (or rework) ONE operator's module. `prior_source` present => rework it."""
    if prior_source:
        head = (f"Improve this `nlu_{op}` module -- it COMPILES but is refused: "
                f"{prior_why}\nKeep what works, fix the failing region. ")
        tail = f"\n\nCurrent source:\n\n```verilog\n{prior_source}```\n"
    else:
        head = (f"Design the `nlu_{op}` module: FP16 {op} within {ulp_budget} ULP of "
                f"the reference on ALL 65536 inputs (checked exhaustively -- a corner "
                f"case is a refusal with the failing input attached). ")
        tail = ""
    uses_table = bool(re.search(r"rom|table|lut|lookup|interpol|seed|coeff",
                                (method or "") + (prior_why or ""), re.I))
    nudge = ("" if not uses_table else
             "YOUR METHOD USES A TABLE. Do NOT type its constants -- every previous "
             "attempt that typed constants produced a step function. Put the table in "
             "\"tables\" (the harness computes and rounds it exactly) and call NAME(idx) "
             "in your source. ")
    parts = [p for p in (
        human,
        head + (f"Suggested method: {method}. " if method else "") + nudge
        + "Small area and high fmax after the gate.",
        knowledge if with_static else "",
        _OP_CONTRACT.format(op=op) if with_static else "",
        (_REPLY_SHAPE.format(op=op) if with_static else "") + tail,
    ) if p]
    return "\n\n".join(parts)


def op_repair_prompt(op: str, source: str, failure: str, with_static: bool = True) -> str:
    static = ("\n\n" + _OP_CONTRACT.format(op=op) + "\n\n" + _REPLY_SHAPE.format(op=op)
              if with_static else "")
    return (f"Your `nlu_{op}` module was refused:\n\n{failure}\n\nFix it, change as "
            "little as possible." + static
            + "\n\nCurrent source:\n\n```verilog\n" + source + "```\n")


def improve_prompt(candidate: dict[str, Any], report: str, *, ulp_budget: int) -> str:
    """Rework a design that COMPILES but is not accurate enough (D411, Cedric's rule:
    rework a good direction rather than restart). Distinct from repair_prompt, whose
    "change as little as possible" is wrong here -- accuracy needs the MATH to change
    while the structure that survived the tools stays. Carries the worst inputs so the
    model attacks the failing region, not the whole function."""
    return (
        "This is the BEST NLU design so far and it COMPILES -- improve its ACCURACY. "
        f"It is refused only because {report}\n\n"
        f"Keep the module structure and the parts already within {ulp_budget} ULP; "
        "change the math where it is wrong (usually the approximation core or a "
        "range-reduction/saturation boundary the worst cases point at). Do NOT start "
        "a different design -- extend THIS one.\n\n"
        "Reply with the same DESIGN: header line (updated if the method changed) and "
        "the complete SystemVerilog in one ```verilog fence.\n\n"
        "Current source:\n\n```verilog\n" + candidate["source"] + "```\n")


def repair_prompt(candidate: dict[str, Any], failures: str) -> str:
    return (
        f"Your NLU design `{candidate['name']}` was refused:\n\n{failures}\n\n"
        "Fix it. Keep the declared style and latency (or update the DESIGN line if "
        "they must change); change as little else as possible.\n\n"
        + _CONTRACT.format(opcodes=", ".join(f"{k}={v}" for k, v in OPCODES.items()))
        + "\n\n" + _DESIGN_SHAPE
        + "\n\nYour previous source:\n\n```verilog\n" + candidate["source"] + "```\n"
    )


def test_author_prompt(*, ops: tuple[str, ...], human: str | None = None) -> str:
    parts = [p for p in (
        human,
        "You are the TEST AUTHOR for an FP16 hardware non-linear unit. For each "
        f"operator ({', '.join(ops)}) name the inputs most likely to break a "
        "hardware approximation: range-reduction seams, saturation thresholds, "
        "subnormals, exact powers of two, values where the function crosses a "
        "representable boundary, both signs of zero. 8 to 24 inputs per operator, "
        "as FP16 bit patterns in hex.",
        'Reply with ONLY JSON: {"vectors": {"<op>": ["0x....", ...], ...}, '
        '"why": "<one sentence>"}',
    ) if p]
    return "\n\n".join(parts)
