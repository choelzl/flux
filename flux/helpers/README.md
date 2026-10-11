# helpers/ — prebuilt checks and measurements

`flux_helpers`: the commands a document names for its checks and measurements, so an application
carries no copy of them (D968). Each is `{python} -m flux_helpers.<module> ...` in a document.

| Module | Commands | Prints |
|---|---|---|
| `golden` | `DESIGN --golden golden.py` | failing vectors, `N failing of M`; exit 0, 1, 3 (did not compile) |
| `rtl` | `lint DESIGN`; `measure DESIGN --stage stat\|synth\|place\|route --clock-ps N`; `cycles DESIGN --golden g.py`; `equiv`, `breakdown`, `fmax`, `power` | `N failing`; `fmax_mhz= area_um2= power_w= cell_count= path_ps=` |
| `prog` | `time\|count\|memory\|size --build "c++ -O2 -o {out} {artifact}"`  | `time_ms=`, `instructions=`, `text_bytes=` |
| `cases` | `DESIGN.py --call f --reference ref.py --random 100` | mismatches, `N failing of M` |
| `pytest` | `DESIGN.py --tests test_design.py` | failing tests, `N failing of M` |
| `ccases` | `DESIGN.c --call f --sig "long(long,long)" --reference ref.py:f --random 100` | mismatches, `N failing of M` |
| `sanitize` | `--build "cc -O1 -o {out} {artifact}"` | memory and UB errors, `N failing` |
| `accuracy` | `DESIGN.py --call f --reference ref.py:f --random 1000` | `max_abs_error= max_rel_error= max_ulp=` |
| `bench` | `DESIGN.py --call f --args "(1000,)" --runs 5` | `time_ms= time_min_ms= time_spread_ms=` |
| `champsim` | `build\|check\|run\|stats ARTIFACT --traces DIR ...` | `geomean_speedup=` |

`flux tools` lists them as the catalog's checks and stages; the document reference
(website/docs/guide/parameters.md) has each one's contract.
