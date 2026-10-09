"""Copying traces off a slow mount (e.g. sshfs), which ChampSim otherwise streams for the whole
run: copy when it helps, and a failed copy costs speed, never the study.
"""

from __future__ import annotations

from pathlib import Path

from champsim_tools.traces import is_slow_mount, scratch_root
from champsim_tools.traces import stage as stage_traces


def test_no_scratch_means_the_originals_are_used(tmp_path, monkeypatch):
    monkeypatch.delenv("FLUX_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    source = tmp_path / "t.gz"
    source.write_bytes(b"x" * 64)
    assert stage_traces({"a": source}) == {"a": source}


def test_a_fast_source_is_not_copied(tmp_path, monkeypatch):
    """A file already on local disk is not staged."""
    monkeypatch.setattr("champsim_tools.traces.is_slow_mount", lambda _p: False)   # tmp may sit on a network home
    source = tmp_path / "t.gz"
    source.write_bytes(b"x" * 64)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert stage_traces({"a": source}, root=scratch) == {"a": source}
    assert not (scratch / "champsim-traces").exists()


def test_a_slow_source_is_copied_once_and_reused(tmp_path, monkeypatch):
    source = tmp_path / "t.gz"
    source.write_bytes(b"trace-bytes" * 100)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr("champsim_tools.traces.is_slow_mount", lambda _p: True)

    first = stage_traces({"a": source}, root=scratch)
    staged = first["a"]
    assert staged != source
    assert staged.read_bytes() == source.read_bytes()

    # A second call reuses the copy.
    marker = staged.stat().st_mtime_ns
    second = stage_traces({"a": source}, root=scratch)
    assert second["a"] == staged
    assert staged.stat().st_mtime_ns == marker


def test_a_changed_source_is_restaged(tmp_path, monkeypatch):
    """Reuse is keyed on size; a different trace under the same name must not be served stale."""
    source = tmp_path / "t.gz"
    source.write_bytes(b"short")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr("champsim_tools.traces.is_slow_mount", lambda _p: True)
    staged = stage_traces({"a": source}, root=scratch)["a"]
    assert staged.read_bytes() == b"short"

    source.write_bytes(b"a much longer trace than before")
    restaged = stage_traces({"a": source}, root=scratch)["a"]
    assert restaged.read_bytes() == b"a much longer trace than before"


def test_a_failed_copy_falls_back_to_the_originals(tmp_path, monkeypatch):
    """A full scratch disk must cost speed, never the study."""
    source = tmp_path / "t.gz"
    source.write_bytes(b"x" * 64)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr("champsim_tools.traces.is_slow_mount", lambda _p: True)

    def _boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("shutil.copy2", _boom)
    assert stage_traces({"a": source}, root=scratch) == {"a": source}


def test_slow_mount_detection_is_conservative(tmp_path):
    """An unrecognised filesystem is treated as fast: a wrong 'yes' costs a copy every run."""
    assert is_slow_mount(tmp_path) in (True, False)      # never raises
    assert is_slow_mount(Path("/definitely/not/mounted/anywhere")) is False


def test_scratch_root_prefers_the_dev_shells_variable(tmp_path, monkeypatch):
    """flake.nix sets FLUX_TMPDIR deliberately; TMPDIR is the fallback, not the other way round."""
    preferred, fallback = tmp_path / "flux", tmp_path / "tmp"
    preferred.mkdir()
    fallback.mkdir()
    monkeypatch.setenv("FLUX_TMPDIR", str(preferred))
    monkeypatch.setenv("TMPDIR", str(fallback))
    assert scratch_root() == preferred
    monkeypatch.delenv("FLUX_TMPDIR")
    assert scratch_root() == fallback
