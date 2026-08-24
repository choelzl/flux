"""Proposer strategies for campaigns (docs/decisions.md D216/D219).

`GridStrategy` is deterministic by construction: its proposal order is a pure function of the
objective document and the visited set, which is what makes a resumed grid campaign
bit-identical to an uninterrupted one (verified by DB-equivalence in the integration suite, not
asserted). The candidate *generators* are `flux_search_architecture`'s own — this module decides
order and bookkeeping, never geometry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from .objective import Objective


@dataclass(frozen=True, slots=True)
class Proposal:
    """One proposed trial: the arch to evaluate plus the provenance the trial row records."""

    candidate: dict[str, Any]  # what lands in trials.candidate_json (generator's to_dict())
    candidate_key: str  # canonical dedup key
    arch: dict[str, Any]  # the Architecture IR document to evaluate
    deterministic: bool
    llm_model: str | None = None
    prompt_sha256: str | None = None
    response_sha256: str | None = None
    used_fallback: bool | None = None
    fallback_reason: str | None = None


@runtime_checkable
class ProposerStrategy(Protocol):
    """`propose()` returns the next unvisited Proposal or None when the space is exhausted;
    `observe()` feeds the measured outcome back (grid ignores it; agentic prompts with it)."""

    kind: str

    def propose(self) -> Proposal | None: ...

    def observe(self, proposal: Proposal, result: Any | None, error: str | None) -> None: ...


def candidate_key(candidate: dict[str, Any]) -> str:
    """Canonical key for the visited set: sorted-key JSON of the generator candidate minus the
    full arch document (the parameters identify the point; the arch is derived from them)."""
    slim = {k: v for k, v in candidate.items() if k != "arch"}
    return json.dumps(slim, sort_keys=True)


# ---- registries (D428): what an APPLICATION adds to the campaign, by name. The
# ---- campaign package imports no application; an application registers its search
# ---- kind and its strategy (or is listed here as a lazy module:attribute pair, the
# ---- same shape as the evaluator registry, so a task document can name it before
# ---- anything imported the application).
_CANDIDATE_GENERATORS: dict[str, Callable[..., list[Any]]] = {}
_APP_CANDIDATE_GENERATORS: dict[str, tuple[str, str]] = {
    "interconnect_topology": ("flux_interconnect.campaign", "topology_candidates"),
}


def register_candidate_generator(kind: str, fn: Callable[..., list[Any]]) -> None:
    """`fn(objective, base_arch, workload) -> [objects with .to_dict()]` for a search kind."""
    _CANDIDATE_GENERATORS[kind] = fn


def _needs_workload(kind: str, workload: dict[str, Any] | None) -> dict[str, Any]:
    # Composition candidates are the axes whose geometry depends on the WORKLOAD (one engine
    # per einsum op), so they need what no other axis does (D236).
    if workload is None:
        raise ValueError(f"search.kind={kind} needs the resolved workload to enumerate per-op "
                         "assignments — construct the strategy with workload=")
    return workload


def _gen_composition_width(objective: Objective, base_arch: dict[str, Any], workload: Any) -> list[Any]:
    from flux_search_architecture.composition_candidates import generate_composition_candidates

    return generate_composition_candidates(
        base_arch, _needs_workload("composition_width", workload), objective.search.get("widths"),
        widths_per_op=objective.search.get("widths_per_op"))


def _gen_composition_system(objective: Objective, base_arch: dict[str, Any], workload: Any) -> list[Any]:
    from flux_search_architecture.composition_candidates import generate_system_candidates

    return generate_system_candidates(     # per-op (width x memory size) engines (D251)
        base_arch, _needs_workload("composition_system", workload), objective.search["widths"],
        objective.search["level"], objective.search["sizes_kb"],
        word_width_bits=objective.search.get("word_width_bits"))


def _gen_architecture_width(objective: Objective, base_arch: dict[str, Any], workload: Any) -> list[Any]:
    from flux_search_architecture.candidates import generate_width_candidates

    return generate_width_candidates(base_arch, objective.search["widths"])


def _gen_memory_size(objective: Objective, base_arch: dict[str, Any], workload: Any) -> list[Any]:
    from flux_search_architecture.memory_candidates import generate_memory_size_candidates

    return generate_memory_size_candidates(base_arch, objective.search["level"], objective.search["sizes_kb"])


def _gen_joint(objective: Objective, base_arch: dict[str, Any], workload: Any) -> list[Any]:
    from flux_search_architecture.memory_candidates import generate_joint_candidates

    return generate_joint_candidates(base_arch, objective.search["widths"], objective.search["level"],
                                     objective.search["sizes_kb"])


def _gen_noc_topology(objective: Objective, base_arch: dict[str, Any], workload: Any) -> list[Any]:
    from flux_search_architecture.noc_candidates import generate_noc_topology_candidates

    variants = [(v[0], list(v[1])) for v in objective.search["variants"]]
    return generate_noc_topology_candidates(base_arch, variants)


# The built-in search kinds, registered like an application's (D439): one table, no if/elif.
for _kind, _fn in (("composition_width", _gen_composition_width),
                   ("composition_system", _gen_composition_system),
                   ("architecture_width", _gen_architecture_width),
                   ("memory_size", _gen_memory_size), ("joint", _gen_joint),
                   ("noc_topology", _gen_noc_topology)):
    _CANDIDATE_GENERATORS[_kind] = _fn


def candidate_generator(kind: str) -> Callable[..., list[Any]] | None:
    if kind in _CANDIDATE_GENERATORS:
        return _CANDIDATE_GENERATORS[kind]
    if kind in _APP_CANDIDATE_GENERATORS:
        import importlib

        module, attr = _APP_CANDIDATE_GENERATORS[kind]
        return getattr(importlib.import_module(module), attr)
    return None


class RemembersOutcomes:
    """What a proposer strategy tells its model about the proposal it just made
    (D467): the metrics the objective asked for when there is a result, the error when
    there is not, appended to its own `_history`.

    Shared because this is the one part of a generative strategy that is not its own.
    Three classes had it line for line -- `AgenticStrategy`, `GenerativeStrategy` and
    the interconnect study's own -- which is how a metric the objective stops asking
    for gets dropped in one copy and remembered in the others."""

    _objective: Any
    _history: list

    def observe(self, proposal: Proposal, result: Any | None, error: str | None) -> None:
        if result is not None:
            values = {}
            for m in self._objective.metrics:
                outcome = result.metric(m.metric)
                if outcome.ok:
                    values[m.metric] = outcome.value
            self._history.append((proposal.candidate, values))
        else:
            self._history.append((proposal.candidate, error or "no result"))


@dataclass(frozen=True)
class StrategySpec:
    """How the runner builds a registered strategy: `factory(objective, base_arch,
    visited, llm, *, history, knowledge)`; whether it needs a model; whether its
    visited set is the whole store's rather than this campaign's."""

    factory: Callable[..., Any]
    needs_llm: bool = True
    store_wide_visited: bool = False
    search_kinds: frozenset[str] | None = None    # the search kinds it pairs with; None = any (D442)


_STRATEGIES: dict[str, StrategySpec] = {}
_APP_STRATEGIES: dict[str, tuple[str, str]] = {
    "generative_interconnect": ("flux_interconnect.campaign", "STRATEGY"),
}


def register_strategy(kind: str, spec: StrategySpec) -> None:
    _STRATEGIES[kind] = spec


def strategy_spec(kind: str) -> StrategySpec | None:
    if kind in _STRATEGIES:
        return _STRATEGIES[kind]
    if kind in _APP_STRATEGIES:
        import importlib

        module, attr = _APP_STRATEGIES[kind]
        return getattr(importlib.import_module(module), attr)
    return None


def __getattr__(name: str) -> Any:
    """The interconnect names this module used to define, for anyone still
    importing them from here (D428)."""
    if name in ("InterconnectGenerativeStrategy", "interconnect_variant_label"):
        import importlib

        return getattr(importlib.import_module("flux_interconnect.campaign"), name)
    raise AttributeError(name)



class GridStrategy:
    kind = "grid"

    def __init__(
        self, objective: Objective, base_arch: dict[str, Any], visited: set[str],
        workload: dict[str, Any] | None = None,
    ) -> None:
        self._proposals = _grid_proposals(objective, base_arch, workload)
        self._visited = set(visited)

    def propose(self) -> Proposal | None:
        for proposal in self._proposals:
            if proposal.candidate_key not in self._visited:
                self._visited.add(proposal.candidate_key)
                return proposal
        return None

    def observe(self, proposal: Proposal, result: Any | None, error: str | None) -> None:
        pass  # a grid has nothing to learn

    def done(self) -> bool:
        return all(p.candidate_key in self._visited for p in self._proposals)


def _grid_proposals(
    objective: Objective, base_arch: dict[str, Any], workload: dict[str, Any] | None = None,
) -> list[Proposal]:
    kind = objective.search["kind"]
    generator = candidate_generator(kind)
    if generator is None:  # unreachable: parse_objective validated the kind against the schema enum
        raise AssertionError(f"unvalidated search kind {kind!r} reached the strategy")
    generated = generator(objective, base_arch, workload)

    proposals = []
    for c in generated:
        d = c.to_dict()
        proposals.append(
            Proposal(candidate=d, candidate_key=candidate_key(d), arch=d["arch"], deterministic=True)
        )
    return proposals


_AGENTIC_PROMPT = """You are exploring accelerator design candidates to optimize several metrics at once.

Objectives (all must be considered; this is a Pareto search unless weights are shown):
{objectives}
{knowledge}
Untried candidates (each is a JSON object of design parameters):
{candidates}

Already tried (with measured results):
{history}

Propose the ONE untried candidate most likely to improve the Pareto frontier.
Respond with JSON only — exactly one candidate object copied verbatim from the untried list.
"""


class AgenticStrategy(RemembersOutcomes):
    """LLM-proposed candidates over ANY grid axis (docs/decisions.md D219/D227). The proposal
    space is the same finite candidate list `GridStrategy` walks; the LLM chooses which unvisited
    point to buy next, so validation is pure membership — one mechanism for every search kind
    instead of a per-axis prompt/parser pair (the fix-never-travels shape, avoided by design).

    Non-deterministic by nature, and *recorded* as such: every proposal carries
    `deterministic=False`, the model name, and sha256 hashes of the exact prompt and response.
    A parse failure or a non-membership proposal falls back to a seeded-random unvisited
    candidate, recorded via `used_fallback`/`fallback_reason` — never silently swapped.
    """

    kind = "agentic"

    # A prompt listing thousands of candidates stops being a choice and starts being noise; the
    # cap keeps the listing legible and is stated in the fallback reason when it bites.
    _MAX_LISTED = 64

    def __init__(
        self,
        objective: Objective,
        base_arch: dict[str, Any],
        visited: set[str],
        llm: Any,  # anything with .propose(prompt: str) -> str (flux_llm.LLMProposer shape)
        history: list[tuple[dict[str, Any], dict[str, float] | str]] | None = None,
        workload: dict[str, Any] | None = None,
        knowledge: str | None = None,
    ) -> None:
        import random

        self._objective = objective
        self._proposals = _grid_proposals(objective, base_arch, workload)
        self._llm = llm
        self._history: list[tuple[dict[str, Any], dict[str, float] | str]] = list(history or [])
        self._visited = set(visited)
        self._rng = random.Random(objective.strategy_seed)
        # Pre-rendered advisory text (docs/decisions.md D245) — e.g. mined facts via
        # flux_knowledge_mining.render_facts_for_prompt, boundaries included. Opaque here on
        # purpose: this package stays free of the mining dependency, and the trial row's
        # prompt_sha256 already captures exactly what the model saw.
        self._knowledge = knowledge

    def _unvisited(self) -> list[Proposal]:
        return [p for p in self._proposals if p.candidate_key not in self._visited]

    def done(self) -> bool:
        return not self._unvisited()

    @staticmethod
    def _slim(candidate: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in candidate.items() if k != "arch"}

    def _format_history(self) -> str:
        if not self._history:
            return "(none yet)"
        lines = []
        for candidate, outcome in self._history:
            label = json.dumps(self._slim(candidate), sort_keys=True)
            if isinstance(outcome, str):
                lines.append(f"{label} -> FAILED: {outcome}")
            else:
                rendered = ", ".join(f"{k}={v:g}" for k, v in sorted(outcome.items()))
                lines.append(f"{label} -> {rendered}")
        return "\n".join(lines)

    def propose(self) -> Proposal | None:
        import hashlib

        unvisited = self._unvisited()
        if not unvisited:
            return None
        listed = unvisited[: self._MAX_LISTED]

        objectives_text = "\n".join(
            f"- {m.metric}: {m.direction}" + (f" (weight {m.weight})" if m.weight else "")
            for m in self._objective.metrics
        )
        knowledge_block = (
            f"\nMeasured facts from prior work (each with its limits):\n{self._knowledge}\n"
            if self._knowledge else ""
        )
        prompt = _AGENTIC_PROMPT.format(
            objectives=objectives_text,
            knowledge=knowledge_block,
            candidates="\n".join(json.dumps(self._slim(p.candidate), sort_keys=True)
                                  for p in listed),
            history=self._format_history(),
        )
        prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()

        used_fallback, fallback_reason, response_sha = False, None, None
        repair_note: str | None = None
        chosen: Proposal | None = None
        try:
            raw = self._llm.propose(prompt)
            response_sha = hashlib.sha256(raw.encode()).hexdigest()
            from flux_llm import strip_markdown_fence

            parsed = json.loads(strip_markdown_fence(raw))
            if not isinstance(parsed, dict):
                raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
            wanted = json.dumps(parsed, sort_keys=True)
            chosen = next(
                (p for p in unvisited
                 if json.dumps(self._slim(p.candidate), sort_keys=True) == wanted),
                None,
            )
            if chosen is None:
                used_fallback = True
                fallback_reason = (
                    f"LLM proposal {wanted[:120]} is not an unvisited candidate"
                    + (f" (listing capped at {self._MAX_LISTED})"
                       if len(unvisited) > self._MAX_LISTED else "")
                )
        except Exception as exc:  # noqa: BLE001 — a bad proposal is a fallback, not a crash
            used_fallback = True
            fallback_reason = f"{type(exc).__name__}: {exc}"[:200]
        if chosen is None:
            chosen = self._rng.choice(unvisited)

        self._visited.add(chosen.candidate_key)
        return Proposal(
            candidate=chosen.candidate, candidate_key=chosen.candidate_key, arch=chosen.arch,
            deterministic=False, llm_model=self._objective.llm_model, prompt_sha256=prompt_sha,
            response_sha256=response_sha, used_fallback=used_fallback,
            fallback_reason=fallback_reason,
        )



_GENERATIVE_PROMPT = """You are proposing a new hardware accelerator architecture to optimize
several metrics at once for a fixed workload.

Objectives (all must be considered; this is a Pareto search unless weights are shown):
{objectives}
{knowledge}
The current reference architecture (Flux Architecture IR, YAML):
```yaml
{base_yaml}```

Already tried (architecture summary -> measured results):
{history}

Propose ONE NEW architecture as a complete document with the SAME structure as the reference:
same schema_version, the same hierarchy levels (same `level` and `class` names, same order), the
compute node keeping the same dim names under `attrs.dims`. You may change: each compute dim's
integer size, and each memory level's `attrs.size_kb`. Give it a new `id`. It must differ from
every architecture already tried.

Output ONLY the complete YAML document in a ```yaml fenced code block — nothing else."""


class GenerativeStrategy(RemembersOutcomes):
    """LLM-proposed NOVEL architectures (docs/decisions.md D233) — campaigns stop being
    parameter sweeps: instead of picking from an enumerated grid, the model writes a complete
    Architecture IR document each round, validated by the real schema plus a structural guard
    (same hierarchy skeleton as the base, so every candidate stays inside the screening
    backend's expressible space — D131's own scope), deduplicated by content hash.

    Same honesty contract as `AgenticStrategy`: every proposal is `deterministic=False` with
    model + prompt/response hashes; a failed or duplicate proposal falls back to a seeded
    deterministic mutation of the base architecture (double or halve one knob), recorded via
    `used_fallback`/`fallback_reason`, never silently swapped.
    """

    kind = "generative"

    def __init__(
        self,
        objective: Objective,
        base_arch: dict[str, Any],
        visited: set[str],
        llm: Any,
        history: list[tuple[dict[str, Any], dict[str, float] | str]] | None = None,
        knowledge: str | None = None,
    ) -> None:
        import random

        self._objective = objective
        self._base_arch = base_arch
        self._llm = llm
        self._history: list[tuple[dict[str, Any], dict[str, float] | str]] = list(history or [])
        self._rng = random.Random(objective.strategy_seed)
        self._knowledge = knowledge  # same contract as AgenticStrategy's (D245)
        self._seen_hashes: set[str] = set()
        for key in visited:
            try:
                entry = json.loads(key)
            except ValueError:
                continue
            if "arch_hash" in entry:
                self._seen_hashes.add(entry["arch_hash"])

    def done(self) -> bool:
        return False  # open-ended: the budget latch and stop criteria end the campaign

    # -- structural guard ----------------------------------------------------------------

    def _skeleton(self, arch: dict[str, Any]) -> list[tuple[str, str]]:
        return [(n.get("level"), n.get("class")) for n in arch.get("hierarchy", [])]

    def _validate(self, doc: dict[str, Any]) -> None:
        import flux_ir

        flux_ir.validate("architecture", doc)
        if self._skeleton(doc) != self._skeleton(self._base_arch):
            raise ValueError(
                f"hierarchy skeleton {self._skeleton(doc)} differs from the base "
                f"{self._skeleton(self._base_arch)} — only dim sizes and size_kb may change"
            )
        for node in doc.get("hierarchy", []):
            attrs = node.get("attrs") or {}
            if node.get("class") == "compute":
                dims = attrs.get("dims") or {}
                base_node = next(n for n in self._base_arch["hierarchy"]
                                 if n.get("level") == node.get("level"))
                if set(dims) != set((base_node.get("attrs") or {}).get("dims") or {}):
                    raise ValueError(f"compute dims keys {sorted(dims)} changed")
                if not all(isinstance(v, int) and v >= 1 for v in dims.values()):
                    raise ValueError(f"compute dims must be integers >= 1, got {dims}")
            if node.get("class") == "memory":
                size = attrs.get("size_kb")
                if not isinstance(size, (int, float)) or size <= 0:
                    raise ValueError(f"memory {node.get('level')!r} size_kb={size!r} invalid")

    def _summary(self, arch: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"id": arch.get("id")}
        for node in arch.get("hierarchy", []):
            attrs = node.get("attrs") or {}
            if node.get("class") == "compute":
                out[f"{node['level']}.dims"] = dict(attrs.get("dims") or {})
            elif node.get("class") == "memory":
                out[f"{node['level']}.size_kb"] = attrs.get("size_kb")
        return out

    def _mutated_fallback(self) -> dict[str, Any]:
        """Seeded deterministic mutation: double or halve one knob of the base, skipping seen
        hashes. Guarantees campaign progress when the LLM cannot produce a fresh valid doc."""
        import copy

        import flux_ir

        for _ in range(50):
            doc = copy.deepcopy(self._base_arch)
            knobs = []
            for node in doc["hierarchy"]:
                attrs = node.get("attrs") or {}
                if node.get("class") == "compute":
                    for dim in (attrs.get("dims") or {}):
                        knobs.append(("dim", node, dim))
                elif node.get("class") == "memory" and "size_kb" in attrs:
                    knobs.append(("mem", node, "size_kb"))
            kind, node, key = self._rng.choice(knobs)
            factor = self._rng.choice([0.5, 2, 4])
            if kind == "dim":
                value = max(1, int(node["attrs"]["dims"][key] * factor))
                node["attrs"]["dims"][key] = value
                doc["id"] = f"{self._base_arch.get('id', 'arch')}-gen-{key}{value}"
            else:
                value = max(1, int(node["attrs"]["size_kb"] * factor))
                node["attrs"]["size_kb"] = value
                doc["id"] = f"{self._base_arch.get('id', 'arch')}-gen-{node['level']}{value}"
            if flux_ir.content_hash(doc) not in self._seen_hashes:
                return doc
        raise RuntimeError("mutation fallback could not find an unseen architecture in 50 draws")

    def _format_history(self) -> str:
        if not self._history:
            return "(none yet)"
        lines = []
        for candidate, outcome in self._history:
            label = json.dumps(candidate.get("summary", {}), sort_keys=True)
            if isinstance(outcome, str):
                lines.append(f"{label} -> FAILED: {outcome}")
            else:
                rendered = ", ".join(f"{k}={v:g}" for k, v in sorted(outcome.items()))
                lines.append(f"{label} -> {rendered}")
        return "\n".join(lines)

    def propose(self) -> Proposal | None:
        import hashlib

        import flux_ir
        import yaml as _yaml

        objectives_text = "\n".join(
            f"- {m.metric}: {m.direction}" + (f" (weight {m.weight})" if m.weight else "")
            for m in self._objective.metrics
        )
        knowledge_block = (
            f"\nMeasured facts from prior work (each with its limits):\n{self._knowledge}\n"
            if self._knowledge else ""
        )
        prompt = _GENERATIVE_PROMPT.format(
            objectives=objectives_text,
            knowledge=knowledge_block,
            base_yaml=_yaml.safe_dump(self._base_arch, sort_keys=False),
            history=self._format_history(),
        )
        prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()

        used_fallback, fallback_reason, response_sha = False, None, None
        repair_note: str | None = None
        arch: dict[str, Any] | None = None
        try:
            raw = self._llm.propose(prompt)
            response_sha = hashlib.sha256(raw.encode()).hexdigest()
            from flux_llm import strip_markdown_fence

            arch = _yaml.safe_load(strip_markdown_fence(raw))
            if not isinstance(arch, dict):
                raise ValueError(f"expected a YAML mapping, got {type(arch).__name__}")
            self._validate(arch)
            if flux_ir.content_hash(arch) in self._seen_hashes:
                raise ValueError("proposed architecture is identical to one already tried")
        except Exception as exc:  # noqa: BLE001 — a bad proposal is a fallback, not a crash
            used_fallback = True
            fallback_reason = f"{type(exc).__name__}: {exc}"[:200]
            arch = self._mutated_fallback()

        arch_hash = flux_ir.content_hash(arch)
        self._seen_hashes.add(arch_hash)
        candidate = {"generated": True, "arch_hash": arch_hash,
                     "summary": self._summary(arch), "arch": arch}
        return Proposal(
            candidate=candidate, candidate_key=candidate_key(candidate), arch=arch,
            deterministic=False, llm_model=self._objective.llm_model,
            prompt_sha256=prompt_sha, response_sha256=response_sha,
            used_fallback=used_fallback, fallback_reason=fallback_reason,
        )

