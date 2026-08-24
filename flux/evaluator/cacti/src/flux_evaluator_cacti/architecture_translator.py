"""Flux Architecture IR -> a CACTI `SRAMSpec` (docs/decisions.md D36). v0.1 scope: `Candidate.arch`
must describe exactly *one* physical SRAM macro — one `class == "memory"` `hierarchy` node, no
more, no fewer — the same "the arch dict *is* the thing being characterized" shape
`evaluators/booksim`/`evaluators/noxim` already use for `interconnect.noc` (not a whole SoC's
multi-level hierarchy at once: CACTI characterizes a single macro, not a memory system).

Two fields Flux's existing Architecture IR memory-class `attrs` don't already carry, both
required explicitly here, neither guessed:

- **`word_width_bits`**: `size_kb` alone doesn't determine a real SRAM's `depth` (word count) —
  a 512 KiB macro could be 4096 words x 128 bytes or 32768 words x 16 bytes, physically very
  different characterizations. Every existing example (`generic-riscv-soc-v1.yaml`,
  `simple-npu-1d-v1.yaml`, ...) omits this — CACTI-characterizing any of them needs it added
  explicitly, the same "extend additively, don't guess" pattern `evaluators/booksim` used for
  `interconnect.noc`.
- **Technology, from `tech.node`** (already present, e.g. `"n28"`): parsed as `n<nm>` ->
  microns. CACTI 7's own real, verified constraint (docs/decisions.md D35: built and ran the
  actual tool, got `"Feature size must be <= 90 nm"` for a too-large node) means anything above
  90nm raises here rather than reaching CACTI and failing there — this repo's own examples
  (n28/n16) are already well inside the supported range, so this is a real safety check, not a
  practical blocker for Flux's own use.
"""

from __future__ import annotations

import re
from typing import Any

from chia.vlsi.sram_cacti.cacti_runner import SRAMSpec

from .errors import NotExpressibleError

_NODE_RE = re.compile(r"^n(\d+)$")
_MAX_SUPPORTED_NM = 90  # CACTI 7's own real, verified constraint (docs/decisions.md D35)
# CACTI 7's smallest ITRS node - below this its planar models do not exist (D253).
_MIN_SUPPORTED_NM = 22


def architecture_ir_to_sram_spec(arch: dict[str, Any]) -> SRAMSpec:
    """Extract a `SRAMSpec` from `arch["hierarchy"]`'s single memory-class node. Raises
    `NotExpressibleError` for anything this v0.1 translator can't express — see module docstring.
    """
    arch_id = arch.get("id", "<no id>")
    hierarchy = arch.get("hierarchy", [])
    memory_nodes = [n for n in hierarchy if n.get("class") == "memory"]
    if len(memory_nodes) != 1:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: evaluators/cacti v0.1 needs exactly one "
            f"class=='memory' hierarchy node (the single SRAM macro being characterized), found "
            f"{len(memory_nodes)} — CACTI characterizes one physical macro, not a whole memory "
            "system; pass an arch dict containing just the level you want characterized."
        )
    node = memory_nodes[0]
    attrs = node.get("attrs", {})

    word_width_bits = attrs.get("word_width_bits")
    if not word_width_bits:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: memory level {node.get('level')!r} has no "
            "attrs.word_width_bits — size_kb alone doesn't determine depth (see module "
            "docstring); this adapter doesn't guess a word width."
        )

    size_kb = attrs.get("size_kb")
    if not size_kb:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: memory level {node.get('level')!r} has no attrs.size_kb."
        )
    size_bits = size_kb * 1024 * 8
    if size_bits % word_width_bits != 0:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: memory level {node.get('level')!r}'s size_kb={size_kb} "
            f"({size_bits} bits) does not divide evenly by word_width_bits={word_width_bits} — "
            "not a whole number of words."
        )
    depth = size_bits // word_width_bits

    ports = attrs.get("ports")
    if ports:
        num_read_ports = ports.get("r", 0)
        num_write_ports = ports.get("w", 0)
        num_rw_ports = ports.get("rw", 0)
        if num_read_ports == num_write_ports == num_rw_ports == 0:
            raise NotExpressibleError(
                f"architecture {arch_id!r}: memory level {node.get('level')!r}'s attrs.ports "
                f"has no r/w/rw counts — {ports!r}."
            )
    else:
        # No ports declared at all: the same single-unified-read/write-port default this
        # adapter's own real verification run used (docs/decisions.md D35), not a CACTI default.
        num_read_ports, num_write_ports, num_rw_ports = 0, 0, 1

    return SRAMSpec(
        name=str(node.get("level", "sram")),
        depth=depth,
        width=word_width_bits,
        ports="rw" if num_rw_ports else "r+w",
        mask_gran=None,
        num_rw_ports=num_rw_ports,
        num_read_ports=num_read_ports,
        num_write_ports=num_write_ports,
    )


def architecture_ir_to_technology_um(arch: dict[str, Any]) -> float:
    """Extract CACTI's `technology_um` from `arch["tech"]["node"]` (e.g. `"n28"` -> `0.028`).
    Raises `NotExpressibleError` for an unparseable node string or one above CACTI 7's own real
    90nm ceiling (see module docstring).
    """
    arch_id = arch.get("id", "<no id>")
    node = arch.get("tech", {}).get("node")
    match = _NODE_RE.match(str(node))
    if not match:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: tech.node={node!r} isn't in the expected 'n<nm>' form "
            "(e.g. 'n28') — evaluators/cacti can't derive a technology from it."
        )
    nm = int(match.group(1))
    if nm < _MIN_SUPPORTED_NM:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: tech.node={node!r} ({nm}nm) is below CACTI 7's own "
            f"{_MIN_SUPPORTED_NM}nm floor — its planar ITRS device models end there, and "
            "sub-22nm FinFET nodes are outside its physics (probed: it fails outright at "
            "7nm, docs/decisions.md D253). The sanctioned route is characterize at a native "
            "node and scale by a published factor: see flux_evaluator_cacti.scaling and the "
            "composition rung's cacti_scale_from_nm option."
        )
    if nm > _MAX_SUPPORTED_NM:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: tech.node={node!r} ({nm}nm) is above CACTI 7's own real "
            f"{_MAX_SUPPORTED_NM}nm ceiling ('Feature size must be <= 90 nm', confirmed by "
            "actually running it, docs/decisions.md D35) — not expressible here."
        )
    return nm / 1000.0
