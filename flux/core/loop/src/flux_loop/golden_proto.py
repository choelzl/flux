"""A prototype stage from a golden model (D604): any document whose gate is
`flux rtl test ... --golden <file>` gets a prototype-first path with no world to write. The
model first writes the algorithm as plain Python -- `design(**inputs) -> {output: value}` over
integers and bits, as hardware would compute it -- checked in seconds against the golden
model's vectors. Only a prototype that passes every vector goes on; the RTL turn is handed it
to transcribe (the loop's VERIFIED PROTOTYPE block), so the model does not invent the numerics
and the SystemVerilog at the same time.

The golden model may be the document's, or written by a model when the document names one that
does not exist (`author.write_golden`).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .capabilities import Prototype
from .types import Verdict

__all__ = ["PYTHON_RULES", "capability", "check", "golden_path", "load"]

#: The language's rules for a `python` prototype, in place of the numpy-integer DSL's.
PYTHON_RULES = (
    "RULES so it transcribes to hardware line for line: plain Python on integers -- shifts, "
    "masks, integer multiply/add/subtract, comparisons, `if`/`else`, `for` over a constant "
    "range. COMPUTE THE FUNCTION WITH A FORMULA, NOT A LOOKUP OF ITS ANSWERS: decode the input "
    "into fields (sign, exponent, mantissa), split its range where the function behaves "
    "differently (where it saturates, where it is the identity or zero, special values) and "
    "decide those regions with comparisons; in the rest, evaluate a low-degree polynomial in "
    "fixed point (Horner's rule), one per segment. The only tables are SMALL ones built at "
    "MODULE level for such constants -- the coefficients per segment -- at most 64 entries "
    "each unless the problem says otherwise; you may compute their constants with float math "
    "there, ONCE. A FLOATING-POINT input spans too wide a range for one fixed-point format "
    "(a binary point that suits 1.0 flushes the small values to zero): keep the exponent, "
    "work on the mantissa, and treat each exponent region by what the function is there "
    "(its leading Taylor terms where the input is tiny). No float arithmetic on the inputs "
    "inside design(): decide each fixed-point format (bit widths, binary point) explicitly, and round where the target rounds (to "
    "nearest even, unless the golden model says otherwise). Every value you return is the "
    "output port's bit pattern as an integer. The check runs design() on every vector of the "
    "golden model and reports the failing ones with the expected and returned values. After "
    "it passes, the target is written from this prototype, the same widths, tables and "
    "rounding.")


def golden_check(task: Any) -> list[str]:
    """The command of the gate's check that names `--golden` (D652), or []."""
    return next(([str(t) for t in c.run] for c in (task.gate or ()) if "--golden" in c.run), [])


def golden_path(task: Any) -> Path | None:
    """The golden model a document's gate names (`--golden <file>`, `{home}` resolved); none
    for a parent of sub-loops, whose gate is its children's (D802)."""
    if getattr(task, "subtasks", None) or getattr(task, "split", False):
        return None
    cmd = golden_check(task)
    if "--golden" not in cmd or cmd.index("--golden") + 1 >= len(cmd):
        return None
    raw = cmd[cmd.index("--golden") + 1].replace("{home}", str(task.home or "."))
    return Path(raw)


def load(path: Path) -> Any:
    """The golden model as the harness's `Golden`."""
    import importlib.util

    from flux_codegen_rtl_harness import Golden

    spec = importlib.util.spec_from_file_location(f"golden_{abs(hash(str(path)))}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return Golden.from_module(mod)


_RUNNER = r'''
import json, sys, traceback
code = open(sys.argv[1]).read()
rows = json.load(open(sys.argv[2]))
ns = {"__name__": "prototype"}
try:
    exec(compile(code, "prototype.py", "exec"), ns)
    design = ns["design"]
except Exception:
    print(json.dumps({"error": "the prototype did not load:\n" + traceback.format_exc(limit=3)[-1500:]}))
    sys.exit(0)
cap = int(sys.argv[3])
for name, v in ns.items():
    if name.startswith("__") or callable(v) or isinstance(v, (str, int, float)):
        continue
    try:
        n = len(v)
    except Exception:
        continue
    if n > cap:
        print(json.dumps({"error": f"the module-level table `{name}` has {n} entries, over the {cap} a prototype may use: "
                          "a table of the answers is a lookup, not a formula, and costs more hardware than it "
                          "saves. Decode the input (sign, exponent, mantissa), split the range where the function "
                          "saturates or is the identity, and evaluate a low-degree fixed-point polynomial per "
                          f"segment; a table holds only its coefficients (at most {cap} entries)."}))
        sys.exit(0)
out = []
for r in rows:
    try:
        got = design(**r)
        out.append({k: int(v) for k, v in dict(got).items()} if isinstance(got, dict) else {"__not_a_dict__": repr(got)[:80]})
    except Exception as exc:
        out.append({"__error__": f"{type(exc).__name__}: {exc}"[:200]})
print(json.dumps({"outputs": out}))
'''


#: Cap on any module-level table (`budget.prototype_table_max`), so a prototype computes with a
#: formula instead of tabulating the answers (D616). 64 holds a polynomial's coefficients per
#: segment, not the answers.
TABLE_MAX = 64


#: What a function of the prototype may not call: the float side of Python and numpy.
_FLOAT_MODULES = {"math", "cmath", "decimal", "fractions", "statistics"}
_NUMPY_FLOAT = {"float16", "float32", "float64", "float128", "half", "single", "double", "longdouble",
                "exp", "exp2", "expm1", "log", "log2", "log10", "log1p", "sqrt", "cbrt", "tanh", "sinh", "cosh",
                "sin", "cos", "tan", "arctan", "arctanh", "erf", "power", "float_power", "divide", "true_divide",
                "round", "around", "rint", "floor", "ceil", "trunc", "frexp", "ldexp", "nextafter", "spacing"}


def float_work(code: str) -> list[str]:
    """Where design() -- or a function it calls -- computes with floats (D605): `float(...)`, the `math`
    module, numpy's float types and functions, `.view(...)` (reading bits as a float), true
    division `/`, a float constant. Module level is free -- tables are built there, once, as a
    ROM is generated. Without this check the golden model itself would pass as a prototype and
    could not be transcribed."""
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []                        # the run reports the parse error with its line
    # only what design() runs: design and the functions it calls, transitively -- a helper that
    # builds a table once at module level may use floats
    defs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    reach, todo = set(), ["design"]
    while todo:
        name = todo.pop()
        if name in reach or name not in defs:
            continue
        reach.add(name)
        todo += [c.func.id for c in ast.walk(defs[name]) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
    out: list[str] = []
    for fn in (defs[n] for n in sorted(reach)):
        for n in ast.walk(fn):
            what = None
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "float":
                what = "float(...)"
            elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                if n.value.id in _FLOAT_MODULES:
                    what = f"{n.value.id}.{n.attr}"
                elif n.value.id in ("np", "numpy") and n.attr in _NUMPY_FLOAT:
                    what = f"{n.value.id}.{n.attr}"
            elif isinstance(n, ast.Attribute) and n.attr == "view":
                what = ".view(...) (reading the bits as a float)"
            elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
                what = "true division `/` (use `//` or a shift)"
            elif isinstance(n, ast.Constant) and isinstance(n.value, float):
                what = f"the float constant {n.value!r}"
            if what:
                line = f"line {getattr(n, 'lineno', '?')}: {what}"
                if line not in out:
                    out.append(line)
    return out


def check(code: str, g: Any, rows: list[dict[str, Any]], timeout_s: float = 60.0,
          table_max: int = TABLE_MAX) -> Verdict:
    """The prototype on every vector of the golden model, in its own process (a loop that
    never ends is a timeout, not a hung campaign). Score: the failing vectors."""
    if "def design(" not in code:
        return Verdict(False, float(len(rows)), "the prototype defines no `design(...)` function")
    floats = float_work(code)
    if floats:
        return Verdict(False, float(len(rows)),
                       "the prototype computes with floats inside its functions, which hardware does not: "
                       + "; ".join(floats[:8]) + (f"; ... {len(floats) - 8} more" if len(floats) > 8 else "")
                       + ". Decode the bit patterns into integer fields (sign, exponent, mantissa), compute in "
                       "fixed point with shifts and integer multiplies, and put any float math into module-level "
                       "tables (at most the table limit each).")
    with tempfile.TemporaryDirectory(prefix="flux-proto-") as d:
        src, data = Path(d) / "prototype.py", Path(d) / "rows.json"
        src.write_text(code)
        data.write_text(json.dumps([r["inputs"] for r in rows]))
        try:
            run = subprocess.run([sys.executable, "-c", _RUNNER, str(src), str(data), str(int(table_max))], capture_output=True,
                                 text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return Verdict(False, float(len(rows)), f"the prototype ran longer than {timeout_s:g}s on {len(rows)} vectors")
    try:
        doc = json.loads(run.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Verdict(False, float(len(rows)), "the prototype crashed the check:\n" + (run.stderr or run.stdout)[-1500:])
    if "error" in doc:
        return Verdict(False, float(len(rows)), doc["error"])
    return compare(g, rows, doc["outputs"], _unspelled(code, g))


def compare(g: Any, rows: list[dict[str, Any]], outputs: list[dict[str, Any]], unspelled: str = "") -> Verdict:
    """A prototype's outputs, one dict per vector, against the golden model's, in any language.
    Score: the failing vectors, reported by input region with examples."""
    from flux_codegen_rtl_harness.golden import ulp_distance

    widths = {p["name"]: int(p["bits"]) for p in g.ports if p["dir"] == "out"}
    bad: list[str] = []
    wrong_at: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    bad_at: list[int] = []
    for r, got in zip(rows, outputs):
        wrong = []
        for name, want in r["expected"].items():
            if name not in got:
                wrong.append(f"{name} missing" + (f" ({got.get('__error__') or got.get('__not_a_dict__')})"
                                                  if "__error__" in got or "__not_a_dict__" in got else ""))
                continue
            n = g.ulp.get(name)
            same = (ulp_distance(got[name], int(want), widths[name]) <= int(n)) if n is not None \
                else (got[name] - int(want)) % (1 << widths[name]) == 0
            if not same:
                wrong.append(f"{name}={got[name]:#x} expected {int(want) % (1 << widths[name]):#x}")
        if wrong:
            wrong_at.append((r["inputs"], got, r["expected"]))
            bad_at.append(len(wrong_at) - 1)
            bad.append(f"design({', '.join(f'{k}={v:#x}' if isinstance(v, int) else f'{k}={v!r}' for k, v in r['inputs'].items())}): "
                       + "; ".join(wrong))
    if not bad:
        if unspelled:
            # every input passes, but the loop cannot spell it -- the stage must not end here (D618)
            return Verdict(False, 1.0, f"passes all {len(rows)} vectors, but the loop cannot spell it as hardware: "
                           f"{unspelled}. Rewrite that construct; nothing else needs to change.")
        return Verdict(True, 0.0, f"passes all {len(rows)} vectors")
    where = where_wrong(g, rows, wrong_at)
    picked = _spread(g, wrong_at, 12) if where else list(range(min(12, len(bad))))
    shown = "\n  ".join(bad[i] for i in picked)
    # the table first and the examples from the largest failing ranges, not the first few in
    # input order, which all sit in one corner (D617)
    return Verdict(False, float(len(bad)), f"{len(bad)} of {len(rows)} vectors wrong." + where
                   + (f"\nALSO: the loop cannot spell it as hardware yet: {unspelled}." if unspelled else "")
                   + f"\nExamples{' across those ranges, the largest first' if where else ''}:\n  {shown}"
                   + (f"\n  ... {len(bad) - len(picked)} more" if len(bad) > len(picked) else ""))


def _spread(g: Any, wrong_at: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]], n: int) -> list[int]:
    """Up to `n` failures, round-robin over the leading-bits groups, the groups with the most
    failures first; within a group, from its middle outwards."""
    ins = [p for p in g.ports if p["dir"] == "in"]
    name, bits = ins[0]["name"], int(ins[0]["bits"])
    groups: dict[int, list[int]] = {}
    for i, w in enumerate(wrong_at):
        groups.setdefault(int(w[0][name]) >> max(bits - 6, 0), []).append(i)
    order = sorted(groups.values(), key=len, reverse=True)
    queues = [sorted(q, key=lambda i, q=q: abs(q.index(i) - len(q) // 2)) for q in order]
    out: list[int] = []
    while len(out) < n and any(queues):
        for q in queues:
            if q and len(out) < n:
                out.append(q.pop(0))
    return sorted(out)


def _unspelled(code: str, g: Any) -> str:
    """Why the loop could not spell this prototype (`py2sv`), or "" -- only where it spells
    (inputs of at most EXHAUSTIVE_BITS; above that the target is transcribed)."""
    ins = [p for p in g.ports if p["dir"] == "in"]
    if sum(int(p["bits"]) for p in ins) > EXHAUSTIVE_BITS:
        return ""
    from .py2sv import Unsupported, _compile

    try:
        _compile(code, list(g.ports))
    except Unsupported as exc:
        return str(exc)
    except Exception:  # noqa: BLE001 -- the check already reported what the code itself does wrong
        return ""
    return ""


def _as_float(v: int, bits: int) -> str:
    import numpy as np

    kind = {16: (np.uint16, np.float16), 32: (np.uint32, np.float32), 64: (np.uint64, np.float64)}.get(bits)
    return f"{float(kind[0](v % (1 << bits)).view(kind[1])):.6g}" if kind else f"{v:#x}"


def where_wrong(g: Any, rows: list[dict[str, Any]], wrong_at: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]) -> str:
    """Where the failures are (D617): grouped by the first input's six leading bits -- sign and
    exponent for a 16-bit float -- runs of groups alike merged, one example each, decoded as
    floats when the golden compares in ULPs. The first failing vectors in order all sit in one
    corner and hide failures elsewhere."""
    ins = [p for p in g.ports if p["dir"] == "in"]
    if not ins or not wrong_at:
        return ""
    name, bits = ins[0]["name"], int(ins[0]["bits"])
    shift = max(bits - 6, 0)
    fl = bool(getattr(g, "ulp", None))
    total: dict[int, int] = {}
    for r in rows:
        total[int(r["inputs"][name]) >> shift] = total.get(int(r["inputs"][name]) >> shift, 0) + 1
    fails: dict[int, list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]] = {}
    for w in wrong_at:
        fails.setdefault(int(w[0][name]) >> shift, []).append(w)
    if len(total) < 2:
        return ""

    def show(v: int, b: int) -> str:
        return f"{v:#x} ({_as_float(v, b)})" if fl else f"{v:#x}"

    lines, keys, i = [], sorted(total), 0
    while i < len(keys):
        k = keys[i]
        state = "none" if k not in fails else "all" if len(fails[k]) == total[k] else "some"
        j = i
        while j + 1 < len(keys) and keys[j + 1] == keys[j] + 1 and (
                "none" if keys[j + 1] not in fails else "all" if len(fails[keys[j + 1]]) == total[keys[j + 1]] else "some") == state:
            j += 1
        if state != "none":
            n = sum(len(fails[keys[t]]) for t in range(i, j + 1))
            of = sum(total[keys[t]] for t in range(i, j + 1))
            inp, got, want = fails[keys[(i + j) // 2]][0]
            out = next(iter(want))
            ow = next((int(p["bits"]) for p in g.ports if p["name"] == out), 16)
            got_s = show(int(got[out]), ow) if out in got else "missing"
            lines.append(f"{name}[{bits - 1}:{shift}] = {keys[i]:#x}..{keys[j]:#x}: {'ALL' if state == 'all' else 'some'} "
                         f"wrong ({n}/{of}), e.g. {name}={show(int(inp[name]), bits)} -> {out}={got_s}, "
                         f"expected {show(int(want[out]) % (1 << ow), ow)}")
        i = j + 1
    if not lines:
        return ""
    head = (f"\nWHERE they fail, by {name}'s six leading bits"
            + (" (for a 16-bit float: the sign, then the exponent)" if bits == 16 and fl else "") + ":\n  ")
    return head + "\n  ".join(lines[:24]) + (f"\n  ... {len(lines) - 24} more ranges" if len(lines) > 24 else "")


#: Inputs of at most this many bits in all are checked exhaustively at the prototype stage
#: (D606): sampled vectors can miss rare wrong inputs, and a Python prototype runs every 16-bit
#: input in about a second. The RTL gate keeps the sampled vectors.
EXHAUSTIVE_BITS = 20


def exhaustive(g: Any) -> list[dict[str, Any]] | None:
    """Every input combination with the golden's answer, when the inputs are small enough;
    None otherwise (or when the golden fails on one: its own sampled vectors then)."""
    ins = [p for p in g.ports if p["dir"] == "in"]
    total = sum(int(p["bits"]) for p in ins)
    if not ins or total > EXHAUSTIVE_BITS or g.vectors:
        return None
    import itertools

    def values(p: dict[str, Any]) -> range:
        n = int(p["bits"])
        return range(0, 1 << n) if p.get("unsigned") else range(-(1 << (n - 1)), 1 << (n - 1))

    rows: list[dict[str, Any]] = []
    try:
        for combo in itertools.product(*(values(p) for p in ins)):
            inputs = {p["name"]: v for p, v in zip(ins, combo)}
            rows.append({"inputs": inputs, "expected": {k: int(v) for k, v in g.fn(**inputs).items()}})
    except Exception:  # noqa: BLE001 -- a golden that cannot answer everywhere keeps its own vectors
        return None
    return rows


def capability(task: Any) -> Prototype | None:
    """The prototype stage for a document whose gate names a golden model that exists."""
    path = golden_path(task)
    if path is None or not path.is_file():
        return None
    try:
        g = load(path)
    except Exception:  # noqa: BLE001 -- a golden that cannot load is refused by the gate itself
        return None
    if g.clocked:
        return None                       # a clocked design's cycles are the RTL's, not an algorithm's
    from flux_codegen_rtl_harness import golden_vectors

    rows = exhaustive(g) or golden_vectors(g)
    ins = [p["name"] for p in g.ports if p["dir"] == "in"]
    outs = [p for p in g.ports if p["dir"] == "out"]
    ports = ", ".join(f"{p['name']} ({p['bits']} bits{', unsigned' if p.get('unsigned') else ''}, {p['dir']})" for p in g.ports)
    source = path.read_text()
    if task.budget.get("prototype") == "systemc":
        return _systemc(task, g, rows, ins, outs, ports, source)
    return Prototype(
        check=lambda code, part, state: judge(code, part, state, g, rows, task,
                                              int(task.budget.get("prototype_table_max") or TABLE_MAX)),
        reference=lambda part: ("{" + ", ".join(f"'{p['name']}': <{p['bits']}-bit pattern>" for p in outs) + "}"),
        domain=f"the inputs as keyword arguments ({', '.join(ins)}; each an integer bit pattern)",
        gate=f"equal to the golden model on all {len(rows)} of its vectors" + (" (within its TOLERANCE_ULP)" if g.ulp else ""),
        # the stage formats the contract with `{part}`: every brace of the source is doubled
        contract=(f"THE GOLDEN MODEL the design must match (ports: {ports}) -- the specification, not an "
                  f"implementation to copy: it may use floats; your design() may not.\n```python\n{source.strip()}\n```"
                  ).replace("{", "{{").replace("}", "}}"),
        language="python", family=False, domain_size=len(rows), score_unit=" failing vectors",
        extra={"signature": f"design({', '.join(ins)})", "rules": PYTHON_RULES})


def _systemc(task: Any, g: Any, rows: list[dict[str, Any]], ins: list[str], outs: list[dict[str, Any]],
             ports: str, source: str) -> Prototype | None:
    """The prototype stage in SystemC (D635): the same golden model and vectors, an SC_MODULE."""
    from . import systemc_proto
    from .py2sv import module_name

    if any(int(p["bits"]) > 64 for p in g.ports):
        return None                       # the testbench drives ports of up to 64 bits
    module = module_name(task.statement, task.contract, task.id.split(".")[-1])
    return Prototype(
        check=lambda code, part, state: systemc_proto.check(code, g, rows, module),
        reference=lambda part: "its output ports (" + ", ".join(f"{p['name']}, {p['bits']} bits" for p in outs) + ")",
        domain=f"its input ports ({', '.join(ins)}), each an integer bit pattern",
        gate=f"equal to the golden model on all {len(rows)} of its vectors" + (" (within its TOLERANCE_ULP)" if g.ulp else ""),
        contract=(f"THE GOLDEN MODEL the design must match (ports: {ports}) -- the specification, not an "
                  f"implementation to copy.\n```python\n{source.strip()}\n```").replace("{", "{{").replace("}", "}}"),
        language="systemc", family=False, domain_size=len(rows), score_unit=" failing vectors",
        extra={"signature": f"SC_MODULE({module})", "marker": f"SC_MODULE({module})", "name": "SystemC",
               "as": "a synthesizable SystemC module", "rules": systemc_proto.SYSTEMC_RULES,
               # the verified module spelled as SV by ICSC when it is installed (D636)
               "translate": lambda code: systemc_proto.translate(code, module, g)})


# ---- the prototype's tables, spelled by the loop (D606) ------------------------------------
_TABLES_RUNNER = r'''
import json, sys
ns = {"__name__": "prototype"}
exec(compile(open(sys.argv[1]).read(), "prototype.py", "exec"), ns)
out = {}
for k, v in ns.items():
    if k.startswith("__") or callable(v) or isinstance(v, (str, bytes, dict)):
        continue
    try:
        vals = [int(x) for x in v]
    except Exception:
        try:                                   # a table of rows: one table per column (D618)
            rows = [[int(x) for x in r] for r in v]
        except Exception:
            continue
        if len(rows) >= 2 and rows[0] and all(len(r) == len(rows[0]) for r in rows):
            for j, col in enumerate(zip(*rows)):
                out[f"{k}__c{j}"] = list(col)
        continue
    if len(vals) >= 2:
        out[k] = vals
print(json.dumps(out))
'''

#: The marker the inserted tables carry, so a module is never given them twice.
TABLES_MARK = "// flux: the verified prototype's tables, spelled by the loop (D606)"


def table_functions(code: str, timeout_s: float = 60.0) -> tuple[str, list[str]]:
    """The prototype's module-level integer tables as SystemVerilog lookup functions
    (`<name>_rom(index)`, a `case` over every entry) and one line per table for the prompt.
    The table is data the loop already has, so the loop writes it rather than the model (D478)."""
    with tempfile.TemporaryDirectory(prefix="flux-tables-") as d:
        src = Path(d) / "prototype.py"
        src.write_text(code)
        try:
            run = subprocess.run([sys.executable, "-c", _TABLES_RUNNER, str(src)], capture_output=True,
                                 text=True, timeout=timeout_s)
            tables = json.loads(run.stdout.strip().splitlines()[-1])
        except (subprocess.TimeoutExpired, ValueError, IndexError):
            return "", []
    blocks, lines = [], []
    for name, vals in sorted(tables.items()):
        n = len(vals)
        idx = max(1, (n - 1).bit_length())
        signed = min(vals) < 0
        width = (max((-min(vals) - 1).bit_length() if min(vals) < 0 else 0, max(vals).bit_length()) + 1) if signed \
            else max(1, max(vals).bit_length())
        mask = (1 << width) - 1
        sign = "signed " if signed else ""
        body = "\n".join(f"      {idx}'d{i}: {name}_rom = {width}'h{v & mask:x};" for i, v in enumerate(vals))
        # the index is `flux_<name>_index`: a bare `i` hid a port of the same name (Verilator VARHIDDEN)
        blocks.append(f"  function automatic logic {sign}[{width - 1}:0] {name}_rom(input logic [{idx - 1}:0] flux_{name}_index);\n"
                      f"    case (flux_{name}_index)\n{body}\n      default: {name}_rom = '0;\n    endcase\n  endfunction")
        lines.append(f"`{name}_rom(i)` -- the prototype's `{name}`: {n} entries, index `logic [{idx - 1}:0]`, "
                     f"value `logic {sign}[{width - 1}:0]` ({'two' + chr(39) + 's complement' if signed else 'unsigned'})")
    if not blocks:
        return "", []
    return TABLES_MARK + "\n" + "\n\n".join(blocks) + "\n  // flux: end of the tables", lines


def insert_tables(artifact: str, functions: str) -> str:
    """The tables' functions put into the module, right after its port list (once)."""
    import re

    if not functions or TABLES_MARK in (artifact or ""):
        return artifact
    m = re.search(r"\bmodule\b[^;]*?\)\s*;", artifact or "", re.S)
    if m is None:
        return artifact
    return artifact[:m.end()] + "\n\n" + functions + "\n" + artifact[m.end():]


def fold_tables(artifact: str) -> str:
    """The module as the model is shown it: the inserted tables folded to one line (thousands of
    generated lines are nothing to read, nothing to patch). Edits outside the fold apply to the
    whole text as they are; a rewrite without the tables gets them inserted again."""
    import re

    if TABLES_MARK not in (artifact or ""):
        return artifact
    names = re.findall(r"function automatic logic [^\n]*? (\w+_rom)\(", artifact)
    return re.sub(re.escape(TABLES_MARK) + r".*?// flux: end of the tables",
                  f"// flux: the prototype's tables ({', '.join(names)}) are inserted here by the loop -- "
                  "call them; do not edit, move or rewrite this line", artifact, count=1, flags=re.S)


# ---- the verified prototype, spelled as the target (D611) ------------------------------------
def spell_prototype(code: str, task: Any, timeout_s: float = 900.0) -> tuple[str, str]:
    """(SystemVerilog, "") -- the verified prototype spelled by `flux_loop.py2sv` -- or ("", why).
    Only on the exhaustive domain: the widths are measured over the inputs, and hold for the
    inputs measured. In its own process, with a time limit, like the check."""
    path = golden_path(task)
    if path is None or not path.is_file():
        return "", "no golden model"
    g = load(path)
    if exhaustive(g) is None:
        return "", f"the inputs are over {EXHAUSTIVE_BITS} bits in all: the widths could not be measured on every input"
    from .py2sv import module_name

    module = module_name(task.statement, task.contract, task.id.split(".")[-1])
    with tempfile.TemporaryDirectory(prefix="flux-spell-") as d:
        src, out = Path(d) / "prototype.py", Path(d) / "spelled.sv"
        src.write_text(code)
        try:
            run = subprocess.run([sys.executable, "-c", "from flux_loop.golden_proto import _spell_main; _spell_main()",
                                  str(src), str(path), module, str(out)], capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return "", f"the spelling ran longer than {timeout_s:g}s"
        if run.returncode != 0 or not out.is_file():
            return "", (run.stdout.strip().splitlines() or [run.stderr.strip()[-300:] or "the spelling failed"])[-1]
        return out.read_text(), ""


def _spell_main() -> None:
    """`python -c ... _spell_main() <prototype.py> <golden.py> <module> <out.sv>` (the subprocess)."""
    from .py2sv import Unsupported, spell

    src, golden, module, out = sys.argv[1:5]
    g = load(Path(golden))
    try:
        sv = spell(Path(src).read_text(), list(g.ports), exhaustive(g) or [], module, table_functions)
    except Unsupported as exc:
        print(f"not spelled: {exc}")
        sys.exit(2)
    Path(out).write_text(sv)



# ---- a verified prototype's hardware cost, and the pass that lowers it (D613) ---------------------
def prototype_cost(code: str, task: Any, timeout_s: float = 600.0) -> tuple[float | None, str]:
    """(cost, what it is made of) of a prototype that passes, from `py2sv.cost` in its own
    process; (None, why) when it cannot be read."""
    path = golden_path(task)
    if path is None or not path.is_file():
        return None, "no golden model"
    with tempfile.TemporaryDirectory(prefix="flux-cost-") as d:
        src = Path(d) / "prototype.py"
        src.write_text(code)
        try:
            run = subprocess.run([sys.executable, "-c", "from flux_loop.golden_proto import _cost_main; _cost_main()",
                                  str(src), str(path)], capture_output=True, text=True, timeout=timeout_s)
            doc = json.loads(run.stdout.strip().splitlines()[-1])
        except (subprocess.TimeoutExpired, ValueError, IndexError):
            return None, "the cost could not be measured"
    return (float(doc["cost"]), doc["why"]) if "cost" in doc else (None, doc.get("error", "not measured"))


def _cost_main() -> None:
    from .py2sv import Unsupported, cost

    src, golden = sys.argv[1:3]
    g = load(Path(golden))
    try:
        c, why = cost(Path(src).read_text(), list(g.ports), exhaustive(g) or golden_vectors_of(g))
        print(json.dumps({"cost": c, "why": why}))
    except Unsupported as exc:
        print(json.dumps({"error": f"not read: {exc}"}))


def golden_vectors_of(g: Any) -> list[dict[str, Any]]:
    from flux_codegen_rtl_harness import golden_vectors

    return golden_vectors(g)


#: What the cost pass tells the model (D613), beside the numbers.
CHEAPER = ("Cheaper, every input still passing: a formula rather than a table (fewer segments, "
           "a lower-degree polynomial, regions decided by comparisons), the narrowest fixed point "
           "that still rounds right (a 16-bit float needs far fewer bits than Q32.32), no dividers "
           "(multiply by a reciprocal constant and shift), fewer and narrower multipliers.")


def judge(code: str, part: str | None, state: Any, g: Any, rows: list[dict[str, Any]], task: Any, table_max: int) -> Verdict:
    """The golden check; in a cost pass (the part's `optimise` = {kind: cost}), a passing
    prototype is scored by its hardware cost against the pass's goal."""
    v = check(code, g, rows, table_max=table_max)
    goal = getattr(state.part(part), "optimise", None) if state is not None and hasattr(state, "part") else None
    if not goal or goal.get("kind") != "cost":
        return v
    if not v.ok:
        return Verdict(False, 1e9 + v.score, v.why + "\nOPTIMISING for cost: every input must pass again before the "
                       "cost counts", v.payload)
    c, why = prototype_cost(code, task)
    if c is None:
        return Verdict(False, 1e9, f"passes every input, but {why}")
    ok = c <= goal["target"]
    return Verdict(ok, c, f"passes every input; {why} -- it was {goal['cost']:,.0f} when this pass began, the goal "
                   f"is <= {goal['target']:,.0f} ({'REACHED' if ok else 'not yet'}). {CHEAPER}")
