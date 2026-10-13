"""A stand-in for pto-spec's page runner in Flux's tests: the tests at once, one result each, and a
mark that it ran (the preset must use it when it is there)."""
import json
import os
from pathlib import Path


def execute_matrix(root: Path, entries, *, jobs, timeout_seconds, output):
    assert os.environ.get("ASLREF"), "ASLREF not given"
    results = root / "build" / "asl-test-results"
    results.mkdir(parents=True, exist_ok=True)
    (root / "build" / "paged").write_text(str(jobs))
    for e in entries:
        text = next(p.read_text() for p in (root / "tests").rglob("*.asl") if f'"id":"{e["id"]}"' in p.read_text())
        ok = "PASS" in text
        d = results / e["id"]
        d.mkdir()
        (d / "result.json").write_text(json.dumps({"status": "passed" if ok else "failed"}))
        (d / "aslref.log").write_text("" if ok else "ASL Dynamic error (DE_DAF): Assertion failed: paged\n{\n")
    return []
