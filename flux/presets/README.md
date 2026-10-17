# presets/ — prebuilt checks and measurements

`flux_presets`: the commands a document names for its checks and measurements, so an application
carries no copy of them (D968). Each is `{python} -m flux_presets.<module> ...` in a document.

| Module | Commands | Prints |
|---|---|---|
| `golden` | `DESIGN --golden golden.py` | failing vectors, `N failing of M`; exit 0, 1, 3 (did not compile) |
| `rtl` | `lint DESIGN`; `measure DESIGN --stage stat\|synth\|place\|route --clock-ps N`; `cycles DESIGN --golden g.py`; `equiv`, `breakdown`, `fmax`, `power`, `energy`, `path` | `N failing`; `fmax_mhz= area_um2= power_w= cell_count= path_ps=` |
| `prog` | `time\|count\|memory\|size --build "c++ -O2 -o {out} {artifact}"`  | `time_ms=`, `instructions=`, `text_bytes=` |
| `cases` | `DESIGN.py --call f --reference ref.py --random 100` | mismatches, `N failing of M` |
| `pytest` | `DESIGN.py --tests test_design.py` | failing tests, `N failing of M` |
| `ccases` | `DESIGN.c --call f --sig "long(long,long)" --reference ref.py:f --random 100` | mismatches, `N failing of M` |
| `sanitize` | `--build "cc -O1 -o {out} {artifact}"` | memory and UB errors, `N failing` |
| `accuracy` | `DESIGN.py --call f --reference ref.py:f --random 1000` | `max_abs_error= max_rel_error= max_ulp=` |
| `systemc` | `DESIGN.h --top m --golden g.py [--then "golden --golden g.py"]` | `N failing`, or the next preset's output |
| `timebound` | `DESIGN.py --call f --args "(1000,)" --max-ms 50` | over-budget calls, `N failing` |
| `regress` | `--baseline base.json --tol 5 -- CMD` | worse metrics, `N failing` |
| `extract` | `--pick results.latency=latency_ms -- CMD` | `name=value` |
| `complexity` | `DESIGN [--max-cyclomatic 15]` (any language) | `loc= functions= cyclomatic_max= tokens=` |
| `scaling` | `--start 1000 -- CMD {n}` | `scaling_exponent= scaling_r2= time_at_max_ms=` |
| `repeat` | `--n 5 -- CMD` | each metric's median, `NAME_spread_pct=` |
| `derive` | `--expr "adp=area_um2*path_ps" -- CMD` | the command's numbers and the formulas' |
| `bench` | `DESIGN.py --call f --args "(1000,)" --runs 5` | `time_ms= time_min_ms= time_spread_ms=` |
| `champsim` | `build\|check\|run\|stats ARTIFACT --traces DIR ...` | `geomean_speedup=` |

`flux tools` lists them as the catalog's checks and stages; the site's
[Presets](https://choelzl.github.io/flux/reference/presets/) page has each one's contract.
