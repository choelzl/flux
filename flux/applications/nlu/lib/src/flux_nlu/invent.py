"""The NLU study's model roles: test author, designer, repairer.

The framework states the interface contract and hands over method knowledge, the record and
the operator table; the model decides method, sharing and pipelining. Every reply is judged by
tools (Verilator, the exhaustive ULP check, yosys, OpenROAD), never by what a prompt says.
"""

from __future__ import annotations

from pathlib import Path

import json
import re
from typing import Any

from flux_loop import patch_prompt as _patch_prompt, patch_schema as _patch_schema

from .tables import table_schema as _table_schema

__all__ = ["PROTO_CONTRACT", "design_op_prompt", "design_schema", "op_repair_prompt",
           "op_static_prefix", "parse_design", "parse_reply_tables", "parse_structured_design",
           "patch_prompt", "patch_schema", "plan_prompt", "plan_schema", "test_author_prompt"]

#: The prompts' prose as files beside the application (D532), read once at import;
#: `{part}`-style placeholders are filled by the functions below.
_PROMPTS = Path(__file__).resolve().parents[3] / "prompts"


def _prompt(name: str) -> str:
    return (_PROMPTS / name).read_text()


def parse_design(reply: str, *, ops: tuple[str, ...]) -> tuple[dict[str, Any] | None, str | None]:
    """(candidate, None) or (None, refusal reason). The candidate carries the model's
    declared knobs plus the source; only the structure is trusted, the tools check claims."""
    # The header may be pretty-printed across lines, so a single-line match is only the
    # first try; the fallback brace-matches from the first "DESIGN" occurrence.
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


#: Constrained-decoding schemas (D413): the server cannot emit a reply missing the header
#: or the source. `source` is plain SystemVerilog text, no fence.
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
    """A schema-constrained reply -> candidate. Decoding guarantees the shape, so only
    content is checked: the module is named nlu_<op> and latency is sane."""
    from flux_llm import strip_markdown_fence

    # Raw JSON first: the source field may itself contain a ```verilog fence, and
    # stripping fences before parsing would break the reply.
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
    """The loop's find/replace edits (D414) plus this world's `tables` requests (D420)."""
    schema = _patch_schema()
    schema["properties"]["tables"] = _table_schema()
    return schema


def patch_prompt(op: str, source: str, failure: str, view: str | None = None) -> str:
    """The loop's patch request for `nlu_<op>` (the smallest edits against numbered source,
    or a window of it, D422), plus what this world's replies may carry: table requests."""
    tables = ('If the failures say a whole region is ALL FAIL, the core is missing, not '
              'mis-tuned: build it, and if it needs a table of constants REQUEST the table '
              'in "tables" (the harness computes it exactly and inserts NAME(idx) as a '
              'function) instead of typing values.\n\n' if "ALL FAIL" in failure else "")
    return (_patch_prompt(f"nlu_{op}", source, failure, view=view)
            + f'\n{tables}The JSON may also carry "tables": [] -- table requests as in the '
            'design reply; a table you already requested is placed again when you call it.\n')


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


def plan_prompt(*, todo: list[str], admitted: list[str],
                partials: dict[str, str], human: str | None = None,
                prototype: bool = True) -> str:
    """The model plans the next step: from what is proven, what is left and how close each
    unproven operator is, it picks one operator to attempt.

    Advisory: the gate still applies, and an unparseable choice falls back to difficulty
    order. `prototype` says whether the method is proven in Python first (D424) or the RTL
    is written directly, so the plan names an algorithm or a design accordingly."""
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


_REPLY_SHAPE = (
    'Reply with ONLY a JSON object: {{"name": "<short>", "latency": <int>, '
    '"method": "<method>", "tables": [{{"name": "TWO_POW_F", "func": "exp2", "lo": 0, '
    '"hi": 1, "entries": 64, "format": "ufixed", "frac_bits": 12}}], '
    '"source": "<the complete SystemVerilog module nlu_{op}, as a JSON string, calling '
    'TWO_POW_F(idx) where it needs the table>"}} -- "tables" may be [] when the design '
    'needs none.')


def op_static_prefix(op: str, *, knowledge: str, library: str = "", example: str = "") -> str:
    """The part of every `nlu_<op>` prompt that never changes between turns: knowledge sheet,
    library, interface contract, a worked example and the reply shape. It goes first so the
    server's prefix cache is reused."""
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
