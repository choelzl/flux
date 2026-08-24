"""Mapping functions from address to bank, as hardware would build them.

Two families, one interface. `Modulo` is the mapping every memory starts with -- a slice of
address bits -- and the one every stride that is a multiple of the bank count defeats. `XorFold`
is the family the solvers search: each BANK BIT is the XOR of a chosen subset of ADDRESS BITS.
That is a linear map over GF(2), it is what real skewed and hashed bank interleavings are
(a handful of XOR gates on the address path), and it has a hardware cost a search can minimise:
one two-input XOR per folded-in bit beyond the first.

A mapping is also DATA: a matrix, serialisable, comparable, and describable as the Verilog it
would become. That is what lets z3 search it, a model propose it, and a checker verify it
without any of the three knowing about the others.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


class Mapping:
    """The interface every family implements."""

    name: str

    def banks_of(self, addresses: np.ndarray, bank_bits: int) -> np.ndarray:
        """Vectorised: the bank index of every address in `addresses` (uint64)."""
        raise NotImplementedError

    def hardware_cost(self) -> int:
        """Two-input XOR gates on the address path."""
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def verilog(self, address_bits: int, bank_bits: int) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class Modulo(Mapping):
    """`bank = (addr >> shift) & (B-1)` -- the mapping that is not a mapping at all.

    `shift` is 0 for word-interleaved banks. Every stride that is a multiple of B*2^shift sends
    every access to one bank. Kept as the baseline every result is quoted against, because "we
    found a conflict-free mapping" means nothing unless the obvious one was NOT.
    """

    shift: int = 0
    name: str = "modulo"

    def banks_of(self, addresses: np.ndarray, bank_bits: int) -> np.ndarray:
        return (addresses >> np.uint64(self.shift)) & np.uint64((1 << bank_bits) - 1)

    def hardware_cost(self) -> int:
        return 0

    def describe(self) -> str:
        return f"bank = (addr >> {self.shift}) mod B"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "modulo", "shift": self.shift}

    def verilog(self, address_bits: int, bank_bits: int) -> str:
        hi = self.shift + bank_bits - 1
        return f"assign bank = addr[{hi}:{self.shift}];"


@dataclass(frozen=True)
class XorFold(Mapping):
    """Each bank bit is the XOR of a subset of address bits.

    `taps[i]` is the tuple of address-bit indices folded into bank bit i. `(3,)` is a plain wire;
    `(3, 9, 15)` is two XOR gates. The bit-0 slice `taps = ((0,), (1,), (2,))` IS `Modulo(0)`,
    which is the sense in which this family contains the baseline.
    """

    taps: tuple[tuple[int, ...], ...]
    name: str = "xor-fold"

    def __post_init__(self) -> None:
        for i, t in enumerate(self.taps):
            if not t:
                raise ValueError(f"bank bit {i} folds no address bits: it would be constant 0")
            if len(set(t)) != len(t):
                raise ValueError(f"bank bit {i} lists an address bit twice: {t}")

    def banks_of(self, addresses: np.ndarray, bank_bits: int) -> np.ndarray:
        out = np.zeros_like(addresses, dtype=np.uint64)
        for i, tap in enumerate(self.taps[:bank_bits]):
            bit = np.zeros_like(addresses, dtype=np.uint64)
            for a_bit in tap:
                bit ^= (addresses >> np.uint64(a_bit)) & np.uint64(1)
            out |= bit << np.uint64(i)
        return out

    def hardware_cost(self) -> int:
        return sum(max(0, len(t) - 1) for t in self.taps)

    def describe(self) -> str:
        terms = [" ^ ".join(f"a{b}" for b in t) for t in self.taps]
        return "bank = {" + ", ".join(f"b{i}=[{x}]" for i, x in enumerate(terms)) + "}"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "xor-fold", "taps": [list(t) for t in self.taps]}

    def verilog(self, address_bits: int, bank_bits: int) -> str:
        lines = []
        for i, t in enumerate(self.taps[:bank_bits]):
            lines.append(f"assign bank[{i}] = " + " ^ ".join(f"addr[{b}]" for b in t) + ";")
        return "\n".join(lines)

    @property
    def matrix(self) -> np.ndarray:
        """The GF(2) matrix, bank_bits x address_bits, for anyone who wants linear algebra."""
        width = max(b for t in self.taps for b in t) + 1
        m = np.zeros((len(self.taps), width), dtype=np.uint8)
        for i, t in enumerate(self.taps):
            for b in t:
                m[i, b] = 1
        return m


def from_dict(d: dict[str, Any]) -> Mapping:
    kind = d.get("kind")
    if kind == "modulo":
        return Modulo(shift=int(d.get("shift", 0)))
    if kind == "xor-fold":
        return XorFold(taps=tuple(tuple(int(b) for b in t) for t in d["taps"]))
    raise ValueError(f"unknown mapping kind {kind!r}")


def modulo_baseline(bank_bits: int) -> XorFold:
    """`Modulo(0)` written as a fold, so every candidate is one family for the solvers."""
    return XorFold(taps=tuple((i,) for i in range(bank_bits)), name="modulo-as-fold")


# ---- the open family: an expression a model may write --------------------------------------
import ast as _ast

#: Operators an expression mapping may use, with a crude per-use hardware cost. XOR/AND/shift
#: are wires and single gates; an adder is a carry chain; a multiplier and a modulo by anything
#: but a power of two are the two things an address path should not carry.
_OPS = {
    _ast.BitXor: ("^", 1), _ast.BitAnd: ("&", 1), _ast.BitOr: ("|", 1),
    _ast.RShift: (">>", 0), _ast.LShift: ("<<", 0),
    _ast.Add: ("+", 8), _ast.Sub: ("-", 8), _ast.Mult: ("*", 64), _ast.Mod: ("%", 16),
}


class InvalidExpression(ValueError):
    pass


@dataclass(frozen=True)
class Expr(Mapping):
    """`bank = <expression in a> mod B`, for the families XOR-folds cannot express.

    z3 proves when NO linear map exists (D356: strides 1 and 8 with eight concurrent accesses
    have none). What remains is non-linear: a rotation, an addition before the fold, a modulo by
    a prime. Those are what a model proposes, in an expression over the address `a` using
    `+ - * % ^ & | << >>` and integer constants. Evaluated vectorised over numpy uint64, so the
    checker treats it exactly like a fold; the final `mod B` is applied here, so the expression
    may be any width.

    A modulo by a non-power-of-two costs a real divider and is priced accordingly; a model that
    reaches for `a % 7` will find it is the most expensive thing on the menu.
    """

    text: str
    name: str = "expr"

    def __post_init__(self) -> None:
        self._compile()

    def _compile(self):
        try:
            tree = _ast.parse(self.text.strip(), mode="eval")
        except SyntaxError as exc:
            raise InvalidExpression(f"not an expression: {exc.msg}") from exc
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Expression):
                continue
            if isinstance(node, _ast.Name):
                if node.id != "a":
                    raise InvalidExpression(f"only `a` (the address) may be named, not {node.id!r}")
            elif isinstance(node, _ast.Constant):
                if not isinstance(node.value, int) or isinstance(node.value, bool):
                    raise InvalidExpression("constants must be integers")
                if node.value < 0 or node.value >= (1 << 40):
                    raise InvalidExpression("constants must be within 0 .. 2^40")
            elif isinstance(node, _ast.BinOp):
                if type(node.op) not in _OPS:
                    raise InvalidExpression(f"operator {type(node.op).__name__} is not allowed")
            elif isinstance(node, (_ast.UnaryOp,)) and isinstance(node.op, _ast.Invert):
                continue
            elif isinstance(node, (_ast.Load, _ast.Invert)) or type(node) in _OPS:
                continue
            else:
                raise InvalidExpression(f"{type(node).__name__} is not allowed in a mapping")
        return tree

    def banks_of(self, addresses: np.ndarray, bank_bits: int) -> np.ndarray:
        tree = self._compile()
        mask = np.uint64((1 << bank_bits) - 1)

        def ev(node):
            if isinstance(node, _ast.Expression):
                return ev(node.body)
            if isinstance(node, _ast.Name):
                return addresses.astype(np.uint64)
            if isinstance(node, _ast.Constant):
                return np.uint64(node.value)
            if isinstance(node, _ast.UnaryOp):
                return ~ev(node.operand)
            left, right = ev(node.left), ev(node.right)
            op = type(node.op)
            with np.errstate(over="ignore"):
                if op is _ast.Add: return left + right
                if op is _ast.Sub: return left - right
                if op is _ast.Mult: return left * right
                if op is _ast.Mod:
                    r = np.asarray(right, dtype=np.uint64)
                    if np.any(r == 0):
                        raise InvalidExpression("modulo by zero")
                    return left % right
                if op is _ast.BitXor: return left ^ right
                if op is _ast.BitAnd: return left & right
                if op is _ast.BitOr: return left | right
                if op is _ast.RShift: return left >> (right & np.uint64(63))
                if op is _ast.LShift: return left << (right & np.uint64(63))
            raise InvalidExpression(f"unsupported operator {op.__name__}")

        return np.asarray(ev(tree), dtype=np.uint64) & mask

    def hardware_cost(self) -> int:
        tree = self._compile()
        cost = 0
        for node in _ast.walk(tree):
            if isinstance(node, _ast.BinOp):
                sym, c = _OPS[type(node.op)]
                if type(node.op) is _ast.Mod and isinstance(node.right, _ast.Constant):
                    v = node.right.value
                    c = 0 if v and not (v & (v - 1)) else 128        # power of two is a mask
                if type(node.op) is _ast.Mult and isinstance(node.right, _ast.Constant):
                    v = node.right.value
                    c = 0 if v and not (v & (v - 1)) else 64         # power of two is a shift
                cost += c
        return cost

    def describe(self) -> str:
        return f"bank = ({self.text}) mod B"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "expr", "text": self.text}

    def verilog(self, address_bits: int, bank_bits: int) -> str:
        return (f"// bank = ({self.text}) mod {1 << bank_bits}  -- an expression mapping; "
                f"synthesise the arithmetic explicitly\nassign bank = ({self.text.replace('a', 'addr')}) "
                f"& {bank_bits}'h{(1 << bank_bits) - 1:x};")


def _from_dict_expr(d: dict[str, Any]) -> Mapping:
    return Expr(text=str(d["text"]))


_original_from_dict = from_dict


def from_dict(d: dict[str, Any]) -> Mapping:                          # noqa: F811
    if d.get("kind") == "expr":
        return _from_dict_expr(d)
    return _original_from_dict(d)
