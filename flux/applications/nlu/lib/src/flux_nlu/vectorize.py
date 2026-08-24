"""Per-element Python -> array Python (docs/decisions.md D483).

Every model, thinking or not, writes an operator the way a person does: for ONE input --
`if is_nan(x): return QNAN`, `if e < -14: v = v >> (-14 - e)`, `min(30, max(0, e))`. The
harness runs design() on all 65,536 inputs as one array, so that text crashes ("truth value
of an array is ambiguous") or is refused, and the attempt is spent on the notation instead of
the algorithm. This pass takes the notation over -- if-conversion, the compiler's name for it:

- `if c: A else: B` on data becomes both branches evaluated on every element and each
  variable they assign merged with `np.where(c, a, b)`;
- an early `return v` under a condition becomes a pending result: the function's single
  return folds them, earliest first, `np.where(p1, v1, np.where(p2, v2, final))`;
- `and`/`or`/`not` become `&`/`|`/`~`; `a if c else b` becomes np.where; `min`/`max`/`abs`
  become np.minimum/np.maximum/np.abs; `int(v)` is v; a chained comparison is an `&` of pairs;
- a table indexed inside a converted branch is indexed with a clipped index, because the
  branch now runs on elements that never took it (numpy's shifts and divisions are safe on
  those; an index out of range is not);
- a `while` on data is unrolled as WHILE_STEPS (24) predicated iterations -- a normalisation
  loop over 16 bits ends within them; an element still looping after that keeps its value.

`vectorize(code)` returns the array-form text and a line map back to the model's lines, so
every refusal and traceback still names the line the model wrote. The model's text stays the
prototype it edits; the sandbox, the rules and the transpiler see the array form.
"""

from __future__ import annotations

import ast
import copy

__all__ = ["VectorizeError", "vectorize", "relocate_lines"]

_TABLE_CALLS = {"lookup": 1, "interp1": 2}       # block name -> position of the index argument
WHILE_STEPS = 24                                 # a data `while` is unrolled this many times


class VectorizeError(ValueError):
    """The text cannot be turned into array form; the message names the line."""


def vectorize(code: str) -> tuple[str, dict[int, int]]:
    """(array-form source, {array-form line: model line}). Text without per-element control
    flow comes back unchanged (an identity map)."""
    tree = ast.parse(code)
    module_names = {t.id for n in tree.body if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name)}
    changed = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and _needs_conversion(node):
            _Converter(node, module_names).run()
            changed = True
    if not changed:
        return code, {}
    ast.fix_missing_locations(tree)
    text = ast.unparse(tree)
    # the line map: statements of the re-parsed text, in order, against the converted tree's
    # statements, which carry the model's line numbers
    fresh = ast.parse(text)
    linemap: dict[int, int] = {}
    pairs = list(zip(_statements(fresh), _statements(tree)))
    pairs.sort(key=lambda p: -((p[0].end_lineno or p[0].lineno) - p[0].lineno))   # outer first
    for new, old in pairs:                                                        # inner wins
        src = getattr(old, "_src_line", None) or getattr(old, "lineno", None)
        if src is not None:
            for ln in range(new.lineno, (new.end_lineno or new.lineno) + 1):
                linemap[ln] = int(src)
    return text, linemap


def relocate_lines(text: str, linemap: dict[int, int]) -> str:
    """Line numbers in a message about the array form made the model's line numbers."""
    import re

    if not linemap:
        return text
    return re.sub(r"\bline (\d+)", lambda m: f"line {linemap.get(int(m.group(1)), int(m.group(1)))}", text)


def _statements(tree: ast.AST) -> list[ast.stmt]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.stmt)]


def _needs_conversion(fn: ast.FunctionDef) -> bool:
    """Any `if`/`if-expression`/`and`/`or`/`not`/`min`/`max`/`abs`/`int` on a value that is
    not a module-level constant -- the notation of per-element code. Constant `if`s (on knobs)
    are left to the transpiler, which evaluates them."""
    params = {a.arg for a in fn.args.args}
    local = set(params)
    for n in ast.walk(fn):
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                local |= {m.id for m in ast.walk(t) if isinstance(m, ast.Name)}
    def depends(e: ast.AST) -> bool:
        return any(isinstance(m, ast.Name) and m.id in local for m in ast.walk(e))
    for n in ast.walk(fn):
        if isinstance(n, (ast.If, ast.While)) and depends(n.test):
            return True
        if isinstance(n, ast.IfExp) and depends(n.test):
            return True
        if isinstance(n, ast.BoolOp) and depends(n):
            return True
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not) and depends(n.operand):
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id in ("min", "max", "abs", "int") and depends(n):
            return True
        if isinstance(n, ast.Compare) and len(n.ops) > 1 and depends(n):
            return True
    return False


def _is_boolean(e: ast.expr) -> bool:
    """An expression that is a boolean array already: a comparison, `&`/`|` of them, `~`."""
    if isinstance(e, ast.Compare):
        return True
    if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Invert):
        return _is_boolean(e.operand)
    if isinstance(e, ast.BinOp) and isinstance(e.op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
        return _is_boolean(e.left) and _is_boolean(e.right)
    return False


def _truth(e: ast.expr) -> ast.expr:
    """Python's truth test of `e` as a boolean array: `e` itself when it is one, else `e != 0`."""
    if _is_boolean(e):
        return e
    return ast.Compare(left=e, ops=[ast.NotEq()], comparators=[ast.Constant(value=0)])


def _np(attr: str, *args: ast.expr) -> ast.Call:
    return ast.Call(func=ast.Attribute(value=ast.Name(id="np", ctx=ast.Load()), attr=attr,
                                       ctx=ast.Load()), args=list(args), keywords=[])


def _and(a: ast.expr | None, b: ast.expr) -> ast.expr:
    return b if a is None else ast.BinOp(left=copy.deepcopy(a), op=ast.BitAnd(), right=b)


def _not(a: ast.expr) -> ast.expr:
    return ast.UnaryOp(op=ast.Invert(), operand=copy.deepcopy(a))


class _Exprs(ast.NodeTransformer):
    """Expression-level rewrites: renames under the current branch, `and`/`or`/`not`, the
    scalar builtins, if-expressions, chained comparisons, clipped table indexing."""

    def __init__(self, rename: dict[str, str], tables: set[str], guard: bool,
                 stores: bool = False) -> None:
        self.rename = rename
        self.tables = tables
        self.guard = guard
        self.stores = stores                     # rename assignment targets too (loop bodies)

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if (isinstance(node.ctx, ast.Load) or self.stores) and node.id in self.rename:
            return ast.copy_location(ast.Name(id=self.rename[node.id], ctx=node.ctx), node)
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        vals = [_truth(self.visit(v)) for v in node.values]
        op = ast.BitAnd() if isinstance(node.op, ast.And) else ast.BitOr()
        out: ast.expr = vals[0]
        for v in vals[1:]:
            out = ast.BinOp(left=out, op=copy.deepcopy(op), right=v)
        return ast.copy_location(out, node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        node = self.generic_visit(node)  # type: ignore[assignment]
        if isinstance(node.op, ast.Not):
            # `not v`: `~` is right for a boolean only -- on the 0/1 of fp16_sign(x), ~1 is -2
            # and truthy (D483: this cost the live model a verified recip); a value is `== 0`
            if _is_boolean(node.operand):
                return ast.copy_location(ast.UnaryOp(op=ast.Invert(), operand=node.operand), node)
            return ast.copy_location(ast.Compare(left=node.operand, ops=[ast.Eq()],
                                                 comparators=[ast.Constant(value=0)]), node)
        return node

    def visit_IfExp(self, node: ast.IfExp) -> ast.AST:
        node = self.generic_visit(node)  # type: ignore[assignment]
        return ast.copy_location(_np("where", _truth(node.test), node.body, node.orelse), node)

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        node = self.generic_visit(node)  # type: ignore[assignment]
        if len(node.ops) == 1:
            return node
        operands = [node.left] + list(node.comparators)
        pairs = [ast.Compare(left=copy.deepcopy(operands[i]), ops=[node.ops[i]],
                             comparators=[copy.deepcopy(operands[i + 1])]) for i in range(len(node.ops))]
        out: ast.expr = pairs[0]
        for p in pairs[1:]:
            out = ast.BinOp(left=out, op=ast.BitAnd(), right=p)
        return ast.copy_location(out, node)

    def visit_Call(self, node: ast.Call) -> ast.AST:
        node = self.generic_visit(node)  # type: ignore[assignment]
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name in ("min", "max") and len(node.args) >= 2 and not node.keywords:
                fn = "minimum" if name == "min" else "maximum"
                out: ast.expr = node.args[0]
                for a in node.args[1:]:
                    out = _np(fn, out, a)
                return ast.copy_location(out, node)
            if name == "abs" and len(node.args) == 1:
                return ast.copy_location(_np("abs", node.args[0]), node)
            if name == "int" and len(node.args) == 1:
                return node.args[0]
            if self.guard and name in _TABLE_CALLS and len(node.args) > _TABLE_CALLS[name] \
                    and isinstance(node.args[0], ast.Name) and node.args[0].id in self.tables:
                k = _TABLE_CALLS[name]
                node.args[k] = self._clipped(node.args[0].id, node.args[k])
        return node

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        node = self.generic_visit(node)  # type: ignore[assignment]
        if self.guard and isinstance(node.value, ast.Name) and node.value.id in self.tables \
                and isinstance(node.ctx, ast.Load) and not isinstance(node.slice, ast.Slice):
            node.slice = self._clipped(node.value.id, node.slice)
        return node

    @staticmethod
    def _clipped(table: str, idx: ast.expr) -> ast.expr:
        top = ast.BinOp(left=ast.Call(func=ast.Name(id="len", ctx=ast.Load()),
                                      args=[ast.Name(id=table, ctx=ast.Load())], keywords=[]),
                        op=ast.Sub(), right=ast.Constant(value=1))
        return _np("clip", idx, ast.Constant(value=0), top)


class _Converter:
    """One function body into straight-line, single-return array form."""

    def __init__(self, fn: ast.FunctionDef, tables: set[str]) -> None:
        self.fn = fn
        self.tables = tables
        self.n = 0
        self.pending: list[tuple[ast.expr, ast.expr]] = []   # (predicate, value) of early returns

    def fresh(self, base: str) -> str:
        self.n += 1
        return f"{base}__{self.n}"

    def run(self) -> None:
        out: list[ast.stmt] = []
        rename: dict[str, str] = {a.arg: a.arg for a in self.fn.args.args}   # locals so far
        ret = self._block(self.fn.body, out, rename, pred=None, guard=False)
        if ret is None:
            raise VectorizeError(f"`{self.fn.name}` does not return on every path (line {self.fn.lineno})")
        for pred, value in reversed(self.pending):
            ret = _np("where", pred, value, ret)
        final = ast.Return(value=ret)
        final._src_line = self.fn.body[-1].lineno  # type: ignore[attr-defined]
        out.append(final)
        self.fn.body = out

    def _block(self, stmts: list[ast.stmt], out: list[ast.stmt], rename: dict[str, str],
               pred: ast.expr | None, guard: bool) -> ast.expr | None:
        """Emit `stmts` into `out`; returns the value returned on EVERY path through them
        (None when some path falls through). `pred` is the predicate under which this block
        runs (None at the top); `guard` marks a converted branch (table indexing is clipped)."""
        for st in stmts:
            line = st.lineno
            if isinstance(st, ast.Return):
                if st.value is None:
                    raise VectorizeError(f"`return` without a value (line {line})")
                return self._expr(st.value, rename, guard)
            if isinstance(st, ast.If) and self._depends(st.test, rename):
                ret = self._if(st, out, rename, pred, guard)
                if ret is not None:
                    return ret
                continue
            if isinstance(st, ast.While) and self._depends(st.test, rename):
                # a data-dependent loop is UNROLLED as WHILE_STEPS predicated iterations (each
                # one `if cond: body`, merged like any branch): a normalisation loop over a
                # 16-bit value ends within 24 steps; an element still looping after that keeps
                # its last value. `break`/`else` are not part of it.
                if st.orelse or any(isinstance(n, (ast.Break, ast.Continue)) for n in ast.walk(st)):
                    raise VectorizeError(f"`while` with break/continue/else cannot be made array form (line {line})")
                for _ in range(WHILE_STEPS):
                    step = ast.If(test=copy.deepcopy(st.test), body=copy.deepcopy(st.body), orelse=[])
                    ast.copy_location(step, st)
                    ret = self._if(step, out, rename, pred, guard)
                    if ret is not None:
                        return ret
                continue
            if isinstance(st, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                self._assign(st, out, rename, guard)
                continue
            if isinstance(st, (ast.For, ast.If)):
                # a constant loop or a constant `if` (on knobs): kept as a statement, its body
                # rewritten in place -- no single-assignment renaming inside, a loop body runs
                # again with the same names. Per-element control flow inside it is refused.
                for inner in ast.walk(st):
                    if inner is not st and isinstance(inner, (ast.If, ast.While)) \
                            and self._depends(inner.test, rename):
                        raise VectorizeError(
                            f"`if` on data inside a loop or a constant `if` -- write the loop "
                            f"body with np.where instead (line {inner.lineno})")
                    if isinstance(inner, ast.Return):
                        raise VectorizeError(f"`return` inside a loop or a constant `if` (line {inner.lineno})")
                for inner in ast.walk(st):
                    if isinstance(inner, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                        targets = inner.targets if isinstance(inner, ast.Assign) else [inner.target]
                        for t in targets:
                            for m in ast.walk(t):
                                if isinstance(m, ast.Name):
                                    rename.setdefault(m.id, m.id)
                self._push(out, self._rewrite(st, rename, guard, stores=True), line)
                continue
            if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
                continue                                     # a docstring
            if isinstance(st, ast.Pass):
                continue
            self._push(out, self._rewrite(st, rename, guard), line)
        return None

    def _if(self, st: ast.If, out: list[ast.stmt], rename: dict[str, str],
            pred: ast.expr | None, guard: bool) -> ast.expr | None:
        line = st.lineno
        cond = self.fresh("c")
        test = _truth(self._expr(st.test, rename, guard))     # a where needs a boolean
        self._push(out, ast.Assign(targets=[ast.Name(id=cond, ctx=ast.Store())], value=test), line)
        c = ast.Name(id=cond, ctx=ast.Load())
        then_rename = dict(rename)
        else_rename = dict(rename)
        then_out: list[ast.stmt] = []
        else_out: list[ast.stmt] = []
        then_ret = self._block(st.body, then_out, then_rename, _and(pred, c), True)
        else_ret = self._block(st.orelse, else_out, else_rename, _and(pred, _not(c)), True)
        out.extend(then_out)
        out.extend(else_out)
        if then_ret is not None and else_ret is not None:
            return _np("where", c, then_ret, else_ret)
        if then_ret is not None:
            self.pending.append((_and(pred, c), then_ret))
        if else_ret is not None:
            self.pending.append((_and(pred, _not(c)), else_ret))
        # merge every variable either branch assigned; a branch that returned contributes
        # nothing to the elements that continue, so the other side's value is kept for it
        assigned = [v for v in dict.fromkeys(list(then_rename) + list(else_rename))
                    if then_rename.get(v) != rename.get(v) or else_rename.get(v) != rename.get(v)]
        for v in assigned:
            before = rename.get(v)
            t = then_rename.get(v, before)
            e = else_rename.get(v, before)
            if t is None or then_ret is not None:            # unbound on that side, or it returned
                t = e
            if e is None or else_ret is not None:
                e = t
            if t is None or e is None:
                continue
            merged = self.fresh(v)
            value: ast.expr = (ast.Name(id=t, ctx=ast.Load()) if t == e
                               else _np("where", copy.deepcopy(c), ast.Name(id=t, ctx=ast.Load()),
                                        ast.Name(id=e, ctx=ast.Load())))
            self._push(out, ast.Assign(targets=[ast.Name(id=merged, ctx=ast.Store())], value=value), line)
            rename[v] = merged
        return None

    def _assign(self, st: ast.stmt, out: list[ast.stmt], rename: dict[str, str], guard: bool) -> None:
        line = st.lineno
        if isinstance(st, ast.AugAssign):
            if not isinstance(st.target, ast.Name):
                raise VectorizeError(f"only `name op= expr` (line {line})")
            cur = ast.Name(id=rename.get(st.target.id, st.target.id), ctx=ast.Load())
            value = ast.BinOp(left=cur, op=st.op, right=self._expr(st.value, rename, guard))
            self._bind(st.target.id, value, out, rename, line)
            return
        if isinstance(st, ast.AnnAssign):
            if st.value is None or not isinstance(st.target, ast.Name):
                raise VectorizeError(f"only `name = expr` assignments (line {line})")
            self._bind(st.target.id, self._expr(st.value, rename, guard), out, rename, line)
            return
        assert isinstance(st, ast.Assign)
        if len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            self._bind(st.targets[0].id, self._expr(st.value, rename, guard), out, rename, line)
            return
        if len(st.targets) == 1 and isinstance(st.targets[0], ast.Tuple) \
                and all(isinstance(n, ast.Name) for n in st.targets[0].elts):
            names = [n.id for n in st.targets[0].elts]
            value = self._expr(st.value, rename, guard)
            if isinstance(value, ast.Tuple) and len(value.elts) == len(names):
                for n, v in zip(names, value.elts):
                    self._bind(n, v, out, rename, line)
                return
            # `a, b = helper(...)`: kept as a tuple assignment with fresh names -- the transpiler
            # spells tuple-returning helpers this way (D478), and indexes nothing
            fresh = [self.fresh(n) for n in names]
            self._push(out, ast.Assign(targets=[ast.Tuple(elts=[ast.Name(id=f, ctx=ast.Store()) for f in fresh],
                                                          ctx=ast.Store())], value=value), line)
            for n, f in zip(names, fresh):
                rename[n] = f
            return
        if len(st.targets) == 1 and isinstance(st.targets[0], ast.Subscript):
            # `a[mask] = v` stays (the transpiler spells it as a where); its names are renamed
            self._push(out, self._rewrite(st, rename, guard), line)
            return
        if all(isinstance(t, ast.Name) for t in st.targets):
            value = self._expr(st.value, rename, guard)
            first = st.targets[0].id
            self._bind(first, value, out, rename, line)
            for t in st.targets[1:]:
                self._bind(t.id, ast.Name(id=rename[first], ctx=ast.Load()), out, rename, line)
            return
        raise VectorizeError(f"unsupported assignment (line {line})")

    def _bind(self, name: str, value: ast.expr, out: list[ast.stmt], rename: dict[str, str], line: int) -> None:
        """Every assignment gets a fresh name (single assignment), so branches can be merged."""
        fresh = self.fresh(name)
        self._push(out, ast.Assign(targets=[ast.Name(id=fresh, ctx=ast.Store())], value=value), line)
        rename[name] = fresh

    def _expr(self, e: ast.expr, rename: dict[str, str], guard: bool) -> ast.expr:
        return _Exprs(rename, self.tables, guard).visit(copy.deepcopy(e))

    def _rewrite(self, st: ast.stmt, rename: dict[str, str], guard: bool, stores: bool = False) -> ast.stmt:
        return _Exprs(rename, self.tables, guard, stores=stores).visit(copy.deepcopy(st))

    @staticmethod
    def _depends(e: ast.expr, rename: dict[str, str]) -> bool:
        """A test that names a local (parameter or assigned variable) is per-element."""
        return any(isinstance(n, ast.Name) and n.id in rename for n in ast.walk(e))

    @staticmethod
    def _push(out: list[ast.stmt], st: ast.stmt, line: int) -> None:
        st._src_line = line  # type: ignore[attr-defined]
        st.lineno = line
        out.append(st)
