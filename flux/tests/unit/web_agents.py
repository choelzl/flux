"""D807: the web offers an agent only where its program is found and runnable -- a stand-in
program for each built-in agent, named by FLUX_<NAME>_BIN, for tests of what the web offers."""

from __future__ import annotations

from pathlib import Path

NAMES = ("opencode", "claude", "codex")


def install(monkeypatch, where: Path, names=NAMES) -> dict[str, Path]:
    where = Path(where) / "agent-programs"
    where.mkdir(parents=True, exist_ok=True)
    out = {}
    for n in names:
        p = where / n
        p.write_text(f"#!/bin/sh\necho {n} 1.0\n")
        p.chmod(0o755)
        monkeypatch.setenv(f"FLUX_{n.upper()}_BIN", str(p))
        out[n] = p
    return out
