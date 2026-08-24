"""The interconnect study as something another orchestrator can CALL (docs/decisions.md D345).

A larger design has an interconnect in it. The orchestrator responsible for that design should be
able to say "I need one, here is the requirement" and get an answer back — not run a script and
read its stdout. That needs two things this demo never had: a request that carries the requirement
without a command line, and a result that carries the answer without a terminal.

WHAT A RESULT IS, and it is the harder half. This study's answer is not a number; it is a fabric
that was placed on real silicon, the evidence behind it, and an honest account of what was NOT
established. A caller that receives only `area_mm2` has been told less than the study knows, and
the parts it is missing are exactly the ones this repo has spent its history learning to report:
that the screen runs optimistic (D316), that a fabric can pass every check and still not route
(D319), that conclusions drawn by a model are inferences (D314). So a result carries its
provenance and its refusals, and `met_requirement` is a separate question from "here is the best
thing found".

The request's numbers may be given directly or authored from prose by a model (D333). Either way
they are VALIDATED by building the space they describe before anything runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["InterconnectRequest", "InterconnectResult", "PlacedFabric"]


@dataclass(frozen=True)
class InterconnectRequest:
    """What a caller wants, and what it will spend to find out.

    The attribute names match the demo's own command-line namespace deliberately: the CLI and a
    programmatic caller are two front doors to one study, and a request that had to be translated
    into flags would be a second definition of the same thing.
    """

    db: str
    problem: str | None = None          # prose; authored and validated when given
    clients: int | None = None          # or the numbers directly, when the caller has them
    banks: int | None = None
    width_bits: int | None = None
    target_mhz: float | None = None
    bank_rows: int | None = None
    # What it may spend. `max_rounds` is a runaway guard rather than a plan: the orchestrator
    # stops when it judges there is nothing left (D291).
    max_rounds: int = 16
    rounds: int = 5
    budget: int | None = None
    llm_round: int = 12
    decide_on_finalists: int = 5

    def is_authored(self) -> bool:
        return bool(self.problem)


@dataclass(frozen=True)
class PlacedFabric:
    """One fabric, as measured on silicon rather than as estimated."""

    label: str
    area_mm2: float
    fmax_mhz: float
    served_per_cycle: float
    power_w: float
    latency_cycles: int
    dead_client_ports: int = 0
    dead_bank_ports: int = 0

    @property
    def words_per_cycle_per_mm2(self) -> float:
        return self.served_per_cycle / self.area_mm2 if self.area_mm2 else 0.0


@dataclass
class InterconnectResult:
    """What the study found, and what it did not establish.

    `met_requirement` is deliberately separate from `decision`. A study that searched hard and
    found nothing clearing the constraint has a best-effort answer and no decision, and those two
    facts must not be collapsed into one field that a caller reads as success.
    """

    request: InterconnectRequest
    decision: PlacedFabric | None = None
    finalists: list[PlacedFabric] = field(default_factory=list)
    #: Fabrics the study refused, and why — a caller planning a fallback needs the reason.
    refused: dict[str, str] = field(default_factory=dict)
    #: What the run actually did: steps taken, families covered, what it declined to explore.
    provenance: dict[str, Any] = field(default_factory=dict)
    #: Claims the mentor drew, already filtered by the mechanical guards and a second model.
    lessons: list[str] = field(default_factory=list)
    #: Stated limits — the screen's optimism, families not explored, anything measured once.
    not_established: list[str] = field(default_factory=list)

    @property
    def met_requirement(self) -> bool:
        """Whether a fabric was found AND placed on real silicon meeting the constraint."""
        return self.decision is not None

    def summary(self) -> str:
        """One line for a caller that wants to log what happened."""
        if self.decision is None:
            return ("no fabric cleared the requirement on placed silicon "
                    f"({len(self.finalists)} finalist(s) measured)")
        d = self.decision
        return (f"{d.label}: {d.area_mm2:.4f} mm2 at {d.fmax_mhz:.0f} MHz, "
                f"{d.served_per_cycle:.1f} words/cycle")
