"""How complex a design's source is (D975): read, not run, in any language lizard reads.

    fluxpreset complexity DESIGN [--ext .c]
    # loc= functions= cyclomatic_max= cyclomatic_mean= tokens= max_function_loc= params_max=
    fluxpreset complexity DESIGN --max-cyclomatic 15
    # each function over the limit a line, then "N failing"

lizard (C, C++, Rust, Go, Java, JavaScript, Python, and more) counts each function's lines of code
(no blanks or comments), its cyclomatic complexity (1 + its branches) and its tokens. DESIGN is a
file or a folder (every file lizard reads in it). A file whose extension lizard does not know is
read as `--ext`. A source with no functions has `functions=0` and the cyclomatic numbers 0.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import sys
import tempfile
from pathlib import Path


def catalog() -> list[dict]:
    """This preset's catalog entries (D975)."""
    from ._entries import N_FAILING, metrics, param
    return [{"id": "complexity", "role": "stage", "title": "Source complexity",
             "what": "Reads the design's source (any common language) and counts its lines of code, functions, cyclomatic "
                     "complexity and tokens; nothing is built or run.",
             "run": "fluxpreset complexity {artifact}",
             "params": {},
             "metrics": metrics("loc", "functions", "cyclomatic_max", "cyclomatic_mean", "tokens", "max_function_loc"),
             "needs": [], "languages": [], "kinds": [], "short": "complexity"},
            {"id": "complexity-limit", "role": "check", "title": "No function too complex",
             "what": "Fails each function whose cyclomatic complexity is over the limit.",
             "run": "fluxpreset complexity {artifact} --max-cyclomatic {max_cyclomatic}",
             "params": {"max_cyclomatic": param("Most branches per function", 15)},
             "needs": [], "pass": N_FAILING, "languages": [], "kinds": [], "short": "complexity"}]


def _functions(path: Path, ext: str) -> tuple[list, int, int] | str:
    """(every function, the lines of code, the tokens), or why nothing could be read."""
    try:
        import lizard
    except ImportError:
        return "lizard is not installed (pip install lizard)"
    files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    funcs, loc, tokens, read = [], 0, 0, 0
    with tempfile.TemporaryDirectory(prefix="flux-complexity-", ignore_cleanup_errors=True) as d:
        for f in files:
            name = str(f)
            if lizard.get_reader_for(name) is None:
                if path.is_dir() or not ext:
                    continue
                name = str(Path(d) / (f.stem + (ext if ext.startswith(".") else "." + ext)))
                shutil.copyfile(f, name)
                if lizard.get_reader_for(name) is None:
                    return f"lizard reads no {ext} files"
            info = lizard.analyze_file(name)
            funcs += info.function_list
            loc, tokens, read = loc + info.nloc, tokens + info.token_count, read + 1
    if not read:
        return f"no source lizard reads in {path} (give --ext for an unusual extension)"
    return funcs, loc, tokens


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset complexity", description=__doc__.split("\n\n")[0])
    ap.add_argument("artifact")
    ap.add_argument("--ext", default="", help="read a file of an unknown extension as this one (.c, .rs, .py, ...)")
    ap.add_argument("--max-cyclomatic", type=int, default=0, help="a check: fail each function over this")
    a = ap.parse_args(argv)
    path = Path(a.artifact)
    if not path.exists():
        print(f"no such design: {path}", file=sys.stderr)
        return 2
    got = _functions(path, a.ext)
    if isinstance(got, str):
        print(got, file=sys.stderr)
        return 2 if got.startswith("lizard is not") else 1
    funcs, loc, tokens = got
    if a.max_cyclomatic:
        over = sorted((f for f in funcs if f.cyclomatic_complexity > a.max_cyclomatic), key=lambda f: -f.cyclomatic_complexity)
        print("\n".join([f"FAIL {f.name} (line {f.start_line}): cyclomatic {f.cyclomatic_complexity}, over {a.max_cyclomatic}"
                         for f in over] + [f"{len(over)} failing"]))
        return 1 if over else 0
    cc = [f.cyclomatic_complexity for f in funcs]
    print(f"loc={loc} functions={len(funcs)} cyclomatic_max={max(cc, default=0)} "
          f"cyclomatic_mean={statistics.fmean(cc) if cc else 0:.3g} tokens={tokens} "
          f"max_function_loc={max((f.nloc for f in funcs), default=0)} "
          f"params_max={max((len(f.parameters) for f in funcs), default=0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
