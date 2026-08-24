"""A step of the evaluation chain is a STAGE, not a rung (docs/decisions.md D466).

Cedric: "a rung is not an intuitive name/term" -- and his own description of the loop had the
better word already ("single stage evaluation or multi-stage"). The rename is mechanical
everywhere, which is exactly why it needs a guard: one file left behind means two words for one
thing, and the next reader has to learn both.

What these pin: the vocabulary is `stage` in the loop's own surface, a campaign file written
before the rename keeps its data and gains the new column names when it is opened, and the old
word does not survive anywhere in the code or the current docs (the decision log's past entries
keep their words on purpose -- it is an append-only record of what was decided when).
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

FLUX = pathlib.Path(__file__).resolve().parents[2]
ROOT = FLUX.parent


def test_the_loops_vocabulary_is_stage():
    import flux_loop

    assert hasattr(flux_loop, "StageNames") and not hasattr(flux_loop, "RungNames")
    assert flux_loop.Problem.stages(flux_loop.Problem()) == ["screen"]
    assert not hasattr(flux_loop.Problem, "rungs")
    scored = flux_loop.Scored(flux_loop.Candidate("c", ""), "screen", {"x": 1.0}, {})
    assert scored.stage == "screen" and not hasattr(scored, "rung")
    for hook in ("analytic_stages", "evaluator_name", "cutoff", "route"):
        assert hasattr(flux_loop.Problem, hook), hook


def test_a_document_declares_stages():
    from flux_loop import PromptProblem, TaskSpec

    task = TaskSpec.from_dict({
        "id": "renamed", "statement": "write it", "extension": ".txt",
        "gate": {"test": ["true"]},
        "stages": [{"name": "size", "command": ["wc", "-c", "{artifact}"],
                    "metrics_re": {"bytes": r"(\d+)"}}]})
    assert [s.name for s in task.stages] == ["size"]
    assert PromptProblem(task).stages() == ["size"]
    assert "stages" in task.to_dict() and "rungs" not in task.to_dict()


def test_a_campaign_written_before_the_rename_still_opens(tmp_path):
    """The columns are renamed in place on open: nothing is copied and nothing is lost.

    Built the honest way round -- a file the current store wrote, with its two columns put back
    to the old names, which is exactly what every campaign db on disk looks like.
    """
    from flux_store import CampaignStore

    db = str(tmp_path / "old.db")
    store = CampaignStore(db)
    cid, _new = store.start_campaign({"study": "renamed"}, "h1")
    seq = store.begin_trial(cid, phase="screen", candidate={"w": 8}, candidate_key="k1",
                            workload_hash="", arch_hash=None, strategy_kind="loop", stage="placement")
    store.complete_trial(cid, seq, status="ok", result=None, error=None, wall_clock_s=0.5)
    store.close()

    conn = sqlite3.connect(db)                     # back to how it was written before D466
    conn.execute("ALTER TABLE trials RENAME COLUMN stage TO rung")
    conn.execute("ALTER TABLE trials ADD COLUMN rung_index INTEGER")       # the old file had the index too
    conn.commit()
    conn.close()

    store = CampaignStore(db)
    columns = {row[1] for row in store._conn.execute("PRAGMA table_info(trials)")}
    assert "stage" in columns and "rung" not in columns
    # the index column is renamed with it and then dropped: schema v2 has no stage index (D524)
    assert "rung_index" not in columns and "stage_index" not in columns
    (trial,) = store.trials(cid)
    assert trial.stage == "placement", "the data came with it"
    assert trial.candidate == {"w": 8} and trial.status == "ok"
    store.close()

    reopened = CampaignStore(db)                   # and opening a migrated file is a no-op
    assert [t.stage for t in reopened.trials(cid)] == ["placement"]
    reopened.close()


def test_the_old_word_is_gone_from_the_code_and_the_current_docs():
    """The decision log keeps its history: an entry says what was decided when it was written,
    and rewriting D351's words would make the record a lie. Everything else moved."""
    # The migration in `flux_store.campaign` is the one place that MUST still know the old
    # column names, because it is the one place that meets a file written before the rename.
    kept = {ROOT / "docs" / "decisions.md",
            FLUX / "core" / "stores" / "src" / "flux_store" / "campaign.py"}
    skip_dirs = {"__pycache__", ".git", "site", "vendor", "result", ".nix-bin"}
    left: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if path.suffix not in {".py", ".md", ".json", ".yaml", ".yml", ".nix", ".toml"}:
            continue
        if path in kept or any(part in skip_dirs for part in path.parts):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if "rung" in text.lower() and path.name != "test_stage_rename.py":
            left.append(str(path.relative_to(ROOT)))
    assert not left, f"the old word survives in: {left}"


def test_the_store_still_knows_what_a_stage_is_called_in_an_older_file():
    """The migration names the old columns, so the one place that has to remember the old word
    is the one place that meets old files."""
    from flux_store import campaign

    source = pathlib.Path(campaign.__file__).read_text()
    assert '(("rung", "stage"), ("rung_index", "stage_index"))' in source
