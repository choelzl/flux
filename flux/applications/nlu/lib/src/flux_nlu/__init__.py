"""flux_nlu: the FP16 non-linear-unit study (docs/decisions.md D408).

Claude built the rig -- the exhaustive ULP truth (`fp16`), the sweep harness
(`verify`), the vector tiers (`vectors`), the method cheat sheet (`knowledge`), the
model roles (`invent`) and the loop (`flow`); the MODEL running inside it designs the
unit: methods, sharing, pipelining. Tools judge everything.
"""

from .flow import DEFAULT_OPS, NluRequest, NluResult, Scored, run_study
from .fp16 import OPCODES, all_inputs, reference, ulp_distance, ulp_report

__all__ = ["DEFAULT_OPS", "NluRequest", "NluResult", "OPCODES", "Scored",
           "all_inputs", "reference", "run_study", "ulp_distance", "ulp_report"]
