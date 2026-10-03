"""PY2SV (D611): a verified `python` prototype spelled as SystemVerilog by the loop, not the model.

The prototype is the design; spelling it is mechanical (D478), so the loop does it:

1. COMPILE `design()` into straight-line operations: every branch is computed and merged with a
   multiplexer on its path condition (if-conversion), an early `return` sets a done flag,
   `for` over a constant range is unrolled, helper functions are inlined, a module-level
   table read is a call to the loop's `<table>_rom` function (D606).
2. EVALUATE those operations on every input the check ran (the exhaustive domain, D606), with
   two purposes: each value's range is measured -- on the inputs where its path is taken, since
   a value computed on an untaken branch is multiplexed away -- and the outputs are compared
   with `design()`'s own. A disagreement is a compiler limit, and the spelling is refused.
3. EMIT one signed signal per operation, each exactly as wide as its measured range, so the
   hardware computes what Python computed, bit for bit, on every input measured.

A construct outside the subset (a `while`, a float, a division of a value that can be negative
by a constant that is not a power of two, ...) raises `Unsupported` with the reason, and the
model transcribes instead, as before.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["Unsupported", "spell"]


class Unsupported(ValueError):
    """The prototype uses something the spelling does not cover; the model transcribes."""


@dataclass
class Node:
    op: str
    args: tuple = ()
    value: Any = None          # const value, input name, table name
    guard: int = -1            # the node whose truth says this value is live (-1: always)
    lo: int | None = None
    hi: int | None = None


@dataclass
class _Frame:
    """A function being compiled: its done flag and what it returned."""
    done: int
    ret: dict[str, int] = field(default_factory=dict)


class _Compiler:
    def __init__(self, consts: dict[str, int], tables: dict[str, list[int]], funcs: dict[str, ast.FunctionDef]):
        self.nodes: list[Node] = []
        self.consts, self.tables, self.funcs = consts, tables, funcs
        self._const: dict[int, int] = {}
        self.zero, self.one = self.const(0), self.const(1)

    # ---- nodes ---------------------------------------------------------------------------
    def const(self, v: int) -> int:
        v = int(v)
        if v not in self._const:
            self.nodes.append(Node("const", value=v))
            self._const[v] = len(self.nodes) - 1
        return self._const[v]

    def is_const(self, n: int) -> bool:
        return self.nodes[n].op == "const"

    def op(self, kind: str, *args: int, guard: int = -1, value: Any = None) -> int:
        if any(isinstance(a, list) for a in args):
            raise Unsupported(f"a tuple of values used as one value (in `{kind}`): take its parts first")
        if kind in _FOLD and all(self.is_const(a) for a in args):
            try:
                return self.const(_FOLD[kind](*[self.nodes[a].value for a in args]))
            except Exception:  # noqa: BLE001 -- not foldable (a negative shift): keep the node
                pass
        if kind == "mux":
            c, a, b = args
            if self.is_const(c):
                return a if self.nodes[c].value else b
            if a == b:
                return a
        if kind == "land":
            if any(self.is_const(a) and not self.nodes[a].value for a in args):
                return self.zero
            rest = [a for a in args if not self.is_const(a)]
            if not rest:
                return self.one
            if len(rest) == 1 and self.nodes[rest[0]].op in _BOOLEAN:
                return rest[0]
            args = tuple(rest)
        self.nodes.append(Node(kind, tuple(args), value, guard))
        return len(self.nodes) - 1

    def truth(self, n: int, g: int) -> int:
        if isinstance(n, list):
            raise Unsupported("a tuple of values used as a condition")
        return n if self.nodes[n].op in _BOOLEAN else self.op("truth", n, guard=g)

    def both(self, a: int, b: int, g: int) -> int:
        return self.op("land", a, b, guard=g)

    def negate(self, a: int, g: int) -> int:
        return self.op("not", a, guard=g)

    # ---- statements ----------------------------------------------------------------------
    def body(self, stmts: list[ast.stmt], guard: int, env: dict[str, int], frame: _Frame) -> None:
        for st in stmts:
            live = self.both(guard, self.negate(frame.done, guard), guard) if not (self.is_const(frame.done) and self.nodes[frame.done].value == 0) else guard
            if self.is_const(live) and not self.nodes[live].value:
                return
            self.stmt(st, live, env, frame)

    rows: dict[str, int] = {}                       # a table of rows: its name -> its row length (D618)

    def assign(self, env: dict[str, Any], name: str, value: Any, g: int) -> None:
        if name in env and not (self.is_const(g) and self.nodes[g].value):
            old = env[name]
            if isinstance(value, list) or isinstance(old, list):
                if not (isinstance(value, list) and isinstance(old, list) and len(value) == len(old)):
                    raise Unsupported(f"`{name}` is a row in one branch and not in another")
                env[name] = [self.op("mux", g, v, o, guard=-1) for v, o in zip(value, old)]
            else:
                env[name] = self.op("mux", g, value, old, guard=-1)
        else:
            env[name] = value

    def stmt(self, st: ast.stmt, g: int, env: dict[str, int], frame: _Frame) -> None:
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            return                                          # a docstring
        if isinstance(st, ast.Pass):
            return
        if isinstance(st, ast.Assign) and all(isinstance(t, ast.Name) for t in st.targets):
            val = self.expr(st.value, g, env)               # `a = b = 0` too
            for t in st.targets:
                self.assign(env, t.id, val, g)
            return
        if (isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Tuple)
                and all(isinstance(t, ast.Name) for t in st.targets[0].elts)):
            # `g, r, s = 0, 0, 0`, `a, b = b, a` and `s, m, k = unpack(x)` (D804): every value
            # first, then every name
            vals, names = self.expr(st.value, g, env), st.targets[0].elts
            if not isinstance(vals, list) or len(vals) != len(names):
                raise Unsupported(f"line {st.lineno}: {len(names)} names from a value that is not {len(names)} values")
            for t, v in zip(names, vals):
                self.assign(env, t.id, v, g)
            return
        if isinstance(st, ast.AugAssign) and isinstance(st.target, ast.Name):
            cur = self.expr(st.target, g, env)
            self.assign(env, st.target.id, self.binop(st.op, cur, self.expr(st.value, g, env), g), g)
            return
        if isinstance(st, ast.If):
            c = self.truth(self.expr(st.test, g, env), g)
            self.body(st.body, self.both(g, c, g), env, frame)
            self.body(st.orelse, self.both(g, self.negate(c, g), g), env, frame)
            return
        if isinstance(st, ast.For) and isinstance(st.target, ast.Name) and not st.orelse:
            it = st.iter
            if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range"):
                raise Unsupported(f"line {st.lineno}: `for` only over `range(...)`")
            bounds = [self.expr(a, g, env) for a in it.args]
            if not all(self.is_const(b) for b in bounds):
                raise Unsupported(f"line {st.lineno}: a `for` range must be constant")
            for k in range(*[self.nodes[b].value for b in bounds]):
                env[st.target.id] = self.const(k)
                self.body(st.body, g, env, frame)
            return
        if isinstance(st, ast.Return):
            if st.value is None:
                raise Unsupported(f"line {st.lineno}: a bare `return`")
            if isinstance(st.value, ast.Dict):
                for k, v in zip(st.value.keys, st.value.values):
                    if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                        raise Unsupported(f"line {st.lineno}: a returned dict needs literal keys")
                    val = self.expr(v, g, env)
                    frame.ret[k.value] = self.op("mux", g, val, frame.ret[k.value], guard=-1) if k.value in frame.ret else val
            else:
                val = self.expr(st.value, g, env)
                if "" in frame.ret:                         # a later return: its value where it is taken
                    old = frame.ret[""]
                    if isinstance(val, list) != isinstance(old, list) or (isinstance(val, list) and len(val) != len(old)):
                        raise Unsupported(f"line {st.lineno}: the returns give different numbers of values")
                    val = ([self.op("mux", g, v, o, guard=-1) for v, o in zip(val, old)] if isinstance(val, list)
                           else self.op("mux", g, val, old, guard=-1))
                frame.ret[""] = val
            frame.done = self.op("lor", frame.done, g, guard=-1)
            return
        raise Unsupported(f"line {getattr(st, 'lineno', '?')}: `{type(st).__name__}` is not spelled "
                          "(assignments, if/elif/else, for over a constant range, return)")

    # ---- expressions ---------------------------------------------------------------------
    def binop(self, op: ast.operator, a: int, b: int, g: int) -> int:
        simple = {ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.BitAnd: "and", ast.BitOr: "or",
                  ast.BitXor: "xor", ast.LShift: "shl", ast.RShift: "shr"}
        if type(op) in simple:
            return self.op(simple[type(op)], a, b, guard=g)
        if isinstance(op, (ast.FloorDiv, ast.Mod)):
            if not self.is_const(b) or self.nodes[b].value <= 0:
                raise Unsupported("`//` and `%` only by a positive constant")
            c = self.nodes[b].value
            if c & (c - 1) == 0:                            # a power of two: a shift or a mask, any sign
                k = c.bit_length() - 1
                return self.op("shr", a, self.const(k), guard=g) if isinstance(op, ast.FloorDiv) \
                    else self.op("and", a, self.const(c - 1), guard=g)
            return self.op("div" if isinstance(op, ast.FloorDiv) else "mod", a, b, guard=g)
        if isinstance(op, ast.Pow) and self.is_const(b) and 0 <= self.nodes[b].value <= 4:
            out = self.one
            for _ in range(self.nodes[b].value):
                out = self.op("mul", out, a, guard=g)
            return out
        raise Unsupported(f"the operator `{type(op).__name__}` is not spelled")

    def expr(self, e: ast.expr, g: int, env: dict[str, int]) -> int:
        if isinstance(e, ast.Constant):
            if isinstance(e.value, bool) or isinstance(e.value, int):
                return self.const(int(e.value))
            raise Unsupported(f"the constant {e.value!r} is not an integer")
        if isinstance(e, ast.Name):
            if e.id in env:
                return env[e.id]
            if e.id in self.consts:
                return self.const(self.consts[e.id])
            raise Unsupported(f"`{e.id}` is not an integer the spelling knows (a local, an argument, a module-level int)")
        if isinstance(e, ast.BinOp):
            return self.binop(e.op, self.expr(e.left, g, env), self.expr(e.right, g, env), g)
        if isinstance(e, ast.UnaryOp):
            a = self.expr(e.operand, g, env)
            if isinstance(e.op, ast.USub):
                return self.op("sub", self.zero, a, guard=g)
            if isinstance(e.op, ast.UAdd):
                return a
            if isinstance(e.op, ast.Invert):
                return self.op("inv", a, guard=g)
            if isinstance(e.op, ast.Not):
                return self.negate(a, g)
        if isinstance(e, ast.Compare):
            names = {ast.Lt: "lt", ast.LtE: "le", ast.Gt: "gt", ast.GtE: "ge", ast.Eq: "eq", ast.NotEq: "ne"}
            left, out = self.expr(e.left, g, env), self.one
            for op, right_e in zip(e.ops, e.comparators):
                if type(op) not in names:
                    raise Unsupported(f"the comparison `{type(op).__name__}` is not spelled")
                right = self.expr(right_e, g, env)
                out = self.both(out, self.op(names[type(op)], left, right, guard=g), g)
                left = right
            return out
        if isinstance(e, ast.BoolOp):
            vals = [self.truth(self.expr(v, g, env), g) for v in e.values]
            out = vals[0]
            for v in vals[1:]:
                out = self.both(out, v, g) if isinstance(e.op, ast.And) else self.op("lor", out, v, guard=g)
            return out
        if isinstance(e, ast.IfExp):
            c = self.truth(self.expr(e.test, g, env), g)
            a = self.expr(e.body, self.both(g, c, g), env)
            b = self.expr(e.orelse, self.both(g, self.negate(c, g), g), env)
            if isinstance(a, list) or isinstance(b, list):          # D806: `p, q = (a, 1) if c else (1, a)`
                if not (isinstance(a, list) and isinstance(b, list) and len(a) == len(b)):
                    raise Unsupported(f"line {e.lineno}: the two sides give different numbers of values")
                return [self.op("mux", c, x, y, guard=g) for x, y in zip(a, b)]
            return self.op("mux", c, a, b, guard=g)
        if isinstance(e, (ast.Tuple, ast.List)):              # D804: a tuple is a row of values, as a table's row is
            vals = [self.expr(x, g, env) for x in e.elts]
            if any(isinstance(v, list) for v in vals):
                raise Unsupported(f"line {e.lineno}: a tuple of tuples is not spelled")
            return vals
        local = isinstance(e, ast.Subscript) and isinstance(e.value, ast.Name) and e.value.id in env
        if isinstance(e, ast.Subscript) and isinstance(e.value, ast.Name) and e.value.id in self.tables and not local:
            return self.op("table", self.expr(e.slice, g, env), guard=g, value=e.value.id)
        if isinstance(e, ast.Subscript) and isinstance(e.value, ast.Name) and e.value.id in self.rows and not local:
            # a row of a table of rows: one lookup per column, at the same index (D618)
            i = self.expr(e.slice, g, env)
            return [self.op("table", i, guard=g, value=column_name(e.value.id, j)) for j in range(self.rows[e.value.id])]
        if isinstance(e, ast.Subscript):
            row = self.expr(e.value, g, env)
            k = self.expr(e.slice, g, env)
            if isinstance(row, list) and self.is_const(k) and -len(row) <= self.nodes[k].value < len(row):
                return row[self.nodes[k].value]
            if isinstance(row, list) and row and not self.is_const(k):
                # D806: a tuple at a computed position, a multiplexer over its values (a negative
                # position is not modelled: the measurement compares with design() and refuses it)
                out = row[0]
                for i in range(1, len(row)):
                    out = self.op("mux", self.op("eq", k, self.const(i), guard=g), row[i], out, guard=g)
                return out
            raise Unsupported(f"line {getattr(e, 'lineno', '?')}: only a table or a tuple may be indexed"
                              + (" (this position is outside it)" if isinstance(row, list) else ""))
        if isinstance(e, ast.Call):
            return self.call(e, g, env)
        raise Unsupported(f"line {getattr(e, 'lineno', '?')}: `{type(e).__name__}` is not spelled")

    def call(self, e: ast.Call, g: int, env: dict[str, int]) -> int:
        if isinstance(e.func, ast.Attribute) and e.func.attr == "bit_length" and not e.args:
            return self.op("bitlen", self.expr(e.func.value, g, env), guard=g)
        if not isinstance(e.func, ast.Name) or e.keywords:
            raise Unsupported(f"line {e.lineno}: the call is not spelled")
        if (e.func.id == "len" and len(e.args) == 1 and isinstance(e.args[0], ast.Name)
                and e.args[0].id not in env and (e.args[0].id in self.tables or e.args[0].id in self.rows)):
            t = e.args[0].id                                # D806: a module table's length, a constant
            return self.const(len(self.tables[t] if t in self.tables else self.tables[column_name(t, 0)]))
        name, args = e.func.id, [self.expr(a, g, env) for a in e.args]
        if name == "len" and len(args) == 1 and isinstance(args[0], list):
            return self.const(len(args[0]))
        if name in ("int",) and len(args) == 1:
            return args[0]
        if name == "bool" and len(args) == 1:
            return self.truth(args[0], g)
        if name == "abs" and len(args) == 1:
            neg = self.op("lt", args[0], self.zero, guard=g)
            return self.op("mux", neg, self.op("sub", self.zero, args[0], guard=g), args[0], guard=g)
        if name in ("min", "max") and len(args) >= 2:
            out = args[0]
            for a in args[1:]:
                better = self.op("lt" if name == "min" else "gt", a, out, guard=g)
                out = self.op("mux", better, a, out, guard=g)
            return out
        if name in self.funcs:
            fn = self.funcs[name]
            params = [a.arg for a in fn.args.args]
            if len(params) != len(args):
                raise Unsupported(f"`{name}` called with {len(args)} argument(s), defined with {len(params)}")
            frame = _Frame(done=self.zero)
            self.body(fn.body, g, dict(zip(params, args)), frame)
            if "" not in frame.ret:
                raise Unsupported(f"`{name}` returns nothing the spelling can read")
            return frame.ret[""]
        raise Unsupported(f"line {e.lineno}: `{name}(...)` is not spelled")


_BOOLEAN = frozenset({"lt", "le", "gt", "ge", "eq", "ne", "truth", "not", "land", "lor"})
_FOLD: dict[str, Callable[..., int]] = {
    "add": lambda a, b: a + b, "sub": lambda a, b: a - b, "mul": lambda a, b: a * b,
    "and": lambda a, b: a & b, "or": lambda a, b: a | b, "xor": lambda a, b: a ^ b, "inv": lambda a: ~a,
    "shl": lambda a, b: a << b, "shr": lambda a, b: a >> b,
    "lt": lambda a, b: int(a < b), "le": lambda a, b: int(a <= b), "gt": lambda a, b: int(a > b),
    "ge": lambda a, b: int(a >= b), "eq": lambda a, b: int(a == b), "ne": lambda a, b: int(a != b),
    "truth": lambda a: int(a != 0), "not": lambda a: int(a == 0),
    "land": lambda *a: int(all(a)), "lor": lambda a, b: int(bool(a) or bool(b)),
}


# ---- evaluation: ranges, and the self-check ---------------------------------------------------
def row_columns(v: Any) -> list[list[int]] | None:
    """A module-level table of rows (`[(c0, c1, c2, c3), ...]`, coefficients per segment) as its
    columns, one integer table each; None otherwise."""
    try:
        rows = [[int(x) for x in r] for r in v]
    except Exception:  # noqa: BLE001
        return None
    if len(rows) < 2 or not rows[0] or any(len(r) != len(rows[0]) for r in rows):
        return None
    return [list(col) for col in zip(*rows)]


def column_name(table: str, j: int) -> str:
    """The name of column `j` of a table of rows, as a table and a SystemVerilog function."""
    return f"{table}__c{j}"


def _run(nodes: list[Node], tables: dict[str, list[int]], inputs: dict[str, int]) -> list[int]:
    """Every node's value on one input, Python's semantics. A node off its path (its guard
    false) is 0 -- in hardware it computes garbage the multiplexers never select, and keeping it
    out keeps the measured ranges, so the widths, tight; a value that would raise is 0 too."""
    vals: list[int] = [0] * len(nodes)
    for i, n in enumerate(nodes):
        if n.guard >= 0 and not vals[n.guard]:
            continue                      # off its path: 0 in the model, multiplexed away in hardware
        a = [vals[x] for x in n.args]
        op = n.op
        try:
            if op == "const":
                v = n.value
            elif op == "input":
                v = inputs[n.value]
            elif op == "mux":
                v = a[1] if a[0] else a[2]
            elif op == "div":
                v = a[0] // a[1]
            elif op == "mod":
                v = a[0] % a[1]
            elif op == "table":
                t = tables[n.value]
                v = t[a[0]] if 0 <= a[0] < len(t) else 0
            elif op == "bitlen":
                v = a[0].bit_length()
            elif op == "shl":
                v = a[0] << a[1] if 0 <= a[1] <= 1024 else 0
            elif op == "shr":
                v = a[0] >> a[1] if a[1] >= 0 else 0
            else:
                v = _FOLD[op](*a)
        except (ValueError, ZeroDivisionError, IndexError, OverflowError):
            v = 0
        vals[i] = v
    return vals


def _measure(nodes: list[Node], tables: dict[str, list[int]], rows: list[dict[str, Any]],
             outs: dict[str, int], design: Callable[..., Any]) -> None:
    """Ranges on the live inputs; the outputs checked against `design()` on every row."""
    for r in rows:
        vals = _run(nodes, tables, r)
        for i, n in enumerate(nodes):
            if n.op == "const" or (n.guard >= 0 and not vals[n.guard]):
                continue
            v = vals[i]
            n.lo = v if n.lo is None or v < n.lo else n.lo
            n.hi = v if n.hi is None or v > n.hi else n.hi
        want = design(**r)
        for name, node in outs.items():
            if int(want[name]) != vals[node]:
                raise Unsupported(f"the spelling disagrees with design() at {r}: {name}={vals[node]} "
                                  f"against {int(want[name])} (a construct the compiler reads differently)")


# ---- emission ---------------------------------------------------------------------------------
def _width(lo: int | None, hi: int | None) -> int:
    if lo is None:
        return 2
    return max(2, max((-lo - 1).bit_length() if lo < 0 else 0, hi.bit_length()) + 1)


def _lit(v: int) -> str:
    w = abs(v).bit_length() + 1
    return f"{w}'sd{v}" if v >= 0 else f"-{w}'sd{-v}"


def _compile(code: str, ports: list[dict[str, Any]]) -> tuple["_Compiler", dict[str, int], Callable[..., Any], dict[str, list[int]]]:
    """The prototype's `design()` compiled to operations: (compiler, output nodes, design, tables)."""
    ns: dict[str, Any] = {"__name__": "prototype"}
    exec(compile(code, "prototype.py", "exec"), ns)      # the prototype already ran in the check
    design = ns.get("design")
    if not callable(design):
        raise Unsupported("no design()")
    tree = ast.parse(code)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    consts = {k: int(v) for k, v in ns.items() if isinstance(v, (int, bool)) and not k.startswith("__")}
    tables, rows = {}, {}
    for k, v in ns.items():
        if k.startswith("__") or callable(v) or isinstance(v, (str, bytes, dict, int, float)):
            continue
        try:
            vals = [int(x) for x in v]
        except Exception:  # noqa: BLE001
            cols = row_columns(v)
            if cols:
                rows[k] = len(cols)
                tables.update({column_name(k, j): col for j, col in enumerate(cols)})
            continue
        if len(vals) >= 2:
            tables[k] = vals
    fn = funcs["design"]
    ins = [p for p in ports if p["dir"] == "in"]
    outs_p = [p for p in ports if p["dir"] == "out"]
    params = [a.arg for a in fn.args.args]
    if sorted(params) != sorted(p["name"] for p in ins):
        raise Unsupported(f"design({', '.join(params)}) does not take the golden's inputs ({', '.join(p['name'] for p in ins)})")
    c = _Compiler(consts, tables, funcs)
    c.rows = rows
    env = {}
    for p in ins:
        c.nodes.append(Node("input", value=p["name"]))
        env[p["name"]] = len(c.nodes) - 1
    frame = _Frame(done=c.zero)
    c.body(fn.body, c.one, env, frame)
    missing = [p["name"] for p in outs_p if p["name"] not in frame.ret]
    if missing:
        raise Unsupported(f"design() never returns {', '.join(missing)}")
    outs = {p["name"]: frame.ret[p["name"]] for p in outs_p}
    return c, outs, design, tables


def spell(code: str, ports: list[dict[str, Any]], rows: list[dict[str, Any]], module: str,
          table_functions: Callable[[str], tuple[str, list[str]]]) -> str:
    """The prototype as a SystemVerilog module, or `Unsupported` with the reason. `rows` are
    the inputs to measure on (they must be the whole domain for the widths to hold)."""
    c, outs, design, tables = _compile(code, ports)
    ins = [p for p in ports if p["dir"] == "in"]
    outs_p = [p for p in ports if p["dir"] == "out"]
    rows_in = [r["inputs"] for r in rows]
    _measure(c.nodes, tables, rows_in, outs, design)
    # the table functions the prototype's tables need, the bit-length helpers, the signals
    used_tables = {n.value for n in c.nodes if n.op == "table"}
    fns, _lines = table_functions(code) if used_tables else ("", [])
    decl, body, helpers = [], [], {}
    name: dict[int, str] = {}
    widths: dict[int, int] = {}
    for i, n in enumerate(c.nodes):
        if n.op == "const":
            name[i] = _lit(n.value)
            widths[i] = abs(n.value).bit_length() + 1
            continue
        if n.op == "input":
            name[i] = f"n{i}"
            p = next(p for p in ins if p["name"] == n.value)
            w = int(p["bits"]) + (1 if p.get("unsigned") else 0)
            widths[i] = w
            decl.append(f"  logic signed [{w - 1}:0] n{i};")
            body.append(f"  assign n{i} = " + (f"$signed({{1'b0, {p['name']}}})" if p.get("unsigned") else p["name"]) + ";")
            continue
        w = _width(n.lo, n.hi)
        widths[i] = w
        a = [name[x] for x in n.args]
        e = _emit(n, a, c, helpers, w, [widths[x] for x in n.args])
        name[i] = f"n{i}"
        decl.append(f"  logic signed [{w - 1}:0] n{i};")
        body.append(f"  assign n{i} = {e};")
    for p in outs_p:
        body.append(f"  assign {p['name']} = {int(p['bits'])}'({name[outs[p['name']]]});")
    port_list = ",\n".join(
        [f"  input logic {'' if p.get('unsigned') else 'signed '}[{int(p['bits']) - 1}:0] {p['name']}" for p in ins]
        + [f"  output logic {'' if p.get('unsigned') else 'signed '}[{int(p['bits']) - 1}:0] {p['name']}" for p in outs_p])
    header = (f"// {module}: spelled by the loop from the verified prototype (flux py2sv, D611); every signal is\n"
              f"// as wide as its range measured over {len(rows_in)} inputs, where its path is taken.\n")
    parts = [header + f"module {module} (\n{port_list}\n);"]
    if fns:
        parts.append(fns)
    parts += list(helpers.values())
    parts += decl + body + ["endmodule"]
    return "\n".join(parts) + "\n"


def _emit(n: Node, a: list[str], c: _Compiler, helpers: dict[str, str], w: int, aw: list[int]) -> str:
    """One node's expression, every operand cast to the width it is computed at -- Verilator's
    -Wall makes an implicit widening fatal, and a size cast keeps a signed value's sign."""
    op = n.op

    def at(x: str, width: int) -> str:
        return f"{width}'({x})"

    binary = {"add": "+", "sub": "-", "mul": "*", "and": "&", "or": "|", "xor": "^"}
    if op in binary:
        return f"{at(a[0], w)} {binary[op]} {at(a[1], w)}"
    if op == "shl":                       # the low bits of a << b depend on a's low bits only
        return f"{at(a[0], w)} <<< {a[1]}"
    if op == "shr":                       # ... but a >> b brings a's HIGH bits down: shift at full width
        k = max(w, aw[0])
        return at(f"{at(a[0], k)} >>> {a[1]}", w)
    if op == "inv":
        return f"~{at(a[0], w)}"
    cmp_ = {"lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "==", "ne": "!="}
    def bit(x: str) -> str:             # a 1-bit result zero-extended to the node's width
        return "{" + f"{w - 1}'d0, " + x + "}"

    if op in cmp_:
        k = max(aw)
        return bit(f"({at(a[0], k)} {cmp_[op]} {at(a[1], k)})")
    if op == "truth":
        return bit(f"({a[0]} != 0)")
    if op == "not":
        return bit(f"({a[0]} == 0)")
    if op == "land":
        return bit("(" + " && ".join(f"({x} != 0)" for x in a) + ")")
    if op == "lor":
        return bit(f"(({a[0]} != 0) || ({a[1]} != 0))")
    if op == "mux":
        return f"({a[0]} != 0) ? {at(a[1], w)} : {at(a[2], w)}"
    if op in ("div", "mod"):
        lhs = c.nodes[n.args[0]]
        if lhs.lo is not None and lhs.lo < 0:
            raise Unsupported("`//` or `%` of a value that can be negative, by a constant that is not a power of two")
        k = max(w, *aw)
        return at(f"{at(a[0], k)} {'/' if op == 'div' else '%'} {at(a[1], k)}", w)
    if op == "table":
        size = len(c.tables[n.value])
        idx = max(1, (size - 1).bit_length())
        signed = min(c.tables[n.value]) < 0
        call = f"{n.value}_rom({idx}'({a[0]}))"
        return at(call if signed else f"$signed({{1'b0, {call}}})", w)
    if op == "bitlen":
        src = c.nodes[n.args[0]]
        if src.lo is not None and src.lo < 0:
            raise Unsupported("`.bit_length()` of a value that can be negative")
        sw = _width(src.lo, src.hi)
        fn = f"flux_bitlen_{sw}"
        helpers.setdefault(fn, f"  function automatic logic [15:0] {fn}(input logic [{sw - 1}:0] v);\n"
                               f"    {fn} = 0;\n    for (int i = 0; i < {sw}; i++) if (v[i]) {fn} = 16'(i + 1);\n"
                               f"  endfunction")
        return at(f"$signed({{1'b0, {fn}({sw}'({a[0]}))}})", w)
    raise Unsupported(f"no spelling for `{op}`")


def module_name(statement: str, contract: str, fallback: str) -> str:
    """The module the document names (`module \\`gelu_fp16\\``), else the task's id."""
    m = re.search(r"module\s+`(\w+)`", f"{statement}\n{contract}")
    return m.group(1) if m else fallback


# ---- what a prototype costs in hardware, before any synthesis (D613) ----------------------------
def _cost_of(c: _Compiler, n: Node, i: int) -> tuple[float, str]:
    """A gate-count proxy for one operation, from the measured widths: what makes one prototype
    cheaper than another, in seconds instead of a synthesis run. Calibrated on ASAP7 (D619):
    one unit is about 1/3 um2."""
    w = _width(n.lo, n.hi)
    aw = [(_width(c.nodes[a].lo, c.nodes[a].hi) if c.nodes[a].op != "const" else abs(c.nodes[a].value).bit_length() + 1)
          for a in n.args]
    const_b = len(n.args) > 1 and c.nodes[n.args[1]].op == "const"
    if n.op == "mul":
        if any(c.nodes[a].op == "const" for a in n.args):
            return float(min(aw) * max(aw)) / 2, f"a {aw[0]}x{aw[1]}-bit multiply by a constant"
        return float(aw[0] * aw[1]), f"a {aw[0]}x{aw[1]}-bit multiplier"
    if n.op in ("div", "mod"):
        return 0.35 * aw[0] * aw[1], f"a {aw[0]}-bit {'divider' if n.op == 'div' else 'modulo'} by a {aw[1]}-bit constant"
    if n.op in ("shl", "shr"):
        if const_b:
            return 0.0, ""
        return float(max(w, aw[0]) * max(1, max(w, aw[0]).bit_length())), f"a {max(w, aw[0])}-bit variable shift"
    if n.op == "table":
        t = c.tables[n.value]
        tw = max((-min(t) - 1).bit_length() if min(t) < 0 else 0, max(t).bit_length()) + (1 if min(t) < 0 else 0)
        return float(len(t) * tw) / 20, f"the table `{n.value}` ({len(t)} x {tw} bits)"
    if n.op in ("add", "sub", "lt", "le", "gt", "ge", "eq", "ne", "bitlen"):
        return float(max([w, *aw])), ""
    if n.op in ("and", "or", "xor", "inv", "mux"):
        return float(w) / 2, ""
    return 0.0, ""


def cost(code: str, ports: list[dict[str, Any]], rows: list[dict[str, Any]]) -> tuple[float, str]:
    """(the prototype's hardware cost, what it is made of): compiled and measured as `spell`
    does, without emitting. `Unsupported` when it cannot be read."""
    c, outs, design, tables = _compile(code, ports)
    _measure(c.nodes, tables, [r["inputs"] for r in rows], outs, design)
    total, parts = 0.0, {}
    widest = max((_width(n.lo, n.hi) for n in c.nodes if n.op not in ("const", "input")), default=0)
    for i, n in enumerate(c.nodes):
        if n.op in ("const", "input"):
            continue
        v, what = _cost_of(c, n, i)
        total += v
        if what:
            parts[what] = parts.get(what, 0.0) + v
    top = sorted(parts.items(), key=lambda kv: -kv[1])[:6]
    signals = sum(1 for n in c.nodes if n.op not in ("const", "input"))
    return total, (f"cost {total:,.0f} ({signals} signals, the widest {widest} bits); the largest parts: "
                   + "; ".join(f"{k} ({v:,.0f})" for k, v in top))
