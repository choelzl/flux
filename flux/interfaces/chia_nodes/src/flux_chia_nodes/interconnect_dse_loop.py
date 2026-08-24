"""`flux_interconnect_dse_loop` — the interconnect study as a dispatchable CHIA node (D350).

WHY THIS EXISTS. `flux_prefetcher_dse_loop` shipped first (D349) and left the two applications
with different front doors: the prefetcher study was callable as a CHIA node, the interconnect
study only as a Python import. That asymmetry was the order the two were built in, not a design
-- D345 made `run_study` importable before "callable as a CHIA node" was a goal, and nothing went
back to add the node. `applications/README` claims the next problem domain gets the same shape;
two shapes for two domains is the thing that claim exists to prevent.

WHAT IT DOES NOT DO. It does not move the search onto Ray. The interconnect's expensive unit is a
whole-fabric placement, and `flux_interconnect.flow` already spreads those across a local pool
(`escalation_parallelism`). Dispatching them remotely would buy other MACHINES, which is a real
gain and a separate decision with its own measurement to make first -- see D350's open note. This
node is the door, not a rewrite of the room behind it.

So it composes: `run_study` unchanged, its result mapped to JSON so a parent orchestrator can read
the answer without importing `flux_interconnect`.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction


def _placed_payload(fabric) -> dict[str, Any]:
    """One placed fabric, JSON-safe.

    `dead_client_ports`/`dead_bank_ports` are carried deliberately: a fabric may legitimately
    over-provision ports, and a caller comparing two answers needs to see that rather than infer
    it from a label (D-the dead-port work).
    """
    return {
        "label": fabric.label,
        "area_mm2": fabric.area_mm2,
        "fmax_mhz": fabric.fmax_mhz,
        "served_per_cycle": fabric.served_per_cycle,
        "power_w": fabric.power_w,
        "latency_cycles": fabric.latency_cycles,
        "dead_client_ports": fabric.dead_client_ports,
        "dead_bank_ports": fabric.dead_bank_ports,
    }


@ChiaFunction()
def flux_interconnect_dse_loop(
    db_path: str,
    *,
    problem: str | None = None,
    clients: int | None = None,
    banks: int | None = None,
    width_bits: int | None = None,
    target_mhz: float | None = None,
    bank_rows: int | None = None,
    max_rounds: int = 16,
    rounds: int | None = None,
    budget: int | None = None,
    llm_round: int = 12,
    decide_on_finalists: int = 5,
) -> dict[str, Any]:
    """Search interconnect fabrics for a requirement and return the one that should be built.

    A larger design has an interconnect in it; this is how the orchestrator responsible for that
    design asks for one. Give it a requirement -- either as `problem` text or as the explicit
    `clients`/`banks`/`width_bits`/`target_mhz` numbers -- and it returns a decision, the
    finalists it placed, what it refused, and what it could not establish.

    Called in-process rather than via `.chia_remote(...)`: this call is already the unit of
    dispatch, the same reasoning every composed node in this package uses.

    `decide_on_finalists` places each of the top N fabrics whole, which is minutes apiece and is
    what makes the answer a measurement rather than a screen. Setting it to 0 makes the run fast
    and the frequency claim unmeasured -- the study reports that in `not_established` rather than
    quietly downgrading the number.
    """
    from flux_interconnect.flow import run_study
    from flux_interconnect.study import InterconnectRequest

    fields = {"db": db_path, "problem": problem, "max_rounds": max_rounds,
              "llm_round": llm_round, "decide_on_finalists": decide_on_finalists}
    for name, value in (("clients", clients), ("banks", banks), ("width_bits", width_bits),
                        ("target_mhz", target_mhz), ("bank_rows", bank_rows),
                        ("rounds", rounds), ("budget", budget)):
        if value is not None:
            fields[name] = value

    result = run_study(InterconnectRequest(**fields))

    return {
        "decision": _placed_payload(result.decision) if result.decision else None,
        "finalists": [_placed_payload(f) for f in result.finalists],
        "refused": [{"fabric": label, "why": why} for label, why in result.refused],
        "lessons": list(result.lessons),
        "not_established": list(result.not_established),
        "met_requirement": result.met_requirement,
        "provenance": dict(result.provenance),
    }
