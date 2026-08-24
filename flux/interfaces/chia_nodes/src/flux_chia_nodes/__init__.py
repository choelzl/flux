"""Flux evaluators exposed as real CHIA library nodes (docs/agent-surface.md)."""

from __future__ import annotations

from .calibrate import flux_calibrate
from .conformance import flux_conformance_check
from .bankmap_dse_loop import flux_bankmap_dse_loop
from .nlu_dse_loop import flux_nlu_dse_loop
from .interconnect_mapping_dse_loop import flux_interconnect_mapping_dse_loop
from .macarray_dse_loop import flux_macarray_dse_loop
from .omni_run import flux_omni_run
from .invent_prefetcher import flux_invent_prefetcher
from .dynamic_shape import flux_sweep_dynamic_shape
from .moe_routing import flux_sweep_moe_routing
from .compose_rtl import flux_compose_and_verify_rtl_design
from .compose_systemc import flux_compose_and_verify_systemc_design
from .evaluate import flux_evaluate
from .generate_architecture import flux_generate_architecture_candidate
from .generate_architecture import GenerationResult as ArchitectureGenerationResult
from .generate_rtl import flux_generate_rtl_module
from .generate_rtl import GenerationResult as RtlGenerationResult
from .generate_rtl_for_architecture import ArchitectureRtlReport, flux_generate_rtl_for_architecture
from .explain_candidate import (
    BackendExpressibility,
    CandidateExplanation,
    flux_explain_candidate,
)
from .author_design_spec import AuthoredDesignSpec, flux_author_design_spec
from .mine_knowledge import flux_mine_knowledge, flux_recall_facts
from .check_faithfulness import FaithfulnessReport, flux_check_prose_faithfulness
from .health import BackendHealth, HealthReport, flux_backend_health
from .protocols import (
    ProtocolCheck,
    flux_check_protocol_conformance,
    ProtocolCheckReport,
    flux_check_ir_protocols,
    flux_list_protocols,
    flux_protocol_lookup,
)
from .calibrate_against_generated import (
    GeneratedReferenceReport,
    flux_calibrate_against_generated_rtl,
)
from .generate_sequential_rtl import (
    GemmRtlReport,
    SequentialRtlReport,
    flux_generate_gemm_rtl_for_architecture,
    flux_generate_sequential_rtl_for_architecture,
)
from .generate_systemc import GenerationResult, flux_generate_systemc_module
from .knowledge import flux_knowledge_lookup
from .prefetcher_dse_loop import (
    flux_champsim_run,
    flux_prefetcher_dse_loop,
)
from .store import flux_find_results, flux_get_result, flux_leaderboard, flux_list_public_corpus
from .synthesize_composite_rtl import flux_synthesize_composite_rtl_design
from .synthesize_with_asap7 import flux_synthesize_with_asap7
from .synthesize_with_asap7_redacted import flux_synthesize_with_asap7_redacted
from .validity import flux_check_validity

__all__ = [
    "FaithfulnessReport",
    "ProtocolCheckReport",
    "ProtocolCheck",
    "AuthoredDesignSpec",
    "flux_evaluate",
    "flux_calibrate",
    "flux_conformance_check",
    "flux_check_validity",
    "flux_knowledge_lookup",
    "flux_get_result",
    "flux_find_results",
    "flux_list_public_corpus",
    "flux_bankmap_dse_loop",
    "flux_nlu_dse_loop",
    "flux_interconnect_mapping_dse_loop",
    "flux_macarray_dse_loop",
    "flux_omni_run",
    "flux_champsim_run",
    "flux_invent_prefetcher",
    "flux_prefetcher_dse_loop",
    "flux_generate_systemc_module",
    "GenerationResult",
    "flux_generate_rtl_module",
    "RtlGenerationResult",
    "flux_compose_and_verify_rtl_design",
    "flux_synthesize_composite_rtl_design",
    "flux_compose_and_verify_systemc_design",
    "flux_leaderboard",
    "flux_sweep_dynamic_shape",
    "flux_sweep_moe_routing",
    "flux_generate_architecture_candidate",
    "ArchitectureRtlReport",
    "flux_generate_rtl_for_architecture",
    "flux_generate_sequential_rtl_for_architecture",
    "flux_generate_gemm_rtl_for_architecture",
    "flux_calibrate_against_generated_rtl",
    "flux_author_design_spec",
    "flux_mine_knowledge",
    "flux_recall_facts",
    "flux_check_prose_faithfulness",
    "flux_backend_health",
    "flux_check_ir_protocols",
    "flux_check_protocol_conformance",
    "flux_list_protocols",
    "flux_protocol_lookup",
    "flux_explain_candidate",
    "CandidateExplanation",
    "BackendExpressibility",
    "HealthReport",
    "BackendHealth",
    "GeneratedReferenceReport",
    "SequentialRtlReport",
    "GemmRtlReport",
    "ArchitectureGenerationResult",
    "flux_synthesize_with_asap7",
    "flux_synthesize_with_asap7_redacted",
]
