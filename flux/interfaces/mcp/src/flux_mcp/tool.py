"""`FluxTool` — the third surface docs/agent-surface.md promises ("one definition, three surfaces"):
every real `flux_chia_nodes` function `flows/chia_nodes/` ships — evaluate/search/calibrate/
conformance-check/validity-check, D9/D11's knowledge-lookup and read-only store access, D9/D12–
D17/D26/D27/D28's five agentic-search strategies, and D18's end-to-end reference DSE loop — now
also reachable as real MCP tools over HTTP, so an LLM agent (Claude Code's `--mcp-config`, or any
other MCP client) can call them without touching Python at all.

Modeled directly on CHIA's own working examples (`chia.base.tools.ChiaToolTemplate`,
`chia.base.tools.BashTool`): `setup()` registers methods via `self.mcp.add_tool(...)`, and
`ChiaTool.__init_subclass__` auto-generates `__init__` to bracket it with `ChiaTool.__init__`
(before) and `__post_init__` (after, which spins up the real Ray-actor-backed uvicorn server) —
no hand-written `__init__` needed here, same as every other real `ChiaTool` subclass upstream.

Wraps, not forks: every method here calls the matching `flux_chia_nodes` function directly as an
in-process Python call (not `.chia_remote(...)` — the MCP call itself is already the network hop;
`flux_search`'s own `parallel_screening` already dispatches the inner sweep over Ray, so nesting
another remote call here would just add latency, not new parallelism), then serializes the
result via `.to_dict()` before returning — MCP tool return values must be JSON-safe, and none of
`Result`/`ArchitectureDSEReport`/`ConformanceReport` is JSON-safe on its own (`Estimate` carries a
`Method` enum; `ArchitectureDSEReport.winner` is one of several candidate-generator-specific
dataclasses; `ConformanceReport` nests two `Result`s).
"""

from __future__ import annotations

from flux_llm import default_local_model
from typing import Any

from chia.base.tools.ChiaTool import ChiaTool
from flux_chia_nodes import (
    flux_agentic_architecture_search,
    flux_agentic_dse_loop,
    flux_agentic_joint_search,
    flux_agentic_mapping_search,
    flux_agentic_memory_search,
    flux_agentic_multi_axis_dse,
    flux_agentic_noc_search,
    flux_calibrate,
    flux_characterize_memory_level,
    flux_check_validity,
    flux_conformance_check,
    flux_evaluate,
    flux_compose_and_verify_rtl_design,
    flux_compose_and_verify_systemc_design,
    flux_find_results,
    flux_generate_architecture_candidate,
    flux_generate_rtl_for_architecture,
    flux_generate_sequential_rtl_for_architecture,
    flux_generate_gemm_rtl_for_architecture,
    flux_calibrate_against_generated_rtl,
    flux_backend_health,
    flux_check_ir_protocols,
    flux_check_protocol_conformance,
    flux_list_protocols,
    flux_protocol_lookup,
    flux_explain_candidate,
    flux_generate_rtl_module,
    flux_generate_systemc_module,
    flux_get_result,
    flux_knowledge_lookup,
    flux_leaderboard,
    flux_list_public_corpus,
    flux_rtl_generate_dse,
    flux_search,
    flux_sweep_dynamic_shape,
    flux_sweep_moe_routing,
    flux_synthesize_composite_rtl_design,
    flux_synthesize_with_asap7,
    flux_synthesize_with_asap7_redacted,
    flux_systemc_generate_dse,
)


class FluxTool(ChiaTool):
    """`FluxTool(name="flux")` starts a real MCP server at
    `http://{self.hostname}:{self.port}/{self.name}/mcp` exposing every `flux_chia_nodes`
    function as a tool — the authoritative, parity-guarded list is docs/agent-surface.md
    (`tests/unit/test_mcp_surface_parity.py` keeps it complete); counts and name lists in prose
    rotted twice (docs/decisions.md D95/D96) and are deliberately not repeated here.
    """

    def setup(self) -> None:
        self.mcp.add_tool(self.evaluate, name=f"{self.name}_evaluate")
        self.mcp.add_tool(self.search, name=f"{self.name}_search")
        self.mcp.add_tool(self.calibrate, name=f"{self.name}_calibrate")
        self.mcp.add_tool(self.conformance_check, name=f"{self.name}_conformance_check")
        self.mcp.add_tool(self.check_validity, name=f"{self.name}_check_validity")
        self.mcp.add_tool(self.knowledge_lookup, name=f"{self.name}_knowledge_lookup")
        self.mcp.add_tool(self.get_result, name=f"{self.name}_get_result")
        self.mcp.add_tool(self.find_results, name=f"{self.name}_find_results")
        self.mcp.add_tool(self.list_public_corpus, name=f"{self.name}_list_public_corpus")
        self.mcp.add_tool(self.agentic_mapping_search, name=f"{self.name}_agentic_mapping_search")
        self.mcp.add_tool(
            self.agentic_architecture_search, name=f"{self.name}_agentic_architecture_search"
        )
        self.mcp.add_tool(self.agentic_noc_search, name=f"{self.name}_agentic_noc_search")
        self.mcp.add_tool(self.agentic_memory_search, name=f"{self.name}_agentic_memory_search")
        self.mcp.add_tool(self.agentic_joint_search, name=f"{self.name}_agentic_joint_search")
        self.mcp.add_tool(self.agentic_dse_loop, name=f"{self.name}_agentic_dse_loop")
        self.mcp.add_tool(
            self.agentic_multi_axis_dse, name=f"{self.name}_agentic_multi_axis_dse"
        )
        self.mcp.add_tool(
            self.characterize_memory_level, name=f"{self.name}_characterize_memory_level"
        )
        self.mcp.add_tool(
            self.generate_systemc_module, name=f"{self.name}_generate_systemc_module"
        )
        self.mcp.add_tool(
            self.systemc_generate_dse, name=f"{self.name}_systemc_generate_dse"
        )
        self.mcp.add_tool(
            self.generate_rtl_module, name=f"{self.name}_generate_rtl_module"
        )
        self.mcp.add_tool(
            self.rtl_generate_dse, name=f"{self.name}_rtl_generate_dse"
        )
        self.mcp.add_tool(
            self.compose_and_verify_rtl_design, name=f"{self.name}_compose_and_verify_rtl_design"
        )
        self.mcp.add_tool(
            self.synthesize_composite_rtl_design, name=f"{self.name}_synthesize_composite_rtl_design"
        )
        self.mcp.add_tool(
            self.compose_and_verify_systemc_design, name=f"{self.name}_compose_and_verify_systemc_design"
        )
        self.mcp.add_tool(self.leaderboard, name=f"{self.name}_leaderboard")
        self.mcp.add_tool(self.sweep_dynamic_shape, name=f"{self.name}_sweep_dynamic_shape")
        self.mcp.add_tool(self.sweep_moe_routing, name=f"{self.name}_sweep_moe_routing")
        self.mcp.add_tool(
            self.generate_architecture_candidate, name=f"{self.name}_generate_architecture_candidate"
        )
        self.mcp.add_tool(self.synthesize_with_asap7, name=f"{self.name}_synthesize_with_asap7")
        self.mcp.add_tool(
            self.synthesize_with_asap7_redacted, name=f"{self.name}_synthesize_with_asap7_redacted"
        )
        self.mcp.add_tool(
            self.generate_rtl_for_architecture, name=f"{self.name}_generate_rtl_for_architecture"
        )
        self.mcp.add_tool(
            self.generate_sequential_rtl_for_architecture,
            name=f"{self.name}_generate_sequential_rtl_for_architecture",
        )
        self.mcp.add_tool(
            self.generate_gemm_rtl_for_architecture,
            name=f"{self.name}_generate_gemm_rtl_for_architecture",
        )
        self.mcp.add_tool(
            self.calibrate_against_generated_rtl,
            name=f"{self.name}_calibrate_against_generated_rtl",
        )
        self.mcp.add_tool(self.backend_health, name=f"{self.name}_backend_health")
        self.mcp.add_tool(self.explain_candidate, name=f"{self.name}_explain_candidate")
        self.mcp.add_tool(self.protocol_lookup, name=f"{self.name}_protocol_lookup")
        self.mcp.add_tool(self.list_protocols, name=f"{self.name}_list_protocols")
        self.mcp.add_tool(self.check_ir_protocols, name=f"{self.name}_check_ir_protocols")
        self.mcp.add_tool(self.check_protocol_conformance, name=f"{self.name}_check_protocol_conformance")
        self.mcp.add_tool(self.author_objective, name=f"{self.name}_author_objective")
        self.mcp.add_tool(self.author_design_spec, name=f"{self.name}_author_design_spec")
        self.mcp.add_tool(self.mine_knowledge, name=f"{self.name}_mine_knowledge")
        self.mcp.add_tool(self.recall_facts, name=f"{self.name}_recall_facts")
        self.mcp.add_tool(self.ip_catalog, name=f"{self.name}_ip_catalog")
        self.mcp.add_tool(self.check_prose_faithfulness, name=f"{self.name}_check_prose_faithfulness")
        self.mcp.add_tool(self.campaign_start, name=f"{self.name}_campaign_start")
        self.mcp.add_tool(self.campaign_step, name=f"{self.name}_campaign_step")
        self.mcp.add_tool(self.campaign_status, name=f"{self.name}_campaign_status")
        self.mcp.add_tool(self.campaign_resume, name=f"{self.name}_campaign_resume")
        self.mcp.add_tool(self.campaign_stop, name=f"{self.name}_campaign_stop")
        self.mcp.add_tool(self.campaign_frontier, name=f"{self.name}_campaign_frontier")

    def evaluate(
        self,
        backend: str,
        workload: dict[str, Any],
        arch: dict[str, Any] | None = None,
        mapping: dict[str, Any] | None = None,
        metrics: list[str] | None = None,
        wall_clock_s: float | None = None,
        usd: float | None = None,
        result_db_path: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate a candidate accelerator design through a named Flux evaluator backend.

        Returns latency/energy/area estimates with confidence intervals, an independently
        computed validity check, a structured bottleneck explanation, and full provenance
        (docs/evaluator-abi.md) — not a bare number.

        Args:
            backend: Evaluator backend registry name (e.g. "zigzag" analytic, "rtl"
                Verilator simulation, "openroad" placed silicon PPA). The live, complete list
                with per-backend usability comes from flux_backend_health — a hand-list here
                would rot, and did (docs/decisions.md D119's lesson, applied).
            workload: Flux Workload IR document (inline dict).
            arch: Flux Architecture IR document, or omit to use the backend's own reference
                architecture.
            mapping: Flux Mapping IR document, or omit to let the evaluator choose one.
            metrics: Metric names to compute, e.g. ["latency_cycles", "energy_pj"], or omit for
                the backend's default set.
            wall_clock_s: Optional wall-clock budget in seconds.
            usd: Optional dollar-cost budget.
            result_db_path: SQLite file to warm-start against (docs/decisions.md D19) — pass the
                same path across calls and an identical (workload, arch, mapping) triple is
                served from the store instead of a real evaluator call. Omit for the original
                always-real-evaluation behavior.
        """
        result = flux_evaluate(
            backend, workload, arch, mapping, metrics, wall_clock_s, usd,
            result_db_path=result_db_path,
        )
        return result.to_dict()

    def search(
        self,
        workload: dict[str, Any],
        base_arch: dict[str, Any],
        screening_backend: str,
        search_kind: str = "architecture_width",
        tile_sizes: list[int] | None = None,
        escalate_contenders: bool = False,
        widths: list[int] | None = None,
        noc_topology_variants: list[tuple[str, list[int]]] | None = None,
        memory_level: str | None = None,
        memory_sizes_kb: list[float] | None = None,
        metric: str = "latency_cycles",
        minimize: bool = True,
        escalation_backends: list[str] | None = None,
        parallel_screening: bool = True,
        result_db_path: str | None = None,
        wall_clock_budget_s: float | None = None,
    ) -> dict[str, Any]:
        """Run architecture design-space exploration: sweep a fixed workload across candidate
        architectures, rank by a fast screening evaluator, then confirm the winner through
        slower escalation rungs.

        Args:
            workload: Flux Workload IR document (inline dict), held fixed across the sweep.
            base_arch: Flux Architecture IR document the candidate generator varies.
            screening_backend: Fast evaluator backend ranking every candidate, e.g. "zigzag" for
                search_kind="architecture_width"/"memory_size"/"joint" or "booksim" for
                "noc_topology".
            search_kind: "architecture_width" (sweep compute array width — requires `widths`),
                "noc_topology" (sweep NoC topology/dimensionality — requires
                `noc_topology_variants`), "memory_size" (sweep one named memory level's capacity
                — requires `memory_level` and `memory_sizes_kb`; docs/decisions.md D26), or
                "joint" (sweep compute width and memory size together, the full Cartesian product
                — requires `widths`, `memory_level`, and `memory_sizes_kb`), or "fusion_tile"
                (the one *mapping*-space axis: sweep a layer-fusion tile size over a multi-op
                chain through real Stream — optional `tile_sizes`, defaulting to the complete
                feasible space; docs/decisions.md D104).
            widths: Compute array widths to try, e.g. [4, 8, 16]. Required for
                search_kind="architecture_width" or "joint".
            noc_topology_variants: [topology, dimensions] pairs to try, e.g.
                [["mesh", [8, 8]], ["mesh", [4, 4, 4]]]. Required when
                search_kind="noc_topology".
            memory_level: Name of the memory-class hierarchy level to vary, e.g. "gbuf". Required
                for search_kind="memory_size" or "joint".
            escalate_contenders: escalate every candidate the screening data cannot rule out
                (its CI overlaps the leader's), not just the best point estimate, and re-pick the
                winner from those higher-fidelity results — the Pareto-front-relevance escalation
                trigger (docs/decisions.md D105). Costs one escalation call per contender per
                rung; `wall_clock_budget_s` bounds it.
            tile_sizes: Layer-fusion tile sizes to try for search_kind="fusion_tile", e.g.
                [1, 2, 4, 8]. Omit to sweep every divisor of the fused chain's shared row dim
                (the complete feasible space — Stream has no ragged-final-tile support).
            memory_sizes_kb: Memory sizes (KiB) to try, e.g. [1.25, 2, 4, 64, 512] — note a size
                too small for the workload's working set to fit is a real, expected outcome
                (recorded as a per-candidate failure, not a crash), not a caller error. Required
                for search_kind="memory_size" or "joint".
            metric: Metric name to rank candidates by.
            minimize: Whether a lower metric value is better.
            escalation_backends: Backend names to confirm the winner against, in order, e.g.
                ["systemc", "rtl"].
            parallel_screening: Screen candidates as concurrent Ray tasks (default) instead of
                sequentially in-process.
            result_db_path: SQLite file to warm-start against (docs/decisions.md D19), for both
                screening and escalation — pass the same path across calls and a repeated sweep
                (or repeated winner) skips candidates it's already scored. Composes with
                parallel_screening=True unchanged. Omit for the original always-real-evaluation
                behavior.
            wall_clock_budget_s: Real, enforced wall-clock budget (docs/decisions.md D71) for the
                *escalation* cascade only — checked before each rung's own evaluator call.
                Screening's own batched/parallel dispatch isn't interruptible the same way.
                `stopped_early` in the returned report is True when the budget cut escalation
                short.
        """
        report = flux_search(
            workload, base_arch, screening_backend,
            search_kind=search_kind, widths=widths, noc_topology_variants=noc_topology_variants,
            memory_level=memory_level, memory_sizes_kb=memory_sizes_kb,
            metric=metric, minimize=minimize, escalation_backends=escalation_backends,
            parallel_screening=parallel_screening, result_db_path=result_db_path,
            wall_clock_budget_s=wall_clock_budget_s,
            tile_sizes=tile_sizes, escalate_contenders=escalate_contenders,
        )
        return report.to_dict()

    def calibrate(
        self,
        backend: str,
        workload: dict[str, Any],
        arch: dict[str, Any] | None = None,
        mapping: dict[str, Any] | None = None,
        metrics: list[str] | None = None,
        calibration_db_path: str = "flux_calibration.db",
        max_relative_ci_width: float = 0.5,
        escalate_if_recommended: bool = False,
        reference_backend: str = "rtl",
    ) -> dict[str, Any]:
        """Evaluate a candidate through a named backend, then widen its confidence intervals
        from real calibration residual data and recompute its escalation recommendation
        (docs/calibration.md: "a result without a calibration id and a confidence interval is a bug").

        Args:
            backend: Evaluator backend registry name (e.g. "zigzag" analytic, "rtl"
                Verilator simulation, "openroad" placed silicon PPA). The live, complete list
                with per-backend usability comes from flux_backend_health — a hand-list here
                would rot, and did (docs/decisions.md D119's lesson, applied).
            workload: Flux Workload IR document (inline dict).
            arch: Flux Architecture IR document, or omit to use the backend's own reference
                architecture.
            mapping: Flux Mapping IR document, or omit to let the evaluator choose one.
            metrics: Metric names to compute, or omit for the backend's default set.
            calibration_db_path: SQLite file of prior (predicted, reference) residual records,
                created if missing. Pass the same path across calls so records accumulate.
            max_relative_ci_width: Escalation trigger — a metric whose CI exceeds this fraction
                of its point value is flagged for escalation to a higher-fidelity rung.
            escalate_if_recommended: act on the escalation advisory (docs/decisions.md D99):
                when the policy recommends it, buy one real `reference_backend` measurement,
                record its residual (the D98 flywheel), and re-calibrate before returning.
                Skipped when no escalation is recommended, when this exact candidate already
                has a calibration record, or when the reference can't express the candidate.
            reference_backend: which higher-fidelity rung to buy the measurement from, e.g.
                "rtl" (real Verilator simulation).
        """
        result = flux_calibrate(
            backend, workload, arch, mapping, metrics,
            calibration_db_path=calibration_db_path, max_relative_ci_width=max_relative_ci_width,
            escalate_if_recommended=escalate_if_recommended, reference_backend=reference_backend,
        )
        return result.to_dict()

    def conformance_check(
        self,
        workload: dict[str, Any],
        arch: dict[str, Any] | None = None,
        mapping: dict[str, Any] | None = None,
        metrics: list[str] | None = None,
        declared_backend: str = "zigzag",
        reference_backend: str = "rtl",
        calibration_db_path: str = "flux_calibration.db",
        record_residuals: bool = False,
    ) -> dict[str, Any]:
        """Check whether a fast/analytic "declared" model's *calibrated* confidence interval for
        a candidate actually contains a slower, more-trusted "reference" evaluator's measurement
        — docs/roadmap.md's exit criterion made checkable: a candidate "passes RTL conformance against
        its declared model within the calibrated uncertainty band."

        Args:
            workload: Flux Workload IR document (inline dict).
            arch: Flux Architecture IR document, or omit to use each backend's own reference
                architecture.
            mapping: Flux Mapping IR document, or omit to let each evaluator choose one.
            metrics: Metric names to compare, or omit for the declared backend's default set.
            declared_backend: Fast/analytic evaluator backend being checked, e.g. "zigzag".
            reference_backend: Slower, more-trusted evaluator serving as ground truth, e.g.
                "rtl" (real Verilator simulation).
            calibration_db_path: SQLite file of prior residual records used to calibrate the
                declared backend's confidence interval before comparing.
            record_residuals: also record this check's own real (predicted, reference) pairs
                back into `calibration_db_path` — the calibration flywheel (docs/decisions.md
                D98): each conformance run then improves future calibrated CIs. Idempotent per
                exact (workload, arch) pair; opt-in because it makes the next identical call
                return a better-informed (different) CI.
        """
        report = flux_conformance_check(
            workload, arch, mapping, metrics,
            declared_backend=declared_backend, reference_backend=reference_backend,
            calibration_db_path=calibration_db_path, record_residuals=record_residuals,
        )
        return report.to_dict()

    def check_validity(
        self,
        backend: str,
        workload: dict[str, Any],
        arch: dict[str, Any] | None = None,
        mapping: dict[str, Any] | None = None,
        metrics: list[str] | None = None,
    ) -> dict[str, Any]:
        """Evaluate a candidate, then overlay an *independently*-computed validity check onto the
        result (docs/gap-analysis.md G14) — merged with, not replacing, the evaluator's own self-report.
        `ok=True` on the returned result means both the evaluator's own check and a check that
        shares no code with any evaluator (declared Architecture-IR constraints, e.g. `area_mm2`/
        `tdp_w` max bounds; a first-principles compute-bound latency lower-bound) found nothing
        wrong — not just that the evaluator says so.

        Args:
            backend: Evaluator backend registry name (e.g. "zigzag" analytic, "rtl"
                Verilator simulation, "openroad" placed silicon PPA). The live, complete list
                with per-backend usability comes from flux_backend_health — a hand-list here
                would rot, and did (docs/decisions.md D119's lesson, applied).
            workload: Flux Workload IR document (inline dict).
            arch: Flux Architecture IR document, or omit to use the backend's own reference
                architecture. The independent checks need an inline document to check anything
                architecture-specific (declared constraints, the roofline lane count) — omitting
                `arch` still evaluates, but those checks report themselves as not applicable
                rather than silently passing.
            mapping: Flux Mapping IR document, or omit to let the evaluator choose one.
            metrics: Metric names to compute, or omit for the backend's default set.
        """
        result = flux_check_validity(backend, workload, arch, mapping, metrics)
        return result.to_dict()

    def knowledge_lookup(
        self, query: str, standard_id: str | None = None, k: int = 5
    ) -> list[dict[str, Any]]:
        """Retrieve the top-`k` chunks of ingested spec/standard text matching `query`.

        Args:
            query: Free-text search query.
            standard_id: Restrict results to one standard, e.g. "riscv-unpriv", or omit to
                search every ingested standard.
            k: Maximum number of chunks to return.
        """
        return flux_knowledge_lookup(query, standard_id, k)

    def get_result(self, db_path: str, result_id: int) -> dict[str, Any] | None:
        """Fetch one previously stored evaluator `Result` by row id, with its full lineage
        (workload/architecture/mapping content hashes, evaluator name) — `None` if no such id
        exists in the store.

        Args:
            db_path: Path to a `flux_store.ResultStore` SQLite file.
            result_id: Row id returned when the result was originally stored.
        """
        return flux_get_result(db_path, result_id)

    def find_results(
        self,
        db_path: str,
        workload_hash: str | None = None,
        arch_hash: str | None = None,
        evaluator: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query previously stored results by any combination of lineage fields — omitting all
        three returns every stored result. Use this to check whether a candidate has already
        been evaluated before spending budget on it again.

        Args:
            db_path: Path to a `flux_store.ResultStore` SQLite file.
            workload_hash: Restrict to results for this workload content hash, or omit.
            arch_hash: Restrict to results for this architecture content hash, or omit.
            evaluator: Restrict to results from this evaluator (e.g. "zigzag"), or omit.
        """
        return flux_find_results(db_path, workload_hash, arch_hash, evaluator)

    def list_public_corpus(self, corpus_root: str) -> list[dict[str, Any]]:
        """List every **public** benchmark corpus entry under `corpus_root` — a (workload,
        architecture) pair plus a description of why it's a useful benchmark point. Never
        includes the holdout partition: there is no argument on this tool that can ask for it,
        by design — the holdout partition exists specifically so no agent or search strategy can
        see it (docs/roadmap.md's validation methodology).

        Args:
            corpus_root: Path to a corpus directory containing `public/` and `holdout/`
                subdirectories of entry manifests (see `corpus/README.md`).
        """
        return flux_list_public_corpus(corpus_root)

    def agentic_mapping_search(
        self,
        workload: dict[str, Any],
        arch: dict[str, Any],
        backend: str,
        for_op: str,
        metric: str = "latency_cycles",
        minimize: bool = True,
        max_iterations: int = 12,
        seed: int = 0,
        llm_model: str = default_local_model(),
        wall_clock_budget_s: float | None = None,
    ) -> dict[str, Any]:
        """Search the flat-mapping space (spatial split × temporal loop order) for one op, using
        a real local LLM (Ollama, no API credentials) to propose one candidate per round instead
        of enumerating or randomly perturbing them.

        Args:
            workload: Flux Workload IR document (inline dict), held fixed across the search.
            arch: Flux Architecture IR document (single spatial dim, single compute node).
            backend: Evaluator backend name evaluating each proposed mapping, e.g. "zigzag".
            for_op: The `id` of the single `einsum` op in `workload` to search a mapping for.
            metric: Metric name to minimize (or maximize, see `minimize`).
            minimize: Whether a lower metric value is better.
            max_iterations: Number of LLM-proposed candidates to try.
            seed: Random seed for the fallback-to-unvisited mechanism (deterministic).
            llm_model: Ollama model tag to use, e.g. "qwen2.5-coder:7b" (must already be pulled
                on the local Ollama server).
            wall_clock_budget_s: Real, enforced wall-clock budget (docs/decisions.md D73),
                checked before every real LLM-proposal-plus-evaluation round.
        """
        report = flux_agentic_mapping_search(
            workload, arch, backend, for_op=for_op, metric=metric, minimize=minimize,
            max_iterations=max_iterations, seed=seed, llm_model=llm_model,
            wall_clock_budget_s=wall_clock_budget_s,
        )
        return report.to_dict()

    def agentic_architecture_search(
        self,
        workload: dict[str, Any],
        base_arch: dict[str, Any],
        backend: str,
        valid_widths: list[int],
        metric: str = "latency_cycles",
        minimize: bool = True,
        max_iterations: int | None = None,
        seed: int = 0,
        llm_model: str = default_local_model(),
        wall_clock_budget_s: float | None = None,
    ) -> dict[str, Any]:
        """Search a compute array's spatial width, using a real local LLM (Ollama, no API
        credentials) to propose one width per round from a caller-supplied candidate set.

        Args:
            workload: Flux Workload IR document (inline dict), held fixed across the search.
            base_arch: Flux Architecture IR document the candidate widths are applied to.
            backend: Evaluator backend name evaluating each proposed width, e.g. "zigzag".
            valid_widths: Candidate widths to choose from, e.g. [4, 8, 16, 32].
            metric: Metric name to minimize (or maximize, see `minimize`).
            minimize: Whether a lower metric value is better.
            max_iterations: Number of LLM-proposed candidates to try, or omit to use the full
                size of `valid_widths` (guarantees every candidate gets tried).
            seed: Random seed for the fallback-to-unvisited mechanism (deterministic).
            llm_model: Ollama model tag to use (must already be pulled on the local server).
            wall_clock_budget_s: Real, enforced wall-clock budget (docs/decisions.md D73),
                checked before every real LLM-proposal-plus-evaluation round.
        """
        report = flux_agentic_architecture_search(
            workload, base_arch, backend, valid_widths=valid_widths, metric=metric,
            minimize=minimize, max_iterations=max_iterations, seed=seed, llm_model=llm_model,
            wall_clock_budget_s=wall_clock_budget_s,
        )
        return report.to_dict()

    def agentic_noc_search(
        self,
        workload: dict[str, Any],
        base_arch: dict[str, Any],
        backend: str,
        valid_variants: list[tuple[str, list[int]]],
        metric: str = "latency_cycles",
        minimize: bool = True,
        max_iterations: int | None = None,
        seed: int = 0,
        llm_model: str = default_local_model(),
        wall_clock_budget_s: float | None = None,
    ) -> dict[str, Any]:
        """Search a NoC's topology/dimensionality, using a real local LLM (Ollama, no API
        credentials) to propose one (topology, dimensions) variant per round from a
        caller-supplied candidate set.

        Args:
            workload: Flux Workload IR document (inline dict), held fixed across the search.
            base_arch: Flux Architecture IR document with an `interconnect.noc` block the
                candidate variants are applied to.
            backend: Evaluator backend name evaluating each proposed variant, e.g. "booksim".
            valid_variants: [topology, dimensions] pairs to choose from, e.g.
                [["mesh", [8, 8]], ["mesh", [4, 4, 4]], ["torus", [4, 4, 4]]].
            metric: Metric name to minimize (or maximize, see `minimize`).
            minimize: Whether a lower metric value is better.
            max_iterations: Number of LLM-proposed candidates to try, or omit to use the full
                size of `valid_variants` (guarantees every candidate gets tried).
            seed: Random seed for the fallback-to-unvisited mechanism (deterministic).
            llm_model: Ollama model tag to use (must already be pulled on the local server).
            wall_clock_budget_s: Real, enforced wall-clock budget (docs/decisions.md D73),
                checked before every real LLM-proposal-plus-evaluation round.
        """
        report = flux_agentic_noc_search(
            workload, base_arch, backend, valid_variants=valid_variants, metric=metric,
            minimize=minimize, max_iterations=max_iterations, seed=seed, llm_model=llm_model,
            wall_clock_budget_s=wall_clock_budget_s,
        )
        return report.to_dict()

    def agentic_memory_search(
        self,
        workload: dict[str, Any],
        base_arch: dict[str, Any],
        backend: str,
        level: str,
        valid_sizes_kb: list[float],
        metric: str = "energy_pj",
        minimize: bool = True,
        max_iterations: int | None = None,
        seed: int = 0,
        llm_model: str = default_local_model(),
        wall_clock_budget_s: float | None = None,
    ) -> dict[str, Any]:
        """Search a memory-hierarchy level's capacity, using a real local LLM (Ollama, no API
        credentials) to propose one size per round from a caller-supplied candidate set.

        Args:
            workload: Flux Workload IR document (inline dict), held fixed across the search.
            base_arch: Flux Architecture IR document with the named memory-class hierarchy level
                the candidate sizes are applied to.
            backend: Evaluator backend name evaluating each proposed size, e.g. "zigzag".
            level: Name of the memory-class hierarchy level to vary, e.g. "gbuf".
            valid_sizes_kb: Sizes (KiB) to choose from, e.g. [1.25, 2, 4, 64, 512] — note a size
                too small for the workload's working set to fit is a real, expected outcome
                (recorded as a rejected move, not a caller error).
            metric: Metric name to minimize (or maximize, see `minimize`) — defaults to
                "energy_pj", not "latency_cycles": docs/decisions.md D26 found latency is flat
                across feasible sizes for this axis, while energy is where the real signal is.
            minimize: Whether a lower metric value is better.
            max_iterations: Number of LLM-proposed candidates to try, or omit to use the full
                size of `valid_sizes_kb` (guarantees every candidate gets tried).
            seed: Random seed for the fallback-to-unvisited mechanism (deterministic).
            llm_model: Ollama model tag to use (must already be pulled on the local server).
            wall_clock_budget_s: Real, enforced wall-clock budget (docs/decisions.md D73),
                checked before every real LLM-proposal-plus-evaluation round.
        """
        report = flux_agentic_memory_search(
            workload, base_arch, backend, level=level, valid_sizes_kb=valid_sizes_kb,
            metric=metric, minimize=minimize, max_iterations=max_iterations, seed=seed,
            llm_model=llm_model, wall_clock_budget_s=wall_clock_budget_s,
        )
        return report.to_dict()

    def agentic_joint_search(
        self,
        workload: dict[str, Any],
        base_arch: dict[str, Any],
        backend: str,
        level: str,
        valid_widths: list[int],
        valid_sizes_kb: list[float],
        metric: str = "energy_pj",
        minimize: bool = True,
        max_iterations: int | None = None,
        seed: int = 0,
        llm_model: str = default_local_model(),
        wall_clock_budget_s: float | None = None,
    ) -> dict[str, Any]:
        """Jointly search a compute array's width AND a memory-hierarchy level's capacity
        together, using a real local LLM (Ollama, no API credentials) to propose one
        (width, size_kb) pair per round from the caller-supplied candidate grid — the first
        agentic axis over a genuinely two-dimensional candidate space.

        Args:
            workload: Flux Workload IR document (inline dict), held fixed across the search.
            base_arch: Flux Architecture IR document with the named memory-class hierarchy level
                and compute-array dim the candidate pairs are applied to.
            backend: Evaluator backend name evaluating each proposed pair, e.g. "zigzag".
            level: Name of the memory-class hierarchy level to vary, e.g. "gbuf".
            valid_widths: Compute array widths to choose from, e.g. [4, 8, 16, 32]. Every
                (width, size_kb) combination of `valid_widths` x `valid_sizes_kb` is a valid
                candidate.
            valid_sizes_kb: Sizes (KiB) to choose from, e.g. [1.25, 2, 4, 64, 512] — note a size
                too small for the workload's working set to fit at a given width is a real,
                expected outcome (recorded as a rejected move, not a caller error).
            metric: Metric name to minimize (or maximize, see `minimize`) — defaults to
                "energy_pj", same reasoning as `agentic_memory_search`.
            minimize: Whether a lower metric value is better.
            max_iterations: Number of LLM-proposed candidates to try, or omit to use the full
                `len(valid_widths) * len(valid_sizes_kb)` grid (guarantees every pair gets
                tried).
            seed: Random seed for the fallback-to-unvisited mechanism (deterministic).
            llm_model: Ollama model tag to use (must already be pulled on the local server).
            wall_clock_budget_s: Real, enforced wall-clock budget (docs/decisions.md D73),
                checked before every real LLM-proposal-plus-evaluation round.
        """
        report = flux_agentic_joint_search(
            workload, base_arch, backend, level=level, valid_widths=valid_widths,
            valid_sizes_kb=valid_sizes_kb, metric=metric, minimize=minimize,
            max_iterations=max_iterations, seed=seed, llm_model=llm_model,
            wall_clock_budget_s=wall_clock_budget_s,
        )
        return report.to_dict()

    def agentic_dse_loop(
        self,
        workload: dict[str, Any],
        base_arch: dict[str, Any],
        screening_backend: str,
        axis: str = "architecture_width",
        reference_backend: str = "rtl",
        metric: str = "latency_cycles",
        minimize: bool = True,
        max_iterations: int | None = None,
        seed: int = 0,
        calibration_db_path: str = "flux_dse_loop_calibration.db",
        result_db_path: str = "flux_dse_loop_results.db",
        llm_model: str = default_local_model(),
        valid_widths: list[int] | None = None,
        baseline_width: int | None = None,
        for_op: str | None = None,
        baseline_mapping_index: int = 0,
        valid_variants: list[tuple[str, list[int]]] | None = None,
        baseline_variant_index: int = 0,
        memory_level: str | None = None,
        valid_sizes_kb: list[float] | None = None,
        baseline_size_index: int = 0,
        baseline_pair_index: int = 0,
    ) -> dict[str, Any]:
        """Run the full reference agentic DSE loop docs/roadmap.md Phase 4 names as its exit
        criterion, as one tool call: LLM-driven search over one axis, an independent validity
        check on the winner, a formal conformance check within a calibrated confidence interval,
        storing the winner and proving it replays deterministically, and a real cost report — not
        four separate tool calls an agent has to sequence by hand.

        Args:
            workload: Flux Workload IR document (inline dict), held fixed across the search.
            base_arch: Flux Architecture IR document — the one whose width/topology/memory-size
                varies for axis="architecture_width"/"noc_topology"/"memory_size"/"joint", or the
                fixed architecture the mapping search runs against for axis="mapping".
            screening_backend: Fast evaluator backend the LLM-driven search screens with, e.g.
                "zigzag" (architecture_width/mapping/memory_size/joint) or "booksim"
                (noc_topology).
            axis: "architecture_width" (default, requires valid_widths/baseline_width),
                "mapping" (requires for_op; baseline_mapping_index optional), "noc_topology"
                (requires valid_variants; baseline_variant_index optional), "memory_size"
                (requires memory_level/valid_sizes_kb; baseline_size_index optional), or "joint"
                (requires valid_widths/memory_level/valid_sizes_kb; baseline_pair_index
                optional) — the width x memory-size Cartesian product, the first axis over a
                genuinely two-dimensional candidate space.
            reference_backend: Slower, more-trusted evaluator the winner's conformance is
                checked against, e.g. "rtl" (real Verilator simulation) for
                axis="architecture_width", "timeloop" for axis="mapping"/"memory_size"/"joint",
                "noxim" for axis="noc_topology" (docs/decisions.md D32) — a second, independent
                NoC simulator, real but narrower than Booksim2's own coverage: Noxim has no
                torus network at all, so a torus/3D/6D noc_topology winner still honestly reports
                `conformance=None` with a `conformance_error`, same as every axis/backend
                mismatch; only a 2D-mesh winner gets a real check against it.
            metric: Metric name to minimize (or maximize, see `minimize`) — pass "energy_pj" for
                axis="memory_size"/"joint" (docs/decisions.md D26: latency is flat once a
                candidate is feasible, energy is where the signal is).
            minimize: Whether a lower metric value is better.
            max_iterations: Number of LLM-proposed candidates to try, or omit to use the full
                candidate-space size for the chosen axis.
            seed: Random seed for the fallback-to-unvisited mechanism (deterministic).
            calibration_db_path: SQLite file of prior residual records used to calibrate the
                conformance check. An empty/missing file honestly yields `conformance.ok=False`
                (an uncalibrated point estimate has a degenerate confidence interval) rather than
                a fabricated pass.
            result_db_path: SQLite file the winner's result is stored into before the
                deterministic-replay check re-evaluates it fresh and diffs every metric.
            llm_model: Ollama model tag to use (must already be pulled on the local server).
            valid_widths: Candidate widths to choose from, e.g. [4, 8, 16, 32]. Required for
                axis="architecture_width" or "joint".
            baseline_width: A human-plausible width (e.g. the one already in `base_arch`) the
                winner is compared against. Required when axis="architecture_width".
            for_op: The `id` of the einsum op in `workload` to search a mapping for. Required
                when axis="mapping".
            baseline_mapping_index: Which deterministically-ordered flat-mapping candidate stands
                in for "a mapping a human might pick without searching," when axis="mapping".
            valid_variants: [topology, dimensions] pairs to choose from, e.g. [["mesh", [8, 8]],
                ["torus", [4, 4, 4]]]. Required when axis="noc_topology".
            baseline_variant_index: Which deterministically-ordered NoC-topology candidate stands
                in for a human-picked baseline, when axis="noc_topology".
            memory_level: Name of the memory-class hierarchy level to vary, e.g. "gbuf". Required
                for axis="memory_size" or "joint".
            valid_sizes_kb: Sizes (KiB) to choose from, e.g. [1.25, 2, 4, 64, 512] — note a size
                too small for the workload's working set to fit is a real, expected outcome
                (the baseline pick falls through to the next candidate, not a caller error).
                Required for axis="memory_size" or "joint".
            baseline_size_index: Which deterministically-ordered memory-size candidate stands in
                for a human-picked baseline, when axis="memory_size".
            baseline_pair_index: Which deterministically-ordered (width, size_kb) candidate — over
                the full `valid_widths` x `valid_sizes_kb` grid — stands in for a human-picked
                baseline, when axis="joint".
        """
        report = flux_agentic_dse_loop(
            workload, base_arch, screening_backend,
            axis=axis, reference_backend=reference_backend, metric=metric, minimize=minimize,
            max_iterations=max_iterations, seed=seed,
            calibration_db_path=calibration_db_path, result_db_path=result_db_path,
            llm_model=llm_model, valid_widths=valid_widths, baseline_width=baseline_width,
            for_op=for_op, baseline_mapping_index=baseline_mapping_index,
            valid_variants=valid_variants, baseline_variant_index=baseline_variant_index,
            memory_level=memory_level, valid_sizes_kb=valid_sizes_kb,
            baseline_size_index=baseline_size_index, baseline_pair_index=baseline_pair_index,
        )
        return report.to_dict()

    def agentic_multi_axis_dse(
        self,
        workload: dict[str, Any],
        compute_memory_arch: dict[str, Any],
        noc_arch: dict[str, Any],
        compute_memory_backend: str,
        noc_backend: str,
        valid_widths: list[int],
        memory_level: str,
        valid_sizes_kb: list[float],
        valid_noc_variants: list[tuple[str, list[int]]],
        composite_metric: str = "energy_pj",
        max_iterations: int | None = None,
        seed: int = 0,
        llm_model: str = default_local_model(),
    ) -> dict[str, Any]:
        """Run three independent agentic axis searches (architecture_width, memory_size,
        noc_topology) as real, concurrent Ray tasks (docs/decisions.md D34) — not `agentic_dse_
        loop`'s one-axis-at-a-time shape. The MCP call itself stays in-process (same reasoning
        every other method here uses); the concurrency happens *inside* this call, across the
        three sub-searches.

        `architecture_width` and `memory_size` share an evaluator family and both vary
        `compute_memory_arch`, so their two independent winners are also composed into one
        candidate and evaluated for real — checking whether two *blindly* (each search never
        sees the other's result) optimized axes land on the same point a *coordinated* joint
        search would (docs/decisions.md D26/D28). `noc_topology` varies a separate `noc_arch`
        (no existing architecture example here has both a compute node and a real NoC block) and
        is reported alongside, not merged into the same composite — no evaluator in this repo
        spans both.

        Args:
            workload: Flux Workload IR document (inline dict), held fixed across all three
                searches.
            compute_memory_arch: Architecture IR with one compute node and a `memory_level`-named
                memory node, e.g. `simple-npu-1d-v1.yaml` — varied by the width and memory-size
                searches.
            noc_arch: Architecture IR with a real `interconnect.noc` block, e.g.
                `noc-mesh-2d-v1.yaml` — varied by the NoC-topology search.
            compute_memory_backend: Evaluator backend for the width/memory-size searches and the
                composite candidate, e.g. "zigzag".
            noc_backend: Evaluator backend for the NoC-topology search, e.g. "booksim".
            valid_widths: Compute array widths to choose from, e.g. [4, 32].
            memory_level: Name of the memory-class hierarchy level to vary, e.g. "gbuf".
            valid_sizes_kb: Sizes (KiB) to choose from, e.g. [1.0, 1.25, 64.0].
            valid_noc_variants: [topology, dimensions] pairs to choose from, e.g.
                [["mesh", [8, 8]], ["torus", [4, 4, 4]]].
            composite_metric: Metric the composed width+memory-size candidate is evaluated on —
                defaults to "energy_pj" (same reasoning as `agentic_memory_search`), enabling a
                direct comparison against a known joint-search optimum on the same metric.
            max_iterations: Number of LLM-proposed candidates to try per axis, or omit to use
                each axis's own full candidate-space size.
            seed: Random seed for the fallback-to-unvisited mechanism (deterministic), shared
                across all three searches.
            llm_model: Ollama model tag to use (must already be pulled on the local server).
        """
        report = flux_agentic_multi_axis_dse(
            workload, compute_memory_arch, noc_arch, compute_memory_backend, noc_backend,
            valid_widths=valid_widths, memory_level=memory_level, valid_sizes_kb=valid_sizes_kb,
            valid_noc_variants=valid_noc_variants, composite_metric=composite_metric,
            max_iterations=max_iterations, seed=seed, llm_model=llm_model,
        )
        return report.to_dict()

    def characterize_memory_level(
        self,
        arch: dict[str, Any],
        level: str,
        word_width_bits: int,
        backend: str = "cacti",
        metrics: list[str] | None = None,
    ) -> dict[str, Any]:
        """Extract one named memory-hierarchy level from a (possibly multi-level) architecture
        and characterize it as a standalone physical macro (docs/decisions.md D37) — the glue
        between this repo's real, multi-level Architecture IR documents and `evaluators/cacti`'s
        single-macro contract (D36): every real architecture example here has multiple
        `class=="memory"` nodes, but CACTI characterizes exactly one macro at a time.

        Not a conformance check against any other evaluator: CACTI's `energy_pj` is one memory
        *access*'s energy; ZigZag's/Timeloop's `energy_pj` is a whole *workload*'s energy — same
        name, different quantities. This tool reports CACTI's own real, standalone
        characterization, not a comparison.

        Args:
            arch: Flux Architecture IR document (inline dict) containing the level to
                characterize, e.g. the winner of an `agentic_memory_search`/`agentic_dse_loop`
                run with `axis="memory_size"`.
            level: Name of the `class=="memory"` hierarchy level to extract, e.g. "gbuf".
            word_width_bits: Bits per word for this macro — a real physical property `size_kb`
                alone can't determine (a 512 KiB macro could be 4096x128b or 32768x16b); none of
                this repo's real examples carry it yet, so it's always explicit here.
            backend: Evaluator backend to characterize the extracted macro with — defaults to
                "cacti", the only backend built for this v0.1.
            metrics: Metric names to request, or omit for every metric `backend`'s adapter
                reports for this macro (not the generic `latency_cycles`/`energy_pj` baseline
                `evaluate`/`check_validity` default to — CACTI doesn't report `latency_cycles` at
                all, and its two most interesting numbers, `area_mm2`/`power_w`, aren't in that
                baseline).
        """
        result = flux_characterize_memory_level(
            arch, level, word_width_bits, backend=backend, metrics=metrics,
        )
        return result.to_dict()

    def generate_systemc_module(
        self,
        spec: dict[str, Any],
        model: str = default_local_model(),
        max_repair_attempts: int = 3,
    ) -> dict[str, Any]:
        """Generate a SystemC DUT module from a declarative `DesignSpec` (docs/decisions.md D40)
        and verify it through `codegen/systemc_harness`'s deterministic compile/VCD-trace/
        test-vector checker — real g++/SystemC compilation, no mocking. On a real compile or
        verification failure the actual failure detail (compiler stderr, or failing test-vector
        values) is fed back to the LLM for up to `max_repair_attempts` rounds.

        Args:
            spec: A `DesignSpec` dict — `module_name`, `ports` (each `{name, dir: "in"|"out",
                dtype: "int"|"bool"}`), `behavior` (a natural-language description — the LLM's
                only spec of what to build), `test_vectors` (each `{inputs: {...}, expected:
                {...}}` — caller-authored, never LLM-generated, so verification never checks a
                design against test data the same model that wrote the design also invented).
            model: Ollama model tag to use (must already be pulled on the local server).
            max_repair_attempts: Bound on generate-verify-repair rounds.
        """
        result = flux_generate_systemc_module(spec, model=model, max_repair_attempts=max_repair_attempts)
        return result.to_dict()

    def systemc_generate_dse(
        self,
        variant_specs: list[dict[str, Any]],
        model: str = default_local_model(),
        max_repair_attempts: int = 3,
    ) -> dict[str, Any]:
        """Generate and verify multiple `DesignSpec` variants as real, independent, concurrent
        Ray tasks (docs/decisions.md D41 — the same proven-concurrent `.chia_remote()` shape
        `agentic_multi_axis_dse` established in D34), reporting which variants came out genuinely
        valid — compiled and passed every one of their own test vectors.

        No area/power/timing comparison across variants — generated modules have no wired-up
        physical or cycle-accurate evaluator (unlike `evaluators/rtl`/`evaluators/systemc`'s
        fixed `mac_array`); this reports correctness and generation cost only, honestly.

        Args:
            variant_specs: A list of `DesignSpec` dicts (see `generate_systemc_module`) — each
                variant's own `behavior`/`ports`/`test_vectors`, never an LLM-authored delta
                interpreted mid-loop, so the same design/its-own-checker independence
                `generate_systemc_module` gives one module holds across every variant here too.
            model: Ollama model tag to use for every variant.
            max_repair_attempts: Bound on generate-verify-repair rounds, per variant.
        """
        report = flux_systemc_generate_dse(
            variant_specs, model=model, max_repair_attempts=max_repair_attempts,
        )
        return report.to_dict()

    def generate_rtl_module(
        self,
        spec: dict[str, Any],
        model: str = default_local_model(),
        max_repair_attempts: int = 3,
    ) -> dict[str, Any]:
        """The Verilog sibling of `generate_systemc_module` (docs/decisions.md D44): generates a
        DUT module from a `DesignSpec` and verifies it through `codegen/rtl_harness`'s real
        Verilator compile/VCD-trace/test-vector checker. Same arguments and contract as
        `generate_systemc_module` — see that method's docstring for the exact field meanings.
        """
        result = flux_generate_rtl_module(spec, model=model, max_repair_attempts=max_repair_attempts)
        return result.to_dict()

    def rtl_generate_dse(
        self,
        variant_specs: list[dict[str, Any]],
        model: str = default_local_model(),
        max_repair_attempts: int = 3,
        cache_db_path: str | None = None,
    ) -> dict[str, Any]:
        """The Verilog sibling of `systemc_generate_dse` (docs/decisions.md D45): generates and
        verifies multiple `DesignSpec` variants as real, independent, concurrent Ray tasks over
        real Verilator. Same arguments and contract as `systemc_generate_dse`.

        Args:
            cache_db_path: real, content-hash-keyed synthesis caching
                (docs/decisions.md D89) — pass the same path across calls and a variant whose
                exact final generated source was already synthesized skips a real Yosys re-run.
                Omit for the original always-real-synthesis behavior.
        """
        report = flux_rtl_generate_dse(
            variant_specs, model=model, max_repair_attempts=max_repair_attempts,
            cache_db_path=cache_db_path,
        )
        return report.to_dict()

    def compose_and_verify_rtl_design(
        self,
        leaf_spec_docs: dict[str, dict[str, Any]],
        leaf_sources: dict[str, str],
        composition_spec_doc: dict[str, Any],
    ) -> dict[str, Any]:
        """Wires already-verified leaf Verilog modules into a real, multi-module composite and
        verifies it end-to-end through real Verilator (docs/decisions.md D48) — the "many
        different and various designs" half of this framework, not just isolated single modules.

        Trusts `leaf_sources` are already verified (e.g. from a successful `generate_rtl_module`/
        `rtl_generate_dse` call) — doesn't re-verify them, the same trust boundary
        `rtl_generate_dse` already places on `generate_rtl_module`'s own output.

        Args:
            leaf_spec_docs: module_name -> `DesignSpec` dict (see `generate_rtl_module`) for
                every leaf instantiated in `composition_spec_doc` — the source of truth for how
                each instance's ports get wired, never re-declared or guessed from source alone.
            leaf_sources: module_name -> that module's already-verified Verilog source.
            composition_spec_doc: `{top_module_name, instances: [{module_name, instance_name}],
                nets: {instance_name: {leaf_port_name: net_name}}, ports: [DesignSpec-shaped top-
                level ports], test_vectors: [DesignSpec-shaped end-to-end vectors]}` — a real,
                declarative netlist. The composite module itself is generated deterministically
                from this, never LLM-authored (the same "verification owns structure" split
                `generate_rtl_module`'s own harness already applies to port binding).
        """
        result = flux_compose_and_verify_rtl_design(
            leaf_spec_docs=leaf_spec_docs, leaf_sources=leaf_sources, composition_spec_doc=composition_spec_doc,
        )
        return result.to_dict()

    def synthesize_composite_rtl_design(
        self,
        leaf_spec_docs: dict[str, dict[str, Any]],
        leaf_sources: dict[str, str],
        composition_spec_doc: dict[str, Any],
        cache_db_path: str | None = None,
    ) -> dict[str, Any]:
        """Real Yosys synthesis of a composed design (docs/decisions.md D52), extending D47's
        single-module ranking to composites — closing the gap D47/D51 both named directly.
        Reports a real, whole-design cell count (Yosys flattens the real hierarchy, so this
        reflects every leaf instance's own logic, not just the top-level wrapper) — a
        logic-complexity signal, not a physical `area_mm2` (no PDK wired in). Same
        `leaf_spec_docs`/`leaf_sources`/`composition_spec_doc` shape as
        `compose_and_verify_rtl_design` — see that method's docstring for field meanings. Trusts
        `leaf_sources` are already verified; this method makes no correctness claim of its own.

        Args:
            cache_db_path: real, content-hash-keyed synthesis caching (docs/decisions.md D89) —
                pass the same path across calls and an identical `(leaf_sources,
                composition_spec_doc)` pair is served from the cache instead of a real Yosys
                re-run. Omit for the original always-real-synthesis behavior.
        """
        result = flux_synthesize_composite_rtl_design(
            leaf_spec_docs=leaf_spec_docs, leaf_sources=leaf_sources, composition_spec_doc=composition_spec_doc,
            cache_db_path=cache_db_path,
        )
        return result.to_dict()

    def compose_and_verify_systemc_design(
        self,
        leaf_spec_docs: dict[str, dict[str, Any]],
        leaf_sources: dict[str, str],
        composition_spec_doc: dict[str, Any],
    ) -> dict[str, Any]:
        """Wires already-verified leaf SystemC modules into a real, multi-module composite and
        verifies it end-to-end through real g++/SystemC (docs/decisions.md D55) — the SystemC
        sibling of `compose_and_verify_rtl_design` (D48), closing the one asymmetry D54 left
        standing after closing clocked-design parity.

        Trusts `leaf_sources` are already verified (e.g. from a successful
        `generate_systemc_module`/`systemc_generate_dse` call) — doesn't re-verify them, the same
        trust boundary `compose_and_verify_rtl_design` already places on `generate_rtl_module`'s
        output.

        Args:
            leaf_spec_docs: module_name -> `DesignSpec` dict (see `generate_systemc_module`) for
                every leaf instantiated in `composition_spec_doc` — the source of truth for how
                each instance's ports get wired, never re-declared or guessed from source alone.
            leaf_sources: module_name -> that module's already-verified SystemC source.
            composition_spec_doc: `{top_module_name, instances: [{module_name, instance_name}],
                nets: {instance_name: {leaf_port_name: net_name}}, ports: [DesignSpec-shaped top-
                level ports], test_vectors: [DesignSpec-shaped end-to-end vectors]}` — a real,
                declarative netlist. The composite module itself is generated deterministically
                from this, never LLM-authored (the same "verification owns structure" split
                `generate_systemc_module`'s own harness already applies to port binding).
        """
        result = flux_compose_and_verify_systemc_design(
            leaf_spec_docs=leaf_spec_docs, leaf_sources=leaf_sources, composition_spec_doc=composition_spec_doc,
        )
        return result.to_dict()

    def leaderboard(self, corpus_root: str, entry_id: str, db_path: str) -> list[dict[str, Any]]:
        """Ranks every stored result for a **public** corpus entry's workload — across every
        architecture anyone has ever evaluated it against, not just that entry's own named
        `arch_path` — by its declared objective, best first (docs/decisions.md D58).

        Holdout-safe by construction, same shape as `list_public_corpus`: `entry_id` is looked up
        via `public_entries()` only, so a holdout entry can't be named or ranked through this
        tool. Raises if `entry_id` isn't a public corpus entry, if that entry has no declared
        objective, or if nothing stored yet reports its metric.

        Args:
            corpus_root: path to the `corpus/` directory (its parent is treated as the repo root
                for resolving the entry's `workload_path`, same convention every corpus-consuming
                test in this repo already assumes).
            entry_id: a public corpus entry's `id` (see `list_public_corpus`).
            db_path: path to the `ResultStore` SQLite file to rank results from.
        """
        return flux_leaderboard(corpus_root=corpus_root, entry_id=entry_id, db_path=db_path)

    def sweep_dynamic_shape(
        self,
        backend: str,
        workload: dict[str, Any],
        op_id: str,
        dim: str,
        sample_points: list[int] | None = None,
        arch: dict[str, Any] | None = None,
        mapping: dict[str, Any] | None = None,
        metric: str = "latency_cycles",
        wall_clock_s: float | None = None,
        usd: float | None = None,
        result_db_path: str | None = None,
        n_samples: int | None = None,
        corpus_root: str | None = None,
    ) -> dict[str, Any]:
        """A real, honest cost estimate for a Workload IR op with a declared dynamic bound (e.g.
        KV-cache growth), by evaluating several concrete sample points through a named evaluator
        backend and aggregating the real per-sample results (docs/decisions.md D63) — not a new
        cost model, a real composition of the ones that already exist.

        Every metric present in every sample's own result gets `Estimate.value` = the uniform
        mean across samples, `ci_low`/`ci_high` = the real observed min/max — an honest report of
        the real spread across the exact points evaluated, not a fabricated confidence interval.

        Args:
            backend: Evaluator backend name, same set `evaluate` accepts.
            workload: Flux Workload IR document containing a `{dyn: [lo, hi]}` bound on op
                `op_id`'s `dim`.
            op_id: which op's bound to resolve at each sample point.
            dim: which of that op's bounds is the dynamic one.
            sample_points: concrete integer values to evaluate, e.g. real KV-cache lengths. Omit
                in favor of `n_samples` to draw real quantile points from `workload`'s own
                declared `dynamism.distributions[dim]` reference instead (docs/decisions.md D87).
            arch: Flux Architecture IR document, or omit to use the backend's own reference.
            mapping: Flux Mapping IR document, or omit to let the evaluator choose one.
            metric: which metric decides the "representative" sample used for `bottleneck` —
                bottleneck isn't a quantity that meaningfully averages across samples.
            wall_clock_s: Optional wall-clock budget in seconds, applied per sample point.
            usd: Optional dollar-cost budget, applied per sample point.
            result_db_path: SQLite file to warm-start against (docs/decisions.md D19/D86) — a
                repeated per-sample `(workload, arch, mapping)` triple, whether a duplicate
                `sample_points` entry or one recurring across calls, is served from the store
                instead of a real evaluator call.
            n_samples: draw this many real, evenly-probability-spaced quantile sample points from
                `workload`'s own declared `dynamism.distributions[dim]` reference instead of a
                caller-hand-picked `sample_points` list (docs/decisions.md D87) — give exactly one
                of `sample_points`/`n_samples`.
            corpus_root: directory holding the ingested distribution data `n_samples` resolves
                against; omit for this repo's own `knowledge/corpus/distributions/` (the same
                parameter the underlying CHIA node already accepted — previously not exposed
                over MCP, a review finding).
        """
        result = flux_sweep_dynamic_shape(
            backend, workload, op_id, dim, sample_points,
            arch=arch, mapping=mapping, metric=metric, wall_clock_s=wall_clock_s, usd=usd,
            result_db_path=result_db_path, n_samples=n_samples, corpus_root=corpus_root,
        )
        return result.to_dict()

    def sweep_moe_routing(
        self,
        backend: str,
        workload: dict[str, Any],
        op_id: str,
        routing_samples: list[list[str]],
        arch: dict[str, Any] | None = None,
        mapping: dict[str, Any] | None = None,
        metric: str = "latency_cycles",
        wall_clock_s: float | None = None,
        usd: float | None = None,
        result_db_path: str | None = None,
    ) -> dict[str, Any]:
        """A real, honest cost estimate for a Workload IR op with `kind: data_dependent` MoE
        routing semantics, by resolving several concrete routing decisions (which `top_k` of the
        declared candidate experts actually ran) and evaluating each through a named evaluator
        backend, aggregating the real per-sample results (docs/decisions.md D68) — not a new cost
        model, a real composition of the ones that already exist, the MoE-routing sibling of
        `sweep_dynamic_shape`.

        Every metric present in every sample's own result gets `Estimate.value` = the uniform
        mean across samples, `ci_low`/`ci_high` = the real observed min/max — an honest report of
        the real spread across the exact routing decisions evaluated, not a fabricated confidence
        interval.

        Args:
            backend: Evaluator backend name, same set `evaluate` accepts.
            workload: Flux Workload IR document containing a `data_dependent` op `op_id` with
                `semantics.candidate_ops` naming its real, sibling expert einsum ops.
            op_id: which `data_dependent` op's routing decision to resolve at each sample.
            routing_samples: a list of routing decisions, each a list of `top_k` selected expert
                op ids (real, distinct combinations of `op_id`'s own `semantics.candidate_ops`).
            arch: Flux Architecture IR document, or omit to use the backend's own reference.
            mapping: Flux Mapping IR document, or omit to let the evaluator choose one.
            metric: which metric decides the "representative" sample used for `bottleneck` —
                bottleneck isn't a quantity that meaningfully averages across samples.
            wall_clock_s: Optional wall-clock budget in seconds, applied per sample.
            usd: Optional dollar-cost budget, applied per sample.
            result_db_path: SQLite file to warm-start against (docs/decisions.md D19/D86) — a
                repeated `(workload, arch, mapping)` triple, whether a duplicate `routing_samples`
                entry or one recurring across calls, is served from the store instead of a real
                evaluator call. No real, ingested MoE routing-frequency distribution exists yet
                (docs/decisions.md D87 checked and confirmed none was available to ingest), so
                unlike `sweep_dynamic_shape` there is no `n_samples` here — `routing_samples` must
                still be given explicitly.
        """
        result = flux_sweep_moe_routing(
            backend, workload, op_id, routing_samples,
            arch=arch, mapping=mapping, metric=metric, wall_clock_s=wall_clock_s, usd=usd,
            result_db_path=result_db_path,
        )
        return result.to_dict()

    def generate_architecture_candidate(
        self,
        workload: dict[str, Any],
        base_arch: dict[str, Any],
        objective_metric: str,
        minimize: bool = True,
        backend: str = "zigzag",
        reference_backend: str = "rtl",
        model: str = default_local_model(),
        calibration_db_path: str = "flux_calibration.db",
        result_db_path: str = "flux_generation_results.db",
        max_repair_attempts: int = 3,
        record_residuals: bool = False,
    ) -> dict[str, Any]:
        """An LLM proposes a *whole* new Architecture IR document for `workload` — not filling in
        one caller-named numeric slot the way `agentic_architecture_search` does — real-verified
        against docs/roadmap.md's own Phase 3.5 exit criterion (docs/decisions.md D91): independent
        validity, RTL conformance within the calibrated uncertainty band, and deterministic
        replay, each its own field on the returned report.

        A real schema or evaluation error is fed back to the LLM for up to `max_repair_attempts`
        retries — the same generate-verify-repair shape `generate_rtl_module` already uses for
        RTL source, applied here to a structured IR document.

        Args:
            workload: Flux Workload IR document to generate an architecture for.
            base_arch: a real, schema-valid Architecture IR document used as the LLM's own
                structural reference (same hierarchy levels, same single-dim compute node) and,
                once a valid candidate is generated, as the real fallback nothing else depends on.
            objective_metric: which real metric the new architecture should minimize/maximize.
            minimize: whether lower `objective_metric` is better (the common case) or higher.
            backend: the "declared" evaluator backend the candidate is proposed and calibrated
                against (default `"zigzag"`).
            reference_backend: the real ground-truth evaluator conformance is checked against
                (default `"rtl"` — real Verilator simulation of `evaluators/rtl`'s own hand-written
                design). A candidate that isn't expressible by this backend's own real translator
                (e.g. more than one compute dim) gets a real, honest `conformance_error` instead
                of a crash.
            model: the local Ollama model proposing candidates.
            calibration_db_path: SQLite file real calibration residual statistics accumulate in.
            result_db_path: SQLite file the winning candidate's result is stored into, then
                re-evaluated fresh from, for the real deterministic-replay check.
            max_repair_attempts: how many real generate/validate/repair rounds to try before
                reporting `success=False`.
        """
        result = flux_generate_architecture_candidate(
            workload, base_arch, objective_metric,
            minimize=minimize, backend=backend, reference_backend=reference_backend, model=model,
            calibration_db_path=calibration_db_path, result_db_path=result_db_path,
            max_repair_attempts=max_repair_attempts, record_residuals=record_residuals,
        )
        return result.to_dict()

    def synthesize_with_asap7(
        self,
        module_source: str,
        module_name: str,
        extra_sources: dict[str, str] | None = None,
        cache_db_path: str | None = None,
    ) -> dict[str, Any]:
        """Real ASIC synthesis of `module_source` against ASAP7's real, vendored 7nm predictive
        PDK liberty library (docs/decisions.md D92, BSD-3-Clause, see
        `codegen/rtl_harness/src/flux_codegen_rtl_harness/asap7_pdk/PROVENANCE.md`) — a real,
        physical `area_um2`, not `synthesize_composite_rtl_design`'s own generic-cell logic-
        complexity signal. Real sequential/combinational area split
        (`sequential_area_um2`/`sequential_fraction`) and a real per-cell-type breakdown
        (`cells_by_type`), neither available without a real PDK.

        Args:
            module_source: real Verilog/SystemVerilog source for the DUT's own top-level module.
            module_name: which module in `module_source` (plus `extra_sources`) is `-top`.
            extra_sources: module_name -> source for any real leaf modules `module_source`
                instantiates (e.g. an already-verified composite's own leaves) — `area_um2` then
                reflects the *whole* real design, the same parameter
                `synthesize_composite_rtl_design` already uses.
            cache_db_path: real, content-hash-keyed synthesis caching (docs/decisions.md D89/D92)
                — pass the same path across calls and an identical `(module_source, module_name,
                extra_sources)` triple is served from the cache instead of a real Yosys/ABC
                re-run. Omit for the original always-real-synthesis behavior.
        """
        result = flux_synthesize_with_asap7(
            module_source, module_name, extra_sources=extra_sources, cache_db_path=cache_db_path,
        )
        return result.to_dict()

    def generate_rtl_for_architecture(
        self,
        workload: dict[str, Any],
        arch: dict[str, Any],
        n_vectors: int = 4,
        model: str = default_local_model(),
        max_repair_attempts: int = 3,
    ) -> dict[str, Any]:
        """The architecture→RTL bridge (docs/decisions.md D100): derive a real DesignSpec from an
        accepted Architecture IR's own compute width — ports mechanically, golden test vectors
        computed deterministically in Python from the (workload, arch) content hashes, never by
        the LLM — then generate and harness-verify a real Verilog implementation via the same
        generate-verify-repair loop `generate_rtl_module` uses. No caller-authored spec.

        Args:
            workload: Flux Workload IR document (exactly one einsum op — the bridge's v0.1
                scope, matching evaluators/rtl's own).
            arch: Flux Architecture IR document with exactly one single-dim compute node; its
                width becomes the derived module's lane count.
            n_vectors: how many golden test vectors to derive (deterministic per candidate pair).
            model: Ollama model name for the implementation LLM.
            max_repair_attempts: total generate attempts, real failures fed back in between.
        """
        report = flux_generate_rtl_for_architecture(
            workload, arch, n_vectors=n_vectors, model=model, max_repair_attempts=max_repair_attempts,
        )
        return report.to_dict()

    def generate_sequential_rtl_for_architecture(
        self,
        workload: dict[str, Any],
        arch: dict[str, Any],
        model: str = default_local_model(),
        max_repair_attempts: int = 3,
    ) -> dict[str, Any]:
        """The sequential architecture→RTL bridge (docs/decisions.md D117/D118): derive a whole
        sequential design from the candidate pair — the tile's width from the architecture's own
        compute dimension, the cycle count from the workload's reduction length as
        `ceil(C / lanes)` — emit the handshake, step counter and tiling as deterministic Verilog,
        LLM-implement *only* the combinational tile, then compose and measure through real
        Verilator.

        Returns both findings separately: whether the composed design computes the right result,
        and whether its measured latency equals the cycle count predicted before it was built.
        `success` requires both — a right answer at the wrong latency is not a usable reference.

        Args:
            workload: Flux Workload IR document (exactly one einsum op, static bounds); its
                reduction length sets the cycle count.
            arch: Flux Architecture IR document with exactly one single-dim compute node; its
                width becomes the tile's lane count.
            model: Ollama model name for the tile-implementation LLM.
            max_repair_attempts: total generate attempts, real failures fed back in between.
        """
        report = flux_generate_sequential_rtl_for_architecture(
            workload, arch, model=model, max_repair_attempts=max_repair_attempts,
        )
        return report.to_dict()

    def generate_gemm_rtl_for_architecture(
        self,
        workload: dict[str, Any],
        arch: dict[str, Any],
        model: str = default_local_model(),
        max_repair_attempts: int = 3,
    ) -> dict[str, Any]:
        """The reference-dataflow GEMM bridge (docs/decisions.md D121): derive the design whose
        schedule is the one `evaluators/rtl`'s own hand-written `mac_array.sv` runs — same loop
        nest, same preloaded operand memories, same drain — emit all of it deterministically,
        LLM-implement only the combinational broadcast multiply-accumulate step, then compose and
        measure through real Verilator.

        Use this over `generate_sequential_rtl_for_architecture` when the measured cycle count
        needs to be *comparable to the rtl evaluator's own*: that one parallelises the reduction
        and this one parallelises the output dimension, so only this one produces the same
        quantity the reference reports.

        Args:
            workload: Flux Workload IR document (one einsum op, static bounds, plain 2D GEMM).
            arch: Flux Architecture IR document with one single-dim compute node. A K that is
                not a whole number of lane-count groups is supported by masking the ragged final
                group (docs/decisions.md D130) — and is the case worth reaching for, since the
                `rtl` evaluator refuses those candidates outright, so they have no reference
                measurement at all.
            model: Ollama model name for the step-implementation LLM.
            max_repair_attempts: total generate attempts, real failures fed back in between.
        """
        report = flux_generate_gemm_rtl_for_architecture(
            workload, arch, model=model, max_repair_attempts=max_repair_attempts,
        )
        return report.to_dict()

    def calibrate_against_generated_rtl(
        self,
        workload: dict[str, Any],
        arch: dict[str, Any],
        calibration_db_path: str,
        backend: str = "zigzag",
        metric: str = "latency_cycles",
        model: str = default_local_model(),
        allow_redundant: bool = False,
    ) -> dict[str, Any]:
        """Measure a candidate with a *generated* design and record it as a calibration reference
        (docs/decisions.md D136) — narrowing that candidate's interval by evidence rather than by
        assumption. Measured effect on a real candidate: 24.32x wide before, 1.04x after.

        Refuses by default when `evaluators/rtl` can already measure the candidate: that residual
        is already obtainable, so recording it again would count the same evidence twice. The
        candidates worth pointing this at are the ones the reference refuses — a K that is not a
        whole number of lane-count groups, for instance.

        Records nothing unless the generated design both verified and measured its predicted cycle
        count; an unverified design is not a reference. Every skip says which case it was.

        Args:
            workload: Flux Workload IR document (one einsum op, static bounds).
            arch: Flux Architecture IR document with one single-dim compute node.
            calibration_db_path: SQLite calibration store to record into.
            backend: the fast model whose prediction is being calibrated.
            metric: which metric to record; the generated design measures `latency_cycles`.
            model: Ollama model name for the tile-implementation LLM.
            allow_redundant: record even where a reference already exists (double-counts).
        """
        report = flux_calibrate_against_generated_rtl(
            workload, arch, calibration_db_path, backend=backend, metric=metric, model=model,
            allow_redundant=allow_redundant,
        )
        return report.to_dict()

    def protocol_lookup(
        self, protocol_id: str, version: str | None = None, signal: str | None = None
    ) -> dict[str, Any]:
        """Structured facts about a bus/stream protocol: signals with widths and directions,
        parameters, and the numbered rules of the source document (docs/decisions.md D174).

        `protocol_id` is one of the ids `list_protocols` reports (`obi`, `axi4`, `axi4-lite`,
        `wishbone`). Pass `version` when several ship; pass `signal` to narrow to one signal.

        Read the `provenance` on every answer, and in particular `normative`. `normative: false`
        means these facts were read from an *implementation* of the standard rather than from the
        standard itself — which is how AXI is available here at all, since Arm's specification
        cannot be redistributed. Where an implementation and the standard could differ, the
        standard governs and this is not evidence about it.
        """
        return flux_protocol_lookup(protocol_id, version, signal)

    def list_protocols(self) -> dict[str, Any]:
        """Every protocol this build ships, with its source licence and whether that source is
        the standard or an implementation of it (docs/decisions.md D174).

        Worth calling first: the set is deliberately small and sourcing-driven, so a protocol's
        absence means no verified open source has been ingested for it, not that it is unimportant.
        """
        return flux_list_protocols()

    def check_ir_protocols(self, document: dict[str, Any]) -> dict[str, Any]:
        """Resolve every `protocol`/`model` reference in an IR document against the shipped
        protocol specs (docs/decisions.md D174).

        Reports per reference rather than raising, so one unknown string doesn't hide the rest.
        An unresolved reference means Flux has no sourced description of that protocol at that
        version — not that the design is wrong. Note that `all_resolved` is `True` vacuously for a
        document with no protocol references; `checks` being empty is how you tell.
        """
        return flux_check_ir_protocols(document)

    def check_protocol_conformance(
        self, source: str, protocol_id: str, role: str, version: str | None = None,
        module_name: str | None = None, parameters: dict[str, int] | None = None,
        prefix: str = "",
    ) -> dict[str, Any]:
        """Does this SystemVerilog module present a conformant interface for `protocol_id` as
        `role`? (docs/decisions.md D178)

        Worth running on any RTL you or a model just wrote that claims a bus interface: a reversed
        handshake pair, a missing required signal or a mis-sized data bus all pass Verilator
        without complaint, and this catches them against a sourced protocol document rather than
        against remembered knowledge of the protocol.

        `prefix` strips a per-interface naming prefix (`s_axis_tdata` against `tdata`); globals
        like a clock are never prefixed. `parameters` supplies values for parameterised widths;
        without them widths are reported as unchecked notes rather than guessed.

        `conforms=True` means the interface is *shaped* right — names, directions, widths — never
        that the design speaks the protocol. Ordering and timing are not checked.
        """
        return flux_check_protocol_conformance(
            source, protocol_id, role, version, module_name, parameters, prefix,
        )

    def author_objective(
        self,
        prose: str,
        workload: dict[str, Any],
        base_arch: dict[str, Any],
        model: str = default_local_model(),
        max_repair_attempts: int = 3,
        facts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Author a validated Objective IR document from a natural-language request
        (docs/decisions.md D232) — the missing NL step in front of the campaign tools.

        A local LLM drafts the document; the REAL campaign validator (schema + semantic parser)
        accepts or rejects it, and rejections are repaired with the actual error fed back, up to
        `max_repair_attempts` times. The result is never executed here: on success, pass
        `objective` to campaign_start. The document's own provenance records the model and the
        exact prose, so any campaign started from it is auditable back to the request.

        Args:
            prose: The request in plain language (metrics and directions, search space, budget,
                fidelity wishes — e.g. "minimize latency and energy over widths 8/16/32, screen
                with zigzag, escalate the winner through rtl, at most 8 evaluations").
            workload: Workload IR document (inline dict) the campaign will evaluate.
            base_arch: Architecture IR document (inline dict) the search varies.
            model: Local Ollama model that drafts the document.
            max_repair_attempts: Bounded validate-repair rounds before giving up.
            facts: Optional mined facts (from mine_knowledge, docs/decisions.md D245) rendered
                into the authoring prompt with their not_established boundaries — helps the
                model pick realistic budgets, stop targets and search ranges.
        """
        from flux_chia_nodes import flux_author_objective

        return flux_author_objective(
            prose, workload, base_arch, model=model, max_repair_attempts=max_repair_attempts,
            facts=facts,
        ).to_dict()

    def author_design_spec(
        self,
        prose: str,
        model: str = default_local_model(),
        max_repair_attempts: int = 3,
        n_vectors: int = 4,
    ) -> dict[str, Any]:
        """Author a validated combinational DesignSpec from a natural-language request
        (docs/decisions.md D235) — "build me a module that does X" beyond the derivable
        dot-product family.

        A local LLM designs the ports AND writes a Python reference function; this node EXECUTES
        the reference to compute the golden test vectors (expected outputs are computed, never
        model-asserted), checks determinism and that every output fits its declared port width,
        and validates through the real harness parser — failures repaired with the real error,
        bounded. Returns the spec plus a holdout twin (fresh seeds, same reference) ready for
        `flux_generate_rtl_module` and the D223/D234 holdout-and-regeneration machinery. Nothing
        is generated or simulated here.

        Args:
            prose: The module's function in plain language (e.g. "an 8-bit saturating adder:
                out is a+b clamped to [-128, 127]").
            model: Local Ollama model that authors the design.
            max_repair_attempts: Bounded author-validate-repair rounds.
            n_vectors: Shown golden vectors (the holdout twin carries at least 2x).
        """
        from flux_chia_nodes import flux_author_design_spec

        return flux_author_design_spec(
            prose, model=model, max_repair_attempts=max_repair_attempts, n_vectors=n_vectors
        ).to_dict()

    def mine_knowledge(
        self,
        campaign_db_paths: list[str] | None = None,
        calibration_db_paths: list[str] | None = None,
        facts_db_path: str | None = None,
    ) -> dict[str, Any]:
        """Mine typed, provenance-carrying facts from campaign and calibration stores
        (docs/decisions.md D243) — the Knowledge role learning from the Evaluator's own
        measured history.

        Fact kinds: `estimator_bias` (observed prediction/reference ratio ranges per residual
        family), `measured_point` (rung measurements per campaign), `observed_ratio` (the
        effect of one knob doubling between two measured candidates — never a fitted law),
        `refusal_pattern` (exact stored failure messages, grouped verbatim), and
        `frontier_outcome` (completed campaigns' final frontiers with per-metric fidelity).
        Every fact carries evidence (the stored numbers), scope (the claim's boundary), an
        explicit `not_established` line (the inference the numbers do NOT license), and
        pointers (campaign ids, trial seqs, record ids). Non-done campaigns and unusable
        stores land in `skipped`, counted rather than silently dropped.

        Args:
            campaign_db_paths: Campaign store SQLite files to mine.
            calibration_db_paths: Calibration store SQLite files to mine.
            facts_db_path: Optional FactStore file — persists the mined facts
                (content-addressed, idempotent; recall with recall_facts, D250).
        """
        from flux_chia_nodes import flux_mine_knowledge

        return flux_mine_knowledge(
            campaign_db_paths=campaign_db_paths, calibration_db_paths=calibration_db_paths,
            facts_db_path=facts_db_path,
        )

    def recall_facts(
        self,
        facts_db_path: str,
        kind: str | None = None,
        contains: str | None = None,
        verify: bool = False,
    ) -> dict[str, Any]:
        """Recall facts persisted by mine_knowledge's `facts_db_path` (docs/decisions.md
        D250): filter by fact kind (`estimator_bias`, `measured_point`, `observed_ratio`,
        `refusal_pattern`, `frontier_outcome`) and/or a case-insensitive substring of the
        statement.

        `verify=True` re-derives each recalled fact from the store rows its pointers name:
        `intact` (the same statement is still derivable), `dangling` (source store gone or
        unreadable), or `superseded` (the source's evidence moved on) — recall across time is
        never silent trust. The facts store is deliberately separate from the BM25 spec/wisdom
        corpus: measured facts and licensed text are different provenance classes.

        Args:
            facts_db_path: FactStore SQLite file (written by mine_knowledge).
            kind: Optional fact-kind filter.
            contains: Optional case-insensitive substring filter on the statement.
            verify: Re-derive each recalled fact and attach its verification status.
        """
        from flux_chia_nodes import flux_recall_facts

        return flux_recall_facts(
            facts_db_path, kind=kind, contains=contains, verify=verify
        )

    def ip_catalog(
        self,
        interface: str | None = None,
        status: str | None = None,
        contains: str | None = None,
        instantiate: str | None = None,
        clients: int = 0,
        banks: int = 0,
        width_bits: int = 0,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Referenceable interconnect IP — crossbars, staged fabrics, Clos networks,
        butterflies, NoCs (docs/decisions.md D267) — with the rule that an entry is only
        listed if Flux can BUILD it.

        Each entry carries its parameters, the interfaces it fits (`obi` natively, `axis` only
        with a skid-buffer adapter that is not generated today, and the entry says so), how its
        cost grows, when to reach for it, its known limits, published references, and — kept
        separate from the references — what this repo has actually measured. `status` is
        `constructible` (a generator exists, so it can be built, simulated and placed),
        `evaluable_only` (no RTL generator here; reachable through the BookSim/Noxim NoC
        evaluators, whose numbers are not comparable to placed silicon), or `not_implemented`
        (listed so the absence is explicit).

        Pass `instantiate` with an IP id plus `clients`/`banks`/`width_bits` and any entry
        parameters in `params` to get the concrete topology back — blocks, stages, peak
        concurrency, modelled throughput, inter-stage link bits — ready to hand to a campaign
        as an `interconnect` Architecture IR block.

        Args:
            interface: Filter to IP fitting an interface (`obi`, `axis`).
            status: Filter by build status.
            contains: Case-insensitive substring filter over the entry text.
            instantiate: IP id to construct instead of listing.
            clients: Requester count (with `instantiate`).
            banks: Target count (with `instantiate`).
            width_bits: Datapath width (with `instantiate`).
            params: Entry-specific parameters, e.g. `{"n": 4, "m": 4}` for `clos`.
        """
        from flux_chia_nodes import flux_ip_catalog

        return flux_ip_catalog(
            interface=interface, status=status, contains=contains, instantiate=instantiate,
            clients=clients, banks=banks, width_bits=width_bits, params=params,
        )

    def check_prose_faithfulness(
        self,
        prose: str,
        objective: dict[str, Any] | None = None,
        design_spec: dict[str, Any] | None = None,
        model: str = default_local_model(),
    ) -> dict[str, Any]:
        """Cross-examine an authored artifact against the prose that requested it
        (docs/decisions.md D249) — the semantic half of prose-faithfulness (D240's backend
        capability table is the mechanical half).

        Pass exactly one of `objective` (an authored Objective IR document) or `design_spec`
        (an authored DesignSpec). A judge model sees a CODE-RENDERED summary of the parsed
        document — every semantic field, fixed wording, never raw JSON — next to the original
        request, and returns verdict "faithful" | "unfaithful" (with one named mismatch per
        finding) | "unknown" (no parseable verdict after a bounded retry — never a silent
        pass). Advisory: the verdict gates whatever the caller decides it gates; the full
        transcript is returned so a human can overrule the judge with the evidence in hand.

        Args:
            prose: The original natural-language request the artifact was authored from.
            objective: Authored Objective IR document to check (or None).
            design_spec: Authored DesignSpec document to check (or None).
            model: Local Ollama judge model.
        """
        from flux_chia_nodes import flux_check_prose_faithfulness

        return flux_check_prose_faithfulness(
            prose, objective=objective, design_spec=design_spec, model=model
        ).to_dict()

    def campaign_start(
        self, objective: dict[str, Any], db_path: str, run_trials: int = 0
    ) -> dict[str, Any]:
        """Create or resume a durable, multi-objective search campaign from an Objective IR
        document (docs/decisions.md D216-D220).

        The campaign_id is the objective document's content hash: starting the same objective
        twice resumes it, and any change to the document (a weight, a width, a budget) is a new
        campaign identity. State (trials, results, budget ledger, events) lives in `db_path` and
        survives interruption — the database IS the checkpoint.

        Args:
            objective: Objective IR document — objectives (metric + direction, optionally
                weighted), mode ("pareto" | "weighted"), workload/base_arch (inline documents or
                store refs), backends (screening + optional escalation rungs), search space
                (e.g. {"kind": "architecture_width", "widths": [4, 8, 16]}), strategy ("grid"
                deterministic | "agentic" LLM-proposed), and a hard budget
                (evaluations/wall_clock_s/usd — at least one).
            db_path: SQLite file for all campaign state and results.
            run_trials: 0 creates and checkpoints only; N > 0 also runs up to N trials now.
        """
        from flux_chia_nodes import flux_campaign_start

        return flux_campaign_start(objective=objective, db_path=db_path, run_trials=run_trials)

    def campaign_step(
        self, db_path: str, campaign_id: str, max_trials: int = 1,
        screening_parallelism: int = 1,
        knowledge_facts: list[dict[str, Any]] | None = None,
        knowledge_text: str | None = None,
    ) -> dict[str, Any]:
        """Run up to `max_trials` more trials of an existing campaign — the agent-paced mode.

        Safe to call at any time: after a crash it replays interrupted trials honestly (they
        stay recorded as interrupted; the candidate is re-measured as a new trial), and when the
        budget is exhausted it stops without overdrawing (top up via campaign_resume).

        `screening_parallelism` > 1 batches GRID screening and dispatches each batch as
        concurrent Ray tasks (docs/decisions.md D238); agentic/generative strategies ignore it.
        `knowledge_facts` (docs/decisions.md D245): facts from mine_knowledge, rendered into
        the agentic/generative proposal prompt with their not_established boundaries attached —
        advisory; the grid strategy has nothing to advise and ignores it.
        `knowledge_text` (docs/decisions.md D270) carries CURATED knowledge into the same
        prompt — a retrieved `design-guidance` chunk from knowledge_lookup, an entry from
        ip_catalog, a rule from a protocol — under its own heading, kept separate from the
        measured facts so the two are never read as the same kind of claim.
        """
        from flux_chia_nodes import flux_campaign_step

        return flux_campaign_step(
            db_path=db_path, campaign_id=campaign_id, max_trials=max_trials,
            screening_parallelism=screening_parallelism, knowledge_facts=knowledge_facts,
            knowledge_text=knowledge_text,
        )

    def campaign_status(self, db_path: str, campaign_id: str) -> dict[str, Any]:
        """Campaign status without running anything: phase, per-status trial counts, the derived
        budget ledger (granted/spent/remaining — `usd` stays null when no backend ever reported a
        cost), frontier size, non-deterministic trial count, and the event tail."""
        from flux_chia_nodes import flux_campaign_status

        return flux_campaign_status(db_path=db_path, campaign_id=campaign_id)

    def campaign_resume(
        self,
        db_path: str,
        campaign_id: str,
        top_up: dict[str, float] | None = None,
        max_trials: int | None = None,
    ) -> dict[str, Any]:
        """Resume a paused or budget-exhausted campaign, optionally topping up the budget
        (e.g. {"evaluations": 16}) as an append-only ledger event. Refuses terminal campaigns
        (stopped/done) — any changed objective is a new campaign, never a mutation."""
        from flux_chia_nodes import flux_campaign_resume

        return flux_campaign_resume(
            db_path=db_path, campaign_id=campaign_id, top_up=top_up, max_trials=max_trials
        )

    def campaign_stop(self, db_path: str, campaign_id: str, reason: str = "") -> dict[str, Any]:
        """Terminally stop a campaign, recording the reason. A double stop is refused with the
        original reason, so the caller learns why it already stopped."""
        from flux_chia_nodes import flux_campaign_stop

        return flux_campaign_stop(db_path=db_path, campaign_id=campaign_id, reason=reason)

    def campaign_frontier(
        self, db_path: str, campaign_id: str, include_contenders: bool = False
    ) -> dict[str, Any]:
        """The campaign's Pareto frontier: per-objective values with CI bounds, fidelity (screen
        vs escalation rung), and result row ids for full lineage. `include_contenders` adds the
        escalation set — everything the screening data cannot rule out (docs/decisions.md D218)."""
        from flux_chia_nodes import flux_campaign_frontier

        return flux_campaign_frontier(
            db_path=db_path, campaign_id=campaign_id, include_contenders=include_contenders
        )

    def backend_health(self) -> dict[str, Any]:
        """Which evaluator backends are usable right now, and why the others are not
        (docs/decisions.md D156).

        Worth calling before committing to a backend: `usable_backends` lists the ones whose
        prerequisites are present, so a plan can pick one that will run rather than discovering a
        stopped Docker daemon or a missing `verilator` partway through an evaluation.

        Checks adapter import and external-tool presence only — for Timeloop that means actually
        asking the Docker daemon, not just finding the client. A tool that exists can still be
        broken, so `usable` means prerequisites are present, never that a result will be correct.
        """
        return flux_backend_health().to_dict()

    def explain_candidate(
        self, workload: dict[str, Any], arch: dict[str, Any],
        mapping: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Which backends can express this (workload, architecture) pair, and why the others
        cannot (docs/decisions.md D157).

        Costs no simulation — it runs each adapter's own translators, so the reasons are the
        specific ones they already produce ("K=32 is not a multiple of LANES=12") rather than a
        generic rejection. Worth calling before an evaluation or a search, so a plan picks a
        backend that can express the design instead of finding out by failing.

        Pass `mapping` when the candidate has one: some backends refuse any mapping at all (the
        `rtl` evaluator's schedule is fixed in its RTL), and others check it against the
        architecture, so omitting it makes the answer optimistic.

        Backends with no cheap check report `expressible=null`, and so does a check that is itself
        broken — an unknown is deliberately not reported as a refusal.
        """
        return flux_explain_candidate(workload, arch, mapping).to_dict()

    def synthesize_with_asap7_redacted(
        self,
        module_source: str,
        module_name: str,
        baseline_module_source: str,
        baseline_module_name: str,
        extra_sources: dict[str, str] | None = None,
        baseline_extra_sources: dict[str, str] | None = None,
        cache_db_path: str | None = None,
    ) -> dict[str, Any]:
        """The real, agent-facing redacted comparison docs/gap-analysis.md G15 is actually about
        (docs/decisions.md D93): real ASIC synthesis of both `module_source` (the real candidate)
        and `baseline_module_source` (the real baseline) via `synthesize_with_asap7`, but only a
        real, redacted comparison ever leaves this tool — a relative area delta and a real,
        kept-because-already-normalized sequential fraction. The real absolute `area_um2` for
        either design is computed internally and never appears anywhere in the response,
        structurally, not by convention (see `flux_redaction.core`'s own module docstring).

        Args:
            module_source: real Verilog/SystemVerilog source for the real candidate's top module.
            module_name: which module in `module_source` is `-top`.
            baseline_module_source: real source for the real baseline the candidate is compared
                against (e.g. an already-shipped reference design).
            baseline_module_name: which module in `baseline_module_source` is `-top`.
            extra_sources: module_name -> source for the candidate's own real leaf modules.
            baseline_extra_sources: module_name -> source for the baseline's own real leaf modules.
            cache_db_path: real, content-hash-keyed synthesis caching (docs/decisions.md D89/D92),
                shared by both the real candidate and real baseline synthesis calls.
        """
        result = flux_synthesize_with_asap7_redacted(
            module_source, module_name, baseline_module_source, baseline_module_name,
            extra_sources=extra_sources, baseline_extra_sources=baseline_extra_sources,
            cache_db_path=cache_db_path,
        )
        return result.to_dict()
