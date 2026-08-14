# codegen/systemc_harness — design-agnostic build/trace/verify harness

The verification half of a new agentic RTL/SystemC generation framework (docs/decisions.md D39):
compiles a DUT (device-under-test) SystemC module against a **deterministically generated**
driver, runs it with real VCD tracing, and self-checks it against a spec's test vectors. No LLM
anywhere in this package.

## Why generation and verification are split

`flows/chia_nodes` (D40) will use an LLM to generate the DUT module's *behavior*. This package
generates everything else — port binding, signal wiring, VCD tracing, pass/fail checking —
deterministically, from a declarative `DesignSpec`, never from the LLM. The thing doing the
checking is never the thing being checked, the same independence `flows/chia_nodes/validity.py`
already applies to evaluated candidates (D-series precedent, not a new principle invented here).

This also structurally eliminates a real bug class found during design: an early standalone check
of the intended LLM backend (`chia.models.ollama.OllamaLLM`, `qwen2.5-coder:7b`) asked it to write
a *complete* `sc_main` including port bindings, and it bound a port directly to a plain `int`
(`adder.a(a_val)`) instead of an `sc_signal<int>` — a real compile error. Because this harness's
generated driver owns every `dut.port(signal)` binding call, the LLM is never asked to write one,
so that entire bug class can't occur here.

## `DesignSpec`

A plain, validated dict (`design_spec_from_dict`), loosely following this repo's IR conventions
(`schema_version`/`id`/a typed structural list) without pulling in `flux_ir`'s full
canonicalization/hashing machinery — v0.1 scope:

```python
{
    "module_name": "Adder2",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",   # the LLM's only spec of what to build
    "test_vectors": [
        {"inputs": {"a": 3, "b": 4}, "expected": {"sum": 7}},
    ],
}
```

`dtype` is deliberately small (`"int"`, `"bool"`) — the two C++ builtins every `sc_in`/`sc_out`
template instantiates trivially. `is_clocked` defaults to `False`; `is_clocked=True` isn't
implemented yet (`generate_driver_cpp` raises `InvalidSpecError` — v0.1 only drives combinational
DUTs via `SC_METHOD`, no clock/reset sequencing).

## `compile_and_run`

```python
result = compile_and_run(module_source, spec)
result.compiled        # True unless g++ itself rejected the source (raises CompileError instead)
result.all_passed       # every test vector passed
result.passed_vectors / result.total_vectors
result.vcd_nonempty    # a real VCD file was written and is non-trivial (checked, not assumed)
result.failing_vector_lines   # e.g. ("VECTOR 0 FAIL sum=-1 ",) — real diagnostic values
```

`module_source` is expected to be just the `SC_MODULE(...) { ... };` body — no `sc_main`, no
`#include <systemc.h>` needed (the harness adds both when writing `dut.h`). `CompileError` carries
the real `g++` stderr, for a caller's generate-repair loop to act on.

## Verified, not assumed

A hand-written, correct `Adder2` module: real g++ compile, real run, real non-empty VCD trace
(confirmed via SystemC's own `sc_create_vcd_trace_file` info line), 3/3 test vectors passed. A
deliberately wrong module (`sum = a - b`): compiles fine, runs, correctly reports 1/3 passed with
accurate failing values (`sum=-1`, `sum=-2` for the two wrong vectors). A syntactically broken
module (missing semicolon): correctly raises `CompileError` with real compiler stderr containing
`"expected"`. See `tests/integration/test_systemc_harness_live.py`.
