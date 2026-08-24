"""The three hand-written FP16 families (exp, rsqrt, recip) in the integer subset, kept as
FIXTURES for the transpiler tests (D478): each has a known-good numpy face to compare the
emitted RTL against. They were the D475 floor generator until D507 cut it from the flow;
they never enter a campaign -- the flow's own work is what the campaign is for."""

from __future__ import annotations

import math
from pathlib import Path
from dataclasses import dataclass

import numpy as np

QNAN, PINF, NINF = 0x7E00, 0x7C00, 0xFC00


@dataclass(frozen=True)
class ExpConfig:
    """2^(x*log2e) = 2^k * 2^f. `xf`: fractional bits of x as fixed point; `lb`: bits of the
    log2(e) constant; `t`: table index bits (2^t entries of 2^f on [0,1)); `m`: fractional
    bits of the table's values (Q1.m); `cb`: bits of the remaining fraction used by the
    first-order correction 2^r ~ 1 + r ln2 (0 = table only)."""

    xf: int = 14
    lb: int = 14
    t: int = 6
    m: int = 13
    cb: int = 6

    LN2_BITS = 10           # the ln2 constant of the correction, Q0.10

    @property
    def ff(self) -> int:    # fractional bits of t = x*log2e
        return self.xf + self.lb


def _round_half_even(v: np.ndarray, sh: np.ndarray | int) -> np.ndarray:
    """v >> sh, rounded to nearest with ties to even, elementwise; sh may be an array."""
    sh = np.asarray(sh, dtype=np.int64)
    sh_c = np.clip(sh, 0, 62)
    q = v >> sh_c
    rem = v & ((np.int64(1) << sh_c) - 1)
    half = np.where(sh_c > 0, np.int64(1) << np.maximum(sh_c - 1, 0), np.int64(0))
    up = np.where(sh_c > 0, (rem > half) | ((rem == half) & ((q & 1) == 1)), False)
    return q + up.astype(np.int64)


def exp_tables(cfg: ExpConfig) -> dict[str, np.ndarray]:
    i = np.arange(1 << cfg.t)
    return {"TWO_POW_F": np.rint(np.exp2(i / (1 << cfg.t)) * (1 << cfg.m)).astype(np.int64)}


def exp_model(x: np.ndarray, cfg: ExpConfig) -> np.ndarray:
    """exp over every FP16 bit pattern in `x`, integer only on the data path."""
    x = x.astype(np.int64)
    sign = (x >> 15) & 1
    ex = (x >> 10) & 0x1F
    fr = x & 0x3FF
    is_nan = (ex == 31) & (fr != 0)
    is_inf = (ex == 31) & (fr == 0)
    mant = np.where(ex == 0, fr, fr | 0x400)
    # |x| as fixed point with xf fractional bits (truncated); |x| >= 32 saturates
    sh = ex + (cfg.xf - 25)
    mag = np.where(sh >= 0, mant << np.clip(sh, 0, 62), mant >> np.clip(-sh, 0, 62))
    mag = np.minimum(mag, np.int64(32) << cfg.xf)
    xfix = np.where(sign == 1, -mag, mag)
    tab = exp_tables(cfg)["TWO_POW_F"]
    L = int(round(math.log2(math.e) * (1 << cfg.lb)))
    tt = xfix * L                                   # ff fractional bits
    k = tt >> cfg.ff                                # floor (arithmetic shift)
    f = tt - (k << cfg.ff)                          # [0, 2^ff)
    idx = f >> (cfg.ff - cfg.t)
    p = tab[idx]                                    # Q1.m
    if cfg.cb:
        rem = (f >> (cfg.ff - cfg.t - cfg.cb)) & ((1 << cfg.cb) - 1)   # top cb bits after idx
        LN2 = int(round(math.log(2) * (1 << cfg.LN2_BITS)))
        # p * rem * ln2 / 2^(t + cb): rem/2^(t+cb) is the remaining fraction
        p = p + ((p * rem * LN2) >> (cfg.t + cfg.cb + cfg.LN2_BITS))
    e = k + 15
    # normal: round p (Q1.m) to 10 fractional bits, carry into the exponent
    mant_n = _round_half_even(p, cfg.m - 10)
    carry = mant_n >= 2048
    mant_n = np.where(carry, 1024, mant_n)
    e_n = e + carry.astype(np.int64)
    normal = (e_n << 10) | (mant_n & 0x3FF)
    # subnormal: field = round(p * 2^(k + 24 - m))
    sub_sh = cfg.m - 24 - k
    field = np.where(sub_sh <= 0, np.int64(0), _round_half_even(p, np.clip(sub_sh, 0, 62)))
    field = np.where(sub_sh <= 0, 0, field)       # would be normal; the branch below decides
    y = np.where(e_n >= 31, PINF, np.where(e_n >= 1, normal, np.minimum(field, 0x7FF)))
    y = np.where(sign == 1, y, y)   # exp is never negative
    y = np.where(is_nan, QNAN, np.where(is_inf, np.where(sign == 1, 0, PINF), y))
    return y.astype(np.uint16)


@dataclass(frozen=True)
class RsqrtConfig:
    """1/sqrt(m * 2^e) = 2^(-e'/2) * T(m') with m' = m or 2m in [1, 4) so e' is even.
    `t`: index bits of the seed table over m' in [1,4) (the top bit is the parity);
    `m`: fractional bits of the table's values; `cb`: bits of the first-order correction
    (a slope table of the same size, 0 = none)."""

    t: int = 7
    m: int = 12
    cb: int = 6
    SLOPE_BITS = 12

    @property
    def mp_bits(self) -> int:   # fraction bits of m' kept: the index and the correction
        return self.t + self.cb


def rsqrt_tables(cfg: RsqrtConfig) -> dict[str, np.ndarray]:
    n = 1 << cfg.t
    i = np.arange(n)
    lo = 1.0 + 3.0 * i / n                       # m' in [1, 4), n sub-intervals of width 3/n
    val = np.rint((1.0 / np.sqrt(lo)) * (1 << cfg.m)).astype(np.int64)
    tabs = {"RSQRT_T": val}
    if cfg.cb:
        hi = lo + 3.0 / n
        slope = (1.0 / np.sqrt(lo) - 1.0 / np.sqrt(hi))          # positive, per sub-interval
        tabs["RSQRT_S"] = np.rint(slope * (1 << cfg.m)).astype(np.int64)
    return tabs


def rsqrt_model(x: np.ndarray, cfg: RsqrtConfig) -> np.ndarray:
    x = x.astype(np.int64)
    sign = (x >> 15) & 1
    ex = (x >> 10) & 0x1F
    fr = x & 0x3FF
    is_nan = (ex == 31) & (fr != 0)
    is_inf = (ex == 31) & (fr == 0)
    is_zero = (ex == 0) & (fr == 0)
    # normalise a subnormal: m = fr << lz so that bit 10 is set; unbiased e = 1 - 15 - lz
    lz = np.zeros_like(fr)                                           # leading zeros of the 10-bit fraction
    for _ in range(9):
        lz = np.where((ex == 0) & (fr != 0) & (((fr << lz) & 0x200) == 0), lz + 1, lz)
    mant = np.where(ex == 0, (fr << (lz + 1)) & 0x7FF, fr | 0x400)    # 1.xxxxxxxxxx, 11 bits
    e_unb = np.where(ex == 0, -15 - lz, ex - 15)
    odd = (e_unb & 1) == 1
    mp = np.where(odd, mant << 1, mant)                              # m' in [1,4), Q2.10
    e_even = np.where(odd, e_unb - 1, e_unb)
    r_exp = -(e_even >> 1)                                           # result exponent (even/2 exact)
    # index the table over [1,4) uniformly: idx = floor((m' - 1) / 3 * 2^t); m' - 1 in [0,3) Q2.10
    tabs = rsqrt_tables(cfg)
    n = 1 << cfg.t
    off = np.maximum(mp - (1 << 10), 0)                              # Q2.10, [0, 3*1024); 0 for +-0
    # idx = off * n / (3*1024): integer division by a constant is a multiply by a reciprocal in RTL;
    # we use the exact quotient here and emit a small multiply-shift with the same result (checked)
    idx = (off * n) // (3 * 1024)
    v = tabs["RSQRT_T"][idx]
    if cfg.cb:
        # position inside the sub-interval, cb bits: frac = (off*n mod 3*1024) / (3*1024)
        rem = (off * n) - idx * (3 * 1024)                           # [0, 3072)
        q = (rem << cfg.cb) // (3 * 1024)                            # cb bits
        v = v - ((tabs["RSQRT_S"][idx] * q) >> cfg.cb)
    # v ~ 1/sqrt(m') in (0.5, 1] with m fractional bits; result = v * 2^r_exp
    # normalise v into [1,2): if v < 2^m then shift left 1 and r_exp -= 1
    small = v < (1 << cfg.m)
    v = np.where(small, v << 1, v)
    r_exp = np.where(small, r_exp - 1, r_exp)
    mant_q = _round_half_even(v, cfg.m - 10)
    carry = mant_q >= 2048
    mant_q = np.where(carry, 1024, mant_q)
    r_exp = r_exp + carry.astype(np.int64)
    e_out = r_exp + 15                                               # 1..30 for every finite x > 0
    y = (e_out << 10) | (mant_q & 0x3FF)
    y = np.where(is_nan | ((sign == 1) & ~is_zero), QNAN, y)
    y = np.where(is_zero, np.where(sign == 1, NINF, PINF), y)     # 1/sqrt(-0) = -Inf
    y = np.where(is_inf & (sign == 0), 0, y)
    return y.astype(np.uint16)


@dataclass(frozen=True)
class RecipConfig:
    """1/(m 2^e) = (1/m) 2^-e with m in [1,2) after normalisation. `t`: index bits of the table
    over m (the top t fraction bits); `m`: fractional bits of its values (Q1.m); `cb`: bits of
    the first-order correction from a slope table (0 = none)."""

    t: int = 6
    m: int = 13
    cb: int = 6


def recip_tables(cfg: RecipConfig) -> dict[str, np.ndarray]:
    n = 1 << cfg.t
    i = np.arange(n)
    lo = 1.0 + i / n
    tabs = {"RECIP_T": np.rint((1.0 / lo) * (1 << cfg.m)).astype(np.int64)}
    if cfg.cb:
        hi = lo + 1.0 / n
        tabs["RECIP_S"] = np.rint((1.0 / lo - 1.0 / hi) * (1 << cfg.m)).astype(np.int64)
    return tabs


def recip_model(x: np.ndarray, cfg: RecipConfig) -> np.ndarray:
    x = x.astype(np.int64)
    sign = (x >> 15) & 1
    ex = (x >> 10) & 0x1F
    fr = x & 0x3FF
    is_nan = (ex == 31) & (fr != 0)
    is_inf = (ex == 31) & (fr == 0)
    is_zero = (ex == 0) & (fr == 0)
    lz = np.zeros_like(fr)
    for _ in range(9):
        lz = np.where((ex == 0) & (fr != 0) & (((fr << lz) & 0x200) == 0), lz + 1, lz)
    frac = np.where(ex == 0, (fr << (lz + 1)) & 0x3FF, fr)            # the 10 fraction bits of m
    e_unb = np.where(ex == 0, -15 - lz, ex - 15)
    tabs = recip_tables(cfg)
    idx = frac >> (10 - cfg.t)
    v = tabs["RECIP_T"][idx]
    if cfg.cb:
        rem = frac & ((1 << (10 - cfg.t)) - 1)                          # 10-t bits
        q = (rem << cfg.cb) >> (10 - cfg.t)                             # the top cb bits of the rest
        v = v - ((tabs["RECIP_S"][idx] * q) >> cfg.cb)
    r_exp = -e_unb
    vlow = v < (1 << cfg.m)                                             # 1/m < 1: normalise
    v = np.where(vlow, v << 1, v)
    r_exp = np.where(vlow, r_exp - 1, r_exp)
    mant_q = _round_half_even(v, cfg.m - 10)
    carry = mant_q >= 2048
    mant_q = np.where(carry, 1024, mant_q)
    r_exp = r_exp + carry.astype(np.int64)
    e_out = r_exp + 15
    normal = (e_out << 10) | (mant_q & 0x3FF)
    # subnormal output (x > 16384): field = round(v * 2^(r_exp + 24 - m)) with v pre-carry
    sub_sh = cfg.m - 24 - (r_exp - carry.astype(np.int64))
    vpre = np.where(carry, v, v)                                        # v as normalised, before rounding
    field = np.where(sub_sh <= 0, np.int64(0), _round_half_even(vpre, np.clip(sub_sh, 0, 62)))
    y = np.where(e_out >= 31, PINF, np.where(e_out >= 1, normal, np.minimum(field, 0x7FF)))
    y = np.where(is_zero, PINF, np.where(is_inf, 0, y))
    y = y | (sign << 15)
    y = np.where(is_nan, QNAN, y)
    return y.astype(np.uint16)


# The cheapest configuration of each family at 0 over a 1-ULP budget, as the D475 search
# ordered them by its area proxy (exp: 971, rsqrt: 2312, recip: 1920).
FIXTURES = {
    "exp": {"model": exp_model, "tables": exp_tables, "config": ExpConfig(xf=12, lb=14, t=5, m=12, cb=8)},
    "rsqrt": {"model": rsqrt_model, "tables": rsqrt_tables, "config": RsqrtConfig(t=6, m=13, cb=8)},
    "recip": {"model": recip_model, "tables": recip_tables, "config": RecipConfig(t=6, m=12, cb=4)},
}


# ---- the NLU as a document (D519) --------------------------------------------------------
NLU_DOC = Path(__file__).resolve().parents[2] / "applications" / "nlu" / "nlu.problem.yaml"


def nlu_problem(*, ops: tuple[str, ...], ulp_budget: int = 1, clock_period_ps: float = 1250.0,
                target_mhz: float | None = None, test_rounds: int = 1, seed: int = 0):
    """The NLU problem the tests work on: `applications/nlu/nlu.problem.yaml` with these
    operators, gate and clock -- the document path (D519), the way `flux task run` builds it.
    `ops` is the RTL's operator order and the campaign identity's; the parts keep the
    document's order among them."""
    import yaml
    from flux_loop import PromptProblem, TaskSpec

    doc = yaml.safe_load(NLU_DOC.read_text())
    order = [str(p) for p in doc["parts"]]
    doc["parts"] = [o for o in order if o in ops] + [o for o in ops if o not in order]
    doc["params"] = {**doc["params"], "ops": list(ops), "ulp_budget": int(ulp_budget),
                     "clock_period_ps": float(clock_period_ps), "seed": int(seed), "test_rounds": int(test_rounds)}
    doc["campaign"] = {"study": "nlu", "ops": list(ops), "ulp_budget": int(ulp_budget),
                       "clock_period_ps": float(clock_period_ps)}
    doc["objectives"][0]["goal"] = float(target_mhz) if target_mhz else 1e6 / float(clock_period_ps)
    return PromptProblem(TaskSpec.from_dict(doc, base=NLU_DOC.parent))
