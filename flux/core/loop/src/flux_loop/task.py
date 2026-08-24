"""`PromptProblem`: the `Problem` that runs a task document (D430). What a document says, and
how it is loaded, is `flux_loop.document`.

Prompts are composed from the statement and contract; the gate and stages are commands (or
evaluators named in the ABI registry); the record and report are the loop's.

Commands carry placeholders: `{artifact}` (the candidate written to a file), `{workdir}`,
`{name}`, `{part}`, `{python}` (this interpreter). A gate's `test` prints its failures;
`count_re` (one integer group) or `fail_re` (one match per failure) counts them, and a non-zero
exit with nothing counted is one failure. Exit 3 means the candidate did not build (D594): a
build failure, not a score, so "best so far" is always a design that compiles.

A world (D519), `world: package.module:World`, supplies what a document cannot say in prose
or numbers (toolkit, transpiler, exhaustive judge, composition, measurement): a callable that
takes the problem and returns an object whose methods are bound as `Problem` hooks.
`hooks: {name: module:callable}` replaces one hook with a callable taking the problem first.
The document still owns parts, objectives, ladder, stages (a stage with no command and no
evaluator is the world's; `needs:` names required tools on PATH, else it is skipped),
knowledge, budget and the world's `params:`; the record is named by the id.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .model import _json
from .problem import Problem
from .objective import Objectives
from .types import (BuildError, Candidate, LoopRequest, LoopState, StageNames, Scored,
                    SubLoop, Verdict)

if TYPE_CHECKING:  # pragma: no cover
    from .roles import Roles
from .document import (BUILD_FAILED, Part, TaskError, TaskSpec, _digest_of, _flux_rtl_tools,
                        _knob_subs, _leaf, _point_name, _rig_for, _write_point, _substitute, contract_lines, describe_flow, loop_owned,
                        resolve, world_hooks)

__all__ = ["PromptProblem", "model_use", "task_report_lines"]


# ------------------------------------------------------------------ the problem
#: Default ceiling on a prototype's estimated cost (`py2sv.cost`, about 3 units per um2), set by
#: ASAP7 synthesis time, which grows much faster than cost: ~1,500 takes minutes, ~20,000 over
#: an hour (D619).
DEFAULT_COST_MAX = 2000.0


class PromptProblem(Problem):
    """A `Problem` from a task document alone (D430): the prompts from the statement and
    contract, the gate and the stages from its commands, the parts from its list."""

    def __init__(self, task: TaskSpec, *, roles: "Roles | None" = None) -> None:
        self.task = task
        self.name = task.id
        self._count = 0
        self.parts: tuple[Part, ...] = task.parts     # decided by `decompose` when the task says so
        #: Sub-task specs once `"subtasks": "decompose"` has been answered (D455); None until.
        self._children: tuple[TaskSpec, ...] | None = None
        self._roles = _rig_for(task, roles)
        self._caller_roles = roles                     # caller overrides reach the children (D555)
        #: The world's object of hooks (D519), or None for a document that runs on its own.
        self.world: Any = None
        if task.world:
            self.world = resolve(task.world)(self)
            owned = loop_owned()
            taken = sorted(n for n in owned if callable(getattr(self.world, n, None)) and n in type(self.world).__dict__)
            if taken:
                raise TaskError(f"world {task.world}: {', '.join(taken)} is the loop's, not a world's hook (D561); "
                                "a world fills the contract: " + "; ".join(contract_lines()))
            for name in world_hooks():
                fn = getattr(self.world, name, None)
                if callable(fn):
                    setattr(self, name, fn)
        import functools

        for name, spec in task.hooks.items():
            setattr(self, name, functools.partial(resolve(spec, f"hooks.{name}"), self))
        from .skills import load_skills, skill_index

        self._skills = load_skills(list(task.skills)) if task.skills else []
        if self._skills:                               # every prompt's static part names the skills (D588)
            base_prefix = self.prompt_prefix

            def prefixed(subgoal: Any, state: Any, _base=base_prefix) -> str:
                tools = bool(getattr(getattr(state, "request", None), "tools", True))
                index = skill_index(self._skills, tools=tools)
                head = _base(subgoal, state) or ""
                # before the reply shape: models skip what follows "reply with ONLY JSON"
                i = head.find("REPLY SHAPE")
                if i > 0:
                    return head[:i].rstrip() + "\n\n" + index + "\n\n" + head[i:]
                return (head + "\n\n" + index) if head else index

            self.prompt_prefix = prefixed
        declared = frozenset(task.flow.get("analytical") or ())
        if declared:                                   # the document's analytical stages join the world's (D542)
            world_analytic = self.__dict__.get("analytic_stages") or (lambda: frozenset())
            self.analytic_stages = lambda: frozenset(world_analytic()) | declared

    def skill_list(self) -> list[Any]:
        """The document's skills (D588): what the `skill` tool loads and the agents receive."""
        return list(self.__dict__.get("_skills") or [])

    def __getattr__(self, name: str) -> Any:
        """Fall back to the world's attributes (D519), so its own state is reachable here."""
        world = self.__dict__.get("world")
        if world is None or name.startswith("__"):
            raise AttributeError(name)
        return getattr(world, name)

    # ---- what the document says, and what the world fills (D519)
    def ladder(self):
        """The document's `ladder:` (D517): true is the default ladder, an object its fields."""
        doc = self.task.ladder
        if not doc:
            return None
        from .ladder import Ladder

        if doc is True:
            return Ladder()
        return Ladder(**{k: tuple(v) if isinstance(v, list) else v for k, v in doc.items()})

    def prototype(self):
        """The world's prototype stage (D515), using this problem's own check when one was put
        on the instance (D516); otherwise the loop's skeleton runs the world's judge."""
        world = self.__dict__.get("world")
        proto = getattr(world, "prototype", None)
        cap = proto() if callable(proto) else None
        if cap is None and world is None:
            # a gate that names a golden model gives any document the prototype stage (D604)
            if "_golden_cap" not in self.__dict__:
                from .golden_proto import capability

                self.__dict__["_golden_cap"] = capability(self.task)
            return self.__dict__["_golden_cap"]
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
                state.request = dataclasses.replace(request, prototype_attempts=int(request.prototype_shrink_attempts),
                                                    prototype_attempts_max=int(request.prototype_shrink_attempts))
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
        if cap is None or getattr(cap, "language", "") != "python" or key not in state.prototypes:
            return Problem.improve(self, item, state)
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

    def roles(self) -> "Roles":
        """Who fills each of the four roles (D460): the document's `roles`, overridden slot by
        slot by the caller's."""
        return self._roles

    # ---- mentor
    def space(self, state: Any) -> dict[str, list]:
        return dict(self.task.space)

    def seeds(self, state: Any) -> list[dict[str, Any]]:
        """The document's `seeds:`, a knob a seed leaves out at its first choice."""
        from .dse import first

        base = first(self.task.space)
        return [{**base, **p} for p in self.task.seeds]

    def instantiate(self, points: list[dict[str, Any]], state: LoopState) -> list[Candidate]:
        """With a `space:` and a generator command, run the command once per point with its knobs
        as `{knob}`; the file written at `{artifact}` is the candidate (D581). Without a command,
        a point stays knobs."""
        cmd = self.task.generator.get("command")
        if not cmd:
            return Problem.instantiate(self, points, state)
        workdir = Path(state.workdir or ".")
        workdir.mkdir(parents=True, exist_ok=True)
        out: list[Candidate] = []
        for point in points:
            name = _point_name(point) or _leaf(self.task.id)
            path = workdir / f"point-{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}{self.task.extension}"
            path.unlink(missing_ok=True)
            subs = {**_knob_subs(point), "artifact": str(path), "workdir": str(workdir), "name": name,
                    "point": _write_point(path, point),
                    "part": "", "python": sys.executable, "home": self.task.home or ".",
                    "failure": "", "attempt": "1"}
            run = self._run(tuple(cmd), subs, self.task.gate.timeout_s, "generate")
            if not run.ok or not path.is_file():
                tail = ((run.stdout or "") + "\n" + (run.stderr or "")).strip()[-300:]
                state.say(f"  {name}: the generator " + (f"exited {run.returncode}" if not run.ok
                                                         else f"wrote no {path.name}") + (f": {tail}" if tail else ""))
                continue
            out.append(Candidate(name, path.read_text(), knobs=dict(point)))
        return out

    def objectives(self) -> Objectives:
        """The document's objectives, with `stage: deepest` resolved to the chain's last stage and
        the margins measured on shallower stages applied (D562)."""
        stages = self.stages()
        out = []
        for o in self.task.objectives:
            if o.stage == "deepest" and stages:
                o = replace(o, stage=stages[-1])
            learned = self.__dict__.get("_margins", {}).get(o.metric) or {}
            if learned:
                o = replace(o, margins=tuple(sorted(learned.items())))
            out.append(o)
        return Objectives(out)

    def calibrated(self, biases: list[Any], state: Any) -> None:
        """Turn measured stage biases into margins on cheaper stages (D562).

        For a goal on stage D, a shallower stage S must clear it by the measured ratio D/S (the
        product of every link from S to D); the document's `margin` is the floor. A chain with an
        unmeasured link keeps the document's margin."""
        stages = self.stages()
        ratio = {(b.stage, b.against, b.metric): float(b.ratio) for b in biases if getattr(b, "ratio", None)}
        margins: dict[str, dict[str, float]] = dict(self.__dict__.get("_margins", {}))
        for o in self.objectives():
            if o.goal is None or o.stage not in stages:
                continue
            top = stages.index(o.stage)
            for i in range(top):
                shallow = stages[i]
                r = 1.0
                for j in range(i, top):
                    link = ratio.get((stages[j], stages[j + 1], o.metric))
                    if link is None or link <= 0:
                        r = None
                        break
                    r *= link
                if r is None:
                    continue
                measured = max(0.0, (1.0 / r - 1.0) if o.direction == "maximize" else (r - 1.0))
                before = o.margin_at(shallow)
                margins.setdefault(o.metric, {})[shallow] = measured
                if measured > float(o.margin) + 1e-9 and abs(measured - before) > 1e-6:
                    state.say(f"  the margin on the {shallow} stage for {o.metric} is {measured:.1%} from the measured "
                              f"{o.stage}/{shallow} ratio {r:.3f} (the document's {o.margin:.0%} is the floor): "
                              f"the goal there is {o.goal * (1 + measured) if o.direction == 'maximize' else o.goal / (1 + measured):.4g}")
        self._margins = margins

    def objective(self, request: LoopRequest) -> dict[str, Any]:
        """The record's identity document: the document's id, so sibling campaigns of one
        document find each other (D540)."""
        return {"study": self.task.id}

    def campaign_name(self, request: LoopRequest) -> str | None:
        """The record's name: the document's id, `<parent>/<child>` for a sub-document (D524, D628)."""
        return self.task.record or None

    def objections(self, state: Any) -> list[str]:
        """The model's objections to the document before any step runs (`flow: {validate: llm}`,
        D556). Advisory, never a gate; empty when not asked for or with no model."""
        if self.task.flow.get("validate") != "llm" or state.proposer is None:
            return []
        import json as _json_mod

        from .model import _ask, _json

        doc = _json_mod.dumps(self.task.to_dict(), indent=1, default=str)[:12000]
        prompt = ("Read this problem document before the run spends anything and OBJECT to what makes it "
                  "unanswerable or wasteful as written: an objective on a metric no stage measures, a goal no "
                  "stage could reach, a part with no gate, a cutoff that contradicts an objective, a budget that "
                  "cannot finish, a statement the parts do not add up to. Say nothing about style. "
                  "Reply as JSON: {\"ok\": true|false, \"objections\": [\"one line each\"]}.\n\nTHE DOCUMENT:\n"
                  + doc + "\n\nTHE FLOW IN FORCE:\n" + "\n".join(describe_flow(self.task, self)))
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"},
                                                   "objections": {"type": "array", "items": {"type": "string"}}},
                  "required": ["objections"]}
        try:
            got = _json(_ask(state, prompt, schema).text)
        except Exception as exc:  # noqa: BLE001 -- advisory: a failed reading objects to nothing
            return [f"(the model's reading did not run: {exc!s:.100})"]
        raw = (got or {}).get("objections") if isinstance(got, dict) else None
        return [str(o)[:300] for o in (raw or []) if str(o).strip()]

    def validate(self, request: LoopRequest) -> list[str]:
        """Problems that make the document unanswerable as written (D463): an objective on a
        metric no stage produces, or a cutoff on a metric its own stage does not measure.

        A stage with undeclared metrics (an `evaluator` stage without `metrics`) could produce
        anything, so it silences the objective check rather than failing it."""
        wrong: list[str] = []
        unknown = any((r.evaluator or not r.command) and not r.metrics for r in self.task.stages)
        produced = {m for r in self.task.stages for m in (*r.metrics_re, *r.metrics)}
        if self.task.stages and not unknown:
            for objective in self.task.objectives:
                if objective.metric not in produced:
                    wrong.append(
                        f"objective {objective.metric!r} names a metric no stage "
                        f"measures (this task measures: "
                        f"{', '.join(sorted(produced)) or 'nothing'})")
        if self.task.stages and not unknown and not self.task.world:
            # each stage ranks its own rows by the objectives (D351): a stage that lacks one has
            # no front, so nothing climbs from it and nothing is decided on it (D625)
            wanted = [o.metric for o in self.task.objectives]
            for stage in self.task.stages:
                lacks = [m for m in wanted if m not in {*stage.metrics_re, *stage.metrics}]
                if lacks:
                    wrong.append(f"the {stage.name} stage does not measure {', '.join(lacks)}: every stage must "
                                 "measure every objective, since each ranks its own results (measure them in "
                                 "one stage, or print them from each stage's command)")
        for stage in self.task.stages:
            metric = stage.cutoff.get("metric")
            mine = {*stage.metrics_re, *stage.metrics}
            if metric and mine and metric not in mine:
                wrong.append(f"the {stage.name} stage cuts on {metric!r}, which it does "
                             f"not measure (it measures: {', '.join(sorted(mine))})")
        return wrong

    def tools_missing(self) -> list[str]:
        missing = self._tools_missing()
        if self.task.generator.get("agent"):
            from .agent import missing_agent

            missing = list(missing) + [t for t in missing_agent(self.task.generator["agent"]) if t not in missing]
        return missing

    def _tools_missing(self) -> list[str]:
        missing: list[str] = []
        world_missing = getattr(self.__dict__.get("world"), "tools_missing", None)
        if callable(world_missing):
            missing.extend(world_missing())
        declared = {f"stage {r.name}" for r in self.task.stages if r.needs}   # skipped, not missing
        for _label, cmd in self.task.commands():
            head = _substitute(cmd[:1], {"python": sys.executable, "home": self.task.home or "."})[0]
            if head in ("{artifact}", "{workdir}", "{name}", "{part}"):
                continue
            found = Path(head).exists() if "/" in head else shutil.which(head) is not None
            if not found and head not in missing:
                missing.append(head)
            for tool in ([] if _label in declared else _flux_rtl_tools(cmd)):   # tools `flux rtl` runs (D600)
                if shutil.which(tool) is None and tool not in missing:
                    missing.append(tool)
        return missing

    def mentor_sections(self, state: LoopState) -> list[tuple[str, str]]:
        out = [("task", self.task.statement + ("\n\n" + self.task.contract if self.task.contract else ""))]
        if self.task.knowledge:
            out.append(("knowledge", self.task.knowledge))
        mentor = self.knowledge()
        if mentor is not None:                  # the knowledge role's sections (D462)
            out.extend(mentor.sections(state))
        return out

    def _role_knowledge(self, state: LoopState) -> str:
        """The knowledge role's text for a prompt (D462), beside the document's `knowledge`.
        Help, never a gate: an unreadable source contributes nothing."""
        mentor = self.knowledge()
        if mentor is None:
            return ""
        try:
            return mentor.prefix(state).strip()
        except Exception:  # noqa: BLE001
            return ""

    # ---- orchestrator
    def subgoals(self) -> list[str]:
        return [p.name for p in self.parts]

    def decompose(self, state: LoopState,
                  critique: str | None = None) -> list[str | SubLoop]:
        """The parts to make (D431/D455): the document's sub-tasks (each its own loop), else its
        listed `parts`, else, with "decompose", a division asked of the model.

        A model division is validated (unique identifier names, 1..max_parts) and remembered
        so a resume reuses it; no model raises. With a `critique` (D433) the previous division
        and the objection are shown and a new one is asked for; only the standing one is kept."""
        t = self.task
        if t.subtasks or t.split:
            return list(self.subproblems(state))
        if not t.decompose:
            return [p.name for p in self.parts]
        records = state.records
        earlier = records.recall("decomposition") if records is not None else []
        if earlier and critique is None:
            doc = earlier[-1]
            self.parts = tuple(Part(p["name"], p.get("statement", "")) for p in doc["parts"])
            state.say(f"decompose: {len(self.parts)} part(s) resumed from the record: "
                      + ", ".join(p.name for p in self.parts))
            return [p.name for p in self.parts]
        if state.proposer is None:
            raise RuntimeError(f"task {t.id} asks to be decomposed but no model is available")
        from .model import _ask, _json

        objection = ""
        if critique:
            previous = "; ".join(f"{p.name}: {p.statement}" for p in self.parts)
            objection = (f"Your previous division was: {previous}\n\nA critic objected: "
                         f"{critique}\n\nDivide it again, answering the objection.")
        prompt = "\n\n".join(x for x in (
            f"TASK {t.id}: {t.statement}",
            f"CONTRACT:\n{t.contract}" if t.contract else "",
            f"KNOWLEDGE:\n{t.knowledge}" if t.knowledge else "",
            objection,
            f"Divide this task into 1 to {t.max_parts} parts that can be written and checked "
            "one at a time and then joined in order into the whole. Each part has a short "
            "identifier name (letters, digits, underscores) and a one-line statement of exactly "
            "what it must contain. Fewer parts is better when the task is small.",
            'Reply with ONLY JSON: {"parts": [{"name": "<identifier>", "statement": "<one line>"}], '
            '"why": "<one line>"}') if x)
        schema = {"type": "object",
                  "properties": {"parts": {"type": "array", "minItems": 1, "maxItems": t.max_parts,
                                           "items": {"type": "object",
                                                     "properties": {"name": {"type": "string"},
                                                                    "statement": {"type": "string"}},
                                                     "required": ["name", "statement"]}},
                                 "why": {"type": "string"}},
                  "required": ["parts"]}
        reply = _ask(state, prompt, schema).text
        doc = _json(reply)
        why = self._check_decomposition(doc)
        if why:
            raise RuntimeError(f"task {t.id}: the decomposition was refused: {why}")
        self.parts = tuple(Part(str(p["name"]).strip(), str(p.get("statement") or "").strip())
                           for p in doc["parts"])
        self._division_why = str(doc.get("why") or "")[:200]
        # Remember it when it stands: at once without a critic or when answering a critique
        # (the last remembered wins on resume), otherwise on the critic's acceptance.
        if critique is not None or not (t.critique and state.request.critique_rounds > 0):
            self._remember_division(state)
        state.say(f"decompose: {len(self.parts)} part(s): " + ", ".join(p.name for p in self.parts))
        return [p.name for p in self.parts]

    def _remember_division(self, state: LoopState) -> None:
        if state.records is not None:
            state.records.remember("decomposition", {
                "parts": [{"name": p.name, "statement": p.statement} for p in self.parts],
                "why": getattr(self, "_division_why", "")})

    def critique(self, kind: str, subject: Any, state: LoopState) -> Verdict:
        """The model as critic (D433) of a division, a gate-passed candidate, or the decision.

        The verdict is remembered, and an accepted division is remembered as the division.
        Without `"critique": "propose"` or a model, everything passes."""
        t = self.task
        if not t.critique or state.proposer is None:
            if kind == "decomposition" and t.decompose and not getattr(self, "_division_kept", False):
                self._division_kept = True
                self._remember_division(state)
            return Verdict(True, 0.0, "")
        from .model import _ask, _json

        if kind == "decomposition":
            shown = "\n".join(f"- {p.name}: {p.statement}" for p in self.parts)
            what = (f"THE DIVISION INTO PARTS (to be written one at a time and joined in order):\n{shown}\n\n"
                    "Object only to a division that cannot produce the whole: a missing piece, an "
                    "overlap, a part that cannot be checked on its own, an order that cannot be joined.")
            label = ", ".join(p.name for p in self.parts)
        elif kind == "candidate":
            cand: Candidate = subject
            numbered = "\n".join(f"{i + 1:4d} | {ln}" for i, ln in enumerate(cand.artifact.splitlines()))
            part = self._part(cand.subgoal)
            what = ((f"PART {part.name}: {part.statement}\n\n" if part else "")
                    + f"THE CANDIDATE, which the gate has ALREADY PASSED:\n\n{numbered}\n\n"
                    "Object only to something the gate cannot see and the contract requires: a "
                    "violated constraint, a fragile construction, a mismatch with the statement. "
                    "Passing the gate is not an issue.")
            label = cand.name
        else:
            pick: Scored = subject
            metrics = ", ".join(f"{k}={v:g}" for k, v in pick.metrics.items())
            what = (f"THE DECISION: {pick.name} on stage {pick.stage} with {metrics}. "
                    "Object only if the objectives or the statement point elsewhere.")
            label = pick.name
        gate = " ".join(t.gate.test or t.gate.build or ())
        prompt = "\n\n".join(x for x in (
            f"TASK {t.id}: {t.statement}",
            f"CONTRACT:\n{t.contract}" if t.contract else "",
            f"HOW IT IS JUDGED: `{gate}`; zero failures admits." if gate else "",
            "You are the critic. Your job is to find what is WRONG, precisely and briefly; a "
            "verdict without a concrete issue is worthless, and so is an issue the gate already "
            "covers.",
            what,
            'Reply with ONLY JSON: {"ok": true|false, "issues": ["<one concrete issue each>"], '
            '"why": "<one line>"}') if x)
        schema = {"type": "object",
                  "properties": {"ok": {"type": "boolean"},
                                 "issues": {"type": "array", "items": {"type": "string"}},
                                 "why": {"type": "string"}},
                  "required": ["ok"]}
        try:
            doc = _json(_ask(state, prompt, schema).text)
        except Exception as exc:  # noqa: BLE001 -- a critic that cannot speak does not veto
            state.say(f"  critic did not answer ({exc!s:.80})")
            doc = None
        issues = [str(i) for i in (doc.get("issues") or [])] if isinstance(doc, dict) else []
        ok = bool(doc.get("ok", True)) if isinstance(doc, dict) else True
        if ok or not issues:
            ok, why = True, ""
        else:
            why = "; ".join(issues)[:600]
        if state.records is not None:
            state.records.remember("critique", {"kind": kind, "subject": label, "ok": ok, "why": why})
        if kind == "decomposition" and ok:
            self._division_kept = True
            self._remember_division(state)
        return Verdict(ok, 0.0 if ok else 1.0, why, {"issues": issues})

    def plan_part(self, subgoal: str | None, state: LoopState) -> dict[str, Any]:
        """The part's brief (D432): with `"brief": "propose"` the model writes it and may set the
        repair budget (clamped to twice the request's). Remembered for resume; empty with no
        model."""
        t = self.task
        if not t.brief:
            return {}
        key = subgoal or "*"
        records = state.records
        earlier = [d for d in (records.recall("brief") if records is not None else [])
                   if d.get("part") == key]
        if earlier:
            state.say(f"brief for {subgoal or t.id}: resumed from the record")
            return {k: v for k, v in earlier[-1].items() if k in ("brief", "repair_attempts")}
        if state.proposer is None:
            return {}
        from .model import _ask, _json

        part = self._part(subgoal)
        gate = " ".join(t.gate.test or t.gate.build or ())
        prompt = "\n\n".join(x for x in (
            f"TASK {t.id}: {t.statement}",
            f"PART {part.name}: {part.statement}" if part is not None else "",
            f"CONTRACT:\n{t.contract}" if t.contract else "",
            f"KNOWLEDGE:\n{t.knowledge}" if t.knowledge else "",
            f"HOW IT IS JUDGED: the gate runs `{gate}` and counts failures; zero admits." if gate else "",
            "You are briefing the writer of this part. Write a BRIEF of at most 12 lines: exactly "
            "what the part must contain, the constraints most likely to be missed, what the gate "
            "will check, and the smallest correct approach. Do not write the part itself. Then "
            f"say how many repair attempts it deserves (1 to {2 * state.request.repair_attempts}; "
            f"{state.request.repair_attempts} is the default).",
            'Reply with ONLY JSON: {"brief": "<text>", "repair_attempts": <int>, "why": "<one line>"}',
        ) if x)
        schema = {"type": "object",
                  "properties": {"brief": {"type": "string"},
                                 "repair_attempts": {"type": "integer", "minimum": 1},
                                 "why": {"type": "string"}},
                  "required": ["brief"]}
        doc = _json(_ask(state, prompt, schema).text)
        if not isinstance(doc, dict) or not isinstance(doc.get("brief"), str) or not doc["brief"].strip():
            state.say(f"brief for {subgoal or t.id}: the reply carried no brief; the statement is the brief")
            return {}
        plan: dict[str, Any] = {"brief": doc["brief"].strip()[:2000]}
        ra = doc.get("repair_attempts")
        if isinstance(ra, int) and ra > 0:
            plan["repair_attempts"] = max(1, min(ra, 2 * state.request.repair_attempts))
        if records is not None:
            records.remember("brief", {"part": key, **plan, "why": str(doc.get("why") or "")[:200]})
        state.say(f"brief for {subgoal or t.id}: {len(plan['brief'].splitlines())} line(s)"
                  + (f", {plan['repair_attempts']} repair attempts" if "repair_attempts" in plan else ""))
        return plan

    def _check_decomposition(self, doc: Any) -> str:
        if not isinstance(doc, dict) or not isinstance(doc.get("parts"), list) or not doc["parts"]:
            return "the reply carried no parts"
        if len(doc["parts"]) > self.task.max_parts:
            return f"{len(doc['parts'])} parts, at most {self.task.max_parts} allowed"
        names = []
        for p in doc["parts"]:
            name = str((p or {}).get("name") or "").strip() if isinstance(p, dict) else ""
            if not name or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
                return f"part name {name!r} is not an identifier"
            if name in names:
                return f"part name {name!r} repeats"
            names.append(name)
        return ""


    # ---- generator
    def _part(self, subgoal: str | None) -> Part | None:
        return next((p for p in self.parts if p.name == subgoal), None)

    def prompt_prefix(self, subgoal: str | None, state: LoopState) -> str:
        t = self.task
        lines = [f"TASK {t.id}: {t.statement}"]
        part = self._part(subgoal)
        if part is not None:
            lines.append(f"PART {part.name}" + (f": {part.statement}" if part.statement else "")
                         + " -- this turn is about this part only.")
        if t.contract:
            lines.append(f"CONTRACT:\n{t.contract}")
        if t.knowledge:
            lines.append(f"KNOWLEDGE:\n{t.knowledge}")
        role = self._role_knowledge(state)      # the knowledge role's text, if on (D462)
        if role:
            lines.append(role)
        lines.append(
            f'REPLY SHAPE: reply with ONLY JSON: {{"artifact": "<the complete {t.language} '
            'text>", "why": "<one line>"}. When asked for edits, reply {"edits": [{"find": '
            '"...", "replace": "..."}], "why": "..."} instead.')
        return "\n\n".join(lines)

    def design_prompt(self, subgoal: str | None, method: str, state: LoopState,
                      human: str | None, prior: Candidate | None, prior_why: str
                      ) -> tuple[str, dict | None]:
        target = f"part {subgoal}" if subgoal else f"task {self.task.id}"
        parts = [human or ""]
        if prior is not None:
            numbered = "\n".join(f"{i + 1:4d} | {ln}" for i, ln in enumerate(prior.artifact.splitlines()))
            parts += [f"Your previous attempt for {target} was refused:\n\n{prior_why}",
                      "Rework it, or send a new one if the approach itself is wrong.",
                      f"Previous attempt (line numbers for reading only):\n\n{numbered}"]
        else:
            parts.append(f"Write {target} now" + (f" ({method})" if method else "") + ".")
        schema = {"type": "object",
                  "properties": {"artifact": {"type": "string"}, "why": {"type": "string"}},
                  "required": ["artifact"]}
        return "\n\n".join(p for p in parts if p), schema

    def parse_design(self, reply: str, subgoal: str | None) -> tuple[Candidate | None, str]:
        doc = _json(reply)
        artifact: str | None = None
        if isinstance(doc, dict) and isinstance(doc.get("artifact"), str) and doc["artifact"].strip():
            artifact = doc["artifact"]
        elif not reply.lstrip().startswith("{"):
            try:
                from flux_llm import strip_markdown_fence
            except Exception:  # noqa: BLE001
                strip_markdown_fence = lambda t: t  # noqa: E731
            text = strip_markdown_fence(reply)
            if text.strip():
                artifact = text
        if artifact is None:
            return None, "the reply carried no artifact"
        self._count += 1
        return Candidate(f"{subgoal or self.task.id}#{self._count}", artifact,
                         knobs={"task": self.task.id, "part": subgoal or ""}, subgoal=subgoal), ""

    # ---- evaluator
    def _subs(self, cand: Candidate, subgoal: str | None, state: LoopState) -> dict[str, str]:
        workdir = Path(state.workdir or ".")
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", cand.name)
        path = workdir / f"{safe}{self.task.extension}"
        path.write_text(cand.artifact)
        return {**_knob_subs(cand.knobs), "artifact": str(path), "workdir": str(workdir), "name": cand.name,
                "point": _write_point(path, cand.knobs or {}),
                "part": subgoal or "", "python": sys.executable, "home": self.task.home or "."}

    def _run(self, cmd: tuple[str, ...], subs: dict[str, str], timeout_s: float, what: str):
        from flux_evaluator_abi.tools import run_tool

        return run_tool(_substitute(cmd, subs), cwd=subs["workdir"], timeout_s=timeout_s, what=what)

    def _count_failures(self, run: Any) -> tuple[int, str]:
        out = (run.stdout or "") + ("\n" + run.stderr if run.stderr else "")
        g = self.task.gate
        fails: int | None = None
        if g.count_re:
            m = re.search(g.count_re, out)
            fails = int(m.group(1)) if m else None
        elif g.fail_re:
            fails = len(re.findall(g.fail_re, out))
            if fails == 0 and not run.ok:
                fails = None
        if fails is None:
            fails = 0 if run.ok else 1
        text = out.strip()[-4000:] or (f"exit {run.returncode}" if not run.ok else "")
        return fails, text

    def build(self, cand: Candidate, subgoal: str | None, state: LoopState) -> Any:
        subs = self._subs(cand, subgoal, state)
        if self.task.gate.build:
            run = self._run(self.task.gate.build, subs, self.task.gate.timeout_s, "build")
            if not run.ok:
                raise BuildError(((run.stdout or "") + "\n" + (run.stderr or "")).strip()[-4000:]
                                 or f"build exited {run.returncode}")
        elif self.task.gate.test:
            # No build command: run the test once here; exit 3 means it did not build (D594).
            # fast_check reuses this run.
            run = self._run(self.task.gate.test, subs, self.task.gate.timeout_s, "test")
            if run.returncode == BUILD_FAILED:
                raise BuildError(((run.stdout or "") + "\n" + (run.stderr or "")).strip()[-4000:])
            self.__dict__.setdefault("_tested", {})[_digest_of(cand.artifact)] = run
        return subs["artifact"]

    def fast_check(self, built: Any, cand: Candidate, subgoal: str | None,
                   state: LoopState) -> tuple[int, str]:
        if not self.task.gate.test:
            return 0, ""
        run = self.__dict__.get("_tested", {}).pop(_digest_of(cand.artifact), None)   # build ran it (D594)
        if run is None:
            subs = self._subs(cand, subgoal, state)
            run = self._run(self.task.gate.test, subs, self.task.gate.timeout_s, "test")
        return self._count_failures(run)

    def judge(self, built: Any, cand: Candidate, subgoal: str | None, state: LoopState) -> Verdict:
        fails, text = self.fast_check(built, cand, subgoal, state)
        return Verdict(fails == 0, float(fails), text if fails else "", {"failures": fails})

    # ------------------------------------------------------------ who drafts (D456)
    def generator(self, subgoal: str | None, state: LoopState):
        """Who drafts: the model, unless the document names a command, a catalog of existing
        designs, or a coding agent. The generate/build/fast-check sub-loop is the loop's."""
        from .sources import Catalog, Template

        chosen = self.roles().generator
        if chosen is not None:
            return chosen                 # a rig (a flag, a caller) named the generator
        spec = self.task.generator
        if not spec:
            return None
        if "catalog" in spec:
            return Catalog(list(spec["catalog"]), self._from_catalog)
        if "agent" in spec:
            from .agent import agent_spec

            agent = agent_spec(spec["agent"])
            return Template(lambda attempt: self._agent_draft(attempt, agent), name=f"agent:{agent.tool}")
        command = tuple(spec["command"])
        return Template(lambda attempt: self._rendered(attempt, command))

    def _agent_draft(self, attempt: Any, agent: Any):
        """A coding agent's turn (D575): brief on disk, agent run in the work directory, its
        questions answered by the document's policy (D585). The candidate is the file it wrote,
        or its printed reply parsed like a model's."""
        from dataclasses import asdict

        from .agent import agent_brief, converse

        state = attempt.state
        workdir = Path(state.workdir or ".")
        workdir.mkdir(parents=True, exist_ok=True)
        self._count += 1
        sg = attempt.subgoal
        name = f"{sg or _leaf(self.task.id)}#{self._count}"
        safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', name)
        path = workdir / f"draft-{safe}{self.task.extension}"
        if attempt.prior is not None and attempt.failure:
            body, _schema = self.rewrite_prompt(sg, attempt.prior, attempt.failure, state)
        else:
            body, _schema = self.design_prompt(sg, "", state, None, attempt.prior, attempt.failure)
        gate = self.task.gate                          # the agent can run the gate it is judged by (D595)
        check_subs = {"artifact": str(path), "workdir": str(workdir), "name": name, "part": sg or "",
                      "python": sys.executable, "home": str(Path(self.task.home or ".").resolve())}
        check = " && ".join(shlex.join(_substitute(c, check_subs)) for c in (gate.build, gate.test) if c)
        brief = agent_brief(body=body, prefix=self.prompt_prefix(sg, state) or "", artifact=path, workdir=workdir,
                            language=self.task.language or "text", part=sg or self.task.id,
                            prior=attempt.prior.artifact if attempt.prior is not None else None, failure=attempt.failure,
                            questions=agent.questions, check=check)
        prompt_file = workdir / f"PROMPT-{safe}.md"
        prompt_file.write_text(brief)
        subs = {"prompt": brief, "prompt_file": str(prompt_file), "artifact": str(path), "workdir": str(workdir),
                "part": sg or "", "name": name, "python": sys.executable, "home": self.task.home or "."}
        if self.skill_list():                          # install skills where the agent looks (D588)
            from .skills import install

            install(self.skill_list(), workdir)
        turn, asked = converse(agent, subs, workdir=workdir, artifact=path, prompt_file=prompt_file,
                               answer=self._agent_answerer(agent, brief, state), say=state.say)
        knobs = {"task": self.task.id, "part": sg or "", "generator": f"agent:{agent.tool}"}
        meta = {"questions": [asdict(e) for e in asked]} if asked else {}
        if path.is_file():
            return Candidate(name, path.read_text(), knobs=knobs, meta=meta, subgoal=sg), ""
        from .agent import question_in

        if turn.ok and turn.text.strip() and question_in(turn.text) is None:
            cand, why = self.parse_design(turn.text, sg)          # the agent printed the artifact instead
            if cand is not None:
                return Candidate(name, cand.artifact, knobs=knobs, meta=meta, subgoal=sg), ""
        tail = ((turn.text or turn.stdout or "") + "\n" + (turn.stderr or "")).strip()[-2000:]
        still = (f" (still asking after {len(asked)} answer(s))" if question_in(turn.text) is not None and asked
                 else " (it asked, and its questions are answered by nobody here)" if question_in(turn.text) is not None else "")
        return None, (f"the coding agent {agent.tool} exited {turn.rc} and wrote no {path.name}{still}"
                      + (f": {tail}" if tail else ""))

    def prototype_agent(self) -> Any | None:
        """The coding agent that writes the prototype (D618), when `flow.generate: {agent: ...}`
        is set and a Python prototype stage exists; the loop spells the RTL from its output."""
        if not self.task.generator.get("agent"):
            return None
        cap = self.prototype()
        if cap is None or getattr(cap, "language", "") != "python":
            return None
        from .agent import agent_spec

        return agent_spec(self.task.generator["agent"])

    def prototype_agent_turn(self, agent: Any, prompt: str, code: str | None, failure: str,
                             subgoal: str | None, state: LoopState) -> str:
        """One agent turn on the prototype (D618): it edits a file and runs `flux rtl proto` (the
        stage's own check) until it passes. Returns the file as a `{"prototype": ...}` reply,
        or "" when nothing new was written."""
        from .agent import agent_brief, converse
        from .golden_proto import TABLE_MAX, golden_path

        workdir = Path(state.workdir or ".")
        workdir.mkdir(parents=True, exist_ok=True)
        self._count += 1
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{subgoal or _leaf(self.task.id)}_{self._count}")
        path = workdir / f"prototype-{safe}.py"
        if code:
            path.write_text(code)
        from .document import _command

        check = shlex.join(_substitute(_command(
            ["flux", "rtl", "proto", str(path), "--golden", str(golden_path(self.task)),
             "--table-max", str(int(self.task.budget.get("prototype_table_max") or TABLE_MAX))], "the prototype check") or (),
            {"python": sys.executable}))
        brief = agent_brief(body=prompt, prefix="", artifact=path, workdir=workdir, language="Python",
                            part=f"{subgoal or self.task.id} (the prototype `design(...)`)", prior=None,
                            failure=failure, questions=agent.questions, check=check)
        prompt_file = workdir / f"PROMPT-{safe}.md"
        prompt_file.write_text(brief)
        subs = {"prompt": brief, "prompt_file": str(prompt_file), "artifact": str(path), "workdir": str(workdir),
                "part": subgoal or "", "name": safe, "python": sys.executable, "home": self.task.home or "."}
        turn, _asked = converse(agent, subs, workdir=workdir, artifact=path, prompt_file=prompt_file,
                                answer=self._agent_answerer(agent, brief, state), say=state.say)
        text = path.read_text() if path.is_file() else ""
        if not text.strip() or (code and text == code):
            tail = ((turn.text or turn.stdout or "") + "\n" + (turn.stderr or "")).strip()[-400:]
            state.say(f"  prototype {subgoal or self.task.id}: the coding agent {agent.tool} exited {turn.rc} "
                      f"and left no new prototype" + (f": {tail}" if tail else ""))
            return ""
        return json.dumps({"prototype": text, "why": (turn.text or "")[-600:]})

    def _agent_answerer(self, agent: Any, brief: str, state: LoopState):
        """Who answers the agent's questions (D585), as the document's `questions:` allows: the
        operator within `wait_s`, then the loop's model as the brief's author, else "decide"."""
        from .agent import DECIDE

        def answer(question: str) -> tuple[str, str]:
            if agent.questions == "operator" and state.feedback is not None:
                import time

                state.say(f"QUESTION from the coding agent (answer at the prompt line within {agent.wait_s:.0f}s):\n{question}")
                until = time.monotonic() + agent.wait_s
                while time.monotonic() < until:
                    before = len(state.human_notes)
                    state.drain()                       # records the note as every operator line is (D388)
                    fresh = [n.text for n in state.human_notes[before:] if str(n.text).strip()]
                    if fresh:
                        return "\n".join(fresh).strip(), "operator"
                    time.sleep(2.0)
            if agent.questions in ("model", "operator") and state.proposer is not None:
                from .model import _ask

                prompt = ("A coding agent working from the brief below stopped to ask a question. Answer it as the "
                          "designer who wrote the brief: short and decisive. Where the brief does not settle it, "
                          "choose what is most likely to pass the gate and say why in one line.\n\nTHE BRIEF:\n"
                          + brief[-12000:] + "\n\nTHE AGENT ASKS:\n" + question)
                try:
                    text = (_ask(state, prompt).text or "").strip()
                except Exception as exc:  # noqa: BLE001 -- a failed answer is "decide", never a crash
                    state.say(f"  the model could not answer the agent ({exc!s:.100})")
                    text = ""
                if text:
                    return text, "model"
            return DECIDE, "decide"

        return answer

    def _from_catalog(self, item: Any, attempt: Any):
        """One catalog entry as a candidate: a path whose text is the design."""
        from dataclasses import replace

        from .sources import from_file

        self._count += 1
        cand, why = from_file(item, attempt,
                              name=f"{_leaf(self.task.id)}#{self._count}")
        if cand is None:
            return None, why
        return replace(cand, knobs={**cand.knobs, "task": self.task.id,
                                                "part": attempt.subgoal or ""}), ""

    def _rendered(self, attempt: Any, command: tuple[str, ...]):
        """Run the document's generator command: it writes `{artifact}` and is given `{failure}`
        (why the last draft was refused) and `{attempt}`."""
        state = attempt.state
        workdir = Path(state.workdir or ".")
        self._count += 1
        name = f"{attempt.subgoal or _leaf(self.task.id)}#{self._count}"
        path = workdir / f"draft-{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}{self.task.extension}"
        subs = {"artifact": str(path), "workdir": str(workdir), "name": name,
                "part": attempt.subgoal or "", "python": sys.executable, "home": self.task.home or ".",
                "failure": attempt.failure, "attempt": str(attempt.index + 1)}
        run = self._run(command, subs, self.task.gate.timeout_s, "generate")
        if not run.ok:
            text = ((run.stdout or "") + "\n" + (run.stderr or "")).strip()[-2000:]
            return None, text or f"the generator command exited {run.returncode}"
        if not path.is_file():
            return None, (f"the generator command exited 0 but wrote no {path.name}; it must "
                          f"write the artifact to {{artifact}}")
        return Candidate(name, path.read_text(),
                         knobs={"task": self.task.id, "part": attempt.subgoal or "",
                                "generator": "command"}, subgoal=attempt.subgoal), ""

    def subproblems(self, state: LoopState) -> list[SubLoop]:
        """The document's sub-tasks, each its own loop (D455).

        Either nested documents, or with `"subtasks": "decompose"` a split asked of the model:
        children inherit this task's contract, gate, stages and objectives with their own
        statement. An asked-for split is remembered so a resume reuses it.
        """
        t = self.task
        if t.subtasks:
            return [SubLoop(name=_leaf(c.id), problem=PromptProblem(c, roles=self._caller_roles), statement=c.statement)
                    for c in t.subtasks]
        if not t.split:
            return []
        if self._children is None:
            self._children = self._ask_for_subtasks(state)
        return [SubLoop(name=_leaf(c.id), problem=PromptProblem(c, roles=self._caller_roles), statement=c.statement)
                for c in self._children]

    def _ask_for_subtasks(self, state: LoopState) -> tuple["TaskSpec", ...]:
        t = self.task
        records = state.records
        earlier = records.recall("subtasks") if records is not None else []
        if earlier:
            named = [(str(c["name"]), str(c.get("statement") or "")) for c in earlier[-1]["subtasks"]]
            state.say(f"subtasks: {len(named)} resumed from the record: "
                      + ", ".join(n for n, _s in named))
            return self._children_from(named)
        if state.proposer is None:
            raise RuntimeError(f"task {t.id} asks to be split into sub-tasks but no model is "
                               "available to divide it")
        from .model import _ask, _json

        prompt = "\n\n".join(x for x in (
            f"TASK {t.id}: {t.statement}",
            f"CONTRACT:\n{t.contract}" if t.contract else "",
            f"KNOWLEDGE:\n{t.knowledge}" if t.knowledge else "",
            f"Divide this into 1 to {t.max_subtasks} SUB-TASKS. A sub-task is not a piece of one "
            "artifact: it is a problem of its own, designed, built and judged on its own, whose "
            "answer the others do not contain. Each has a short identifier name (letters, "
            "digits, underscores) and a one-line statement of exactly what it must answer. "
            "Fewer is better; one means the task should not be split at all.",
            'Reply with ONLY JSON: {"subtasks": [{"name": "<identifier>", "statement": '
            '"<one line>"}], "why": "<one line>"}') if x)
        schema = {"type": "object",
                  "properties": {"subtasks": {
                      "type": "array", "minItems": 1, "maxItems": t.max_subtasks,
                      "items": {"type": "object",
                                "properties": {"name": {"type": "string"},
                                               "statement": {"type": "string"}},
                                "required": ["name", "statement"]}},
                      "why": {"type": "string"}},
                  "required": ["subtasks"]}
        doc = _json(_ask(state, prompt, schema).text)
        named: list[tuple[str, str]] = []
        if isinstance(doc, dict):
            for child in doc.get("subtasks") or ():
                name = str(child.get("name") or "").strip()
                if name.isidentifier() and name not in {n for n, _s in named}:
                    named.append((name, str(child.get("statement") or "").strip()))
        if not named:
            raise RuntimeError(f"task {t.id}: the split into sub-tasks was refused: no usable "
                               f"names in {str(doc)[:200]}")
        state.say(f"subtasks: {len(named)}: " + ", ".join(n for n, _s in named))
        if records is not None:
            records.remember("subtasks", {
                "subtasks": [{"name": n, "statement": st} for n, st in named],
                "why": str(doc.get("why") or "")[:200] if isinstance(doc, dict) else ""})
        return self._children_from(named)

    def _children_from(self, named: list[tuple[str, str]]) -> tuple["TaskSpec", ...]:
        """One child spec per sub-task: this task, restated, and never splitting again."""
        from dataclasses import replace

        return tuple(replace(self.task, id=f"{self.task.id}/{name}", statement=statement,
                             subtasks=(), split=False, parts=(),
                             decompose=self.task.decompose)
                     for name, statement in named)

    def compose(self, admitted: dict[str, Candidate], state: LoopState) -> Candidate | None:
        if self.task.subtasks or self.task.split:
            # the sub-loops' decisions, joined in declared order (D455)
            names = [_leaf(c.id) for c in (self.task.subtasks or self._children or ())]
            ordered = [admitted[n] for n in names if n in admitted]
            if not ordered or len(ordered) != len(names):
                return None
            return Candidate(self.task.id, self.task.joiner.join(c.artifact for c in ordered),
                             knobs={"task": self.task.id, "subtasks": names})
        if not self.parts:
            return super().compose(admitted, state)
        ordered = [admitted[p.name] for p in self.parts if p.name in admitted]
        if len(ordered) != len(self.parts):
            return None
        return Candidate(self.task.id, self.task.joiner.join(c.artifact for c in ordered),
                         knobs={"task": self.task.id, "parts": [c.name for c in ordered]})

    def cutoff(self, stage: str, scored, state):
        """The stage's declared cutoff (D454): a floor, a budget or a band around this run's best.
        Without one, every measured candidate goes on."""
        from .cutoff import above, below, within_best

        mine = self.role_cutoff(stage, scored, state)
        if mine is not None:
            return mine                       # the evaluation component's own stage (D461)
        spec = next((r for r in self.task.stages if r.name == stage), None)
        rule = dict(spec.cutoff) if spec is not None else {}
        metric = rule.get("metric")
        if not metric:
            return list(scored)
        if "at" in rule:
            return above(scored, metric, float(rule["at"]))
        if "below" in rule:
            return below(scored, metric, float(rule["below"]))
        direction = {o.metric: o.direction for o in self.task.objectives}.get(metric, "maximize")
        return within_best(scored, metric, float(rule["within"]),
                           higher_is_better=direction != "minimize")

    def skipped_stages(self) -> list[tuple[str, list[str]]]:
        """The document's stages that will not run here, with the missing tools they need (D590).
        Reported by `task check`, at run start and in the report, so no stage is dropped silently."""
        return [(r.name, [t for t in r.needs if not shutil.which(t)])
                for r in self.task.stages if not all(shutil.which(t) for t in r.needs)]

    def stages(self) -> list[str]:
        """The document's stages whose `needs` are on PATH, plus any the evaluation component
        adds below them (D461)."""
        mine = [r.name for r in self.task.stages if all(shutil.which(t) for t in r.needs)]
        return self.chained(mine or [StageNames.GATE])

    def measure(self, cand: Candidate, stage: str, state: LoopState) -> dict[str, float] | None:
        predicted = self.role_measure(cand, stage, state)
        if predicted is not None:
            return predicted                  # the evaluation component's own stage (D461)
        spec = next((r for r in self.task.stages if r.name == stage), None)
        if spec is None:
            return {"failures": 0.0} if stage == StageNames.GATE else None
        if not spec.command and not spec.evaluator:
            state.say(f"  stage {stage}: the world names no way to measure it")
            return None
        over = self._over_ceiling(cand, state)
        if over:
            return {"error": over}            # over the cost ceiling: not synthesised (D615)
        if spec.command:
            subs = self._subs(cand, None, state)
            run = self._run(spec.command, subs, spec.timeout_s, f"stage {stage}")
            out = (run.stdout or "") + "\n" + (run.stderr or "")
            got: dict[str, float] = {}
            for metric, pat in spec.metrics_re.items():
                m = re.search(pat, out)
                if m:
                    try:
                        got[metric] = float(m.group(1))
                    except ValueError:
                        pass
            if not got:
                state.say(f"  stage {stage}: no metric matched in the output")
                return None
            return got
        try:
            from flux_evaluator_abi import Budget, Candidate as AbiCandidate, make_evaluator

            ev = make_evaluator(spec.evaluator or "")
            arch = _document(cand.artifact)
            workload = self.task.workload
            if isinstance(workload, str) and Path(workload).exists():
                workload = _document(Path(workload).read_text())
            result = ev.evaluate(AbiCandidate(workload=workload, arch=arch), Budget(),
                                 frozenset(spec.metrics) if spec.metrics else frozenset())
            return {k: float(v.value) for k, v in result.metrics.items()
                    if not spec.metrics or k in spec.metrics}
        except Exception as exc:  # noqa: BLE001
            state.say(f"  stage {stage} ({spec.evaluator}) could not measure: {exc!s:.120}")
            return None

    def cache_suffix(self) -> str | None:
        """The document's `cache:` (D541): the loop's sidecar, none, or a name."""
        cache = self.task.cache
        if cache is False:
            return None
        return cache if isinstance(cache, str) else f"{self.task.id}.json"


def _document(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        import yaml

        return yaml.safe_load(text)


# ------------------------------------------------------------------ the report
def task_report_lines(task: TaskSpec, out: Any, problem: Any = None) -> list[str]:
    """The standard report for a task run: the decision and its metrics, the world's
    `report(out)` lines if any, the frontier, then the shared closing sections (D558)."""
    lines = [f"TASK {task.id}: {task.statement[:100]}"]
    d = out.decision
    if d is not None:
        metrics = ", ".join(f"{k}={v:g}" for k, v in d.metrics.items())
        lines.append(f"  DECISION {d.name} [{d.stage}; {out.decided_by}]" + (f": {metrics}" if metrics else ""))
    else:
        lines.append("  NO CANDIDATE SURVIVED -- see NOT ESTABLISHED below")
    report = getattr(getattr(problem, "world", None), "report", None)
    if callable(report):
        try:
            lines.extend(report(out))
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  (the world's report could not be made: {exc!s:.120})")
    pool = out.confirmed or out.frontier
    if len(pool) > 1:
        lines.append(f"  frontier ({len(pool)} point(s)):")
        for p in pool:
            lines.append("    " + p.name + ": " + ", ".join(f"{k}={v:g}" for k, v in p.metrics.items()))
    if out.admitted:
        lines.append("  proven: " + ", ".join(f"{k}={c.name}" for k, c in sorted(out.admitted.items())))
    skipped = problem.skipped_stages() if callable(getattr(problem, "skipped_stages", None)) else []
    for name, tools in skipped:
        lines.append(f"  NOT RUN: stage {name} -- needs {', '.join(tools)}, not on PATH; every number above "
                     "is from the stages before it")
    try:
        from .report import established, not_established, notes, refused

        for block in (established(out.lessons), not_established(out.not_established),
                      refused(out.refused, render=lambda r: f"{r[0]}: {r[1]}"), notes(out.notes)):
            if block:
                lines += [""] + list(block)
    except Exception:  # noqa: BLE001
        lines += [f"  {ln}" for ln in out.lessons]
    return lines


def model_use(task: "TaskSpec") -> str:
    """Why this document needs a model, or "" when it does not (D608), so a banner names a
    model only when one is used. A world may call one in ways the document does not show."""
    if task.world:
        return "its world may ask one"
    flow = dict(task.flow or {})
    gen = dict(task.generator or {})
    reasons = []
    if not gen and not task.space:
        reasons.append("it writes the candidates")
    phases = flow.get("dse")
    specs = phases if isinstance(phases, list) else [phases] if phases else []
    if any((s if isinstance(s, str) else (s or {}).get("policy", "")) in ("llm", "model") for s in specs):
        reasons.append("a search phase asks it for points")
    for box in ("plan", "critique", "validate"):
        if flow.get(box) == "llm":
            reasons.append(f"{box}: llm")
    orch = (task.roles or {}).get("orchestrator")
    if orch in ("llm", "model", "agent") or (isinstance(orch, dict) and set(orch) & {"llm", "model", "agent"}):
        reasons.append(f"the orchestrator is the {orch if isinstance(orch, str) else next(iter(orch))}")
    return "; ".join(reasons)
