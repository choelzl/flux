"""D1015: the results table's column labels -- a dictionary's column is its own name, folded or not;
each test what tells it from its siblings, not a cut-off name."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).parents[2] / "interfaces/web/src/flux_web/static"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def labels(metrics, groups):
    script = """const src = require('fs').readFileSync(process.argv[1], 'utf8');
const a = src.indexOf('export function measurementLabels'), b = src.indexOf('export function measurementHeader');
eval(src.slice(a, b).replace('export function', 'function'));
const [metrics, groups] = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(Object.fromEntries(measurementLabels(metrics, groups))));"""
    r = subprocess.run(["node", "-e", script, str(STATIC / "results/preferences.js"), json.dumps([metrics, groups])],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


SIM = {"sim_us": {"type": "dict", "aggregate": "sum", "metrics": ["sim_us", "sim_us.spec_ca_server_3freq_lf_mcf",
                                                                  "sim_us.spec_ca_server_3freq_lf_gcc", "sim_us.spec_ca_server_3freq_lf_omnetpp"]}}


def test_a_folded_dictionary_is_its_own_name():
    assert labels(["sim_us", "area"], SIM) == {"sim_us": "sim_us", "area": "area"}


def test_unrolled_tests_are_named_by_what_tells_them_apart():
    got = labels(SIM["sim_us"]["metrics"] + ["area"], SIM)
    assert got == {"sim_us": "sim_us", "sim_us.spec_ca_server_3freq_lf_mcf": "mcf", "sim_us.spec_ca_server_3freq_lf_gcc": "gcc",
                   "sim_us.spec_ca_server_3freq_lf_omnetpp": "omnetpp", "area": "area"}


def test_a_number_keeps_the_word_before_it():
    g = {"t": {"type": "dict", "aggregate": "mean", "metrics": ["t", "t.run_1_ms", "t.run_2_ms"]}}
    assert labels(["t", "t.run_1_ms", "t.run_2_ms"], g) == {"t": "t", "t.run_1_ms": "run 1", "t.run_2_ms": "run 2"}


def test_a_metric_keeps_its_raw_name():
    """results-9: one spelling everywhere -- fmax_mhz in the table as in the detail, Compare and Graphs."""
    assert labels(["fmax_mhz", "area_um2"], {}) == {"fmax_mhz": "fmax_mhz", "area_um2": "area_um2"}
