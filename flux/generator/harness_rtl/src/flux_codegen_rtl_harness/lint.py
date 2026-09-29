"""Verilator `--lint-only` as a gate check (D653): the warnings that are hardware defects, not
style. The golden test cannot see them when the vectors happen to pass.

* LATCH -- a combinational block that does not assign on every path infers a latch.
* MULTIDRIVEN -- two processes drive one signal: synthesis picks one or shorts them.
* UNOPTFLAT -- circular combinational logic (a loop, or a vector read and written across its
  bits; split it bit by bit if the loop is false).
* COMBDLY -- `<=` in combinational logic: simulation and synthesis disagree.
* BLKANDNBLK -- `=` and `<=` to one variable: a race.
* IMPLICIT -- a net made up by a typo, one bit wide.

Width, unused and naming warnings are style: the golden test judges the values.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .build import _normalized

__all__ = ["DEFECTS", "Lint", "lint_rtl"]

DEFECTS = ("LATCH", "MULTIDRIVEN", "UNOPTFLAT", "COMBDLY", "BLKANDNBLK", "IMPLICIT")

_LINE = re.compile(r"^%(Warning|Error)(?:-([A-Z0-9_]+))?: (.*)$")


@dataclass
class Lint:
    defects: list[str] = field(default_factory=list)   # one line per defect
    error: str = ""                                    # set when the source does not parse or elaborate

    @property
    def ok(self) -> bool:
        return not self.error and not self.defects


def lint_rtl(source: str, module: str, *, extra_sources: dict[str, str] | None = None,
             timeout_s: float = 120.0) -> Lint:
    """Lint `source` with `module` as its top. A defect's code is counted whether Verilator says
    it as a warning or an error; any other error (syntax, an unknown name) means the source does
    not parse."""
    with tempfile.TemporaryDirectory(prefix="flux-rtl-lint-") as tmp:
        work = Path(tmp)
        (work / "dut.sv").write_text(_normalized(source))
        extra = []
        for stem, text in (extra_sources or {}).items():
            (work / f"{stem}.sv").write_text(_normalized(text))
            extra.append(f"{stem}.sv")
        proc = subprocess.run(["verilator", "--lint-only", "-Wall", "-Wno-fatal", "-sv",
                               "--top-module", module, "dut.sv", *extra],
                              capture_output=True, text=True, cwd=work, timeout=timeout_s)
    got = Lint()
    errors: list[str] = []
    for line in (proc.stderr or "").splitlines():
        m = _LINE.match(line)
        if not m or m.group(3).startswith("Exiting due to"):
            continue
        kind, code, text = m.groups()
        text = re.sub(r"^dut\.sv:(\d+):\d+: ", r"line \1: ", text)      # the artifact's own lines
        if code in DEFECTS:
            got.defects.append(f"{code}: {text}")
        elif kind == "Error":
            errors.append(f"{code}: {text}" if code else text)
    if errors or (proc.returncode != 0 and not got.defects):
        got.error = "; ".join(errors) or (proc.stderr or "").strip()[-600:] or f"verilator exited {proc.returncode}"
    return got
