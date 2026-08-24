"""The interconnect IP catalog as a CHIA node (docs/decisions.md D267).

Knowledge the orchestrator can reference, with one rule that separates it from a reading list:
an entry is listed only if Flux can BUILD it. `instantiate` turns an entry plus its parameters
into a concrete topology and the Architecture IR block that a campaign consumes, so choosing
from the catalog and measuring the choice are the same motion.
"""

from __future__ import annotations

from typing import Any


def flux_ip_catalog(
    interface: str | None = None,
    status: str | None = None,
    contains: str | None = None,
    instantiate: str | None = None,
    clients: int = 0,
    banks: int = 0,
    width_bits: int = 0,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """List referenceable interconnect IP, or instantiate one.

    Listing returns each entry's parameters, the interfaces it fits, how its cost grows, when
    to use it, its known limits, published references, and — deliberately kept apart from those
    references — what this repo has measured. Filter by `interface` (`obi`, `axis`), by
    `status` (`constructible` / `evaluable_only` / `not_implemented`), or by a substring.

    With `instantiate`, pass an IP id plus `clients`/`banks`/`width_bits` and any entry
    parameters in `params` (e.g. `{"n": 4, "m": 4}` for `clos`). The reply carries the built
    topology — blocks, stages, peak concurrency, modelled throughput, inter-stage link bits —
    and the `interconnect` Architecture IR block that hands straight to a campaign.
    """
    from flux_interconnect.catalog import INTERFACES, instantiate as build_ip, list_ips

    if instantiate:
        topo = build_ip(instantiate, clients, banks, width_bits, **(params or {}))
        return {
            "ip": instantiate,
            "topology": topo.to_dict(),
            "modelled_words_per_cycle": topo.expected_served_per_cycle(),
            "interstage_link_bits": topo.interstage_link_bits(),
            "architecture_ir": {"interconnect": {
                "kind": topo.kind, "clients": clients, "banks": banks,
                "width_bits": width_bits, **(params or {})}},
        }
    return {
        "ip": list_ips(interface=interface, status=status, contains=contains),
        "interfaces": list(INTERFACES.values()),
    }
