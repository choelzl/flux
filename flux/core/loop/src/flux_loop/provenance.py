"""What produced a row (docs/decisions.md D510): the code, the tools, the model turn's cost,
the place its traces went.

A number on the record is a claim, and the claim is only as good as what can be said about
how it was made. Before D510 a row carried the evaluator's name and the stage; the wall
time, the tokens a turn spent, the toolchain build (D316 had the fingerprint, nothing wrote
it to the loop's rows), the repository revision and the directory the prompts and replies
were written to were nowhere, or in a temp directory named by a random suffix that nothing
pointed at (38 GB of them, 1,393 directories, on 2026-09-21). Every row the loop writes now
carries a `provenance` document in its candidate's `meta`, and the trace directory is named
by the campaign and the pass so `flux gc` can tell what a record still points at.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = ["git_revision", "stamp", "toolchain", "trace_dir", "trace_root", "turn_cost"]


@lru_cache(maxsize=1)
def git_revision() -> str:
    """The repository's short revision, `+dirty` when the tree differs from it; "" outside a
    repository. Once per process: the tree does not change under a running pass."""
    here = Path(__file__).resolve().parent
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=here, capture_output=True,
                             text=True, timeout=10, check=False).stdout.strip()
        if not rev:
            return ""
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=here,
                               capture_output=True, text=True, timeout=20, check=False).stdout.strip()
        return rev + ("+dirty" if dirty else "")
    except Exception:  # noqa: BLE001 -- no git, no revision; the row says so by its absence
        return ""


@lru_cache(maxsize=1)
def toolchain() -> dict[str, str]:
    """`{tool: build fingerprint}` for the measuring tools on PATH (D316)."""
    try:
        from flux_evaluator_abi import toolchain_fingerprint

        return dict(toolchain_fingerprint())
    except Exception:  # noqa: BLE001
        return {}


def stamp(**more: Any) -> dict[str, Any]:
    """The provenance document of one row: the revision and the toolchain, plus whatever the
    writer knows (seconds, tokens, the trace directory, the batch size)."""
    doc: dict[str, Any] = {"git": git_revision(), "toolchain": toolchain()}
    doc.update({k: v for k, v in more.items() if v is not None and v != ""})
    return doc


def turn_cost(reply: Any) -> dict[str, Any]:
    """What a model turn cost, from its `Reply` (D508): the model, the tokens in and out."""
    if reply is None:
        return {}
    usage = getattr(reply, "usage", None) or {}
    notes = getattr(reply, "notes", None) or {}
    return {k: v for k, v in {"model": notes.get("model"), "tokens_in": usage.get("input_tokens"),
                              "tokens_out": usage.get("output_tokens")}.items() if v is not None}


def trace_root() -> str:
    """Where passes write their traces: `FLUX_TRACE_ROOT`, else `<tmp>/flux-traces`."""
    return os.environ.get("FLUX_TRACE_ROOT") or os.path.join(tempfile.gettempdir(), "flux-traces")


def trace_dir(campaign_id: str | None, problem: str) -> str:
    """A pass's trace directory, made: `<root>/<campaign>/<UTC stamp>` -- by the campaign
    (its first twelve characters) and the moment the pass began, so a row can point at it
    and `flux gc` can keep what the record still names. A run without a record traces
    under the problem's name."""
    who = (campaign_id or "")[:12] or problem
    stamp_ = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    d = os.path.join(trace_root(), who, stamp_)
    n = 0
    while os.path.exists(d):                    # two passes in one second: the second is -1
        n += 1
        d = os.path.join(trace_root(), who, f"{stamp_}-{n}")
    os.makedirs(d, exist_ok=True)
    return d
