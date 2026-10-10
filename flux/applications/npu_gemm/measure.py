"""Measure one architecture: ZigZag's cycles and energy of the workload on it (evaluate.py), and
a first-order area estimate at 28 nm. Prints `name=value` lines. `python measure.py ARCH WORKLOAD`."""

import sys
from pathlib import Path

import yaml

from evaluate import evaluate

MAC_MM2 = 0.004             # one int8 multiply-accumulate with its registers
SRAM_MM2_PER_KB = 0.005     # the global buffer's SRAM

arch = yaml.safe_load(Path(sys.argv[1]).read_text())
got = evaluate(arch, yaml.safe_load(Path(sys.argv[2]).read_text()))
levels = {h["level"]: h["attrs"] for h in arch["hierarchy"]}
macs = 1
for n in levels["pe_array"]["dims"].values():
    macs *= int(n)
print(f"latency_cycles={got['latency_cycles']:g}")
print(f"energy_pj={got['energy_pj']:g}")
print(f"area_mm2={macs * MAC_MM2 + levels['gbuf']['size_kb'] * SRAM_MM2_PER_KB:.4f}")
