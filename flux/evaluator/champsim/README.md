# evaluator/champsim — ChampSim, owned by flux

Runs the ChampSim (Pythia) simulator on a directory of traces and reports IPC, the speedup over
a no-L2-prefetcher baseline, and the L2 prefetch counters. Knows nothing about any one
prefetcher: an artifact is either

- an `.ini` of knobs for the prebuilt binary, whose `l2c_prefetcher_types = a,b` line picks the
  L2 prefetchers (stripped before ChampSim reads the file), or
- a C++ header with one `class X : public Prefetcher`, built into a private copy of the source
  tree and selectable as `x`.

    flux champsim run ARTIFACT --traces DIR --warmup N --sim M [--with a,b] [--jobs K] [--config KNOBS.ini]
    flux champsim build HEADER.h                # `0 failing`, or the first error + `1 failing` (exit 3)
    flux champsim check HEADER.h --traces DIR   # build + a 1M-instruction smoke run

The binary is `$FLUX_CHAMPSIM_BIN`, else `pythia` from the dev shell (nixchip, which also installs
the source tree under `share/pythia`). Builds are cached under `$FLUX_TMPDIR/champsim/`, baselines
in `$XDG_CACHE_HOME/flux/champsim/baseline.json`.
