"""Metrics made from other metrics (D975): a command's numbers, and formulas over them.

    fluxpreset derive --expr "adp=area_um2*path_ps" --expr "ppw=1/(time_ms*power_w)" -- CMD ...
    # the command's own name=value, then each formula's

A formula is NAME=EXPRESSION over the command's metrics (and formulas before it): numbers, + - * /
** %, parentheses, and min, max, abs, sqrt, log, log2, log10, exp. Nothing else is evaluated. A
formula naming a metric the command did not print, or dividing by zero, exits 1. The command's exit
code and stderr are passed on when it fails.
"""

from __future__ import annotations

import argparse
import ast
import math
import operator
import re
import sys

from ._run import command, numbers, run

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"min": min, "max": max, "abs": abs, "sqrt": math.sqrt, "log": math.log, "log2": math.log2,
          "log10": math.log10, "exp": math.exp}


def catalog() -> list[dict]:
    """This preset's catalog entry (D975)."""
    from ._entries import param
    return [{"id": "derive", "role": "stage", "title": "Metrics from formulas",
             "what": "Runs any measurement and adds metrics computed from its numbers, such as area times delay or "
                     "throughput per watt.",
             "run": "fluxpreset derive --expr {expr} -- {command}",
             "params": {"expr": param("Formula (name=expression)", "adp=area_um2*path_ps"),
                        "command": param("Measurement command", "")},
             "metrics": {}, "needs": [], "languages": [], "kinds": ["own"], "short": "derive"}]


def evaluate(expr: str, names: dict[str, float]) -> float:
    """The value of an arithmetic formula over `names`; ValueError for anything else."""
    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id not in names:
                raise ValueError(f"{n.id} was not printed")
            return names[n.id]
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            return float(_FUNCS[n.func.id](*map(ev, n.args)))
        raise ValueError(f"{ast.unparse(n)} is not arithmetic")
    try:
        return ev(ast.parse(expr, mode="eval"))
    except SyntaxError as exc:
        raise ValueError(f"{expr!r} does not parse") from exc
    except (ZeroDivisionError, OverflowError) as exc:
        raise ValueError(f"{expr}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset derive", description=__doc__.split("\n\n")[0])
    ap.add_argument("--expr", action="append", default=[], required=True, help="NAME=EXPRESSION, repeatable")
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument("rest", nargs=argparse.REMAINDER, help="-- the measurement command")
    a = ap.parse_args(argv)
    if not a.rest or a.rest == ["--"]:
        ap.error("give the measurement command after --")
    formulas = []
    for e in a.expr:
        name, eq, body = e.partition("=")
        if not eq or not re.fullmatch(r"[A-Za-z_]\w*", name.strip()) or not body.strip():
            ap.error(f"--expr is NAME=EXPRESSION, not {e!r}")
        formulas.append((name.strip(), body.strip()))
    r = run(command(a.rest), a.timeout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        print(f"the command exited {r.returncode}", file=sys.stderr)
        return r.returncode if r.returncode in (1, 3) else 1
    got = numbers(r.stdout)
    derived = []
    for name, body in formulas:
        try:
            got[name] = evaluate(body, got)
        except ValueError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            return 1
        derived.append(f"{name}={got[name]:g}")
    print(r.stdout.rstrip())
    print(" ".join(derived))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
