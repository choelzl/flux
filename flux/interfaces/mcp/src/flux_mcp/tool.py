"""`FluxTool` — the third surface docs/agent-surface.md promises ("one definition, three surfaces"):
every real `flux_chia_nodes` function `interfaces/chia_nodes/` ships — evaluate/search/calibrate/
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

import inspect

from flux_llm import default_local_model
from typing import Any

from chia.base.tools.ChiaTool import ChiaTool
from flux_chia_nodes import (
    flux_bankmap_dse_loop,
    flux_nlu_dse_loop,
    flux_interconnect_mapping_dse_loop,
    flux_macarray_dse_loop,
    flux_omni_run,
    flux_calibrate,
    flux_champsim_run,
    flux_check_validity,
    flux_invent_prefetcher,
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
    flux_prefetcher_dse_loop,
    flux_protocol_lookup,
    flux_explain_candidate,
    flux_generate_rtl_module,
    flux_generate_systemc_module,
    flux_get_result,
    flux_knowledge_lookup,
    flux_leaderboard,
    flux_list_public_corpus,
    flux_sweep_dynamic_shape,
    flux_sweep_moe_routing,
    flux_synthesize_composite_rtl_design,
    flux_synthesize_with_asap7,
    flux_synthesize_with_asap7_redacted,
)


class FluxTool(ChiaTool):
    """`FluxTool(name="flux")` starts a real MCP server at
    `http://{self.hostname}:{self.port}/{self.name}/mcp` exposing every `flux_chia_nodes`
    function as a tool — the authoritative, parity-guarded list is docs/agent-surface.md
    (`tests/unit/test_mcp_surface_parity.py` keeps it complete); counts and name lists in prose
    rotted twice (docs/decisions.md D95/D96) and are deliberately not repeated here.
    """

    #: Methods that are not tools. Everything else this class defines publicly IS one
    #: (`tools()`), because the method list and the tool list were two hand-kept copies of
    #: the same names and a node registered in one and missing from the other is invisible
    #: to every agent while looking complete from the inside (D119, D452).
    _NOT_TOOLS = frozenset({"setup", "tools"})

    @classmethod
    def tools(cls) -> dict[str, Any]:
        """Every tool this class exposes, by MCP name suffix, in definition order.

        ASKED, not scanned (the D442 move applied to this surface): the parity guards and the
        docs table check read this instead of regexing `setup`'s source, and a capability
        reaches an agent by being a documented public method here -- one edit, not two.
        A helper that must NOT be a tool is `_`-prefixed, the convention everywhere else here;
        a public method with no matching CHIA node fails `test_mcp_surface_parity`.
        """
        found: dict[str, Any] = {}
        for klass in reversed(cls.__mro__):
            if not issubclass(klass, FluxTool):
                continue          # `ChiaTool`'s own API (mcp, name, port, run) is not a surface
            for name, fn in vars(klass).items():
                if (inspect.isfunction(fn) and not name.startswith("_")
                        and name not in cls._NOT_TOOLS):
                    found[name] = fn
        return found

    def setup(self) -> None:
        """Register every tool this class defines, under `{self.name}_<method>`."""
        for suffix in self.tools():
            self.mcp.add_tool(getattr(self, suffix), name=f"{self.name}_{suffix}")

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
                of its point value is flagged for escalation to a higher-fidelity stage.
            escalate_if_recommended: act on the escalation advisory (docs/decisions.md D99):
                when the policy recommends it, buy one real `reference_backend` measurement,
                record its residual (the D98 flywheel), and re-calibrate before returning.
                Skipped when no escalation is recommended, when this exact candidate already
                has a calibration record, or when the reference can't express the candidate.
            reference_backend: which higher-fidelity stage to buy the measurement from, e.g.
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
        — docs/history/roadmap.md's exit criterion made checkable: a candidate "passes RTL conformance against
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
        result (docs/history/gap-analysis.md G14) — merged with, not replacing, the evaluator's own self-report.
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
        see it (docs/history/roadmap.md's validation methodology).

        Args:
            corpus_root: Path to a corpus directory containing `public/` and `holdout/`
                subdirectories of entry manifests (see `corpus/README.md`).
        """
        return flux_list_public_corpus(corpus_root)

    def prefetcher_dse_loop(
        self,
        db_path: str,
        problem: str | None = None,
        traces_dir: str | None = None,
        champsim_bin: str | None = None,
        stage: int = 2,
        budget: int = 24,
        llm_round: int = 8,
        parallelism: int = 16,
        seed: int = 0,
        retention_floor: float = 0.90,
        strategy: str = "climb",
        max_storage_bytes: int | None = None,
        decide_on_finalists: int = 4,
        screen_only: bool = False,
        compose_rounds: int = 2,
        tune_partners: int = 12,
        include_invented: bool = True,
        invent_rounds: int = 0,
        remote: bool = True,
    ) -> dict[str, Any]:
        """Search an L2 prefetcher's configuration space against real ChampSim traces, in two
        stages: maximise geomean IPC speedup over the no-prefetcher baseline, then minimise the
        prefetcher's hardware storage while holding a fraction of that speedup.

        Every speedup returned was measured by a real simulation. One configuration costs three
        ChampSim runs of about six minutes each, so `budget` is the run's cost in wall clock.

        Args:
            db_path: Where measurements accumulate. Re-running against the same file re-measures
                nothing, so an interrupted study resumes rather than restarting.
            problem: Plain-language goal, replacing the default in the proposer's prompt.
            traces_dir: Where the traces live (default: applications/prefetcher/traces).
            champsim_bin: The simulator; otherwise $FLUX_CHAMPSIM_BIN, then PATH, then the
                in-tree project build.
            stage: 1 stops after maximising speedup; 2 also minimises storage.
            budget: Configurations to MEASURE per stage.
            llm_round: Configurations proposed by a local model before the search starts; 0 for a
                fully deterministic run.
            parallelism: Simulations in flight at once. This simulator is memory-bandwidth bound,
                so throughput grows sublinearly with this.
            seed: Seed for the sampled part of the search.
            retention_floor: Stage 2 must keep this fraction of stage 1's speedup. A CONSTRAINT:
                configurations below it are refused, not ranked lower.
            strategy: How stage 1 spends its budget. "climb" expands the fastest configuration
                (the default); "pareto-uct" grows a tree over (speedup, storage) and expands
                the branch contributing most hypervolume to the frontier -- MicroEvo's tree
                policy (arXiv:2608.06183) on this study's own move generators and stages.
            max_storage_bytes: A hard budget on the prefetcher's modelled storage. Configurations
                over it are refused before any simulation, the proposer is told the budget, and
                the decision is the best confirmed design that fits. None searches freely; either
                way the result carries the speedup-vs-storage `frontier` -- every design faster
                than everything smaller -- so a caller who values SRAM differently can pick
                another point with the same evidence behind it.
            decide_on_finalists: Re-measure N configurations at full length before deciding,
                spread along the screened speedup-vs-storage frontier rather than the top N by
                speedup, so the confirmed numbers trace the trade-off instead of one corner. The search itself runs on a cheap screen whose rank correlation with a
                full run is +0.776 — good for ordering, not for quoting. Instruction counts are
                deliberately not a parameter: this is a fidelity decision the study makes.
            screen_only: Skip the full-length confirmation. Fast, and every number returned is
                then a screen estimate, which the result states in `not_established`.
            compose_rounds: Greedily add L2 partner prefetchers alongside Bingo, up to this many
                times, keeping each only if it earns its place.
            tune_partners: Hill-climb the partners' own knobs for this many measurements.
            include_invented: Offer the invention loop's kept designs as partners too, on a
                simulator built with them installed (about a minute, cached by content).
            invent_rounds: Have the local model invent NEW prefetchers during this run, judged
                by the compiler and the screen; survivors join the compose menu immediately.
            remote: Dispatch simulations as Ray tasks. False runs them in a local thread pool.

        Returns:
            The decision, both stages' best configurations, everything measured, what was refused
            and why, and what the run could not establish.
        """
        return flux_prefetcher_dse_loop(
            db_path, problem=problem, traces_dir=traces_dir, champsim_bin=champsim_bin,
            stage=stage, budget=budget, llm_round=llm_round, parallelism=parallelism, seed=seed,
            retention_floor=retention_floor, strategy=strategy,
            max_storage_bytes=max_storage_bytes,
            decide_on_finalists=decide_on_finalists,
            screen_only=screen_only, compose_rounds=compose_rounds, tune_partners=tune_partners,
            include_invented=include_invented, invent_rounds=invent_rounds, remote=remote,
        )

    def invent_prefetcher(
        self,
        rounds: int = 4,
        repair_attempts: int = 3,
        inert_repairs: int = 1,
        problem: str | None = None,
        traces_dir: str | None = None,
        source_tree: str | None = None,
        parallelism: int = 12,
        llm_model: str | None = None,
        num_predict: int = 2400,
        scratch_root: str | None = None,
        keep_dir: str | None = None,
        confirm_best: bool = True,
        reference_stack: list[str] | None = None,
    ) -> dict[str, Any]:
        """Design NEW L2 cache prefetchers in C++, compile them, and measure whether they win.

        The code-space stage of the prefetcher study: everything else searches the parameters of
        prefetchers somebody already wrote, this writes one. A model emits a complete prefetcher
        class; the dispatch wiring and knob declarations are generated deterministically around
        it; a real compiler is the first verdict and measured IPC the second.

        It is affordable because a ChampSim rebuild costs seconds while one evaluation costs three
        simulations of several minutes, so compile failures are cheap feedback rather than lost
        work.

        Args:
            rounds: How many designs to ask for. Each is asked to beat the best measured so far,
                starting from the tallest stack the kept library's records vouch for — the stock
                bingo+sms+stride plus each kept design beside the stack it earned its place in —
                re-measured in one wave, so the target is never a stack the study has moved past.
            repair_attempts: How many times a design that fails to compile is handed its own first
                compiler diagnostic and asked to fix it.
            inert_repairs: How many times a design that compiled and ran but changed nothing is
                handed the simulator's counters (zero prefetches issued, or none useful) and asked
                to fix the logic. A few lines' repair, cheaper than a fresh design.
            problem: The workload in plain language, replacing the default description of the 5G
                baseband traces.
            traces_dir: Where the traces live.
            source_tree: A ChampSim source tree to build in; otherwise the one beside the binary.
            parallelism: Simulations in flight at once.
            llm_model: Override the local model.
            num_predict: Output token budget. A C++ header needs far more than this repository's
                usual JSON proposal, and the default of 1200 truncates one mid-statement.
            scratch_root: Where to stage build trees; each candidate gets a private copy.
            keep_dir: Where to write every design that compiled, with its measurements. Build
                trees are temporary, so without this a design that beat the reference is lost.
            confirm_best: Re-measure the best design at full length before declaring anything. The
                screen orders candidates; whether an invention is worth having is a question only
                the expensive stage answers. Only a design that beat the reference on the screen
                is confirmed.
            reference_stack: Measure this stack as the reference instead of choosing the tallest
                one the library can vouch for.

        Returns:
            Every attempt with what happened to it — compiled and measured, failed to compile with
            the diagnostic, or built and crashed — plus the best design and whether it beat Bingo.
            All numbers are from the cheap screening stage and order candidates rather than
            settling them.
        """
        return flux_invent_prefetcher(
            rounds=rounds, repair_attempts=repair_attempts, inert_repairs=inert_repairs,
            problem=problem, traces_dir=traces_dir, source_tree=source_tree,
            parallelism=parallelism, llm_model=llm_model, num_predict=num_predict,
            scratch_root=scratch_root, keep_dir=keep_dir, confirm_best=confirm_best,
            reference_stack=reference_stack,
        )

    def bankmap_dse_loop(
        self,
        strides: list[int],
        concurrent: int,
        banks: int = 8,
        address_bits: int = 20,
        z3_seconds: int = 60,
        llm_round: int = 6,
        max_xor_inputs: int | None = None,
        problem: str | None = None,
        db_path: str = "demo-bankmap.db",
        crossbar: str | None = None,
        stage_capacities: list[int] | None = None,
        lanes: int | None = None,
        stages: list[dict[str, Any]] | None = None,
        topology: str | None = None,
    ) -> dict[str, Any]:
        """Find an address-to-bank mapping that is conflict-free for N concurrent accesses per
        stride: the plain modulo checked, the XOR-fold family searched exactly with z3 (the
        cheapest conflict-free fold, or a proof none exists), what IS feasible when the request
        is not, then non-linear mappings a local model proposes -- every one checked over the
        whole address space.

        Args:
            strides: Access strides in words, e.g. [1, 8, 16, 17].
            concurrent: N accesses issued together; at most `banks`.
            banks: B banks, a power of two.
            address_bits: The address space the guarantee must hold over.
            z3_seconds: Solver budget per attempt.
            llm_round: Mappings a local model may propose once the linear answer is known; 0
                for a solver-only run.
            max_xor_inputs: Hardware bound on address bits folded into one bank bit.
            problem: The requirement in words, for the model.
            db_path: Where the study records itself.
            crossbar: A staged crossbar in front of the banks, e.g. "4x2" for 8 banks: stage 1
                routes on the top bank bits into 4 groups, stage 2 into 2 banks per group.
                Every stage but the last is a conflict point checked and solved for.
            stage_capacities: Accesses one stage resource carries per cycle, one per stage.
            lanes: The input width of the first stage's crossbars. "7 4x4s feeding 4 7x8s" over
                32 banks is crossbar "4x8" with lanes 4: each 4x4 sees four consecutive accesses
                of the window and must send them to four different second-stage crossbars. The
                stage's capacity then binds within each chunk of `lanes`, not across the window.
            topology: The interconnect between requesters and banks, as a spec: "crossbar" (one
                full switch; the default), "staged:GxH" (a tree of crossbars; `lanes` and
                `stage_capacities` refine it), "omega" or "butterfly" (self-routed 2x2 switch
                networks, one conflict point per stage), "clos:n,m,r" (per-cycle routed,
                non-blocking when m >= n) or "benes" (non-blocking). Each becomes the stages the
                checker, z3 and the pigeonhole understand, plus a note on what it assumes.
            stages: Explicit sharing points, each {"bits": [bank-index bits], "capacity": c},
                for topologies a GxH layout cannot describe.

        Returns:
            The cheapest conflict-free mapping with its Verilog, every candidate with its
            verdict, and what the run established or could not.
        """
        return flux_bankmap_dse_loop(
            strides, concurrent, banks, address_bits=address_bits, z3_seconds=z3_seconds,
            llm_round=llm_round, max_xor_inputs=max_xor_inputs, problem=problem, db_path=db_path,
            crossbar=crossbar, stage_capacities=stage_capacities, lanes=lanes, stages=stages,
            topology=topology,
        )

    def nlu_dse_loop(
        self,
        db_path: str = "demo-nlu.db",
        ops: list[str] | None = None,
        ulp_budget: int = 1,
        op_steps: int = 24,
        test_rounds: int = 1,
        clock_period_ps: float = 1250.0,
        target_mhz: float | None = None,
        decide_on_finalists: int = 3,
        screen_only: bool = False,
        llm_model: str | None = None,
    ) -> dict[str, Any]:
        """Design an FP16 non-linear unit (exp, log, sigmoid, tanh, gelu, recip, rsqrt) and
        measure it: a local model chooses the computation method per operator (LUT, piecewise
        polynomial, interpolation, Newton-Raphson, CORDIC, ...), shared vs per-operator
        hardware, and combinational vs pipelined depth, and authors adversarial unit-test
        vectors; the gate is EXHAUSTIVE -- every operator within `ulp_budget` ULP of the FP16
        reference on all 65536 inputs, or refused with the failing inputs. Survivors are
        screened by yosys/STA and finalists placed by OpenROAD for PPA on ASAP7.

        Args:
            db_path: The campaign record; resume re-judges its designs, keeps its authored
                test suite, and reads its conclusions and refusals back.
            ops: Operator subset (default: all seven).
            ulp_budget: The correctness gate, in ULP over the whole FP16 domain.
            op_steps: Steps the loop may spend per pass; 0 re-judges the record with no model.
            test_rounds: Model test-author rounds adding adversarial vectors.
            clock_period_ps: What the synthesis and placement tools are constrained to.
            target_mhz: A demanded clock; the decision becomes the smallest area meeting
                it. None reports the knee of area/fmax/power instead.
            decide_on_finalists: Frontier points placed whole for PPA.
            screen_only: Skip placement; order by synthesis, quote nothing as PPA.
            llm_model: Ollama tag for the designer (default: the repo's local model).

        Returns:
            The decision with PPA, fmax and per-operator error rates, the measured
            frontier, refusals with reasons, and what the run established.
        """
        return flux_nlu_dse_loop(
            db_path, ops=ops, ulp_budget=ulp_budget, op_steps=op_steps,
            test_rounds=test_rounds, clock_period_ps=clock_period_ps,
            target_mhz=target_mhz, decide_on_finalists=decide_on_finalists,
            screen_only=screen_only, llm_model=llm_model,
        )

    def interconnect_mapping_dse_loop(
        self,
        seed: int = 0,
        ops: int = 8,
        climb_rounds: int = 40,
        llm_rounds: int = 0,
        llm_model: str | None = None,
        vu_probability: float = 0.7,
        dma_probability: float = 0.6,
    ) -> dict[str, Any]:
        """Run the banked-L1 conflict study: tensor storage modes vs 32 banks, map
        policies crossed with twelve interconnect topologies, judged on a four-cost
        Pareto with proofs (docs/decisions.md D378-D383).

        Returns every design point (pair name, train AND holdout latency/throughput,
        padding, area score), the Pareto front, certificates (proved by exhaustion or
        refuted with the exact counterexample), and refused hash proposals.

        Args:
            seed: Workload seed; train/holdout use disjoint ranges.
            ops: MU operations per workload.
            climb_rounds: XOR tap hill-climb rounds (injectivity-gated; 0 disables).
            llm_rounds: Model-proposed hash rounds via local Ollama (0 = model-free).
            llm_model: Ollama tag, or omit for the default local model.
            vu_probability: Chance VU traffic joins a system step (regime knob).
            dma_probability: Chance a DMA stream joins an operation (regime knob).
        """
        return flux_interconnect_mapping_dse_loop(
            seed, ops=ops, climb_rounds=climb_rounds, llm_rounds=llm_rounds,
            llm_model=llm_model, vu_probability=vu_probability,
            dma_probability=dma_probability)

    def omni_run(
        self,
        prompt: str,
        tools: list[str] | None = None,
        max_rounds: int = 6,
        max_calls: int = 16,
        wall_clock_budget_s: float | None = None,
        workdir: str = "/tmp/flux-omni-run",
        llm_model: str | None = None,
    ) -> dict[str, Any]:
        """Run the omni loop: a local model plans typed tool calls over the whole Flux
        catalog for `prompt`, a validator refuses what does not type-check, real tools
        execute, and the conclusion cites executed results (docs/decisions.md D377).

        The returned report includes executed steps with results, refusals with
        reasons, honest done/budget-stop status, and the provenance path -- that file
        replays deterministically with no model. Omni's own catalog excludes this
        tool, so a run can never recursively dispatch itself.

        Args:
            prompt: The task, in prose.
            tools: Restrict the offered catalog to these tool names; omit for all.
            max_rounds: Model rounds before an honest budget stop.
            max_calls: Executed tool calls before an honest budget stop.
            wall_clock_budget_s: Wall-clock budget; on expiry the model gets one
                conclude-only round over the evidence so far.
            workdir: Run directory for written files and provenance.
            llm_model: Ollama tag, or omit for the default local model.
        """
        return flux_omni_run(
            prompt, tools=tools, max_rounds=max_rounds, max_calls=max_calls,
            wall_clock_budget_s=wall_clock_budget_s, workdir=workdir,
            llm_model=llm_model)

    def macarray_dse_loop(
        self,
        db_path: str = "demo-macarray.db",
        workload: str | None = None,
        lanes: int = 8,
        accumulate: bool = True,
        target_mhz: float | None = 1000.0,
        preserve_fmax: bool = False,
        clock_period_ps: float | None = None,
        multipliers: list[str] | None = None,
        reducers: list[str] | None = None,
        pipelines: list[int] | None = None,
        mappings: list[str] | None = None,
        invent_rounds: int = 0,
        include_invented: bool = True,
        decide_on_finalists: int = 4,
        screen_only: bool = False,
        workers: int = 0,
        problem: str | None = None,
        llm_model: str | None = None,
        seed: int = 0,
    ) -> dict[str, Any]:
        """Search a MAC processing element's microarchitecture on real ASAP7 silicon numbers.

        The array is given: `lanes` products summed per cycle at the precision the workload IR
        declares. What is searched is inside the PE: how each product is formed (behavioral,
        shift-and-add array, radix-4 Booth, Wallace tree, plus any multiplier a model invented),
        how the products are reduced (adder chain, balanced tree, carry-save), and how deep the
        pipeline is (0-3 register stages). Every design is generated by code, verified by real
        Verilator against golden vectors (latency checked), screened by Yosys + OpenSTA, and the
        finalists along the fmax-vs-area frontier are placed by OpenROAD.

        Args:
            db_path: Where measurements accumulate; a resumed run re-measures nothing.
            workload: A Workload IR document (path); its einsum's precision sets the PE's.
                Default: the repo's int8 GEMM example.
            lanes: Products summed per cycle.
            accumulate: Whether the PE carries an accumulator input (acc = acc_in + sum).
            target_mhz: The clock the PE must make; the decision is the smallest PE that does.
                None: the fastest wins.
            preserve_fmax: Area is the objective and the incumbent's clock the floor: the target
                becomes the incumbent's own measured fmax on each stage and the decision is the
                smallest PE that holds it.
            clock_period_ps: What the tools are constrained to; default the target's period.
            multipliers, reducers, pipelines, mappings: Restrict the space. `mappings` is the
                technology mapper's recipe: "delay" (ABC given the clock period) or "area"
                (withheld); same RTL, different netlist.
            invent_rounds: Ask the local model for N new multiplier structures; each that
                passes its vectors is kept and joins the menu of every later run.
            include_invented: Offer the kept inventions.
            decide_on_finalists: How many designs, spread along the screened frontier, to place.
            screen_only: No placement; every number is then the synthesis screen's.
            workers: Tool processes in flight at once.
            problem: What is needed, in words, for the model.
            llm_model: Override the local model.
            seed: Golden-vector seed.

        Returns:
            The decision with fmax, area, power and latency; the incumbent (the behavioral
            multiply-and-sum every silicon number this repo pinned before was measured on);
            the fmax-vs-area frontier on the confirmed stage; what was refused and why.
        """
        return flux_macarray_dse_loop(
            db_path, workload=workload, lanes=lanes, accumulate=accumulate,
            target_mhz=target_mhz, preserve_fmax=preserve_fmax, clock_period_ps=clock_period_ps,
            multipliers=multipliers, reducers=reducers, pipelines=pipelines, mappings=mappings,
            invent_rounds=invent_rounds,
            include_invented=include_invented, decide_on_finalists=decide_on_finalists,
            screen_only=screen_only, workers=workers, problem=problem, llm_model=llm_model,
            seed=seed,
        )

    def champsim_run(
        self,
        config: dict[str, Any],
        trace: str,
        types: list[str] | None = None,
        warmup_instructions: int = 100_000_000,
        simulation_instructions: int = 150_000_000,
        binary: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Run one ChampSim simulation and report the IPC it measured.

        The unit the prefetcher search fans out. Useful on its own to measure a single
        configuration, or to establish a no-prefetcher baseline by passing an empty `types`.

        Args:
            config: The prefetcher configuration as plain values, e.g. {"region_size": 2048,
                "pattern_len": 32, ...}. An illegal combination aborts the simulator, so it is
                validated before anything runs.
            trace: Path to a ChampSim trace.
            types: L2 prefetchers to enable, e.g. ["bingo"]. An EMPTY list means no prefetcher,
                which is how the baseline is measured. Several may run at once.
            warmup_instructions: Instructions before measurement starts.
            simulation_instructions: Measured instructions.
            binary: The simulator; otherwise resolved from the environment.
            timeout_s: Give up after this long. A full run takes about six minutes.

        Returns:
            {"ipc", "cycles", "instructions", "wall_clock_s"}, or {"error"} if it could not run.
        """
        return flux_champsim_run(
            config, trace, types=types, warmup_instructions=warmup_instructions,
            simulation_instructions=simulation_instructions, binary=binary, timeout_s=timeout_s,
        )

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

    def generate_rtl_module(
        self,
        spec: dict[str, Any],
        model: str = default_local_model(),
        max_repair_attempts: int = 3,
    ) -> dict[str, Any]:
        """The Verilog sibling of `generate_systemc_module` (docs/decisions.md D44): generates a
        DUT module from a `DesignSpec` and verifies it through `generator/harness_rtl`'s real
        Verilator compile/VCD-trace/test-vector checker. Same arguments and contract as
        `generate_systemc_module` — see that method's docstring for the exact field meanings.
        """
        result = flux_generate_rtl_module(spec, model=model, max_repair_attempts=max_repair_attempts)
        return result.to_dict()

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
            corpus_root: path to the benchmarks corpus directory (`mentor/benchmarks`); the
                repo root for the entry's repo-relative `workload_path` is the nearest ancestor
                where that path exists.
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
                against; omit for this repo's own `mentor/knowledge/corpus/distributions/` (the same
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
        one caller-named numeric slot the way the removed single-axis searches did — real-verified
        against docs/history/roadmap.md's own Phase 3.5 exit criterion (docs/decisions.md D91): independent
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
                (default `"rtl"` — real Verilator simulation of `evaluator/rtl`'s own hand-written
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
        `generator/harness_rtl/src/flux_codegen_rtl_harness/asap7_pdk/PROVENANCE.md`) — a real,
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
                scope, matching evaluator/rtl's own).
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
        schedule is the one `evaluator/rtl`'s own hand-written `mac_array.sv` runs — same loop
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

        Refuses by default when `evaluator/rtl` can already measure the candidate: that residual
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
        family), `measured_point` (stage measurements per campaign), `observed_ratio` (the
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
        """The real, agent-facing redacted comparison docs/history/gap-analysis.md G15 is actually about
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
