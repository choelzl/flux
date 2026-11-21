# Example applications

The problem documents of the loops bundled with Flux, as they ship. They show the document format
on real problems: software and RTL (primes, adder16, mul8, pto), memory and compute architecture
(bankmap, macarray), one unit of seven operators (nlu) and a cache prefetcher (prefetcher).

They are for reading: the scripts, libraries, traces and records they call are part of the Flux
source, which is not public. The documentation explains every field:
<https://choelzl.github.io/flux/>.


- [adder16](adder16/): `problem.yaml` -- An unsigned 16-bit adder (module `adder16`, inputs `a` and `b`, 17-bit sum `s`): the smallest that makes 2900 MHz placed on ASAP7, chosen from the architectures gen.py spells.
- [bankmap](bankmap/): `problem.yaml` -- An address-to-bank mapping for a memory of 8 banks that is conflict-free for 4 concurrent accesses at each of the strides 1, 8 and 16, for every start address of a 20-bit space: the cheapest one the ...
- [macarray](macarray/): `invent.problem.yaml`, `problem.yaml`, `workload.yaml` -- One MAC processing element, 8 lanes at the workload's precision (int8 x int8, accumulated): the smallest PE that makes 1000 MHz placed on ASAP7, chosen from every combination of multiplier, reducer ...
- [mul8](mul8/): `problem.yaml` -- A combinational signed 8x8 -> 16-bit multiplier in SystemVerilog: module `mul8` with inputs `a` and `w` (`input logic signed [7:0]`) and output `p` (`output logic signed [15:0]`), the smallest that ...
- [nlu](nlu/): `problem.yaml` -- One purely combinational SystemVerilog unit `nlu_fp16` computing the seven operators of an FP16 non-linear unit (recip, rsqrt, sigmoid, tanh, gelu, log, exp) under one select: each within 1 ULP of ...
- [prefetcher](prefetcher/): `invent.problem.yaml`, `problem.yaml` -- The L2 prefetcher configuration for 5G baseband workloads (FFT-heavy signal processing, channel-estimation matrix work, bursty packet buffers): Bingo, the partners that run beside it and their knobs.
- [primes](primes/): `problem.yaml` -- A Python function `count_primes(n: int) -> int` that returns how many primes are below n, as fast as possible for n up to 2,000,000.
- [pto](pto/): `problem.yaml` -- One combinational datapath, in pyCircuit, for the PTO ISA's elementwise tile units: what a vector engine lane computes for one element.
