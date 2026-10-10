"""The generic ChampSim evaluator against a FAKE ChampSim (no simulator, no compiler).

The fake binary is a python script reached through $FLUX_CHAMPSIM_BIN: it parses the flags and
the ini, prints ChampSim-shaped output with an IPC hashed from (ini, types, trace) -- the baseline
lower -- and logs each invocation. Its tree has a Makefile whose "compiler" fails on a header
containing COMPILE_ERROR and otherwise writes a binary that knows the registered prefetchers.
"""

from __future__ import annotations

import math
import os
import stat
from pathlib import Path

import pytest

from champsim_tools.commands import main
from champsim_tools import build_header, measure, simulate
from champsim_tools.baseline import baseline_ipc

FAKE_SIM = r'''#!/usr/bin/env python3
import hashlib, os, sys
REGISTERED = __REGISTERED__
args = sys.argv[1:]
flags = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in args if a.startswith("--") and "=" in a}
types = [a.split("=", 1)[1] for a in args if a.startswith("--l2c_prefetcher_types=")]
trace = args[args.index("-traces") + 1]
ini = open(flags["--config"]).read() if "--config" in flags else ""
with open(os.environ["FAKE_CHAMPSIM_LOG"], "a") as log:
    log.write(" ".join(types) + "|" + os.path.basename(trace) + "\n")
if "l2c_prefetcher_types" in ini:
    sys.exit("the types line reached ChampSim")
for t in types:
    if t not in REGISTERED:
        sys.stderr.write("unsupported prefetcher type " + t + "\n"); sys.exit(1)
def h(s): return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)
base = 0.5 + (h(os.path.basename(trace)) % 1000) / 2000
active = [t for t in types if not t.startswith("inert")]
ipc = base * (1.02 + (h(ini + ",".join(types)) % 100) / 1000) if active else base
sim = int(flags["--simulation_instructions"])
print("Finished CPU 0 instructions: %d cycles: %d cumulative IPC: %.5f" % (sim, int(sim / ipc), ipc))
print("Core_0_IPC %.5f" % ipc)
issued = 1000 * len(active)
print("Core_0_L2C_prefetch_issued %d" % issued)
print("Core_0_L2C_prefetch_useful %d" % (issued // 2))
print("Core_0_L2C_prefetch_late %d" % (issued // 10))
'''

FAKE_CC = r'''import os, re, stat, sys, glob
for h in glob.glob("inc/*.h"):
    if "COMPILE_ERROR" in open(h).read():
        print(h + ":3:5: error: expected ';' before '}' token"); print("more cascade"); sys.exit(1)
names = re.findall(r'compare\("(\w+)"\)', open("prefetcher/l2c_prefetcher.cc").read())
os.makedirs("bin", exist_ok=True)
open("bin/champsim", "w").write(open("fake_sim.py").read().replace("__REGISTERED__", repr(names)))
os.chmod("bin/champsim", 0o755)
'''

MULTI = ('#include "prefetcher.h"\n\nvoid CACHE::l2c_prefetcher_initialize()\n{\n'
         '\tfor(uint32_t index = 0; index < knob::l2c_prefetcher_types.size(); ++index)\n\t{\n'
         '\t\tif(!knob::l2c_prefetcher_types[index].compare("none"))\n\t\t{\n\t\t}\n'
         '\t\telse if(!knob::l2c_prefetcher_types[index].compare("stride"))\n\t\t{\n\t\t}\n\t}\n}\n')
KNOBS = ('namespace knob\n{\n\tuint64_t warmup_instructions = 1000000;\n\tuint64_t simulation_instructions = 1000000;\n}\n'
         'void parse()\n{\n    if (MATCH("", "warmup_instructions"))\n    {\n\t\tknob::warmup_instructions = atol(value);\n    }\n'
         '    else if (MATCH("", "simulation_instructions"))\n    {\n\t\tknob::simulation_instructions = atol(value);\n    }\n}\n')

HEADER = '''#ifndef EXSTRIDE_H
#define EXSTRIDE_H
#include "prefetcher.h"
namespace knob { extern uint32_t exstride_degree; }
// KNOBS: exstride_degree=2
class ExstridePrefetcher : public Prefetcher
{
public:
   ExstridePrefetcher(std::string type) : Prefetcher(type) {}
   void invoke_prefetcher(uint64_t pc, uint64_t address, uint8_t, uint8_t, std::vector<uint64_t> &pref_addr) {}
   void dump_stats() {}
   void print_config() {}
};
#endif
'''


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    tree = tmp_path / "pythia"
    for rel, text in {
        "Makefile": "all:\n\tpython3 fakecc.py\n", "fakecc.py": FAKE_CC, "fake_sim.py": FAKE_SIM,
        "prefetcher/multi.l2c_pref": MULTI, "prefetcher/multi.l1d_pref": "", "prefetcher/no.llc_pref": "",
        "branch/perceptron.bpred": "", "replacement/ship.llc_repl": "", "src/knobs.cc": KNOBS, "inc/prefetcher.h": "",
    }.items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(text)
    exe = tree / "bin" / "pythia"
    exe.parent.mkdir()
    exe.write_text(FAKE_SIM.replace("__REGISTERED__", repr(["none", "stride", "bingo", "next_line"])))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    traces = tmp_path / "traces"
    traces.mkdir()
    for name in ("alpha.champsim.gz", "beta_2.champsim.xz", "notes.txt"):
        (traces / name).write_bytes(os.urandom(64) + name.encode())
    log = tmp_path / "invocations.log"
    log.touch()
    monkeypatch.setenv("FLUX_CHAMPSIM_BIN", str(exe))
    monkeypatch.setenv("FAKE_CHAMPSIM_LOG", str(log))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FLUX_TMPDIR", str(tmp_path / "scratch"))
    (tmp_path / "scratch").mkdir()
    return {"tree": tree, "exe": exe, "traces": traces, "log": log, "tmp": tmp_path}


def _calls(fake) -> list[str]:
    return fake["log"].read_text().splitlines()


def test_simulate_parses_ipc_and_the_l2_prefetch_counters(fake):
    got = simulate(None, "bingo_region_size = 2048\n", ["bingo"], fake["traces"] / "alpha.champsim.gz", 100, 1000)
    assert 0.5 <= got["ipc"] <= 1.1
    assert got["l2_pf_issued"] == 1000 and got["l2_pf_useful"] == 500 and got["l2_pf_late"] == 100
    assert got["cycles"] == int(1000 / got["ipc"]) or abs(got["cycles"] - 1000 / got["ipc"]) < 2


def test_measure_an_ini_strips_the_types_line_and_reports_every_trace(fake):
    ini = fake["tmp"] / "bingo.ini"
    ini.write_text("bingo_region_size = 2048\nl2c_prefetcher_types = bingo\n")
    got = measure(ini, fake["traces"], 100, 1000, with_types=["next_line"])
    for t in ("alpha", "beta_2"):
        assert got[f"speedup_{t}"] == pytest.approx(got[f"ipc_{t}"] / got[f"baseline_ipc_{t}"])
        assert got[f"speedup_{t}"] > 1
        assert got[f"l2_pf_issued_{t}"] == 2000
    assert got["geomean_speedup"] == pytest.approx(math.sqrt(got["speedup_alpha"] * got["speedup_beta_2"]))
    assert "bingo next_line|alpha.champsim.gz" in _calls(fake)


def test_the_baseline_is_computed_once_and_cached(fake):
    trace = fake["traces"] / "alpha.champsim.gz"
    first = baseline_ipc(None, trace, 100, 1000)
    assert baseline_ipc(None, trace, 100, 1000) == first
    assert _calls(fake) == ["|alpha.champsim.gz"]
    assert (fake["tmp"] / "cache" / "flux" / "champsim" / "baseline.json").is_file()
    baseline_ipc(None, trace, 100, 2000)                      # another length is another key
    assert len(_calls(fake)) == 2


def test_cli_run_prints_name_value_lines(fake, capsys):
    ini = fake["tmp"] / "k.ini"
    ini.write_text("l2c_prefetcher_types = bingo,stride\n")
    assert main(["run", str(ini), "--traces", str(fake["traces"]), "--warmup", "100", "--sim", "1000"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("geomean_speedup=")
    names = {ln.split("=")[0] for ln in lines}
    assert {"ipc_alpha", "baseline_ipc_beta_2", "l2_pf_useful_alpha", "speedup_beta_2"} <= names
    assert all("=" in ln for ln in lines)


def test_build_installs_a_header_and_caches_the_binary(fake, capsys):
    got = build_header(HEADER)
    assert got.ok and got.name == "exstride" and not got.cached
    again = build_header(HEADER)
    assert again.cached and again.binary == got.binary
    # the built binary knows the new prefetcher; the header's knob default reached knobs.cc
    ran = simulate(got.binary, "", ["exstride"], fake["traces"] / "alpha.champsim.gz", 100, 1000)
    assert ran["l2_pf_issued"] == 1000
    header = fake["tmp"] / "exstride.h"
    header.write_text(HEADER)
    assert main(["build", str(header)]) == 0
    assert capsys.readouterr().out.splitlines() == ["0 failing"]


def test_the_install_wires_knobs_and_the_dispatch(fake, tmp_path):
    from champsim_tools.build import install, stage_tree

    tree = stage_tree(fake["tree"], tmp_path / "copy")
    assert install(HEADER, tree) == "exstride"
    assert "uint32_t exstride_degree = 2;" in (tree / "src" / "knobs.cc").read_text()
    assert 'MATCH("", "exstride_degree")' in (tree / "src" / "knobs.cc").read_text()
    multi = (tree / "prefetcher" / "multi.l2c_pref").read_text()
    assert '#include "flux_exstride.h"' in multi and 'compare("exstride")' in multi
    assert multi.index('compare("exstride")') < multi.index('else if(!knob::l2c_prefetcher_types[index].compare("none"))')


def test_build_failure_prints_the_first_error_and_exits_3(fake, capsys):
    header = fake["tmp"] / "bad.h"
    header.write_text(HEADER.replace("public:", "public: COMPILE_ERROR"))
    assert main(["build", str(header)]) == 3
    out = capsys.readouterr().out.splitlines()
    assert out == ["inc/flux_exstride.h:3:5: error: expected ';' before '}' token", "1 failing"]
    header.write_text("int x;\n")                         # no Prefetcher class at all
    assert main(["build", str(header)]) == 3


def test_check_refuses_a_prefetcher_that_issues_nothing(fake, capsys):
    good = fake["tmp"] / "good.h"
    good.write_text(HEADER)
    assert main(["check", str(good), "--traces", str(fake["traces"])]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "0 failing"
    inert = fake["tmp"] / "inert.h"
    inert.write_text(HEADER.replace("Exstride", "InertStride"))
    assert main(["check", str(inert), "--traces", str(fake["traces"])]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == "1 failing: issued no prefetches"


def test_cli_run_on_a_header_builds_it_and_runs_it_with_its_partners(fake, capsys):
    header = fake["tmp"] / "exstride.h"
    header.write_text(HEADER)
    assert main(["run", str(header), "--traces", str(fake["traces"]), "--warmup", "100",
                 "--sim", "1000", "--with", "stride", "--jobs", "1"]) == 0
    assert "l2_pf_issued_alpha=2000" in capsys.readouterr().out.splitlines()
    assert "exstride stride|alpha.champsim.gz" in _calls(fake)


def test_a_header_run_takes_its_partners_knobs_from_config(fake):
    header, knobs = fake["tmp"] / "exstride.h", fake["tmp"] / "knobs.ini"
    header.write_text(HEADER)
    knobs.write_text("stride_degree = 2\nl2c_prefetcher_types = stride\n")
    got = measure(header, fake["traces"], 100, 1000, config=knobs)
    assert got["l2_pf_issued_alpha"] == 2000
    assert "exstride stride|alpha.champsim.gz" in _calls(fake)
