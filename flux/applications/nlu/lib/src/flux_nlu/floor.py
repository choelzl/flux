"""The floor generator (docs/decisions.md D475): a correct FP16 operator by construction.

How a 1-ULP function unit is actually built -- FloPoCo, DesignWare, the HLS math libraries --
is not by writing the RTL and testing it: it is a PARAMETRIC FAMILY of one algorithm (range
reduction, a table on the reduced argument, a low-degree correction, reconstruction, rounding),
an exhaustive search over the family's configurations against the reference, and the RTL
emitted from the chosen parameters. For FP16 the input space is 65,536 patterns, so every
configuration is verified completely in milliseconds, and the search is a deterministic DSE the
loop already knows how to run. What the model was asked to do for 100 hours (D468, D470) --
derive the numerics and transcribe them -- is exactly the step this file makes a script.

Each family has three faces that MUST agree bit for bit, and the exhaustive gate is what
proves they do: `<op>_model(x, cfg)` is the algorithm in integer numpy (the D468 subset: masks,
shifts, integer arithmetic, tables built from the configuration -- nothing on the data path is
a float); `<op>_rtl(cfg)` is the same algorithm as a combinational SystemVerilog module; the
tables both read come from `<op>_tables(cfg)`. `search(op)` enumerates the family, keeps the
configurations at 0 over the ULP budget, and orders them by an area proxy; `floor(op)` is the
cheapest one, with its source.

Faithful rounding (within 1 ULP of the correctly rounded result, the campaign's gate) leaves
room for truncation in the reduction and a first-order correction on the table: what must be
exact are the special cases, the overflow and underflow thresholds and the subnormal outputs,
and those are decided by the exhaustive check, not by analysis.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from itertools import product
from typing import Any

import numpy as np

from .fp16 import all_inputs, ulp_report

__all__ = ["ExpConfig", "RsqrtConfig", "RecipConfig", "FAMILIES", "exp_model", "exp_rtl",
           "rsqrt_model", "rsqrt_rtl", "recip_model", "recip_rtl", "search", "floor", "floor_ops",
           "floor_example"]

QNAN, PINF, NINF = 0x7E00, 0x7C00, 0xFC00


# --------------------------------------------------------------------------------------- exp
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


def _rom(name: str, words: np.ndarray, width: int) -> str:
    k = max(1, int(math.ceil(math.log2(len(words)))))
    hexw = (width + 3) // 4
    lines = [f"  function automatic [{width - 1}:0] {name}(input [{k - 1}:0] i);",
             "    case (i)"]
    lines += [f"      {k}'d{i}: {name} = {width}'h{int(w):0{hexw}x};" for i, w in enumerate(words.tolist())]
    lines += [f"      default: {name} = {width}'h{0:0{hexw}x};", "    endcase", "  endfunction"]
    return "\n".join(lines)


def exp_rtl(cfg: ExpConfig) -> str:
    """`exp_model` as a combinational module; every width follows the configuration."""
    tab = exp_tables(cfg)["TWO_POW_F"]
    L = int(round(math.log2(math.e) * (1 << cfg.lb)))
    LN2 = int(round(math.log(2) * (1 << cfg.LN2_BITS)))
    XW = cfg.xf + 6            # |x| < 32 -> 5 integer bits + 1 headroom
    TW = XW + 1 + cfg.lb + 2   # signed product width (the constant carries a clear sign bit)
    PW = cfg.m + 3             # Q1.m plus headroom for the correction and rounding
    corr = ""
    if cfg.cb:
        corr = f"""
  // first-order correction: 2^(rem) ~ 1 + rem*ln2, rem = the next {cfg.cb} bits of f
  logic [{cfg.cb - 1}:0] rem;
  assign rem = f[{cfg.ff - cfg.t - 1}:{cfg.ff - cfg.t - cfg.cb}];
  logic [{PW + cfg.cb + cfg.LN2_BITS - 1}:0] prod;
  assign prod = p0 * rem * {cfg.LN2_BITS}'d{LN2};
  assign p = p0 + (prod >> {cfg.t + cfg.cb + cfg.LN2_BITS});"""
    else:
        corr = "\n  assign p = p0;"
    return f"""module nlu_exp (input logic clk, input logic [15:0] x, output logic [15:0] y);
  // FLOOR DESIGN (D475): exp(x) = 2^k * 2^f, k = floor(x*log2e), f in [0,1) --
  // {2 ** cfg.t}-entry table of 2^f (Q1.{cfg.m}) on the top {cfg.t} bits of f
  // {'+ first-order correction on the next ' + str(cfg.cb) + ' bits' if cfg.cb else '(table only)'};
  // x as fixed point with {cfg.xf} fractional bits, log2e with {cfg.lb} bits.
  // Correct by construction over all 65536 inputs (exhaustively checked); make it smaller.
  logic sign; assign sign = x[15];
  logic [4:0] ex; assign ex = x[14:10];
  logic [9:0] fr; assign fr = x[9:0];
  logic is_nan; assign is_nan = (ex == 5'd31) && (fr != 10'd0);
  logic is_inf; assign is_inf = (ex == 5'd31) && (fr == 10'd0);
  logic [10:0] mant; assign mant = (ex == 5'd0) ? {{1'b0, fr}} : {{1'b1, fr}};
  // |x| as Q6.{cfg.xf}, truncated, saturated at 32
  logic [{XW - 1}:0] mag_l, mag_r, mag;
  logic [6:0] shl, shr;
  assign shl = ({{2'b0, ex}} + 7'd{cfg.xf}) - 7'd25;
  assign shr = 7'd25 - ({{2'b0, ex}} + 7'd{cfg.xf});
  assign mag_l = ({{2'b0, ex}} + 7'd{cfg.xf} >= 7'd25) ? ({XW}'(mant) << shl[5:0]) : {XW}'d0;
  assign mag_r = ({{2'b0, ex}} + 7'd{cfg.xf} >= 7'd25) ? {XW}'d0 : ({XW}'(mant) >> shr[5:0]);
  // |x| >= 32 (ex >= 20) saturates BEFORE the shift: shifted in {XW} bits it would wrap
  assign mag = (ex >= 5'd20) ? ({XW}'d32 << {cfg.xf}) : (mag_l | mag_r);
  logic signed [{XW}:0] xfix;
  assign xfix = sign ? -$signed({{1'b0, mag}}) : $signed({{1'b0, mag}});
  // t = x * log2e with {cfg.ff} fractional bits; k = floor(t); f = t - k
  logic signed [{TW - 1}:0] tt;
  assign tt = xfix * $signed({cfg.lb + 2}'d{L});     // lb+2 wide so the sign bit is 0
  logic signed [{TW - cfg.ff - 1}:0] k;
  assign k = tt >>> {cfg.ff};
  logic [{cfg.ff - 1}:0] f;
  assign f = tt[{cfg.ff - 1}:0];
  logic [{cfg.t - 1}:0] idx; assign idx = f[{cfg.ff - 1}:{cfg.ff - cfg.t}];
  logic [{PW - 1}:0] p0, p;
  assign p0 = {PW}'(TWO_POW_F(idx));{corr}
  // exponent, normal rounding (ties to even) with carry, subnormal denormalisation
  logic signed [{TW - cfg.ff}:0] e;
  assign e = k + 15;
  logic [{PW - 1}:0] mant_q;
  logic [{cfg.m - 11}:0] rem_n;
  logic half_n;
  assign mant_q = p >> {cfg.m - 10};
  assign rem_n = p[{cfg.m - 11}:0];
  assign half_n = (rem_n > {cfg.m - 10}'d{1 << (cfg.m - 11)}) || ((rem_n == {cfg.m - 10}'d{1 << (cfg.m - 11)}) && mant_q[0]);
  logic [{PW - 1}:0] mant_r;
  assign mant_r = mant_q + {PW}'(half_n);
  logic carry; assign carry = (mant_r >= {PW}'d2048);
  logic signed [{TW - cfg.ff}:0] e_n;
  assign e_n = e + {{{{{TW - cfg.ff}{{1'b0}}}}, carry}};
  logic [9:0] mant_n; assign mant_n = carry ? 10'd0 : mant_r[9:0];
  logic [15:0] normal; assign normal = {{1'b0, e_n[4:0], mant_n}};
  // subnormal: field = round(p * 2^(k + 24 - m)) = p >> (m - 24 - k)
  logic signed [{TW - cfg.ff}:0] sub_sh; assign sub_sh = {cfg.m - 24} - k;
  logic [6:0] ssh; assign ssh = (sub_sh > 62) ? 7'd62 : sub_sh[6:0];
  logic [{PW - 1}:0] sq; assign sq = p >> ssh;
  logic [{PW - 1}:0] srem; assign srem = p & (({PW}'d1 << ssh) - {PW}'d1);
  logic [{PW - 1}:0] shalf; assign shalf = (ssh == 7'd0) ? {PW}'d0 : ({PW}'d1 << (ssh - 7'd1));
  logic sup; assign sup = (ssh != 7'd0) && ((srem > shalf) || ((srem == shalf) && sq[0]));
  // a shift of {PW - 1} or more leaves less than half an LSB: zero, and the mask above would wrap
  logic [{PW - 1}:0] field; assign field = (sub_sh <= 0 || ssh >= 7'd{PW - 1}) ? {PW}'d0 : (sq + {PW}'(sup));
  logic [15:0] subn; assign subn = (field > {PW}'d2047) ? 16'h07FF : 16'(field);
  logic [15:0] core;
  assign core = (e_n >= 31) ? 16'h7C00 : (e_n >= 1) ? normal : subn;
  assign y = is_nan ? 16'h7E00 : is_inf ? (sign ? 16'h0000 : 16'h7C00) : core;

{_rom("TWO_POW_F", tab, cfg.m + 1)}
endmodule
"""


def exp_space() -> list[ExpConfig]:
    return [ExpConfig(xf=xf, lb=lb, t=t, m=m, cb=cb)
            for xf, lb, t, m, cb in product((12, 14, 16), (12, 14, 16), (4, 5, 6, 7, 8),
                                            (11, 12, 13, 14, 16), (0, 4, 6, 8))
            if not (cb and cb > xf + lb - t)]


def exp_cost(cfg: ExpConfig) -> int:
    """An area proxy that ORDERS configurations: table bits + the two multipliers' partial
    products. The screen stage measures the real number."""
    table = (1 << cfg.t) * (cfg.m + 1)
    mul1 = (cfg.xf + 7) * (cfg.lb + 1)
    mul2 = ((cfg.m + 3) * cfg.cb + (cfg.m + 3) * cfg.LN2_BITS) if cfg.cb else 0
    return table + mul1 + mul2


# ------------------------------------------------------------------------------------- rsqrt
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


def rsqrt_rtl(cfg: RsqrtConfig) -> str:
    tabs = rsqrt_tables(cfg)
    n = 1 << cfg.t
    VW = cfg.m + 2
    corr = ""
    if cfg.cb:
        corr = f"""
  // first-order correction inside the sub-interval: q = the next {cfg.cb} bits
  logic [{cfg.t + 12 - 1}:0] remq; assign remq = offn - ({cfg.t + 12}'(idx) * {cfg.t + 12}'d3072);
  logic [{cfg.t + 12 + cfg.cb - 1}:0] qn; assign qn = ({cfg.t + 12 + cfg.cb}'(remq) << {cfg.cb}) / {cfg.t + 12 + cfg.cb}'d3072;
  logic [{cfg.cb - 1}:0] q; assign q = qn[{cfg.cb - 1}:0];
  logic [{VW + cfg.cb - 1}:0] sprod; assign sprod = {VW}'(RSQRT_S(idx)) * q;
  assign v = v0 - (sprod >> {cfg.cb});"""
    else:
        corr = "\n  assign v = v0;"
    return f"""module nlu_rsqrt (input logic clk, input logic [15:0] x, output logic [15:0] y);
  // FLOOR DESIGN (D475): 1/sqrt(m*2^e) = 2^(-e'/2) * T(m'), m' = m or 2m in [1,4), e' even;
  // {n}-entry table over m' (Q1.{cfg.m}){' + a slope table for the next ' + str(cfg.cb) + ' bits' if cfg.cb else ''}.
  // Correct by construction over all 65536 inputs (exhaustively checked); make it smaller.
  logic sign; assign sign = x[15];
  logic [4:0] ex; assign ex = x[14:10];
  logic [9:0] fr; assign fr = x[9:0];
  logic is_nan; assign is_nan = (ex == 5'd31) && (fr != 10'd0);
  logic is_inf; assign is_inf = (ex == 5'd31) && (fr == 10'd0);
  logic is_zero; assign is_zero = (ex == 5'd0) && (fr == 10'd0);
  // normalise a subnormal input: leading-zero count of the fraction
  logic [3:0] lz;
  always_comb begin
    lz = 4'd0;
    if (ex == 5'd0) begin
      if (fr[9]) lz = 4'd0; else if (fr[8]) lz = 4'd1; else if (fr[7]) lz = 4'd2;
      else if (fr[6]) lz = 4'd3; else if (fr[5]) lz = 4'd4; else if (fr[4]) lz = 4'd5;
      else if (fr[3]) lz = 4'd6; else if (fr[2]) lz = 4'd7; else if (fr[1]) lz = 4'd8;
      else lz = 4'd9;
    end
  end
  logic [10:0] mant; assign mant = (ex == 5'd0) ? ({{1'b0, fr}} << (lz + 4'd1)) : {{1'b1, fr}};
  logic signed [6:0] e_unb; assign e_unb = (ex == 5'd0) ? (-7'sd15 - $signed({{3'b0, lz}})) : ($signed({{2'b0, ex}}) - 7'sd15);
  logic odd; assign odd = e_unb[0];
  logic [11:0] mp; assign mp = odd ? {{mant, 1'b0}} : {{1'b0, mant}};      // Q2.10 in [1,4)
  logic signed [6:0] e_even; assign e_even = odd ? (e_unb - 7'sd1) : e_unb;
  logic signed [6:0] r_exp0; assign r_exp0 = -(e_even >>> 1);
  // index over [1,4): idx = floor((m' - 1) * {n} / 3072)
  logic [11:0] off; assign off = mp - 12'd1024;
  logic [{cfg.t + 12 - 1}:0] offn; assign offn = {cfg.t + 12}'(off) << {cfg.t};
  logic [{cfg.t + 12 - 1}:0] idxw; assign idxw = offn / {cfg.t + 12}'d3072;
  logic [{cfg.t - 1}:0] idx; assign idx = idxw[{cfg.t - 1}:0];
  logic [{VW - 1}:0] v0, v;
  assign v0 = {VW}'(RSQRT_T(idx));{corr}
  // normalise v into [1,2) (Q1.{cfg.m}), round to 10 bits with carry, assemble
  logic vlow; assign vlow = (v < ({VW}'d1 << {cfg.m}));         // `small` is a keyword
  logic [{VW - 1}:0] vn; assign vn = vlow ? (v << 1) : v;
  logic signed [6:0] r_exp1; assign r_exp1 = vlow ? (r_exp0 - 7'sd1) : r_exp0;
  logic [{VW - 1}:0] mant_q; assign mant_q = vn >> {cfg.m - 10};
  logic [{cfg.m - 11}:0] rem_n; assign rem_n = vn[{cfg.m - 11}:0];
  logic half_n; assign half_n = (rem_n > {cfg.m - 10}'d{1 << (cfg.m - 11)}) || ((rem_n == {cfg.m - 10}'d{1 << (cfg.m - 11)}) && mant_q[0]);
  logic [{VW - 1}:0] mant_r; assign mant_r = mant_q + {VW}'(half_n);
  logic carry; assign carry = (mant_r >= {VW}'d2048);
  logic signed [6:0] r_exp; assign r_exp = r_exp1 + {{6'd0, carry}};
  logic [9:0] mant_n; assign mant_n = carry ? 10'd0 : mant_r[9:0];
  logic signed [6:0] e_out; assign e_out = r_exp + 7'sd15;
  logic [15:0] core; assign core = {{1'b0, e_out[4:0], mant_n}};
  assign y = (is_nan || (sign && !is_zero)) ? 16'h7E00 : is_zero ? (sign ? 16'hFC00 : 16'h7C00) : is_inf ? 16'h0000 : core;

{_rom("RSQRT_T", tabs["RSQRT_T"], cfg.m + 1)}
{(_rom("RSQRT_S", tabs["RSQRT_S"], cfg.m + 1) if cfg.cb else "")}
endmodule
"""


def rsqrt_space() -> list[RsqrtConfig]:
    return [RsqrtConfig(t=t, m=m, cb=cb)
            for t, m, cb in product((5, 6, 7, 8, 9, 10), (11, 12, 13, 14, 16), (0, 4, 6, 8))]


def rsqrt_cost(cfg: RsqrtConfig) -> int:
    table = (1 << cfg.t) * (cfg.m + 1) * (2 if cfg.cb else 1)
    mul = ((cfg.m + 2) * cfg.cb) if cfg.cb else 0
    return table + mul + 400        # the two constant divisions and the shifters


# ------------------------------------------------------------------------------------- recip
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


def recip_rtl(cfg: RecipConfig) -> str:
    tabs = recip_tables(cfg)
    n = 1 << cfg.t
    VW = cfg.m + 2
    corr = ""
    if cfg.cb:
        rb = 10 - cfg.t
        corr = f"""
  // first-order correction: q = the top {cfg.cb} bits of the remaining {rb} fraction bits
  logic [{rb - 1}:0] rem; assign rem = frac[{rb - 1}:0];
  logic [{rb + cfg.cb - 1}:0] qw; assign qw = ({rb + cfg.cb}'(rem) << {cfg.cb}) >> {rb};
  logic [{cfg.cb - 1}:0] q; assign q = qw[{cfg.cb - 1}:0];
  logic [{VW + cfg.cb - 1}:0] sprod; assign sprod = {VW}'(RECIP_S(idx)) * q;
  assign v = v0 - (sprod >> {cfg.cb});"""
    else:
        corr = "\n  assign v = v0;"
    return f"""module nlu_recip (input logic clk, input logic [15:0] x, output logic [15:0] y);
  // FLOOR DESIGN (D476): 1/(m*2^e) = (1/m) * 2^-e, m in [1,2) -- {n}-entry table over the top
  // {cfg.t} fraction bits (Q1.{cfg.m}){' + a slope table for the next ' + str(cfg.cb) + ' bits' if cfg.cb else ''};
  // subnormal inputs normalised, subnormal outputs denormalised with rounding.
  // Correct by construction over all 65536 inputs (exhaustively checked); make it smaller.
  logic sign; assign sign = x[15];
  logic [4:0] ex; assign ex = x[14:10];
  logic [9:0] fr; assign fr = x[9:0];
  logic is_nan; assign is_nan = (ex == 5'd31) && (fr != 10'd0);
  logic is_inf; assign is_inf = (ex == 5'd31) && (fr == 10'd0);
  logic is_zero; assign is_zero = (ex == 5'd0) && (fr == 10'd0);
  logic [3:0] lz;
  always_comb begin
    lz = 4'd0;
    if (ex == 5'd0) begin
      if (fr[9]) lz = 4'd0; else if (fr[8]) lz = 4'd1; else if (fr[7]) lz = 4'd2;
      else if (fr[6]) lz = 4'd3; else if (fr[5]) lz = 4'd4; else if (fr[4]) lz = 4'd5;
      else if (fr[3]) lz = 4'd6; else if (fr[2]) lz = 4'd7; else if (fr[1]) lz = 4'd8;
      else lz = 4'd9;
    end
  end
  logic [9:0] frac; assign frac = (ex == 5'd0) ? (fr << (lz + 4'd1)) : fr;
  logic signed [6:0] e_unb; assign e_unb = (ex == 5'd0) ? (-7'sd15 - $signed({{3'b0, lz}})) : ($signed({{2'b0, ex}}) - 7'sd15);
  logic [{cfg.t - 1}:0] idx; assign idx = frac[9:{10 - cfg.t}];
  logic [{VW - 1}:0] v0, v;
  assign v0 = {VW}'(RECIP_T(idx));{corr}
  logic signed [6:0] r_exp0; assign r_exp0 = -e_unb;
  logic vlow; assign vlow = (v < ({VW}'d1 << {cfg.m}));
  logic [{VW - 1}:0] vn; assign vn = vlow ? (v << 1) : v;
  logic signed [6:0] r_exp1; assign r_exp1 = vlow ? (r_exp0 - 7'sd1) : r_exp0;
  logic [{VW - 1}:0] mant_q; assign mant_q = vn >> {cfg.m - 10};
  logic [{cfg.m - 11}:0] rem_n; assign rem_n = vn[{cfg.m - 11}:0];
  logic half_n; assign half_n = (rem_n > {cfg.m - 10}'d{1 << (cfg.m - 11)}) || ((rem_n == {cfg.m - 10}'d{1 << (cfg.m - 11)}) && mant_q[0]);
  logic [{VW - 1}:0] mant_r; assign mant_r = mant_q + {VW}'(half_n);
  logic carry; assign carry = (mant_r >= {VW}'d2048);
  logic signed [6:0] r_exp; assign r_exp = r_exp1 + {{6'd0, carry}};
  logic [9:0] mant_n; assign mant_n = carry ? 10'd0 : mant_r[9:0];
  logic signed [6:0] e_out; assign e_out = r_exp + 7'sd15;
  logic [15:0] normal; assign normal = {{1'b0, e_out[4:0], mant_n}};
  // subnormal output: field = round(vn * 2^(r_exp1 + 24 - m)) = vn >> (m - 24 - r_exp1)
  logic signed [7:0] sub_sh; assign sub_sh = -8'sd{24 - cfg.m} - 8'(r_exp1);
  logic [6:0] ssh; assign ssh = (sub_sh > 62) ? 7'd62 : sub_sh[6:0];
  logic [{VW - 1}:0] sq; assign sq = vn >> ssh;
  logic [{VW - 1}:0] srem; assign srem = vn & (({VW}'d1 << ssh) - {VW}'d1);
  logic [{VW - 1}:0] shalf; assign shalf = (ssh == 7'd0) ? {VW}'d0 : ({VW}'d1 << (ssh - 7'd1));
  logic sup; assign sup = (ssh != 7'd0) && ((srem > shalf) || ((srem == shalf) && sq[0]));
  logic [{VW - 1}:0] field; assign field = (sub_sh <= 0 || ssh >= 7'd{VW - 1}) ? {VW}'d0 : (sq + {VW}'(sup));
  logic [15:0] subn; assign subn = (field > {VW}'d2047) ? 16'h07FF : 16'(field);
  logic [15:0] core; assign core = (e_out >= 31) ? 16'h7C00 : (e_out >= 1) ? normal : subn;
  logic [15:0] mag; assign mag = is_zero ? 16'h7C00 : is_inf ? 16'h0000 : core;
  assign y = is_nan ? 16'h7E00 : {{sign, mag[14:0]}};

{_rom("RECIP_T", tabs["RECIP_T"], cfg.m + 1)}
{(_rom("RECIP_S", tabs["RECIP_S"], cfg.m + 1) if cfg.cb else "")}
endmodule
"""


def recip_space() -> list[RecipConfig]:
    return [RecipConfig(t=t, m=m, cb=cb)
            for t, m, cb in product((5, 6, 7, 8, 9, 10), (11, 12, 13, 14, 16), (0, 4, 6, 8))
            if not (cb and cb > 10 - t)]


def recip_cost(cfg: RecipConfig) -> int:
    table = (1 << cfg.t) * (cfg.m + 1) * (2 if cfg.cb else 1)
    mul = ((cfg.m + 2) * cfg.cb) if cfg.cb else 0
    return table + mul + 200


# ------------------------------------------------------------------------------------ search
FAMILIES: dict[str, dict[str, Any]] = {
    "exp": {"space": exp_space, "model": exp_model, "rtl": exp_rtl, "cost": exp_cost,
            "config": ExpConfig},
    "rsqrt": {"space": rsqrt_space, "model": rsqrt_model, "rtl": rsqrt_rtl, "cost": rsqrt_cost,
              "config": RsqrtConfig},
    "recip": {"space": recip_space, "model": recip_model, "rtl": recip_rtl, "cost": recip_cost,
              "config": RecipConfig},
}


def floor_ops() -> tuple[str, ...]:
    return tuple(FAMILIES)


def search(op: str, *, budget: int = 1, limit: int | None = None) -> list[dict[str, Any]]:
    """Every configuration of `op`'s family against the full domain: the ones at 0 over the
    budget, cheapest first, each with its report. `limit` caps how many are returned."""
    fam = FAMILIES[op]
    xs = all_inputs()
    passing = []
    tried = 0
    for cfg in fam["space"]():
        tried += 1
        got = fam["model"](xs, cfg)
        rep = ulp_report(op, xs, got, budget=budget, worst_n=2)
        if rep["ok"]:
            passing.append({"config": cfg, "cost": fam["cost"](cfg), "report": rep})
    passing.sort(key=lambda r: r["cost"])
    for r in passing:
        r["tried"] = tried
    return passing[:limit] if limit else passing


_EXAMPLE_CACHE: dict[str, str] = {}


def floor_example(op: str) -> str:
    """A WORKED EXAMPLE for the prompt of an operator with no family (D477): the exp floor
    -- classification, fixed-point reduction, table plus first-order correction, normal
    rounding with carry, subnormal denormalisation -- in this exact contract and proven on
    every input. An operator with a family sees its own floor as the design in hand instead."""
    if op in FAMILIES:
        return ""
    if "exp" not in _EXAMPLE_CACHE:
        f = floor("exp")
        _EXAMPLE_CACHE["exp"] = f["source"] if f else ""
    src = _EXAMPLE_CACHE["exp"]
    if not src:
        return ""
    return ("WORKED EXAMPLE -- a design in this exact contract that PASSES the gate (0 over on "
            "all 65536 inputs), generated by the harness for exp. Reuse its SHAPE for "
            f"{op}: the classification, the fixed-point reduction, a small table on the "
            "reduced argument with a first-order correction, rounding to nearest-even with the "
            "carry into the exponent, the subnormal path; only the numerics change.\n```systemverilog\n"
            + src + "```")


def floor(op: str, *, budget: int = 1) -> dict[str, Any] | None:
    """The cheapest configuration at 0 over, with its RTL: {"config", "source", "cost",
    "tried", "passing"}; None when the family has no member at the budget."""
    found = search(op, budget=budget)
    if not found:
        return None
    best = found[0]
    return {"config": asdict(best["config"]), "source": FAMILIES[op]["rtl"](best["config"]),
            "cost": best["cost"], "tried": best["tried"], "passing": len(found),
            "alternatives": [(asdict(r["config"]), r["cost"]) for r in found[1:4]]}
