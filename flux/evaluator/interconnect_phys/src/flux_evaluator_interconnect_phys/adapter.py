"""Two interconnect evaluators (docs/decisions.md D261): a structural screen and a physical
rung that measures real ASAP7 silicon.

`InterconnectStructuralEvaluator` is instant and analytic: mux-bit count as an area proxy,
peak concurrency and expected served-per-cycle from the topology model. It ranks the space
cheaply and produces no silicon claim.

`InterconnectPhysicalEvaluator` measures. For every DISTINCT arbitrated selector a topology
uses, it runs real Yosys + OpenROAD at several narrow widths and fits:

    area(W)  = a + b*W                 (fixed arbiter/decode cost, then per-bit mux cost)
    delay(W) = c_K + POOLED*log2(W)     (per-arity intercept, ONE shared width slope)

then evaluates both at the real datapath width. Why fit rather than build the full-width
block: a 28-client 128-bit selector has 3718 ports, so a standalone place-and-route is
PIN-limited — the die inflates to fit pins and wire delay swamps the logic, measured as
-29.7 ns of slack for a block whose logic is ~0.5 ns. Narrow slices place normally, and the
fit separates the arbiter's fixed cost from the datapath's linear one. Stated plainly: the
128-bit numbers are an extrapolation over measured widths, identical in method for every
topology, so the COMPARISON is sound even where an absolute carries fit error.

Throughput is MEASURED here too, and separately (docs/decisions.md D266): the whole fabric is
generated as SystemVerilog, driven with uniform-random traffic under Verilator, and the
accepted transfers counted. Measured against the screen's analytic model across the families,
the model lands within ~10% wherever each destination has ONE path, and misses by 21% for a
Clos at m = n, whose whole point is that it has several — the model assumes each switch picks
uniformly at random, while the RTL rotates among equivalent paths and so load-balances better
than chance. A rung that measured area and frequency while inheriting the modelled throughput
would rank the space by its one unmeasured number, and would rank path-diverse fabrics worst
exactly where they are strongest. Costs ~5-8 s per fabric against minutes for place-and-route.

Metrics: `area_mm2` (sum over blocks), `fmax_mhz` (the slowest block sets the clock),
`throughput_words_per_cycle` (Verilator, falling back to the model), `latency_cycles`.
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

from flux_interconnect import arbitrated_selector_rtl, build
from flux_interconnect.fabric import (
    FabricIncorrectError,
    UnroutableFabricError,
    measure_throughput,
)

# The SAME widths for every arity (docs/decisions.md D265). A K:1 selector of W bits has
# ~(K+1)*W + K ports, so wide fits are PIN-limited (a 32:1 x32b block wants 1122 pins against
# the 1048 the default single pin layer pair offers, PPL-0024). Earlier this evaluator dropped
# the widths that would not place, per arity — which made two arities' extrapolations
# incomparable and reported a 32:1 selector as FASTER at 128 bits than a 28:1 one, impossible,
# and visible only because the search put both on one table. Two horizontal and two vertical
# pin layers carry the wide case instead, so every arity is fitted identically.
_CANDIDATE_FIT_WIDTHS = (8, 16, 32)
_FIT_PIN_LAYERS = (("M4", "M6"), ("M5", "M7"))

# Delay is nearly FLAT in width, and pooling that across arities is the second half of the
# same lesson. A K:1 mux's critical path is its select tree, depth log2(K); widening the
# datapath replicates that tree in parallel and adds only select-fanout buffering. Measured
# (ASAP7, 8/16/32 bits): a 8:1 selector runs 902 / 898 / 872 ps, a 16:1 runs 1212 / 1139 /
# 1441. Per-arity least squares over three such points fits mostly noise — the slopes came
# out anywhere from -15 to +436 ps/octave — and extrapolating that two octaves out to 128
# bits swamped the real arity ordering. So the width term is fitted ONCE across all measured
# arities and only the intercept is per-arity, which restores monotonicity in K.
_WIDTH_DELAY_SLOPE_PS = 145.0
# The measurement METHOD's version, and it belongs in the evaluator's identity because a
# campaign store outlives the code that filled it. A store carried results from before the
# shared-width fit existed, and the demo's final table — which aggregates every campaign in the
# store — reported a radix-32 butterfly at 1012 MHz and 0.0250 mm2 alongside current numbers.
# Both were wrong by the current method (that fabric is a direct crossbar; it measures 406 MHz)
# and nothing on the row said so. Bump this whenever a change moves measured values: the fit
# widths or slope, what blocks a topology is charged for, or where throughput comes from.
_METHOD_VERSION = "m2"  # m1: per-arity fit widths, request-path-only butterfly, modelled
                        # throughput. m2: shared width set + pooled slope, response path
                        # charged everywhere, throughput measured under Verilator.
_SIM_CYCLES = 20000  # reproduces the crossbar's analytic value to four digits
_TARGET_PERIOD_PS = 1667.0  # 600 MHz, the frequency this fabric family is being asked for


EVALUATOR_ID = f"interconnect_phys@asap7/{_METHOD_VERSION}"


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


class InterconnectPhysicalEvaluator:
    """Real Yosys + OpenROAD per distinct selector arity, fitted to the datapath width, and
    real Verilator on the whole fabric for throughput. Where the simulator is unavailable the
    analytic model stands in — and says so in the result's provenance rather than passing
    itself off as a measurement."""

    name = "interconnect_phys"

    def __init__(self, *, target_period_ps: float = _TARGET_PERIOD_PS) -> None:
        self._target_period_ps = target_period_ps
        self._fits: dict[int, tuple[tuple[float, float], tuple[float, float]]] = {}
        self._throughput: dict[tuple, tuple[float, str]] = {}
        self._lock = threading.Lock()
        # SINGLE-FLIGHT, one lock per key. The caches above make a repeated arity free once it is
        # known, but only AFTER the first measurement returns; the check-then-measure gap is wide
        # here — three OpenROAD placements per arity — so several fabrics escalated concurrently
        # would each start the same fit and each pay for it. These locks make the second arrival
        # wait for the first's answer instead of duplicating it. Correctness never depended on
        # this (every flow runs in its own temp directory); the cost did.
        self._inflight: dict[object, threading.Lock] = {}

    def _flight_lock(self, key: object) -> threading.Lock:
        with self._lock:
            return self._inflight.setdefault(key, threading.Lock())

    def _measure_throughput(self, topo) -> tuple[float, str]:
        """(words per cycle, how it was obtained). Verilator on the generated fabric where it
        can be built; the analytic model where it cannot, said plainly rather than silently."""
        key = (topo.kind, tuple(sorted(topo.blocks.items())), topo.stages, topo.clients,
               topo.banks, topo.width_bits)
        with self._lock:
            if key in self._throughput:
                return self._throughput[key]
        with self._flight_lock(("throughput", key)):
            with self._lock:
                if key in self._throughput:
                    return self._throughput[key]
            return self._measure_throughput_uncached(topo, key)

    def _measure_throughput_uncached(self, topo, key: tuple) -> tuple[float, str]:
        modelled = topo.expected_served_per_cycle()
        try:
            run = measure_throughput(topo, cycles=_SIM_CYCLES)
            checks = run.get("correctness", {})
            got = (run["measured_words_per_cycle"],
                   f"verilator, {_SIM_CYCLES} cycles uniform-random "
                   f"(model said {modelled:.2f}, ratio {run['ratio']:.2f}; "
                   f"{checks.get('route_errors', '?')} misrouted, "
                   f"{checks.get('data_errors', '?')} corrupted, per-client delivery ratio "
                   f"{checks.get('starvation_ratio', 0):.2f})")
        except (FabricIncorrectError, UnroutableFabricError):
            # NOT caught into a model fallback: a fabric that delivers words to the wrong bank
            # is not a slow candidate, it is a broken one, and substituting a modelled number
            # would keep it in the running with a clean-looking score (docs/decisions.md D268).
            #
            # `UnroutableFabricError` belongs here for the same reason and did not used to
            # (D325). It fell through to the generic handler below, was read as "the simulator
            # is missing", and was answered with `expected_served_per_cycle()` — a Patel-model
            # number computed from stage loads, which needs no routing and so cheerfully returns
            # 8.4 words/cycle for a fabric where client 4 reaches no bank above 7. That number
            # then carried the fabric to the top of the frontier as the smallest design meeting
            # timing, and the only thing that ever contradicted it was the decision rung failing
            # to place it, minutes and one screen later. A fabric that cannot reach every bank
            # is broken MORE completely than one that misroutes, not less.
            raise
        except Exception as exc:  # simulator absent or fabric not buildable
            got = (modelled, f"analytic model — RTL measurement unavailable: {exc!s:.120}")
        with self._lock:
            self._throughput[key] = got
        return got

    def _fit_block(self, inputs: int) -> tuple[tuple[float, float], tuple[float, float]]:
        """((area_a, area_b), (delay_c, delay_d)) for one selector arity, measured once."""
        with self._lock:
            if inputs in self._fits:
                return self._fits[inputs]
        with self._flight_lock(("fit", inputs)):
            with self._lock:  # another thread may have finished it while this one waited
                if inputs in self._fits:
                    return self._fits[inputs]
            return self._fit_block_uncached(inputs)

    def _fit_block_uncached(self, inputs: int) -> tuple[tuple[float, float], tuple[float, float]]:
        from flux_evaluator_openroad.flow import run_ppa_flow

        areas: list[tuple[float, float]] = []
        delays: list[tuple[float, float]] = []
        for width in _CANDIDATE_FIT_WIDTHS:
            name = f"Sel{inputs}w{width}"
            report = run_ppa_flow(
                arbitrated_selector_rtl(inputs, width, name), name,
                clock_port="clk", clock_period_ps=self._target_period_ps,
                repair_design=True, timeout_s=900, pin_layers=_FIT_PIN_LAYERS,
                # Full technology mapping and a timing-repair pass, matching what the
                # whole-fabric measurement does (docs/decisions.md D278). The screen and the
                # rung it feeds have to be synthesised the same way or the screen is ranking
                # candidates by a flow nobody would ship.
                full_mapping=True, reset_port="rst_n",
            )
            areas.append((float(width), report.area_um2))
            delays.append((math.log2(width), self._target_period_ps - report.worst_slack_ps))
        # area: both terms fitted. delay: intercept only, on the pooled width slope.
        intercept = sum(ps - _WIDTH_DELAY_SLOPE_PS * lw for lw, ps in delays) / len(delays)
        fit = (_linear_fit(areas), (intercept, _WIDTH_DELAY_SLOPE_PS))
        with self._lock:
            self._fits[inputs] = fit
        return fit

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        topo = build(_spec_of(candidate))
        width = float(topo.width_bits)
        total_um2 = 0.0
        worst_delay_ps = 0.0
        per_block: dict[str, str] = {}
        for (inputs, block_width), count in sorted(topo.blocks.items()):
            (a, b), (c, d) = self._fit_block(inputs)
            area_um2 = a + b * block_width
            delay_ps = c + d * math.log2(block_width)
            total_um2 += area_um2 * count
            worst_delay_ps = max(worst_delay_ps, delay_ps)
            per_block[f"selector_{inputs}x{block_width}b"] = (
                f"{count} x {area_um2:.1f} um2, {delay_ps:.0f} ps"
            )
        fmax_mhz = 1e6 / worst_delay_ps if worst_delay_ps > 0 else 0.0
        area_mm2 = total_um2 * 1e-6
        served, served_how = self._measure_throughput(topo)

        out = {
            "area_mm2": Estimate(value=area_mm2, ci_low=area_mm2, ci_high=area_mm2,
                                 unit="mm2", method=Method.SIMULATED),
            "fmax_mhz": Estimate(value=fmax_mhz, ci_low=fmax_mhz, ci_high=fmax_mhz,
                                 unit="MHz", method=Method.SIMULATED),
            "throughput_words_per_cycle": Estimate(
                value=served, ci_low=served, ci_high=served, unit="words/cycle",
                method=Method.SIMULATED),
            # Two cycles per stage, not one: the generated switch is pipelined into a decode
            # stage and an arbitrate-and-mux stage (docs/decisions.md D273). Reporting the
            # stage count as the latency would have understated every fabric by half the
            # moment that pipeline register was added.
            # The capacity the measured throughput is read against (D282): transfers the
            # fabric can carry at once, a count fixed by its narrowest point. Structural, so
            # unlike the rate beside it this does not move when the traffic model does.
            "max_throughput_words_per_cycle": Estimate(
                value=float(topo.max_served_per_cycle()),
                ci_low=float(topo.max_served_per_cycle()),
                ci_high=float(topo.max_served_per_cycle()),
                unit="words/cycle", method=Method.ANALYTIC),
            "latency_cycles": Estimate(value=float(topo.stages * 2),
                                       ci_low=float(topo.stages * 2),
                                       ci_high=float(topo.stages * 2), unit="cycles",
                                       method=Method.ANALYTIC),
            # The wiring the composed cell area does NOT include (D265) — reported as its own
            # cost so a fabric of many tiny switches cannot look free.
            "interstage_link_bits": Estimate(
                value=float(topo.interstage_link_bits()),
                ci_low=float(topo.interstage_link_bits()),
                ci_high=float(topo.interstage_link_bits()), unit="bits",
                method=Method.ANALYTIC),
        }
        return _result(
            out, EVALUATOR_ID, limiter=Limiter.NOC,
            inputs={"topology": topo.kind, "throughput": served_how, "blocks": "; ".join(
                f"{k}: {v}" for k, v in sorted(per_block.items())),
                "width_fit": "; ".join(
                    f"selector {k}:1 fitted at widths {_CANDIDATE_FIT_WIDTHS}"
                    for k in sorted({b[0] for b in topo.blocks})
                ) + f", evaluated at {width:.0f}b"},
        )

    def evaluate_batch(self, candidates, budget, metrics):
        """The ABI's batch surface: a plain loop — these evaluations share no setup that a
        batch could amortize (the physical rung's sharing is its per-arity fit cache)."""
        return [self.evaluate(c, budget, metrics) for c in candidates]


def _linear_fit(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Least-squares (intercept, slope) — the fixed cost and the per-unit cost."""
    n = len(points)
    sx = sum(x for x, _ in points)
    sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points)
    sxy = sum(x * y for x, y in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        return (sy / n, 0.0)
    slope = (n * sxy - sx * sy) / denom
    return ((sy - slope * sx) / n, slope)
