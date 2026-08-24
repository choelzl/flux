INTERFACE (this operator's module, instantiated exactly as written):
  module nlu_{op} (input wire clk, input wire [15:0] x, output wire [15:0] y);
* x and y are IEEE FP16 bit patterns; combinational designs still declare clk.
* Prefer a COMBINATIONAL module (latency 0) -- the operators are composed under one
  mux, so a pure `assign`/`always @*` module drops straight in.
* Declare every signal as `logic` (the harness rewrites `wire`/`reg` to `logic`
  and adds the `clk` port if you drop it, so those two cannot fail the build);
  drive each signal EITHER by one `assign` OR inside `always` blocks, never both.
* Sized literals must FIT: 11'd2048 needs 12 bits (a repeat offender).
* TABLE ORACLE -- you do NOT have to derive table constants yourself (that is where
  every previous attempt stalled). Add a "tables" list to your reply and the harness
  computes each table EXACTLY, rounds it, and inserts it into your module before
  `endmodule` as `function automatic [W-1:0] NAME(input [K-1:0] i)`; call it as
  `NAME(idx)`. Each request: {{"name": "TWO_POW_F", "func": "exp2", "lo": 0, "hi": 1,
  "entries": 32, "format": "ufixed", "frac_bits": 10}} -- func is one of exp2, exp,
  ln, log2, recip, rsqrt, sqrt, sigmoid, tanh, gelu, erf; entry i holds func at
  lo + i*(hi-lo)/entries (or the sub-interval's mid/right with "sample"); format
  ufixed (unsigned, frac_bits fractional bits, width fitted), sfixed, or fp16 (raw
  bit patterns). A small table on the REDUCED argument (e.g. 2^f for f in [0,1),
  32-64 entries, plus a linear correction from the remaining bits) is the workhorse
  and is exactly what the knowledge sheet means by lut/interpolation; only a table
  on the whole 16-bit input is the anti-pattern.
* Same SAFE SUBSET as always: assign / always @* / always @(posedge clk) / case /
  localparam / function; declare every signal at module scope; complete sized
  literals; one statement per line. Split on the sign bit x[15] before any
  magnitude compare (FP16 patterns do not order as unsigned once negative).
* A table you requested in an earlier attempt stays available: call it by name
  and the harness places it again; do not write your own copy of it.
* DECIDE BEFORE YOU WRITE. `source` is the finished module only: no deliberation
  in comments (sampled replies stopped mid-comment before `endmodule` three times
  in eight), at most one short comment per block, reasoning goes in `method`.