"""flux_nlu: the FP16 non-linear-unit WORLD (docs/decisions.md D408, D519).

Claude built the rig -- the exhaustive ULP truth (`fp16`), the sweep harness (`verify`),
the vector tiers (`vectors`), the toolkit (`blocks`), the transpiler (`transpile`), the
table oracle (`tables`), the hygiene pass (`hygiene`), the model roles' words (`invent`)
-- and `world.World` binds them to a problem document: `applications/nlu/nlu.problem.yaml`
names it once and `flux task run` runs the campaign. The MODEL running inside it designs
the unit: methods, sharing, pipelining. Tools judge everything.
"""

from .fp16 import OPCODES, all_inputs, reference, ulp_distance, ulp_report

__all__ = ["OPCODES", "all_inputs", "reference", "ulp_distance", "ulp_report"]
