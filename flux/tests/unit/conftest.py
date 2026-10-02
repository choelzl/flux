from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


import pytest

# A test never reads this machine's ~/.config/flux/flux.env (D651): `main()` would load its model
# settings into the process and leak them into later tests.
os.environ["FLUX_CONFIG"] = os.devnull + ".flux-tests"
os.environ["FLUX_SANDBOX"] = "0"             # the tests run flux in-process; tests/unit/test_sandbox.py covers the sandbox

FLUX_ROOT = Path(__file__).resolve().parents[2]

# Every local package on sys.path once, from the flake's list (D404): harmless inside the dev
# shell, needed outside it. conftest loads before any test module, so every test file sees it.
_block = re.search(r"localSrcDirs = \[(.*?)\];", (FLUX_ROOT / "flake.nix").read_text(), re.S)
for _d in re.findall(r'"([^"]+/src)"', _block.group(1)) if _block else []:
    _p = str(FLUX_ROOT / _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The shared library is this machine's papers (D648): a unit test reads an empty one unless it
# makes its own, so prompts are the same on every machine.
os.environ["FLUX_LIBRARY"] = tempfile.mkdtemp(prefix="flux-library-")

# (kind, example_path) pairs covering the DNN-accelerator and general-SoC cases (D1), per IR category.
IR_EXAMPLES = [
    ("workload", FLUX_ROOT / "core/ir/workload/examples/llama3-8b-decode-layer0.yaml"),
    ("workload", FLUX_ROOT / "core/ir/workload/examples/soc-dma-desc-fetch.yaml"),
    ("workload", FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"),
    ("architecture", FLUX_ROOT / "core/ir/architecture/examples/my-npu-v3.yaml"),
    ("architecture", FLUX_ROOT / "core/ir/architecture/examples/generic-riscv-soc-v1.yaml"),
    ("mapping", FLUX_ROOT / "core/ir/mapping/examples/attn-qk-map0.yaml"),
    ("mapping", FLUX_ROOT / "core/ir/mapping/examples/dma-desc-fetch-map0.yaml"),
]


#: The heavy files (D531): real tools or whole studies, each taking about a minute or more.
#: Run with `-m heavy`; the core is everything else.
HEAVY_FILES = {
    "test_nlu_family.py", "test_nlu_transpile.py", "test_nlu_framework.py", "test_nlu_blocks.py",
    "test_nlu_vectorize.py", "test_nlu_hygiene.py", "test_macarray.py", "test_imapping.py",
    "test_feedback_consumers.py", "test_mentor_records_extract.py", "test_design_guidance_corpus.py",
    "test_bankmap.py", "test_shortlist.py", "test_instruments.py",
    "test_prototype_hardware_subset.py", "test_library_source_connector.py",
}


#: Single tests of the core that take 25 s or more (D639): a real prototype-to-RTL run, a CLI
#: example, a whole loop. The rest of their files stays in the core.
HEAVY_TESTS = {
    "test_golden_prototype.py::test_a_spelled_design_sent_back_gets_a_cost_pass_on_its_prototype",
    "test_golden_prototype.py::test_the_prototype_is_proven_then_transcribed",
    "test_golden_prototype.py::test_a_coding_agent_writes_the_prototype_and_the_loop_checks_it",
    "test_golden_prototype.py::test_the_prototype_agent_is_resumed_until_its_prototype_passes",
    "test_golden_prototype.py::test_a_spelled_design_on_record_is_not_synthesised_again_once_over_the_ceiling",
    "test_golden_prototype.py::test_a_prototype_over_the_ceiling_is_made_cheaper_before_anything_is_built",
    "test_flux_new.py::test_a_sweep_phase_moves_only_its_knobs",
    "test_flux_new.py::test_the_sweep_runs_to_a_decision_without_a_model",
    "test_escalation.py::test_every_stage_written_in_this_repository_resolves",
    "test_loop_live.py::test_the_digits_example_runs_through_the_cli_and_writes_its_artifact",
    "test_py2sv.py::test_each_construct_is_spelled_bit_for_bit",
    "test_campaign_record.py::test_the_loop_records_each_dse_phase_by_name",
    "test_knowledge_retrieval.py::test_default_index_cites_paths_from_the_repo_root",
    "test_systemc_prototype.py::test_icsc_translates_the_verified_module_and_the_gate_proves_it",
    "test_prefetcher_document.py::test_the_model_writes_the_file_and_it_is_measured_on_a_fake_champsim",
    "test_pareto_uct.py::test_the_pareto_phase_spends_the_same_budget_on_at_least_as_much_frontier",
}


def pytest_configure(config: pytest.Config) -> None:
    """Scratch in memory when the machine has room (D715): the tests' records are SQLite, and each
    commit's fsync costs 20-70 ms on a disk (one test paid 20 s for 305 commits; on a home over
    sshfs, more) and nothing on tmpfs. The tests' own directories and the TMPDIR of what they run.
    `--basetemp` or FLUX_TEST_SHM=0 keeps pytest's default."""
    if config.option.basetemp or hasattr(config, "workerinput") or os.environ.get("FLUX_TEST_SHM") == "0":
        return
    shm = Path("/dev/shm")
    try:
        roomy = shm.is_dir() and os.access(shm, os.W_OK) and shutil.disk_usage(shm).free > 4 << 30
    except OSError:
        roomy = False
    if roomy:
        base = tempfile.mkdtemp(prefix="flux-pytest-", dir=shm)
        config.option.basetemp = str(Path(base) / "t")        # xdist hands each worker its own folder under it
        (Path(base) / "tmp").mkdir()
        for k in ("TMPDIR", "TMP", "TEMP"):                   # the workers start after this: they inherit it
            os.environ[k] = str(Path(base) / "tmp")
        tempfile.tempdir = None
        config._flux_shm = base                               # type: ignore[attr-defined]


def pytest_unconfigure(config: pytest.Config) -> None:
    base = getattr(config, "_flux_shm", None)
    if base:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(autouse=True)
def _signals_as_they_were():
    """A test that runs a process entry point in this process (`flux_web.stamp.main` ignores
    SIGINT) leaves it ignored for every later test -- and every child process inherits an ignored
    signal across exec, so a later "stop now" reaches nothing. Each test's handlers are undone."""
    import signal

    kept = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    yield
    for s, handler in kept.items():
        signal.signal(s, handler)


@pytest.fixture(autouse=True)
def _own_trace_root(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Every test registers its runs and traces under its own root, so parallel tests do not race (D531)."""
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path_factory.mktemp("traces")))
    # D744: a sandboxed run's HOME is a Flux home; a test never starts one in the real home
    monkeypatch.setenv("FLUX_SANDBOX_HOME", str(tmp_path_factory.mktemp("flux-home")))
    # D745: a container command's variables file goes in the run's own runtime folder: a test's, not the machine's
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path_factory.mktemp("runtime")))
    monkeypatch.setenv("FLUX_DIGESTS", str(tmp_path_factory.mktemp("digests")))   # D794: no test reads another's


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Every test in a heavy file, and every heavy test, carries the `heavy` marker (D531, D639)."""
    for item in items:
        name = Path(str(item.fspath)).name
        if name in HEAVY_FILES or f"{name}::{item.originalname}" in HEAVY_TESTS:
            item.add_marker(pytest.mark.heavy)


@pytest.fixture(params=IR_EXAMPLES, ids=[p.stem for _, p in IR_EXAMPLES])
def ir_example(request: pytest.FixtureRequest) -> tuple[str, Path]:
    return request.param
