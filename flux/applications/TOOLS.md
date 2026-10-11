# Application tools

Flux runs ordinary commands from each loop's folder. The checks and measurements Flux ships are
its helpers (`flux/helpers/`, D968), run as `fluxhelper NAME ...`; an application
carries no copy of them:

- `fluxhelper golden`: an RTL design against `golden.py` (Verilator).
- `fluxhelper rtl`: `lint` (Verilator's hardware defects) and `measure` (OpenROAD-flow-scripts
  on ASAP7: `stat`, `synth`, `place`, `route`).
- `fluxhelper prog`, `fluxhelper bench`: a compiled program's time, counts and size; a Python
  function's time.
- `fluxhelper champsim`: ChampSim `build`, `check`, `run` (the prefetcher; `bingo.py` is its own).

External tools such as Verilator, Yosys, OpenROAD (with ORFS for ASAP7) and Pythia must be
available in the loop's environment. Set `language`, stage `metrics` and stage `needs` explicitly
in the problem document. Loops made before D968 carry their own `rtl.py` or `champsim.py` and keep
working.
