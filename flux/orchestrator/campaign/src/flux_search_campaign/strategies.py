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
from typing import Any, Protocol, runtime_checkable

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


def interconnect_variant_label(variant: dict) -> str:
    """The human-facing name of one interconnect variant, and the only definition of it.

    Every distinguishing parameter has to appear here: a label that two different fabrics share
    silently merges them in every table downstream (docs/decisions.md D267 — thirty Clos
    configurations once shared the name `clos`). The proposer refuses to generate a colliding
    pair rather than trusting this list to stay complete.
    """
    label = str(variant.get("kind", "interconnect"))
    if variant.get("ports") is not None:
        chain = "-".join(str(x) for x in variant["ports"])
        label += f"-{chain or 'direct'}"
    if variant.get("layers"):  # a hybrid is named by the families it chains, in order
        label += "-" + "-".join(
            str(x.get("family")) + "".join(
                f"{k}{v}" for k, v in sorted(x.items()) if k != "family")
            for x in variant["layers"])
    if variant.get("stages"):
        label += "-" + "-".join(
            f"{s['switches']}x{s['in']}x{s['out']}" for s in variant["stages"])
    # A router network is named by its geometry: `rows`/`cols` for a mesh or torus, `routers`
    # for a ring. Without these every mesh size collapsed to the single name "mesh", which the
    # proposer's collision check caught immediately — the check exists because this list going
    # stale is the failure mode, not a hypothetical one.
    if variant.get("rows") is not None and variant.get("cols") is not None:
        label += f"-{variant['rows']}x{variant['cols']}"
    # Only when it is not the default, so every label and every stored measurement taken before
    # routing was searchable keeps the exact name it had (D302).
    if variant.get("routing") and variant["routing"] != "rotate":
        label += f"-{variant['routing']}"
    for key in ("groups", "radix", "n", "m", "routers"):
        if key in variant:
            label += f"-{key}{variant[key]}"
    return label


def _grid_proposals(
    objective: Objective, base_arch: dict[str, Any], workload: dict[str, Any] | None = None,
) -> list[Proposal]:
    kind = objective.search["kind"]
    if kind == "composition_width":
        # Composition candidates are the one axis whose geometry depends on the WORKLOAD (one
        # engine per einsum op), so this branch needs what no other axis does (D236).
        if workload is None:
            raise ValueError(
                "search.kind=composition_width needs the resolved workload to enumerate per-op "
                "assignments — construct the strategy with workload="
            )
        from flux_search_architecture.composition_candidates import (
            generate_composition_candidates,
        )

        generated = generate_composition_candidates(
            base_arch, workload, objective.search.get("widths"),
            widths_per_op=objective.search.get("widths_per_op"),
        )
    elif kind == "composition_system":
        # per-op (width x memory size) engines (D251) — same workload dependency as above
        if workload is None:
            raise ValueError(
                "search.kind=composition_system needs the resolved workload — construct the "
                "strategy with workload="
            )
        from flux_search_architecture.composition_candidates import generate_system_candidates

        generated = generate_system_candidates(
            base_arch, workload, objective.search["widths"],
            objective.search["level"], objective.search["sizes_kb"],
            word_width_bits=objective.search.get("word_width_bits"),
        )
    elif kind == "architecture_width":
        from flux_search_architecture.candidates import generate_width_candidates

        generated = generate_width_candidates(base_arch, objective.search["widths"])
    elif kind == "memory_size":
        from flux_search_architecture.memory_candidates import generate_memory_size_candidates

        generated = generate_memory_size_candidates(
            base_arch, objective.search["level"], objective.search["sizes_kb"]
        )
    elif kind == "joint":
        from flux_search_architecture.memory_candidates import generate_joint_candidates

        generated = generate_joint_candidates(
            base_arch, objective.search["widths"], objective.search["level"],
            objective.search["sizes_kb"],
        )
    elif kind == "interconnect_topology":
        # One candidate per declared fabric variant (D261): the arch carries the variant in
        # its `interconnect` block, which both interconnect evaluators read.
        import copy

        variants = objective.search.get("variants")
        if variants is None:
            # No list given: DISCOVER the space from the problem statement (D262). The
            # objective says 28 clients, 32 banks, 128 bits, at most 3 stages; which
            # topologies are worth trying is the search's job, not the caller's.
            from flux_interconnect import enumerate_space

            variants = enumerate_space(
                int(objective.search["clients"]), int(objective.search["banks"]),
                int(objective.search["width_bits"]),
                max_stages=int(objective.search.get("max_stages", 3)),
                breadth=str(objective.search.get("breadth", "narrow")),
                max_candidates=int(objective.search.get("max_candidates", 5000)),
                families=objective.search.get("families") or None,
            )
            # Discovery plus anything the caller specifically wants compared: a named
            # structure the enumeration's families cannot express (an explicit
            # parallel-switch fabric, say) still belongs in the same measured run.
            variants = list(variants) + list(objective.search.get("extra_variants") or ())
        generated = []
        seen_labels: dict[str, dict] = {}
        for variant in variants:
            arch = copy.deepcopy(base_arch)
            arch["interconnect"] = dict(variant)
            label = interconnect_variant_label(variant)
            # A label is what every report, table and frontier row identifies a fabric BY, so
            # two distinct fabrics sharing one is not cosmetic: thirty Clos configurations all
            # labelled `clos` collapsed to a single row in the demo's own results, hiding
            # twenty-nine measured fabrics behind the last one written.
            if label in seen_labels and seen_labels[label] != variant:
                raise ValueError(
                    f"two different interconnect variants both label as {label!r}: "
                    f"{seen_labels[label]} and {variant} — extend "
                    "interconnect_variant_label() to name what distinguishes them"
                )
            seen_labels[label] = variant
            arch["id"] = f"{base_arch.get('id', 'arch')}-{label}"

            class _V:  # the to_dict() shape _grid_proposals expects
                def __init__(self, arch, variant, label):
                    self._d = {"variant": dict(variant), "label": label, "arch": arch}

                def to_dict(self):
                    return dict(self._d)

            generated.append(_V(arch, variant, label))
    elif kind == "noc_topology":
        from flux_search_architecture.noc_candidates import generate_noc_topology_candidates

        variants = [(v[0], list(v[1])) for v in objective.search["variants"]]
        generated = generate_noc_topology_candidates(base_arch, variants)
    else:  # unreachable: parse_objective validated the kind against the schema enum
        raise AssertionError(f"unvalidated search kind {kind!r} reached the strategy")

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


class AgenticStrategy:
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


class GenerativeStrategy:
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


_INTERCONNECT_PROMPT = """You design on-chip interconnect fabrics. Propose ONE new fabric.

PROBLEM: {clients} clients of {width_bits} bits must reach {banks} banks CONCURRENTLY.
GOALS: {objectives}
HARD CONSTRAINT: every switch must close timing at >= {target_mhz:.0f} MHz.

MEASURED ON THIS SILICON (ASAP7, {width_bits}-bit datapath) — an arbitrated K:1 selector runs:
  2:1 ~1241 MHz   4:1 ~772 MHz   8:1 ~754 MHz   16:1 ~589 MHz   28:1 ~455 MHz   32:1 ~406 MHz
So a switch with more than 8 inputs will MISS the target. Fabric area grows with the number of
selectors and their arity; inter-stage links cost wiring that the area figure does not include.
{knowledge}
PROPOSE exactly one JSON object, no prose, no markdown fence, one line:
  {{"kind": "xbar_staged", "stages": [{{"switches": S, "out": O}}, ...]}}   (1 to 3 stages)
  {{"kind": "clos", "n": N, "m": M}}
  {{"kind": "butterfly", "radix": R}}
  {{"kind": "xbar_staged", "ports": [P1, P2]}}   <- rank spelling of the same family
  {{"kind": "hybrid", "layers": [ ... ]}}   <- MIX FAMILIES, see below

For xbar_staged you choose only TWO numbers per stage: how many switches, and how many outputs
each switch has. Each switch's INPUT count is derived for you, so the stages always connect.
Choose so that:
  1. the product of every stage's `out` is >= {banks}, so each client can reach each bank
  2. the last stage: switches * out >= {banks}
  3. no switch ends up with more than 8 inputs, or it will miss the frequency target
     (stage 1 gives each switch about {clients}/switches inputs; a later stage gives each
      switch (previous switches * previous out) / its own switches inputs)
  4. EVERY stage must carry all {clients} clients at once: switches * out >= {clients} at every
     stage, no exceptions. The clients access the banks simultaneously, so a narrower stage is
     a bottleneck the problem does not permit, however small it makes the fabric. Seven
     switches with out=2 carry 14 and are refused before anything is built; the same seven
     with out=4 carry 28 and are not.

Worked example for 28 clients into 32 banks:
  [{{"switches": 7, "out": 4}}, {{"switches": 4, "out": 8}}]
  stage 1: 7 switches x 4 inputs each = 28 clients, emitting 7 x 4 = 28 links
  stage 2: 28 links / 4 switches = 7 inputs each, 4 x 8 = 32 banks. Reach = 4 x 8 = 32.
  Both stages carry 28 links, so full concurrency holds. Valid.

MIXING FAMILIES — the "hybrid" kind. Every classical fabric is a stage list, so one fabric can
use a Clos ingress and finish with a crossbar, or route with radix-4 switches and then fan out.
List the layers and they are chained for you:
  {{"family": "clos", "n": 4, "m": 4}}          a Clos ingress + middle stage
  {{"family": "radix", "radix": 4, "stages": 2}} that many stages of 4x4 switches
  {{"family": "xbar", "switches": 4}}           a crossbar stage fanning out to the banks
  {{"family": "concentrate", "factor": 2}}      squeeze this many links into one

Examples that are legitimate proposals, and none of them is a named classical fabric:
  {{"kind": "hybrid", "layers": [{{"family": "clos", "n": 4, "m": 4}}, {{"family": "xbar", "switches": 4}}]}}
  {{"kind": "hybrid", "layers": [{{"family": "radix", "radix": 8}}, {{"family": "xbar", "switches": 4}}]}}
  {{"kind": "hybrid", "layers": [{{"family": "concentrate", "factor": 2}}, {{"family": "radix", "radix": 4}}, {{"family": "xbar", "switches": 4}}]}}
Measured here: a Clos ingress feeding a crossbar reached 14.57 words/cycle, and a radix-8 layer
feeding a crossbar 14.08 — both real, both built and simulated. Mixing is worth trying.

ALREADY COVERED, across every round this store holds:
{coverage}

ALREADY MEASURED IN THIS ROUND (do not repeat these):
{history}
{rejection}
JSON only:"""


class InterconnectGenerativeStrategy:
    """LLM-proposed interconnect FABRICS, beyond the enumerated space (docs/decisions.md D269).

    The enumerator widens within families someone wrote; this proposes structures directly, so
    the model can express a fabric no generator rule produces. What makes that safe rather than
    reckless is that the model proposes and the deterministic machinery disposes: every
    proposal is built by the real constructor (which refuses a fabric whose stages do not chain
    or that cannot reach every bank), screened, measured on real silicon, and simulated with
    the correctness checks of D268. A bad proposal costs an evaluation. It cannot produce a
    wrong answer.

    Same honesty contract as the other LLM strategies: `deterministic=False`, the model name,
    sha256 of the exact prompt and response, and a seeded deterministic fallback recorded via
    `used_fallback`/`fallback_reason` rather than silently swapped in.
    """

    kind = "generative_interconnect"

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

        from flux_interconnect import build, enumerate_space

        self._objective = objective
        self._base_arch = base_arch
        self._llm = llm
        self._history = list(history or [])
        self._knowledge = knowledge
        self._rng = random.Random(objective.strategy_seed)
        search = objective.search
        self._clients = int(search["clients"])
        self._banks = int(search["banks"])
        self._width_bits = int(search["width_bits"])
        self._target_mhz = next(
            (float(c.bound) for c in objective.metric_constraints
             if c.metric == "fmax_mhz" and c.kind == "metric_min"), 600.0)
        self._visited = set(visited)
        # SEEDED from everything already tried, not empty. Left empty, the structural dedup only
        # caught repeats within one round, so across rounds and runs the model could propose the
        # same fabric indefinitely and each repeat cost a real model call and a screening slot.
        # Measured before this: 23 distinct proposals over many rounds, most of them re-proposals
        # of fabrics the store already held (docs/decisions.md D300).
        self._signatures: set[tuple] = set()
        self._covered: dict[str, int] = {}
        for key in self._visited:
            try:
                variant = json.loads(key).get("variant")
            except (TypeError, ValueError):
                continue
            if not isinstance(variant, dict):
                continue
            kind = str(variant.get("kind", "?"))
            self._covered[kind] = self._covered.get(kind, 0) + 1
            try:
                self._signatures.add(self._signature(variant))
            except Exception:  # noqa: BLE001 — a variant this build cannot construct is not
                continue      # a signature worth having, and must not break the round
        # Why the LAST proposal was refused, fed back into the next prompt. A rejected
        # proposal whose reason is never shown is a lesson the model cannot learn: measured
        # on a real 7B, the same inter-stage mistake recurred proposal after proposal.
        self._last_rejection: str | None = None
        # How many fabrics this round may propose. An open-ended strategy otherwise proposes
        # until the BUDGET latches, which means screening consumes the whole grant and
        # escalation never runs — measured: a 12-trial LLM round produced 12 proposals, zero
        # measurements, and an empty results table (docs/decisions.md D269).
        self._max_proposals = objective.doc.get("strategy", {}).get("max_proposals")
        self._proposed = 0
        self._build = build
        # The fallback pool: real, valid fabrics from the deterministic enumeration, so a
        # campaign always progresses even against a model that never returns usable JSON.
        self._pool = enumerate_space(self._clients, self._banks, self._width_bits,
                                     max_stages=3, breadth="wide", max_candidates=400)
        self._rng.shuffle(self._pool)

    def done(self) -> bool:
        """Open-ended by nature, but capped so that proposing does not eat the grant that
        MEASURING the proposals needs."""
        return bool(self._max_proposals) and self._proposed >= int(self._max_proposals)

    def _signature(self, spec: dict[str, Any]) -> tuple:
        topo = self._build(spec)
        # Includes routing for the same reason the enumerator's does (D302): a policy
        # variant is a different fabric to measure, not a duplicate.
        return (tuple(sorted(topo.blocks.items())), topo.stages,
                topo.peak_concurrency, topo.params.get("routing", "rotate"))

    def _derive_stage_inputs(self, stages: list[dict[str, Any]]) -> list[dict[str, int]]:
        """The library's derivation (docs/decisions.md D269), used here for the reason it
        exists: measured against a real qwen2.5-coder:7b, three proposals in four died on
        consecutive stages disagreeing on their link count. That is arithmetic consistency, not
        a design decision, so the model states only the decisions and this fills in the rest."""
        from flux_interconnect.topology import derive_stage_inputs

        return derive_stage_inputs(self._clients, stages)

    def _validate(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Build it for real, and prove routability constructively — the same two gates every
        enumerated candidate passes, applied to a proposal from a model."""
        from flux_interconnect.fabric import routing_tables

        # A model asked for "one fabric as a JSON object" quite often returns it wrapped in an
        # array. Observed twice in a single six-proposal round on qwen3.8, and the fabric inside
        # was well formed both times — refusing it spends an evaluation on a punctuation habit
        # rather than on a design judgement. One element is unwrapped; several is genuinely
        # ambiguous (which one was proposed?) and is still refused.
        if isinstance(spec, list) and len(spec) == 1:
            spec = spec[0]
        if not isinstance(spec, dict) or "kind" not in spec:
            raise ValueError(f"expected a JSON object with a `kind`, got {type(spec).__name__}")
        spec = dict(spec)
        if spec.get("kind") == "xbar_staged":
            stages = spec.get("stages")
            if not isinstance(stages, list) or not 1 <= len(stages) <= 3:
                raise ValueError(f"xbar_staged needs 1-3 stages, got {stages!r}")
            spec["stages"] = self._derive_stage_inputs(stages)
        full = {"clients": self._clients, "banks": self._banks,
                "width_bits": self._width_bits, **spec}
        topo = self._build(full)          # refuses mis-chained or unreachable fabrics
        routing_tables(topo)              # refuses anything the wiring cannot actually route
        # ONE definition of identity, `_signature`. This used to be a second inline copy, and
        # when the stored one gained routing (D302) the two stopped matching and dedup silently
        # stopped catching anything at all — including exact repeats, which is what it was built
        # for. A duplicated definition is the bug, not the drift.
        signature = self._signature(full)
        if signature in self._signatures:
            raise ValueError("structurally identical to a fabric already proposed")
        return full

    def _repair(self, spec: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
        """Try to fix a refused proposal instead of discarding it (docs/decisions.md D286).

        A proposal is usually refused over arithmetic, not over intent: a stage one link too
        narrow to carry every client, or a fan-out product that falls short of the bank count.
        Throwing the whole fabric away for that discards the part the model actually chose —
        how many switches, arranged in how many stages — and replaces it with something from a
        pool that has nothing to do with what it was reaching for.

        The repair widens; it never renarrows. Switch counts are the model's decision and are
        left alone, and only fan-outs move, upward, to the smallest value that satisfies the
        rule that was broken. A repair that still does not validate is abandoned rather than
        iterated, so this cannot loop.
        """
        if spec.get("kind") != "xbar_staged" or not isinstance(spec.get("stages"), list):
            return None
        stages = [dict(s) for s in spec["stages"]]
        if not stages:
            return None
        notes: list[str] = []

        for index, stage in enumerate(stages):
            switches = max(1, int(stage.get("switches", 1)))
            carried = switches * max(1, int(stage.get("out", 1)))
            if carried < self._clients:                       # cannot carry every client (D283)
                widened = -(-self._clients // switches)
                notes.append(f"stage {index + 1} fan-out {stage.get('out')}->{widened} "
                             f"to carry all {self._clients} clients")
                stage["out"] = widened

        last = stages[-1]
        if last["switches"] * max(1, int(last.get("out", 1))) < self._banks:
            widened = -(-self._banks // last["switches"])
            notes.append(f"last stage fan-out {last.get('out')}->{widened} to drive "
                         f"{self._banks} banks")
            last["out"] = widened

        reach = 1
        for stage in stages:
            reach *= max(1, int(stage.get("out", 1)))
        if reach < self._banks:                               # some bank is unreachable
            factor = -(-self._banks // reach)
            notes.append(f"last stage fan-out x{factor} so every client reaches every bank")
            last["out"] = int(last["out"]) * factor

        if not notes:
            return None
        try:
            repaired = self._validate({**spec, "stages": stages})
        except Exception:  # noqa: BLE001 — a repair that does not validate is simply abandoned
            return None
        return repaired, "; ".join(notes)

    def _fallback(self) -> dict[str, Any]:
        for spec in self._pool:
            try:
                candidate = self._validate(spec)
            except Exception:  # noqa: BLE001 — pool entries can collide with what was tried
                continue
            return candidate
        raise RuntimeError("no unseen valid fabric left in the deterministic fallback pool")

    def _format_history(self) -> str:
        if not self._history:
            return "(nothing yet)"
        lines = []
        for candidate, outcome in self._history[-12:]:
            label = candidate.get("label", "?")
            if isinstance(outcome, str):
                lines.append(f"  {label} -> FAILED: {outcome[:60]}")
            else:
                lines.append("  " + label + " -> " + ", ".join(
                    f"{k}={v:g}" for k, v in sorted(outcome.items())))
        return "\n".join(lines)

    def _coverage_note(self) -> str:
        """What the store already holds, by family and count.

        Compact on purpose: naming 1,156 fabrics would drown the prompt and cost prefill on
        every call. A model that knows `xbar_staged` has 900 entries and `mesh` has 5 can aim
        at what is thin, which is the actual question — "propose something not yet covered"
        cannot be answered by a model that is never told what IS covered.
        """
        if not self._covered:
            return "(nothing tried yet)"
        rows = ", ".join(f"{kind} x{n}" for kind, n in
                         sorted(self._covered.items(), key=lambda kv: -kv[1]))
        return (f"{sum(self._covered.values())} fabrics already tried: {rows}.\n"
                "  Proposing one that is structurally identical to any of them is REFUSED and "
                "wastes this round's budget; aim at a shape this list is thin on.")

    def _frontier_target(self) -> str:
        """The measured frontier so far, and the ONE gap worth aiming at (D281).

        Without this the model proposes into a vacuum: it is told the goal and shown a history,
        but nothing about which part of the trade-off is already well covered. Measured
        consequence — it clustered around the throughput end and produced a fabric that topped
        the screened table and then placed at 430 MHz, because nothing asked it for anything
        else. Naming the frontier and the widest hole in it turns open-ended generation into
        directed search, and costs one prompt paragraph.
        """
        points = []
        for candidate, outcome in self._history:
            if isinstance(outcome, str):
                continue
            area = outcome.get("area_mm2")
            served = outcome.get("throughput_words_per_cycle")
            if area and served:
                points.append((float(area), float(served), candidate.get("label", "?")))
        if len(points) < 2:
            return ""

        # Pareto-nondominated on (small area, high throughput), ordered by area
        points.sort()
        frontier: list[tuple[float, float, str]] = []
        best_served = -1.0
        for area, served, label in points:
            if served > best_served:
                frontier.append((area, served, label))
                best_served = served
        if len(frontier) < 2:
            return ""

        rendered = "\n".join(
            f"  {label}: {served:.1f} words/cycle at {area:.4f} mm2"
            for area, served, label in frontier)
        # the widest step in throughput between neighbouring frontier points
        gap = max(zip(frontier, frontier[1:]), key=lambda pair: pair[1][1] - pair[0][1])
        (lo_area, lo_served, _), (hi_area, hi_served, _) = gap
        return (
            f"\nTHE FRONTIER SO FAR (nothing else is worth repeating):\n{rendered}\n"
            f"\nAIM HERE: the widest gap is between {lo_served:.1f} words/cycle at "
            f"{lo_area:.4f} mm2 and {hi_served:.1f} at {hi_area:.4f} mm2. A fabric landing "
            f"inside that gap — more than {lo_served:.1f} words/cycle for less than "
            f"{hi_area:.4f} mm2 — improves the frontier. A fabric outside it has to beat an "
            f"existing point outright to be worth anything.\n")

    def propose(self) -> Proposal | None:
        import copy
        import hashlib

        # The runner advances to escalation when a proposer returns None; it never consults
        # done(). An open-ended strategy that always returns a Proposal therefore proposes
        # until the BUDGET latches, and the round measures nothing it proposed — observed
        # directly (docs/decisions.md D269).
        if self.done():
            return None

        objectives_text = ", ".join(
            f"{m.metric} {m.direction}" for m in self._objective.metrics)
        knowledge_block = (f"\n{self._knowledge}\n" if self._knowledge else "")
        rejection = (
            f"\nYOUR LAST PROPOSAL WAS REJECTED: {self._last_rejection}\n"
            "Fix exactly that and propose a different fabric.\n"
            if self._last_rejection else ""
        )
        prompt = _INTERCONNECT_PROMPT.format(
            clients=self._clients, banks=self._banks, width_bits=self._width_bits,
            objectives=objectives_text, target_mhz=self._target_mhz,
            knowledge=knowledge_block, history=self._format_history(),
            coverage=self._coverage_note(),
            rejection=self._frontier_target() + rejection,
        )
        prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()

        used_fallback, fallback_reason, response_sha = False, None, None
        repair_note: str | None = None
        spec: dict[str, Any] | None = None
        try:
            raw = self._llm.propose(prompt)
            response_sha = hashlib.sha256(raw.encode()).hexdigest()
            from flux_llm import strip_markdown_fence

            spec = self._validate(json.loads(strip_markdown_fence(raw).strip()))
            self._last_rejection = None
        except Exception as exc:  # noqa: BLE001 — a bad proposal is repaired or replaced
            self._last_rejection = str(exc)[:180]
            attempt = None
            try:
                attempt = self._repair(json.loads(strip_markdown_fence(raw).strip()))
            except Exception:  # noqa: BLE001 — unparseable input cannot be repaired
                attempt = None
            if attempt is not None:
                # the model's structure survived; the arithmetic was corrected, and the
                # correction is recorded so nobody reads the result as its unaided proposal
                spec, repair_note = attempt
            else:
                used_fallback = True
                fallback_reason = f"{type(exc).__name__}: {exc}"[:200]
                spec = self._fallback()

        self._signatures.add(self._signature(spec))
        self._proposed += 1
        label = interconnect_variant_label(spec)
        arch = copy.deepcopy(self._base_arch)
        arch["interconnect"] = dict(spec)
        arch["id"] = f"{self._base_arch.get('id', 'arch')}-{label}"
        candidate = {"variant": dict(spec), "label": label, "arch": arch}
        if repair_note:
            candidate["repaired"] = repair_note
        return Proposal(
            candidate=candidate, candidate_key=candidate_key(candidate), arch=arch,
            deterministic=False, llm_model=self._objective.llm_model,
            prompt_sha256=prompt_sha, response_sha256=response_sha,
            used_fallback=used_fallback, fallback_reason=fallback_reason,
        )

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
