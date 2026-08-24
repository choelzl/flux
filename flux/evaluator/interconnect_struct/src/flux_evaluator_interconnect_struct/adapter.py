"""Two interconnect evaluators (docs/decisions.md D261): a structural screen and a physical
rung that measures real ASAP7 silicon.

`InterconnectStructuralEvaluator` is instant and analytic: mux-bit count as an area proxy,
peak concurrency and expected served-per-cycle from the topology model. It ranks the space
cheaply and produces no silicon claim.

`InterconnectPhysicalEvaluator` measures. For every DISTINCT arbitrated selector a topology
uses, it runs real Yosys + OpenROAD at several narrow widths and fits:

    area(W)  = a + b*W        (fixed arbiter/decode cost, then per-bit mux cost)
    delay(W) = c + d*log2(W)  (select fanout, which buffering makes logarithmic)

then evaluates both at the real datapath width. Why fit rather than build the full-width
block: a 28-client 128-bit selector has 3718 ports, so a standalone place-and-route is
PIN-limited — the die inflates to fit pins and wire delay swamps the logic, measured as
-29.7 ns of slack for a block whose logic is ~0.5 ns. Narrow slices place normally, and the
fit separates the arbiter's fixed cost from the datapath's linear one. Stated plainly: the
128-bit numbers are an extrapolation over measured widths, identical in method for every
topology, so the COMPARISON is sound even where an absolute carries fit error.

Metrics: `area_mm2` (sum over blocks), `fmax_mhz` (the slowest block sets the clock),
`throughput_words_per_cycle` (the topology model), `latency_cycles` (pipeline stages).
"""

from __future__ import annotations

import math
import threading
from typing import Any

from flux_evaluator_abi import (
    Bottleneck,
    Budget,
    Candidate,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)

from flux_interconnect import build

_TARGET_PERIOD_PS = 1667.0  # 600 MHz, the frequency this fabric family is being asked for


class NotExpressibleError(ValueError):
    """`Candidate.arch` carries no `interconnect` block this evaluator can read.

    NAMED FOR THE ABI, not for this evaluator (docs/decisions.md D321). Every backend must refuse
    a candidate it cannot express by raising `NotExpressibleError`; `tests/integration/
    test_arch_none_conformance.py` checks the raised type by name across every registered backend,
    and these two were the only ones calling it something else. They therefore failed a contract
    every other evaluator honours, in a nightly job, for as long as they had been registered.

    `NotAnInterconnectError` remains as an alias because it is exported in this package's
    `__all__` and callers import it.
    """


NotAnInterconnectError = NotExpressibleError


def _spec_of(candidate: Candidate) -> dict[str, Any]:
    arch = candidate.arch
    if not isinstance(arch, dict) or "interconnect" not in arch:
        raise NotExpressibleError(
            "interconnect evaluators need an Architecture IR dict with an `interconnect` "
            "block ({kind, clients, banks, width_bits, ...})"
        )
    return arch["interconnect"]


def _result(metrics: dict[str, Estimate], evaluator: str, *, limiter: Limiter,
            inputs: dict[str, str], ok: bool = True, detail: str = "") -> Result:
    from flux_evaluator_abi import Constraint

    return Result(
        metrics=metrics,
        validity=Validity(
            ok=ok, checker_version="interconnect@0.1",
            violations=() if ok else (Constraint(kind="fmax_mhz", detail=detail),),
        ),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=limiter),
        provenance=Provenance(evaluator=evaluator, inputs=inputs),
        escalation=Escalation(recommended=False),
    )


def _unroutable_reason(topo) -> str | None:
    """Why this fabric cannot deliver, or None if it routes.

    The routing tables are the authority and building them costs about 180 us against the
    screen's 36 -- five times the screen, and still four orders of magnitude under one placement.
    """
    from flux_interconnect.fabric import UnroutableFabricError, routing_tables

    try:
        routing_tables(topo)
    except UnroutableFabricError as exc:
        return str(exc)[:160]
    except Exception:  # noqa: BLE001 — a fabric we cannot route for any other reason is not
        return "routing tables could not be built"  # this check's business to diagnose
    return None


class InterconnectStructuralEvaluator:
    """Instant analytic screen: no tool runs, no silicon claim."""

    name = "interconnect_struct"

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        topo = build(_spec_of(candidate))
        # ROUTABILITY, checked here and not only at the decision rung (docs/decisions.md D319).
        # `build` succeeding means the shape is constructible, NOT that every client can reach
        # every bank: a chain whose ranks do not cover the banks builds fine and routes nothing.
        # Such a fabric is cheap on `mux_bits` for exactly the reason it is broken -- it is
        # missing connections -- so a search minimizing area actively HUNTS them. They are 0.5%
        # of the enumerated space and were three of five finalists in a real run, including the
        # one reported as the smallest fabric meeting timing. It could not carry a single word.
        #
        # Reported as zero capacity rather than raised, because the study already refuses a
        # fabric whose waist is too narrow. A fabric that routes nothing has the narrowest waist
        # there is, and this way every existing caller -- the campaign's constraint, the
        # annealer's feasibility test, the frontier -- refuses it without knowing this check
        # exists.
        unroutable = _unroutable_reason(topo)
        # Area proxy: total mux bits (inputs x width) summed over blocks — monotone in the
        # real thing, in arbitrary units, and NOT reported as mm2.
        mux_bits = sum(k[0] * k[1] * n for k, n in topo.blocks.items())
        served = 0.0 if unroutable else topo.expected_served_per_cycle()
        capacity = 0.0 if unroutable else float(topo.max_served_per_cycle())
        out = {
            "mux_bits": Estimate(value=float(mux_bits), ci_low=float(mux_bits),
                                 ci_high=float(mux_bits), unit="bits",
                                 method=Method.ANALYTIC),
            "throughput_words_per_cycle": Estimate(
                value=served, ci_low=served, ci_high=served, unit="words/cycle",
                method=Method.ANALYTIC),
            "max_throughput_words_per_cycle": Estimate(
                value=capacity, ci_low=capacity, ci_high=capacity,
                unit="words/cycle", method=Method.ANALYTIC),
            "latency_cycles": Estimate(value=float(topo.stages), ci_low=float(topo.stages),
                                       ci_high=float(topo.stages), unit="cycles",
                                       method=Method.ANALYTIC),
            # The wiring the composed cell area does NOT include (D265) — reported as its own
            # cost so a fabric of many tiny switches cannot look free.
            "interstage_link_bits": Estimate(
                value=float(topo.interstage_link_bits()),
                ci_low=float(topo.interstage_link_bits()),
                ci_high=float(topo.interstage_link_bits()), unit="bits",
                method=Method.ANALYTIC),
        }
        # Every metric is returned regardless of the request: all three are free to
        # compute, and a caller that asked for one still benefits from seeing the others
        # (the ABI permits a superset; the cache's covering rule only needs >= requested).
        note = topo.note if not unroutable else f"UNROUTABLE: {unroutable}"
        return _result(out, "interconnect_struct@0.1", limiter=Limiter.NOC,
                       inputs={"topology": topo.kind, "note": note})

    def evaluate_batch(self, candidates, budget, metrics):
        """The ABI's batch surface: a plain loop — these evaluations share no setup that a
        batch could amortize (the physical rung's sharing is its per-arity fit cache)."""
        return [self.evaluate(c, budget, metrics) for c in candidates]

