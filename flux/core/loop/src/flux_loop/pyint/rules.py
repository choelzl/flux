"""What makes a prototype TRANSCRIBABLE, checked rather than asked for (D468).

D424's prototype stage asks the model for the algorithm in Python first -- integer and bit
operations on the data path, tables built once with float math as a ROM generator would -- and
transcribes only a prototype that passes 0 over. The prompt stated those rules; nothing checked
them. On the live NLU run the model's `exp` prototype was

    y_f16 = np.exp(x.view(np.float16))

which passes 0 over because it IS the reference, and then sat in every RTL prompt for three days
as "VERIFIED PROTOTYPE -- TRANSCRIBE it 1:1". There was nothing to transcribe, every SystemVerilog
attempt was invented from nothing, and the best of them was 43% wrong. A rule stated but not
checked is a rule the model will find the shortcut through.

So this is the check, and it checks exactly the rule: **no float arithmetic on the DATA PATH.**
The data path is everything that depends on `design`'s input -- the parameter, and every name
computed from it, followed through assignments, `np.where`, indexing and calls. A float
computation whose operands do not depend on the input is a ROM being generated (the seeded
reciprocal builds its 1024-entry table from `np.arange(1024)` inside `design`, and that is fine:
the table does not depend on x). A float computation with the input in it is the reference
function in a costume. Inside a function, on the data path, the following are refused:

* a transcendental or float-valued call: `np.exp`, `np.log`, `np.sqrt`, `np.tanh`, `np.power`,
  `np.divide`, everything in `math`, ...
* a reinterpretation as float: `.view(np.float16)`, `.astype(np.float32)`, `float(...)`
* true division (`/` yields a float; `//` is a shifter or a divider) and a float literal in
  the same expression -- a constant on the data path is an integer in a declared Q format.

Each violation names its line and says what to do instead, so the refusal is a lesson and not a
wall. The check is an AST walk and runs BEFORE the prototype is executed, which also saves the
harness run on the 65536 inputs.
"""

from __future__ import annotations

import ast

__all__ = ["hardware_subset_violations"]

#: numpy names that compute a float or a transcendental of their argument.
_FLOAT_CALLS = {
    "exp", "exp2", "expm1", "log", "log2", "log10", "log1p", "sqrt", "cbrt", "power",
    "float_power", "tanh", "sinh", "cosh", "arctanh", "sin", "cos", "tan", "arcsin", "arccos",
    "arctan", "arctan2", "divide", "true_divide", "reciprocal", "hypot", "erf", "erfc",
    "logaddexp", "logaddexp2", "float16", "float32", "float64", "single", "double", "half",
    "longdouble",
}
_FLOAT_DTYPES = {"float16", "float32", "float64", "single", "double", "half", "longdouble",
                 "float", "floating"}


def hardware_subset_violations(code: str) -> list[str]:
    """Every way `code`'s functions put float arithmetic on the data path, as
    "line N: what -- what to do instead". Empty means the prototype is something a
    SystemVerilog transcription can follow. A prototype without a `design` function is one
    violation on its own."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"line {exc.lineno or 0}: the prototype does not parse ({exc.msg})"]
    functions = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    out: list[str] = []
    design = functions.get("design")
    if design is None:
        out.append("line 1: no `design(x)` function -- the harness calls design() on all "
                   "65536 inputs")
        return out
    # INTERPROCEDURAL (D478): the input is `design`'s first parameter; a helper is analysed
    # when it is CALLED with a tainted argument, with exactly those parameters tainted -- a
    # table builder called on the configuration alone is never on the data path, however
    # much float work it does (that is a ROM being generated, the D468 allowance).
    memo: dict[tuple[str, tuple[str, ...]], None] = {}
    first = design.args.args[0].arg if design.args.args else None
    _Taint(design, {first} if first else set(), functions, memo, out)
    out.extend(_whole_domain_tables(tree))
    out.extend(_dicts_of_tables(tree))
    return sorted(set(out), key=lambda s: (int(s.split(":")[0].split()[1]), s))


def _dicts_of_tables(tree: ast.AST) -> list[str]:
    """A dict literal anywhere is a table of tables picked by a VALUE (D491: tanh's
    `TABLES = {-1: rom(...), 0: rom(...), 1: rom(...)}` then `TABLES[e] if e in TABLES
    else TABLES[0]` -- a Python lookup by an array, dead at "unhashable type" with nothing
    said). Hardware has one ROM: the cases side by side, indexed by (case << T) | idx."""
    out: list[str] = []

    def table_like(v: ast.AST) -> bool:              # a value that IS a built table
        return isinstance(v, ast.Call) and _dotted(v.func).rsplit(".", 1)[-1] in _TABLE_MAKERS

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and any(table_like(v) for v in node.values if v is not None) \
                or isinstance(node, ast.DictComp) and table_like(node.value):
            out.append(f"line {getattr(node, 'lineno', 0)}: a dict is a table of tables picked "
                       "by a value -- hardware has one ROM: put the cases side by side, "
                       "np.concatenate([rom(f, lo0, hi0, 1 << T, M), rom(f, lo1, hi1, 1 << T, M), "
                       "...]), and index it with (case << T) | idx where case is 0, 1, 2 ... "
                       "from a comparison; or look the value up in each table and select() "
                       "between the VALUES")
    return out


#: A table may hold this many entries at most (D420's oracle cap): a 2^10 table is a reduced
#: argument's; 2^16 is the input domain itself.
MAX_TABLE_ENTRIES = 1024
_RANGE_MAKERS = {"arange", "range", "zeros", "empty", "ones", "linspace", "full"}
_TABLE_MAKERS = _RANGE_MAKERS | {"rom", "slope_rom", "array", "asarray", "concatenate"}
_TABLE_FUNCS = {"exp2", "exp", "ln", "log2", "recip", "rsqrt", "sqrt", "sigmoid", "tanh", "erf", "gelu"}


def _static_int(node: ast.AST) -> int | None:
    """The value of a literal size: 65536, 0x10000, 1 << 16, 2 ** 16, 4 * 1024."""
    try:
        v = eval(compile(ast.Expression(ast.fix_missing_locations(node)), "<n>", "eval"), {})  # noqa: S307
    except Exception:  # noqa: BLE001
        return None
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _whole_domain_tables(tree: ast.AST) -> list[str]:
    """Every range/array built with a literal size above the cap, wherever it is built: the
    live run's two "best" prototypes were `np.log` over `np.arange(65536)` indexed by the
    input (D480) -- input-independent, so the taint rule saw a ROM being generated, and a
    ROM the size of the domain IS the reference."""
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name in ("rom", "slope_rom") and len(node.args) >= 4:
            # D491: tanh reached 0 over on a 4096-entry rom and learnt the cap from the
            # transpiler -- the rule the transpiler enforces is refused BEFORE the run
            n = _static_int(node.args[3])
            if n is not None and n > MAX_TABLE_ENTRIES:
                out.append(f"line {getattr(node, 'lineno', 0)}: {name}(...) with {n} entries -- "
                           f"tables are capped at {MAX_TABLE_ENTRIES} entries (D420): reach the "
                           "precision with interp1 (a slope_rom and the bits below the index) "
                           "or a second-order term, not with a wider table")
            continue
        if name not in _RANGE_MAKERS or not node.args:
            continue
        sizes = [_static_int(a) for a in node.args[:3]]
        if name == "linspace" and len(node.args) >= 3:
            sizes = [sizes[2]]
        elif name in ("arange", "range") and len(node.args) >= 2:
            lo, hi = sizes[0], sizes[1]
            sizes = [hi - lo if lo is not None and hi is not None else None]
        big = [n for n in sizes if n is not None and n > MAX_TABLE_ENTRIES]
        if big:
            out.append(f"line {getattr(node, 'lineno', 0)}: a {big[0]}-entry table is the input "
                       f"domain itself -- the reference as a ROM. Tables are capped at "
                       f"{MAX_TABLE_ENTRIES} entries (D420): build them on a REDUCED argument "
                       f"(a mantissa slice, a fraction of the reduced range), never on x. If "
                       "this is a self-test: the harness runs design() on every input itself")
    return out


class _Taint:
    """Follows the input through one function body and flags float work on it."""

    def __init__(self, fn: ast.FunctionDef | ast.AsyncFunctionDef, tainted: set[str],
                 functions: dict[str, ast.FunctionDef] | None = None,
                 memo: dict | None = None, out: list[str] | None = None) -> None:
        self.tainted: set[str] = set(tainted)
        self.inputs: set[str] = set(tainted)          # the parameters carrying the data
        self.functions = functions or {}
        self.memo = memo if memo is not None else {}
        self.violations: list[str] = out if out is not None else []
        self._flagged: set[int] = set()
        # Two passes over the body so a name assigned late in a loop taints its earlier uses.
        for _ in range(2):
            for stmt in fn.body:
                self._stmt(stmt)

    # ---- statements: what becomes tainted
    def _stmt(self, node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return                          # its own function, walked on its own
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if value is not None:
                self._expr(value)
                if isinstance(node, ast.AugAssign) or (value is not None and self._depends(value)):
                    for t in targets:
                        self.tainted |= _names_in(t)
            return
        if isinstance(node, ast.For):
            self._expr(node.iter)
            if self._depends(node.iter):
                self.tainted |= _names_in(node.target)
            for s in node.body + node.orelse:
                self._stmt(s)
            return
        if isinstance(node, (ast.While, ast.If)):
            self._expr(node.test)
            if self._depends(node.test):
                # PER-ELEMENT CONTROL FLOW (D482): design() runs on all 65,536 inputs as one
                # array; `if` on data is the way every model writes it and the way none of
                # it runs. Named statically, every site, before an attempt is spent crashing.
                kind = "while" if isinstance(node, ast.While) else "if"
                if id(node) not in self._flagged:
                    self._flagged.add(id(node))
                    self.violations.append(
                        f"line {node.lineno}: `{kind}` on data -- design() runs on ALL inputs at "
                        f"once as one integer array; write both outcomes and pick with "
                        f"np.where(cond, a, b) / select(); an early `return` inside `if` becomes a "
                        f"where at the end; specials() handles NaN/Inf/zero")
            for s in node.body + node.orelse:
                self._stmt(s)
            return
        if isinstance(node, (ast.With,)):
            for s in node.body:
                self._stmt(s)
            return
        if isinstance(node, ast.Return) and node.value is not None:
            self._expr(node.value)
            return
        if isinstance(node, ast.Expr):
            self._expr(node.value)

    # ---- expressions: flag float work whose operands depend on the input
    def _expr(self, node: ast.AST) -> None:
        for sub in ast.walk(node):
            if id(sub) in self._flagged:
                continue
            what = self._violation(sub)
            if what:
                self._flagged.add(id(sub))
                self.violations.append(f"line {getattr(sub, 'lineno', 0)}: {what}")
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                    and sub.func.id in self.functions:
                self._call(sub, self.functions[sub.func.id])

    def _call(self, call: ast.Call, fn: ast.FunctionDef) -> None:
        """A helper called with tainted arguments is walked with those parameters tainted."""
        params = [a.arg for a in fn.args.args]
        tainted = {params[i] for i, a in enumerate(call.args) if i < len(params) and self._depends(a)}
        tainted |= {k.arg for k in call.keywords if k.arg and self._depends(k.value)}
        if not tainted:
            return
        key = (fn.name, tuple(sorted(tainted)))
        if key in self.memo:
            return
        self.memo[key] = None
        _Taint(fn, tainted, self.functions, self.memo, self.violations)

    def _depends(self, node: ast.AST) -> bool:
        return bool(_names_in(node) & self.tainted)

    def _violation(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.BoolOp) and any(self._depends(v) for v in node.values):
            op = "and" if isinstance(node.op, ast.And) else "or"
            return (f"`{op}` on data is a Python truth test of a whole array -- use "
                    f"`{'&' if op == 'and' else '|'}` between comparisons (parenthesised)")
        if isinstance(node, ast.IfExp) and self._depends(node.test):
            return "`a if cond else b` on data -- np.where(cond, a, b) / select(cond, a, b)"
        if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
            return ("a comprehension inside a function builds a table per call, as a Python "
                    "list -- build it ONCE at module level with rom(func, lo, hi, entries, "
                    "frac_bits) / slope_rom(...) (or np.array([...])) and index it here")
        if isinstance(node, ast.Compare) and any(isinstance(o, (ast.In, ast.NotIn)) for o in node.ops) \
                and (self._depends(node.left) or any(self._depends(c) for c in node.comparators)):
            return ("`in` on data is a Python membership test of a whole array -- compare with "
                    "`==` (or a range check) and select() between values; a case-per-table "
                    "design is one concatenated ROM indexed by (case << T) | idx")
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            head, _, _rest = name.partition(".")
            last = name.rsplit(".", 1)[-1]
            args_depend = any(self._depends(a) for a in node.args) or \
                any(self._depends(k.value) for k in node.keywords)
            if name in _TABLE_FUNCS and args_depend:
                # D484: `recip(y_abs)` -- the float table FUNCTION (there for rom) applied to an
                # FP16 bit pattern on the data path
                return (f"`{name}(...)` is the float function tables are built from (rom({name}, "
                        f"...)), not an operator on data -- on the data path build a ROM of it on "
                        f"a reduced argument and index that, or use integer arithmetic")
            if name in ("min", "max", "abs", "int", "round", "bool") and args_depend:
                alt = {"min": "np.minimum", "max": "np.maximum", "abs": "np.abs",
                       "int": "nothing (it is an integer array already)",
                       "round": "round_rne()", "bool": "a comparison"}[name]
                return f"`{name}(...)` on data is Python's scalar builtin -- use {alt}"
            if head == "math" and args_depend:
                return (f"`{name}` is float math on the data path -- compute it ONCE into a "
                        f"table from a constant index range, and index the table here")
            if head in ("np", "numpy") and last in _FLOAT_CALLS and args_depend:
                return (f"`{name}` on the data path is the reference function, not an "
                        f"algorithm -- build a ROM from a constant range (float math is fine "
                        f"THERE) and index it with integer arithmetic here")
            if name == "float" and args_depend:
                return "`float(...)` reinterprets the data path as float -- keep it in integers"
            if last in ("view", "astype") and node.args and isinstance(node.func, ast.Attribute):
                target = _dotted(node.args[0]).rsplit(".", 1)[-1]
                if target in _FLOAT_DTYPES and self._depends(node.func.value):
                    return (f"`.{last}({_dotted(node.args[0])})` turns the FP16 bit pattern "
                            f"into a float value -- the hardware only has the bits: split "
                            f"sign, exponent and mantissa with masks and shifts instead")
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Div) and (self._depends(node.left)
                                                 or self._depends(node.right)):
                return ("`/` is true division and yields a float -- use `//` (a shifter when "
                        "the divisor is a power of two, a divider otherwise) or a reciprocal "
                        "table")
            for side, other in ((node.left, node.right), (node.right, node.left)):
                if (isinstance(side, ast.Constant) and isinstance(side.value, float)
                        and self._depends(other)):
                    return (f"the float constant {side.value!r} is on the data path -- "
                            f"express it as an integer in a declared Q format (e.g. "
                            f"round(c * 2**14) with the shift applied explicitly)")
        if isinstance(node, ast.Compare):
            for c in node.comparators:
                if (isinstance(c, ast.Constant) and isinstance(c.value, float)
                        and self._depends(node.left)):
                    return (f"the float constant {c.value!r} is compared with the data path "
                            f"-- compare integers in a declared Q format instead")
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and node.value.id in self.inputs and isinstance(node.slice, ast.Constant) \
                and isinstance(node.slice.value, int):
            # D484: `x[15] == 1` -- Verilog's bit select, written in Python, where it is the
            # 16th ELEMENT of the input array: a constant, and a saturation test that never fired
            k = node.slice.value
            return (f"`{node.value.id}[{k}]` is Python indexing -- element {k} of the whole "
                    f"input array, one scalar -- not Verilog's bit {k}; a bit is "
                    f"`({node.value.id} >> {k}) & 1` (the sign: fp16_sign(x))")
        return None


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
