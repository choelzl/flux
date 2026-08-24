"""`flux_bankmap_dse_loop` -- the bank-mapping study as a dispatchable CHIA node (D356).

Same shape as the other two studies' nodes: `run_study` unchanged, its result mapped to JSON so
a parent orchestrator can read the answer -- and the Verilog for it -- without importing
`flux_bankmap`. Called in-process: this call is already the unit of dispatch.
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
    from flux_bankmap.flow import run_study
    from flux_bankmap.problem import MappingRequest, Stage
    from flux_bankmap.topology import parse as parse_topology

    from flux_bankmap.topology import Topology

    caps = tuple(stage_capacities) if stage_capacities else None
    if topology or crossbar or not stages:
        spec = topology or (f"staged:{crossbar}" if crossbar else "crossbar")
        topo = parse_topology(spec, int(banks), capacities=caps, lanes=lanes)
    else:
        # Explicit stages and nothing else: the default crossbar's "adds no conflict point"
        # note printed beside a stage that IS one, which is the kind of sentence a reader
        # rightly stops trusting a report over.
        topo = Topology(name="explicit stages")
    stage_list: list[Stage] = list(topo.stages)
    for st in stages or []:
        stage_list.append(Stage(bits=tuple(int(b) for b in st["bits"]),
                                capacity=int(st.get("capacity", 1)), name=st.get("name", ""),
                                lanes=int(st["lanes"]) if st.get("lanes") else None,
                                lane_key=st.get("lane_key", "chunk"),
                                blocks=int(st["blocks"]) if st.get("blocks") else None))
    request = MappingRequest(strides=tuple(int(s) for s in strides), concurrent=int(concurrent),
                             banks=int(banks), address_bits=int(address_bits), db=db_path,
                             problem=problem, llm_round=llm_round, z3_seconds=z3_seconds,
                             max_xor_inputs=max_xor_inputs, stages=tuple(stage_list),
                             topology=topo.name, notes=topo.notes)
    propose = None
    if llm_round > 0:
        try:
            from flux_bankmap.propose import llm_proposer
            propose = llm_proposer()
        except Exception:                                                 # noqa: BLE001
            propose = None
    result = run_study(request, propose=propose, feedback=feedback)
    n = request.concurrent
    return {
        "request": {"strides": list(request.strides), "concurrent": n, "banks": request.banks,
                    "address_bits": request.address_bits,
                    "stages": [st.describe() for st in request.stages],
                    "topology": request.topology, "notes": list(request.notes)},
        "decision": _mapping_payload(result.decision, request) if result.decision else None,
        "conflict_free": result.conflict_free,
        "hardware_cost": result.hardware_cost,
        "candidates": [
            {**_mapping_payload(m, request), "conflict_free": v.conflict_free,
             "verdict": v.summary(n), "proposed_by": who} for m, v, who in result.candidates],
        "refused": [{"mapping": d, "why": w} for d, w in result.refused],
        "progress": list(result.progress),
        "lessons": result.lessons,
        "not_established": result.not_established,
        "met_requirement": result.met_requirement,
        "provenance": result.provenance,
    }
