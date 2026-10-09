"""The gate (D652): `flow.test`, named checks run in order."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .commands import _command
from .keys import TaskError


#: D594: a gate test's exit code for "the candidate did not build" (nothing was tested).
BUILD_FAILED = 3


@dataclass(frozen=True)
class Check:
    """One named check of a gate (D652): a command whose failures are counted by `count_re`
    (one integer group), `fail_re` (one match per failure) or, with neither matching, its exit
    code. Exit 3 means the candidate did not build; `builds` (the old `build:` key) makes any
    non-zero exit mean that."""

    name: str
    run: tuple[str, ...]
    count_re: str | None = None
    fail_re: str | None = None
    timeout_s: float = 120.0
    builds: bool = False

    @property
    def rule(self) -> str:
        """The pass rule in words, for `flux task check`."""
        if self.builds:
            return "passes when it exits 0; otherwise the candidate did not build"
        how = (f"`{self.count_re}` reads 0" if self.count_re
               else f"no line matches `{self.fail_re}`" if self.fail_re else "it exits 0")
        return f"passes when {how}; exit 3 = did not build"


class Gate(tuple):
    """How a candidate is checked (D652): its checks, run in order, cheapest first. The first
    that reports failures refuses the design; the checks after it do not run."""

    def named(self, name: str) -> Check | None:
        return next((c for c in self if c.name == name), None)

    @property
    def timeout_s(self) -> float:
        return max((c.timeout_s for c in self), default=120.0)

    def line(self) -> str:
        """The checks as one shell line, for a prompt or an agent's brief."""
        return " && ".join(" ".join(c.run) for c in self)


#: D628: what the application checkers print; a gate that
#: prints no such line is judged by its exit code
DEFAULT_COUNT_RE = r"(\d+) failing"


_GATE_HELP = ("`flow.test` is a command (one check, `test`), or a map from each check's name to its command "
              "or `{run, count_re?, fail_re?, timeout_s?}`, run in order -- a check named `build` refuses on any "
              "non-zero exit; `{artifact}`, `{workdir}`, `{name}`, `{part}`, `{python}`, `{home}` are substituted; "
              "a `flux ...` head runs this flux")
_CHECK_KEYS = ("run", "count_re", "fail_re", "timeout_s")


def _patterns(doc: dict[str, Any], what: str) -> tuple[str | None, str | None]:
    for key in ("count_re", "fail_re"):
        pat = doc.get(key)
        if pat is not None:
            try:
                re.compile(pat)
            except re.error as exc:
                raise TaskError(f"{what}.{key} is not a regex: {exc}") from exc
    return doc.get("count_re") or (None if doc.get("fail_re") else DEFAULT_COUNT_RE), doc.get("fail_re")


def _gate(doc: Any) -> Gate:
    """`flow.test` (D652, D789): a map by name like `flow.measure`, the checks in the order
    written -- each a command or `{run, count_re, fail_re, timeout_s}`. A command alone is one
    check named `test`; a check named `build` refuses on any non-zero exit (did not build)."""
    if isinstance(doc, (str, list)):
        doc = {"test": doc}
    if not isinstance(doc, dict) or not doc:
        raise TaskError(_GATE_HELP)
    loose = sorted(k for k in doc if k in _CHECK_KEYS)
    if loose:
        raise TaskError(f"flow.test: {', '.join(loose)} is a check's setting, said under its name "
                        f"(test: {{run: ..., {loose[0]}: ...}}); flow.test is a map of checks by name")
    checks = []
    for name, c in doc.items():
        name, where = str(name), f"flow.test.{name}"
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
            raise TaskError(f"{where}: a check's name is letters, digits, _ and -")
        if isinstance(c, (str, list)):
            c = {"run": c}
        if not isinstance(c, dict):
            raise TaskError(f"{where}: a command, or {{run, count_re, fail_re, timeout_s}}")
        if "name" in c:
            raise TaskError(f"{where}: a check's name is its key")
        bad = sorted(set(c) - set(_CHECK_KEYS))
        if bad:
            raise TaskError(f"{where}: keys {bad} are not a check's; known: {', '.join(_CHECK_KEYS)}")
        if not c.get("run"):
            raise TaskError(f"{where} needs `run`: its command")
        count_re, fail_re = _patterns(c, where) if name != "build" or c.get("count_re") or c.get("fail_re") else (None, None)
        checks.append(Check(name, _command(c["run"], f"{where}.run"), count_re, fail_re,
                            float(c.get("timeout_s") or 120.0), builds=name == "build"))
    return Gate(checks)


def _gate_doc(gate: Gate) -> Any:
    """The gate as a document says it (D789): a map by name; a bare command when it is all."""
    out: dict[str, Any] = {}
    for c in gate:
        settings = {**({"count_re": c.count_re} if c.count_re and c.count_re != DEFAULT_COUNT_RE else {}),
                    **({"fail_re": c.fail_re} if c.fail_re else {}),
                    **({"timeout_s": c.timeout_s} if c.timeout_s != 120.0 else {})}
        out[c.name] = {"run": list(c.run), **settings} if settings else list(c.run)
    return out
