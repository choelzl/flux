# flux-codegen-harness-spec

What a generated-design harness is **told** and what it **reports**, for any target language
(docs/decisions.md D453):

| piece | what it is |
|---|---|
| `DesignSpec` / `Port` / `TestVector` | the declarative input a driver is generated from: ports with dirs, dtypes, widths and array dims, plus the golden vectors (D39, D115, D120/D121, D202) |
| `design_spec_from_dict` | the one validating parser for it -- a bad spec is refused before any compiler runs |
| `CompositionSpec` / `Instance` | the netlist a composite is wired from: instances, one net per (instance, port), top-level ports (D48/D55) |
| `composition_spec_from_dict` | the one validating parser for THAT, with the language's reserved-word check injected |
| `HarnessRunResult` | what a harness returns: compiled, ran, vectors passed, the failing lines, the VCD, and measured cycles where a spec asks for them (D115) |
| `reserved_check` | the reserved-identifier check, over whichever word set a language supplies (D51/D55) |

**No language is in here.** `flux-codegen-rtl-harness` emits SystemVerilog and runs Verilator;
`flux-codegen-systemc-harness` emits C++ and runs g++; each owns its driver, its composite
emitter and its own reserved words, and imports this package for the rest.

**Why it exists.** These pieces were written twice, and the two copies drifted: the SystemC
composition parser had gained a net-width conflict check and top-port width parsing (D203) that
the RTL one lacked, so an RTL composite silently emitted a 32-bit port where its doc declared 16;
the RTL parser had gained a top-port identifier check the SystemC one lacked. Each side was
missing the other's fix, which is exactly the failure mode D429 said it would take to justify
this package.
