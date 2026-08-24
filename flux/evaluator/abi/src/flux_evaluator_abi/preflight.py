"""Refuse to start when the tools that would produce the numbers are not there.

Learned by doing it (docs/decisions.md D288): run a study from a shell without `openroad` and
every escalation fails one line at a time while the run CONTINUES, tallying failures per round
and printing a table at the end. Against a warm store that table is fully populated, from rows
measured on an earlier day, and nothing in the output says this run measured none of them. That
is indistinguishable from success at a glance, which is why this is a hard exit and not a warning.
"""

from __future__ import annotations

import shutil


class MissingTools(Exception):
    """Required binaries are absent, so the measured rung cannot run at all."""

    def __init__(self, missing: dict[str, str]) -> None:
        self.missing = missing
        super().__init__("; ".join(f"{tool} ({why})" for tool, why in sorted(missing.items())))


def missing_tools(required: dict[str, str]) -> dict[str, str]:
    """`{tool: what it is needed for}` for every one not on PATH."""
    return {tool: why for tool, why in required.items() if shutil.which(tool) is None}


def require_tools(required: dict[str, str], *, hint: str = "") -> None:
    """Raise `MissingTools` unless every named binary is on PATH.

    `hint` is the command that would provide them, which is the difference between an error a
    reader can act on and one they have to research.
    """
    missing = missing_tools(required)
    if not missing:
        return
    lines = ["the measured rung cannot run, so every number would be modelled or stale:"]
    lines += [f"    missing: {tool:10} needed for {why}" for tool, why in sorted(missing.items())]
    if hint:
        lines.append(f"\nUse the shell that carries them:\n    {hint}")
    error = MissingTools(missing)
    error.args = ("\n".join(lines),)
    raise error
