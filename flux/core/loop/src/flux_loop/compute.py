"""The compute tool (D422): a reply may ask the harness to RUN a small numpy snippet, the way a coding agent runs scratch Python instead of deriving a constant in its head. Sandboxed: an isolated interpreter, numpy and the pure standard library only, time and memory limits, capped output."""

from __future__ import annotations

__all__ = ["preamble_lines", "run_compute"]


#: `struct` joined numpy and math (D468): packing a float to its bits is what an FP16 operator
#: prototype does constantly, the model reached for it, and every refusal was a snippet lost.
#: The pure-compute standard library joined (D545): live, a generator turn spent its tool
#: rounds on `import re` refused, then `exec(...)` refused, and each refusal only said no.
_COMPUTE_ALLOWED_MODULES = {"numpy", "math", "struct", "re", "itertools", "functools", "collections",
                            "fractions", "decimal", "random", "statistics", "operator", "string",
                            "bisect", "heapq", "array", "copy", "enum", "typing", "dataclasses"}
ALLOWED_TEXT = ("numpy as np, math, struct, re, itertools, functools, collections, fractions, decimal, "
                "random, statistics, operator, string, bisect, heapq, array, copy, enum, typing, dataclasses")
_COMPUTE_FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals",
                            "locals", "getattr", "setattr", "delattr", "input",
                            "breakpoint", "vars", "memoryview"}
#: No files, in or out (D480): a compute turn `np.save`d the reference to /tmp and the next
#: prototype `np.load`ed it back -- "512 over", the reference through the filesystem. A
#: snippet's whole world is its own process; nothing persists between turns but the text
#: the loop carries.
_COMPUTE_FORBIDDEN_ATTRS = {"load", "save", "savez", "savez_compressed", "loadtxt", "savetxt",
                            "fromfile", "tofile", "genfromtxt", "memmap", "frombuffer", "dump",
                            "dumps", "loads"}


def _check_snippet(code: str) -> str | None:
    """Static screen before running: only numpy/math imports, no dangerous names,
    no dunder attributes. Returns a refusal reason or None."""
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"syntax error: {exc.msg} (line {exc.lineno})"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""])
            for n in names:
                if n.split(".")[0] not in _COMPUTE_ALLOWED_MODULES:
                    return (f"import of {n!r} is not allowed; the sandbox has {ALLOWED_TEXT} and nothing "
                            "else -- write the computation with those, do not try another import")
        elif isinstance(node, ast.Name) and node.id in _COMPUTE_FORBIDDEN_NAMES:
            return (f"{node.id!r} is not allowed in the sandbox: the snippet runs as written, so put the "
                    "code in the snippet itself -- do not try exec/eval/compile/__import__ again")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return f"dunder attribute {node.attr!r} is not allowed"
        elif isinstance(node, ast.Attribute) and node.attr in _COMPUTE_FORBIDDEN_ATTRS:
            return (f"`.{node.attr}` is not allowed: the sandbox has no files, in or out -- "
                    "nothing persists between turns but the text you send")
    return None


def screen_snippet(code: str) -> str | None:
    """The static screen on its own: None when `code` may run, else why not (D479: a
    framework harness that EMBEDS a model's snippet screens the snippet, then runs
    `trusted`)."""
    return _check_snippet(code)


def _preamble(timeout_s: float) -> str:
    return (
        "import resource, sys\n"
        "try:\n    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)\n"
        "except Exception: pass\n"
        "try:\n    resource.setrlimit(resource.RLIMIT_CPU, (%d, %d))\n"
        "except Exception: pass\n"
        "import math\nimport numpy as np\n" % (int(timeout_s) + 1, int(timeout_s) + 1))


def preamble_lines() -> int:
    """How many lines the sandbox puts before a snippet: a traceback's line N is the
    snippet's line N - preamble_lines() (D482: errors reported in the model's coordinates)."""
    return _preamble(10.0).count("\n")


def run_compute(requests: list[dict], *, timeout_s: float = 10.0,
                max_chars: int = 2000, trusted: bool = False) -> list[tuple[str, str]]:
    """Run each request's code in a fresh isolated interpreter; (name, output)
    per request, where output is stdout+stderr capped, or the refusal. `trusted`
    skips the static screen for code the FRAMEWORK wrote (a family harness re-executing
    a screened prototype per member needs `exec`); the interpreter limits still apply."""
    import subprocess
    import sys
    import tempfile as _tf

    out: list[tuple[str, str]] = []
    preamble = _preamble(timeout_s)
    for i, req in enumerate(requests, 1):
        name = str((req or {}).get("name") or f"compute {i}")[:60]
        code = str((req or {}).get("code") or "")
        why = None if trusted else _check_snippet(code)
        if why:
            out.append((name, f"refused: {why}"))
            continue
        with _tf.TemporaryDirectory(prefix="flux-compute-") as d:
            try:
                r = subprocess.run(
                    [sys.executable, "-I", "-c", preamble + code], cwd=d,
                    capture_output=True, text=True, timeout=timeout_s,
                    env={"PATH": "", "PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1"})
                text = (r.stdout + (("\n[stderr] " + r.stderr) if r.stderr.strip() else ""))
                if r.returncode != 0 and not text.strip():
                    text = f"exited {r.returncode}"
            except subprocess.TimeoutExpired:
                text = f"timed out after {timeout_s:g}s"
            except Exception as exc:  # noqa: BLE001
                text = f"could not run: {exc}"
        text = text.strip() or "(no output)"
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n… (+{len(text) - max_chars} chars)"
        out.append((name, text))
    return out


