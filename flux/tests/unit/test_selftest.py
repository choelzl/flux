"""`flux selftest` runs what a newcomer would and says PASS / FAIL / SKIP per check (D623)."""

from __future__ import annotations

import argparse


def test_selftest_without_a_model_or_rtl_tools_runs_the_sweep_and_skips_the_rest(capsys, monkeypatch):
    import shutil

    from flux_cli import selftest

    real = shutil.which
    monkeypatch.setattr(selftest.shutil, "which", lambda t: None if t in ("verilator", "yosys", "opencode", "claude", "codex") else real(t))
    rc = selftest.cmd_selftest(argparse.Namespace(full=False, no_model=True, model=None, model_timeout=60.0))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "PASS  a command-driven loop, no model" in out and "SKIP  the model" in out
    assert "6 of 6" not in out and "checks passed or skipped" in out
