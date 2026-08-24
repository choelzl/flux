"""The `phys` stage: real Yosys + OpenSTA on ASAP7 for the pieces that set fmax and area.

The problem statement's hard restriction is fmax > 600 MHz post-synthesis. The study's
gate-unit scores rank fabrics under one structural rule; this stage grounds them for the
finalists the frontier sends up (D454): it synthesizes the actual critical blocks -- each
pair's hash (a few XOR gates on the address path) and its fabric family's switching element
(the 52:1 crossbar selector, radix-2/4 butterfly elements, the hierarchical group selector)
-- through the same `run_synthesis_flow` screen the macarray uses (~2 s each, D365), at a
1667 ps clock, and reports worst slack against it.

Composed area is then element_um2 x (structural units / element structural units) --
an explicit per-family calibration of the gate-unit score into um2. That composition
is a SCREEN, not a placement: D272 measured composed-vs-whole-fabric disagreeing in
both directions (gates optimise across a whole design; wires are real), so the number
is labeled composed wherever it is printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .fabric import ROW_BITS, FabricModel

_CLOCK_600MHZ_PS = 1667.0


def hash_block_rtl(mapping, addr_bits: int = 16, bank_bits: int = 5) -> tuple[str, str]:
    """One port's bank-hash block, from the mapping's own Verilog (flux_bankmap)."""
    body = mapping.verilog(addr_bits, bank_bits)
    src = (f"module imapping_hash(input [{addr_bits - 1}:0] addr, "
           f"output [{bank_bits - 1}:0] bank);\n{body}\nendmodule\n")
    return src, "imapping_hash"


def mux_rtl(inputs: int, width: int = ROW_BITS, name: str = "imapping_mux") -> tuple[str, str]:
    """An inputs:1 selector at row width -- the switching element whose depth sets a
    fabric's combinational fmax (52:1 for the full crossbar, 2:1/4:1 for butterflies,
    uplink:1 for the hierarchical group switch)."""
    sel_bits = max(1, (inputs - 1).bit_length())
    src = (f"module {name}(input [{inputs * width - 1}:0] in_flat, "
           f"input [{sel_bits - 1}:0] sel, output [{width - 1}:0] out);\n"
           f"  assign out = in_flat[sel * {width} +: {width}];\n"
           f"endmodule\n")
    return src, name


# One representative element per fabric family: (element inputs, structural units of
# that element under the same rule that priced the fabric).
_FAMILY_ELEMENT: dict[str, tuple[int, int]] = {
    "xbar-full": (52, 52 * ROW_BITS),
    "benes": (2, 2 * ROW_BITS),
    "fly-r2": (2, 2 * ROW_BITS),
    "fly-r4": (4, 4 * ROW_BITS),
    "cx": (52, 52 * ROW_BITS // 4),     # concentrator stage dominates depth
    "hier": (8, 8 * ROW_BITS),
    "unit-split": (36, 36 * ROW_BITS // 2),
    "ring": (2, 2 * ROW_BITS),
}


def _family_of(fabric: FabricModel) -> str:
    for key in _FAMILY_ELEMENT:
        if fabric.name.startswith(key):
            return key
    return "fly-r2"


@dataclass(frozen=True, slots=True)
class PhysReport:
    block: str
    area_um2: float
    worst_slack_ps: float
    meets_600mhz: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"block": self.block, "area_um2": self.area_um2,
                "worst_slack_ps": self.worst_slack_ps,
                "meets_600mhz": self.meets_600mhz, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class PairScreen:
    """One pair's physical screen: its hash block, its fabric family's switching element,
    and the fabric's composed um2 (None when the element could not be synthesized)."""

    hash: PhysReport
    element: PhysReport
    composed_um2: float | None

    @property
    def worst_slack_ps(self) -> float:
        return min(self.hash.worst_slack_ps, self.element.worst_slack_ps)

    @property
    def meets_600mhz(self) -> bool:
        return self.hash.meets_600mhz and self.element.meets_600mhz

    def to_dict(self) -> dict[str, Any]:
        return {"hash": self.hash.to_dict(), "element": self.element.to_dict(),
                "composed_um2": self.composed_um2}


def screen_block(source: str, top: str, label: str,
                 timeout_s: float = 300.0) -> PhysReport:
    from flux_evaluator_openroad import run_synthesis_flow

    try:
        rep = run_synthesis_flow(source, top, clock_port=None, reset_port=None,
                                 clock_period_ps=_CLOCK_600MHZ_PS, timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001 -- a screen failure is a report, not a crash
        return PhysReport(block=label, area_um2=float("nan"),
                          worst_slack_ps=float("nan"), meets_600mhz=False,
                          detail=f"{type(exc).__name__}: {str(exc)[:200]}")
    # a combinational block with no path to time reports infinite slack; the clock is the
    # honest bound and keeps the number a JSON reader can hold
    slack = min(rep.worst_slack_ps, _CLOCK_600MHZ_PS)
    return PhysReport(block=label, area_um2=rep.area_um2, worst_slack_ps=slack,
                      meets_600mhz=slack >= 0)


def screen_pairs(scored: list) -> dict[str, PairScreen]:
    """Screen each DISTINCT hash block and fabric element among `scored` pairs (the
    finalists), once each, and return every pair's screen by its name: the hash's report,
    the element's, and the composed um2 (element_um2 scaled by the structural ratio; the
    D272 caveat applies and is repeated wherever the number is printed)."""
    from flux_bankmap.mapping import Modulo

    hashes: dict[str, PhysReport] = {}
    families: dict[str, PhysReport] = {}
    out: dict[str, PairScreen] = {}
    for s in scored:
        probe = s.solution.hash_of(_probe_layout())
        h_desc = probe.mapping.describe()
        if h_desc not in hashes:
            if isinstance(probe.mapping, Modulo):
                hashes[h_desc] = PhysReport(
                    block=f"hash[{s.solution.name}]", area_um2=0.0,
                    worst_slack_ps=_CLOCK_600MHZ_PS, meets_600mhz=True,
                    detail="plain bit-select: wires, zero gates")
            else:
                src, top = hash_block_rtl(probe.mapping)
                hashes[h_desc] = screen_block(src, top, f"hash[{s.solution.name}]")
        fam = _family_of(s.fabric)
        if fam not in families:
            inputs, _units = _FAMILY_ELEMENT[fam]
            src, top = mux_rtl(inputs)
            families[fam] = screen_block(src, top, f"element[{fam}] {inputs}:1x{ROW_BITS}b")
        element = families[fam]
        composed = None
        if element.area_um2 == element.area_um2:      # not NaN: the element synthesized
            _inputs, units = _FAMILY_ELEMENT[fam]
            composed = s.fabric.gate_units / units * element.area_um2
        out[s.pair_name] = PairScreen(hash=hashes[h_desc], element=element, composed_um2=composed)
    return out


def _probe_layout():
    from .model import Mode, TensorLayout

    return TensorLayout(r=16, c=32, l=2, mode=Mode.Loop_Row_Col, base=0)


__all__ = ["PairScreen", "PhysReport", "hash_block_rtl", "mux_rtl", "screen_block", "screen_pairs"]
