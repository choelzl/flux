"""Any tool's output as `name=value` (D973), so a tool that does not speak Flux needs no script.

    fluxpreset extract --pick results.latency.mean=latency_ms --pick results.ok=ok -- mytool --json design.c
    fluxpreset extract --file out.csv --format csv --pick runtime=time_ms --row mean -- mytool design.c
    fluxpreset extract --format regex --pick "took ([0-9.]+) ms=time_ms" -- mytool design.c

The command after `--` runs; its stdout (or `--file`, written by it) is read as `--format`: `json`
(a dotted path into the object, list items by index), `csv` (a column, its `--row`: first, last,
mean, min or max) or `regex` (one capture group, the first match). Each `--pick SOURCE=NAME` prints
`NAME=value`. A command that fails, or a pick that finds nothing, exits 1.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import statistics
from pathlib import Path
from typing import Any

from ._run import command, run


def catalog() -> list[dict]:
    """This preset's catalog entry (D973)."""
    from ._entries import param
    return [{"id": "extract", "role": "stage", "title": "Numbers out of any tool's output",
             "what": "Runs your tool and reads its JSON, CSV or text output, printing the numbers you pick as name=value.",
             "run": "fluxpreset extract --format {format} --pick {pick} -- {command}",
             "params": {"format": param("Format (json, csv, regex)", "json"), "pick": param("Pick (source=name)", "value=metric"),
                        "command": param("Tool command", "")},
             "metrics": {}, "needs": [], "languages": [], "kinds": ["own"], "short": "extract"}]


def _json(data: Any, path: str) -> Any:
    for key in path.split("."):
        data = data[int(key)] if isinstance(data, list) else data[key]
    return data


def _csv(text: str, column: str, row: str) -> float:
    values = [float(r[column]) for r in csv.DictReader(io.StringIO(text)) if (r.get(column) or "").strip()]
    if not values:
        raise KeyError(column)
    return {"first": values[0], "last": values[-1], "mean": statistics.fmean(values), "min": min(values),
            "max": max(values)}[row]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset extract", description=__doc__.split("\n\n")[0])
    ap.add_argument("--format", choices=("json", "csv", "regex"), default="json")
    ap.add_argument("--pick", action="append", required=True, help="SOURCE=NAME: a JSON path, a CSV column or a regex")
    ap.add_argument("--file", default="", help="read this file (the command writes it) instead of stdout")
    ap.add_argument("--row", choices=("first", "last", "mean", "min", "max"), default="last")
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument("rest", nargs=argparse.REMAINDER, help="-- the tool's command")
    a = ap.parse_args(argv)
    if not a.rest or a.rest == ["--"]:
        ap.error("give the tool's command after --")
    picks = []
    for p in a.pick:
        source, sep, name = p.rpartition("=")
        if not sep or not source or not re.fullmatch(r"[A-Za-z_][\w.]*", name):
            ap.error(f"--pick is SOURCE=NAME, not {p!r}")
        picks.append((source, name))
    r = run(command(a.rest), a.timeout)
    if r.returncode != 0:
        print(f"the tool exited {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}")
        return 1
    text = Path(a.file).read_text() if a.file else r.stdout
    out, missing = [], []
    try:
        data = json.loads(text) if a.format == "json" else None
    except json.JSONDecodeError as exc:
        print(f"the output is not JSON: {exc}")
        return 1
    for source, name in picks:
        try:
            if a.format == "json":
                value = float(_json(data, source))
            elif a.format == "csv":
                value = _csv(text, source, a.row)
            else:
                m = re.search(source, text, re.M)
                if not m:
                    raise KeyError(source)
                value = float(m.group(1))
        except (KeyError, IndexError, TypeError, ValueError):
            missing.append(f"{source} (for {name})")
            continue
        out.append(f"{name}={value:.6g}")
    if missing:
        print("not found: " + ", ".join(missing))
        return 1
    print(" ".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
