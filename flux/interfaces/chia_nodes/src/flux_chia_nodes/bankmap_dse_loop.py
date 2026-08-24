"""`flux_bankmap_dse_loop` -- the bank-mapping study as a dispatchable CHIA node (D356).

The study is a DOCUMENT (`applications/bankmap/bankmap.problem.yaml`, review 2 step R3) and
this node builds it with the call's ask, runs the loop and maps the result to JSON so a parent
orchestrator can read the answer -- and the Verilog for it -- without importing `flux_bankmap`.
Called in-process: this call is already the unit of dispatch.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction


def _mapping_payload(m, request) -> dict[str, Any]:
    return {"describe": m.describe(), "kind": m.to_dict(), "hardware_cost": m.hardware_cost(),
            "verilog": m.verilog(request.address_bits, request.bank_bits)}


@ChiaFunction()
def flux_bankmap_dse_loop(
    strides: list[int],
    concurrent: int,
    banks: int = 8,
    *,
    address_bits: int = 20,
    z3_seconds: int = 60,
    llm_round: int = 6,
    max_xor_inputs: int | None = None,
    problem: str | None = None,
    db_path: str = "demo-bankmap.db",
    crossbar: str | None = None,
    stage_capacities: list[int] | None = None,
    lanes: int | None = None,
    stages: list[dict[str, Any]] | None = None,
    topology: str | None = None,
    feedback: Any | None = None,
) -> dict[str, Any]:
    """Find an address-to-bank mapping that is conflict-free for N concurrent accesses per stride.

    Checks the plain modulo, searches the XOR-fold family exactly with z3 (finding the cheapest
    conflict-free fold or proving none exists), reports what IS feasible when the request is
    not, then lets a local model propose non-linear mappings, every one of which is checked
    exhaustively over the whole address space. Returns the cheapest conflict-free mapping with
    its Verilog, or the best partial answer labelled as partial.
    """
    from pathlib import Path

    import yaml
    from flux_loop import PromptProblem, TaskSpec, request_for, run_loop

    # the study's document with this call's ask; `crossbar` is the staged tree's spelling
    doc_path = Path(__file__).resolve().parents[4] / "applications" / "bankmap" / "bankmap.problem.yaml"
    doc = yaml.safe_load(doc_path.read_text())
    doc["params"] = {**doc["params"], "strides": [int(s) for s in strides], "concurrent": int(concurrent),
                     "banks": int(banks), "address_bits": int(address_bits), "z3_seconds": int(z3_seconds),
                     "max_xor_inputs": max_xor_inputs, "llm_round": int(llm_round), "problem": problem,
                     "topology": topology or (f"staged:{crossbar}" if crossbar else None),
                     "stage_capacities": list(stage_capacities) if stage_capacities else None,
                     "lanes": lanes, "stages": list(stages or [])}
    prob = PromptProblem(TaskSpec.from_dict(doc, base=doc_path.parent))
    request = request_for(prob.task, db=db_path)
    from ._loop_glue import loop_report, ollama_proposer, optional_proposer

    proposer = optional_proposer(llm_round, lambda: ollama_proposer())
    result = run_loop(prob, request, proposer=proposer, feedback=feedback, log=lambda _m: None)
    world = prob.world
    r = world.request                     # with the wiring the solver chose, when one was free (D372)
    n = r.concurrent
    best = world.decided(result)
    return loop_report(result, refused_key="mapping", frontier_from=None, extra={
        "decision": _mapping_payload(best, r) if best is not None else None,
        "conflict_free": world.conflict_free(result),
        "met_requirement": world.conflict_free(result),
        "hardware_cost": best.hardware_cost() if best is not None else None,
        "request": {"strides": list(r.strides), "concurrent": n, "banks": r.banks,
                    "address_bits": r.address_bits,
                    "stages": [st.describe() for st in r.stages],
                    "topology": r.topology, "notes": list(r.notes)},
        "candidates": [
            {**_mapping_payload(m, r), "conflict_free": v.conflict_free,
             "verdict": v.summary(n), "proposed_by": who} for m, v, who in world.candidates],
        "progress": list(world.progress),
        "provenance": world.provenance(),
        "report": world.report(result),
    })
