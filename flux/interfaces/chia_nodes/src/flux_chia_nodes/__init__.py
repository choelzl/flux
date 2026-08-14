"""Flux evaluators exposed as real CHIA library nodes (docs/agent-surface.md)."""

from __future__ import annotations

from .agentic import (
    CostTrackingProposer,
    flux_agentic_architecture_search,
    flux_agentic_joint_search,
    flux_agentic_mapping_search,
    flux_agentic_memory_search,
    flux_agentic_noc_search,
)
from .calibrate import flux_calibrate
from .conformance import flux_conformance_check
from .cost import MissingUsageMetadataError, UnknownModelPricingError, compute_usd_cost, known_models
from .dse_loop import flux_agentic_dse_loop
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
from .ip_catalog import flux_ip_catalog
from .check_faithfulness import FaithfulnessReport, flux_check_prose_faithfulness
from .author_objective import AuthoredObjective, flux_author_objective
from .campaign import (
    flux_campaign_frontier,
    flux_campaign_resume,
    flux_campaign_start,
    flux_campaign_status,
    flux_campaign_step,
    flux_campaign_stop,
)
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
from .memory_characterize import flux_characterize_memory_level
from .multi_axis_dse import MultiAxisDSEReport, flux_agentic_multi_axis_dse
from .parallel import ChiaParallelEvaluator
from .rtl_dse import RtlDSEReport, flux_rtl_generate_dse
from .search import flux_search
from .store import flux_find_results, flux_get_result, flux_leaderboard, flux_list_public_corpus
from .synthesize_composite_rtl import flux_synthesize_composite_rtl_design
from .synthesize_with_asap7 import flux_synthesize_with_asap7
from .synthesize_with_asap7_redacted import flux_synthesize_with_asap7_redacted
from .systemc_dse import SystemCDSEReport, flux_systemc_generate_dse
from .validity import flux_check_validity

__all__ = [
    "FaithfulnessReport",
    "ProtocolCheckReport",
    "ProtocolCheck",
    "AuthoredObjective",
    "AuthoredDesignSpec",
    "flux_evaluate",
    "ChiaParallelEvaluator",
    "flux_search",
    "flux_calibrate",
    "flux_conformance_check",
    "flux_check_validity",
    "flux_knowledge_lookup",
    "flux_get_result",
    "flux_find_results",
    "flux_list_public_corpus",
    "flux_agentic_mapping_search",
    "flux_agentic_architecture_search",
    "flux_agentic_noc_search",
    "flux_agentic_memory_search",
    "flux_agentic_joint_search",
    "flux_agentic_dse_loop",
    "flux_agentic_multi_axis_dse",
    "MultiAxisDSEReport",
    "flux_characterize_memory_level",
    "flux_generate_systemc_module",
    "GenerationResult",
    "flux_systemc_generate_dse",
    "SystemCDSEReport",
    "flux_generate_rtl_module",
    "RtlGenerationResult",
    "flux_rtl_generate_dse",
    "RtlDSEReport",
    "flux_compose_and_verify_rtl_design",
    "flux_synthesize_composite_rtl_design",
    "flux_compose_and_verify_systemc_design",
    "flux_leaderboard",
    "flux_sweep_dynamic_shape",
    "flux_sweep_moe_routing",
    "CostTrackingProposer",
    "compute_usd_cost",
    "known_models",
    "MissingUsageMetadataError",
    "UnknownModelPricingError",
    "flux_generate_architecture_candidate",
    "ArchitectureRtlReport",
    "flux_generate_rtl_for_architecture",
    "flux_generate_sequential_rtl_for_architecture",
    "flux_generate_gemm_rtl_for_architecture",
    "flux_calibrate_against_generated_rtl",
    "flux_author_design_spec",
    "flux_mine_knowledge",
    "flux_recall_facts",
    "flux_ip_catalog",
    "flux_check_prose_faithfulness",
    "flux_author_objective",
    "flux_backend_health",
    "flux_campaign_frontier",
    "flux_campaign_resume",
    "flux_campaign_start",
    "flux_campaign_status",
    "flux_campaign_step",
    "flux_campaign_stop",
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
