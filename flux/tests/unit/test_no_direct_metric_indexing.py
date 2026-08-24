"""`result.metrics[...]` is not used in production code (D201); `Result.metric()` handles a
legally omitted metric.

Production packages only: tests index directly on purpose, where a missing metric should fail.
AST-based, not a grep, so comments may still mention the spelling.
"""

from __future__ import annotations

import ast
from pathlib import Path

_FLUX = Path(__file__).resolve().parents[2]

# Every production src/ tree, discovered so a new module is covered; `parents[2]` excludes tests/.
_SOURCE_ROOTS = sorted(_FLUX.glob("*/src")) + sorted(_FLUX.glob("*/*/src"))

# The one legitimate subscript: the ABI defining the map itself.
_ALLOWED = {_FLUX / "evaluator/abi/src/flux_evaluator_abi/types.py"}


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

    # An empty glob, or one missing the relevant packages, would pass vacuously.
    assert scanned >= 100, f"only {scanned} files scanned — source-root discovery is broken"
    assert any((r / "flux_loop").is_dir() for r in _SOURCE_ROOTS)

    assert not offenders, (
        "direct `.metrics[...]` indexing in production code — use Result.value_of()/estimate_of() "
        f"(crash-with-explanation) or Result.metric() (handled): {offenders}"
    )


def test_the_detector_actually_detects():
    """The AST walk flags the banned shape and passes the allowed ones, on synthetic code."""
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
