"""Measure one architecture: ZigZag (through the evaluator registry) for the cycles and energy
of the workload on it, and a first-order area estimate at 28 nm. Prints `name=value` lines.
`python measure.py ARCH WORKLOAD`."""

import sys

import yaml
from flux_evaluator_abi import Budget, Candidate, make_evaluator

MAC_MM2 = 0.004             # one int8 multiply-accumulate with its registers
SRAM_MM2_PER_KB = 0.005     # the global buffer's SRAM

arch = yaml.safe_load(open(sys.argv[1]))
workload = yaml.safe_load(open(sys.argv[2]))
result = make_evaluator("zigzag").evaluate(Candidate(workload=workload, arch=arch), Budget(),
                                           frozenset({"latency_cycles", "energy_pj"}))
levels = {h["level"]: h["attrs"] for h in arch["hierarchy"]}
macs = 1
for n in levels["pe_array"]["dims"].values():
    macs *= int(n)
print(f"latency_cycles={result.metrics['latency_cycles'].value:g}")
print(f"energy_pj={result.metrics['energy_pj'].value:g}")
print(f"area_mm2={macs * MAC_MM2 + levels['gbuf']['size_kb'] * SRAM_MM2_PER_KB:.4f}")
