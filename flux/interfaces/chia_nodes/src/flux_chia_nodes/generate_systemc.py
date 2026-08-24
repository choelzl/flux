"""`flux_generate_systemc_module` — the generation half of the agentic RTL/SystemC framework
started by `codegen/systemc_harness` (docs/decisions.md D39/D40): an LLM proposes a SystemC DUT
module's *behavior*; `codegen/systemc_harness` (deterministic, no LLM) compiles it against a
generated driver, traces it, and checks it against `DesignSpec.test_vectors`; on a real failure
(compile error or failing vectors) the real error is fed back to the LLM for a bounded number of
repair attempts — a genuine generate-verify-repair loop, not single-shot trust.

**Backend, chosen empirically, not by default.** CHIA's `chia.models.claude.ClaudeCodeLLM` was
checked first and found unusable in this sandbox: `backend="cli"` needs a `claude` binary, not on
`PATH` anywhere (checked in the outer session shell too, not just this repo's nix shells);
`backend="api"` needs `anthropic`, not installed anywhere in this environment's nix store, and no
`ANTHROPIC_API_KEY` is set. A real, already-running local Ollama server was found instead
(`localhost:11434`, confirmed reachable, three real pulled models) — `chia.models.ollama.OllamaLLM`
is the same backend `flows/chia_nodes/agentic.py`'s five DSE strategies already use, so this node
follows that precedent rather than introducing a second LLM-wiring convention. Free, local
inference — no per-call cost, unlike a real Claude/OpenAI API call would be.

**Markdown-fence stripping uses `flux_llm.strip_markdown_fence`** (docs/decisions.md D200). This
node's backend (`qwen2.5-coder:7b`) wraps generated C++ in ` ```cpp ` fences despite an explicit
"no markdown" instruction — a real, checked habit. That once justified a local copy, back when the
shared helper was JSON-only; four copies later, the shared one handles any tag and surrounding
prose, and the copies were what drifted.

**`_SYNTAX_PRIMER` exists because of a real, reproducible failure mode, not speculatively.** The
first version of `_module_prompt` had no syntax example — `qwen2.5-coder:7b` failed to produce a
compiling module for even a trivial two-input adder in 3/3 real attempts, every time writing
Verilog-style `always @(*)` blocks (a real syntax error in C++/SystemC) instead of
`SC_METHOD`/`sensitive`, even with the real compiler error fed back each attempt — the model
confuses SystemC with Verilog specifically, not a generic capability gap. A short syntax example
(a two-input AND gate, deliberately unrelated to any real spec's own behavior, so success can't be
explained by the model just echoing the primer) fixed this completely: confirmed on two
structurally different specs (a 2-input int adder, a 4-port bool/int 2-to-1 mux), both succeeding
on the *first* attempt after the primer was added, both having failed repeatedly without it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_llm import default_local_model, strip_markdown_fence
from flux_codegen_systemc_harness import (
    CompileError,
    DesignSpec,
    HarnessRunResult,
    compile_and_run,
    design_spec_from_dict,
)

_DEFAULT_MODEL = default_local_model()
_MAX_REPAIR_ATTEMPTS = 3
_SYNTAX_PRIMER = """SystemC is C++, not Verilog — never use Verilog syntax like `always @(*)`.
A combinational module looks exactly like this (a two-input AND gate, as a syntax example only —
do not reuse this behavior):

SC_MODULE(AndGate) {
    sc_in<bool> x;
    sc_in<bool> y;
    sc_out<bool> z;

    void compute() {
        z.write(x.read() && y.read());
    }

    SC_CTOR(AndGate) {
        SC_METHOD(compute);
        sensitive << x << y;
    }
};

Always read a port with `.read()` and write a port with `.write(value)` — never assign to or from
a port directly. Always declare a plain C++ method and register it with `SC_METHOD(name)` plus
`sensitive << port1 << port2 << ...` inside `SC_CTOR` — never use `always @(...)`."""


_CLOCKED_PRIMER = """This is a clocked (sequential) design. Your module must have exactly two
extra ports beyond the ones listed below, always first:
    sc_in_clk clk;
    sc_in<bool> rst_n;   // active-LOW asynchronous reset
Use `SC_METHOD` sensitive to `clk.pos()` (and to `rst_n` too if you want the reset to take effect
immediately rather than only on the next clock edge) for all sequential logic, reading and writing
registered state with `.read()`/`.write()` only inside that method — never use `always_ff` or any
other non-SystemC syntax. Reset every register to 0 when `rst_n.read()` is false.

The sensitivity list must contain ONLY `clk.pos()` (and optionally `rst_n`) — NEVER add any other
port (a data input like `en` or `d`) to `sensitive << ...`. Adding a data input there makes the
method re-run the instant that input changes, not just on the clock edge, silently corrupting
registered state (e.g. a counter incrementing twice per real clock cycle instead of once)."""


def _module_prompt(spec: DesignSpec, guidance: str | None = None) -> str:
    port_lines = "\n".join(
        f"  {'sc_in' if p.dir == 'in' else 'sc_out'}<{p.cpp_type}> {p.name};" for p in spec.ports
    )
    clocked_block = f"{_CLOCKED_PRIMER}\n\n" if spec.is_clocked else ""
    # Same advisory guidance block as the RTL sibling (docs/decisions.md D244).
    guidance_block = (
        f"Relevant design guidance (advisory, from the caller's knowledge base):\n{guidance}\n\n"
        if guidance else ""
    )
    return (
        f"{_SYNTAX_PRIMER}\n\n"
        f"{clocked_block}"
        f"{guidance_block}"
        f"Write a SystemC module named {spec.module_name!r} implementing this behavior: "
        f"{spec.behavior}\n\n"
        + (
            "Remember: declare `sc_in_clk clk;` and `sc_in<bool> rst_n;` as the FIRST two data "
            "members, before any port below — do not skip this even though neither is listed "
            "below.\n\n"
            if spec.is_clocked else ""
        )
        + f"It must have exactly this port list (do not add, remove, or rename any port"
        f"{' beyond the clock/reset ports described above' if spec.is_clocked else ''}):\n"
        f"{port_lines}\n\n"
        "Output ONLY the SC_MODULE(...) { ... }; definition — nothing else. Do not write sc_main, "
        "do not write #include lines, do not bind any port to a signal (a separate harness does "
        "that), do not wrap the output in markdown code fences, do not add any explanation before "
        "or after the code."
    )


def _repair_prompt(spec: DesignSpec, prior_source: str, failure_detail: str) -> str:
    return (
        f"Your previous SystemC module for {spec.module_name!r} failed verification.\n\n"
        f"--- your previous code ---\n{prior_source}\n\n"
        f"--- real failure detail ---\n{failure_detail}\n\n"
        "Fix the module and output ONLY the corrected SC_MODULE(...) { ... }; definition — same "
        "rules as before: no sc_main, no #include, no port-signal binding, no markdown fences, no "
        "explanation."
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
def flux_generate_systemc_module(
    spec: dict[str, Any], *, model: str = _DEFAULT_MODEL, max_repair_attempts: int = _MAX_REPAIR_ATTEMPTS,
    guidance: str | None = None
) -> GenerationResult:
    """Generate a SystemC DUT module for `spec` (a plain dict, see `DesignSpec`), verify it
    through `codegen_systemc_harness.compile_and_run`, and retry with the real failure fed back
    to the LLM up to `max_repair_attempts` times. Returns a `GenerationResult` whose
    `harness_result` is the *last* attempt's harness run — `None` iff the last attempt failed to
    compile, even when an earlier attempt compiled and partially passed (`harness_result` always
    corresponds to `final_source`, never to a discarded earlier attempt — the earlier docstring's
    "None only if every attempt failed to compile" claim was wrong, review finding);
    `success=True` iff the final attempt compiled, ran, and passed every test vector.
    """
    from chia.models.ollama import OllamaLLM  # imported lazily, same pattern as agentic.py

    design_spec = design_spec_from_dict(spec)
    llm = OllamaLLM(model=model, system_message="You write minimal, correct SystemC C++.")

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
            prompt = _repair_prompt(design_spec, source, f"g++ compile error:\n{exc.stderr[-2000:]}")
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
