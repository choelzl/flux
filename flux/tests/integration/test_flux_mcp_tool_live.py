"""`FluxTool` against a real local Ray instance, a real running uvicorn/MCP server, and a real
MCP client — not a local Python call dressed up as a tool call. Client-side pattern (`streamable_
http_client` + `mcp.ClientSession`) copied from CHIA's own `chia/base/tools/test/test_tool.py`,
which proves the same pattern against `BashTool`.

Requires the real `chia` package (see `flows/chia_nodes/README.md` for the submodule gotcha),
`flux-evaluator-zigzag` (real ZigZag, not mocked), and `flux-evaluator-rtl` (real Verilator)
installed alongside `flux-mcp`.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import flux_ir
import pytest

import _helpers
import ray
from flux_calibration import CalibrationStore
from flux_mcp import FluxTool
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _url(tool: FluxTool) -> str:
    return f"http://{tool.hostname}:{tool.port}/{tool.name}/mcp"


async def _wait_ready(url: str, retries: int = 30, delay: float = 0.5) -> None:
    for _ in range(retries):
        try:
            async with streamable_http_client(url) as (r, w, _):
                async with ClientSession(r, w) as sess:
                    await sess.initialize()
                    return
        except Exception:
            await asyncio.sleep(delay)
    raise RuntimeError(f"MCP server at {url} not ready after {retries} attempts")


async def _call_tool(url: str, method: str, args: dict) -> dict:
    """Call an MCP tool over the real wire protocol and return its structured result.

    FastMCP puts the tool's returned dict directly in `structuredContent` (no wrapper key) when
    the tool's return annotation is `dict[str, Any]` — checked empirically, not assumed from the
    `mcp` package's docs.
    """
    async with streamable_http_client(url) as (r, w, _):
        async with ClientSession(r, w) as sess:
            await sess.initialize()
            result = await sess.call_tool(method, arguments=args)
            assert not result.isError, f"tool call failed: {result.content}"
            return result.structuredContent


async def _call_tool_unwrapped(url: str, method: str, args: dict):
    """Same as `_call_tool`, but for tools whose return annotation is `list[...]` or `X | None`
    — FastMCP wraps those in a `{"result": ...}` envelope instead of returning the value directly
    (confirmed empirically, the same way the bare-dict case was: printed the raw response before
    relying on it).
    """
    async with streamable_http_client(url) as (r, w, _):
        async with ClientSession(r, w) as sess:
            await sess.initialize()
            result = await sess.call_tool(method, arguments=args)
            assert not result.isError, f"tool call failed: {result.content}"
            return result.structuredContent["result"]


@pytest.fixture(scope="module")
def flux_tool():
    if not ray.is_initialized():
        ray.init(log_to_driver=True)
    tool = FluxTool(_uid("flux"))
    asyncio.run(_wait_ready(_url(tool)))
    yield tool
    tool.stop()
    if ray.is_initialized():
        ray.shutdown()


def test_tool_registers_every_flux_node(flux_tool):
    """Every tool `setup()` registers is actually visible over the real MCP `list_tools` call —
    not just present as a Python method that happens to never reach the wire.

    UPDATED (docs/decisions.md D119). This assertion had been failing since D91/D92/D93/D100:
    four registered tools were never added to the list below. It went unnoticed because this file
    is an integration test, and the standard regression command runs `tests/unit/` and
    `tests/conformance/` only — so the check written specifically to catch a stale list was itself
    stale, and nothing ran it. The count is also gone from the test's own name: a number in a name
    is a fact that rots in the one place nobody re-reads. `tests/unit/test_mcp_surface_parity.py`
    now enforces the same node↔tool parity in the suite that actually runs on every change; this
    one keeps the hand-written list, which is what makes it an independent check rather than a
    restatement of the source.
    """
    async def _list() -> set[str]:
        async with streamable_http_client(_url(flux_tool)) as (r, w, _):
            async with ClientSession(r, w) as sess:
                await sess.initialize()
                tools = await sess.list_tools()
                return {t.name for t in tools.tools}

    names = asyncio.run(_list())
    assert names == {
        f"{flux_tool.name}_evaluate",
        f"{flux_tool.name}_search",
        f"{flux_tool.name}_calibrate",
        f"{flux_tool.name}_conformance_check",
        f"{flux_tool.name}_check_validity",
        f"{flux_tool.name}_knowledge_lookup",
        f"{flux_tool.name}_get_result",
        f"{flux_tool.name}_find_results",
        f"{flux_tool.name}_list_public_corpus",
        f"{flux_tool.name}_agentic_mapping_search",
        f"{flux_tool.name}_agentic_architecture_search",
        f"{flux_tool.name}_agentic_noc_search",
        f"{flux_tool.name}_agentic_memory_search",
        f"{flux_tool.name}_agentic_joint_search",
        f"{flux_tool.name}_agentic_dse_loop",
        f"{flux_tool.name}_agentic_multi_axis_dse",
        f"{flux_tool.name}_characterize_memory_level",
        f"{flux_tool.name}_generate_systemc_module",
        f"{flux_tool.name}_systemc_generate_dse",
        f"{flux_tool.name}_generate_rtl_module",
        f"{flux_tool.name}_rtl_generate_dse",
        f"{flux_tool.name}_compose_and_verify_rtl_design",
        f"{flux_tool.name}_synthesize_composite_rtl_design",
        f"{flux_tool.name}_compose_and_verify_systemc_design",
        f"{flux_tool.name}_leaderboard",
        f"{flux_tool.name}_sweep_dynamic_shape",
        f"{flux_tool.name}_sweep_moe_routing",
        f"{flux_tool.name}_generate_architecture_candidate",
        f"{flux_tool.name}_synthesize_with_asap7",
        f"{flux_tool.name}_synthesize_with_asap7_redacted",
        f"{flux_tool.name}_generate_rtl_for_architecture",
        f"{flux_tool.name}_generate_sequential_rtl_for_architecture",
        f"{flux_tool.name}_generate_gemm_rtl_for_architecture",
        f"{flux_tool.name}_calibrate_against_generated_rtl",
        f"{flux_tool.name}_backend_health",
        f"{flux_tool.name}_explain_candidate",
        f"{flux_tool.name}_protocol_lookup",
        f"{flux_tool.name}_list_protocols",
        f"{flux_tool.name}_check_ir_protocols",
        f"{flux_tool.name}_check_protocol_conformance",
        f"{flux_tool.name}_campaign_start",
        f"{flux_tool.name}_campaign_step",
        f"{flux_tool.name}_campaign_status",
        f"{flux_tool.name}_campaign_resume",
        f"{flux_tool.name}_campaign_stop",
        f"{flux_tool.name}_campaign_frontier",
    }


def test_evaluate_tool_call_runs_the_real_zigzag_backend(flux_tool):
    """Same pinned numbers test_chia_flux_evaluate_live.py checks against a direct in-process
    call — proving the MCP hop doesn't change the answer, just the transport.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_evaluate",
            {"backend": "zigzag", "workload": workload},
        )
    )
    assert result["metrics"]["latency_cycles"]["value"] == pytest.approx(145.0)
    assert result["provenance"]["evaluator"].startswith("zigzag@")
    assert result["validity"]["ok"] is True


def test_evaluate_tool_call_runs_the_real_thermal_backend_over_mcp(flux_tool):
    """`evaluators/thermal` (docs/decisions.md D64/D65) reached over the real MCP wire for the
    first time — verified in-process (direct adapter call, then the `flux_evaluate` CHIA node)
    when it was built, but never before through the actual wire protocol, closing a real gap in
    this repo's own "three surfaces, all verified" discipline. No dedicated CHIA node/MCP tool
    exists for thermal (D64's own design choice, matching gem5's precedent) — reached through the
    generic `flux_evaluate` tool with `backend="thermal"`, same as every other backend that has
    no dedicated tool of its own.
    """
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-thermal-v1.yaml")
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_evaluate",
            {
                "backend": "thermal", "workload": workload, "arch": arch,
                "metrics": ["avg_temp_c", "peak_temp_c"],
            },
        )
    )
    assert result["metrics"]["peak_temp_c"]["value"] == pytest.approx(29.673000000000002)
    assert result["metrics"]["avg_temp_c"]["value"] == pytest.approx(29.135000000000048)
    assert result["provenance"]["evaluator"] == "3d-ice@real"


def test_evaluate_tool_call_runs_the_real_chiplet_d2d_interconnect_over_mcp(flux_tool):
    """`evaluators/booksim`'s real chiplet inter-die (D2D) `anynet` path (docs/decisions.md
    D66/D67) reached over the real MCP wire for the first time — same real gap this closes for
    `evaluators/thermal` above, and the same reasoning: no dedicated tool (the plain KNCube NoC
    path is likewise only ever reached indirectly, via `agentic_noc_search`), so this goes through
    the generic `flux_evaluate` tool with `backend="booksim"`.
    """
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/chiplet-2die-noc-v1.yaml")
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_evaluate",
            {"backend": "booksim", "workload": workload, "arch": arch, "metrics": ["latency_cycles"]},
        )
    )
    assert result["metrics"]["latency_cycles"]["value"] > 0
    assert result["provenance"]["inputs"]["topology"] == "anynet-chiplet-2die-1link"
    assert result["provenance"]["evaluator"] == "booksim2@real"


def test_evaluate_tool_call_result_db_path_opts_into_warm_start_over_mcp(flux_tool, tmp_path):
    """`result_db_path` (docs/decisions.md D19) survives the MCP hop: two identical calls store
    exactly one row, not two — proving the second call was a real cache hit. Checked by inspecting
    the store directly rather than timing, since this module-scoped `flux_tool` server is already
    warmed up by earlier tests by the time this one runs, making a fresh-vs-cached wall-clock
    comparison unreliable here (tests/integration/test_chia_flux_evaluate_live.py's dedicated,
    freshly-started-process version of this same check uses timing safely).
    """
    from flux_store import ResultStore

    workload = flux_ir.load_document(GEMM_WORKLOAD)
    db_path = str(tmp_path / "cal.db")
    args = {"backend": "zigzag", "workload": workload, "result_db_path": db_path}

    first = asyncio.run(_call_tool(_url(flux_tool), f"{flux_tool.name}_evaluate", args))
    second = asyncio.run(_call_tool(_url(flux_tool), f"{flux_tool.name}_evaluate", args))

    assert first["metrics"]["latency_cycles"]["value"] == second["metrics"]["latency_cycles"]["value"]
    with ResultStore(db_path) as store:
        assert len(store.find_results(evaluator_prefix="zigzag")) == 1


def test_search_tool_call_runs_a_real_width_sweep_over_mcp(flux_tool):
    """search_kind='architecture_width' end to end through the wire: real ZigZag screening of
    three real candidate architectures, ranked, with a winner an MCP client can read straight off
    the returned dict (no dataclasses, no enums to unpack).
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    report = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_search",
            {
                "workload": workload,
                "base_arch": base_arch,
                "screening_backend": "zigzag",
                "search_kind": "architecture_width",
                "widths": [4, 8, 16],
                "parallel_screening": False,
            },
        )
    )
    assert len(report["swept"]) == 3
    assert report["winner"] is not None
    assert report["winner"]["width"] in (4, 8, 16)
    assert report["winner_screening_result"]["metrics"]["latency_cycles"]["value"] > 0


def test_calibrate_tool_call_widens_ci_from_real_seeded_residual_data(flux_tool, tmp_path):
    """Same real ZigZag-vs-RTL gap the direct-call integration test pins (1554 vs. 529 cycles for
    this workload/arch pair) — seeded through the store, then read back through the MCP hop.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    workload_hash = flux_ir.content_hash(workload)
    arch_hash = flux_ir.content_hash(arch)
    db_path = str(tmp_path / "cal.db")

    with CalibrationStore(db_path) as store:
        store.add_record(
            workload_hash=workload_hash, arch_hash=arch_hash, evaluator="zigzag@3.8.5",
            metric="latency_cycles", predicted_value=1554.0, reference_value=529.0,
            reference_source="rtl_sim",
        )

    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_calibrate",
            {
                "backend": "zigzag", "workload": workload, "arch": arch,
                "calibration_db_path": db_path,
            },
        )
    )
    estimate = result["metrics"]["latency_cycles"]
    assert estimate["ci_low"] < estimate["value"] < estimate["ci_high"]
    assert result["escalation"]["recommended"] is True


def test_conformance_check_tool_call_runs_real_zigzag_and_real_rtl(flux_tool, tmp_path):
    """The fourth tool, over the real wire: real ZigZag as the declared model, real Verilator RTL
    as the reference — no calibration data seeded, so this reproduces the honest "not yet
    conformant" result the direct-call integration test gets in the same uncalibrated case.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    report = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_conformance_check",
            {
                "workload": workload, "arch": arch,
                "declared_backend": "zigzag", "reference_backend": "rtl",
                "calibration_db_path": str(tmp_path / "cal.db"),
            },
        )
    )
    assert report["ok"] is False
    assert report["per_metric"]["latency_cycles"]["reference_value"] == pytest.approx(529.0)
    assert report["per_metric"]["latency_cycles"]["declared_value"] == pytest.approx(1554.0)


def test_check_validity_tool_call_merges_real_evaluator_and_independent_checks(flux_tool):
    """The fifth tool, over the real wire: real ZigZag's own self-report merged with
    flux_validity's independent constraints/roofline checks — same real 1554-cycle number, now
    with checker_version naming every check that actually ran.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_check_validity",
            {"backend": "zigzag", "workload": workload, "arch": arch},
        )
    )
    assert result["metrics"]["latency_cycles"]["value"] == pytest.approx(1554.0)
    assert result["validity"]["ok"] is True
    assert "roofline-v0.1:lower_bound=512.0" in result["validity"]["checker_version"]
    assert "constraints-v0.1:checked=0/2" in result["validity"]["checker_version"]


def test_knowledge_lookup_tool_call_hits_the_real_riscv_corpus(flux_tool):
    results = asyncio.run(
        _call_tool_unwrapped(
            _url(flux_tool), f"{flux_tool.name}_knowledge_lookup",
            {"query": "fence instruction memory ordering", "standard_id": "riscv-unpriv", "k": 2},
        )
    )
    assert len(results) > 0
    assert all(r["chunk"]["standard_id"] == "riscv-unpriv" for r in results)


def test_get_result_and_find_results_tool_calls_round_trip_a_real_evaluation(flux_tool, tmp_path):
    from flux_store import ResultStore

    workload = flux_ir.load_document(GEMM_WORKLOAD)
    db_path = str(tmp_path / "flux.db")
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_evaluate",
            {"backend": "zigzag", "workload": workload},
        )
    )
    # Store it directly (there's no "put" tool — storing is the search/generation loop's job, not
    # something an agent needs write access to via this read-only surface) then read it back
    # through the MCP tools.
    from flux_evaluator_abi import Result

    with ResultStore(db_path) as store:
        workload_hash = store.put_document("workload", workload)
        result_id = store.put_result(_result_from_dict(result), workload_hash=workload_hash)

    fetched = asyncio.run(
        _call_tool_unwrapped(
            _url(flux_tool), f"{flux_tool.name}_get_result",
            {"db_path": db_path, "result_id": result_id},
        )
    )
    assert fetched["result"]["metrics"]["latency_cycles"]["value"] == pytest.approx(145.0)

    found = asyncio.run(
        _call_tool_unwrapped(
            _url(flux_tool), f"{flux_tool.name}_find_results",
            {"db_path": db_path, "workload_hash": workload_hash},
        )
    )
    assert len(found) == 1


def _result_from_dict(d: dict):
    """Reconstruct a typed `Result` from the plain dict an MCP tool call returns — `ResultStore.
    put_result` needs the real dataclass (it reads `.provenance.evaluator`), an MCP client only
    ever sees the dict form.
    """
    from flux_evaluator_abi import (
        Bottleneck, Domain, Escalation, Estimate, Limiter, Method, Provenance, Result, Validity,
    )

    return Result(
        metrics={
            name: Estimate(
                value=m["value"], ci_low=m["ci_low"], ci_high=m["ci_high"], unit=m["unit"],
                method=Method(m["method"]),
            )
            for name, m in d["metrics"].items()
        },
        validity=Validity(ok=d["validity"]["ok"], checker_version=d["validity"]["checker_version"]),
        domain=Domain(in_domain=d["domain"]["in_domain"]),
        bottleneck=Bottleneck(limiter=Limiter(d["bottleneck"]["limiter"])),
        provenance=Provenance(evaluator=d["provenance"]["evaluator"], inputs=d["provenance"]["inputs"]),
        escalation=Escalation(recommended=d["escalation"]["recommended"]),
    )


def test_list_public_corpus_tool_call_matches_the_real_corpus_and_excludes_holdout(flux_tool):
    """UPDATED (docs/decisions.md D129). This list went stale when a seventh public entry was
    added, exactly like the copy in `test_chia_flux_knowledge_and_store_live.py` — and fixing
    that one in D123 did not fix this one, because nothing connects them. The holdout entry is
    correctly absent in both; the *invariant* is enforced in `tests/unit/test_corpus_holdout_real.py`
    against the real corpus on every run, which is the check that matters. These enumerations
    remain deliberately hand-written and therefore deliberately duplicated: each is an
    independent statement of what should exist, and the cost of that independence is that both
    have to be maintained.
    """
    # Absolute path, not "corpus" — the MCP server runs inside a Ray actor process whose cwd
    # cannot be assumed to match the test runner's.
    entries = asyncio.run(
        _call_tool_unwrapped(
            _url(flux_tool), f"{flux_tool.name}_list_public_corpus",
            {"corpus_root": str(FLUX_ROOT / "mentor" / "benchmarks")},
        )
    )
    ids = {e["id"] for e in entries}
    assert ids == {
        "mlp-gemm0-simple-npu-1d-v1",
        "mlp-gemm0-simple-npu-1d-v2",
        "mlp-gemm0-simple-npu-1d-v3",
        "mlp-gemm0-simple-npu-1d-gbuf1p25kb",  # docs/decisions.md D58: a second benchmark family
        "mlp-gemm0-simple-npu-1d-gbuf64kb",
        "mlp-gemm0-simple-npu-1d-dual-core-v1",  # the multi-core (Stream) entry
        "mlp-ffn0-simple-npu-1d-v1",  # docs/decisions.md D59: a real second workload
    }
    assert "mlp-gemm0-simple-npu-1d-v4" not in ids


@_helpers.requires_ollama
def test_agentic_architecture_search_tool_call_finds_the_proven_optimum_over_mcp(flux_tool):
    """The tenth tool, over the real wire: real Ollama (docs/decisions.md D9) proposing
    candidates, real ZigZag evaluating them, dispatched as a real CHIA node (D17) and read back
    through a real MCP client — same 263.0-cycle/width=32 optimum
    test_chia_flux_agentic_search_live.py already proves in-process and via `.chia_remote()`.
    max_iterations=4 covers the full 4-candidate width space, so the result is deterministic
    regardless of what the LLM actually proposes (the fallback-to-unvisited mechanism guarantees
    every candidate gets visited).
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    report = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_agentic_architecture_search",
            {
                "backend": "zigzag", "workload": workload, "base_arch": base_arch,
                "valid_widths": [4, 8, 16, 32], "max_iterations": 4, "seed": 0,
            },
        )
    )
    assert report["iterations"] == 4
    assert report["best"]["width"] == 32
    assert report["best_result"]["metrics"]["latency_cycles"]["value"] == pytest.approx(263.0)


@_helpers.requires_ollama
def test_agentic_memory_search_tool_call_finds_the_proven_optimum_over_mcp(flux_tool):
    """The fourteenth tool (docs/decisions.md D26/D27), over the real wire: real Ollama proposing
    gbuf sizes, real ZigZag evaluating them, dispatched as a real CHIA node and read back through
    a real MCP client — same 1.25-KiB/energy-1116618.0081255918-pJ optimum
    test_chia_flux_agentic_search_live.py already proves in-process. max_iterations=4 covers the
    full 4-candidate size space, so the result is deterministic regardless of what the LLM
    actually proposes.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    report = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_agentic_memory_search",
            {
                "backend": "zigzag", "workload": workload, "base_arch": base_arch,
                "level": "gbuf", "valid_sizes_kb": [1.0, 1.25, 2.0, 64.0],
                "max_iterations": 4, "seed": 0,
            },
        )
    )
    assert report["iterations"] == 4
    assert report["skipped_infeasible"] == 1
    assert report["best"]["size_kb"] == 1.25
    assert report["best_result"]["metrics"]["energy_pj"]["value"] == pytest.approx(1116618.0081255918)


@_helpers.requires_ollama
def test_agentic_joint_search_tool_call_finds_the_proven_optimum_over_mcp(flux_tool):
    """The fifteenth tool (docs/decisions.md D26/D28), over the real wire: real Ollama proposing
    (width, size_kb) pairs, real ZigZag evaluating them, dispatched as a real CHIA node and read
    back through a real MCP client — same width=32/size_kb=1.25/energy-193018.0081255918-pJ
    optimum test_chia_flux_agentic_search_live.py already proves in-process. max_iterations=6
    covers the full 2x3 grid, so the result is deterministic regardless of what the LLM actually
    proposes.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    report = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_agentic_joint_search",
            {
                "backend": "zigzag", "workload": workload, "base_arch": base_arch,
                "level": "gbuf", "valid_widths": [4, 32], "valid_sizes_kb": [1.0, 1.25, 64.0],
                "max_iterations": 6, "seed": 0,
            },
        )
    )
    assert report["iterations"] == 6
    assert report["skipped_infeasible"] == 2
    assert report["best"]["width"] == 32
    assert report["best"]["size_kb"] == 1.25
    assert report["best_result"]["metrics"]["energy_pj"]["value"] == pytest.approx(193018.0081255918)


@_helpers.requires_ollama
def test_agentic_dse_loop_tool_call_meets_every_phase4_exit_criterion_clause_over_mcp(
    flux_tool, tmp_path
):
    """The reference loop docs/roadmap.md Phase 4 names as its exit criterion (docs/decisions.md
    D18), as a single real MCP tool call — the same four clauses
    test_chia_flux_agentic_dse_loop_live.py checks in-process, now proven to survive the MCP wire
    hop and its dict-in/dict-out serialization.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    report = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_agentic_dse_loop",
            {
                "workload": workload, "base_arch": base_arch, "screening_backend": "zigzag",
                "valid_widths": [4, 8, 16, 32], "baseline_width": 8,
                "max_iterations": 4, "seed": 0,
                "calibration_db_path": str(tmp_path / "cal.db"),
                "result_db_path": str(tmp_path / "results.db"),
            },
        )
    )
    assert report["winner_candidate"]["width"] == 32
    assert report["baseline_candidate"]["width"] == 8
    assert report["beats_baseline"] is True
    assert report["validity"]["validity"]["ok"] is True
    assert report["conformance"]["ok"] is False  # empty calibration store, honestly reported
    assert report["replay"]["matched"] is True
    assert report["estimated_cost_usd"] == 0.0


@_helpers.requires_ollama
def test_agentic_dse_loop_tool_call_joint_axis_over_mcp(flux_tool, tmp_path):
    """`axis="joint"` (docs/decisions.md D26/D28/D29) over the real MCP wire — the fifth and last
    axis this reference loop covers, verified the same way the architecture-width run above is:
    same width=32/size_kb=1.25/193018.0081255918-pJ optimum
    test_chia_flux_agentic_dse_loop_live.py already proves in-process, with real Timeloop
    conformance (honestly `ok=False` on this test's empty calibration store).
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    report = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_agentic_dse_loop",
            {
                "workload": workload, "base_arch": base_arch, "screening_backend": "zigzag",
                "axis": "joint", "reference_backend": "timeloop", "metric": "energy_pj",
                "memory_level": "gbuf", "valid_widths": [4, 32],
                "valid_sizes_kb": [1.0, 1.25, 64.0], "baseline_pair_index": 5,
                "max_iterations": 6, "seed": 0,
                "calibration_db_path": str(tmp_path / "cal.db"),
                "result_db_path": str(tmp_path / "results.db"),
            },
        )
    )
    assert report["winner_candidate"]["width"] == 32
    assert report["winner_candidate"]["size_kb"] == 1.25
    assert report["winner_value"] == pytest.approx(193018.0081255918)
    assert report["baseline_candidate"]["width"] == 32
    assert report["baseline_candidate"]["size_kb"] == 64.0
    assert report["beats_baseline"] is True
    assert report["validity"]["validity"]["ok"] is True
    assert report["conformance"]["ok"] is False  # empty calibration store, honestly reported
    assert report["replay"]["matched"] is True
    assert report["estimated_cost_usd"] == 0.0


def test_characterize_memory_level_tool_call_runs_real_cacti_over_mcp(flux_tool):
    """The seventeenth tool (docs/decisions.md D36/D37), over the real wire: extracts `gbuf`
    from a real, multi-level architecture and characterizes it through real CACTI, read back
    through a real MCP client — same pinned numbers
    test_chia_flux_characterize_memory_level_live.py already proves in-process.
    """
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_characterize_memory_level",
            {"arch": base_arch, "level": "gbuf", "word_width_bits": 128},
        )
    )
    assert result["metrics"]["area_mm2"]["value"] == pytest.approx(0.527745648101, rel=1e-6)
    assert result["metrics"]["energy_pj"]["value"] == pytest.approx(88.4356, rel=1e-6)
    assert result["metrics"]["power_w"]["value"] == pytest.approx(0.206906, rel=1e-6)
    assert result["provenance"]["evaluator"] == "cacti7@real"


@_helpers.requires_ollama
def test_generate_systemc_module_tool_call_runs_real_ollama_and_gpp_over_mcp(flux_tool):
    """The eighteenth tool (docs/decisions.md D40), over the real wire: a real Ollama call plus
    real g++/SystemC compilation and verification, read back through a real MCP client."""
    spec = {
        "module_name": "Adder2",
        "ports": [
            {"name": "a", "dir": "in", "dtype": "int"},
            {"name": "b", "dir": "in", "dtype": "int"},
            {"name": "sum", "dir": "out", "dtype": "int"},
        ],
        "behavior": "combinational: sum = a + b",
        "test_vectors": [{"inputs": {"a": 3, "b": 4}, "expected": {"sum": 7}}],
    }
    result = asyncio.run(
        _call_tool(_url(flux_tool), f"{flux_tool.name}_generate_systemc_module", {"spec": spec})
    )
    assert result["success"] is True
    assert result["harness_result"]["all_passed"] is True
    assert result["harness_result"]["vcd_path"] is None  # keep_workdir defaults False in the harness


@_helpers.requires_ollama
def test_systemc_generate_dse_tool_call_dispatches_real_concurrent_variants_over_mcp(flux_tool):
    """The nineteenth tool (docs/decisions.md D41), over the real wire: two real design variants
    generated and verified as real concurrent Ray tasks, read back through a real MCP client."""
    variant_specs = [
        {
            "module_name": "Adder2",
            "id": "adder2-mcp-variant",
            "ports": [
                {"name": "a", "dir": "in", "dtype": "int"},
                {"name": "b", "dir": "in", "dtype": "int"},
                {"name": "sum", "dir": "out", "dtype": "int"},
            ],
            "behavior": "combinational: sum = a + b",
            "test_vectors": [{"inputs": {"a": 3, "b": 4}, "expected": {"sum": 7}}],
        },
        {
            "module_name": "Inverter",
            "id": "inverter-mcp-variant",
            "ports": [
                {"name": "x", "dir": "in", "dtype": "bool"},
                {"name": "y", "dir": "out", "dtype": "bool"},
            ],
            "behavior": "combinational: y = logical NOT of x",
            "test_vectors": [{"inputs": {"x": True}, "expected": {"y": False}}],
        },
    ]
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_systemc_generate_dse",
            {"variant_specs": variant_specs},
        )
    )
    assert result["all_valid"] is True
    assert set(result["valid_variant_ids"]) == {"adder2-mcp-variant", "inverter-mcp-variant"}
    assert result["dispatch_wall_clock_s"] > 0


@_helpers.requires_ollama
def test_generate_rtl_module_tool_call_runs_real_ollama_and_verilator_over_mcp(flux_tool):
    """The twentieth tool (docs/decisions.md D44), over the real wire: a real Ollama call plus
    real Verilator compilation and verification, read back through a real MCP client."""
    spec = {
        "module_name": "Adder2",
        "ports": [
            {"name": "a", "dir": "in", "dtype": "int"},
            {"name": "b", "dir": "in", "dtype": "int"},
            {"name": "sum", "dir": "out", "dtype": "int"},
        ],
        "behavior": "combinational: sum = a + b",
        "test_vectors": [{"inputs": {"a": 3, "b": 4}, "expected": {"sum": 7}}],
    }
    result = asyncio.run(
        _call_tool(_url(flux_tool), f"{flux_tool.name}_generate_rtl_module", {"spec": spec})
    )
    assert result["success"] is True
    assert result["harness_result"]["all_passed"] is True


@_helpers.requires_ollama
def test_rtl_generate_dse_tool_call_dispatches_real_concurrent_variants_over_mcp(flux_tool):
    """The twenty-first tool (docs/decisions.md D45), over the real wire: two real design
    variants generated and verified as real concurrent Ray tasks, read back through a real MCP
    client."""
    variant_specs = [
        {
            "module_name": "Adder2",
            "id": "adder2-rtl-mcp-variant",
            "ports": [
                {"name": "a", "dir": "in", "dtype": "int"},
                {"name": "b", "dir": "in", "dtype": "int"},
                {"name": "sum", "dir": "out", "dtype": "int"},
            ],
            "behavior": "combinational: sum = a + b",
            "test_vectors": [{"inputs": {"a": 3, "b": 4}, "expected": {"sum": 7}}],
        },
        {
            "module_name": "Inverter",
            "id": "inverter-rtl-mcp-variant",
            "ports": [
                {"name": "x", "dir": "in", "dtype": "bool"},
                {"name": "y", "dir": "out", "dtype": "bool"},
            ],
            "behavior": "combinational: y = logical NOT of x",
            "test_vectors": [{"inputs": {"x": True}, "expected": {"y": False}}],
        },
    ]
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_rtl_generate_dse",
            {"variant_specs": variant_specs},
        )
    )
    assert result["all_valid"] is True
    assert set(result["valid_variant_ids"]) == {"adder2-rtl-mcp-variant", "inverter-rtl-mcp-variant"}
    assert result["dispatch_wall_clock_s"] > 0
    # docs/decisions.md D47: real Yosys synthesis results, read back over the wire, not just the
    # pass/fail shape D45 originally shipped.
    assert set(result["synthesis_results"]) == {"adder2-rtl-mcp-variant", "inverter-rtl-mcp-variant"}
    assert result["synthesis_results"]["inverter-rtl-mcp-variant"]["total_cells"] > 0
    assert result["smallest_valid_variant_id"] == "inverter-rtl-mcp-variant"


def test_compose_and_verify_rtl_design_tool_call_wires_real_modules_over_mcp(flux_tool):
    """The twenty-second tool (docs/decisions.md D48), over the real wire: two already-verified
    Adder2 leaf instances composed into a real Adder3, verified end-to-end through real
    Verilator, read back through a real MCP client."""
    adder_spec_doc = {
        "module_name": "Adder2",
        "ports": [
            {"name": "a", "dir": "in", "dtype": "int"},
            {"name": "b", "dir": "in", "dtype": "int"},
            {"name": "sum", "dir": "out", "dtype": "int"},
        ],
        "behavior": "combinational: sum = a + b",
        "test_vectors": [{"inputs": {"a": 1, "b": 1}, "expected": {"sum": 2}}],
    }
    adder_source = """
module Adder2 (
    input  logic signed [31:0] a,
    input  logic signed [31:0] b,
    output logic signed [31:0] sum
);
    assign sum = a + b;
endmodule
"""
    composition_spec_doc = {
        "top_module_name": "Adder3",
        "instances": [
            {"module_name": "Adder2", "instance_name": "add1"},
            {"module_name": "Adder2", "instance_name": "add2"},
        ],
        "nets": {
            "add1": {"a": "x", "b": "y", "sum": "partial"},
            "add2": {"a": "partial", "b": "z", "sum": "total"},
        },
        "ports": [
            {"name": "x", "dir": "in", "dtype": "int"},
            {"name": "y", "dir": "in", "dtype": "int"},
            {"name": "z", "dir": "in", "dtype": "int"},
            {"name": "total", "dir": "out", "dtype": "int"},
        ],
        "test_vectors": [{"inputs": {"x": 3, "y": 4, "z": 5}, "expected": {"total": 12}}],
    }
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_compose_and_verify_rtl_design",
            {
                "leaf_spec_docs": {"Adder2": adder_spec_doc},
                "leaf_sources": {"Adder2": adder_source},
                "composition_spec_doc": composition_spec_doc,
            },
        )
    )
    assert result["all_passed"] is True
    assert result["total_vectors"] == 1
    assert result["passed_vectors"] == 1


def test_synthesize_composite_rtl_design_tool_call_runs_real_yosys_over_mcp(flux_tool):
    """The twenty-third tool (docs/decisions.md D52), over the real wire: real Yosys synthesis
    of a composed design, read back through a real MCP client."""
    adder_spec_doc = {
        "module_name": "Adder2",
        "ports": [
            {"name": "a", "dir": "in", "dtype": "int"},
            {"name": "b", "dir": "in", "dtype": "int"},
            {"name": "sum", "dir": "out", "dtype": "int"},
        ],
        "behavior": "combinational: sum = a + b",
        "test_vectors": [{"inputs": {"a": 1, "b": 1}, "expected": {"sum": 2}}],
    }
    adder_source = """
module Adder2 (
    input  logic signed [31:0] a,
    input  logic signed [31:0] b,
    output logic signed [31:0] sum
);
    assign sum = a + b;
endmodule
"""
    composition_spec_doc = {
        "top_module_name": "Adder3",
        "instances": [
            {"module_name": "Adder2", "instance_name": "add1"},
            {"module_name": "Adder2", "instance_name": "add2"},
        ],
        "nets": {
            "add1": {"a": "x", "b": "y", "sum": "partial"},
            "add2": {"a": "partial", "b": "z", "sum": "total"},
        },
        "ports": [
            {"name": "x", "dir": "in", "dtype": "int"},
            {"name": "y", "dir": "in", "dtype": "int"},
            {"name": "z", "dir": "in", "dtype": "int"},
            {"name": "total", "dir": "out", "dtype": "int"},
        ],
        "test_vectors": [{"inputs": {"x": 1, "y": 1, "z": 1}, "expected": {"total": 3}}],
    }
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_synthesize_composite_rtl_design",
            {
                "leaf_spec_docs": {"Adder2": adder_spec_doc},
                "leaf_sources": {"Adder2": adder_source},
                "composition_spec_doc": composition_spec_doc,
            },
        )
    )
    assert result["total_cells"] > 0
    assert sum(result["cells_by_type"].values()) == result["total_cells"]


def test_compose_and_verify_systemc_design_tool_call_wires_real_modules_over_mcp(flux_tool):
    """The twenty-fourth tool (docs/decisions.md D55), over the real wire: two already-verified
    Adder2 leaf instances composed into a real Adder3 SystemC composite, verified end-to-end
    through real g++/SystemC, read back through a real MCP client."""
    adder_spec_doc = {
        "module_name": "Adder2",
        "ports": [
            {"name": "a", "dir": "in", "dtype": "int"},
            {"name": "b", "dir": "in", "dtype": "int"},
            {"name": "sum", "dir": "out", "dtype": "int"},
        ],
        "behavior": "combinational: sum = a + b",
        "test_vectors": [{"inputs": {"a": 1, "b": 1}, "expected": {"sum": 2}}],
    }
    adder_source = """
SC_MODULE(Adder2) {
    sc_in<int> a;
    sc_in<int> b;
    sc_out<int> sum;

    void add() { sum.write(a.read() + b.read()); }

    SC_CTOR(Adder2) {
        SC_METHOD(add);
        sensitive << a << b;
    }
};
"""
    composition_spec_doc = {
        "top_module_name": "Adder3",
        "instances": [
            {"module_name": "Adder2", "instance_name": "add1"},
            {"module_name": "Adder2", "instance_name": "add2"},
        ],
        "nets": {
            "add1": {"a": "x", "b": "y", "sum": "partial"},
            "add2": {"a": "partial", "b": "z", "sum": "total"},
        },
        "ports": [
            {"name": "x", "dir": "in", "dtype": "int"},
            {"name": "y", "dir": "in", "dtype": "int"},
            {"name": "z", "dir": "in", "dtype": "int"},
            {"name": "total", "dir": "out", "dtype": "int"},
        ],
        "test_vectors": [{"inputs": {"x": 3, "y": 4, "z": 5}, "expected": {"total": 12}}],
    }
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_compose_and_verify_systemc_design",
            {
                "leaf_spec_docs": {"Adder2": adder_spec_doc},
                "leaf_sources": {"Adder2": adder_source},
                "composition_spec_doc": composition_spec_doc,
            },
        )
    )
    assert result["all_passed"] is True
    assert result["total_vectors"] == 1
    assert result["passed_vectors"] == 1


def test_leaderboard_tool_call_ranks_a_real_evaluate_tool_result_over_mcp(flux_tool, tmp_path):
    """The twenty-fifth tool (docs/decisions.md D58), over the real wire, chained with the real
    `evaluate` tool: a real ZigZag evaluation of corpus entry v1's own architecture, persisted via
    `result_db_path`, then ranked by `leaderboard` for that same entry — a real, self-consistent
    check (the leaderboard's rank-1 value must match what `evaluate` itself just returned) rather
    than a hardcoded pinned number, since this test doesn't control which other real architectures
    might already be evaluated and stored in this shared module-scoped server's history.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    db_path = str(tmp_path / "leaderboard.db")

    evaluated = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_evaluate",
            {"backend": "zigzag", "workload": workload, "arch": arch, "result_db_path": db_path},
        )
    )
    standings = asyncio.run(
        _call_tool_unwrapped(
            _url(flux_tool), f"{flux_tool.name}_leaderboard",
            {
                "corpus_root": str(FLUX_ROOT / "mentor" / "benchmarks"),
                "entry_id": "mlp-gemm0-simple-npu-1d-v1",
                "db_path": db_path,
            },
        )
    )
    assert len(standings) >= 1
    assert any(
        s["value"] == pytest.approx(evaluated["metrics"]["latency_cycles"]["value"])
        for s in standings
    )
    assert standings[0]["rank"] == 1


def test_leaderboard_tool_call_rejects_a_holdout_entry_id_over_mcp(flux_tool):
    """Holdout-safe over the real wire too, not just in-process: the tool's own `entry_id` lookup
    goes through `public_entries()` only, so naming the real holdout entry's id is rejected — this
    call must fail, not silently succeed. The exact `ValueError` message (checked directly at the
    CHIA-node level by test_leaderboard_chia_node_rejects_a_holdout_entry_id in
    tests/integration/test_leaderboard_live.py) doesn't reliably survive this transport's own
    exception-group wrapping for a raised-not-returned error, so this level only checks that
    *some* real failure surfaces, matching this file's own precedent for transport-level errors
    (test_stop_makes_the_server_unreachable).
    """
    with pytest.raises(Exception):
        asyncio.run(
            _call_tool(
                _url(flux_tool), f"{flux_tool.name}_leaderboard",
                {
                    "corpus_root": str(FLUX_ROOT / "mentor" / "benchmarks"),
                    "entry_id": "mlp-gemm0-simple-npu-1d-v4",
                    "db_path": ":memory:",
                },
            )
        )


def test_sweep_dynamic_shape_tool_call_aggregates_real_zigzag_samples_over_mcp(flux_tool):
    """The twenty-sixth tool (docs/decisions.md D63), over the real wire: a real, single-token
    LLM decode-mode attention QK^T workload (T, the KV-cache length, genuinely dynamic) swept
    across four real sample points through real ZigZag, aggregated — pinned against the exact
    same independently-verified numbers docs/decisions.md D63's own decision record cites.
    """
    workload = flux_ir.load_document(FLUX_ROOT / "core/ir/workload/examples/llm-decode-attn-qk0.yaml")
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_sweep_dynamic_shape",
            {
                "backend": "zigzag",
                "workload": workload,
                "op_id": "attn.qk",
                "dim": "T",
                "sample_points": [1, 8, 32, 128],
                "arch": arch,
                "metric": "latency_cycles",
            },
        )
    )
    real_values = [31.0, 200.0, 782.0, 3110.0]
    assert result["metrics"]["latency_cycles"]["value"] == pytest.approx(sum(real_values) / len(real_values))
    assert result["metrics"]["latency_cycles"]["ci_low"] == pytest.approx(min(real_values))
    assert result["metrics"]["latency_cycles"]["ci_high"] == pytest.approx(max(real_values))
    assert result["provenance"]["inputs"]["sample_points"] == [1, 8, 32, 128]


def test_sweep_moe_routing_tool_call_aggregates_real_zigzag_samples_over_mcp(flux_tool):
    """The twenty-seventh tool (docs/decisions.md D68), over the real wire: a real 8-expert MoE
    FFN block, swept across three real top-2 routing samples through real ZigZag, aggregated —
    pinned against the exact same independently-verified numbers docs/decisions.md D68's own
    decision record cites.
    """
    workload = flux_ir.load_document(FLUX_ROOT / "core/ir/workload/examples/moe-ffn-8experts-top2-v1.yaml")
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    routing_samples = [
        ["expert0.ffn", "expert1.ffn"],
        ["expert6.ffn", "expert7.ffn"],
        ["expert0.ffn", "expert7.ffn"],
    ]
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_sweep_moe_routing",
            {
                "backend": "zigzag",
                "workload": workload,
                "op_id": "moe.route",
                "routing_samples": routing_samples,
                "arch": arch,
                "metric": "latency_cycles",
            },
        )
    )
    real_values = [494.0, 1649.0, 1072.0]
    assert result["metrics"]["latency_cycles"]["value"] == pytest.approx(sum(real_values) / len(real_values))
    assert result["metrics"]["latency_cycles"]["ci_low"] == pytest.approx(min(real_values))
    assert result["metrics"]["latency_cycles"]["ci_high"] == pytest.approx(max(real_values))
    assert result["provenance"]["inputs"]["routing_samples"] == routing_samples


@_helpers.requires_ollama
def test_generate_architecture_candidate_tool_call_produces_a_real_verified_candidate_over_mcp(flux_tool):
    """The twenty-eighth tool (docs/decisions.md D91): a real local Ollama model proposes a whole
    new Architecture IR document over the real MCP wire, real-verified against docs/roadmap.md's
    own Phase 3.5 exit criterion — independent validity, RTL conformance (or a real, honest
    conformance_error), and deterministic replay.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_generate_architecture_candidate",
            {
                "workload": workload,
                "base_arch": base_arch,
                "objective_metric": "latency_cycles",
            },
        )
    )
    assert result["success"] is True
    assert result["final_arch"] is not None
    assert result["declared_result"]["metrics"]["latency_cycles"]["value"] > 0
    assert result["replay_matched"] is True
    assert (result["conformance"] is not None) != (result["conformance_error"] is not None)


def test_synthesize_with_asap7_tool_call_reports_a_real_pdk_area_over_mcp(flux_tool):
    """The twenty-ninth tool (docs/decisions.md D92): real ASIC synthesis against a real,
    vendored ASAP7 liberty library over the real MCP wire — pinned against the exact real number
    verified by hand before this test was written.
    """
    adder_source = """
module Adder2 (
    input  logic signed [31:0] a,
    input  logic signed [31:0] b,
    output logic signed [31:0] sum
);
    assign sum = a + b;
endmodule
"""
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_synthesize_with_asap7",
            {"module_source": adder_source, "module_name": "Adder2"},
        )
    )
    assert result["area_um2"] == pytest.approx(12.655440)
    assert result["sequential_area_um2"] == pytest.approx(0.0)


def test_synthesize_with_asap7_redacted_tool_call_never_exposes_the_real_area_over_mcp(flux_tool):
    """The thirtieth tool (docs/decisions.md D93, docs/gap-analysis.md G15): the real,
    agent-facing redacted comparison over the real MCP wire — the actual surface this whole gap
    is about, since an MCP tool response is exactly what would reach a real agent's own context.
    """
    adder_source = """
module Adder2 (
    input  logic signed [31:0] a,
    input  logic signed [31:0] b,
    output logic signed [31:0] sum
);
    assign sum = a + b;
endmodule
"""
    subtractor_source = adder_source.replace("a + b", "a - b").replace("Adder2", "Subtractor2")
    result = asyncio.run(
        _call_tool(
            _url(flux_tool), f"{flux_tool.name}_synthesize_with_asap7_redacted",
            {
                "module_source": subtractor_source, "module_name": "Subtractor2",
                "baseline_module_source": adder_source, "baseline_module_name": "Adder2",
            },
        )
    )
    assert set(result.keys()) == {"area", "sequential_fraction"}
    assert set(result["area"].keys()) == {"relative_delta", "better_than_baseline"}
    assert isinstance(result["area"]["relative_delta"], float)
    # A real, direct check over the real JSON-over-the-wire response: the real, independently
    # known absolute area (12.655440, from the un-redacted tool call above) never appears.
    assert 12.655440 not in result["area"].values()


def test_stop_makes_the_server_unreachable():
    """Independent tool instance (not the module-scoped fixture) so this test's stop() doesn't
    tear down the server the other tests in this module still need.
    """
    if not ray.is_initialized():
        ray.init(log_to_driver=True)
    tool = FluxTool(_uid("flux-lifecycle"))
    url = _url(tool)
    asyncio.run(_wait_ready(url))

    tool.stop()

    import time
    time.sleep(1)
    with pytest.raises(Exception):
        asyncio.run(
            _call_tool(url, f"{tool.name}_evaluate", {"backend": "zigzag", "workload": {}})
        )


def test_campaign_lifecycle_over_the_real_wire(flux_tool, tmp_path):
    """start -> step -> status -> frontier -> stop -> refused double-stop, all through the real
    MCP protocol against real ZigZag (docs/decisions.md D216-D220). The payloads must be
    JSON-safe end to end — the wire is what proves it."""
    import yaml

    root = Path(__file__).resolve().parents[2]
    doc = {
        "schema_version": "0.1.0",
        "id": "test/mcp-campaign/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": yaml.safe_load(
            (root / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())},
        "base_arch": {"inline": yaml.safe_load(
            (root / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())},
        "backends": {"screening": "zigzag"},
        "search": {"kind": "architecture_width", "widths": [4, 8]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 4},
    }
    db = str(tmp_path / "mcp-campaign.db")
    url = _url(flux_tool)

    started = asyncio.run(_call_tool(url, f"{flux_tool.name}_campaign_start",
                                     {"objective": doc, "db_path": db, "run_trials": 1}))
    cid = started["campaign_id"]
    assert started["report"]["trials_run"] == 1

    stepped = asyncio.run(_call_tool(url, f"{flux_tool.name}_campaign_step",
                                     {"db_path": db, "campaign_id": cid, "max_trials": 4}))
    assert stepped["status"] == "done"

    status = asyncio.run(_call_tool(url, f"{flux_tool.name}_campaign_status",
                                    {"db_path": db, "campaign_id": cid}))
    assert status["trial_counts"] == {"ok": 2}
    assert status["remaining_budget"]["evaluations"] == 2
    assert status["spent"]["usd"] is None  # unknown, never 0.0

    frontier = asyncio.run(_call_tool(url, f"{flux_tool.name}_campaign_frontier",
                                      {"db_path": db, "campaign_id": cid,
                                       "include_contenders": True}))
    assert [f["candidate"]["width"] for f in frontier["frontier"]] == [8]
    assert frontier["frontier"][0]["metrics"]["latency_cycles"]["value"] == 1554.0
    assert {c["candidate"]["width"] for c in frontier["contenders"]} == {8}

    stopped = asyncio.run(_call_tool(url, f"{flux_tool.name}_campaign_stop",
                                     {"db_path": db, "campaign_id": cid, "reason": "test over"}))
    assert stopped["status"] == "stopped"

    # A tool error arrives as isError on the wire result (an exception inside the async
    # client gets wrapped into an anyio ExceptionGroup, so asserting on the payload is the
    # reliable spelling). The refusal must carry the ORIGINAL stop reason.
    async def _failing_stop():
        async with streamable_http_client(url) as (r, w, _):
            async with ClientSession(r, w) as sess:
                await sess.initialize()
                return await sess.call_tool(
                    f"{flux_tool.name}_campaign_stop", arguments={"db_path": db, "campaign_id": cid}
                )

    second = asyncio.run(_failing_stop())
    assert second.isError
    text = " ".join(getattr(c, "text", "") for c in second.content)
    assert "already stopped" in text and "test over" in text
