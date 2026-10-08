"""Pass 0: check and measure unchanged inputs, without any model or agent work."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from .inputs import digest, file_digest
from .measure import measure_many
from .observe import _phase
from .records import _record_trial
from .types import Candidate, Scored, Verdict


def _fingerprint(problem):
    """Fresh inputs, configuration and executable builds; never the run's outputs."""
    task = getattr(problem, "task", None)
    if task is None:
        return None  # a custom problem must not reuse evidence without an input identity
    from flux_evaluator_abi import MEASURING_TOOLS, toolchain_fingerprint

    from .provenance import git_revision

    def content(path):
        try:
            return file_digest(path)
        except OSError:
            return "missing or unreadable"

    config = task.baseline or {}
    source = None
    if "file" in config:
        path = Path(config["file"].replace("{home}", task.home or "."))
        source = content(path if path.is_absolute() else Path(task.home or ".") / path)
    binaries = set()
    for label, command in task.commands():
        if label == "generator" or label.startswith("estimate "):
            continue
        binaries.add(command[0].replace("{python}", sys.executable).replace("{home}", task.home or "."))
    needs = tuple(dict.fromkeys((*MEASURING_TOOLS, *(n for st in task.stages for n in st.needs))))
    tools = {}
    for binary in sorted(binaries | set(needs)):
        path = shutil.which(binary)
        resolved = str(Path(path).resolve()) if path else None
        tools[binary] = (resolved, content(resolved) if resolved else None)
    body = {"schema": 1, "document": task.digest, "inputs": digest(task.home, task.params),
            "source": source, "executables": tools, "tools": toolchain_fingerprint(needs), "flux": git_revision(),
            "environment": {key: os.environ.get(key) for key in (
                "PATH", "PYTHONPATH", "LD_LIBRARY_PATH", "NIX_CFLAGS_COMPILE", "NIX_LDFLAGS",
                "CC", "CXX", "CFLAGS", "CXXFLAGS", "LDFLAGS", "PKG_CONFIG_PATH")}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _restore(state, saved):
    """Restore the outcome without writing duplicate trials or running any tools."""
    scored = [Scored(Candidate.from_record(row["candidate"]), row["stage"], row["metrics"], row["payload"])
              for row in saved["scored"]]
    admitted = {key: Candidate.from_record(doc) for key, doc in saved["admitted"].items()}
    refused = [tuple(row) for row in saved["failures"]]
    reached, stopped = saved["reached"], saved["stopped"]
    state.scored = scored
    state.admitted = admitted
    state.refused = refused
    state.reached = reached
    state.stopped = stopped
    for row in scored:
        state.on_stage.setdefault(row.stage, []).append(row)


def run_baseline(problem, state):
    from .loop import _conclude, _judge, _merge_decision_history, _publish, _result

    state.request = dataclasses.replace(state.request, critique_rounds=0, screen_only=False)
    state.cache = None  # a baseline must exercise tools, even when identical numbers are cached
    fingerprint = _fingerprint(problem)
    saved = state.records.recall("baseline") if state.records is not None else []
    reused = False
    if fingerprint and saved and saved[-1].get("fingerprint") == fingerprint and saved[-1].get("ok"):
        try:
            _restore(state, saved[-1])
            reused = True
        except (KeyError, TypeError, ValueError):
            pass  # an older or incomplete snapshot cannot stand in for a real tool run
    if reused:
        line = "baseline pass 0 reused: inputs, configuration and tools unchanged"
        state.say(f"  {line}")
        state.lessons.append(line)
        _merge_decision_history(problem, state)
        with _phase("knowledge: baseline reused", why="unchanged inputs; checks and measurements skipped"):
            result = _conclude(problem, state, []) if state.scored else _result(problem, state, None, state.stopped, [], [])
        result.provenance["baseline_reused"] = True
        if state.depth == 0:
            _publish(problem, state, [], [], line, searching=False)
        return result
    cand = Candidate(f"{problem.name}#baseline", meta={"baseline": True})
    try:
        with _phase("knowledge: baseline", why="unchanged file, preparation command, or current project") as out:
            cand = problem.baseline_candidate(state)
            out["source"] = "current project" if cand.meta.get("baseline_workspace") else "unchanged artifact"
        with _phase("test: baseline", why="no repairs or agent edits") as out:
            built = problem.build(cand, None, state)
            verdict = _judge(problem, built, cand, None, state)
            out["verdict"] = "passed" if verdict.ok else verdict.why
        if not verdict.ok:
            _record_trial(state, cand, None, verdict)
            state.refused.append((cand.name, verdict.why))
        else:
            # A project-only measurement is visible on the record, but supplies no design to resume.
            if not cand.meta.get("baseline_workspace"):
                state.admitted["*"] = cand
                _record_trial(state, cand, None, verdict, admitted=True)
            else:
                _record_trial(state, cand, None, verdict, gate_passed=True)
            for stage in problem.stages():
                rows = measure_many(problem, state, [cand], stage)
                if not rows:
                    break
                state.scored.extend(rows)
                state.on_stage[stage] = rows
                state.reached = stage
    except Exception as exc:  # noqa: BLE001 -- baseline errors are recorded, never sent to an agent
        why = f"{type(exc).__name__}: {exc}"
        state.refused.append((cand.name, why))
        _record_trial(state, cand, None, Verdict(False, 1.0, why), error=why)
    state.stopped = "baseline failed" if state.refused else "baseline checked and measured"
    state.say(f"  {state.stopped}" + (": " + state.refused[-1][1] if state.refused else ""))
    if state.records is not None:
        state.records.remember("baseline", {"candidate": cand.name, "ok": not state.refused,
                                           "failures": list(state.refused), "stages": list(state.on_stage),
                                           "fingerprint": fingerprint, "stopped": state.stopped,
                                           "reached": state.reached, "scored": [dataclasses.asdict(s) for s in state.scored],
                                           "admitted": {k: c.to_record() for k, c in state.admitted.items()}})
    # Save only pass 0 in its reusable snapshot, then rank against the campaign's evidence.
    _merge_decision_history(problem, state)
    with _phase("decide: baseline", why="compare the unchanged design with retained measurements"):
        result = _conclude(problem, state, []) if state.scored else _result(problem, state, None, state.stopped, [], [])
    if state.depth == 0:
        _publish(problem, state, [], [], state.stopped, searching=False)
    return result
