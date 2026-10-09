"""`PromptProblem`'s prototype (D604, D611, D615): the stage a golden model gives a document, its
cost ceiling and the shrink before spelling, the cost pass that improves a spelled design at its
source, and the spelling itself (py2sv, ICSC). A mixin of `flux_loop.task.PromptProblem` (D891).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .problem import Problem
from .types import Candidate, LoopState, Verdict
from .document import TaskError, _digest_of, _leaf


#: Default ceiling on a prototype's estimated cost (`py2sv.cost`, about 3 units per um2), set by
#: ASAP7 synthesis time, which grows much faster than cost: ~1,500 takes minutes, ~20,000 over
#: an hour (D619).
DEFAULT_COST_MAX = 2000.0


class PrototypeMixin:
    """The prototype, its cost and its spelling, for `PromptProblem` (D891)."""

    def prototype(self):
        """The prototype stage (D604): a gate that names a golden model gives a document one,
        using this problem's own check when one was put on the instance (D516)."""
        if "_golden_cap" not in self.__dict__:
            from .golden_proto import capability

            self.__dict__["_golden_cap"] = capability(self.task)
        cap = self.__dict__["_golden_cap"]
        own = self.__dict__.get("prototype_check")
        if cap is not None and own is not None and cap.check is None:
            return replace(cap, check=own)
        return cap

    # ---- the cost of a spelled design, known before any synthesis (D615) --------------------
    def cost_ceiling(self) -> float | None:
        """The most a prototype may cost to be spelled and synthesised: `budget.prototype_cost_max`,
        or DEFAULT_COST_MAX; None = no ceiling (a negative value)."""
        cap = float(self.task.budget.get("prototype_cost_max", 0.0) or 0.0)
        if cap < 0:
            return None
        return cap if cap > 0 else DEFAULT_COST_MAX

    def _prototype_cost(self, code: str) -> tuple[float | None, str]:
        cache = self.__dict__.setdefault("_costs", {})
        digest = _digest_of(code)
        if digest not in cache:
            from .golden_proto import prototype_cost

            cache[digest] = prototype_cost(code, self.task)
        return cache[digest]

    def _over_ceiling(self, cand: Candidate, state: LoopState) -> str:
        """Why a spelled design is not worth a costly stage, or "": its prototype's cost over the
        ceiling. A design spelled before the ceiling existed is judged by its prototype on record."""
        if (cand.knobs or {}).get("generator") != "py2sv" and "flux py2sv" not in (cand.artifact or "")[:300]:
            return ""
        ceiling = self.cost_ceiling()
        if ceiling is None:
            return ""
        key = cand.subgoal or "*"
        cost = (cand.meta or {}).get("prototype_cost")
        why = ""
        if cost is None and key in state.prototypes:
            cost, why = self._prototype_cost(state.prototypes[key])
        if cost is None:
            state.say(f"  {cand.name}: its prototype's cost is not known ({why or 'no prototype for ' + key}); "
                      "measured without the ceiling")
            return ""
        if cost <= ceiling:
            return ""
        return (f"not synthesised: its prototype costs {cost:,.0f}, over the ceiling of {ceiling:,.0f} "
                f"(budget.prototype_cost_max) -- {why or 'the prototype is made cheaper first'}")

    def shrink_prototype(self, subgoal: str | None, state: LoopState) -> str:
        """Make a verified prototype over the cost ceiling cheaper before it is spelled (D615).

        Runs a short cost pass (`prototype_shrink_attempts`) and keeps the cheapest passing
        prototype. Returns why the part stops here (still over the ceiling), or ""."""
        import dataclasses

        from .golden_proto import CHEAPER
        from .prototype import _prototype_stage

        cap = self.prototype()
        key = subgoal or "*"
        if cap is None or getattr(cap, "language", "") != "python" or key not in state.prototypes:
            return ""
        ps = state.part(subgoal)
        proto = state.prototypes[key]
        ceiling = self.cost_ceiling()
        if ceiling is None:
            return ""
        if ps.shrunk != _digest_of(proto) and int(state.request.prototype_shrink_attempts or 0) > 0:
            c0, why0 = self._prototype_cost(proto)
            if c0 is not None and c0 > ceiling:          # under the ceiling it is built; `improve` works on it later
                target = ceiling
                ps.optimise = {"kind": "cost", "cost": c0, "target": target}
                keep_seed = state.proto_best.pop(key, None)
                state.prototypes.pop(key)
                state.proto_best[key] = (float(c0), proto, f"This prototype passes every input. Make it CHEAPER before "
                                         f"it is built: {why0}. The goal is <= {target:,.0f}. {CHEAPER}")
                state.say(f"  shrink {subgoal or self.task.id}: {why0.split(';')[0]} -> goal <= {target:,.0f} "
                          f"(before any RTL is spelled)")
                request = state.request
                n = int(request.prototype_shrink_attempts)
                na = min(n, int(request.prototype_agent_attempts))          # D933: an agent's turns are minutes
                state.request = dataclasses.replace(request, prototype_attempts=n, prototype_attempts_max=n,
                                                    prototype_agent_attempts=na, prototype_agent_attempts_max=na)
                try:
                    code, _why = _prototype_stage(self, subgoal, state, None, method="cheaper")
                    if code is None:
                        best = state.proto_best.get(key)
                        if best and best[0] < c0 and best[1].strip():
                            code = best[1]
                    state.prototypes[key] = code or proto
                finally:
                    state.request = request
                    ps.optimise = None
                    if keep_seed is not None:
                        state.proto_best[key] = keep_seed
                    else:
                        state.proto_best.pop(key, None)
                ps.shrunk = _digest_of(state.prototypes[key])
                c1, _ = self._prototype_cost(state.prototypes[key])
                state.say(f"  shrink {subgoal or self.task.id}: cost {c0:,.0f} -> {c1 if c1 is None else format(c1, ',.0f')}")
        c, why = self._prototype_cost(state.prototypes[key])
        if c is not None and c > ceiling:
            return (f"the verified prototype costs {c:,.0f}, over the ceiling of {ceiling:,.0f} "
                    f"(budget.prototype_cost_max): not spelled nor synthesised; the next pass shrinks it again -- {why}")
        return ""

    def improve(self, item: Any, state: LoopState) -> tuple[Candidate | None, Any, str]:
        """Improve a spelled design at its source, the prototype, with a cost pass (D613);
        reworking the spelled RTL would be overwritten on the next spell. Others use the default."""
        cap = self.prototype()
        key = item.subgoal or "*"
        if item.explore or item.dse == "variations" or cap is None or getattr(cap, "language", "") != "python" or key not in state.prototypes:
            return Problem.improve(self, item, state)     # explore/variations draft independently of the verified prototype
        return self._cost_pass(item, state, key)

    def _cost_pass(self, item: Any, state: LoopState, key: str) -> tuple[Candidate | None, Any, str]:
        """The verified prototype as the seed, passing every input as the gate, its hardware
        cost (`py2sv.cost`) as the score, 30% cheaper as the goal; the best cheaper prototype
        that passes is spelled and measured like any design."""
        from .golden_proto import CHEAPER

        proto = state.prototypes[key]
        c0, why0 = self._prototype_cost(proto)
        if c0 is None:
            return None, None, f"the prototype's cost could not be measured ({why0})"
        ps = state.part(item.subgoal)
        ceiling = self.cost_ceiling()
        target = 0.7 * c0 if ceiling is None or c0 <= ceiling else ceiling   # over the ceiling, the ceiling is the goal
        ps.optimise = {"kind": "cost", "cost": c0, "target": target}
        keep_seed = state.proto_best.pop(key, None)
        state.prototypes.pop(key)
        state.proto_best[key] = (float(c0), proto, f"This prototype passes every input. Now make it CHEAPER in hardware: "
                                 f"{why0}. The goal is <= {target:,.0f}. {CHEAPER}")
        state.say(f"  cost pass {item.subgoal or self.task.id}: {why0.split(';')[0]} -> goal <= {target:,.0f}")
        try:
            cand, built, reason = self.generate(item.subgoal, f"cheaper than cost {c0:,.0f}", state, item.why)
            if cand is None:
                best = state.proto_best.get(key)
                if best and best[0] < c0 and best[1].strip() and best[1] != proto:
                    state.say(f"  cost pass: the goal was not reached, but a prototype at {best[0]:,.0f} passes (from {c0:,.0f}) -- taking it")
                    ps.optimise = None
                    state.prototypes[key] = best[1]
                    cand, built, reason = self.generate(item.subgoal, "the cheaper prototype", state, item.why)
            return cand, built, reason
        finally:
            ps.optimise = None
            state.prototypes.setdefault(key, proto)            # nothing cheaper: the verified one stays
            if keep_seed is not None:
                state.proto_best[key] = keep_seed
            else:
                state.proto_best.pop(key, None)

    def transpile(self, prototype: str, subgoal: str | None, state: LoopState,
                  pipeline: int | None = None) -> Candidate | None:
        """Spell a verified prototype as the target with `flux_loop.py2sv` (D611); None when it
        cannot be spelled, and the model transcribes instead. A world's transpiler replaces this."""
        cap = self.prototype()
        language = getattr(cap, "language", "")
        if cap is None or pipeline or language not in ("python", "systemc"):
            return None
        from .observe import _phase

        if language == "systemc":
            # ICSC translates the verified SC_MODULE (D636); absent, the model transcribes it
            from .systemc_proto import icsc

            translate = (cap.extra or {}).get("translate")
            if translate is None or icsc() is None:
                return None
            generator, how = "icsc", "transpiled from the verified prototype by ICSC"
            with _phase(f"generate: translate {subgoal or self.task.id} (ICSC)", why="the verified prototype, no model") as out:
                sv, why = translate(prototype)
                out["result"] = f"{sv.count(chr(10))} lines" if sv else f"not translated: {why}"
        else:
            from .golden_proto import spell_prototype

            generator, how = "py2sv", "spelled from the verified prototype by the loop"
            with _phase(f"generate: spell {subgoal or self.task.id} (py2sv)", why="the verified prototype, no model") as out:
                sv, why = spell_prototype(prototype, self.task)
                out["result"] = f"{sv.count(chr(10))} lines" if sv else f"not spelled: {why}"
        if not sv:
            state.say(f"  {subgoal or self.task.id}: the prototype is not spelled by the loop ({why}); the model transcribes it")
            return None
        state.say(f"  {subgoal or self.task.id}: {how} ({sv.count(chr(10))} lines)")
        self._count += 1
        from .records import prototype_digest

        cost, _ = self._prototype_cost(prototype) if language == "python" else (None, "")
        # The source prototype's digest lets a reload find it and its cost guard costly stages;
        # `transpiled` lets a transpiler change re-spell it (D510).
        meta: dict[str, Any] = {"prototype_sha": prototype_digest(prototype), "transpiled": True}
        from .ideas import code_ids

        ids = code_ids(state, prototype, subgoal)
        if ids:
            meta["idea_ids"] = ids
        if cost is not None:
            meta["prototype_cost"] = cost
        return Candidate(f"{subgoal or _leaf(self.task.id)}#spelled{self._count}", sv,
                         knobs={"task": self.task.id, "part": subgoal or "", "generator": generator}, subgoal=subgoal,
                         meta=meta)

    def prototype_check(self, code: str, subgoal: str | None, state: LoopState) -> Verdict:
        """The loop's skeleton (D516) over the world's capability -- rules, array form, the
        family search, the sandbox, the world's judge."""
        from .check import check_prototype
        from .prototype import verified_operators

        cap = self.prototype()
        if cap is None:
            raise TaskError(f"{self.task.id}: no prototype stage is declared")
        if cap.check is not None and cap.check is not self.__dict__.get("prototype_check"):
            # The capability's own gate, but not an instance override (D516), which calls this
            # method as its base check and would recurse.
            return cap.check(code, subgoal, state)
        return check_prototype(cap, code, subgoal, state, operators=verified_operators(state, subgoal))

    def _transpiler(self) -> str:
        """D864: what spells this document's prototype -- py2sv and the golden's clocking (its
        LATENCY is the pipeline cut) -- as a version: changed, an admitted design is re-spelled
        from its verified prototype at the next reload (D510). "" when the loop spells nothing."""
        import hashlib

        cap = self.prototype()
        if cap is None or getattr(cap, "language", "") != "python":
            return ""
        from . import py2sv
        from .golden_proto import golden_path, load

        try:
            g = load(golden_path(self.task))
            clocking = f"{bool(g.clocked)}:{g.latency or 0}"
        except Exception:  # noqa: BLE001
            clocking = "?"
        return hashlib.sha256(Path(py2sv.__file__).read_bytes() + clocking.encode()).hexdigest()[:16]
