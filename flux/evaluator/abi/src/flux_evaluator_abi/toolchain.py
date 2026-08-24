"""Which build of which tool produced a number (docs/decisions.md D316).

A placed result is a claim about silicon, and it is only reproducible against the toolchain that
produced it. This repo learned that concretely: an OpenROAD bump moved one fabric's frequency from
871 MHz to 738, and moved another from 686 to 597 — across the 600 MHz constraint the whole study
is built on. The store held both eras of measurement, ranked them together on one frontier, and
had no way to tell them apart, because `Provenance` recorded the evaluator and the inputs and
nothing at all about the binaries.

The fingerprint is deliberately cheap. Under nix a tool's store path already contains an exact
content hash of its build (`/nix/store/03ql61r8...-openroad-unstable-2026-07-02`), so resolving the
binary IS the version query and no subprocess runs. Elsewhere it falls back to `--version`, cached
per process, and finally to the resolved path — each less precise than the last, and each honest
about which it used.

NOT a claim that two results with the same fingerprint are comparable in every other respect: the
platform files, the standard-cell library and this repo's own flow scripts all matter too. It
answers the narrower question that was silently going unasked — were these two numbers produced by
the same binaries.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

# The tools whose build actually moves a measured number here.
MEASURING_TOOLS: tuple[str, ...] = ("openroad", "yosys", "verilator")

_NIX_STORE = "/nix/store/"


@lru_cache(maxsize=64)
def tool_fingerprint(binary: str) -> str | None:
    """A stable identifier for the build of `binary` on PATH, or None if it is not there.

    Three tiers, best first, and the answer says which was used so a reader is never left
    guessing how precise it is.
    """
    resolved = shutil.which(binary)
    if resolved is None:
        return None
    real = str(Path(resolved).resolve())
    if real.startswith(_NIX_STORE):
        # /nix/store/<hash>-<name>-<version>/bin/<tool> -> "<hash>-<name>-<version>". The hash is
        # over the full build inputs, so this is exact and free.
        return f"nix:{Path(real[len(_NIX_STORE):]).parts[0]}"
    try:
        out = subprocess.run([resolved, "--version"], capture_output=True, text=True,
                             timeout=20, check=False)
        first = (out.stdout or out.stderr).strip().splitlines()
        if first:
            return f"version:{first[0].strip()[:120]}"
    except (OSError, subprocess.SubprocessError):
        pass
    return f"path:{real}"


def toolchain_fingerprint(tools: tuple[str, ...] = MEASURING_TOOLS) -> dict[str, str]:
    """`{tool: fingerprint}` for those present. Absent tools are omitted rather than recorded as
    None: a result produced without verilator simply has no verilator to pin."""
    found = {t: fp for t in tools if (fp := tool_fingerprint(t)) is not None}
    return found


def differs_from_current(recorded: dict[str, str] | None,
                         tools: tuple[str, ...] = MEASURING_TOOLS) -> list[str]:
    """Tools whose current build differs from the one a stored result recorded.

    An empty list means agreement OR that nothing was recorded, and those are different situations
    — use `is_unattributed` to tell them apart. A result from before this existed is not known to
    match; it is only unlabelled, and reporting it as agreeing would be the same overconfidence
    that made this necessary.
    """
    if not recorded:
        return []
    current = toolchain_fingerprint(tools)
    return sorted(name for name, fp in recorded.items()
                  if name in current and current[name] != fp)


def is_unattributed(recorded: dict[str, str] | None) -> bool:
    """Whether a result predates toolchain recording, and so cannot be compared at all."""
    return not recorded
