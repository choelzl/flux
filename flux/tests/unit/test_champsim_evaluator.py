"""The ChampSim evaluator's contract, without running a six-minute simulation.

What is worth testing here is everything that happens BEFORE the simulator starts, because that is
where a run either fails in one second with a readable message or fails in six minutes with a
stack trace. Three distinct failures are kept distinct on purpose:

  `InvalidConfig`            -- the candidate is illegal; fix the candidate
  `ChampSimUnavailableError` -- no simulator was found; fix the environment
  `SimulationFailedError`    -- it ran and produced nothing usable; investigate

Collapsing those into one "evaluation failed" is how a broken toolchain becomes a search that
quietly reports every design as bad.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_evaluator_abi import Candidate  # noqa: E402
from flux_evaluator_champsim_bingo import (  # noqa: E402
    BINARY_NAME, ChampSimUnavailableError, NotExpressibleError, config_of, resolve_binary,
    resolve_trace,
)
from flux_evaluator_champsim_bingo.adapter import (  # noqa: E402
    _FINISHED, _IPC, SIMULATION_INSTRUCTIONS, WARMUP_INSTRUCTIONS,
)
from flux_prefetcher.config import DEFAULT  # noqa: E402


def _candidate(**arch):
    return Candidate(workload={"trace": "/nonexistent.gz"},
                     arch={"prefetcher": arch} if arch else {}, mapping=None)


def _fields(cfg):
    """A config as the plain field dict an arch block carries."""
    return {f: getattr(cfg, f) for f in cfg.__dataclass_fields__}


def test_reads_a_configuration_out_of_a_candidate():
    cfg, types = config_of(_candidate(kind="bingo", types=["bingo"], **_fields(DEFAULT)))
    assert cfg == DEFAULT
    assert types == ["bingo"]


def test_defaults_to_bingo_when_no_types_are_given():
    _, types = config_of(_candidate(kind="bingo"))
    assert types == ["bingo"]


def test_an_empty_type_list_is_the_no_prefetcher_baseline():
    """`types=[]` is meaningful, not missing: it is how the baseline is measured."""
    _, types = config_of(_candidate(kind="bingo", types=[]))
    assert types == []


def test_a_candidate_without_a_prefetcher_block_is_not_expressible():
    with pytest.raises(NotExpressibleError) as caught:
        config_of(Candidate(workload={"trace": "x"}, arch={"interconnect": {}}, mapping=None))
    assert "prefetcher" in str(caught.value)


def test_an_unknown_knob_is_rejected_rather_than_ignored():
    """A typo silently dropped is a configuration that measured something nobody asked for."""
    with pytest.raises(NotExpressibleError) as caught:
        config_of(_candidate(kind="bingo", bingo_pht_size_typo=4096))
    assert "unknown" in str(caught.value)


def test_a_missing_trace_is_caught_before_anything_runs():
    with pytest.raises(FileNotFoundError) as caught:
        resolve_trace("/definitely/not/here.gz")
    assert "not in git" in str(caught.value), "the message should say why it is absent"


def test_missing_binary_names_every_place_it_looked(monkeypatch, tmp_path):
    """The error a fresh clone hits. It has to be actionable, not 'No such file'."""
    monkeypatch.setenv("FLUX_CHAMPSIM_BIN", str(tmp_path / "nope"))
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(ChampSimUnavailableError) as caught:
        resolve_binary()
    message = str(caught.value)
    for expected in ["FLUX_CHAMPSIM_BIN", "PATH", "nix develop", "pythia", BINARY_NAME]:
        assert expected in message, f"the error never mentions {expected}"


def test_the_error_offers_no_path_that_cannot_succeed():
    """The in-tree `proj/` fallback is gone with the directory it pointed at.

    A resolution path that can no longer succeed is worse than none: it lengthens the error and
    sends the reader looking for a directory nobody has. The simulator now comes from
    `nixchip.packages.pythia`, so that is what the message should name.
    """
    from flux_evaluator_champsim_bingo import binary as mod

    assert not hasattr(mod, "_IN_TREE"), "the dead in-tree fallback is still present"
    assert "pythia" in mod.ON_PATH, "nixchip installs the binary as `pythia`"


def test_the_output_parsers_match_real_champsim_output():
    """Parsed against a genuine baseline line, not one written to match the regex."""
    real = ("Region of Interest Statistics\n"
            "Core_0_IPC 0.69602\n"
            "Finished CPU 0 instructions: 150000000 cycles: 215511490 cumulative IPC: 0.69602\n")
    assert _IPC.search(real).group(1) == "0.69602"
    finished = _FINISHED.search(real)
    assert finished.group(1) == "150000000" and finished.group(2) == "215511490"


def test_instruction_counts_are_the_ones_the_project_actually_ran():
    """run_benchmark.sh passes each flag twice; ChampSim takes the last, and the shipped
    baselines confirm which won ('simulation_instructions 150000000')."""
    assert WARMUP_INSTRUCTIONS == 100_000_000
    assert SIMULATION_INSTRUCTIONS == 150_000_000


def test_multiple_prefetchers_repeat_the_flag_rather_than_comma_joining(monkeypatch, tmp_path):
    """ChampSim never splits this value on commas, so a comma list names one bogus prefetcher.

    `knobs.cc` does `l2c_prefetcher_types.push_back(string(value))` on the whole value. Passing
    `bingo,sms` therefore registers a prefetcher called "bingo,sms" and the simulator exits with
    "unsupported prefetcher type" — silently, as far as a caller reading only an exit code is
    concerned. Repeating the flag is what fills the vector.
    """
    import subprocess

    from flux_evaluator_champsim_bingo import adapter
    from flux_prefetcher.config import DEFAULT

    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "Core_0_IPC 1.00000\n", "")

    binary = tmp_path / "champsim"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    trace = tmp_path / "t.gz"
    trace.write_bytes(b"x")
    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter.run_champsim(DEFAULT, trace, types=["bingo", "sms", "ampm"], binary=binary)
    flags = [a for a in seen["cmd"] if a.startswith("--l2c_prefetcher_types=")]
    assert flags == ["--l2c_prefetcher_types=bingo",
                     "--l2c_prefetcher_types=sms",
                     "--l2c_prefetcher_types=ampm"]
    assert not any("," in f for f in flags), "a comma list names one prefetcher, not three"


def test_no_prefetcher_emits_no_type_flag_at_all(monkeypatch, tmp_path):
    """The baseline. An empty list must not produce `--l2c_prefetcher_types=`."""
    import subprocess

    from flux_evaluator_champsim_bingo import adapter
    from flux_prefetcher.config import DEFAULT

    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "Core_0_IPC 1.00000\n", "")

    binary = tmp_path / "champsim"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    trace = tmp_path / "t.gz"
    trace.write_bytes(b"x")
    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter.run_champsim(DEFAULT, trace, types=[], binary=binary)
    assert not [a for a in seen["cmd"] if a.startswith("--l2c_prefetcher_types")]
