# Application-owned tools

Flux runs ordinary commands from each loop's folder. The application carries the code for
those commands; the old global RTL, ChampSim, ZigZag and Timeloop packages are removed.

- RTL apps include one file, `rtl.py`: `test` (Verilator against `golden.py`), `lint` and
  `measure` (Yosys + OpenROAD on ASAP7, read from OpenROAD-flow-scripts' platform folder; no PDK
  is bundled).
- The prefetcher includes `champsim.py`, `bingo.py` and `tools/champsim_tools/`.
- The NPU app includes `measure.py`, `evaluate.py` and `tools/zigzag_tools/` plus
  `tools/timeloop_tools/`. `evaluate.py ARCH WORKLOAD --backend zigzag|timeloop` prints
  measurements for a command stage.

These scripts still use Flux's shared values and installed runtime libraries. External tools
such as Verilator, Yosys, OpenROAD (with ORFS for ASAP7), Pythia and Timeloop must be available in the loop's environment.
Set `language`, stage `metrics` and stage `needs` explicitly in the problem document.

To maintain the RTL copies, edit `mul8/rtl.py`, then run `python scripts/sync-app-tools.py`
from `flux/`. `--check` verifies every application, library and test copy matches it. Copies are regular files so an uploaded or copied application is self-contained.
