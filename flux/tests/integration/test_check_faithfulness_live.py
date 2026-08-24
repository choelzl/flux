"""Live prose-faithfulness cross-examination (docs/decisions.md D249): real qwen as the
judge. The cases are deliberately clear-cut — the tri-state machinery and summary
completeness are unit-tested; what only a live run can show is that a real local judge
produces correct verdicts on unambiguous inputs, in both directions. Skips without Ollama."""

from __future__ import annotations

import pytest

import _helpers

pytestmark = _helpers.requires_ollama


_PROSE = ("Minimize latency and real placed silicon area over per-layer engine widths 8 and "
          "16. Screen with zigzag, escalate through rtl and then openroad. "
          "Spend at most 16 evaluations.")

_FAITHFUL_DOC = {
    "schema_version": "0.1.0",
    "id": "live/faithful/v1",
    "objectives": [
        {"metric": "latency_cycles", "direction": "minimize"},
        {"metric": "area_mm2", "direction": "minimize", "measured_at": "escalation"},
    ],
    "mode": "pareto",
    "backends": {"screening": "zigzag", "escalation": ["rtl", "openroad"]},
    "search": {"kind": "composition_width", "widths": [8, 16]},
    "strategy": {"kind": "grid", "seed": 0},
    "budget": {"evaluations": 16},
}


def test_a_faithful_document_passes_the_real_judge():
    from flux_chia_nodes import flux_check_prose_faithfulness

    # votes=5: a lone qwen-7b judge measured ~75-85% on this direction even with the
    # mechanical guards (D249) — the faithful direction gets the deeper majority because its
    # failures are noise (false alarms), not danger.
    report = flux_check_prose_faithfulness(_PROSE, objective=_FAITHFUL_DOC, votes=5)
    assert report.verdict == "faithful", report.mismatches


def test_a_mismatched_document_is_caught_with_the_mismatch_named():
    """The exact D239-class failure the checker exists for: the document silently dropped the
    area objective and changed the widths. An unambiguous double mismatch — the judge must
    catch it and name at least one of the two changed facts."""
    from flux_chia_nodes import flux_check_prose_faithfulness

    unfaithful = {
        **_FAITHFUL_DOC,
        "id": "live/unfaithful/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],  # area dropped
        "search": {"kind": "composition_width", "widths": [4, 64]},  # widths changed
        "backends": {"screening": "zigzag"},  # rungs dropped too
    }
    report = flux_check_prose_faithfulness(_PROSE, objective=unfaithful, votes=3)
    assert report.verdict == "unfaithful", (report.verdict, report.transcript[-1][:300])
    text = " ".join(report.mismatches).lower()
    assert any(word in text for word in ("area", "width", "8", "16", "openroad", "rtl")), \
        report.mismatches
