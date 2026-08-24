"""Copying inputs off a slow mount, and never letting that copy break a study.

This exists because of a measurement, not a hunch. The repository is commonly checked out on an
sshfs mount; reading from it runs at 114 MB/s against 6.4 GB/s for the local disk beside it, and
ChampSim streams its entire trace through a pipe for the whole run. Twelve concurrent simulations
took over three times their solo time each while sitting at 21% CPU -- a shape that looks like
nothing in particular until you check what filesystem the file is on.

The two properties that matter are that it copies when copying helps, and that a failure to copy
costs speed rather than the run.
"""

from __future__ import annotations

from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_prefetcher.staging import is_slow_mount, scratch_root, stage_traces  # noqa: E402


def test_no_scratch_means_the_originals_are_used(tmp_path, monkeypatch):
    monkeypatch.delenv("FLUX_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    source = tmp_path / "t.gz"
    source.write_bytes(b"x" * 64)
    assert stage_traces({"a": source}) == {"a": source}


def test_a_fast_source_is_not_copied(tmp_path):
    """Staging a local file onto local disk is pure cost. It must not happen."""
    source = tmp_path / "t.gz"
    source.write_bytes(b"x" * 64)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert stage_traces({"a": source}, root=scratch) == {"a": source}
    assert not (scratch / "prefetcher-traces").exists()


def test_a_slow_source_is_copied_once_and_reused(tmp_path, monkeypatch):
    source = tmp_path / "t.gz"
    source.write_bytes(b"trace-bytes" * 100)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr("flux_prefetcher.staging.is_slow_mount", lambda _p: True)

    first = stage_traces({"a": source}, root=scratch)
    staged = first["a"]
    assert staged != source
    assert staged.read_bytes() == source.read_bytes()

    # Second call must reuse, not recopy: a study that restages 380 MB every run is worse than
    # one that never staged.
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
    monkeypatch.setattr("flux_prefetcher.staging.is_slow_mount", lambda _p: True)
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
    monkeypatch.setattr("flux_prefetcher.staging.is_slow_mount", lambda _p: True)

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
