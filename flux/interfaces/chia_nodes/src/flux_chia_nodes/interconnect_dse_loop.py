"""`flux_interconnect_dse_loop` — the interconnect study as a dispatchable CHIA node (D350):
`flux_interconnect.run_study` unchanged, its result mapped to JSON. It does not move the
search onto Ray: the expensive unit is a whole-fabric placement, which the flow already
spreads across a local pool (`escalation_parallelism`); dispatching those to other machines
is a separate decision with its own measurement (D350's open note).

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

    from ._loop_glue import loop_report

    return loop_report(result, _placed_payload, refused_key="fabric", frontier_from="finalists")
