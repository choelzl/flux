"""flux_nlu: the FP16 non-linear-unit world (D519).

The rig: exhaustive ULP truth (`fp16`), vector tiers (`vectors`), toolkit (`blocks`),
transpiler (`transpile`), table oracle (`tables`), hygiene pass (`hygiene`) and the model
roles' prompts (`invent`). `world.World` binds them to `applications/nlu/problem.yaml`;
the model designs the unit, tools judge it.
"""

from .fp16 import OPCODES, all_inputs, reference, ulp_distance, ulp_report

__all__ = ["OPCODES", "all_inputs", "reference", "ulp_distance", "ulp_report"]
