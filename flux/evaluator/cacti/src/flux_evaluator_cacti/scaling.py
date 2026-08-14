"""Published technology-scaling factors for CACTI results (docs/decisions.md D253).

CACTI 7's device models are planar ITRS data; its real floor is 22nm, and 7nm FinFET is
outside its physics entirely (probed: it fails outright at 0.007um). The sanctioned route to
sub-22nm numbers is characterize-at-a-native-node THEN scale by a PUBLISHED factor — never
extrapolate CACTI itself.

The factor table below is vendored VERBATIM from:

    A. Stillmaker, B. Baas, "Scaling equations for the accurate prediction of CMOS device
    performance from 180nm to 7nm", Integration, the VLSI Journal 58 (2017) 74-81,
    Table 4 ("Area scaling factors using geometric mean of area values"), the 7nm row.
    Openly hosted by the authors: https://vcl.ece.ucdavis.edu/pubs/2017.02.VLSIintegration.TechScale/

Their factors are geometric means of ITRS minimum-feature-size, metal-1 half-pitch and 4T
logic gate areas per node — LOGIC-geometry scaling. Applying them to an SRAM macro is the
standard approximation and is recorded as exactly that (the provenance string this module
hands back names the source and the factor); SRAM arrays do not scale identically to logic,
and DeepScaleTool (Sarangi & Baas, ISCAS 2021) reports these particular factors OVERESTIMATE
scaling at the newest nodes versus foundry data (their 10->7nm area reduction: 59% here vs
TSMC's 30-35%) — so a scaled 7nm area from this table is, if anything, OPTIMISTIC. Stated
here once, and carried on every scaled result rather than remembered.

Only the 7nm-normalized row is vendored: the full matrix is recoverable as ratios (verified
against the printed matrix: row ratios reproduce its entries within its own 2-significant-
figure rounding, e.g. 45->32 as 17/7.8 = 2.18 vs the printed 2.2).
"""

from __future__ import annotations

# Table 4, 7nm row: area(node) / area(7nm), per Stillmaker & Baas 2017.
_AREA_VS_7NM: dict[int, float] = {
    180: 320.0,
    130: 110.0,
    90: 48.0,
    65: 25.0,
    45: 17.0,
    32: 7.8,
    20: 3.6,
    16: 3.2,
    14: 2.9,
    10: 1.7,
    7: 1.0,
}

SUPPORTED_NODES_NM = tuple(sorted(_AREA_VS_7NM))

CITATION = (
    "Stillmaker & Baas 2017, Integration VLSI J. 58, Table 4 (ITRS geometric-mean area "
    "factors; logic-geometry scaling applied to an SRAM macro — approximate, likely "
    "optimistic at the newest nodes per Sarangi & Baas 2021)"
)

# Published high-density 6T SRAM bitcell areas (um2/bit) — the PHYSICAL FLOOR for a scaled
# macro: no macro is denser than its own periphery-free bitcell array, and the D255 check
# measured the logic-factor route violating exactly that at 7nm (32KB macro scaled to
# 0.0234 um2/bit vs the 0.027 um2/bit N7 bitcell). Sources: TSMC N7 HD 0.027 um2 (WikiChip
# Fuse, "TSMC 7nm HD and HP Cells", 2019); nodes without a verified published anchor are
# absent — the floor simply does not engage there rather than using a guessed number.
_BITCELL_UM2_PER_BIT: dict[int, float] = {
    7: 0.027,
}


def bitcell_floor_mm2(bits: int, to_nm: int) -> float | None:
    """`bits x published bitcell area`, or None where no verified anchor exists (D255)."""
    per_bit = _BITCELL_UM2_PER_BIT.get(to_nm)
    return None if per_bit is None else bits * per_bit * 1e-6


class UnsupportedScalingNode(ValueError):
    """A node the published table does not carry — refused, never interpolated here."""


def area_scaling_factor(from_nm: int, to_nm: int) -> float:
    """area(to_nm) = area(from_nm) / factor. Both nodes must be table nodes: this module
    vendors published numbers and refuses to invent intermediate ones (interpolating a
    published table is a modeling decision the source did not make)."""
    for nm in (from_nm, to_nm):
        if nm not in _AREA_VS_7NM:
            raise UnsupportedScalingNode(
                f"{nm}nm is not in the published table (nodes: {SUPPORTED_NODES_NM}) — "
                "pick a published node; this module does not interpolate"
            )
    return _AREA_VS_7NM[from_nm] / _AREA_VS_7NM[to_nm]


def scale_area_mm2(
    area_mm2: float, *, from_nm: int, to_nm: int, bits: int | None = None,
    array_efficiency: float | None = None,
) -> tuple[float, str]:
    """Scaled area plus the provenance sentence that must travel with it.

    `bits` (D255) engages the bitcell floor where an anchor exists: the scaled macro area is
    clamped to `bits x published bitcell area` — a macro denser than its own bitcell array is
    physically impossible, and the logic-geometry factor produces exactly that at 7nm. The
    provenance says when the floor engaged."""
    factor = area_scaling_factor(from_nm, to_nm)
    scaled = area_mm2 / factor
    note = (
        f"area scaled {from_nm}nm -> {to_nm}nm by published factor {factor:.4g} ({CITATION})"
    )
    if bits is not None and to_nm in _BITCELL_UM2_PER_BIT:
        floor_mm2 = bits * _BITCELL_UM2_PER_BIT[to_nm] * 1e-6
        if array_efficiency is not None:
            # The refined estimate (D256): published bitcell / CACTI's OWN measured array
            # efficiency at the native node. The single assumption — the periphery fraction
            # carries across nodes — is far weaker than logic-scaling the whole macro, and
            # both inputs are sourced: the bitcell is published, the efficiency is the real
            # tool's own report for THIS configuration.
            if not 0.0 < array_efficiency <= 1.0:
                raise ValueError(f"array_efficiency={array_efficiency!r} must be in (0, 1]")
            estimate = floor_mm2 / array_efficiency
            return estimate, note + (
                f"; refined to {bits} bits x {_BITCELL_UM2_PER_BIT[to_nm]} um2/bit "
                f"(TSMC N7 HD, WikiChip Fuse 2019) / {array_efficiency:.4f} array "
                f"efficiency (CACTI's own report at {from_nm}nm; periphery fraction "
                "assumed node-invariant)"
            )
        if scaled < floor_mm2:
            return floor_mm2, note + (
                f"; clamped to the {to_nm}nm bitcell floor {bits} bits x "
                f"{_BITCELL_UM2_PER_BIT[to_nm]} um2/bit (TSMC N7 HD, WikiChip Fuse 2019) — "
                "the unfloored scaled value was denser than a periphery-free bitcell array"
            )
    return scaled, note


_EFFICIENCY_RE = None


def parse_area_efficiency(cacti_detailed_output: str) -> float | None:
    """CACTI's own array efficiency from its DETAILED output — the real line reads
    `Area efficiency (Memory cell area/Total area) - 81.7717 %` (captured from the tool,
    docs/decisions.md D256). None when the output carries no such line."""
    import re

    global _EFFICIENCY_RE
    if _EFFICIENCY_RE is None:
        _EFFICIENCY_RE = re.compile(
            r"Area efficiency \(Memory cell area/Total area\) - ([\d.eE+-]+) %")
    m = _EFFICIENCY_RE.search(cacti_detailed_output)
    return float(m.group(1)) / 100.0 if m else None


def measure_area_efficiency(arch: dict, technology_um: float, *, cacti_path: str,
                            timeout_s: float = 300.0) -> float | None:
    """Run CACTI once at DETAILED print level for `arch`'s single memory node and return its
    own reported array efficiency. Separate from the normal characterization because chia's
    runner uses CONCISE output, which omits the line (measured)."""
    import subprocess
    import tempfile
    from pathlib import Path

    from chia.vlsi.sram_cacti.cacti_runner import _generate_cacti_cfg

    from .architecture_translator import architecture_ir_to_sram_spec

    spec = architecture_ir_to_sram_spec(arch)
    cfg = _generate_cacti_cfg(spec, technology_um) + '\n-Print level "DETAILED"\n'
    with tempfile.TemporaryDirectory(prefix="flux-cacti-eff-") as d:
        cfg_path = Path(d) / "eff.cfg"
        cfg_path.write_text(cfg)
        proc = subprocess.run(
            [cacti_path, "-infile", str(cfg_path)], capture_output=True, text=True,
            cwd=Path(cacti_path).parent, timeout=timeout_s,
        )
        if proc.returncode != 0:
            return None
        return parse_area_efficiency(proc.stdout)


# ---------------------------------------------------------------------------------------------
# Delay / energy / power scaling (docs/decisions.md D257) — same primary source, two methods.
#
# MEASURED (default): Stillmaker & Baas 2017 Table 2 — simulated FO4-inverter delay/energy/
# power per node AT ITS ITRS NOMINAL VOLTAGE. For fixed nominal-voltage scaling these single
# measured points are the direct evidence; only clearly-legible rows are vendored.
#
# POLYNOMIAL: the paper's Eqs. 5-7 with Table 5 coefficients — voltage-dependent factors for
# custom supply voltages. Implementation validated EXACTLY against the authors' own corrected
# worked example (their errata page: EF(32HP,0.9V)=0.4571, EF(65bulk,1.3V)=2.604, ratio
# 0.1755). Measured caveat, from cross-checking both methods at nominal voltages: delay
# ratios agree within ~7% (3.69 vs 3.97 for 32->7), but the energy and power polynomials
# deviate ~1.8x from the measured Table 2 points at 7nm (energy ratio 8.2 vs 4.6; power 5.6
# vs 3.1) — quadratic fits over a voltage sweep are loose at the range's edge. Hence
# "measured" is the default; the polynomial path exists for voltage studies and says so.
# ---------------------------------------------------------------------------------------------

# Table 2 rows (node -> (nominal Vdd, delay_ps, energy_fJ, power_uW)); vendored only where
# every digit was clearly legible in the source.
_MEASURED_INVERTER = {
    45: (1.1, 10.9, 1.05, 5.19),
    32: (0.97, 9.8, 0.51, 2.47),
    16: (0.86, 6.12, 0.179, 1.28),
    14: (0.86, 4.02, 0.144, 0.995),
    10: (0.83, 3.24, 0.122, 0.866),
    7: (0.8, 2.47, 0.111, 0.789),
}

# Table 5 coefficient rows, (delay a_d3..a_d0, energy a_e2..a_e0, power a_p2..a_p0); a "-"
# in the printed table is a missing cubic term (0.0). Vendored: bulk 65 (the errata example
# needs it), High-k HP 45/32, Multi-Gate HP 20-7. LP/LSTP rows exist in the source and are
# deliberately not vendored until needed.
_POLY = {
    65: ((-53.3, 230.4, -333.9, 178.6), (3.755, -4.398, 1.975), (12890, -10510, 4362)),
    45: ((-501.6, 1567, -1619, 566.1), (1.018, -0.3107, 0.1539), (5462, -1760, 522.4)),
    32: ((-1047, 2982, -2797, 873.5), (0.8367, -0.4341, 0.1701), (4001, -1733, 533.6)),
    20: ((0.0, 34.63, -66.37, 41.15), (0.373, -0.1582, 0.04104), (2922, -1286, 299.9)),
    16: ((0.0, 24.8, -47.52, 28.87), (0.2958, -0.1241, 0.03024), (2133, -882.6, 197.7)),
    14: ((-40.66, 109.2, -100.6, 35.92), (0.2363, -0.09675, 0.02239), (1675, -711, 159)),
    10: ((-34.95, 93.63, -85.99, 30.4), (0.2068, -0.09311, 0.02375), (1456, -621.6, 143.8)),
    7: ((-28.58, 76.6, -70.26, 24.69), (0.1776, -0.09097, 0.02447), (1179, -515.7, 123.4)),
}

_TIMING_CITATION = "Stillmaker & Baas 2017, Integration VLSI J. 58"


def _factor_at(nm: int, kind: str, voltage: float) -> float:
    if nm not in _POLY:
        raise UnsupportedScalingNode(
            f"{nm}nm has no vendored Table 5 coefficients ({sorted(_POLY)}) — "
            "this module does not interpolate"
        )
    delay, energy, power = _POLY[nm]
    if kind == "delay":
        a3, a2, a1, a0 = delay
        return a3 * voltage**3 + a2 * voltage**2 + a1 * voltage + a0
    coeffs = energy if kind == "energy" else power
    a2, a1, a0 = coeffs
    return a2 * voltage**2 + a1 * voltage + a0


def _metric_scaling_factor(
    kind: str, from_nm: int, to_nm: int, *, method: str,
    v_from: float | None, v_to: float | None,
) -> tuple[float, str]:
    """(factor, note): value(to) = value(from) / factor."""
    column = {"delay": 1, "energy": 2, "power": 3}[kind]
    if method == "measured":
        for nm in (from_nm, to_nm):
            if nm not in _MEASURED_INVERTER:
                raise UnsupportedScalingNode(
                    f"{nm}nm has no vendored Table 2 measured row "
                    f"({sorted(_MEASURED_INVERTER)}) — use method='polynomial' with "
                    "voltages, or a vendored node"
                )
        factor = _MEASURED_INVERTER[from_nm][column] / _MEASURED_INVERTER[to_nm][column]
        return factor, (
            f"{kind} scaled {from_nm}nm -> {to_nm}nm by measured FO4-inverter ratio "
            f"{factor:.4g} at ITRS nominal voltages ({_TIMING_CITATION}, Table 2)"
        )
    if method == "polynomial":
        vf = _MEASURED_INVERTER[from_nm][0] if v_from is None and from_nm in _MEASURED_INVERTER else v_from
        vt = _MEASURED_INVERTER[to_nm][0] if v_to is None and to_nm in _MEASURED_INVERTER else v_to
        if vf is None or vt is None:
            raise ValueError("polynomial method needs voltages (v_from/v_to) for nodes "
                             "without a vendored nominal")
        factor = _factor_at(from_nm, kind, vf) / _factor_at(to_nm, kind, vt)
        caveat = (
            "; caveat: at nominal voltages the energy/power polynomials deviate ~1.8x from "
            "the measured Table 2 points (D257) — prefer method='measured' at nominals"
            if kind in ("energy", "power") else ""
        )
        return factor, (
            f"{kind} scaled {from_nm}nm@{vf}V -> {to_nm}nm@{vt}V by Eq.5-7 polynomial "
            f"factor {factor:.4g} ({_TIMING_CITATION}, Table 5{caveat})"
        )
    raise ValueError(f"method={method!r} must be 'measured' or 'polynomial'")


# Independent reference (docs/decisions.md D258): the ASAP7 fakeram macros OpenROAD's own
# flow ships — fakeram7_256x32, 8192 bits at 0.7V: 351 um2 LEF footprint (0.0429 um2/bit),
# 218 ps cell_rise, 128.9 uW leakage (15.7 nW/bit). Checked against our chain:
#   area     0.0367 um2/bit  = 0.86x reference — CLOSE, and on the optimistic side as stated
#   access   66.8 ps         = 0.31x reference — 3.2x TOO FAST, the inverter proxy failing
#   leakage  23.9 nW/bit     = 1.52x reference — same order, conservative direction
# The delay gap is the load-bearing finding: an FO4 inverter chain scales with transistor
# speed, while an SRAM access is dominated by bitline/wordline RC that does NOT scale with
# it. So delay scaling is REFUSED by default here; callers who want it must opt in and are
# told the measured discrepancy.
# The reference macro whose published access time anchors ours, and its geometry so the
# same shape can be re-characterized for the ratio below (docs/decisions.md D259).
REFERENCE_MACRO = "fakeram7_256x32"
REFERENCE_ACCESS_NS = 0.218
REFERENCE_DEPTH, REFERENCE_WORD_BITS = 256, 32


def anchored_access_ns(
    config_access_ns: float, reference_access_ns_same_node: float
) -> tuple[float, str]:
    """Access time for one macro: the published 7nm reference value, scaled by CACTI's OWN
    access-time ratio between this configuration and the reference geometry AT THE SAME NODE
    (docs/decisions.md D259).

    Why this shape: the fakeram7 family publishes ONE access time (218 ps) for every size
    from 1344 to 79872 bits — measured, and it means the reference models area but not
    timing. CACTI, by contrast, does model the size and aspect-ratio dependence real SRAM
    has (measured at 32nm: 148.8 ps at the reference geometry, 265.1 ps at 4KBx64, 315.0 ps
    at 32KBx64). Taking the RATIO at a single node cancels the technology term entirely — so
    this needs no delay-scaling factor, which is what D258 measured to be invalid for SRAM
    anyway. Absolute from the published macro; relative from the tool's own array model.
    """
    if reference_access_ns_same_node <= 0:
        raise ValueError("reference access time must be positive")
    ratio = config_access_ns / reference_access_ns_same_node
    return REFERENCE_ACCESS_NS * ratio, (
        f"access time = {REFERENCE_ACCESS_NS * 1000:.0f} ps ({REFERENCE_MACRO} published "
        f"ASAP7 macro, OpenROAD-flow-scripts) x {ratio:.4g} (CACTI's own access ratio, this "
        f"configuration vs the reference geometry {REFERENCE_DEPTH}x{REFERENCE_WORD_BITS} at "
        "the same node — technology cancels in the ratio); the reference family publishes a "
        "single access time for every size, so its size dependence comes from CACTI"
    )


def scale_delay_ns(
    delay_ns: float, *, from_nm: int, to_nm: int, method: str = "measured",
    v_from: float | None = None, v_to: float | None = None,
    allow_inverter_proxy: bool = False,
) -> tuple[float, str]:
    """REFUSED by default below 22nm-class targets (D258): validated against the ASAP7
    fakeram7 reference, inverter-ratio delay scaling produced 66.8 ps where the reference
    macro says 218 ps — 3.2x too fast, because SRAM access is bitline-RC dominated while an
    FO4 chain is transistor-speed dominated. Use `reference_access_ns` instead, or pass
    `allow_inverter_proxy=True` to take the proxy with its measured error stated."""
    if not allow_inverter_proxy:
        raise UnsupportedScalingNode(
            "inverter-ratio delay scaling is not valid for SRAM access time — measured 3.2x "
            "too fast against the ASAP7 fakeram7_256x32 reference (218 ps), docs/decisions.md "
            "D258. Use reference_access_ns(bits), or pass allow_inverter_proxy=True to "
            "accept the proxy knowing its error."
        )
    factor, note = _metric_scaling_factor("delay", from_nm, to_nm, method=method,
                                          v_from=v_from, v_to=v_to)
    return delay_ns / factor, note + (
        "; WARNING: inverter proxy, measured 3.2x optimistic vs the ASAP7 fakeram7 "
        "reference for SRAM access (D258)"
    )


def scale_energy_pj(
    energy_pj: float, *, from_nm: int, to_nm: int, method: str = "measured",
    v_from: float | None = None, v_to: float | None = None,
) -> tuple[float, str]:
    factor, note = _metric_scaling_factor("energy", from_nm, to_nm, method=method,
                                          v_from=v_from, v_to=v_to)
    return energy_pj / factor, note


def scale_power_w(
    power_w: float, *, from_nm: int, to_nm: int, method: str = "measured",
    v_from: float | None = None, v_to: float | None = None,
) -> tuple[float, str]:
    """Leakage scaling by the measured inverter power ratio. Validated (D258) against the
    ASAP7 fakeram7_256x32 reference: our scaled 4KB leakage is 23.9 nW/bit vs the
    reference's 15.7 nW/bit — 1.5x, same order and on the CONSERVATIVE side, so this one
    survives the check; the note says so."""
    factor, note = _metric_scaling_factor("power", from_nm, to_nm, method=method,
                                          v_from=v_from, v_to=v_to)
    return power_w / factor, note + (
        "; validated within 1.5x (conservative) of the ASAP7 fakeram7 reference leakage "
        "per bit (D258)"
    )
