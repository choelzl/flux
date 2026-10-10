"""`PromptProblem`'s measuring (D454, D461, D562, D665): the document's objectives and the margins
learned on cheaper stages, its stages and their cutoffs, measuring and estimating a candidate, and
what makes a measurement the same one -- the cache key, the inputs digest and the judge's versions
(D510, D778, D853). A mixin of `flux_loop.task.PromptProblem` (D891).
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from .objective import Objectives
from .metrics import numeric_metrics
from .types import Candidate, LoopState, StageNames


class MeasureMixin:
    """The objectives, the stages and the measurements, for `PromptProblem` (D891)."""

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

    def versions(self) -> dict[str, str]:
        """D778: a document's judge, as a version (D510) -- its gate (`flow.test`), the files beside
        it the gate names (a golden model, a checker), the measuring tools and this Flux. A design
        admitted under the same judge is kept as it stands at a reload; any of them changed, it is
        re-verified once and recorded again. A world's own `versions` replaces this one."""
        if "_judge" not in self.__dict__:
            import hashlib

            from flux_loop.toolchain import toolchain_fingerprint

            from .provenance import git_revision

            gate = (self.task.to_dict().get("flow") or {}).get("test")
            home = Path(self.task.home) if self.task.home else None
            files = {}
            for rel in sorted(set(re.findall(r"\{home\}/([^\s\"']+)", json.dumps(gate, default=str)))):
                path = home / rel if home is not None else None
                files[rel] = (hashlib.sha256(path.read_bytes()).hexdigest()[:16]
                              if path is not None and path.is_file() else "missing")
            # D853: and every input of the loop with its params -- a checker's imported helper, its
            # data, `{params}` changed, an admission is re-checked
            said = json.dumps({"gate": gate, "files": files, "tools": toolchain_fingerprint(), "flux": git_revision(),
                               "inputs": self.inputs()}, sort_keys=True, default=str)
            self._judge = hashlib.sha256(said.encode()).hexdigest()[:16] if gate else ""
            self._spells = self._transpiler()
        out = {"judge": self._judge} if self._judge else {}
        return {**out, "transpiler": self._spells} if out and self._spells else out

    def inputs(self) -> str:
        """The fingerprint of what this loop's evidence is made from (D853, `flux_loop.inputs`): its
        input files and params -- looked at most every few seconds, a measurement key per candidate
        not walking the folder each time."""
        import time

        from .inputs import digest

        got = self.__dict__.get("_inputs")
        if got is None or time.monotonic() - got[0] > 5.0:
            got = (time.monotonic(), digest(self.task.home, self.task.params))
            self.__dict__["_inputs"] = got
        return got[1]

    def cutoff(self, stage: str, scored, state):
        """The stage's declared cutoff (D454): a floor, a budget or a band around this run's best,
        or several of them applied in order, the words naming which cut whom (D657). Without
        one, every measured candidate goes on."""
        mine = self.role_cutoff(stage, scored, state)
        if mine is not None:
            return mine                       # the evaluation component's own stage (D461)
        spec = next((r for r in self.task.stages if r.name == stage), None)
        if spec is None or not spec.cutoff:
            return list(scored)
        if isinstance(spec.cutoff, dict):
            return self._gate(spec.cutoff, list(scored))
        kept, said = list(scored), []
        for rule in spec.cutoffs:
            passed, why = self._gate(rule, kept)
            ids = {id(s) for s in passed}
            gone = [s.candidate.name for s in kept if id(s) not in ids]
            if gone:
                said.append(f"{why} ({', '.join(gone[:6])}{', ...' if len(gone) > 6 else ''})")
            kept = passed
        return kept, "; ".join(said)

    def _gate(self, rule: dict, scored: list) -> tuple[list, str]:
        """One cutoff condition over these results: the survivors and the rule in words."""
        from .cutoff import above, below, within_best

        metric = rule["metric"]
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
        if not spec.command:
            state.say(f"  stage {stage}: the document names no command to measure it")
            return None
        over = self._over_ceiling(cand, state)
        if over:
            return {"error": over}            # over the cost ceiling: not synthesised (D615)
        subs = self._subs(cand, None, state)
        run = self._run(spec.command, subs, spec.timeout_s, f"stage {stage}")
        if not run.ok:
            # D897: a command that failed measured nothing, whatever it printed before it failed
            tail = ((run.stderr or "").strip() or (run.stdout or "").strip())[-160:]
            state.say(f"  stage {stage}: the command exited {run.returncode}; its numbers are not taken")
            return {"error": f"the command exited {run.returncode}" + (f": {tail}" if tail else "")}
        got = _metrics_in(spec, (run.stdout or "") + "\n" + (run.stderr or ""))
        if not numeric_metrics(got):
            state.say(f"  stage {stage}: no metric matched in the output")
            return {"error": "no metric matched in the output"}
        return got

    def estimated(self, cands: list[Candidate], stage: str, state: LoopState
                  ) -> list[tuple[dict[str, float] | None, str]]:
        """The stage's `estimate:` (D665): per candidate, its estimate and why it skips the tool
        ("" = the tool runs). A design the cache already holds is not estimated: it costs nothing."""
        from .estimate import by_model, by_surrogate, failing, measured_rows

        spec = next((r for r in self.task.stages if r.name == stage), None)
        if spec is None or spec.estimate is None:
            return [(None, "")] * len(cands)
        est = spec.estimate
        metrics = [m for m in (spec.metrics or spec.metrics_re) if spec.reports(m)]
        for metric in [o.metric for o in self.task.objectives] + [r.get("metric") for r in spec.cutoffs]:
            if metric and spec.reports(metric) and metric not in metrics:
                metrics.append(metric)
        todo = [i for i, c in enumerate(cands) if not self._cached(c, stage, state)]
        got: list[dict[str, float] | None] = [None] * len(cands)
        rows = measured_rows(state, stage) if est.kind != "command" else []
        if est.kind == "surrogate":
            for i in todo:
                got[i] = by_surrogate(rows, cands[i].knobs, metrics)
        elif est.kind == "model":
            for i, g in zip(todo, by_model(state, stage, [cands[i] for i in todo], metrics, rows)):
                got[i] = g
        else:
            for i in todo:
                run = self._run(est.command or (), self._subs(cands[i], None, state), spec.timeout_s, f"estimate {stage}")
                got[i] = (numeric_metrics(_metrics_in(spec, (run.stdout or "") + "\n" + (run.stderr or ""))) or None) if run.ok else None   # D897
        rules = self._estimate_rules(spec, state, rows or measured_rows(state, stage))
        return [(g, failing(g, rules, est.margin) if g else "") for g in got]

    def _cached(self, cand: Candidate, stage: str, state: LoopState) -> bool:
        from .measure import measurement_key

        try:
            return state.cache is not None and state.cache.holds(measurement_key(self, state, cand, stage))
        except Exception:  # noqa: BLE001
            return False

    def _estimate_rules(self, spec: Any, state: LoopState, rows: list) -> list[tuple[str, str, float, str]]:
        """What an estimate must not fail on this stage: its cutoffs, then every objective's limit
        at this stage (metric, ">=" | "<=", threshold, what)."""
        rules: list[tuple[str, str, float, str]] = []
        directions = {o.metric: o.direction for o in self.task.objectives}
        for c in spec.cutoffs:
            if not c:
                continue
            m = c["metric"]
            if "at" in c:
                rules.append((m, ">=", float(c["at"]), "the cutoff"))
            elif "below" in c:
                rules.append((m, "<=", float(c["below"]), "the cutoff"))
            else:                              # a band around the best measured on this stage
                vals = [ms[m] for _kn, ms in rows if m in ms]
                f = float(c["within"])
                if vals and directions.get(m, "maximize") != "minimize":
                    best = max(vals)
                    rules.append((m, ">=", best * f if best >= 0 else best / f, f"the cutoff, within {f:.0%} of the best"))
                elif vals:
                    best = min(vals)
                    rules.append((m, "<=", best / f if best >= 0 else best * f, f"the cutoff, within {f:.0%} of the best"))
        chain = self.stages()
        for o in self.objectives():
            o = o.resolved([ms for _kn, ms in rows]) if o.keep is not None else o
            goal = o.goal_at(spec.name, chain)
            if goal is not None:
                rules.append((o.metric, ">=" if o.direction == "maximize" else "<=", goal, "the objective's limit"))
        return rules

    def cache_suffix(self) -> str | None:
        """The loop's measurement cache (D541, D790): always on, beside the record."""
        return f"{self.task.id}.json"

    def cache_key(self, cand: Candidate, stage: str, state: LoopState) -> str:
        """What makes a measurement the same one (D567, D790, D898): the candidate, and what
        measures it -- the stage's command, the files under `{home}` the command names and the
        document's params. A changed clock, script or parameter measures
        again; a world with its own key replaces this.

        D898: the one evidence identity -- the disk cache's key and every row's `measured_as`, so
        `records.fresh` asks the same question the cache does. Beside the artifact it holds what
        the command reads of the candidate (the knobs it substitutes, `{point}`, `{part}`), how
        numbers are read from the output (`metrics_re`), and the builds of the tools the stage
        runs or `needs`. `Candidate.key` stays the design's content, for telling designs apart."""
        import hashlib

        from flux_loop.toolchain import tool_fingerprint

        from .document.commands import _PLACEHOLDER

        spec = next((r for r in self.task.stages if r.name == stage), None)
        if spec is None:
            return cand.key()
        named = {m for tok in spec.command or () for m in _PLACEHOLDER.findall(tok)}
        seen = ({k: v for k, v in sorted((cand.knobs or {}).items()) if "point" in named or k in named}
                if cand.artifact else {})              # an artifact-less candidate's key is its knobs already
        if "part" in named:
            seen["{part}"] = cand.subgoal or ""
        tools = [t for t in (*spec.needs, *(spec.command or ())[:1]) if not _PLACEHOLDER.search(t)]
        builds = {t: tool_fingerprint(t) or "absent" for t in dict.fromkeys(tools)}
        # D853: the loop's inputs -- a helper or a data file a stage reads changed, the measurement is
        # not the same one. D954: "" and None hold the places of the evaluator and the workload that
        # evaluator stages had, so every key a record already holds stays the same
        signature = [list(spec.command or ()), "", sorted(spec.metrics),
                     self.task.params, None, self.inputs(), sorted(spec.metrics_re.items()), seen, builds]
        if spec.metric_specs:
            signature.append(spec.metric_specs)
        h = hashlib.sha256(json.dumps(signature,
                                      sort_keys=True, default=str).encode())
        home = self.task.home
        for token in spec.command or ():
            if home and "{home}/" in token:
                f = Path(token.split("{home}/", 1)[1].split()[0].replace("{home}", home))
                f = f if f.is_absolute() else Path(home) / f
                if f.is_file():
                    h.update(f.read_bytes())
        return f"{cand.key()}@{h.hexdigest()[:16]}"


def _metrics_in(spec: Any, out: str) -> dict[str, Any]:
    """Scalar name=value tokens, JSON dictionaries, or a JSON object of declared metrics."""
    values: dict[str, Any] = {}
    definitions = getattr(spec, "metric_specs", {})
    decoder = json.JSONDecoder()
    # JSON documents can be surrounded by tool diagnostics; later reports replace earlier ones.
    for start in re.finditer(r"(?m)^[ \t]*(?=\{)", out):
        try:
            data = decoder.raw_decode(out[start.end():])[0]
        except ValueError:
            continue
        if isinstance(data, dict):
            for metric in getattr(spec, "metrics", tuple(spec.metrics_re)):
                if metric in data:
                    values[metric] = data[metric]
    for metric, pat in spec.metrics_re.items():
        dictionary = definitions.get(metric, {}).get("type") == "dict"
        matches = re.finditer(pat, out) if dictionary else [re.search(pat, out)]
        for m in matches:
            if m is None:
                continue
            try:
                values[metric] = (decoder.raw_decode(m.group(1).lstrip())[0] if dictionary
                                  else float(m.group(1)))
            except (ValueError, TypeError):
                pass
    got = numeric_metrics({m: v for m, v in values.items()
                           if not isinstance(v, dict) or definitions.get(m, {}).get("type") == "dict"}, definitions)
    groups = {m: list(v) for m, v in values.items() if isinstance(v, dict) and definitions.get(m, {}).get("type") == "dict"}
    if groups:
        got["_metric_groups"] = groups
    return got
