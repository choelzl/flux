"""Every protocol interface `flux_protocols` can emit is real SystemVerilog, proven by running
Verilator on it (docs/decisions.md D177).

The unit tests check that directions and widths come out as the source documents state. This checks
the other half — that the result is something a real tool accepts — which is the part a hand-written
assertion cannot fake. Requires `verilator`, so `nix develop .#default`, not `.#python`.

**What this does and does not prove.** It proves the emitter produces well-formed SystemVerilog with
resolvable widths. It does not prove the protocol documents are right about their protocols: a
reversed direction in the YAML produces a module that lints perfectly and is wrong. Only provenance
speaks to that, which is why the emitted header carries it.

Linting is deliberately without `-Wall`, unlike this repo's generated *designs* (D153/D154 kept that
strict). These modules are intentionally empty — an interface with no behaviour behind it — so every
output is undriven and every input unused by construction. Under `-Wall` all nine fail on exactly
those two warnings, which would be a check calibrated to the wrong artefact rather than a real
finding.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from flux_protocols import ProtocolRegistry, derive_stream_from_bus, emit_sv_module

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None, reason="verilator not on PATH (use nix develop .#default)"
)

# Concrete values for every parameter the shipped documents name, plus the two widths WISHBONE
# leaves to a core's DATASHEET rather than fixing in the specification.
_PARAMETERS = {
    "ADDR_WIDTH": 32, "DATA_WIDTH": 32, "MID_WIDTH": 2,
    "AUSER_WIDTH": 2, "WUSER_WIDTH": 2, "RUSER_WIDTH": 2, "ACHK_WIDTH": 4, "RCHK_WIDTH": 4,
    "AXI_ADDR_WIDTH": 32, "AXI_DATA_WIDTH": 64, "AXI_STRB_WIDTH": 8, "AXI_ID_WIDTH": 4,
    "AXI_USER_WIDTH": 2,
    "KEEP_WIDTH": 4, "ID_WIDTH": 8, "DEST_WIDTH": 8, "USER_WIDTH": 1,
    "PORT_SIZE": 32, "GRANULARITY": 8,
}
_WIDTHS = {"ADR_O": 30, "SEL_O": 4, "TGD_I": 1, "TGD_O": 1}


def _emittable_targets():
    registry = ProtocolRegistry()
    targets = []
    for protocol in registry.all():
        if protocol.kind == "definitions":
            continue
        for role in protocol.roles:
            if role == "syscon":  # a generator, not an interface a core presents
                continue
            targets.append(pytest.param(protocol, role, id=f"{protocol.id}-{role}"))
    derived = derive_stream_from_bus(registry.get("axi4", "pulp-0.39.10"))
    targets.append(pytest.param(derived, "source", id="axi4-stream-derived-source"))
    return targets


def test_there_are_targets_to_check():
    """Guards the guard: an empty target list would make every case below vacuous."""
    assert len(_emittable_targets()) >= 9


@pytest.mark.parametrize("protocol,role", _emittable_targets())
def test_emitted_interface_passes_verilator(protocol, role, tmp_path):
    module_name = f"{protocol.id.replace('-', '_')}_{role}"
    source = emit_sv_module(
        protocol, role=role, module_name=module_name,
        parameters=_PARAMETERS, widths=_WIDTHS, include_optional=True,
    )
    path = tmp_path / f"{module_name}.sv"
    path.write_text(source)

    completed = subprocess.run(
        ["verilator", "--lint-only", str(path)], capture_output=True, text=True, timeout=120
    )

    assert completed.returncode == 0, (
        f"verilator rejected the interface generated for {protocol.ref} role {role!r}:\n"
        f"{completed.stderr}\n--- generated ---\n{source}"
    )


def test_optional_signals_change_the_port_count_for_a_protocol_that_has_them(tmp_path):
    """OBI's optional set is large, and `include_optional` is the difference between a minimal
    conformant interface and a maximal one — worth proving it actually does something, since a
    flag that silently did nothing would be invisible."""
    obi = ProtocolRegistry().get("obi")

    minimal = emit_sv_module(
        obi, role="manager", module_name="obi_min", parameters=_PARAMETERS,
    )
    maximal = emit_sv_module(
        obi, role="manager", module_name="obi_max", parameters=_PARAMETERS, include_optional=True,
    )

    assert maximal.count("logic") > minimal.count("logic")
    for name, source in (("obi_min", minimal), ("obi_max", maximal)):
        path = tmp_path / f"{name}.sv"
        path.write_text(source)
        completed = subprocess.run(
            ["verilator", "--lint-only", str(path)], capture_output=True, text=True, timeout=120
        )
        assert completed.returncode == 0, completed.stderr


# --- Handshake assertions, run against real simulation (docs/decisions.md D212) ------------------

_TB_PREAMBLE = """module tb;
  logic clk=0, reset_n=0, req=0, gnt=0, rvalid=0, rready=0;
  logic [31:0] addr='0; logic we=0; logic [3:0] be='0;
  obi_handshake_checker chk(.*);
  always #5 clk = ~clk;
  initial begin
"""
_TB_EPILOGUE = """    repeat (2) @(posedge clk);
    $finish;
  end
endmodule
"""

# Stimulus drives on negedge so the assertions' posedge sampling is race-free.
_SCENARIOS = {
    "compliant": (
        """    repeat (2) @(posedge clk);
    @(negedge clk); reset_n = 1;
    @(negedge clk); req = 1;
    @(negedge clk); gnt = 1;
    @(negedge clk); req = 0; gnt = 0;
    @(negedge clk); rvalid = 1; rready = 1;
    @(negedge clk); rvalid = 0; rready = 0;
""",
        None,
    ),
    "retract_req_before_grant": (
        """    repeat (2) @(posedge clk);
    @(negedge clk); reset_n = 1;
    @(negedge clk); req = 1;
    @(negedge clk); req = 0;
""",
        "a_address_no_retract",
    ),
    "req_high_in_reset": (
        """    @(negedge clk); req = 1;
    repeat (2) @(posedge clk);
    @(negedge clk); reset_n = 1; req = 0;
""",
        "a_address_req_low_in_reset",
    ),
    "change_addr_mid_phase": (
        """    repeat (2) @(posedge clk);
    @(negedge clk); reset_n = 1;
    @(negedge clk); req = 1; addr = 32'h1000; we = 1; be = 4'hF;
    @(negedge clk); addr = 32'h2000;
""",
        "a_address_addr_stable",
    ),
    "multi_cycle_wait_with_stable_payload": (
        """    repeat (2) @(posedge clk);
    @(negedge clk); reset_n = 1;
    @(negedge clk); req = 1; addr = 32'h1000; we = 1; be = 4'hF;
    @(negedge clk); ;
    @(negedge clk); gnt = 1;
    @(negedge clk); req = 0; gnt = 0; addr = '0; we = 0; be = '0;
""",
        None,
    ),
    "retract_rvalid_before_ready": (
        """    repeat (2) @(posedge clk);
    @(negedge clk); reset_n = 1;
    @(negedge clk); rvalid = 1;
    @(negedge clk); rvalid = 0;
""",
        "a_response_no_retract",
    ),
}


@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_the_generated_obi_assertions_pass_compliant_traffic_and_catch_each_violation(
    scenario, tmp_path
):
    """The emitted SVA is judged by a real simulator on real waveforms, both directions: a
    compliant driver must run clean (an over-strict assertion is a false alarm generator) and each
    violation must trip *its own* assertion by name (a checker that fails somewhere is not the
    same as one that fails at the rule being broken)."""
    from flux_protocols import load_protocol, specs_dir
    from flux_protocols.emit import emit_sv_assertions

    stimulus, expected_assertion = _SCENARIOS[scenario]
    (tmp_path / "checker.sv").write_text(
        emit_sv_assertions(load_protocol(specs_dir() / "obi-1.6.0.yaml"))
    )
    (tmp_path / "tb.sv").write_text(_TB_PREAMBLE + stimulus + _TB_EPILOGUE)

    build = subprocess.run(
        ["verilator", "--binary", "--assert", "--timing", "-Mdir", "obj", "-o", "sim",
         "tb.sv", "checker.sv", "--top", "tb"],
        cwd=tmp_path, capture_output=True, text=True, timeout=300,
    )
    assert build.returncode == 0, f"verilator rejected the generated checker:\n{build.stderr}"

    run = subprocess.run(
        [str(tmp_path / "obj" / "sim")], capture_output=True, text=True, timeout=60, cwd=tmp_path
    )
    if expected_assertion is None:
        assert run.returncode == 0 and "Assertion failed" not in run.stdout + run.stderr, (
            f"compliant traffic tripped an assertion:\n{run.stdout}{run.stderr}"
        )
    else:
        assert run.returncode != 0, "a protocol violation went unreported"
        assert f"Assertion failed in tb.chk.{expected_assertion}" in run.stdout + run.stderr, (
            f"wrong assertion fired for {scenario}:\n{run.stdout}{run.stderr}"
        )
