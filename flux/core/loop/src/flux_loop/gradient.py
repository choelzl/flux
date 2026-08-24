"""The gradient a repair prompt carries (D423) and the revert rule (D504).

Tracks whether the last edit helped and how it stands against the best. Worsening edits are
tolerated up to `regress_after`, plus one per improving edit (at most `max_tolerance`); when
the tolerance is spent, the landing is first the best of the current line (a minor
backtrack), then the global best. An edit that improves on the last while still above the
best is a line being walked, not a regression (D491)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["Gradient"]


@dataclass
class Gradient:
    regress_after: int
    unit: str = " failing"                      # follows every number in the trend text
    fmt: Callable[[float], str] = str           # how a number prints (int, or `:g`)
    noun: str = "attempt"                       # "first measured <noun>"
    best: tuple[float, Any] | None = None       # (score, payload)
    best_key: Any = None                        # what identifies the best (text, code)
    best_failure: str | None = None             # what was wrong with the best (D484)
    prev: float | None = None
    worse: int = 0
    history: list[float] = field(default_factory=list)
    # tolerance earned by progress, and a line's own best to fall back to first (D504)
    tolerance: int | None = None                # regressions still allowed; None = regress_after
    max_tolerance: int = 4
    line_best: tuple[float, Any, Any, str | None] | None = None   # (score, payload, key, failure): the best an
    #                                             IMPROVING edit reached on the current line, above the global best
    line_backtracks: int = 0                    # minor backtracks taken on this line (at most one)
    line_failure: str | None = None             # the failure text of the last minor backtrack's landing

    @property
    def best_score(self) -> float:
        return self.best[0] if self.best is not None else float("inf")

    def _tol(self) -> int:
        return max(1, self.regress_after) if self.tolerance is None else self.tolerance

    def observe(self, score: float, payload: Any, *, key: Any = None,
                failure: str | None = None) -> tuple[str, bool]:
        """Record one attempt: (trend text for the next prompt, is it the best so far).
        Lower is better; a tie with the best is not a regression. A non-finite score is an
        unmeasured attempt (rule violation, crash, no output): never the best, never the revert
        target, never the comparison point, and not a regression either -- the tolerance is for
        edits measured worse (D535)."""
        f = self.fmt
        if not math.isfinite(score):
            trend = f"PROGRESS: your last {self.noun} could not be measured (refused before the test ran)."
            if self.best is not None:
                trend += f" The best so far is {f(self.best[0])}{self.unit}."
            self.history.append(score)
            return trend, False
        if self.prev is None:
            trend = f"PROGRESS: first measured {self.noun} -- {f(score)}{self.unit}."
        elif score < self.prev:
            trend = (f"PROGRESS: your last edit IMPROVED it, {f(self.prev)} -> {f(score)}"
                     f"{self.unit}. Keep going in this direction.")
        elif score == self.prev:
            trend = (f"PROGRESS: your last edit changed NOTHING measurable ({f(score)}"
                     f"{self.unit}). It did not touch the failing cases.")
        else:
            trend = (f"PROGRESS: your last edit made it WORSE, {f(self.prev)} -> {f(score)}"
                     f"{self.unit}.")
        is_best = self.best is None or score < self.best[0]
        improving = self.prev is not None and score < self.prev
        if is_best:
            self.best, self.best_key, self.worse = (score, payload), key, 0
            self.best_failure = failure
            self.tolerance = max(self._tol(), max(1, self.regress_after))
            self.line_best = None                        # a line is what departs from the best
        else:
            if improving and (self.line_best is None or score < self.line_best[0]):
                # the line found something (improved on the last edit): worth backtracking to
                self.line_best = (score, payload, key, failure)
            trend += f" The best so far is {f(self.best[0])}{self.unit}."
            # a step away from the best: worse than (or equal to) the last measured attempt.
            # Improving on the last one while above the best is a line being walked (D491),
            # and earns one more tolerated regression, up to `max_tolerance` (D504).
            if score > self.best[0] and not improving:
                self.worse += 1
                self.tolerance = self._tol() - 1
            elif score > self.best[0]:
                self.tolerance = min(self.max_tolerance, self._tol() + 1)
                trend += " Above the best, but closing in: this line continues."
        self.prev = score
        self.history.append(score)
        return trend, is_best

    def revert_due(self, current_key: Any) -> bool:
        """The tolerance is spent and the current text is not already where the backtrack lands."""
        if self.best is None or self._tol() > 0:
            return False
        target = self._backtrack_target()
        return current_key != target[2]

    def _backtrack_target(self) -> tuple[float, Any, Any, str | None]:
        """Where a spent tolerance lands: the current line's best when it differs from the global
        best and has not been backtracked to yet; else the global best."""
        assert self.best is not None
        lb = self.line_best
        if lb is not None and lb[0] > self.best[0] and self.line_backtracks < 1 and math.isfinite(lb[0]):
            return lb                                    # a tie with the best is the best
        return (self.best[0], self.best[1], self.best_key, self.best_failure)

    def revert(self) -> tuple[Any, str]:
        """Back to where the backtrack lands: (its payload, the sentence the prompt carries).
        The failure carried is the landed text's own (`best_failure` for the global best), never
        the last attempt's (D484)."""
        assert self.best is not None
        f = self.fmt
        target = self._backtrack_target()
        minor = self.line_best is not None and target is self.line_best
        self.worse, self.prev = 0, target[0]
        self.tolerance = max(1, self.regress_after)
        if minor:
            self.line_backtracks += 1
            self.line_failure = target[3]
            self.line_best = None                        # a new line starts here
            return target[1], (f" The text has been BACKTRACKED to the best of this line ({f(target[0])}{self.unit}; "
                               f"the best overall is {f(self.best[0])}{self.unit}, a different text); the failures "
                               "below are ITS failures; keep exploring from here with a smaller change.")
        self.line_backtracks = 0
        self.line_best = None
        return self.best[1], (" The text has been REVERTED to the best attempt; the failures "
                              "below are ITS failures; make a different, smaller change to it.")

    def revert_failure(self) -> str | None:
        """The failure text of what `revert` landed on: the line's best's, or the global best's."""
        return self.line_failure if self.line_backtracks else self.best_failure
