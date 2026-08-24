"""Command implementations for the Flux CLI (docs/roadmap.md Phase 1: `flux eval`, `flux import`,
`flux replay`).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import flux_ir
from flux_evaluator_abi import Budget, Candidate
from flux_store import ResultStore

from .registry import DEFAULT_METRICS, backend_for_evaluator_string, make_evaluator

_KNOWN_KINDS = ("workload", "architecture", "mapping")


def _detect_kind(doc: dict[str, Any]) -> str:
    """Best-effort IR kind detection from a document's shape. `--kind` always overrides this —
    it exists for convenience, not as the source of truth.
    """
    if "ops" in doc:
        return "workload"
    if "hierarchy" in doc:
        return "architecture"
    if "for_op" in doc:
        return "mapping"
    raise ValueError(
        "could not auto-detect IR kind (found none of 'ops', 'hierarchy', 'for_op'); "
        "pass --kind explicitly"
    )


def cmd_import(args: argparse.Namespace) -> int:
    doc = flux_ir.load_document(args.file)
    try:
        kind = args.kind or _detect_kind(doc)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        flux_ir.validate(kind, doc)
    except flux_ir.SchemaValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    content_hash = flux_ir.content_hash(doc)
    print(f"kind: {kind}")
    print(f"id:   {doc.get('id', '<no id>')}")
    print(f"hash: {content_hash}")

    if args.store:
        with ResultStore(args.store) as store:
            stored_hash = store.put_document(kind, doc)
            assert stored_hash == content_hash
        print(f"stored in {args.store}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    workload = flux_ir.load_document(args.workload)
    try:
        flux_ir.validate("workload", workload)
    except flux_ir.SchemaValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    arch = None
    if args.arch:
        arch = flux_ir.load_document(args.arch)
        try:
            flux_ir.validate("architecture", arch)
        except flux_ir.SchemaValidationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    metrics = frozenset(args.metrics.split(",")) if args.metrics else DEFAULT_METRICS
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    try:
        evaluator = make_evaluator(args.backend)
        result = evaluator.evaluate(candidate, Budget(), metrics)
    except Exception as exc:  # noqa: BLE001 — surfaced to the user, not swallowed
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result.to_dict(), indent=2))

    if args.store:
        with ResultStore(args.store) as store:
            workload_hash = store.put_document("workload", workload)
            arch_hash = store.put_document("architecture", arch) if arch is not None else None
            row_id = store.put_result(result, workload_hash=workload_hash, arch_hash=arch_hash)
        print(f"stored in {args.store}: result id={row_id}", file=sys.stderr)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    with ResultStore(args.store) as store:
        record = store.get_result(args.result_id)
        if record is None:
            print(f"error: no result with id={args.result_id} in {args.store}", file=sys.stderr)
            return 1

        workload = store.get_document(record["workload_hash"])
        if workload is None:
            print(
                f"error: workload {record['workload_hash']} referenced by result "
                f"{args.result_id} is not in {args.store} (was it stored with `flux eval "
                "--store`?)",
                file=sys.stderr,
            )
            return 1
        arch = store.get_document(record["arch_hash"]) if record["arch_hash"] else None
        # The mapping is part of the candidate, and replaying without it re-evaluates a different
        # design — silently, since the resulting difference reads as non-determinism rather than
        # as a dropped input. Every mapping-space search stores one (docs/decisions.md D189).
        mapping = store.get_document(record["mapping_hash"]) if record["mapping_hash"] else None
        if record["mapping_hash"] and mapping is None:
            print(
                f"error: mapping {record['mapping_hash']} referenced by result "
                f"{args.result_id} is not in {args.store} — replaying without it would evaluate "
                "a different candidate",
                file=sys.stderr,
            )
            return 1

    try:
        backend_name = backend_for_evaluator_string(record["evaluator"])
        evaluator = make_evaluator(backend_name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    candidate = Candidate(workload=workload, arch=arch, mapping=mapping)
    stored_metrics: dict[str, Any] = record["result"]["metrics"]
    metrics = frozenset(stored_metrics)

    try:
        fresh_result = evaluator.evaluate(candidate, Budget(), metrics)
    except Exception as exc:  # noqa: BLE001 - same "error:, exit 1" shape as every path above
        print(f"error: re-evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    fresh_metrics = fresh_result.to_dict()["metrics"]

    print(f"replaying result id={args.result_id} (backend={backend_name})")
    all_match = True
    for metric_name, stored_estimate in stored_metrics.items():
        stored_value = stored_estimate["value"]
        fresh_value = fresh_metrics.get(metric_name, {}).get("value")
        match = fresh_value == stored_value
        all_match = all_match and match
        status = "OK" if match else "MISMATCH"
        print(f"  {metric_name:20s} stored={stored_value!r:>15}  fresh={fresh_value!r:>15}  [{status}]")

    if all_match:
        print("replay: all metrics match")
        return 0
    print("replay: MISMATCH — see above", file=sys.stderr)
    return 1


def cmd_task_check(args: argparse.Namespace) -> int:
    """Validate a task document, list what it declares, and name any tool it needs that
    is not on PATH -- runs nothing (D430)."""
    from flux_loop import PromptProblem, TaskError, load_task

    try:
        task = load_task(args.file)
    except TaskError as exc:
        print(f"not a task: {exc}")
        return 2
    problem = PromptProblem(task)
    print(f"task {task.id}: {task.statement[:100]}")
    print(f"  language {task.language} ({task.extension}); parts: "
          + ("decided by the model (decompose)" if task.decompose
             else ", ".join(p.name for p in task.parts) or "one goal"))
    if task.split or task.subtasks:      # D455: each one is its own loop
        print("  sub-tasks: " + ("decided by the model (decompose), at most "
                                 f"{task.max_subtasks}" if task.split
                                 else ", ".join(c.id for c in task.subtasks)))
    print("  drafted by: " + _drafted_by(task))       # D456
    print("  roles: " + _roles_line(task))            # D460
    for label, cmd in task.commands():
        print(f"  {label}: {' '.join(cmd)}")
    print("  stages: " + ", ".join(_stage_label(r) for r in task.stages) if task.stages
          else "  stages: " + ", ".join(problem.stages()))
    print("  objectives: " + (", ".join(f"{o.direction} {o.metric}" for o in task.objectives) or "none"))
    missing = problem.tools_missing()
    if missing:
        print(f"  MISSING on PATH: {', '.join(missing)}")
        return 1
    print("  tools: all present")
    return 0


def _roles_from(flags: list[str] | None) -> Any:
    """`--role orchestrator=rules` (repeatable) as a `Roles` bundle (D460). Only the roles
    named are filled, so the document's own choices for the others stand."""
    from flux_loop import Roles, make_role

    if not flags:
        return None
    out = Roles()
    for flag in flags:
        role, _, name = str(flag).partition("=")
        if not name:
            raise ValueError(f"--role takes ROLE=NAME, not {flag!r}")
        out = out.with_role(role.strip(), make_role(role.strip(), name.strip()))
    return out


def _drafted_by(task: Any) -> str:
    """WHO writes the candidates (D456): the model unless the document says otherwise."""
    spec = task.generator
    if spec.get("catalog"):
        return f"a catalog of {len(spec['catalog'])} design(s) that already exist (no model)"
    if spec.get("command"):
        return "the generator command (no model)"
    return "a model"


def _roles_line(task: Any) -> str:
    """Who fills each of the four roles, and what else this loop could be switched to."""
    from flux_loop import ROLES, available_roles

    said = dict(task.roles)
    if task.generator and "generator" not in said:
        said["generator"] = "the document's own generator command/catalog"
    parts = []
    for role in ROLES:
        chosen = said.get(role)
        choices = available_roles(role)
        parts.append(f"{role}={chosen if chosen else 'the problem default'}"
                     + (f" (or: {', '.join(c for c in choices if c != chosen)})" if choices
                        else ""))
    return "; ".join(parts)


def _stage_label(stage: Any) -> str:
    """A stage and what it takes to climb past it (D454)."""
    cut = stage.cutoff
    if not cut:
        return stage.name
    if "at" in cut:
        rule = f"{cut['metric']} >= {cut['at']:g}"
    elif "below" in cut:
        rule = f"{cut['metric']} <= {cut['below']:g}"
    else:
        rule = f"{cut['metric']} within {float(cut['within']):.0%} of the best"
    return f"{stage.name} (cutoff: {rule})"


def cmd_task_run(args: argparse.Namespace) -> int:
    """Run a task document through the loop and print the standard report (D430)."""
    from pathlib import Path

    from flux_loop import PromptProblem, TaskError, load_task, request_for, run_loop, task_report_lines

    try:
        task = load_task(args.file)
    except TaskError as exc:
        print(f"not a task: {exc}")
        return 2
    try:
        problem = PromptProblem(task, roles=_roles_from(getattr(args, "role", None)))
    except (TaskError, ValueError) as exc:
        print(f"not a role this loop has: {exc}")
        return 2
    if problem.roles().named():
        print("roles: " + ", ".join(f"{r}={n}" for r, n in sorted(problem.roles().named().items())))
    missing = problem.tools_missing()
    if missing:
        print(f"{', '.join(missing)} not on PATH; `flux task check` lists what the task needs")
        return 1
    overrides: dict[str, Any] = {"db": args.db or f"{task.id}.db"}
    if args.steps is not None:
        overrides["steps"] = args.steps
    if args.repair is not None:
        overrides["repair_attempts"] = args.repair
    if args.no_structured:
        overrides["structured"] = False
    request = request_for(task, **overrides)
    if args.replies:
        from flux_llm import ScriptedProposer

        proposer: Any = ScriptedProposer(json.loads(Path(args.replies).read_text()))
    else:
        from flux_llm import structured_proposer

        proposer = structured_proposer(args.model)
    out = run_loop(problem, request, proposer=proposer, log=print)
    print()
    print("\n".join(task_report_lines(task, out)))
    if out.decision is not None:
        target = Path(args.out or f"{task.id}{task.extension}")
        target.write_text(out.decision.candidate.artifact)
        print(f"\nartifact written to {target}")
        return 0
    return 1

