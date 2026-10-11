"""What the glue helpers share (D973): run a command given after `--`, and read `name=value`."""

from __future__ import annotations

import re
import shlex
import subprocess

NAME_VALUE = re.compile(r"(?:^|(?<=\s))([A-Za-z_][\w.]*)=([-+0-9.eE]+|nan|inf|-inf)(?=\s|$)", re.M)


def command(rest: list[str]) -> list[str]:
    """The command after `--`: its words, or one string split like a shell would."""
    rest = rest[1:] if rest[:1] == ["--"] else rest
    return shlex.split(rest[0]) if len(rest) == 1 else rest


def run(cmd: list[str], timeout_s: float) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout_s)


def numbers(text: str) -> dict[str, float]:
    """Every `name=value` in `text`, the first of each name (as the loop reads a stage)."""
    out: dict[str, float] = {}
    for name, value in NAME_VALUE.findall(text):
        out.setdefault(name, float(value))
    return out
