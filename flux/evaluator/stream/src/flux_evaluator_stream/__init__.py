"""Stream (multi-core/layer-fused DSE, KU Leuven MICAS) backend adapter (docs/decisions.md D80-D82)."""

from __future__ import annotations

from .adapter import StreamEvaluator
from .architecture_translator import architecture_ir_to_stream_hardware_yaml
from .errors import NotExpressibleError
from .fusion_translator import mapping_fusion_to_intra_core_tiling

__all__ = [
    "StreamEvaluator",
    "architecture_ir_to_stream_hardware_yaml",
    "mapping_fusion_to_intra_core_tiling",
    "NotExpressibleError",
]
