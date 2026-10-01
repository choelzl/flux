"""D740: on a server, a loop works one thing at a time unless an admin raises it for the loop."""

from __future__ import annotations

from types import SimpleNamespace

from flux_loop.pool import parallel_cap, workers


def test_the_cap_holds_the_documents_workers_and_none_is_no_cap(monkeypatch):
    monkeypatch.delenv("FLUX_PARALLEL_MAX", raising=False)
    assert parallel_cap() is None and workers(SimpleNamespace(workers=6)) == 6
    monkeypatch.setenv("FLUX_PARALLEL_MAX", "1")
    assert workers(SimpleNamespace(workers=6)) == 1 and workers(SimpleNamespace(workers=0)) == 1
    monkeypatch.setenv("FLUX_PARALLEL_MAX", "4")
    assert workers(SimpleNamespace(workers=6)) == 4 and workers(SimpleNamespace(workers=2)) == 2
    monkeypatch.setenv("FLUX_PARALLEL_MAX", "nonsense")
    assert parallel_cap() == 1, "a cap that cannot be read is the tightest"

