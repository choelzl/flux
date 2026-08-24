"""Pareto-UCT: a multi-objective tree policy for allocating a measurement budget (D368).

Unlike a hill-climb, a node earns expansions by what its branch contributes to the global
front, not by its own scalar score, so mediocre-looking branches get revisited. The selection
rule follows MicroEvo (Xiong et al., ICCAD'26, arXiv:2608.06183). The score of a child v with
parent p (their Eq. 2, adapted):

    ParetoUCT(v) = Q_hvi(v) + e * D(v) + lambda(t) * sqrt(ln N(p) / N(v))

    Q_hvi   the branch's best hypervolume improvement against the front when it was
            credited, normalised, averaged over visits -- backpropagated up the tree
    D       crowding: how isolated v's objectives are among the front and its siblings,
            so under-covered regions of the trade-off get expansions
    lambda  exploration, decaying with the spent budget: lambda0 * sqrt((T - t) / T)

Generic: candidates are opaque, objectives are "higher is better" tuples (negate a cost), and
measurement is the caller's. This module decides which node to expand next and what the front is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .dominance import dominates as _dominates


def dominates(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    """Maximise-form Pareto dominance: at least as good everywhere, better somewhere --
    the package's one rule (`flux_frontier.dominance`, D429), read as gains here."""
    return _dominates(a, b, minimize=False)


def hypervolume(points: Iterable[tuple[float, float]], reference: tuple[float, float]) -> float:
    """The 2-D dominated volume above `reference`. Exactly two objectives (quality and cost)."""
    kept = [p for p in points if p[0] > reference[0] and p[1] > reference[1]]
    kept.sort(key=lambda p: (-p[0], -p[1]))
    total = 0.0
    best_y = reference[1]
    for x, y in kept:
        if y > best_y:
            total += (x - reference[0]) * (y - best_y)
            best_y = y
    return total


@dataclass
class Node:
    """One measured candidate in the tree. The root is virtual and never measured."""

    candidate: Any = None
    objectives: tuple[float, float] | None = None
    parent: "Node | None" = None
    children: list["Node"] = field(default_factory=list)
    visits: int = 0
    hvi_sum: float = 0.0                  # normalised HVI credited through backpropagation
    expansions: int = 0                   # how many times this node was chosen to expand

    @property
    def q_hvi(self) -> float:
        return self.hvi_sum / self.visits if self.visits else 0.0


class ParetoUCT:
    """The tree and its selection rule. The caller measures; this decides where to spend.

        tree = ParetoUCT(reference=(1.0, 0.0), budget=40)
        tree.grow(seed_candidates_with_objectives)
        while budget_left:
            node = tree.select()
            children = propose_from(node.candidate)          # the caller's move generator
            for cand, objectives in measure(children):
                tree.record(node, cand, objectives)

    `reference` must be dominated by every point worth counting (the no-op design's quality,
    a storage bound); objectives are maximise-form. `scale` normalises each axis before any
    volume is computed, so a hypervolume never mixes IPC with bytes.
    """

    def __init__(self, *, reference: tuple[float, float], scale: tuple[float, float],
                 budget: int, explore0: float = 1.414, crowding_weight: float = 0.05,
                 identity: Callable[[Any], Any] = lambda c: c) -> None:
        if scale[0] <= 0 or scale[1] <= 0:
            raise ValueError("scale must be positive on both axes")
        self.reference = reference
        self.scale = scale
        self.budget = max(1, budget)
        self.spent = 0
        self.explore0 = explore0
        self.crowding_weight = crowding_weight
        self.identity = identity
        self.root = Node()
        self._seen: set[Any] = set()
        self._front: list[Node] = []

    # ---- geometry ------------------------------------------------------------------------
    def _norm(self, objectives: tuple[float, float]) -> tuple[float, float]:
        return ((objectives[0] - self.reference[0]) / self.scale[0],
                (objectives[1] - self.reference[1]) / self.scale[1])

    def front(self) -> list[Node]:
        return list(self._front)

    def predicted_gain(self, objectives: tuple[float, float]) -> float:
        """The hypervolume a point would add: a rollout estimate for ordering a wave.

        Used only to decide what to measure next; never recorded or backpropagated.
        """
        return self._hvi(objectives)

    def _front_points(self) -> list[tuple[float, float]]:
        return [self._norm(n.objectives) for n in self._front]

    def _hvi(self, objectives: tuple[float, float]) -> float:
        pts = self._front_points()
        base = hypervolume(pts, (0.0, 0.0))
        with_it = hypervolume(pts + [self._norm(objectives)], (0.0, 0.0))
        return with_it - base

    def _crowding(self, node: Node) -> float:
        """Normalised distance to the nearest measured neighbours, per axis (their Eq. 5)."""
        pool = {id(n): n for n in self._front}
        if node.parent is not None:
            for sib in node.parent.children:
                pool[id(sib)] = sib
        points = [self._norm(n.objectives) for n in pool.values() if n.objectives is not None]
        if len(points) < 3 or node.objectives is None:
            return 1.0
        mine = self._norm(node.objectives)
        total = 0.0
        for axis in (0, 1):
            values = sorted(p[axis] for p in points)
            lo = max((v for v in values if v < mine[axis]), default=mine[axis])
            hi = min((v for v in values if v > mine[axis]), default=mine[axis])
            span = values[-1] - values[0]
            total += (hi - lo) / span if span > 0 else 0.0
        return total

    # ---- the policy ----------------------------------------------------------------------
    def _explore(self) -> float:
        return self.explore0 * math.sqrt(max(0.0, self.budget - self.spent) / self.budget)

    def _score(self, node: Node) -> float:
        parent_visits = node.parent.visits if node.parent else self.root.visits
        uct = math.sqrt(math.log(max(2, parent_visits)) / node.visits) if node.visits else 1e9
        # The expansion penalty is in the score so a leaf whose moves are spent stops
        # winning selection; otherwise the caller loops on a barren node.
        return (node.q_hvi + self.crowding_weight * self._crowding(node)
                + self._explore() * uct - 0.05 * node.expansions)

    def select(self) -> Node:
        """Descend by Pareto-UCT score to the node worth expanding next."""
        node = self.root
        while node.children:
            best = max(node.children, key=self._score)
            # Expand here rather than descending when this node's own score beats every
            # child's: an interior node may still have unexplored moves.
            if node is not self.root and self._score(node) > self._score(best):
                return node
            node = best
        return node

    def exhausted(self, node: Node) -> None:
        """The caller found no unexplored move at `node`: penalise it and count the attempt."""
        node.expansions += 3

    def grow(self, seeds: list[tuple[Any, tuple[float, float]]]) -> None:
        """Measured starting points become the root's children."""
        for candidate, objectives in seeds:
            self.record(self.root, candidate, objectives)

    def seen(self, candidate: Any) -> bool:
        return self.identity(candidate) in self._seen

    def record(self, parent: Node, candidate: Any, objectives: tuple[float, float]) -> Node:
        """One measured child under `parent`: update the front, credit the branch."""
        self._seen.add(self.identity(candidate))
        child = Node(candidate=candidate, objectives=objectives, parent=parent)
        parent.children.append(child)
        parent.expansions += 1
        hvi = self._hvi(objectives)
        if not any(dominates(n.objectives, objectives) for n in self._front):
            self._front = [n for n in self._front if not dominates(objectives, n.objectives)]
            self._front.append(child)
        # Backpropagate the normalised improvement; a fruitless wave still counts a visit,
        # so a branch goes quiet without being forbidden.
        total_hv = hypervolume(self._front_points(), (0.0, 0.0)) or 1.0
        credit = hvi / total_hv
        walk: Node | None = child
        while walk is not None:
            walk.visits += 1
            walk.hvi_sum += credit
            walk = walk.parent
        self.spent += 1
        return child


__all__ = ["Node", "ParetoUCT", "dominates", "hypervolume"]
