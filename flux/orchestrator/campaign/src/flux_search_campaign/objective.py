"""`Objective` — the typed view over a validated Objective IR document (docs/decisions.md D216).

Parsed once at campaign start and at every resume; downstream code never re-reads the raw dict.
Validation beyond the JSON schema lives here — the schema says what shapes exist, this says what
they mean together (weighted mode needs weights everywhere, a metric constraint needs a bound, a
grid strategy must not carry an LLM model name as if it would be used).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import flux_ir


class InvalidObjectiveError(ValueError):
    """The objective document is schema-valid but semantically wrong."""


@dataclass(frozen=True, slots=True)
class ObjectiveMetric:
    metric: str
    direction: str  # "minimize" | "maximize"
    weight: float | None = None
    # Where the metric is measured (docs/decisions.md D226): "screen" = every screening trial
    # must carry it; "escalation" = the screening backend cannot produce it, and it joins the
    # frontier at escalation fidelity only.
    measured_at: str = "screen"

    @property
    def minimize(self) -> bool:
        return self.direction == "minimize"


@dataclass(frozen=True, slots=True)
class MetricConstraint:
    kind: str  # "metric_max" | "metric_min"
    metric: str
    bound: float
    # Where the metric exists (docs/decisions.md D261): a constraint on a quantity only a
    # deeper rung can produce — "the fabric must clock at 600 MHz", which needs real
    # placement — must not be demanded of screening trials, or every one of them is refused
    # for lacking it. Objectives could already say this; constraints could not.
    measured_at: str = "screen"


@dataclass(frozen=True, slots=True)
class StopCriteria:
    no_improvement_evaluations: int | None = None
    # Each entry: (metric, "max"|"min", bound). Stop when ONE frontier point meets ALL.
    target: tuple[tuple[str, str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class BudgetGrant:
    evaluations: int | None = None
    wall_clock_s: float | None = None
    usd: float | None = None


@dataclass(frozen=True, slots=True)
class Objective:
    doc: dict[str, Any]
    objective_hash: str
    id: str
    mode: str  # "pareto" | "weighted"
    metrics: tuple[ObjectiveMetric, ...]
    metric_constraints: tuple[MetricConstraint, ...]
    check_validity: bool
    workload: dict[str, Any]  # docref: {"ref": hash} or {"inline": doc}
    base_arch: dict[str, Any]
    screening_backend: str
    escalation_backends: tuple[str, ...]
    search: dict[str, Any]
    strategy_kind: str  # "grid" | "agentic"
    strategy_seed: int
    llm_model: str | None
    budget: BudgetGrant
    stop: StopCriteria

    def metric_names(self) -> frozenset[str]:
        return frozenset(m.metric for m in self.metrics)

    def screened_metric_names(self) -> frozenset[str]:
        return frozenset(m.metric for m in self.metrics if m.measured_at == "screen")

    def deferred_metric_names(self) -> frozenset[str]:
        """Metrics no screening trial carries — declared by objectives OR by constraints."""
        return frozenset(
            m.metric for m in self.metrics if m.measured_at == "escalation"
        ) | frozenset(
            c.metric for c in self.metric_constraints if c.measured_at == "escalation"
        )

    def screened_view(self) -> "Objective":
        """This objective restricted to its screen-measured metrics — what the screening-phase
        frontier, contenders and stop criteria legally operate on. Escalation-measured metrics
        cannot influence screening decisions because no screening trial carries them (D226)."""
        import dataclasses

        screened = tuple(m for m in self.metrics if m.measured_at == "screen")
        return dataclasses.replace(self, metrics=screened)

    def constraint_metric_names(self) -> frozenset[str]:
        return frozenset(c.metric for c in self.metric_constraints)

    def objective_for(self, metric: str) -> ObjectiveMetric:
        for m in self.metrics:
            if m.metric == metric:
                return m
        raise KeyError(f"objective {self.id!r} has no objective metric {metric!r}")


def parse_objective(doc: dict[str, Any]) -> Objective:
    """Validate (schema + semantics) and parse. The schema runs first so structural errors carry
    jsonschema's own messages; everything after assumes shape and checks meaning."""
    flux_ir.validate("objective", doc)

    metrics = tuple(
        ObjectiveMetric(
            metric=o["metric"], direction=o["direction"], weight=o.get("weight"),
            measured_at=o.get("measured_at", "screen"),
        )
        for o in doc["objectives"]
    )
    names = [m.metric for m in metrics]
    if len(set(names)) != len(names):
        raise InvalidObjectiveError(f"duplicate objective metric in {names}")

    mode = doc["mode"]
    if mode == "weighted":
        missing = [m.metric for m in metrics if m.weight is None]
        if missing:
            raise InvalidObjectiveError(
                f"mode=weighted requires a weight on every objective; missing on {missing}"
            )
    else:
        weighted = [m.metric for m in metrics if m.weight is not None]
        if weighted:
            raise InvalidObjectiveError(
                f"mode=pareto ignores weights, so carrying them ({weighted}) misleads the reader "
                "about what ran — drop them or use mode=weighted (docs/decisions.md D221)"
            )

    metric_constraints: list[MetricConstraint] = []
    check_validity = False
    for c in doc.get("constraints") or ():
        if c["kind"] == "validity":
            check_validity = True
            continue
        bound_key = "max" if c["kind"] == "metric_max" else "min"
        if "metric" not in c or bound_key not in c:
            raise InvalidObjectiveError(
                f"constraint {c!r} needs both a metric and a {bound_key!r} bound"
            )
        metric_constraints.append(
            MetricConstraint(kind=c["kind"], metric=c["metric"], bound=float(c[bound_key]),
                             measured_at=c.get("measured_at", "screen"))
        )

    search = doc["search"]
    kind = search["kind"]
    if kind == "architecture_width":
        widths = search.get("widths")
        if not widths or not all(isinstance(w, int) and w >= 1 for w in widths):
            raise InvalidObjectiveError(
                f"search.kind=architecture_width needs widths: [int >= 1], got {widths!r}"
            )
    elif kind == "memory_size":
        sizes = search.get("sizes_kb")
        if not sizes or not all(isinstance(s, (int, float)) and s > 0 for s in sizes):
            raise InvalidObjectiveError(
                f"search.kind=memory_size needs sizes_kb: [number > 0], got {sizes!r}"
            )
        if not search.get("level"):
            raise InvalidObjectiveError(
                "search.kind=memory_size needs level: <memory hierarchy level name>"
            )
    elif kind == "joint":
        widths = search.get("widths")
        sizes = search.get("sizes_kb")
        if not widths or not all(isinstance(w, int) and w >= 1 for w in widths):
            raise InvalidObjectiveError(f"search.kind=joint needs widths: [int >= 1], got {widths!r}")
        if not sizes or not all(isinstance(s, (int, float)) and s > 0 for s in sizes):
            raise InvalidObjectiveError(f"search.kind=joint needs sizes_kb: [number > 0], got {sizes!r}")
        if not search.get("level"):
            raise InvalidObjectiveError("search.kind=joint needs level: <memory hierarchy level name>")
    elif kind == "composition_width":
        widths = search.get("widths")
        per_op = search.get("widths_per_op")
        if widths is None and per_op is None:
            raise InvalidObjectiveError(
                "search.kind=composition_width needs widths: [int >= 1] and/or "
                "widths_per_op: {op_id: [int >= 1]} (docs/decisions.md D241)"
            )
        if widths is not None and (
            not widths or not all(isinstance(w, int) and w >= 1 for w in widths)
        ):
            raise InvalidObjectiveError(
                f"search.kind=composition_width widths must be [int >= 1], got {widths!r}"
            )
        if per_op is not None:
            ok = isinstance(per_op, dict) and per_op and all(
                isinstance(op, str) and isinstance(ws, list) and ws
                and all(isinstance(w, int) and w >= 1 for w in ws)
                for op, ws in per_op.items()
            )
            if not ok:
                raise InvalidObjectiveError(
                    f"search.kind=composition_width widths_per_op must map op ids to "
                    f"non-empty [int >= 1] lists, got {per_op!r}"
                )
    elif kind == "composition_system":
        widths = search.get("widths")
        sizes = search.get("sizes_kb")
        if not widths or not all(isinstance(w, int) and w >= 1 for w in widths):
            raise InvalidObjectiveError(
                f"search.kind=composition_system needs widths: [int >= 1], got {widths!r}"
            )
        if not sizes or not all(isinstance(s, (int, float)) and s > 0 for s in sizes):
            raise InvalidObjectiveError(
                f"search.kind=composition_system needs sizes_kb: [number > 0], got {sizes!r}"
            )
        if not search.get("level"):
            raise InvalidObjectiveError(
                "search.kind=composition_system needs level: <memory hierarchy level name>"
            )
        www = search.get("word_width_bits")
        if www is not None and (not isinstance(www, int) or www < 1):
            raise InvalidObjectiveError(
                f"search.word_width_bits must be an int >= 1 when given, got {www!r} "
                "(required for a cacti area rung, D252)"
            )
        scale_from = search.get("cacti_scale_from_nm")
        if scale_from is not None:
            from flux_evaluator_cacti.scaling import SUPPORTED_NODES_NM

            if scale_from not in SUPPORTED_NODES_NM or scale_from < 22:
                raise InvalidObjectiveError(
                    f"search.cacti_scale_from_nm={scale_from!r} must be a published scaling "
                    f"node CACTI can natively run ({[n for n in SUPPORTED_NODES_NM if n >= 22]}) "
                    "- docs/decisions.md D253"
                )
    elif kind == "interconnect_topology":
        variants = search.get("variants")
        if variants is None:
            # The problem statement form (D262): the space is enumerated from it.
            missing = [k for k in ("clients", "banks", "width_bits") if not search.get(k)]
            if missing:
                raise InvalidObjectiveError(
                    f"search.kind=interconnect_topology needs {missing} to enumerate the "
                    "topology space (or an explicit variants: [...] list)"
                )
        elif not isinstance(variants, list) or not variants or not all(
            isinstance(v, dict) and v.get("kind") for v in variants
        ):
            raise InvalidObjectiveError(
                "search.kind=interconnect_topology needs variants: [{kind: ..., ...}] — one "
                "interconnect spec per candidate (docs/decisions.md D261)"
            )
    elif kind == "noc_topology":
        variants = search.get("variants")
        ok = (
            isinstance(variants, list) and variants
            and all(
                isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], str)
                and isinstance(v[1], list) and v[1]
                and all(isinstance(d, int) and d >= 1 for d in v[1])
                for v in variants
            )
        )
        if not ok:
            raise InvalidObjectiveError(
                f"search.kind=noc_topology needs variants: [[topology, [dims...]], ...], got {variants!r}"
            )

    deferred = [m.metric for m in metrics if m.measured_at == "escalation"] + [
        c.metric for c in metric_constraints if c.measured_at == "escalation"
    ]
    if deferred and not (doc["backends"].get("escalation") or ()):
        raise InvalidObjectiveError(
            f"objectives {deferred} are measured_at=escalation but backends.escalation is "
            "empty — nothing would ever measure them"
        )
    if all(m.measured_at == "escalation" for m in metrics):
        raise InvalidObjectiveError(
            "every objective is measured_at=escalation — screening would have nothing to rank "
            "on, and the contender set (what escalation buys) would be undefined"
        )

    strategy = doc["strategy"]
    # generative <-> open_architecture, both directions (docs/decisions.md D233): a generative
    # strategy has no grid to walk, and an open space has nothing for grid/agentic to enumerate.
    if (strategy["kind"] == "generative") != (kind == "open_architecture"):
        raise InvalidObjectiveError(
            f"strategy.kind={strategy['kind']!r} with search.kind={kind!r} — generative "
            "strategies pair with search.kind=open_architecture and only with it"
        )
    # generative_interconnect <-> interconnect_topology, both directions and for the same
    # reason (docs/decisions.md D269): the strategy proposes fabric structures for a stated
    # client/bank/width problem, so it needs that problem, and pairing it with any other search
    # kind would leave it with nothing to propose against.
    if (strategy["kind"] == "generative_interconnect") != (kind == "interconnect_topology"
                                                          and strategy["kind"] != "grid"):
        if strategy["kind"] == "generative_interconnect":
            raise InvalidObjectiveError(
                f"strategy.kind='generative_interconnect' with search.kind={kind!r} — it "
                "proposes fabrics for a stated interconnect problem, so it pairs with "
                "search.kind=interconnect_topology and only with it"
            )
    if strategy["kind"] == "grid" and "llm_model" in strategy:
        raise InvalidObjectiveError(
            "strategy.kind=grid must not carry llm_model — a field that would be silently "
            "ignored misleads the reader about what ran"
        )

    stop_doc = doc.get("stop") or {}
    target: list[tuple[str, str, float]] = []
    for entry in stop_doc.get("target") or ():
        bounds = [(k, float(entry[k])) for k in ("max", "min") if k in entry]
        if not bounds:
            raise InvalidObjectiveError(f"stop.target entry {entry!r} carries no bound")
        for op, bound in bounds:
            target.append((entry["metric"], op, bound))

    budget_doc = doc["budget"]

    return Objective(
        doc=doc,
        objective_hash=flux_ir.content_hash(doc),
        id=doc["id"],
        mode=mode,
        metrics=metrics,
        metric_constraints=tuple(metric_constraints),
        check_validity=check_validity,
        workload=doc["workload"],
        base_arch=doc["base_arch"],
        screening_backend=doc["backends"]["screening"],
        escalation_backends=tuple(doc["backends"].get("escalation") or ()),
        search=dict(search),
        strategy_kind=strategy["kind"],
        strategy_seed=int(strategy.get("seed", 0)),
        llm_model=strategy.get("llm_model"),
        budget=BudgetGrant(
            evaluations=budget_doc.get("evaluations"),
            wall_clock_s=budget_doc.get("wall_clock_s"),
            usd=budget_doc.get("usd"),
        ),
        stop=StopCriteria(
            no_improvement_evaluations=stop_doc.get("no_improvement_evaluations"),
            target=tuple(target),
        ),
    )
