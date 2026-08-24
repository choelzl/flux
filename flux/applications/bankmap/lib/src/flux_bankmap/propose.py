"""Asking a model for a mapping function, and giving it what the solver could not do.

The model is consulted after z3: z3 settles the linear (XOR-fold) question exactly, so a model
only adds the non-linear family (rotations, adds before the fold, prime moduli). The prompt
carries the verdicts so far -- baseline, z3's conclusion, counter-examples, the checker's rules.
Every proposal is still checked exhaustively; a failure is refused with its worst window so the
next round can be told.
"""

from __future__ import annotations

import json

from .mapping import Expr, InvalidExpression, Mapping, XorFold, from_dict

RULES = """\
HARD RULES:
  * A mapping is conflict-free for a stride s if for EVERY start address a, the N addresses
    a, a+s, a+2s, ..., a+(N-1)s land in N distinct banks. Every start address, not most.
  * You may answer in two forms:
      {"kind": "xor-fold", "taps": [[bits for bank bit 0], [bits for bank bit 1], ...]}
        -- each bank bit is the XOR of the listed ADDRESS bit indices (0 = least significant).
      {"kind": "expr", "text": "<expression in a>"}
        -- an integer expression over the address `a` using + - * % ^ & | << >> and integer
           constants; the result is taken mod B. Nothing else: no functions, no other names.
  * Hardware matters and is scored: XOR/AND/shift are cheap, an adder costs ~8, a multiply by a
    non-power-of-two ~64, a modulo by a non-power-of-two ~128 (a real divider on the address
    path). A conflict-free mapping that is cheaper wins.
  * Do not propose the plain modulo, or anything the solver already proved impossible.
"""


def build_prompt(request, *, baseline_summary: str, z3_summary: str,
                 counter_examples: list[str], count: int, tried: list[tuple[str, str]],
                 problem: str | None = None, guidance: str | None = None) -> str:
    """The proposal prompt: the requirement, the verdicts so far, the rules, the output shape."""
    goal = problem or (
        f"Find an address-to-bank mapping for a memory with {request.banks} banks that lets "
        f"{request.concurrent} concurrent accesses proceed without a bank conflict for each of "
        f"the strides {list(request.strides)} (in words), for every start address in a "
        f"{request.address_bits}-bit space.")
    ce = "\n".join(f"  * {c}" for c in counter_examples[:6]) or "  (none yet)"
    hist = "\n".join(f"  * {d}: {why}" for d, why in tried[-8:]) or "  (nothing yet)"
    human = f"{guidance}\n\n" if guidance else ""
    return f"""{human}{goal}

{("CROSSBAR STAGES, each a conflict point in its own right: " + "; ".join(st.describe() for st in request.stages) + ". A window may not load any stage resource beyond its capacity, on top of landing in distinct banks.") if request.stages else ""}
SHAPE: `a` is a {request.address_bits}-bit address (bits a0..a{request.address_bits - 1}); there are
{request.banks} banks, so a mapping produces EXACTLY {request.bank_bits} bank bits -- an xor-fold
must list exactly {request.bank_bits} tap groups, and an expression's value is taken mod {request.banks}.

WHAT IS KNOWN:
  * the plain modulo mapping: {baseline_summary}
  * the linear (XOR-fold) family, searched exactly by a SAT solver: {z3_summary}
COUNTER-EXAMPLES the checker found against the best attempts so far:
{ce}
ALREADY TRIED (do not repeat):
{hist}

{RULES}
Think about WHY the failing strides collide: which address bits vary across the window and
which do not, and what a bank bit must depend on so that they separate. A stride that is a
multiple of 2^k leaves the low k bits constant across the window, so a bank bit that reads
only low bits cannot separate it; a stride of 1 makes the low bits the ONLY thing that varies,
so bank bits must still read them. Non-linear ideas that sometimes reconcile the two: add a
rotated or shifted copy of the address before folding, fold in a carry, use a modulus that is
coprime to every stride.

Propose {count} DIFFERENT mappings. Reply with ONLY a JSON array of objects, each with the
fields above plus "why": one sentence on the idea. No prose outside the JSON, no markdown fence.
"""


def parse_proposals(text: str, bank_bits: int | None = None) -> list[tuple[Mapping, str]]:
    """Turn a reply into `(mapping, why)` pairs, dropping what cannot be built at all.

    Malformed and illegal are different: an object with an unknown kind has no mapping in it to
    refuse, so it is dropped here; a mapping that builds but conflicts survives to be refused by
    the checker WITH its counter-example, which is what the next round learns from.
    """
    from flux_llm import strip_markdown_fence

    raw = strip_markdown_fence(text)
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < 0:
        return []
    try:
        items = json.loads(raw[start:end + 1])
    except ValueError:
        return []
    out: list[tuple[Mapping, str]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            mapping = from_dict(item)
        except (KeyError, ValueError, TypeError, InvalidExpression):
            continue
        if bank_bits is not None and isinstance(mapping, XorFold) and len(mapping.taps) != bank_bits:
            # Too many bank bits: refuse visibly rather than silently ignoring the extra rows.
            continue
        out.append((mapping, str(item.get("why", ""))[:200]))
    return out


__all__ = ["RULES", "build_prompt", "parse_proposals", "Expr", "XorFold"]
