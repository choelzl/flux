"""A model writes a multiplier; the tools decide whether it is one (D365).

Beyond the four enumerated multiplier structures, a model is asked for one SystemVerilog
module with a fixed interface (`a`, `w` in, `p` out, signed at the workload's precision). The
PE around it, the pipeline and the vectors are generated: the model owns the mechanism, the
harness owns the structure.

A design that compiles and passes its vectors is kept in `applications/macarray/invented/`
and joins the multiplier menu of later runs. Compile errors and failing vectors are fed back,
bounded.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from flux_codegen_rtl_harness import LINT_PRAGMA, fenced_module, lint_relaxed, sv_refusal  # noqa: F401 -- re-exported

from .config import Shape

if TYPE_CHECKING:
    from flux_codegen_rtl_harness import Golden

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


_IDEA = re.compile(r"^\s*IDEA:\s*(.+)$", re.MULTILINE)


def parse_module(name: str, reply: str) -> tuple[str, str] | None:
    """(source, idea) from a reply, or None when no module of that name is in it."""
    source = fenced_module(name, reply)
    if source is None:
        return None
    idea = _IDEA.search(reply)
    return source, (idea.group(1).strip()[:300] if idea else "")


def refusal_reason(source: str) -> str | None:
    """What the rules forbid that the text contains, checked before any tool runs: the
    harness's screen (sequential logic, size casts, system tasks), then this world's rule."""
    why = sv_refusal(source, combinational=True)
    if why:
        return why.replace("the module", "the multiplier")
    if re.search(r"\bassign\s+p\s*=\s*a\s*\*\s*w\s*;", source):
        return "`p = a * w` is the behavioral design already in the space"
    return None


def multiplier_golden(shape: Shape) -> "Golden":
    """The multiplier as a golden model: signed a x w -> p, checked with the harness's shared
    vectors (every corner pairwise, since sign combinations break multipliers, then random)."""
    from flux_codegen_rtl_harness import Golden

    return Golden(ports=({"name": "a", "dir": "in", "bits": max(2, shape.in_bits)},
                         {"name": "w", "dir": "in", "bits": max(2, shape.w_bits)},
                         {"name": "p", "dir": "out", "bits": shape.product_bits}),
                  fn=lambda a, w: {"p": a * w}, count=12, seed=f"{shape.in_bits}x{shape.w_bits}",
                  behavior=f"signed int{shape.in_bits} x int{shape.w_bits} -> int{shape.product_bits}")


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


__all__ = ["INVENTED_DIR", "Invention", "build_prompt", "library",
           "multiplier_golden", "parse_module", "record_measurement",
           "refusal_reason", "repair_prompt"]
