"""Command implementations for the Flux CLI (docs/05.md Phase 1: `flux eval`, `flux import`,
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

    try:
        backend_name = backend_for_evaluator_string(record["evaluator"])
        evaluator = make_evaluator(backend_name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    candidate = Candidate(workload=workload, arch=arch, mapping=None)
    stored_metrics: dict[str, Any] = record["result"]["metrics"]
    metrics = frozenset(stored_metrics)

    fresh_result = evaluator.evaluate(candidate, Budget(), metrics)
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
