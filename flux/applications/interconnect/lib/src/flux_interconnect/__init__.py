"""Interconnect fabric models, deterministic RTL, and the IP catalog (docs/decisions.md D261).

Library, not an evaluator: the topology math and RTL emitters live here so both the
structural screen (`evaluators/interconnect_struct`) and the physical rung
(`evaluators/interconnect_phys`) build on one definition — the repo's one-directory-one-
backend registry convention would otherwise force this code to be duplicated or misplaced.

Three layers, deliberately separate:

* `topology.py` — the families, the enumerated space, and the ANALYTIC screen model. Cheap
  enough to rank a thousand candidates; its error against RTL is characterised in D266.
* `fabric.py` — the whole fabric as SystemVerilog, with routing tables computed from the real
  wiring (so an unroutable topology is refused, not simulated) and throughput MEASURED under
  Verilator. This is where a claim stops being a model.
* `catalog.py` — the referenceable IP list behind `flux_ip_catalog`, on the rule that an entry
  is listed only if a generator here can build it.
"""

from .catalog import instantiate as instantiate_ip
from .catalog import list_ips, render_for_prompt
from .study import (
    InterconnectRequest,
    InterconnectResult,
    PlacedFabric,
)
from .fabric import (
    UnroutableFabricError,
    canonical_stages,
    fabric_rtl,
    measure_throughput,
    measure_whole_fabric,
    path_diversity,
    routing_tables,
    traffic_testbench,
)
from .topology import (
    Topology,
    arbitrated_selector_rtl,
    build,
    butterfly,
    clos_network,
    enumerate_space,
    enumerate_staged,
    full_crossbar,
    hierarchical_crossbar,
    multistage_crossbar,
    staged_crossbar,
)

__all__ = [
    "Topology",
    "UnroutableFabricError",
    "arbitrated_selector_rtl",
    "build",
    "butterfly",
    "canonical_stages",
    "clos_network",
    "enumerate_space",
    "enumerate_staged",
    "fabric_rtl",
    "full_crossbar",
    "hierarchical_crossbar",
    "instantiate_ip",
    "list_ips",
    "measure_throughput",
    "InterconnectRequest",
    "InterconnectResult",
    "PlacedFabric",
    "measure_whole_fabric",
    "multistage_crossbar",
    "path_diversity",
    "render_for_prompt",
    "routing_tables",
    "staged_crossbar",
    "traffic_testbench",
]
