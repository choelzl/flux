"""Which build of which tool produced a number (D316).

Tool upgrades move measured results (an OpenROAD bump shifted fmax by over 10%), so results
record the toolchain that produced them. Under nix the store path is an exact content hash of
the build, so resolving the binary is the version query; elsewhere it falls back to
`--version` (cached per process), then the resolved path. The prefix says which tier was used.

Same fingerprint means same binaries, not full comparability: platform files, cell library and
flow scripts matter too.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

# The tools whose build actually moves a measured number here.
MEASURING_TOOLS: tuple[str, ...] = ("openroad", "yosys", "verilator")
#: The RECIPE the tools run (D564): part of every measurement's fingerprint beside the tool
#: builds, so a number cached or recorded under another recipe is never taken for this one.
#: "abc-full": Yosys maps with the full ABC script, not `abc -fast`.
FLOW_RECIPE = "abc-full"

_NIX_STORE = "/nix/store/"


@lru_cache(maxsize=64)
def tool_fingerprint(binary: str) -> str | None:
    """A stable identifier for the build of `binary` on PATH, or None if it is not there.

    Prefixed `nix:`, `version:` or `path:` by the tier used, most precise first.
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
    if found:
        found["flow"] = FLOW_RECIPE                      # the recipe is part of the fingerprint (D564)
    return found
