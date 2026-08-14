"""`flux_backend_health` — what an agent can actually run right now (docs/decisions.md D156).

Every other node here assumes its backend works and surfaces a tool error when it does not. That
is fine for a human reading a traceback and bad for an agent choosing a backend: a Docker daemon
that stopped, a missing `verilator`, or an Ollama that is not up all arrive as opaque failures deep
inside an adapter, after the agent has already committed to a plan.

This session supplied the motivating cases rather than hypotheticals — Docker became unreachable
mid-run (three conformance tests failed on `permission denied ... /var/run/docker.sock`), and the
Nix daemon died (`Connection refused`), each time surfacing as an error about something else.

**Deliberately a cheap probe, and it says so.** It checks that an adapter *imports* and that its
external tool is *present* — not that an evaluation will succeed. A binary can exist and still be
broken, which is exactly what nixchip's CACTI did (D145: installed, ran, segfaulted). Reporting
"available" here is a statement about prerequisites, never a promise about results.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from chia.base.ChiaFunction import ChiaFunction

# The external tool each backend needs on PATH, where it is a plain executable. Backends absent
# from this map need only their Python adapter (plus whatever they clone or build on first use).
_REQUIRED_TOOL: dict[str, str] = {
    "rtl": "verilator",
    "systemc": "g++",
    "booksim": "git",
    "noxim": "git",
    "gem5": "git",
    "cacti": "git",
    "openroad": "openroad",
    "dramsim3": "git",
}
# Backends whose external tool is a container image rather than a binary on PATH. Timeloop is no
# longer *only* that — it has a hermetic runner too (docs/decisions.md D206) — but Docker is still
# what it uses unless a caller opts in, so this is where its probe starts.
_DOCKER_BACKENDS = frozenset({"timeloop"})


@dataclass(frozen=True, slots=True)
class BackendHealth:
    name: str
    importable: bool
    tool_available: bool
    detail: str

    @property
    def usable(self) -> bool:
        return self.importable and self.tool_available

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "importable": self.importable,
            "tool_available": self.tool_available, "usable": self.usable, "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    backends: list[BackendHealth] = field(default_factory=list)

    @property
    def usable_backends(self) -> list[str]:
        return [b.name for b in self.backends if b.usable]

    def to_dict(self) -> dict[str, Any]:
        return {
            "backends": [b.to_dict() for b in self.backends],
            "usable_backends": self.usable_backends,
            "checked": "adapter import + external tool presence; NOT a guarantee that an "
                       "evaluation succeeds (docs/decisions.md D156)",
        }


def _docker_reachable() -> tuple[bool, str]:
    """`docker info`, not `which docker`. The client being installed says nothing about the daemon
    being up or the user being in the right group — the exact failure this session hit, where the
    binary was present and the socket refused the connection."""
    if shutil.which("docker") is None:
        return False, "docker not on PATH"
    try:
        proc = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"docker info failed: {type(exc).__name__}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, f"docker daemon unreachable: {tail[-1][:120] if tail else 'unknown error'}"
    return True, "docker daemon reachable"


def _hermetic_timeloop_installed() -> bool:
    """Whether the non-Docker Timeloop runner exists here (docs/decisions.md D206). Distinct from
    whether it is *selected*, which is the caller's opt-in and not something to infer."""
    try:
        from flux_evaluator_timeloop.adapter import local_timeloop_available
    except Exception:  # noqa: BLE001 - an unimportable adapter is simply not an alternative
        return False
    return local_timeloop_available()


def _probe_containerised(name: str, evaluator: Any) -> BackendHealth:
    """Report on the runner this backend would *actually* use, not on Docker unconditionally.

    Since D206, `timeloop` may be configured for its hermetic runner, in which case a dead Docker
    daemon is irrelevant to whether it works — and answering "unusable" there would send an agent
    to a different backend for no reason. The reverse matters too: a reachable daemon while
    `FLUX_TIMELOOP_LOCAL` is set would report on a runner that is not the one about to run.
    """
    if getattr(evaluator, "use_local", False):
        # Construction already refused to hand back a local-configured evaluator without a usable
        # hermetic Timeloop, so reaching here means one exists.
        return BackendHealth(name, True, True, "hermetic runner selected (FLUX_TIMELOOP_LOCAL)")

    ok, detail = _docker_reachable()
    if ok or not _hermetic_timeloop_installed():
        return BackendHealth(name, True, ok, detail)
    # Installed but not opted into: genuinely unusable *as configured*, and saying only "docker
    # unreachable" would hide that the fix is one environment variable rather than a daemon.
    return BackendHealth(
        name, True, False,
        f"{detail}; a hermetic Timeloop is installed — set FLUX_TIMELOOP_LOCAL=1 to use it",
    )


def _probe(name: str) -> BackendHealth:
    from flux_cli.registry import make_evaluator

    try:
        evaluator = make_evaluator(name)
    except Exception as exc:  # an adapter whose deps are missing raises on construction
        return BackendHealth(name, False, False, f"adapter unavailable: {type(exc).__name__}: {exc}"[:200])

    if name in _DOCKER_BACKENDS:
        return _probe_containerised(name, evaluator)

    tool = _REQUIRED_TOOL.get(name)
    if tool is None:
        return BackendHealth(name, True, True, "pure-Python adapter, no external tool required")
    if shutil.which(tool) is None:
        return BackendHealth(name, True, False, f"{tool!r} not on PATH")
    return BackendHealth(name, True, True, f"{tool!r} found")


@ChiaFunction()
def flux_backend_health() -> HealthReport:
    """Report which evaluator backends are usable right now, and why the others are not.

    Intended to be called *before* committing to a backend: an agent that reads `usable_backends`
    can pick one that will run, instead of discovering a stopped Docker daemon inside an
    evaluation it has already started.

    Checks adapter import and external-tool presence only. A tool that exists can still be broken
    (docs/decisions.md D145), so `usable=True` means "prerequisites are present", never "this will
    produce a correct result".
    """
    from flux_cli.registry import available_backends

    return HealthReport(backends=[_probe(name) for name in available_backends()])
