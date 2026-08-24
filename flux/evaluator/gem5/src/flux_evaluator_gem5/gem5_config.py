"""The gem5 configuration this adapter simulates, on gem5's own STANDARD LIBRARY (D446).

Shipped with the adapter rather than picked out of the clone. Until D446 this adapter drove
`configs/deprecated/example/se.py` -- a script gem5 moved under `configs/deprecated/` and states
it no longer maintains, so every run was one upstream deletion away from failing, and its
hundreds of CLI flags were a surface no adapter can hold stable. The standard library
(`gem5.components.*`, in every gem5 binary since v21) is the supported way to describe a board,
and it is a handful of objects: a processor, a memory, a cache hierarchy, a board, a simulator.

WHAT IT DESCRIBES, and nothing more: one RISC-V core of a chosen type at a chosen clock, no
caches, one channel of DDR3-1600 -- the same shape `se.py` produced from this adapter's four
flags (`architecture_translator.py`), so the thing being varied is still exactly the CPU
configuration the Architecture IR names. Adding a cache hierarchy here would be a new
architectural claim, and the IR has nowhere to say it yet.

THE STATS THIS NAMES. A standard-library board names its core's cycle counter
`board.processor.cores.core.numCycles`, which is one of the two names CHIA's own
`chia.simulators.gem5.DEFAULT_STATS_KEYS` already looks for (the other is `se.py`'s
`system.cpu.numCycles`), so the adapter reads it without teaching CHIA anything new. With more
than one core the name gains an index per core and no single counter stands for the board, which
is why the translator still refuses `cores != 1` -- the same real finding as before, now for the
supported config.

Run by gem5 itself, never imported by Flux: `gem5.opt --outdir=DIR <this file> --cpu-type timing
--num-cpus 1 --cpu-clock 1.2GHz --cmd /path/to/binary`.
"""

import argparse

from gem5.components.boards.simple_board import SimpleBoard
from gem5.components.cachehierarchies.classic.no_cache import NoCache
from gem5.components.memory import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.isas import ISA
from gem5.resources.resource import BinaryResource
from gem5.simulate.simulator import Simulator
from gem5.utils.requires import requires

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--cpu-type", default="timing",
                    help="a gem5 standard-library CPUTypes value: atomic, timing, o3, minor")
parser.add_argument("--num-cpus", type=int, default=1)
parser.add_argument("--cpu-clock", default="1.2GHz")
parser.add_argument("--mem-size", default="512MiB")
parser.add_argument("--cmd", required=True, help="the compiled binary to run in SE mode")
args = parser.parse_args()

# This check ensures the gem5 binary contains the RISC-V ISA target; if not, it raises rather
# than simulating something else (the adapter only ever builds `build/RISCV/gem5.opt`).
requires(isa_required=ISA.RISCV)

board = SimpleBoard(
    clk_freq=args.cpu_clock,
    processor=SimpleProcessor(cpu_type=CPUTypes(args.cpu_type), isa=ISA.RISCV,
                              num_cores=args.num_cpus),
    memory=SingleChannelDDR3_1600(size=args.mem_size),
    cache_hierarchy=NoCache(),
)
# A LOCAL path, never `obtain_resource`: the benchmark is the clone's own bundled binary and this
# adapter must not reach the network to run (the examples under `configs/example/gem5_library/`
# all download theirs from gem5's resource bucket).
board.set_se_binary_workload(BinaryResource(local_path=args.cmd))

Simulator(board=board).run()
