"""Empirical distribution resolution for a Workload IR's `dynamism.distributions` references
(docs/decisions.md D87, closing gap-analysis G5's last open piece). One real ingested dataset so
far: `"empirical@corpus/kv-cache-len-v1"` (measured ShareGPT conversation lengths — source,
license, and processing in that directory's PROVENANCE.md). Only `empirical@` refs resolve, and
only against real ingested data — never a silent placeholder/uniform fallback.

`quantile_sample_points` returns the midpoint of each of `n` equal probability-mass buckets from
the real percentile table — standard quantile quadrature: the uniform mean over these points is a
defensible estimate of the distribution's expectation, so `sweep_dynamic_shape`'s existing
uniform aggregation becomes distribution-aware with zero new weighting logic to get wrong.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DistributionResolutionError(ValueError):
    """A `dynamism.distributions`/`semantics.distribution` reference couldn't be resolved to real,
    ingested data — raised loudly rather than silently falling back to uniform/arbitrary sampling,
    so a caller relying on real weighting finds out immediately, not via a silently-approximate
    result.
    """


_KNOWN_DISTRIBUTIONS = frozenset({"kv-cache-len-v1"})


@dataclass(frozen=True, slots=True)
class EmpiricalDistribution:
    name: str
    summary: dict[str, Any]
    percentiles: dict[int, int]  # 0..100 -> real integer order statistic from real ingested data


def _default_corpus_root() -> Path:
    # The DOCUMENT corpus under mentor/knowledge/, not mentor/benchmarks/ (which holds design
    # points). Both were called "corpus" before the reorganisation, which is how this line got
    # pointed at the wrong one during the move.
    return (Path(__file__).resolve().parents[4]
            / "mentor" / "knowledge" / "corpus" / "distributions")


def parse_distribution_ref(ref: str) -> tuple[str, str]:
    """Split `"empirical@corpus/kv-cache-len-v1"` into `("empirical", "kv-cache-len-v1")`. Raises
    `DistributionResolutionError` if `ref` doesn't match this repo's own `{scheme}@corpus/{name}`
    convention (every real workload example here already uses it).
    """
    if "@corpus/" not in ref:
        raise DistributionResolutionError(
            f"distribution ref {ref!r} doesn't match the {{scheme}}@corpus/{{name}} convention "
            "every real workload example in this repo already uses."
        )
    scheme, _, name = ref.partition("@corpus/")
    if not scheme or not name:
        raise DistributionResolutionError(f"distribution ref {ref!r} has an empty scheme or name.")
    return scheme, name


def load_empirical_distribution(ref: str, *, corpus_root: Path | str | None = None) -> EmpiricalDistribution:
    """Load the real, ingested distribution `ref` names. `corpus_root` defaults to this repo's own
    `knowledge/corpus/distributions/` (see that directory for what's actually ingested so far —
    only `kv-cache-len-v1`, docs/decisions.md D87). Raises `DistributionResolutionError` if `ref`
    doesn't name a real, ingested distribution — never silently substitutes a placeholder or an
    empty/uniform fallback.
    """
    scheme, name = parse_distribution_ref(ref)
    # Enforce the scheme, not just parse it: this loader reads real *empirical* percentile
    # tables, so a "measured@..."/"garbage@..." ref resolving here would silently misrepresent
    # what the data is (review finding — the scheme was previously parsed and discarded).
    if scheme != "empirical":
        raise DistributionResolutionError(
            f"distribution ref {ref!r} has scheme {scheme!r} — this loader resolves only "
            "'empirical' refs (real ingested percentile tables); no other scheme has real "
            "ingested data or a loader yet."
        )
    root = Path(corpus_root) if corpus_root is not None else _default_corpus_root()
    data_path = root / name / "data.json"
    if not data_path.is_file():
        raise DistributionResolutionError(
            f"distribution ref {ref!r} names {name!r}, which isn't real, ingested data — no "
            f"{data_path} file exists. Known, real, ingested distributions: "
            f"{sorted(_KNOWN_DISTRIBUTIONS)}."
        )
    with data_path.open() as f:
        doc = json.load(f)
    percentiles = {int(k): int(v) for k, v in doc["percentiles"].items()}
    return EmpiricalDistribution(name=name, summary=doc["summary"], percentiles=percentiles)


def quantile_sample_points(
    distribution: EmpiricalDistribution, n: int, *, lo: int | None = None, hi: int | None = None,
) -> list[int]:
    """Return `n` real, evenly-probability-spaced sample points from `distribution`'s own real,
    ingested percentile table — the midpoint of each of `n` equal probability-mass buckets (e.g.
    n=4 reads real percentiles 12.5, 37.5, 62.5, 87.5), rounded to the nearest real integer
    percentile this repo actually has data for. A real, standard quantile-sampling technique, not
    invented weights (see this module's own docstring).

    `lo`/`hi`, if given (typically a workload's own declared `{dyn: [lo, hi]}` bound — see
    `sweep.dynamic_bound_range`), clip every returned point into that real, physically valid
    range: real data can and does fall outside any one workload's own declared bound (this repo's
    own `kv-cache-len-v1` has real observations up to 161281, far past any workload's declared
    `hi`), and a sample point outside the declared range would make `resolve_dynamic_bound` raise.
    Clipping is a documented, standard choice, not silent fabrication — which real quantiles get
    clipped to the boundary is itself real information (many real conversations really do exceed
    a small `hi`).

    Raises `ValueError` if `n < 1`.
    """
    if n < 1:
        raise ValueError(f"n={n} must be >= 1 — nothing to sample")

    points: list[int] = []
    for i in range(n):
        p = (i + 0.5) / n * 100.0
        rounded = min(100, max(0, round(p)))
        if rounded not in distribution.percentiles:
            # kv-cache-len-v1 ships a dense 0..100 table, but nothing guarantees a future
            # ingestion does — a sparse table previously surfaced as a bare KeyError (review
            # finding) instead of this module's own typed error.
            raise DistributionResolutionError(
                f"distribution {distribution.name!r} has no percentile {rounded} — its table "
                f"has {len(distribution.percentiles)} entries; quantile sampling needs a dense "
                "0..100 percentile table."
            )
        value = distribution.percentiles[rounded]
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        points.append(value)
    return points
