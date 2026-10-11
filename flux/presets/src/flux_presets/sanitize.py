"""A C or C++ program built with AddressSanitizer and UndefinedBehaviorSanitizer and run (D972): each
memory error, leak or undefined behaviour it hits is a failure.

    fluxhelper sanitize --build "cc -O1 -o {out} {artifact}" [--run "{out} 1000"] [--runs 1]
    # each error's first line, then "N failing"; exit 0, 1, or 3 (the program did not build)

The sanitizer flags are added to `--build` (`-fsanitize=address,undefined -fno-omit-frame-pointer
-g`); `--run` runs the program (default: the program alone; `{out}` is the program). A run that
exits non-zero with no sanitizer error is one failure. Errors are counted per distinct report.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

FLAGS = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g"]
_ERRORS = re.compile(r"(ERROR: (AddressSanitizer|LeakSanitizer): .*|.*: runtime error: .*)")


def catalog() -> list[dict]:
    """This helper's catalog entry (D972)."""
    from ._entries import N_FAILING, param
    return [{"id": "c-sanitize", "role": "check", "title": "Memory and undefined-behaviour errors",
             "what": "Builds the program with AddressSanitizer and UndefinedBehaviorSanitizer and runs it: each memory error, "
                     "leak or undefined behaviour it hits is a failure.",
             "run": 'fluxhelper sanitize --build "{build}" --run "{run}"',
             "params": {"build": param("Build command ({out}: the program)", "cc -O1 -o {out} {artifact}"),
                        "run": param("Run command (empty: the built program)", "")},
             "needs": ["cc"], "pass": N_FAILING, "languages": ["c", "cpp"], "kinds": ["program"], "short": "sanitize"}]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxhelper sanitize", description=__doc__.split("\n\n")[0])
    ap.add_argument("--build", required=True, help='one build command; {out} is the program it writes')
    ap.add_argument("--run", default="", help="the command run; {out} is the program (empty: the program alone)")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--show", type=int, default=8)
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="flux-sanitize-") as d:
        out = Path(d) / "prog"
        build = shlex.split(a.build.replace("{out}", shlex.quote(str(out))))
        build = [build[0], *FLAGS, *build[1:]]
        r = subprocess.run(build, cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=a.timeout)
        if r.returncode != 0 or not out.exists():
            print("did not build: " + "\n".join((r.stderr or r.stdout).strip().splitlines()[:20]))
            print("1 failing")
            return 3
        run = shlex.split((a.run.strip() or "{out}").replace("{out}", shlex.quote(str(out))))
        env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=0:abort_on_error=0",
               "UBSAN_OPTIONS": "print_stacktrace=0:halt_on_error=0"}
        errors: list[str] = []
        for _ in range(max(1, a.runs)):
            p = subprocess.run(run, cwd=d, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               timeout=a.timeout)
            found = [m.group(0).strip() for m in _ERRORS.finditer(p.stderr)]
            errors += found
            if p.returncode != 0 and not found:
                errors.append(f"the program exited {p.returncode}: {(p.stderr or p.stdout).strip()[-200:]}")
    distinct = list(dict.fromkeys(re.sub(r"0x[0-9a-f]+", "0x...", e) for e in errors))
    print("\n".join([f"FAIL {e}" for e in distinct[:a.show]] + [f"{len(distinct)} failing"]))
    return 1 if distinct else 0


if __name__ == "__main__":
    raise SystemExit(main())
