"""The rim, guarded (D404): every application loop keeps a record and consumes feedback.

D398-D402 closed these gaps one loop at a time; this test is what keeps the claim true
when the seventh application arrives. It checks SEAMS, not behavior -- each loop's
entry point must accept the operator channel and name its campaign record -- because
the behavior is pinned per-loop by that loop's own tests, and a new loop that lacks
the seam cannot have wired it.

interconnect is asserted on its own posture: a module-level `set_feedback` (D333's
blessed-globals precedent) and a request-carried db.
"""

from __future__ import annotations

import inspect
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]


def _params(fn) -> set[str]:
    return set(inspect.signature(fn).parameters)


def test_every_loop_entry_accepts_the_operator_channel():
    from flux_bankmap.flow import run_study as bankmap
    from flux_imapping import run_study as imapping
    from flux_macarray.flow import run_study as macarray
    from flux_nlu import run_study as nlu
    from flux_omni import run_omni
    from flux_prefetcher.flow import run_study as prefetcher

    for name, fn in [("prefetcher", prefetcher), ("macarray", macarray),
                     ("bankmap", bankmap), ("imapping", imapping),
                     ("omni", run_omni), ("nlu", nlu)]:
        assert "feedback" in _params(fn), f"{name}: no feedback seam (D398)"

    from flux_interconnect import flow as ic

    assert callable(getattr(ic, "set_feedback", None)), \
        "interconnect: set_feedback seam missing (D398)"


def test_every_loop_names_its_campaign_record():
    from flux_bankmap.problem import MappingRequest
    from flux_imapping import run_study as imapping
    from flux_interconnect.study import InterconnectRequest
    from flux_macarray.flow import MacRequest
    from flux_nlu import NluRequest
    from flux_omni import run_omni
    from flux_prefetcher.study import PrefetcherRequest

    for name, req in [("prefetcher", PrefetcherRequest), ("macarray", MacRequest),
                      ("bankmap", MappingRequest), ("nlu", NluRequest),
                      ("interconnect", InterconnectRequest)]:
        assert "db" in {f for f in getattr(req, "__dataclass_fields__", {})}, \
            f"{name}: request carries no db (the record's home)"
    assert "db_path" in _params(imapping), "imapping: no db_path (D397)"
    assert "db_path" in _params(run_omni), "omni: no db_path (D401)"


def test_the_demos_run_through_the_shared_tail():
    """Five demos use demo_run (D404); the prefetcher's richer run_tui flow is the
    stated exception. Source-level, so a new demo pasting the old tail fails here."""
    for app in ("interconnect_mapping", "macarray", "interconnect", "omni", "bankmap", "nlu"):
        src = (FLUX_ROOT / "applications" / app / "demo.py").read_text()
        assert "demo_run(" in src, f"{app}/demo.py does not use flux_tui.demo_run"
        assert "FeedbackChannel()" not in src, \
            f"{app}/demo.py hand-rolls the stdin channel demo_run already owns"
