"""`flux_author_objective` — natural language to a validated Objective IR document
(docs/decisions.md D232): the NL->objective step the four-roles mapping named as this repo's
genuine thin spot.

The same generate-validate-repair shape as `flux_generate_rtl_module` (D44), with the validator
being the REAL objective parser — JSON schema plus `parse_objective`'s semantic checks — and the
real error fed back for a bounded number of repairs. The LLM never sees or echoes the workload
or architecture documents (the node injects them after parsing, so a large model cannot mangle a
large document), and authoring never *runs* anything: the output is a validated document the
caller hands to `flux_campaign_start`, or doesn't. Provenance inside the document records the
model and the exact prose, so a campaign started from an authored objective is auditable back to
the sentence that asked for it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_llm import default_local_model, strip_markdown_fence

_DEFAULT_MODEL = default_local_model()
_MAX_REPAIR_ATTEMPTS = 3

_PROMPT_TEMPLATE = """Turn this request into a Flux Objective IR document (JSON).

Request: {prose}

Workload summary: id={workload_id!r}, {n_ops} einsum op(s), dims {bounds_summary}.
Architecture summary: id={arch_id!r}, compute dims {compute_dims}, memory levels {memory_levels}.

The document must be a single JSON object with EXACTLY these fields (no others, and do NOT
include "workload" or "base_arch" — they are attached afterwards):
- "schema_version": "0.1.0"
- "id": a short slug for this campaign
- "objectives": list of {{"metric": <str>, "direction": "minimize"|"maximize"}} entries.
  With "mode": "pareto", entries must have NO "weight" field at all — the validator rejects
  weights in pareto mode. Only with "mode": "weighted" does every entry need a
  "weight": <positive number>.
  Common metrics: "latency_cycles", "energy_pj", "area_mm2", "power_w".
  RULE: when "screening" is "zigzag" (an analytic model that cannot measure silicon), any
  "area_mm2" or "power_w" objective entry MUST carry "measured_at": "escalation" — never omit
  it on those metrics.
- "mode": "pareto" (keep a frontier) or "weighted" (scalarize with the weights)
- "backends": {{"screening": <backend name>, "escalation": [<backend names in rung order>]}}.
  Screening should be a fast analytic backend ("zigzag"); escalation rungs may include "rtl"
  (cycle-accurate simulation) and "openroad" (placed-silicon area/power). Omit "escalation"
  or use [] if the request needs no higher fidelity.
- "search": one of
    {{"kind": "architecture_width", "widths": [<ints>]}}
    {{"kind": "composition_width", "widths": [<ints>]}} — for MULTI-op workloads when the
      request asks for per-op/per-layer engine sizing: every op gets its own engine at one of
      the listed widths, latency/energy/area compose over the chain. When the request gives
      DIFFERENT width choices for specific layers, add "widths_per_op": {{<op id>: [<ints>]}}
      (ops not listed there fall back to "widths")
    {{"kind": "composition_system", "widths": [<ints>], "level": <memory level name>,
      "sizes_kb": [<numbers>]}} — like composition_width but each per-op engine is also sized
      in the named memory level: the per-op grid over (width x size) points. Add
      "word_width_bits": <int> when an "area_mm2" objective uses a "cacti" escalation rung
      (CACTI needs the SRAM word width and refuses to guess it)
    {{"kind": "memory_size", "level": <memory level name>, "sizes_kb": [<numbers>]}}
    {{"kind": "joint", "widths": [...], "level": ..., "sizes_kb": [...]}}
    {{"kind": "noc_topology", "variants": [[<topology>, [<dims>]], ...]}}
- "strategy": {{"kind": "grid", "seed": 0}} unless the request asks for LLM-driven choice
  among the listed candidates ({{"kind": "agentic", "seed": 0, "llm_model": <model>}}) or for
  INVENTING new architectures beyond any list ({{"kind": "generative", "seed": 0,
  "llm_model": <model>}} — which requires "search": {{"kind": "open_architecture"}})
- "budget": at least one of {{"evaluations": <int>, "wall_clock_s": <number>}} — honor any
  budget stated in the request; default to {{"evaluations": 16}} if none is stated.
{facts_block}
- "stop": optional; {{"no_improvement_evaluations": <int>}} and/or
  {{"target": [{{"metric": ..., "max"/"min": <number>}}]}}.

Output ONLY the JSON object — no markdown fences, no explanation."""

_REPAIR_TEMPLATE = """Your previous Objective IR document was rejected by the validator.

--- your previous document ---
{prior}

--- real validation error ---
{error}

Fix the document and output ONLY the corrected JSON object — same rules as before."""


# What each backend the PROMPT offers can actually produce (docs/decisions.md D240) — read
# from the adapters, not asserted: zigzag's adapter emits exactly latency+energy (it never
# writes an area key), rtl populates latency only, openroad parses area and power from the
# placed design. Backends the prompt does not offer are absent, and absence means UNCHECKED —
# an honest "don't know", never a refusal. This is the mechanical half of prose-faithfulness:
# D239's first capstone run authored area_mm2 as a screen metric over zigzag, which the schema
# validator legally accepts and every screening trial would then have refused at runtime (or
# worse, ranked on a constant). Caught here, the mistake becomes REPAIR INPUT at authoring
# time instead of a dead campaign — the same pull-it-forward shape as D235's reserved-word
# check.
_BACKEND_METRICS = {
    "zigzag": frozenset({"latency_cycles", "energy_pj"}),
    "rtl": frozenset({"latency_cycles"}),
    "openroad": frozenset({"area_mm2", "power_w"}),
}


def _check_backend_capabilities(doc: dict[str, Any]) -> None:
    """Every objective metric must have a backend that can measure it WHERE it is measured.
    Raises ValueError with an actionable message — the repair loop's input."""
    screening = doc["backends"]["screening"]
    rungs = list(doc["backends"].get("escalation") or ())
    for o in doc["objectives"]:
        metric = o["metric"]
        if o.get("measured_at", "screen") == "screen":
            if screening in _BACKEND_METRICS and metric not in _BACKEND_METRICS[screening]:
                raise ValueError(
                    f"objective {metric!r} is screen-measured but the screening backend "
                    f"{screening!r} can only produce {sorted(_BACKEND_METRICS[screening])} — "
                    f"add \"measured_at\": \"escalation\" to that objective entry and an "
                    "escalation rung that measures it (e.g. \"openroad\" for area/power)"
                )
        else:
            known_rungs = [r for r in rungs if r in _BACKEND_METRICS]
            if known_rungs == rungs and not any(
                metric in _BACKEND_METRICS[r] for r in rungs
            ):
                raise ValueError(
                    f"objective {metric!r} is measured_at=escalation but no escalation rung in "
                    f"{rungs} produces it — add a rung that does (e.g. \"openroad\" for "
                    "area/power, \"rtl\" for latency)"
                )


@dataclass
class AuthoredObjective:
    success: bool
    attempts: int
    objective: dict[str, Any] | None  # validated document, workload/base_arch attached
    error: str | None
    transcript: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "attempts": self.attempts,
            "objective": self.objective,
            "error": self.error,
            "transcript": list(self.transcript),
        }


def _summaries(workload: dict[str, Any], base_arch: dict[str, Any]) -> dict[str, Any]:
    ops = workload.get("ops", [])
    compute = next(
        (n for n in base_arch.get("hierarchy", []) if n.get("class") == "compute"), {})
    memories = [n.get("level") for n in base_arch.get("hierarchy", [])
                if n.get("class") == "memory"]
    return {
        "workload_id": workload.get("id", "<workload>"),
        "n_ops": len(ops),
        "bounds_summary": [op.get("bounds") for op in ops[:3]],
        "arch_id": base_arch.get("id", "<arch>"),
        "compute_dims": (compute.get("attrs") or {}).get("dims"),
        "memory_levels": memories,
    }


@ChiaFunction()
def flux_author_objective(
    prose: str,
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    *,
    model: str = _DEFAULT_MODEL,
    max_repair_attempts: int = _MAX_REPAIR_ATTEMPTS,
    llm: Any | None = None,
    facts: list[dict[str, Any]] | None = None,
) -> AuthoredObjective:
    """Author a validated Objective IR document from a natural-language request. The validator
    is the real campaign parser; a rejected document is repaired with the real error fed back,
    up to `max_repair_attempts` times. Returns the document — it is never executed here.
    `llm` injects a duck-typed `.prompt(str).result` client (D234's scripted-proposer pattern);
    None constructs the real Ollama client."""
    from flux_search_campaign import parse_objective

    if llm is None:
        from chia.models.ollama import OllamaLLM

        llm = OllamaLLM(
            model=model,
            system_message="You write minimal, valid JSON documents. No prose, no fences.",
        )
    facts_block = ""
    if facts:
        from flux_knowledge_mining import render_facts_for_prompt

        facts_block = (
            "\nMeasured facts from prior campaigns (each with its limits — use them to pick "
            "realistic budgets, stop targets and search ranges):\n"
            + render_facts_for_prompt(facts) + "\n"
        )
    prompt = _PROMPT_TEMPLATE.format(prose=prose, facts_block=facts_block,
                                     **_summaries(workload, base_arch))
    transcript: list[str] = []
    doc: dict[str, Any] | None = None
    last_error = ""

    for attempt in range(1, max_repair_attempts + 1):
        transcript.append(f"--- attempt {attempt} prompt ---\n{prompt}")
        raw = strip_markdown_fence(llm.prompt(prompt).result)
        transcript.append(f"--- attempt {attempt} response ---\n{raw}")
        try:
            doc = json.loads(raw)
            if not isinstance(doc, dict):
                raise ValueError(f"expected a JSON object, got {type(doc).__name__}")
            for forbidden in ("workload", "base_arch"):
                doc.pop(forbidden, None)  # attached below; an echoed copy is discarded
            doc["workload"] = {"inline": workload}
            doc["base_arch"] = {"inline": base_arch}
            # the audit trail: which model, from which exact sentence — inside the document,
            # so it is part of the campaign's own content-hashed identity
            doc.setdefault("provenance", {})
            doc["provenance"].update({"source": "llm-authored", "model": model, "prose": prose})
            parse_objective(doc)  # the REAL validator: schema + semantics
            _check_backend_capabilities(doc)  # + what the named backends can measure (D240)
        except Exception as exc:  # noqa: BLE001 — every failure becomes repair input
            last_error = f"{type(exc).__name__}: {exc}"
            transcript.append(f"--- attempt {attempt} validation error ---\n{last_error}")
            prompt = _REPAIR_TEMPLATE.format(prior=raw[:4000], error=last_error[:2000])
            doc = None
            continue
        return AuthoredObjective(
            success=True, attempts=attempt, objective=doc, error=None, transcript=transcript)

    return AuthoredObjective(
        success=False, attempts=max_repair_attempts, objective=None,
        error=last_error, transcript=transcript)
