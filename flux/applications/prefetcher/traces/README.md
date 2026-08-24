# traces/ — the workloads this study optimises for

Three ChampSim instruction traces from 5G baseband workloads, ~380 MB in total:

| trace | size | no-prefetcher IPC |
|---|---|---|
| `fdd_su_v1_0.simout_champsim.gz` | 129 MB | 0.69602 |
| `tdd_dl_mu_v1_0.simout_champsim.gz` | 166 MB | 0.99071 |
| `tdd_ul_mu_v1_0.simout_champsim.gz` | 85 MB | 0.80232 |

**They are not in git.** 380 MB of binary blobs do not belong in a source repository, and the
repository-root `.gitignore` excludes them. The stages measure every `*.gz`/`*.xz` in this
directory (`flux_evaluator_champsim.traces.resolve`) and stop with an error when there is none.

## Where they come from

They were produced by the ChampSim tracer from simulated 5G baseband runs (`*.simout`), and they
arrived with the project rather than being generated here. If you have the originals, the tracer
lives in the Pythia tree (`tracer/`). Otherwise copy the three files above into this directory,
or point the stages' `--traces` at another directory in a copy of the document. Flux does not
download them for you.

The IPC column is a reference, not the definition: every run measures its own no-prefetcher
baseline on the binary in use (`flux_evaluator_champsim.baseline`, cached per binary and trace).
