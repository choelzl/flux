"""The web interface, end to end in a real browser (D710): a fresh `flux serve` on its own data,
headless Firefox driven over Marionette, the flows a user walks -- each step checked, every page
watched for errors (a script error, an unhandled failure, a red notice).

    python3 tests/e2e/web_ui.py            # from flux/, in the dev shell; prints a report, exits 1 on a failure
    FLUX_E2E_STEPS="login,upload" python3 tests/e2e/web_ui.py      # only those steps

The harness (the server, `Browser`, `Run`, the helpers) is harness.py; the steps are steps/<area>.py, one module per
area of tests/areas.py (D1171), run here in `steps.ORDER`. `Browser`, `Run`, `free_port`, `loop` and the rest of
the harness stay importable from here.

Firefox from a Snap reads and writes only under ~/snap/firefox/common: the profile and the files
to upload live there (FLUX_E2E_HOME to choose another place). The server runs `--no-sandbox` unless
FLUX_E2E_SANDBOX=1: the flows test the pages, not the sandbox. Screenshots of failures go to
<home>/shots/."""

from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # harness and steps, however this module is loaded

from harness import (  # noqa: E402,F401 -- the harness, importable from here as before (scripts use web_ui.Browser, ...)
    CHARTS, DOCUMENT, EDITOR, GRAPHS_RESULTS, HOME, PASSWORDS, SORT_FIT, WATCH, Browser, Run, _design, edit, file_api,
    free_port, loop, make_sort_fit, put, seed_history, select_file,
)
from steps import ORDER  # noqa: E402

#: the step modules, steps/<area>.py
AREAS = sorted(p.stem for p in (Path(__file__).resolve().parent / "steps").glob("*.py") if p.stem != "__init__")


def flows(r: Run) -> None:
    # a loop to upload: a sweep, no model needed
    from tests.loop_fixtures import write_loop

    write_loop("sweep", "sw", r.files)
    r.queue = []                                    # each module's steps, gathered, then run in ORDER
    for area in AREAS:
        importlib.import_module(f"steps.{area}").flows(r)
    queued, r.queue = dict(r.queue), None
    for name in ORDER:
        if name == "screens":                       # the opt-in reviews by eye ran before the screenshots
            _opt_in(r)
        if name in queued:
            r.step(name, queued.pop(name))
    for name, fn in queued.items():                 # a step not in ORDER yet (tests/unit/test_areas.py says so)
        r.step(name, fn)


def _opt_in(r: Run) -> None:
    if os.environ.get("FLUX_E2E_GALLERY"):          # screenshots of Live for recorded journals, for review by eye
        from web_ui_gallery import gallery_flows

        gallery_flows(r)
    if os.environ.get("FLUX_E2E_DEMO"):              # a demo server's loops, as they are
        from web_ui_gallery import demo_review_flows

        demo_review_flows(r)


def main() -> int:
    if not shutil.which("firefox"):
        print("no firefox: the end-to-end test needs one")
        return 2
    run = Run()
    try:
        flows(run)
    finally:
        run.close()
    failed = [x for x in run.results if not x[1]]
    print("\nsteps by time: " + ", ".join(f"{n} {t:.0f}s" for n, t in sorted(run.timings, key=lambda x: -x[1])))
    print(f"\n{len(run.results) - len(failed)} of {len(run.results)} checks passed" + (f"; screenshots of failures in {run.shots}" if failed else ""))
    for name, _ok, detail in failed:
        print(f"  FAIL {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
