"""The transpiler: a prototype in the integer subset -> SystemVerilog, widths proven by tracing
(docs/decisions.md D478).

The prototype stage (D424) asks the model for the algorithm as integer numpy over the FP16 bit
patterns, and the D468 gate holds it to the hardware subset: masks, shifts, integer
arithmetic, comparisons, `np.where`, tables built once from the configuration. The step that
then failed every time (D468, D470, D473) was TRANSCRIPTION -- the model rewriting that
prototype as RTL and losing a width, a sign bit, a shift. Everything in that subset has an
exact SystemVerilog spelling, and the widths do not have to be guessed: the prototype runs on
all 65,536 inputs in milliseconds, so every intermediate value's range is a measurement.

So: the prototype's `design(x)` is flattened to one operation per statement (SSA temps), the
flattened program is executed over the full domain with every temp's minimum and maximum
recorded, each temp becomes a `logic` of exactly the width that range needs (signed when the
minimum is negative), and each operation is emitted in a context wide enough for its operands
and truncated to the temp's width. Constant expressions (anything that does not depend on `x`)
are evaluated in Python and emitted as literals; a constant array indexed by a signal is a ROM
function. Helper functions the prototype defines are inlined; `for` loops over a constant
range are unrolled. Anything outside that is REFUSED by name and line, so the model can
rewrite the prototype into the subset rather than the transpiler guessing.

The result is the model's design, not the transpiler's: nothing here knows what exp is. The
exhaustive gate then proves the emitted module against the same reference the prototype was
proved against -- and, separately, against the prototype itself, bit for bit.
"""

from __future__ import annotations

import ast
import copy
import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = ["TranspileError", "cost_estimate", "transpile", "transpile_to_module"]


class TranspileError(ValueError):
    """The prototype uses something outside what the transpiler spells; the message names
    the construct and the line so the prototype can be rewritten."""


_NUMPY_NAMES = {"np", "numpy"}
MAX_TABLE_ENTRIES = 1024           # D420's oracle cap; D480: the whole domain is not a table
_IDENTITY_CALLS = {"astype", "asarray", "array", "int64", "uint16", "uint32", "int32", "uint64",
                   "int", "copy", "squeeze"}
_ZERO_LIKE = {"zeros_like", "zeros"}
_ONE_LIKE = {"ones_like", "ones"}


@dataclass
class _Temp:
    name: str
    expr: ast.expr                 # over SSA names, constants, and table lookups
    line: int
    lo: int = 0
    hi: int = 0
    is_bool: bool = False

    @property
    def signed(self) -> bool:
        return self.lo < 0

    @property
    def width(self) -> int:
        if self.is_bool:
            return 1
        if self.lo < 0:
            return max(int(self.hi).bit_length(), int(-self.lo - 1).bit_length()) + 1
        return max(1, int(self.hi).bit_length())


@dataclass
class _Program:
    temps: list[_Temp] = field(default_factory=list)
    consts: dict[str, Any] = field(default_factory=dict)      # evaluated constant values
    tables: dict[str, np.ndarray] = field(default_factory=dict)
    out: str = ""                                              # the temp that is `y`
    counter: int = 0
    owner: dict[str, str] = field(default_factory=dict)        # SSA name -> function (D496)

    def fresh(self, base: str = "t") -> str:
        self.counter += 1
        return f"{base}_{self.counter}"


def _ev(e: ast.expr, env: dict[str, Any]) -> Any:
    """Evaluate an expression node (synthesised nodes lack positions) in `env`."""
    node = ast.fix_missing_locations(ast.Expression(copy.deepcopy(e)))
    return eval(compile(node, "<const>", "eval"), env)   # noqa: S307 -- the checked prototype


# ------------------------------------------------------------------------------ normalisation
def _load(code: str) -> tuple[ast.Module, dict[str, Any]]:
    tree = ast.parse(code)
    ns: dict[str, Any] = {"np": np, "numpy": np, "math": math}
    try:
        import struct
        ns["struct"] = struct
    except Exception:  # noqa: BLE001
        pass
    exec(compile(tree, "<prototype>", "exec"), ns)          # noqa: S102 -- the checked prototype
    return tree, ns


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise TranspileError(f"no function `{name}` in the prototype")


class _Inliner(ast.NodeTransformer):
    """Straight-line helper functions are inlined at the call site; loops over a constant
    range are unrolled; the result is a flat list of `name = expr` and one `return expr`."""

    def __init__(self, tree: ast.Module, ns: dict[str, Any], xname: str) -> None:
        self.funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.ns = ns
        self.env: dict[str, Any] = dict(ns)           # constants known so far, in order
        self.tainted: set[str] = {xname}               # names that depend on the input
        self.stmts: list[ast.stmt] = []
        self.depth = 0
        self.n_inline = 0
        self._tuples: dict[str, ast.Tuple] = {}      # inlined helpers that returned a tuple
        self.owner: dict[str, str] = {}              # SSA name -> the function it was emitted in (D496)
        self._fn_stack: list[str] = []

    def _emit(self, fresh: str, value: ast.expr, line: int) -> None:
        """Record `fresh = value`; a value with no tainted name is a constant, evaluated
        now so a later constant `if` can be decided."""
        self.stmts.append(ast.Assign(targets=[ast.Name(id=fresh, ctx=ast.Store())],
                                     value=value, lineno=line))
        self.owner[fresh] = self._fn_stack[-1] if self._fn_stack else "design"
        if _names(value) & self.tainted:
            self.tainted.add(fresh)
            return
        try:
            self.env[fresh] = _ev(value, self.env)
        except Exception as exc:  # noqa: BLE001
            raise TranspileError(f"constant `{fresh}` could not be evaluated (line {line}): "
                                 f"{ast.unparse(value)[:60]}: {exc}")

    def flatten(self, fn: ast.FunctionDef, bindings: dict[str, ast.expr] | None = None
                ) -> ast.expr:
        """Emit `fn`'s body into `self.stmts` with its locals renamed under `bindings`
        (parameter name -> expression); returns the return expression."""
        rename = dict(bindings or {})
        ret: ast.expr | None = None
        self._fn_stack.append(fn.name)
        try:
            for st in fn.body:
                ret = self._stmt(st, rename)
                if ret is not None:
                    break
        finally:
            self._fn_stack.pop()
        if ret is None:
            raise TranspileError(f"`{fn.name}` has no return (line {fn.lineno})")
        return ret

    def _stmt(self, st: ast.stmt, rename: dict[str, ast.expr]) -> ast.expr | None:
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            return None                                  # a docstring
        if isinstance(st, ast.Assign):
            if len(st.targets) == 1 and isinstance(st.targets[0], ast.Subscript):
                # `a[mask] = v` is `a = np.where(mask, v, a)` -- the idiom every numpy-trained
                # model reaches for first (D480: a 0-over prototype was refused on it)
                tgt = st.targets[0]
                if not isinstance(tgt.value, ast.Name):
                    raise TranspileError(f"only `name[mask] = value` masked assignments (line {st.lineno})")
                where = ast.Call(func=ast.Attribute(value=ast.Name(id="np", ctx=ast.Load()), attr="where",
                                                    ctx=ast.Load()),
                                 args=[tgt.slice, st.value, ast.Name(id=tgt.value.id, ctx=ast.Load())],
                                 keywords=[])
                return self._stmt(ast.Assign(targets=[ast.Name(id=tgt.value.id, ctx=ast.Store())],
                                             value=where, lineno=st.lineno), rename)
            if len(st.targets) == 1 and isinstance(st.targets[0], ast.Tuple):
                # `a, b = e1, e2`, or `a, b = helper(...)` whose return is a tuple (inlined
                # first, so the right side becomes that tuple)
                names = st.targets[0].elts
                value = st.value
                if isinstance(value, ast.Call):
                    value = self._expr(value, rename)
                    if isinstance(value, ast.Name) and value.id in self._tuples:
                        value = self._tuples[value.id]
                    elif isinstance(value, ast.Call) and not (_names(value) & self.tainted):
                        # a CONSTANT call that returns a tuple (D487: `_fixed24(ONE)` inside
                        # fp16_add(x, ONE)) -- evaluated, each element a constant
                        try:
                            got = _ev(value, self.env)
                        except Exception as exc:  # noqa: BLE001
                            raise TranspileError(f"constant call could not be evaluated (line {st.lineno}): {exc}")
                        if isinstance(got, tuple):
                            value = ast.Tuple(elts=[ast.Constant(value=int(np.asarray(g))) for g in got],
                                              ctx=ast.Load())
                if not (isinstance(value, ast.Tuple) and len(value.elts) == len(names)
                        and all(isinstance(n, ast.Name) for n in names)):
                    raise TranspileError(f"tuple assignment needs a tuple of the same length on the right (line {st.lineno})")
                st = ast.Assign(targets=[st.targets[0]], value=value, lineno=st.lineno)
                temps = []
                for k, val in enumerate(st.value.elts):
                    tmp = f"_unpack__{self.n_inline}_{st.lineno}_{k}"
                    self._stmt(ast.Assign(targets=[ast.Name(id=tmp, ctx=ast.Store())], value=val,
                                          lineno=st.lineno), rename)
                    temps.append(tmp)
                for n, tmp in zip(names, temps):
                    self._stmt(ast.Assign(targets=[ast.Name(id=n.id, ctx=ast.Store())],
                                          value=ast.Name(id=tmp, ctx=ast.Load()), lineno=st.lineno), rename)
                return None
            if len(st.targets) > 1 and all(isinstance(t, ast.Name) for t in st.targets):
                # `a = b = e`
                first = st.targets[0]
                self._stmt(ast.Assign(targets=[first], value=st.value, lineno=st.lineno), rename)
                for t in st.targets[1:]:
                    self._stmt(ast.Assign(targets=[t], value=ast.Name(id=first.id, ctx=ast.Load()),
                                          lineno=st.lineno), rename)
                return None
            if len(st.targets) != 1 or not isinstance(st.targets[0], ast.Name):
                raise TranspileError(f"only `name = expr` assignments (line {st.lineno})")
            value = self._expr(st.value, rename)
            name = st.targets[0].id
            fresh = f"{name}__{self.n_inline}" if self.depth else name
            rename[name] = ast.Name(id=fresh, ctx=ast.Load())
            self._emit(fresh, value, st.lineno)
            return None
        if isinstance(st, ast.AugAssign):
            if not isinstance(st.target, ast.Name):
                raise TranspileError(f"only `name op= expr` (line {st.lineno})")
            cur = rename.get(st.target.id, ast.Name(id=st.target.id, ctx=ast.Load()))
            value = ast.BinOp(left=copy.deepcopy(cur), op=st.op, right=self._expr(st.value, rename))
            name = st.target.id
            fresh = f"{name}__{self.n_inline}" if self.depth else name
            rename[name] = ast.Name(id=fresh, ctx=ast.Load())
            self._emit(fresh, value, st.lineno)
            return None
        if isinstance(st, ast.Return):
            if st.value is None:
                raise TranspileError(f"`return` without a value (line {st.lineno})")
            return self._expr(st.value, rename)
        if isinstance(st, ast.For):
            if not isinstance(st.target, ast.Name):
                raise TranspileError(f"only `for name in range(...)` loops (line {st.lineno})")
            for value in self._const_range(st, rename):
                # the loop variable is a constant in each unrolled copy of the body
                fresh = f"{st.target.id}__{self.n_inline}_{value}"
                self._emit(fresh, ast.Constant(value=int(value)), st.lineno)
                rename[st.target.id] = ast.Name(id=fresh, ctx=ast.Load())
                for inner in st.body:
                    if self._stmt(inner, rename) is not None:
                        raise TranspileError(f"`return` inside a loop (line {inner.lineno})")
            return None
        if isinstance(st, ast.If):
            test = self._expr(st.test, rename)
            if _names(test) & self.tainted:
                raise TranspileError("an `if` on a value that depends on the input is not part "
                                     f"of the subset -- use np.where (line {st.lineno})")
            try:
                taken = bool(_ev(test, self.env))
            except Exception as exc:  # noqa: BLE001
                raise TranspileError(f"the `if` condition could not be evaluated (line {st.lineno}): {exc}")
            for inner in (st.body if taken else st.orelse):
                ret = self._stmt(inner, rename)
                if ret is not None:
                    return ret
            return None
        if isinstance(st, ast.While):
            raise TranspileError(f"`while` is not part of the subset (line {st.lineno})")
        if isinstance(st, ast.Pass):
            return None
        raise TranspileError(f"unsupported statement {type(st).__name__} (line {st.lineno})")

    def _const_range(self, st: ast.For, rename: dict[str, ast.expr]) -> range:
        it = st.iter
        if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range"):
            raise TranspileError(f"only `for name in range(N)` loops (line {st.lineno})")
        try:
            args = [_ev(self._expr(a, rename), self.env) for a in it.args]
        except Exception as exc:  # noqa: BLE001
            raise TranspileError(f"the loop's range must be a constant (line {st.lineno}): {exc}")
        return range(*[int(a) for a in args])

    def _expr(self, e: ast.expr, rename: dict[str, ast.expr]) -> ast.expr:
        if isinstance(e, ast.Name):
            return copy.deepcopy(rename[e.id]) if e.id in rename else e
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id in self.funcs:
            fn = self.funcs[e.func.id]
            args = [self._expr(a, rename) for a in e.args]
            if not any(_names(a) & self.tainted for a in args):
                # every argument is a constant: the call is one too (a table builder, a
                # constant helper) and is evaluated whole rather than inlined
                new = copy.copy(e)
                new.args = args
                new.keywords = [ast.keyword(arg=k.arg, value=self._expr(k.value, rename)) for k in e.keywords]
                return new
            params = [a.arg for a in fn.args.args]
            defaults = fn.args.defaults
            # keyword arguments bind by the callee's signature (D491: tanh's 0-over member
            # was refused for `from_fixed(v, M, sign=s)`)
            by_name: dict[str, ast.expr] = {}
            for k in e.keywords:
                if k.arg is None or k.arg not in params or params.index(k.arg) < len(args):
                    raise TranspileError(f"`{fn.name}` has no keyword `{k.arg}` to bind (line {e.lineno})")
                by_name[k.arg] = self._expr(k.value, rename)
            if len(args) + len(by_name) > len(params):
                raise TranspileError(f"`{fn.name}` called with {len(args) + len(by_name)} argument(s) (line {e.lineno})")
            bindings: dict[str, ast.expr] = {}
            self.n_inline += 1
            self.depth += 1
            try:
                for i, p in enumerate(params):
                    if i < len(args):
                        val = args[i]
                    elif p in by_name:
                        val = by_name[p]
                    elif i >= len(params) - len(defaults):
                        val = defaults[i - (len(params) - len(defaults))]
                    else:
                        raise TranspileError(f"`{fn.name}` called without `{p}` (line {e.lineno})")
                    fresh = f"{p}__{self.n_inline}"
                    self._emit(fresh, val, e.lineno)
                    bindings[p] = ast.Name(id=fresh, ctx=ast.Load())
                ret = self.flatten(fn, bindings)
            finally:
                self.depth -= 1
            fresh = f"{fn.name}__{self.n_inline}"
            if isinstance(ret, ast.Tuple):
                # a tuple return: each element to its own name, the tuple remembered so the
                # caller's `a, b = helper(...)` can take it apart
                elts = []
                for k, el in enumerate(ret.elts):
                    en = f"{fresh}_{k}"
                    self._emit(en, el, e.lineno)
                    elts.append(ast.Name(id=en, ctx=ast.Load()))
                self._tuples[fresh] = ast.Tuple(elts=elts, ctx=ast.Load())
                return ast.Name(id=fresh, ctx=ast.Load())
            self._emit(fresh, ret, e.lineno)
            return ast.Name(id=fresh, ctx=ast.Load())
        new = copy.copy(e)
        for fld, val in ast.iter_fields(e):
            if isinstance(val, ast.expr):
                setattr(new, fld, self._expr(val, rename))
            elif isinstance(val, list):
                setattr(new, fld, [self._expr(v, rename) if isinstance(v, ast.expr) else v for v in val])
        return new


# ------------------------------------------------------------------------ taint and flattening
def _names(e: ast.expr) -> set[str]:
    return {n.id for n in ast.walk(e) if isinstance(n, ast.Name)}


class _Flattener:
    """Tainted statements (depending on `x`) become one-operation temps; constants are
    evaluated in the prototype's namespace."""

    def __init__(self, ns: dict[str, Any], xname: str) -> None:
        self.ns = dict(ns)
        self.prog = _Program()
        self.tainted: dict[str, str] = {xname: "x"}       # source name -> SSA temp
        self.xname = xname

    def run(self, stmts: list[ast.stmt], ret: ast.expr, owner: dict[str, str] | None = None) -> _Program:
        self._owner = dict(owner or {})                    # inliner name -> function (D496)
        self._cur = "design"
        for st in stmts:
            assert isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name)
            name = st.targets[0].id
            self._cur = self._owner.get(name, "design")
            if _names(st.value) & set(self.tainted):
                self.tainted[name] = self._temp(st.value, st.lineno, base=name)
            else:
                try:
                    self.ns[name] = _ev(st.value, self.ns)
                except Exception as exc:  # noqa: BLE001
                    raise TranspileError(f"constant `{name}` could not be evaluated (line {st.lineno}): {exc}")
        self._cur = "design"
        self.prog.out = self._temp(ret, getattr(ret, "lineno", 0), base="y")
        return self.prog

    def _const_value(self, e: ast.expr, line: int) -> Any:
        try:
            return _ev(e, self.ns)
        except Exception as exc:  # noqa: BLE001
            raise TranspileError(f"could not evaluate a constant (line {line}): {ast.unparse(e)}: {exc}")

    def _temp(self, e: ast.expr, line: int, base: str = "t") -> str:
        """Flatten `e` (tainted) into temps; return the temp holding it. An expression that
        flattens to a bare name is an alias, not a new temp."""
        e = self._flat(e, line)
        if isinstance(e, ast.Name):
            return e.id
        name = self.prog.fresh(base)
        self.prog.temps.append(_Temp(name=name, expr=e, line=line))
        self.prog.owner[name] = getattr(self, "_cur", "design")
        return name

    def _leaf(self, e: ast.expr, line: int) -> ast.expr:
        """A sub-expression: a temp name, `x`, or a constant node."""
        if not (_names(e) & set(self.tainted)):
            v = self._const_value(e, line)
            if isinstance(v, np.ndarray) and v.ndim == 0:
                v = v.item()
            if isinstance(v, (bool, np.bool_)):
                return ast.Constant(value=bool(v))
            if isinstance(v, (int, np.integer)):
                return ast.Constant(value=int(v))
            if isinstance(v, np.ndarray) and v.ndim == 1:
                raise TranspileError(f"a constant array used as a value, not indexed (line {line}): {ast.unparse(e)}")
            raise TranspileError(f"a constant of type {type(v).__name__} in the data path (line {line}): {ast.unparse(e)}")
        if isinstance(e, ast.Name):
            return ast.Name(id=self.tainted[e.id], ctx=ast.Load())
        return ast.Name(id=self._temp(e, line), ctx=ast.Load())

    def _flat(self, e: ast.expr, line: int) -> ast.expr:
        """One operation over leaves."""
        line = getattr(e, "lineno", line)
        if isinstance(e, ast.Name):
            if e.id in self.tainted:
                return ast.Name(id=self.tainted[e.id], ctx=ast.Load())
            return self._leaf(e, line)
        if isinstance(e, ast.Constant):
            return self._leaf(e, line)
        if isinstance(e, ast.BinOp):
            return ast.BinOp(left=self._leaf(e.left, line), op=e.op, right=self._leaf(e.right, line))
        if isinstance(e, ast.UnaryOp):
            return ast.UnaryOp(op=e.op, operand=self._leaf(e.operand, line))
        if isinstance(e, ast.Compare):
            if len(e.ops) != 1:
                raise TranspileError(f"chained comparison (line {line})")
            return ast.Compare(left=self._leaf(e.left, line), ops=e.ops,
                               comparators=[self._leaf(e.comparators[0], line)])
        if isinstance(e, ast.BoolOp):
            vals = [self._leaf(v, line) for v in e.values]
            cur: ast.expr = ast.BoolOp(op=e.op, values=vals[:2])
            for v in vals[2:]:                             # a chain, two at a time
                cur = ast.BoolOp(op=e.op, values=[ast.Name(id=self._temp(cur, line), ctx=ast.Load()), v])
            return cur
        if isinstance(e, ast.Subscript):
            base = e.value
            if _names(base) & set(self.tainted):
                raise TranspileError(f"indexing a signal (line {line}): {ast.unparse(e)} -- use shifts and masks")
            table = self._const_value(base, line)
            if not (isinstance(table, np.ndarray) and table.ndim == 1):
                raise TranspileError(f"only a 1-D constant array may be indexed (line {line}): {ast.unparse(e)}")
            tname = ast.unparse(base)
            tname = "".join(ch if ch.isalnum() else "_" for ch in tname).strip("_").upper()[:24] or "TAB"
            if len(table) > MAX_TABLE_ENTRIES:
                raise TranspileError(f"a {len(table)}-entry table indexed by the input (line {line}) is the "
                                     f"reference as a ROM; tables are capped at {MAX_TABLE_ENTRIES} entries")
            if tname in self.prog.tables and not np.array_equal(self.prog.tables[tname], table):
                tname = self.prog.fresh(tname)
            self.prog.tables[tname] = table.astype(np.int64)
            return ast.Subscript(value=ast.Name(id=tname, ctx=ast.Load()),
                                 slice=self._leaf(e.slice, line), ctx=ast.Load())
        if isinstance(e, ast.Call):
            fname, attr = self._callee(e)
            if attr in _IDENTITY_CALLS or fname in _IDENTITY_CALLS:
                target = e.func.value if isinstance(e.func, ast.Attribute) and attr == "astype" else (e.args[0] if e.args else None)
                if target is None:
                    raise TranspileError(f"empty cast (line {line})")
                return self._flat(target, line)
            if fname in _ZERO_LIKE or fname in _ONE_LIKE:
                one = fname in _ONE_LIKE
                as_bool = any(k.arg == "dtype" and ast.unparse(k.value).split(".")[-1] in ("bool", "bool_")
                              for k in e.keywords)
                return ast.Constant(value=(one if as_bool else int(one)))
            if fname in ("full_like", "full") and len(e.args) >= 2:
                return self._flat(e.args[1], line)           # the fill value, broadcast
            if fname in ("where",):
                if len(e.args) != 3:
                    raise TranspileError(f"np.where needs three arguments (line {line})")
                if not (_names(e.args[0]) & set(self.tainted)):
                    # a CONSTANT condition (D483: the array form of `if cfg.cb:` is a where on
                    # a knob) -- only the branch taken exists in hardware
                    try:
                        taken = bool(np.all(self._const_value(e.args[0], line)))
                    except TranspileError:
                        taken = None
                    if taken is not None:
                        return self._flat(e.args[1 if taken else 2], line)
                return ast.Call(func=ast.Name(id="where", ctx=ast.Load()),
                                args=[self._leaf(a, line) for a in e.args], keywords=[])
            if fname in ("minimum", "maximum", "abs", "absolute", "clip", "logical_and",
                         "logical_or", "logical_not", "bitwise_and", "bitwise_or", "bitwise_xor",
                         "left_shift", "right_shift"):
                return ast.Call(func=ast.Name(id=fname, ctx=ast.Load()),
                                args=[self._leaf(a, line) for a in e.args], keywords=[])
            raise TranspileError(f"call `{ast.unparse(e.func)}` is not in the subset (line {line})")
        raise TranspileError(f"unsupported expression {type(e).__name__} (line {line}): {ast.unparse(e)[:60]}")

    @staticmethod
    def _callee(e: ast.Call) -> tuple[str, str]:
        f = e.func
        if isinstance(f, ast.Attribute):
            root = f.value
            if isinstance(root, ast.Name) and root.id in _NUMPY_NAMES:
                return f.attr, ""
            return "", f.attr
        if isinstance(f, ast.Name):
            return f.id, ""
        return "", ""


# ------------------------------------------------------------------------------------ tracing
def _trace(prog: _Program, xs: np.ndarray) -> None:
    """Run the flattened program over `xs` and record every temp's range."""
    env: dict[str, Any] = {"x": xs.astype(np.int64), "np": np}
    for tname, tab in prog.tables.items():
        env[tname] = tab
    env.update({"where": np.where, "minimum": np.minimum, "maximum": np.maximum, "abs": np.abs,
                "absolute": np.abs, "clip": np.clip, "logical_and": np.logical_and,
                "logical_or": np.logical_or, "logical_not": np.logical_not,
                "bitwise_and": np.bitwise_and, "bitwise_or": np.bitwise_or,
                "bitwise_xor": np.bitwise_xor, "left_shift": np.left_shift,
                "right_shift": np.right_shift})
    for t in prog.temps:
        code = compile(ast.Expression(ast.fix_missing_locations(_py(t.expr))), f"<{t.name}>", "eval")
        try:
            v = eval(code, env)                          # noqa: S307 -- the flattened prototype
        except Exception as exc:  # noqa: BLE001
            raise TranspileError(f"`{t.name}` (line {t.line}) failed to evaluate: {exc}")
        v = np.asarray(v)
        if v.ndim == 0:
            v = np.broadcast_to(v, xs.shape)
        if v.dtype == np.bool_:
            t.is_bool = True                              # kept boolean: `~` is logical here
            env[t.name] = v
            t.lo, t.hi = int(v.min()), int(v.max())
            continue
        if not np.issubdtype(v.dtype, np.integer):
            raise TranspileError(f"`{t.name}` (line {t.line}) is not an integer value: {v.dtype}")
        env[t.name] = v.astype(np.int64)
        t.lo, t.hi = int(v.min()), int(v.max())


def _py(e: ast.expr) -> ast.expr:
    """The flattened expression as Python again (table lookups are plain subscripts)."""
    return copy.deepcopy(e)


# ----------------------------------------------------------------------------------- emission
def _operands(e: ast.expr) -> list[ast.expr]:
    """The leaves an operation reads (not the function or table it names)."""
    if isinstance(e, (ast.Name, ast.Constant)):
        return [e]
    if isinstance(e, ast.BinOp):
        return [e.left, e.right]
    if isinstance(e, ast.UnaryOp):
        return [e.operand]
    if isinstance(e, ast.Compare):
        return [e.left, e.comparators[0]]
    if isinstance(e, ast.BoolOp):
        return list(e.values)
    if isinstance(e, ast.Subscript):
        return [e.slice]
    if isinstance(e, ast.Call):
        return list(e.args)
    return []


def _lit(v: int, width: int, signed: bool) -> str:
    if v < 0:
        return f"-{width}'sd{-v}"
    return f"{width}'{'s' if signed else ''}d{v}"


class _Emitter:
    def __init__(self, prog: _Program) -> None:
        self.prog = prog
        self.by = {t.name: t for t in prog.temps}

    def sig(self, name: str) -> tuple[int, bool]:
        base = _undelayed(name)
        if base == "x":
            return 16, False
        t = self.by[base]
        return t.width, t.signed

    def _width_of(self, leaf: ast.expr) -> int:
        if isinstance(leaf, ast.Constant):
            return int(leaf.value).bit_length() if not isinstance(leaf.value, bool) else 1
        return self.sig(leaf.id)[0] if isinstance(leaf, ast.Name) else 64

    def cast(self, leaf: ast.expr, width: int, signed: bool) -> str:
        """`leaf` as a `width`-bit expression, sign-extended when it is signed."""
        if isinstance(leaf, ast.Constant):
            v = int(leaf.value) if not isinstance(leaf.value, bool) else int(leaf.value)
            return _lit(v, width, signed or v < 0)
        assert isinstance(leaf, ast.Name)
        w, s = self.sig(leaf.id)
        inner = f"$signed({leaf.id})" if s else leaf.id
        ext = f"{width}'({inner})"
        return f"$signed({ext})" if signed else ext

    def emit(self, t: _Temp) -> str:
        e = t.expr
        W, S = t.width, t.signed
        leaves = _operands(e)
        widths = [self.sig(n.id)[0] for n in leaves if isinstance(n, ast.Name)]
        signs = [self.sig(n.id)[1] for n in leaves if isinstance(n, ast.Name)]
        signs += [bool(int(n.value) < 0) for n in leaves if isinstance(n, ast.Constant) and not isinstance(n.value, bool)]
        consts = [abs(int(n.value)) for n in leaves if isinstance(n, ast.Constant) and not isinstance(n.value, bool)]
        cw = max([W] + widths + [c.bit_length() + 1 for c in consts]) + 1
        cs = S or any(signs)
        c = lambda leaf: self.cast(leaf, cw, cs)   # noqa: E731

        if isinstance(e, ast.Name):
            rhs = c(e)
        elif isinstance(e, ast.Constant):
            rhs = _lit(int(e.value), W, S)
            return f"  assign {t.name} = {rhs};"
        elif isinstance(e, ast.BinOp):
            op = e.op
            if isinstance(op, ast.Add):
                rhs = f"({c(e.left)} + {c(e.right)})"
            elif isinstance(op, ast.Sub):
                rhs = f"({c(e.left)} - {c(e.right)})"
            elif isinstance(op, ast.Mult):
                rhs = f"({c(e.left)} * {c(e.right)})"
            elif isinstance(op, ast.BitAnd):
                rhs = f"({c(e.left)} & {c(e.right)})"
            elif isinstance(op, ast.BitOr):
                rhs = f"({c(e.left)} | {c(e.right)})"
            elif isinstance(op, ast.BitXor):
                rhs = f"({c(e.left)} ^ {c(e.right)})"
            elif isinstance(op, ast.LShift):
                rhs = f"({c(e.left)} << {self.shift_amount(e.right)})"
            elif isinstance(op, ast.RShift):
                rhs = f"({c(e.left)} {'>>>' if cs else '>>'} {self.shift_amount(e.right)})"
            elif isinstance(op, ast.FloorDiv):
                d = self._pow2(e.right, t)
                if d is not None:
                    rhs = f"({c(e.left)} {'>>>' if cs else '>>'} {d})"
                else:                                      # a constant divisor, both sides >= 0
                    rhs = f"({c(e.left)} / {c(e.right)})"
            elif isinstance(op, ast.Mod):
                d = self._pow2(e.right, t)
                if d is not None:
                    rhs = f"({c(e.left)} & {_lit((1 << d) - 1, cw, False)})"
                else:
                    rhs = f"({c(e.left)} % {c(e.right)})"
            else:
                raise TranspileError(f"operator {type(op).__name__} (line {t.line})")
        elif isinstance(e, ast.UnaryOp):
            # the operand is a size cast, N'(v); `~N'(v)` is read by yosys as a cast of size
            # ~N -- "Static cast with zero or negative size!" on sigmoid's `~2'(is_zero)`, the
            # one operator of seven that failed the screen (D495). Verilator read it as meant.
            if isinstance(e.op, ast.USub):
                rhs = f"(-({c(e.operand)}))"
            elif isinstance(e.op, ast.Invert):
                rhs = f"(~({c(e.operand)}))"
            elif isinstance(e.op, ast.Not):
                rhs = f"(!({c(e.operand)}))"
            else:
                raise TranspileError(f"unary {type(e.op).__name__} (line {t.line})")
        elif isinstance(e, ast.Compare):
            op = e.ops[0]
            sym = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">",
                   ast.GtE: ">="}.get(type(op))
            if sym is None:
                raise TranspileError(f"comparison {type(op).__name__} (line {t.line})")
            rhs = f"({c(e.left)} {sym} {c(e.comparators[0])})"
            return f"  assign {t.name} = {rhs};"
        elif isinstance(e, ast.BoolOp):
            sym = "&&" if isinstance(e.op, ast.And) else "||"
            rhs = f" {sym} ".join(f"({c(v)} != {_lit(0, cw, cs)})" for v in e.values)
            return f"  assign {t.name} = {rhs};"
        elif isinstance(e, ast.Subscript):
            tname = e.value.id
            tab = self.prog.tables[tname]
            k = max(1, math.ceil(math.log2(len(tab))))
            idx = self.cast(e.slice, k, False)
            rhs = f"{tname}({idx})"
            return f"  assign {t.name} = {W}'({rhs});"
        elif isinstance(e, ast.Call):
            f = e.func.id
            a = e.args
            if f == "where":
                # a condition wider than one bit is Python's truth test: nonzero (D483)
                cond = self.cast(a[0], 1, False) if self._width_of(a[0]) <= 1 else f"({c(a[0])} != 0)"
                rhs = f"(({cond}) ? {c(a[1])} : {c(a[2])})"
            elif f == "minimum":
                rhs = f"(({c(a[0])} < {c(a[1])}) ? {c(a[0])} : {c(a[1])})"
            elif f == "maximum":
                rhs = f"(({c(a[0])} > {c(a[1])}) ? {c(a[0])} : {c(a[1])})"
            elif f in ("abs", "absolute"):
                rhs = f"(({c(a[0])} < {_lit(0, cw, cs)}) ? (-{c(a[0])}) : {c(a[0])})"
            elif f == "clip":
                rhs = (f"(({c(a[0])} < {c(a[1])}) ? {c(a[1])} : "
                       f"(({c(a[0])} > {c(a[2])}) ? {c(a[2])} : {c(a[0])}))")
            elif f == "logical_and":
                rhs = f"(({c(a[0])} != {_lit(0, cw, cs)}) && ({c(a[1])} != {_lit(0, cw, cs)}))"
                return f"  assign {t.name} = {rhs};"
            elif f == "logical_or":
                rhs = f"(({c(a[0])} != {_lit(0, cw, cs)}) || ({c(a[1])} != {_lit(0, cw, cs)}))"
                return f"  assign {t.name} = {rhs};"
            elif f == "logical_not":
                rhs = f"({c(a[0])} == {_lit(0, cw, cs)})"
                return f"  assign {t.name} = {rhs};"
            elif f == "bitwise_and":
                rhs = f"({c(a[0])} & {c(a[1])})"
            elif f == "bitwise_or":
                rhs = f"({c(a[0])} | {c(a[1])})"
            elif f == "bitwise_xor":
                rhs = f"({c(a[0])} ^ {c(a[1])})"
            elif f == "left_shift":
                rhs = f"({c(a[0])} << {self.shift_amount(a[1])})"
            elif f == "right_shift":
                rhs = f"({c(a[0])} {'>>>' if cs else '>>'} {self.shift_amount(a[1])})"
            else:
                raise TranspileError(f"call `{f}` (line {t.line})")
        else:
            raise TranspileError(f"expression {type(e).__name__} (line {t.line})")
        return f"  assign {t.name} = {W}'({rhs});"

    def shift_amount(self, leaf: ast.expr) -> str:
        if isinstance(leaf, ast.Constant):
            return str(int(leaf.value))
        w, s = self.sig(leaf.id)
        return f"{leaf.id}" if not s else f"$unsigned({leaf.id})"

    def _pow2(self, leaf: ast.expr, t: _Temp) -> int | None:
        """The shift for a power-of-two divisor; None for another positive constant when
        the dividend is never negative (then `/` and `%` agree with Python's floor semantics
        and yosys builds a constant divider); a refusal otherwise."""
        if not isinstance(leaf, ast.Constant):
            raise TranspileError(f"division by a signal (line {t.line}) -- divide by a constant")
        v = int(leaf.value)
        if v <= 0:
            raise TranspileError(f"division by {v} (line {t.line})")
        if v & (v - 1) == 0:
            return v.bit_length() - 1
        left = t.expr.left
        if isinstance(left, ast.Name) and self.sig(left.id)[1]:
            raise TranspileError(f"floor division of a signed value by {v} (line {t.line}) -- "
                                 "Python floors, SystemVerilog truncates; make the dividend non-negative")
        return None

    def rom(self, name: str, words: np.ndarray) -> str:
        k = max(1, math.ceil(math.log2(len(words))))
        lo, hi = int(words.min()), int(words.max())
        signed = lo < 0
        width = (max(hi.bit_length(), (-lo - 1).bit_length()) + 1) if signed else max(1, hi.bit_length())
        hexw = (width + 3) // 4
        mask = (1 << width) - 1
        lines = [f"  function automatic {'signed ' if signed else ''}[{width - 1}:0] {name}(input [{k - 1}:0] i);",
                 "    case (i)"]
        lines += [f"      {k}'d{i}: {name} = {width}'h{int(w) & mask:0{hexw}x};" for i, w in enumerate(words.tolist())]
        lines += [f"      default: {name} = {width}'h{0:0{hexw}x};", "    endcase", "  endfunction"]
        return "\n".join(lines)


def _program(code: str, xs: np.ndarray | None, entry: str) -> tuple[_Program, np.ndarray]:
    tree, ns = _load(code)
    fn = _find_function(tree, entry)
    if not fn.args.args:
        raise TranspileError(f"`{entry}` takes no argument")
    xname = fn.args.args[0].arg
    for extra in fn.args.args[1:]:
        raise TranspileError(f"`{entry}` takes an extra parameter `{extra.arg}`: bind it to a constant first")
    inl = _Inliner(tree, ns, xname)
    ret = inl.flatten(fn)
    prog = _Flattener(ns, xname).run(inl.stmts, ret, owner=inl.owner)
    if xs is None:
        xs = np.arange(0x10000, dtype=np.uint16)
    _trace(prog, xs)
    return prog, xs


def cost_estimate(code: str, *, xs: np.ndarray | None = None, entry: str = "design") -> int:
    """An area proxy from the measured program (D479): every signal's width, a multiplier's
    partial products, a ROM's bits. It ORDERS members of a family; the screen stage
    measures the real number. Raises `TranspileError` like `transpile`."""
    prog, _xs = _program(code, xs, entry)
    cost = 0
    by = {t.name: t for t in prog.temps}
    for t in prog.temps:
        cost += t.width
        if isinstance(t.expr, ast.BinOp) and isinstance(t.expr.op, ast.Mult):
            ws = [by[n.id].width if isinstance(n, ast.Name) and n.id in by else 16
                  for n in (t.expr.left, t.expr.right)]
            cost += ws[0] * ws[1]
    for words in prog.tables.values():
        hi = int(max(abs(int(words.min())), abs(int(words.max())))) if len(words) else 0
        cost += len(words) * max(1, hi.bit_length() + (1 if int(words.min()) < 0 else 0))
    return cost


def _levels(t: _Temp, by: dict[str, _Temp]) -> int:
    """How many logic levels ONE operation adds, from its kind and width (D496): a prefix
    adder or a comparator is log2(W) deep, a multiplier three times that, a barrel shifter
    log2(W), a mux one, a constant shift or mask none, a ROM its address decode. A proxy
    that ORDERS designs and paths; synthesis measures the number."""
    e = t.expr
    W = max(1, t.width)
    lg = max(1, math.ceil(math.log2(W + 1)))

    def w_of(n: ast.expr) -> int:
        return by[n.id].width if isinstance(n, ast.Name) and n.id in by else 1

    if isinstance(e, ast.BinOp):
        op = e.op
        if isinstance(op, (ast.Add, ast.Sub)):
            return lg + 1
        if isinstance(op, ast.Mult):
            return 3 * max(1, math.ceil(math.log2(max(w_of(e.left), w_of(e.right)) + 1))) + 1
        if isinstance(op, (ast.LShift, ast.RShift)):
            return 0 if isinstance(e.right, ast.Constant) else lg
        if isinstance(op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
            return 0 if isinstance(e.right, ast.Constant) or isinstance(e.left, ast.Constant) else 1
        if isinstance(op, (ast.FloorDiv, ast.Mod)):
            return 0 if isinstance(e.right, ast.Constant) and int(e.right.value) & (int(e.right.value) - 1) == 0 else 4 * W
        return lg
    if isinstance(e, ast.UnaryOp):
        return lg + 1 if isinstance(e.op, ast.USub) else 0
    if isinstance(e, ast.Compare):
        return lg + 1
    if isinstance(e, ast.IfExp):
        return 1
    if isinstance(e, ast.Call):
        name = e.func.id if isinstance(e.func, ast.Name) else getattr(e.func, "attr", "")
        if name in ("where", "select"):
            return 1
        if name in ("minimum", "maximum", "clip", "abs"):
            return lg + 2
        return lg + 2                                   # a ROM lookup: address decode + read
    if isinstance(e, ast.Subscript):
        return lg + 2
    return 0


def logic_depth(code: str, *, xs: np.ndarray | None = None, entry: str = "design") -> dict[str, Any]:
    """The LOGIC DEPTH proxy of a prototype (D496): the longest path of the measured program in
    estimated logic levels, and where it runs -- levels per block on the critical path, blocks
    named from the SSA names (`interp1__12_34` -> interp1; the design's own lines as `design`).
    {"depth": int, "path": [(block, levels), ...] longest first, "n": signals}. Raises
    `TranspileError` like `transpile`."""
    prog, _xs = _program(code, xs, entry)
    by = {t.name: t for t in prog.temps}
    level: dict[str, int] = {}
    pred: dict[str, str | None] = {}
    cost: dict[str, int] = {}
    for t in prog.temps:
        ins = [n.id for n in ast.walk(t.expr) if isinstance(n, ast.Name) and n.id in level]
        best = max(ins, key=lambda n: level[n]) if ins else None
        cost[t.name] = _levels(t, by)
        level[t.name] = (level[best] if best else 0) + cost[t.name]
        pred[t.name] = best
    if prog.out not in level:
        return {"depth": 0, "path": [], "n": len(prog.temps)}
    blocks: dict[str, int] = {}
    worst: tuple[int, str, str] | None = None            # (levels, signal, what it is)
    node: str | None = prog.out
    while node is not None:
        block = prog.owner.get(node) or (node.split("__")[0] if "__" in node else "design")
        blocks[block] = blocks.get(block, 0) + cost[node]
        if worst is None or cost[node] > worst[0]:
            worst = (cost[node], node, _describe_op(by[node]))
        node = pred[node]
    path = sorted(blocks.items(), key=lambda kv: -kv[1])
    out = {"depth": int(level[prog.out]), "path": path, "n": len(prog.temps)}
    if worst is not None and worst[0] >= 16:
        base = re.sub(r"(__\d+)?(_\d+)?$", "", worst[1]) or worst[1]
        out["worst"] = {"levels": worst[0], "signal": base, "what": worst[2]}
    return out


def _describe_op(t: _Temp) -> str:
    """One deep operation, named for the report (D496)."""
    e = t.expr
    if isinstance(e, ast.BinOp):
        if isinstance(e.op, (ast.FloorDiv, ast.Mod)) and isinstance(e.right, ast.Constant):
            return (f"a floor-division by the constant {int(e.right.value)}, not a power of two -- a DIVIDER: "
                    "multiply by a reciprocal constant instead, (v * round(2**k / D)) >> k with k wide enough "
                    "for the precision you need, then check the rounding on every input")
        if isinstance(e.op, (ast.FloorDiv, ast.Mod)):
            return "a division by a signal -- a DIVIDER: a reciprocal table (recip_fixed) and a multiply"
        if isinstance(e.op, ast.Mult):
            return f"a {t.width}-bit multiply -- narrower factors (drop bits neither needs), or a table"
        if isinstance(e.op, (ast.LShift, ast.RShift)):
            return "a shift by a data-dependent amount -- a barrel shifter; a constant shift when the amount is known"
    return type(e).__name__


def logic_depth_note(depth: dict) -> str:
    """The deepest single step on the critical path, as advice (D496)."""
    w = depth.get("worst")
    if not w:
        return ""
    return f"the deepest single step is `{w['signal']}` (~{w['levels']} levels): {w['what']}"


_DELAY = re.compile(r"^(.*)_d(\d+)$")


def _undelayed(name: str) -> str:
    """`a_d3` -> `a`: the signal a delayed copy stands for (D496 pipelining)."""
    m = _DELAY.match(name)
    return m.group(1) if m and m.group(1) in _DELAY_BASES else name


_DELAY_BASES: set[str] = set()          # names that HAVE delayed copies in the module being emitted


def pipeline_stages(code: str, *, xs: np.ndarray | None = None, entry: str = "design") -> int:
    """The `PIPELINE = K` a prototype declares at module level (0 when it does not): the
    number of register stages the transpiler cuts the measured program into (D496)."""
    tree, ns = _load(code)
    k = ns.get("PIPELINE", 0)
    try:
        k = int(k)
    except Exception:  # noqa: BLE001
        raise TranspileError(f"PIPELINE must be an integer register count, not {k!r}")
    if k < 0 or k > 64:
        raise TranspileError(f"PIPELINE = {k}: 0 (combinational) to 64 register stages")
    return k


def _stage_of(prog: _Program, k: int) -> dict[str, int]:
    """Which of the k+1 pipeline stages each signal is computed in: the measured program's
    logic levels cut into k+1 nearly equal slices (D496). `x` is stage 0; the output's stage
    is the last."""
    by = {t.name: t for t in prog.temps}
    level: dict[str, int] = {}
    for t in prog.temps:
        ins = [n.id for n in ast.walk(t.expr) if isinstance(n, ast.Name) and n.id in level]
        level[t.name] = (max(level[n] for n in ins) if ins else 0) + _levels(t, by)
    total = max(level.values(), default=0)
    if k <= 0 or total <= 0:
        return {t.name: 0 for t in prog.temps}
    stage = {t.name: min(k, (level[t.name] * (k + 1)) // (total + 1)) for t in prog.temps}
    stage[prog.out] = k
    return stage


def transpile(code: str, *, top: str, xs: np.ndarray | None = None, entry: str = "design",
              note: str = "", pipeline: int | None = None) -> str:
    """The prototype `code` (a `design(x)` in the integer subset) as a SystemVerilog module
    named `top`, every width proven by tracing over `xs` (all 65536 FP16 patterns by default).
    Combinational unless the prototype declares `PIPELINE = K` (or `pipeline=K` is given):
    then the measured program is cut into K+1 stages by logic level and every signal that
    crosses a cut is registered -- latency K, one input per cycle (D496). Raises
    `TranspileError` naming what it cannot spell."""
    global _DELAY_BASES
    prog, xs = _program(code, xs, entry)
    k = pipeline_stages(code, xs=xs, entry=entry) if pipeline is None else int(pipeline)
    stage = _stage_of(prog, k)
    stage["x"] = 0
    # every consumer in a later stage than its producer reads a delayed copy
    delays: dict[str, int] = {}                          # signal -> max delay demanded
    reads: dict[str, dict[str, str]] = {}                # temp -> {name: delayed name}
    for t in prog.temps:
        sub: dict[str, str] = {}
        for n in {n.id for n in ast.walk(t.expr) if isinstance(n, ast.Name)}:
            if n not in stage:
                continue
            d = stage[t.name] - stage[n]
            if d > 0:
                delays[n] = max(delays.get(n, 0), d)
                sub[n] = f"{n}_d{d}"
        if sub:
            reads[t.name] = sub
    _DELAY_BASES = set(delays)
    try:
        em = _Emitter(prog)
        out = prog.out
        body = []
        for t in prog.temps:
            if t.name in reads:
                t = _Temp(name=t.name, expr=_rename(t.expr, reads[t.name]), line=t.line,
                          lo=t.lo, hi=t.hi, is_bool=t.is_bool)
            body.append(em.emit(t))
        decls = [f"  logic {'signed ' if t.signed else ''}[{t.width - 1}:0] {t.name};" for t in prog.temps]
        regs: list[str] = []
        for n, d in sorted(delays.items()):
            w, sg = em.sig(n)
            decls += [f"  logic {'signed ' if sg else ''}[{w - 1}:0] {n}_d{i};" for i in range(1, d + 1)]
            regs += [f"    {n}_d{i} <= {n if i == 1 else f'{n}_d{i - 1}'};" for i in range(1, d + 1)]
        roms = [em.rom(n, w) for n, w in prog.tables.items()]
        head = (f"module {top} (input logic clk, input logic [15:0] x, output logic [15:0] y);\n"
                f"  // TRANSPILED from the prototype (D478): one operation per signal, every width\n"
                f"  // measured over all {len(xs)} inputs{(' -- ' + note) if note else ''}.\n"
                + (f"  // PIPELINED into {k + 1} stages by logic level, latency {k} (D496).\n" if k else ""))
        return (head + "\n".join(decls) + "\n" + "\n".join(body)
                + (("\n  always_ff @(posedge clk) begin\n" + "\n".join(regs) + "\n  end") if regs else "")
                + f"\n  assign y = 16'({out});\n"
                + ("\n" + "\n\n".join(roms) + "\n" if roms else "")
                + "endmodule\n")
    finally:
        _DELAY_BASES = set()


def _rename(e: ast.expr, sub: dict[str, str]) -> ast.expr:
    """`e` with the names in `sub` replaced (a copy)."""
    class R(ast.NodeTransformer):
        def visit_Name(self, node):
            return ast.Name(id=sub.get(node.id, node.id), ctx=node.ctx)
    return R().visit(copy.deepcopy(e))


def transpile_to_module(code: str, op: str, **kw: Any) -> str:
    return transpile(code, top=f"nlu_{op}", **kw)
