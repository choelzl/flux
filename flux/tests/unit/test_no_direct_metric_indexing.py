"""`result.metrics[...]` is unreachable in production code (docs/decisions.md D201's residue).

D201 built the handled path (`Result.metric()` → `MetricOutcome`) and made the unhandled one
explain itself (`MetricMap.__missing__`), but direct indexing stayed *reachable* — and reachable
means new code will reach it: the D112 crash (a search dying on a legally-omitted metric) was fixed
in `search/architecture` and then re-written independently in `search/agentic` because nothing
stopped it. This test is what stops it.

Scope is production packages only. Tests index directly on purpose — there, a missing metric is an
assertion failure and `MissingMetricError`'s message is exactly what a failing test should print.

AST-based, not a grep: the ban is on the *code shape* `<expr>.metrics[...]`, while prose is free to
mention the spelling — `dse_loop.py` quotes the historical crash verbatim in a comment, and history
should not have to be reworded to satisfy a linter.
"""

from __future__ import annotations

import ast
from pathlib import Path

_FLUX = Path(__file__).resolve().parents[2]

# Every production src/ tree. Discovered, not listed, so a new module is covered the day it
# appears; `parents[2]` keeps tests/ itself out.
_SOURCE_ROOTS = sorted(_FLUX.glob("*/src")) + sorted(_FLUX.glob("*/*/src"))

# The one legitimate subscript: the ABI defining the map itself.
_ALLOWED = {_FLUX / "evaluators/abi/src/flux_evaluator_abi/types.py"}


def _metric_subscripts(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "metrics"
    ]


def test_no_production_code_indexes_result_metrics_directly():
    offenders: list[str] = []
    scanned = 0
    for root in _SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            scanned += 1
            if path in _ALLOWED:
                continue
            offenders.extend(f"{path.relative_to(_FLUX)}:{line}" for line in _metric_subscripts(path))

    # Guards the guard, twice: a glob that found nothing would pass vacuously, and so would one
    # that somehow missed the packages this residue actually lives in.
    assert scanned >= 100, f"only {scanned} files scanned — source-root discovery is broken"
    assert any((r / "flux_search_architecture").is_dir() for r in _SOURCE_ROOTS)

    assert not offenders, (
        "direct `.metrics[...]` indexing in production code — use Result.value_of()/estimate_of() "
        f"(crash-with-explanation) or Result.metric() (handled): {offenders}"
    )


def test_the_detector_actually_detects():
    """The AST walk must flag the banned shape and pass the allowed ones, checked on synthetic
    code rather than trusted — a matcher wired to the wrong node type would pass an empty
    offender list forever."""
    import tempfile

    banned = "x = result.metrics['latency_cycles'].value\n"
    allowed = (
        "# result.metrics['x'] quoted in prose\n"
        "y = result.value_of('latency_cycles')\n"
        "z = aggregated_metrics['x']\n"  # a plain dict named *_metrics is not Result.metrics
        "w = result.metrics.get('x')\n"
    )
    with tempfile.TemporaryDirectory() as d:
        bad, good = Path(d) / "bad.py", Path(d) / "good.py"
        bad.write_text(banned)
        good.write_text(allowed)
        assert _metric_subscripts(bad) == [1]
        assert _metric_subscripts(good) == []
