"""`flux_generate_rtl_module` — the Verilog sibling of `generate_systemc.py`'s
`flux_generate_systemc_module` (docs/decisions.md D44): an LLM proposes a synthesizable Verilog
DUT module's behavior; `codegen/rtl_harness` (deterministic, no LLM) compiles it against a
generated SystemVerilog testbench through real Verilator, traces it, and checks it against
`DesignSpec.test_vectors`; a real compile or verification failure is fed back to the LLM for a
bounded number of repair attempts — the same generate-verify-repair loop as D40, same backend
(`chia.models.ollama.OllamaLLM`, the only one confirmed usable in this sandbox, see D40).

**No syntax primer needed here — verified empirically before assuming one would be, not copied
from D40 by default.** D40 found `qwen2.5-coder:7b` writes *Verilog* syntax when asked for
*SystemC* (a real, reproducible confusion). Asking the same model for actual Verilog directly
first (no primer) was tried and succeeded on the first real attempt for both a two-input adder and
a structurally different four-port multiplexer — consistent with the D40 finding, not
contradicting it: this is presumably closer to the model's own natural training distribution for
"write me combinational hardware description," so nothing extra was needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_llm import default_local_model, strip_markdown_fence
from flux_codegen_rtl_harness import (
    CompileError,
    DesignSpec,
    HarnessRunResult,
    compile_and_run,
    design_spec_from_dict,
)
from flux_codegen_rtl_harness.driver_gen import CLOCK_PORT, DONE_PORT, RESET_PORT, START_PORT

_DEFAULT_MODEL = default_local_model()
_MAX_REPAIR_ATTEMPTS = 3
_CLOCKED_PRIMER = f"""This is a clocked (sequential) design. Your module must have exactly two
extra ports beyond the ones listed below, always first in the port list:
  input logic {CLOCK_PORT},
  input logic {RESET_PORT},   // active-LOW asynchronous reset
Use `always_ff @(posedge {CLOCK_PORT} or negedge {RESET_PORT})` for all sequential logic, and reset
every register to 0 when `{RESET_PORT}` is low (`if (!{RESET_PORT}) ... else ...`). Never use
`always @(*)` or `assign` for state that must persist across clock cycles."""


_LATENCY_PRIMER = f"""This is a clocked design whose LATENCY IS MEASURED. Beyond the ports listed
below, your module must have exactly these four extra ports, always first in the port list:
  input  logic {CLOCK_PORT},
  input  logic {RESET_PORT},   // active-LOW asynchronous reset
  input  logic {START_PORT},
  output logic {DONE_PORT},

Implement this handshake exactly — it is how the harness knows when your result is ready:
  - `{START_PORT}` is pulsed high for exactly one clock cycle. On that cycle, begin the
    computation (latch whatever you need from the inputs).
  - Raise `{DONE_PORT}` high for one cycle as soon as the outputs hold the final result, then
    drive it low again.
  - `{DONE_PORT}` must be LOW at every other time, including during reset and while computing.
    Never tie it high, and never leave it high after finishing.
  - The harness counts clock cycles from `{START_PORT}` until it sees `{DONE_PORT}`. A module
    that never raises `{DONE_PORT}` is a failure, not a slow design.

Use `always_ff @(posedge {CLOCK_PORT} or negedge {RESET_PORT})` for all sequential logic, and
reset every register (including `{DONE_PORT}`) to 0 when `{RESET_PORT}` is low. Never use
`always @(*)` or `assign` for state that must persist across cycles."""

# Verilator 5.051 made `WIDTHEXPAND` fatal under `-Wall`, and the repair loop cannot climb out of
# it on its own: fed the exact error three times, the model changed the constant's *radix* rather
# than its width (`4'h0` -> `4'h0` -> `4'b0`), and generation that used to succeed now reliably
# fails (docs/decisions.md D153). Same shape as D40's SystemC syntax primer — a specific, repeated
# mistake this model makes, corrected once up front rather than through a repair round it does not
# converge on.
# Extended after observing three more real failures of the same class in the demo suites
# (docs/decisions.md D199): the model wrote `opcode == 2'sd2`, `sel[1'b0]`, and — when told the
# comparison was too narrow — changed the *port* to `[1:0]`, which then failed at the harness's
# own binding. All three are the "sized literal of the wrong width" mistake wearing different
# syntax, and none was covered by naming `4'h0`/`4'b11` alone.
_WIDTH_PRIMER = """Every integer port's EXACT width is declared in the port list below — ports may
have different widths (e.g. 8-bit operands, a wider accumulator). Any constant you compare against
a port must match that port's declared width, or the compiler rejects it: a plain decimal like `0`
or `3` is always safe — never a sized literal of a different width such as `4'h0`, `2'sd2` or
`4'b11`.

The same rule covers bit selection: index a port with a plain decimal, `sel[0]` and `sel[1]`,
never `sel[1'b0]` — a 1-bit index into a wider value is rejected for exactly the same reason.

Do not change any port's declared width to make a constant or an intermediate fit. The port list
above is fixed, and a different width fails where it binds to the harness instead.

Declare every intermediate WIDER than its operands, or it silently wraps before you can test it:
a sum or difference of two N-bit values needs N+1 bits (`logic signed [N:0] s;` `assign s = a + b;`),
and comparisons for clamping/saturation must read that wider intermediate — comparing an N-bit
sum against a bound can never see the overflow it wrapped away."""


def _module_prompt(spec: DesignSpec, guidance: str | None = None) -> str:
    port_lines = "\n".join(
        f"  {'input' if p.dir == 'in' else 'output'} "
        f"logic{f' signed [{p.width - 1}:0]' if p.dtype == 'int' else ''} {p.name},"
        for p in spec.ports
    )
    if spec.measures_latency:
        clocked_block = f"{_LATENCY_PRIMER}\n\n"   # supersedes the plain clocked primer (D116)
    elif spec.is_clocked:
        clocked_block = f"{_CLOCKED_PRIMER}\n\n"
    else:
        clocked_block = ""
    has_int_ports = any(p.dtype == "int" for p in spec.ports)
    width_block = f"{_WIDTH_PRIMER}\n\n" if has_int_ports else ""
    # Observed with authored specs (docs/decisions.md D235): fed the exact "Can't find
    # definition of task/function: 'abs'" compile error three times, the model kept calling
    # abs() — the D153 class again: a specific repeated mistake corrected up front, not through
    # repairs it does not converge on.
    width_block += (
        "SystemVerilog has no abs(), min(), max() or clamp() functions. Express them with "
        "conditional expressions: (x < 0) ? -x : x for absolute value, (a < b) ? a : b for "
        "min, and nested conditionals for clamping.\n\n"
    )
    # Caller-curated design guidance (docs/decisions.md D244): e.g. chunks retrieved from the
    # design-guidance corpus via flux_knowledge_lookup. Advisory context only — the verifier
    # stays the judge, so bad guidance costs repair rounds, never correctness.
    guidance_block = (
        f"Relevant design guidance (advisory, from the caller's knowledge base):\n{guidance}\n\n"
        if guidance else ""
    )
    return (
        f"{clocked_block}"
        f"{width_block}"
        f"{guidance_block}"
        f"Write a synthesizable SystemVerilog module named {spec.module_name!r} implementing this "
        f"behavior: {spec.behavior}\n\n"
        f"It must have exactly this port list (do not add, remove, or rename any port"
        f"{' beyond the extra ports described above' if spec.is_clocked else ''}):\n"
        f"{port_lines}\n\n"
        "Output ONLY the module ... endmodule definition — nothing else. Do not write a testbench, "
        "do not instantiate the module or drive its ports (a separate harness does that), do not "
        "wrap the output in markdown code fences, do not add any explanation before or after the "
        "code."
    )


def _repair_prompt(spec: DesignSpec, prior_source: str, failure_detail: str) -> str:
    return (
        f"Your previous Verilog module for {spec.module_name!r} failed verification.\n\n"
        f"--- your previous code ---\n{prior_source}\n\n"
        f"--- real failure detail ---\n{failure_detail}\n\n"
        "Fix the module and output ONLY the corrected module ... endmodule definition — same "
        "rules as before: no testbench, no port binding, no markdown fences, no explanation."
    )


@dataclass
class GenerationResult:
    spec_id: str
    success: bool
    attempts: int
    final_source: str
    harness_result: HarnessRunResult | None
    transcript: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "success": self.success,
            "attempts": self.attempts,
            "final_source": self.final_source,
            "harness_result": self.harness_result.to_dict() if self.harness_result else None,
            "transcript": list(self.transcript),
        }


@ChiaFunction()
def flux_generate_rtl_module(
    spec: dict[str, Any], *, model: str = _DEFAULT_MODEL,
    max_repair_attempts: int = _MAX_REPAIR_ATTEMPTS,
    llm: Any | None = None,
    guidance: str | None = None,
) -> GenerationResult:
    """Generate a Verilog DUT module for `spec` (a plain dict, see `DesignSpec`), verify it
    through `flux_codegen_rtl_harness.compile_and_run`, and retry with the real failure fed back
    to the LLM up to `max_repair_attempts` times. Same contract/shape as
    `generate_systemc.flux_generate_systemc_module` — see that function's docstring for the
    fields' exact meaning.
    """
    design_spec = design_spec_from_dict(spec)
    if llm is None:
        # The default client; `llm` is the injection point (anything with .prompt(str).result),
        # the same injectability pattern the campaign runner has for make_evaluator — what lets
        # the holdout-regeneration control flow (docs/decisions.md D234) be tested with a
        # scripted proposer against the REAL harness, no Ollama needed.
        from chia.models.ollama import OllamaLLM

        llm = OllamaLLM(model=model, system_message="You write minimal, correct, synthesizable Verilog.")

    transcript: list[str] = []
    prompt = _module_prompt(design_spec, guidance=guidance)
    source = ""
    harness_result: HarnessRunResult | None = None

    for attempt in range(1, max_repair_attempts + 1):
        transcript.append(f"--- attempt {attempt} prompt ---\n{prompt}")
        response = llm.prompt(prompt)
        source = strip_markdown_fence(response.result)
        transcript.append(f"--- attempt {attempt} response ---\n{source}")

        try:
            harness_result = compile_and_run(source, design_spec)
        except CompileError as exc:
            transcript.append(f"--- attempt {attempt} compile error ---\n{exc.stderr}")
            prompt = _repair_prompt(design_spec, source, f"verilator compile error:\n{exc.stderr[-2000:]}")
            harness_result = None
            continue

        if harness_result.all_passed:
            return GenerationResult(
                spec_id=design_spec.id, success=True, attempts=attempt,
                final_source=source, harness_result=harness_result, transcript=transcript,
            )

        failure_detail = (
            f"{harness_result.passed_vectors}/{harness_result.total_vectors} test vectors passed. "
            f"Failing vectors: {'; '.join(harness_result.failing_vector_lines)}"
        )
        transcript.append(f"--- attempt {attempt} verification failure ---\n{failure_detail}")
        prompt = _repair_prompt(design_spec, source, failure_detail)

    return GenerationResult(
        spec_id=design_spec.id, success=False, attempts=max_repair_attempts,
        final_source=source, harness_result=harness_result, transcript=transcript,
    )
