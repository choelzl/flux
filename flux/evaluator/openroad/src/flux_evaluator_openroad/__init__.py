"""Real physical-design PPA via Yosys + OpenROAD on ASAP7 (docs/decisions.md D225)."""

from .adapter import OpenRoadEvaluator
from .errors import NotExpressibleError, OpenRoadError
from .flow import RTL_STAGES, PpaReport, measure_rtl, parse_critical_path, run_ppa_flow, run_synthesis_flow

__all__ = [
    "RTL_STAGES", "measure_rtl",
    "NotExpressibleError",
    "OpenRoadError",
    "OpenRoadEvaluator",
    "PpaReport",
    "parse_critical_path",
    "run_ppa_flow",
    "run_synthesis_flow",
]
