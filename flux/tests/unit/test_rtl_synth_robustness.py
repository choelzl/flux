"""Robustness fixes in flux_codegen_rtl_harness.synth / .asap7 (D96): work-dir cleanup on every
path and timeouts becoming SynthesisError. `subprocess.run` is monkeypatched so these hold whatever
the tool does; real Yosys is covered by the integration tests.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from flux_codegen_rtl_harness import SynthesisError, synthesize_and_measure
from flux_codegen_rtl_harness import asap7 as asap7_module
from flux_codegen_rtl_harness import synth as synth_module
from flux_codegen_rtl_harness.asap7 import _synthesize_with_asap7_unchecked

_DUT = "module m(input logic a, output logic b); assign b = a; endmodule\n"


@pytest.fixture
def tracked_workdir(tmp_path, monkeypatch):
    """Route mkdtemp to a known dir in both modules so the tests can assert it was removed."""
    created = []

    def _mkdtemp(prefix: str) -> str:
        d = tmp_path / f"{prefix}work{len(created)}"
        d.mkdir()
        created.append(d)
        return str(d)

    monkeypatch.setattr(synth_module.tempfile, "mkdtemp", _mkdtemp)
    monkeypatch.setattr(asap7_module.tempfile, "mkdtemp", _mkdtemp)
    return created


def test_generic_synth_timeout_raises_synthesis_error_and_cleans_up(tracked_workdir, monkeypatch):
    def _run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="yosys", timeout=0.1)

    monkeypatch.setattr(synth_module.subprocess, "run", _run)
    with pytest.raises(SynthesisError, match="timed out"):
        synthesize_and_measure(_DUT, "m", timeout_s=0.1)
    assert tracked_workdir and not tracked_workdir[0].exists()


def test_generic_synth_failure_still_cleans_up(tracked_workdir, monkeypatch):
    monkeypatch.setattr(
        synth_module.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="ERROR: syntax error", stderr=""),
    )
    with pytest.raises(SynthesisError):
        synthesize_and_measure(_DUT, "m")
    assert tracked_workdir and not tracked_workdir[0].exists()


def test_generic_synth_success_cleans_up(tracked_workdir, monkeypatch):
    fake_stdout = "3. Printing statistics.\n\n   10 cells\n"
    monkeypatch.setattr(
        synth_module.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=fake_stdout, stderr=""),
    )
    result = synthesize_and_measure(_DUT, "m")
    assert result.total_cells == 10
    assert tracked_workdir and not tracked_workdir[0].exists()


def test_asap7_timeout_raises_synthesis_error_and_cleans_up(tracked_workdir, monkeypatch):
    """The ASAP7 path's work dir, holding a ~4.1 MB decompressed liberty per call, is removed too."""

    def _run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="yosys", timeout=0.1)

    monkeypatch.setattr(asap7_module.subprocess, "run", _run)
    with pytest.raises(SynthesisError, match="timed out"):
        _synthesize_with_asap7_unchecked(_DUT, "m", timeout_s=0.1)
    assert tracked_workdir and not tracked_workdir[0].exists()


def test_asap7_success_cleans_up(tracked_workdir, monkeypatch):
    fake_stdout = "5. Printing statistics.\n\nChip area for module '\\m': 12.5\n"
    monkeypatch.setattr(
        asap7_module.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=fake_stdout, stderr=""),
    )
    result = _synthesize_with_asap7_unchecked(_DUT, "m")
    assert result.area_um2 == 12.5
    assert tracked_workdir and not tracked_workdir[0].exists()
