"""A Metropolis walk over fabrics: the Monte-Carlo half of the search (docs/decisions.md D313).

WHY THIS EXISTS. `perturb` (D309) was named for this and did not do it. It called `mutate` once,
got the parent's ENTIRE neighbourhood back deterministically -- seven designs for a typical staged
fabric, so its `limit=12` never even bound -- screened all seven, and stopped. Its `variant_key`
then recorded the (parent, radius) pair as done, so asking again spent a step and did nothing. That
is exhaustive local enumeration: no sampling, no acceptance test, no walk, and above all no way for
the search to MOVE. Two attempts found no improvement, which is the expected yield of looking at
seven neighbours of one design once.

The fix is not a bigger neighbourhood. A seven-neighbour ball is ample if the chain walks, because
each accepted move lands on a new incumbent with a ball of its own; reach comes from the number of
steps, not the width of one. So this walks.

WHAT IT IS, precisely. Simulated annealing -- a Metropolis acceptance rule driving an OPTIMIZATION,
cooled on a schedule. It is not a sampler: the proposal is uniform over a neighbourhood whose size
varies from design to design and no Hastings correction is applied, so the chain does not target a
Boltzmann distribution and no claim here depends on it doing so. Calling it MCMC would be a
category error worth avoiding, since the two differ in exactly what you may conclude at the end.

WHAT IT COSTS. The screen is analytic and runs at ~30,000 designs/second, so a thousand-step chain
is a fraction of a second and the whole walk is free next to a single placement. That asymmetry is
the design: spend Monte-Carlo steps freely on the proxy, and spend real Yosys and OpenROAD only on
the handful of designs the walk actually surfaces.

WHAT IT OPTIMIZES, and the mistake worth not repeating. The first version of this minimized
`mux_bits` alone, subject to the structural-capacity constraint. It "improved" on the exhaustive
enumeration by 30% within seconds, and the design it found was junk: 57,344 mux_bits against the
enumerated optimum's 81,920, but 4.5 words/cycle of delivered throughput against its 13.9. The
constraint it satisfied was `max_served_per_cycle` -- the traffic-agnostic structural waist, which
asks only whether 28 clients COULD be carried. What the fabric actually delivers under the traffic
is `expected_served_per_cycle`, a screen objective in its own right, and nothing was defending it.
So the walk bought its area saving by throwing away three quarters of the throughput, which is not
a better design but a different point on a trade-off the search had been told to ignore.

That is the standard way a Monte-Carlo optimizer fails against a surrogate: it finds the hole in
the objective, fast, and reports a number. The demo screens in `mode: pareto` over three metrics,
so the walk does too -- weighted scalarization for the acceptance test, Pareto dominance for the
archive that decides what earns real tool time.

Even so, every metric here is a SCREEN metric. `mux_bits` is an area proxy (Pearson r = 0.95
against placed area over this repo's own 23-fabric store) and frequency does not exist at screen
time at all, so a walk cannot know whether what it found closes at 600 MHz. The archive is a list
of candidates for measurement, never a result.
"""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass, field
from typing import Any

from .perturb import RADII, mutate, structural_key

# The screen objectives, and their directions, as the demo declares them. `area_mm2` is absent
# because it does not exist until escalation -- which is the whole reason `mux_bits` stands in.
OBJECTIVES: tuple[tuple[str, str], ...] = (
    ("mux_bits", "min"),
    ("throughput_words_per_cycle", "max"),
    ("interstage_link_bits", "min"),
)

# Cooling runs from T0 down to T_END geometrically. Temperature is compared against a RELATIVE
# objective change, so these are dimensionless and do not need rescaling when the problem does.
T0_DEFAULT = 0.08     # at the start, a 8%-worse design is accepted about 1/e of the time
T_END_DEFAULT = 0.002  # by the end, essentially greedy
STALL_BEFORE_KICK = 12  # rejected proposals in a row before widening the radius to escape


def screen(spec: dict[str, Any]) -> dict[str, float]:
    """The screen metrics for one fabric, from the REAL screening evaluator.

    Deliberately not a local reimplementation of `mux_bits` and capacity. Five definitions of
    "the same fabric" had already drifted apart in this repo before D311 collapsed them into
    one, and an acceptance rule scoring designs by its own private copy of the objective would
    reintroduce exactly that failure one layer up: a walk that optimizes something the campaign
    does not screen for.
    """
    from flux_evaluator_abi import Budget, Candidate
    from flux_evaluator_interconnect_struct.adapter import InterconnectStructuralEvaluator

    result = InterconnectStructuralEvaluator().evaluate(
        Candidate(arch={"interconnect": spec}, workload={}, mapping={}),
        Budget(), frozenset({"mux_bits"}))
    return {k: v.value for k, v in result.metrics.items()}


def objective(metrics: dict[str, float], *, clients: int) -> tuple[float, ...] | None:
    """The objective VECTOR, or None where the fabric is infeasible rather than merely bad.

    Carrying every client at once is a constraint, not an objective: the demo refuses a fabric
    with a narrower waist rather than ranking it. Folding that into the cost as a penalty would
    let the walk trade concurrency away for area and return a design the study does not accept.

    Returned in minimize-form throughout, so every comparison downstream -- dominance, deltas,
    scalarization -- is a plain "smaller is better" and no caller has to remember which of the
    three runs the other way. Getting that backwards silently inverts an objective, and an
    inverted objective is not a bug that announces itself.
    """
    if metrics.get("max_throughput_words_per_cycle", 0.0) < clients:
        return None
    return tuple(-float(metrics[name]) if direction == "max" else float(metrics[name])
                 for name, direction in OBJECTIVES)


def dominates(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    """Pareto dominance in minimize-form: no worse on every objective, better on at least one."""
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def scalarize(proposal: tuple[float, ...], incumbent: tuple[float, ...],
              weights: tuple[float, ...]) -> float:
    """A weighted sum of RELATIVE changes, which is what makes one temperature scale work.

    The three objectives differ by orders of magnitude -- mux_bits runs to six figures, throughput
    to about 28 -- so an absolute weighted sum is dominated by whichever happens to be largest and
    the temperature means nothing. Each objective contributes its own fractional change instead.
    """
    total = 0.0
    for value, base, weight in zip(proposal, incumbent, weights):
        scale = abs(base) or 1.0
        total += weight * (value - base) / scale
    return total


def chain_weights(seed: int, n: int = len(OBJECTIVES)) -> tuple[float, ...]:
    """One chain's trade-off, drawn from its seed and summing to 1.

    A single fixed weighting finds a single region of the front, so an ensemble of chains that all
    share one is an expensive way to get one answer. Drawing per chain means a run of eight covers
    eight trade-offs, and the archive keeps whatever any of them found.
    """
    rng = random.Random(seed * 7919 + 13)  # decorrelated from the walk's own stream
    draw = [rng.random() for _ in range(n)]
    total = sum(draw) or 1.0
    return tuple(d / total for d in draw)


@dataclass(frozen=True)
class ChainStep:
    """One proposal. Recorded whether or not it was accepted -- a walk that logs only its accepted
    moves cannot report an acceptance rate, which is the single number that says whether the
    temperature was set anywhere near right."""

    index: int
    radius: str
    temperature: float
    cost: tuple[float, ...] | None   # None where the proposal was infeasible
    delta: float | None              # scalarized change against the incumbent
    accepted: bool


@dataclass
class ChainResult:
    start: dict[str, Any]
    weights: tuple[float, ...]
    steps: list[ChainStep] = field(default_factory=list)
    archive: list[dict[str, Any]] = field(default_factory=list)  # non-dominated, {spec,cost,key}
    kicks: int = 0
    start_cost: tuple[float, ...] = ()

    @property
    def accepted(self) -> int:
        return sum(1 for s in self.steps if s.accepted)

    @property
    def acceptance_rate(self) -> float:
        return self.accepted / len(self.steps) if self.steps else 0.0

    @property
    def infeasible(self) -> int:
        return sum(1 for s in self.steps if s.cost is None)

    def gains(self) -> dict[str, float]:
        """Best fractional gain per objective against the start, over the whole archive.

        Reported per objective rather than as one headline number, because on a Pareto search a
        single "N% better" is nearly always the reader being shown the objective that moved and
        not the one that paid for it. That is precisely how this module's first version reported
        a 30% area win that had quietly given up three quarters of its throughput.
        """
        out = {}
        for i, (name, direction) in enumerate(OBJECTIVES):
            base = self.start_cost[i] if self.start_cost else 0.0
            if not base:
                continue
            best = min(row["cost"][i] for row in self.archive)
            gain = (base - best) / abs(base)
            out[name] = gain if direction == "min" else gain
        return out

    def offer(self, spec: dict[str, Any], cost: tuple[float, ...]) -> None:
        """Add to the Pareto archive if nothing already there dominates it, dropping whatever it
        dominates in turn."""
        key = structural_key(spec)
        if any(row["key"] == key for row in self.archive):
            return
        if any(dominates(row["cost"], cost) for row in self.archive):
            return
        self.archive = [row for row in self.archive if not dominates(cost, row["cost"])]
        self.archive.append({"spec": dict(spec), "cost": cost, "key": key})

    def top(self, k: int, *, objective_index: int | None = None) -> list[dict[str, Any]]:
        """Up to k designs from the archive to spend real tool time on.

        Spread across the front by default -- the archive sorted by each objective in turn, taking
        one at a time -- because placing the k cheapest by one metric measures one corner of a
        trade-off the search exists to explore. Pass `objective_index` to take the best k by a
        single objective where that is genuinely what is wanted.
        """
        if objective_index is not None:
            ranked = sorted(self.archive, key=lambda r: r["cost"][objective_index])
            return [r["spec"] for r in ranked[:k]]
        picked, seen = [], set()
        for i in range(len(OBJECTIVES) * k):  # round-robin over the objectives
            axis = i % len(OBJECTIVES)
            for row in sorted(self.archive, key=lambda r: r["cost"][axis]):
                if row["key"] not in seen:
                    seen.add(row["key"])
                    picked.append(row["spec"])
                    break
            if len(picked) >= k:
                break
        return picked[:k]


def walk(start: dict[str, Any], *, clients: int, steps: int = 400, seed: int = 0,
         t0: float = T0_DEFAULT, t_end: float = T_END_DEFAULT,
         radius: str = "adaptive",
         weights: tuple[float, ...] | None = None) -> ChainResult:
    """Anneal from `start` for `steps` proposals; return the trail and the Pareto archive.

    `radius="adaptive"` proposes small moves and widens to large ones only once the chain stalls.
    That is the honest use of the two radii this repo has: a small move changes one decision, so a
    run of rejections means the incumbent sits in a basin one decision cannot leave, and only a
    shape change gets out. Pinning the radius runs the corresponding fixed-radius chain, which is
    what a comparison between them needs.
    """
    if radius not in (*RADII, "adaptive"):
        raise ValueError(f"radius must be one of {(*RADII, 'adaptive')}, got {radius!r}")
    if steps < 1:
        raise ValueError(f"steps must be at least 1, got {steps}")

    rng = random.Random(seed)
    chosen = weights if weights is not None else chain_weights(seed)
    current = dict(start)
    current_cost = objective(screen(current), clients=clients)
    if current_cost is None:
        raise ValueError("the starting fabric is infeasible: it cannot carry every client at "
                         "once, so there is no feasible region to walk in from here")

    result = ChainResult(start=dict(start), weights=chosen, start_cost=current_cost)
    result.offer(current, current_cost)
    stalled = 0
    # Geometric cooling, computed per step from the ratio rather than multiplied in place, so a
    # chain is reproducible from (seed, steps) alone and not from its own rounding history.
    ratio = (t_end / t0) ** (1.0 / max(1, steps - 1))

    for i in range(steps):
        temperature = t0 * ratio ** i
        step_radius = radius
        if radius == "adaptive":
            step_radius = "large" if stalled >= STALL_BEFORE_KICK else "small"
            if stalled == STALL_BEFORE_KICK:
                result.kicks += 1

        neighbours = mutate(current, radius=step_radius, limit=64)
        if not neighbours:
            # A design with no buildable neighbours at this radius is a dead end, not an error.
            stalled += 1
            result.steps.append(ChainStep(i, step_radius, temperature, None, None, False))
            continue

        proposal = neighbours[rng.randrange(len(neighbours))]
        cost = objective(screen(proposal), clients=clients)
        if cost is None:
            stalled += 1
            result.steps.append(ChainStep(i, step_radius, temperature, None, None, False))
            continue

        delta = scalarize(cost, current_cost, chosen)
        accept = delta <= 0 or rng.random() < math.exp(-delta / max(temperature, 1e-9))
        result.steps.append(ChainStep(i, step_radius, temperature, cost, delta, accept))
        # Offered on merit, not on acceptance: a proposal the chain rejected because it was worse
        # on THIS chain's weighting can still be non-dominated, and dropping it would make the
        # archive a record of one trade-off rather than of everything the walk actually saw.
        result.offer(proposal, cost)

        if accept:
            current, current_cost, stalled = proposal, cost, 0
        else:
            stalled += 1

    return result


def sample_start(population: list[dict[str, Any]], *, seed: int = 0,
                 pressure: float = 1.0) -> dict[str, Any]:
    """Choose a walk's starting design, biased toward good ones but never only the best.

    Ranked by how many designs in the population DOMINATE each one, which is the multi-objective
    reading of "good": rank 0 is the current front, and a design is only pushed down it by
    designs beating it on every objective at once. Ranking by a single objective instead would
    start most chains at whichever extreme of the trade-off that metric prefers, and the search
    would explore one corner of the front repeatedly.

    Weight is 1/(rank+1)**pressure, so pressure=0 restarts uniformly and larger values
    concentrate on the front. `population` rows are {"spec", "cost"} with cost in minimize-form;
    the caller decides what the population is, because "everything measured" and "the current
    front" are different searches.
    """
    if not population:
        raise ValueError("no population to sample a starting design from")
    ranked = sorted(
        population,
        key=lambda row: sum(1 for other in population if dominates(other["cost"], row["cost"])))
    weights = [1.0 / (i + 1) ** pressure for i in range(len(ranked))]
    return dict(random.Random(seed).choices(ranked, weights=weights, k=1)[0]["spec"])


def crossover(a: dict[str, Any], b: dict[str, Any], *, rng: random.Random) -> list[dict[str, Any]]:
    """Recombine two staged fabrics by splicing one's leading ranks onto the other's trailing ones.

    WHY A WALK IS NOT ENOUGH. Every move `mutate` offers is a change to ONE design, so a chain can
    only reach what is connected to its start by single steps -- and it never changes `kind` at
    all, which is why an ensemble seeded on staged fabrics had never once looked at a butterfly.
    Crossover is the move that a neighbourhood cannot express: it takes a good front half from one
    design and a good back half from another, which is a jump no sequence of small edits was going
    to make and the reason genetic and memetic searches carry it.

    Defined only where it MEANS something. A staged fabric is a list of ranks, so a cut point is a
    real structural boundary; a butterfly is a radix and a mesh is a grid, and "splicing" those is
    just picking one parent's number, which mutation already does. Returns [] rather than
    pretending otherwise.

    `in` is re-derived across the splice, never carried over. A spliced fabric whose ranks do not
    chain is exactly the failure the staged form makes easy, and it is caught by building here
    rather than surfacing as an error later.
    """
    from .topology import derive_stage_inputs

    staged = ("xbar_staged", "xbar_multistage")
    if a.get("kind") not in staged or b.get("kind") not in staged:
        return []
    left, right = a.get("stages") or [], b.get("stages") or []
    if len(left) < 1 or len(right) < 1:
        return []

    clients = int(a["clients"])
    out, seen = [], {structural_key(a), structural_key(b)}
    for cut_a in range(1, len(left) + 1):
        for cut_b in range(0, len(right)):
            spliced = [*copy.deepcopy(left[:cut_a]), *copy.deepcopy(right[cut_b:])]
            if not 1 <= len(spliced) <= 4:  # a fabric of many ranks is latency, not a bargain
                continue
            child = {**a, "stages": derive_stage_inputs(clients, spliced)}
            try:
                key = structural_key(child)
            except Exception:  # noqa: BLE001 — a child that will not build is not a child
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(child)
    rng.shuffle(out)
    return out


def ensemble(population: list[dict[str, Any]], *, clients: int, chains: int = 8,
             steps: int = 400, seed: int = 0, recombine: int = 60,
             pressure: float = 1.0) -> ChainResult:
    """Many chains plus recombination, merged into one Pareto archive.

    Three things happen here that a single `walk` cannot do, and each is a limitation that was
    measured rather than assumed:

      - STARTS SPREAD ACROSS THE POPULATION, so chains begin in different families. `mutate` never
        changes `kind`, so a chain's family is fixed at its start and an ensemble seeded from one
        family explores exactly one family however long it runs.
      - EVERY CHAIN GETS ITS OWN TRADE-OFF, drawn from its seed. Eight chains sharing one weighting
        is an expensive way to find one region of the front.
      - THE ARCHIVE IS RECOMBINED once the chains are done, and the children are screened and
        offered like any other design. This is the step that reaches across chains: two fabrics
        that were good for different reasons can only be combined by something that can see both.

    Returned as a `ChainResult` so an ensemble and a single walk report identically -- an archive,
    a step count, an acceptance rate -- and a caller can swap one for the other.
    """
    if chains < 1:
        raise ValueError(f"chains must be at least 1, got {chains}")

    merged = ChainResult(start={}, weights=(), start_cost=())
    rng = random.Random(seed)
    for i in range(chains):
        chain_seed = seed * 1000 + i
        try:
            result = walk(sample_start(population, seed=chain_seed, pressure=pressure),
                          clients=clients, steps=steps, seed=chain_seed)
        except ValueError:
            continue  # an infeasible start is one wasted draw, not a failed ensemble
        merged.steps.extend(result.steps)
        merged.kicks += result.kicks
        if not merged.start_cost:
            merged.start, merged.start_cost = result.start, result.start_cost
        for row in result.archive:
            merged.offer(row["spec"], row["cost"])

    # Recombination, after the chains: the archive it draws from is the merged one, so a child can
    # inherit from two chains that never met.
    for _ in range(recombine):
        if len(merged.archive) < 2:
            break
        a, b = rng.sample(merged.archive, 2)
        for child in crossover(a["spec"], b["spec"], rng=rng)[:4]:
            cost = objective(screen(child), clients=clients)
            if cost is not None:
                merged.offer(child, cost)
    return merged
