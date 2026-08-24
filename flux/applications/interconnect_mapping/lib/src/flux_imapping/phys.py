"""The physical rung: real Yosys+STA on ASAP7 for the pieces that set fmax and area.

The problem statement's hard restriction is fmax > 600 MHz post-synthesis. The study's
gate-unit scores rank fabrics under one structural rule; this module grounds them: it
synthesizes the actual critical blocks -- each frontier hash (a few XOR gates on the
address path) and each fabric family's switching element (the 52:1 crossbar selector,
radix-2/4 butterfly elements, the hierarchical group selector) -- through the same
`run_synthesis_flow` screen the macarray loop uses (~2 s each, D365), at a 1667 ps
clock, and reports worst slack against it.

Composed area is then element_um2 x (structural units / element structural units) --
an explicit per-family calibration of the gate-unit score into um2. That composition
is a SCREEN, not a placement: D272 measured composed-vs-whole-fabric disagreeing in
both directions (gates optimise across a whole design; wires are real), so the number
is labeled composed and the whole-fabric OpenROAD flow remains the confirm rung.
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
    return PhysReport(block=label, area_um2=rep.area_um2,
                      worst_slack_ps=rep.worst_slack_ps,
                      meets_600mhz=rep.worst_slack_ps >= 0)


def screen_pairs(scored: list, mem) -> tuple[list[PhysReport], dict[str, float]]:
    """Screen each DISTINCT hash block and fabric element among `scored` pairs (the
    frontier, typically). Returns the per-block reports and a composed-um2 estimate
    per fabric name (element_um2 scaled by the structural ratio; D272 caveat applies
    and is repeated wherever the number is printed)."""
    reports: list[PhysReport] = []
    done_hash: set[str] = set()
    done_family: dict[str, PhysReport] = {}
    composed: dict[str, float] = {}

    for s in scored:
        probe = s.solution.hash_of(_probe_layout())
        h_desc = probe.mapping.describe()
        if h_desc not in done_hash:
            done_hash.add(h_desc)
            from flux_bankmap.mapping import Modulo
            if isinstance(probe.mapping, Modulo):
                reports.append(PhysReport(
                    block=f"hash[{s.solution.name}]", area_um2=0.0,
                    worst_slack_ps=_CLOCK_600MHZ_PS, meets_600mhz=True,
                    detail="plain bit-select: wires, zero gates"))
            else:
                src, top = hash_block_rtl(probe.mapping)
                reports.append(screen_block(
                    src, top, f"hash[{s.solution.name}]"))
        fam = _family_of(s.fabric)
        if fam not in done_family:
            inputs, el_units = _FAMILY_ELEMENT[fam]
            src, top = mux_rtl(inputs)
            rep = screen_block(src, top, f"element[{fam}] {inputs}:1x{ROW_BITS}b")
            done_family[fam] = rep
            reports.append(rep)
        el_rep = done_family[fam]
        if el_rep.area_um2 == el_rep.area_um2:  # not NaN
            _, el_units = _FAMILY_ELEMENT[fam]
            composed[s.fabric.name] = s.fabric.gate_units / el_units * el_rep.area_um2
    return reports, composed


def _probe_layout():
    from .model import Mode, TensorLayout

    return TensorLayout(r=16, c=32, l=2, mode=Mode.Loop_Row_Col, base=0)
