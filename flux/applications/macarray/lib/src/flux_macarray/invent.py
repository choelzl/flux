"""A model writes a multiplier; the tools decide whether it is one (D365).

The enumerated space has four multiplier structures. Where it ends, a local model is asked for
a fifth: one SystemVerilog module with a fixed interface (`a`, `w` in, `p` out, all signed at
the workload's precision), and nothing else -- the PE that instantiates it, the pipeline
around it, the vectors that judge it are all generated. The same split as the prefetcher
invention loop (D355): the model owns the mechanism, the harness owns the structure.

A design that compiles and passes its vectors is KEPT in `applications/macarray/invented/`,
winner or not, and joins the multiplier menu of every later run, where the synthesis screen
ranks it against the four built in. Compile errors and failing vectors are fed back, bounded.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Shape
from .verify import golden_vectors

INVENTED_DIR = Path(__file__).resolve().parents[3] / "invented"

RULES = """\
HARD RULES. Breaking any of these means the module is refused before it is measured:

  * Emit ONE module named exactly `{name}`, in one ```verilog fence, and nothing else.
  * Ports, exactly: `input logic signed [{in_hi}:0] a`, `input logic signed [{w_hi}:0] w`,
    `output logic signed [{p_hi}:0] p`. p must equal a * w as signed integers, for EVERY input.
  * Purely combinational: no clock, no reset, no registers, no always @(posedge ...).
  * Verilog-2001 constructs only (wire, assign, always @(*), case, +, -, <<, &, |, ^, ~).
    No SystemVerilog casts like 8'(x), no packages, no functions, no generate, no $ system tasks.
  * Do NOT write `assign p = a * w;` -- that is the behavioral design already in the space.
    Build the product from partial products: a recoding (Booth radix-4 or radix-8), a
    compression tree (3:2 or 4:2 compressors), a modified Baugh-Wooley array, a recursive
    (Karatsuba-style) split -- some structure with a reason to be faster or smaller.
  * Under 60 lines. Spend them on the mechanism, not on comments.
"""


@dataclass(frozen=True)
class Invention:
    name: str
    source: str
    idea: str
    area_um2: float | None = None
    fmax_mhz: float | None = None

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.source.encode()).hexdigest()[:12]


def build_prompt(name: str, shape: Shape, *, beat: str, tried: list[tuple[str, str]],
                 problem: str | None = None, guidance: str | None = None) -> str:
    goal = problem or (
        f"Design a signed {shape.in_bits}x{shape.w_bits}-bit integer multiplier for a "
        f"{shape.lanes}-lane multiply-accumulate processing element on a 7nm-class standard "
        "cell library. The PE sums the products of all lanes every cycle; the multiplier's "
        "delay and area set the array's frequency and size.")
    hist = "\n".join(f"  * {n}: {why}" for n, why in tried[-6:]) or "  (nothing yet)"
    human = f"{guidance}\n\n" if guidance else ""
    return f"""{human}{goal}

THE NUMBER TO BEAT: {beat}

ALREADY TRIED (do not repeat):
{hist}

{RULES.format(name=name, in_hi=shape.in_bits - 1, w_hi=shape.w_bits - 1,
              p_hi=shape.product_bits - 1)}
Reply with one line `IDEA: <the mechanism and why it should be faster or smaller>` and then
the module in one ```verilog fence.
"""


def repair_prompt(name: str, source: str, failure: str, shape: Shape) -> str:
    return f"""Your multiplier `{name}` was refused:

    {failure}

Fix it. Change as little as possible; keep the mechanism. Reply with the corrected complete
module in one ```verilog fence (no IDEA line needed).

{RULES.format(name=name, in_hi=shape.in_bits - 1, w_hi=shape.w_bits - 1,
              p_hi=shape.product_bits - 1)}
Your previous version:

```verilog
{source}```
"""


_FENCE = re.compile(r"```(?:verilog|systemverilog|sv)?\s*\n(.*?)```", re.DOTALL)
_IDEA = re.compile(r"^\s*IDEA:\s*(.+)$", re.MULTILINE)


def parse_module(name: str, reply: str) -> tuple[str, str] | None:
    """(source, idea) from a reply, or None when no module of that name is in it."""
    m = _FENCE.search(reply)
    body = m.group(1) if m else reply
    if f"module {name}" not in body or "endmodule" not in body:
        return None
    start = body.index(f"module {name}")
    end = body.rindex("endmodule") + len("endmodule")
    idea = _IDEA.search(reply)
    return body[start:end].strip() + "\n", (idea.group(1).strip()[:300] if idea else "")


def refusal_reason(source: str) -> str | None:
    """What the rules forbid that the text contains -- checked before any tool runs."""
    if re.search(r"always\s*@\s*\(\s*posedge|always_ff|\breg\b.*<=", source):
        return "sequential logic: the multiplier must be combinational"
    if re.search(r"\bassign\s+p\s*=\s*a\s*\*\s*w\s*;", source):
        return "`p = a * w` is the behavioral design already in the space"
    if re.search(r"\d+'\s*\(", source):
        return "size casts like 8'(x) are SystemVerilog; Yosys's front end rejects them"
    if "$" in re.sub(r"\$signed|\$unsigned", "", source):
        return "system tasks are not synthesizable"
    return None


def multiplier_spec(name: str, shape: Shape, vectors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0", "id": f"macarray/multiplier/{name}", "module_name": name,
        "ports": [{"name": "a", "dir": "in", "dtype": "int", "bits": max(2, shape.in_bits)},
                  {"name": "w", "dir": "in", "dtype": "int", "bits": max(2, shape.w_bits)},
                  {"name": "p", "dir": "out", "dtype": "int", "bits": shape.product_bits}],
        "behavior": f"signed int{shape.in_bits} x int{shape.w_bits} -> int{shape.product_bits}",
        "test_vectors": vectors,
    }


def multiplier_vectors(shape: Shape, *, seed: str, count: int = 12) -> list[dict[str, Any]]:
    """Products at the corners, then random -- the sign combinations are where multipliers die."""
    import random

    rng = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big"))
    lo, hi = -(1 << (shape.in_bits - 1)), (1 << (shape.in_bits - 1)) - 1
    wlo, whi = -(1 << (shape.w_bits - 1)), (1 << (shape.w_bits - 1)) - 1
    corners = [(lo, wlo), (lo, whi), (hi, wlo), (hi, whi), (0, whi), (lo, 0), (-1, -1), (1, -1)]
    out = []
    for n in range(count):
        a, w = corners[n] if n < len(corners) else (rng.randint(lo, hi), rng.randint(wlo, whi))
        out.append({"inputs": {"a": a, "w": w}, "expected": {"p": a * w}})
    return out


#: Verilator runs with every warning fatal (`-Wall`), and the first live inventions all died on
#: WIDTHEXPAND/WIDTHTRUNC -- implicit extension and truncation, which Verilog defines and the
#: golden vectors judge. A width lint is a style bar the generated PEs clear by construction;
#: for a model-written module it is not the question being asked. Correctness still is.
LINT_PRAGMA = "/* verilator lint_off WIDTH */\n/* verilator lint_off UNUSEDSIGNAL */\n"


def lint_relaxed(source: str) -> str:
    return source if source.startswith(LINT_PRAGMA) else LINT_PRAGMA + source


def _first_diagnostic(text: str) -> str:
    """The line that names the problem, out of Verilator's whole complaint."""
    for line in text.splitlines():
        if line.startswith("%Error") or line.startswith("%Warning"):
            return line.strip()[:300]
    return text.strip()[:300]


def check_multiplier(name: str, source: str, shape: Shape, vectors: list[dict[str, Any]],
                     *, timeout_s: float = 120.0) -> str | None:
    """Verilator's verdict on a candidate multiplier: None when it passes, else why not."""
    from flux_codegen_rtl_harness import CompileError, compile_and_run, design_spec_from_dict

    spec = design_spec_from_dict(multiplier_spec(name, shape, vectors))
    try:
        run = compile_and_run(lint_relaxed(source), spec, timeout_s=timeout_s)
    except CompileError as exc:
        return f"did not compile: {_first_diagnostic(str(exc))}"
    if not run.all_passed:
        why = run.failing_vector_lines[0] if run.failing_vector_lines else (
            run.stderr or "no vector passed")
        # The harness names the vector and what came out; the model needs what went IN. The
        # first live repairs were told "p=-256" and nothing else, and fixed nothing.
        m = re.match(r"VECTOR (\d+) FAIL", why)
        if m and int(m.group(1)) < len(vectors):
            v = vectors[int(m.group(1))]
            why += (f" -- for a={v['inputs']['a']}, w={v['inputs']['w']} the product must be "
                    f"{v['expected']['p']}")
        return f"{run.passed_vectors}/{run.total_vectors} vectors passed: {why[:300]}"
    return None


def library(root: Path | None = None) -> list[Invention]:
    """Every kept multiplier, best first (by screened fmax when known)."""
    root = root or INVENTED_DIR
    out: list[Invention] = []
    for meta_path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            meta = json.loads(meta_path.read_text())
            source = (root / f"{meta['name']}.sv").read_text()
        except (OSError, ValueError, KeyError):
            continue
        out.append(Invention(name=meta["name"], source=source, idea=str(meta.get("idea", ""))[:300],
                             area_um2=meta.get("area_um2"), fmax_mhz=meta.get("fmax_mhz")))
    out.sort(key=lambda i: -(i.fmax_mhz or 0.0))
    return out


def next_name(root: Path) -> str:
    existing = [int(m.group(1)) for f in (root.glob("mul*.json") if root.is_dir() else [])
                if (m := re.fullmatch(r"mul(\d+)", f.stem))]
    return f"mul{max(existing, default=0) + 1}"


def invent(shape: Shape, *, rounds: int, ask: Callable[[str], str], beat: str,
           keep_dir: Path | None = None, repair_attempts: int = 2, problem: str | None = None,
           log: Callable[[str], None] = lambda _m: None,
           guidance: str | None = None) -> list[Invention]:
    """`rounds` attempts at a new multiplier; every one that passes its vectors is kept."""
    keep = keep_dir or INVENTED_DIR
    keep.mkdir(parents=True, exist_ok=True)
    vectors = multiplier_vectors(shape, seed=f"{shape.in_bits}x{shape.w_bits}")
    tried: list[tuple[str, str]] = []
    kept: list[Invention] = []
    for r in range(rounds):
        name = next_name(keep)
        log(f"invent: round {r + 1}/{rounds}, asking for `{name}` to beat {beat}")
        reply = ask(build_prompt(name, shape, beat=beat, tried=tried, problem=problem,
                                 guidance=guidance))
        parsed = parse_module(name, reply)
        if parsed is None:
            # Say WHICH way it failed: a reply with no `endmodule` was cut off by the output
            # budget, a reply with a module of another name ignored the rules. Two rounds of
            # a live run reported only "no module" and left both possibilities open.
            why = ("reply cut off before `endmodule` (raise num_predict)"
                   if "module" in reply and "endmodule" not in reply else
                   f"no module named `{name}` in the reply ({len(reply)} chars)")
            log(f"  {why}")
            tried.append((name, why))
            continue
        source, idea = parsed
        log(f"  idea: {idea[:120]}")
        failure = refusal_reason(source) or check_multiplier(name, source, shape, vectors)
        attempt = 0
        while failure and attempt < repair_attempts:
            attempt += 1
            log(f"  refused ({attempt}/{repair_attempts}): {failure[:140]}")
            fixed = parse_module(name, ask(repair_prompt(name, source, failure, shape)))
            if fixed is None:
                log("  the repair returned no module")
                break
            source = fixed[0]
            failure = refusal_reason(source) or check_multiplier(name, source, shape, vectors)
        if failure:
            log(f"  refused: {failure[:140]}")
            tried.append((name, failure[:120]))
            continue
        (keep / f"{name}.sv").write_text(lint_relaxed(source))
        (keep / f"{name}.json").write_text(json.dumps(
            {"name": name, "idea": idea, "shape": shape.describe()}, indent=2) + "\n")
        log(f"  passes {len(vectors)} vectors; kept: {keep / (name + '.sv')}")
        tried.append((name, f"correct: {idea[:80]}"))
        kept.append(Invention(name=name, source=source, idea=idea))
    return kept


def record_measurement(name: str, *, area_um2: float, fmax_mhz: float,
                       root: Path | None = None) -> None:
    """Write what the screen measured beside the kept source, so the menu can be ordered."""
    root = root or INVENTED_DIR
    path = root / f"{name}.json"
    try:
        meta = json.loads(path.read_text())
        meta.update({"area_um2": round(area_um2, 2), "fmax_mhz": round(fmax_mhz, 1)})
        path.write_text(json.dumps(meta, indent=2) + "\n")
    except (OSError, ValueError):
        pass


__all__ = ["INVENTED_DIR", "Invention", "build_prompt", "check_multiplier", "invent",
           "library", "multiplier_spec", "multiplier_vectors", "parse_module",
           "record_measurement", "refusal_reason", "repair_prompt"]
