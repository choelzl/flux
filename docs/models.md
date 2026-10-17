# Models and coding agents

Setting up Flux's own model and the coding agents is on the site:
[Models and agents](https://choelzl.github.io/flux/guide/models/), with the recipes for Ollama, a server of your own,
OpenRouter and OpenCode, and the [Agent options](https://choelzl.github.io/flux/reference/agents/) a document takes.

This page keeps the project's measurements and how agent turns work inside, for contributors.

## Which model for which problem

Measured in this project, with the hosted `qwen3.6-35b-a3b-apex` (a 35B mixture of experts):

| problem | result |
|---|---|
| Python count-primes loop | passes on the first draft in seconds; later passes make it about 2x faster |
| RTL 8-bit-adder loop | passes on the first draft; 4378 MHz on the synthesis screen, 28 s |
| `mul8` (Booth or Baugh-Wooley, `a * w` not allowed) | one pass each on two dates (26 min, 4 drafts): the best that compiled failed 22 of 49 vectors, then all 49; it needs several passes or a stronger model |
| a combinational FP16 GELU within 1 ULP, from a plain prompt, RTL directly | after hours and about 60 attempts, no draft compiled: SystemVerilog syntax and FP16 decoding were the walls |
| the same GELU, the model writing a Python prototype (`prototype: true`) | the first to pass was a table of the answers; under the formula rules (D616) it stalled at 24,196 of 65,536 inputs wrong: one fixed-point format for every input, then x/2 across the middle range |
| the same GELU, OpenCode writing the prototype, with a method note in `flow.knowledge` | 48,669 wrong, then 529, then 37 over about a day of turns (D618); the remaining misses are 2 ULP in the negative tail |
| OpenCode writing the GELU's RTL directly | 115 attempts over 1.5 days, none passed the gate |
| the NLU's operators (the same kind of function) | solved, with a Python prototype first, then translated to RTL, with the tools and the ladder |

The loop does not make a weak model strong. It keeps what passes, refuses what doesn't, and
turns every failure into the next prompt. For hard numeric RTL:
- use a prototype stage (`budget.prototype: true` with a golden model). The algorithm is proven
  in Python on every input in seconds, and the loop spells the RTL itself (D611);
- give it a method note (`flow.knowledge: {files: [...]}`) with the method and facts, not a design.
  For the GELU, the note covered not putting a float into one fixed-point format, the function
  factored as x times a smooth h(x), the regions where the answer is x, -0 or a short Taylor
  series, and a polynomial per segment;
- hand the prototype to a coding agent; the loop checks each draft with `python -m flux_loop.golden_proto` and
  tells the agent where it fails.

## Agents mid-turn

**Checking mid-turn: `flux probe`.** A generate or prototype agent may run the loop's own tools
on its file inside its turn (D678). `flux probe gate FILE` runs the document's gate, the
correctness checks (for a prototype, its check). `flux probe measure FILE --stage S [--stage T
...]` runs those measurements, each on its own and side by side, with no gate first unless
`--gate`. It says whether each meets its limits (the stage's cutoffs and the objectives' limits
at it, D679). Each probe uses the loop's exact commands and flags, prints what the loop would
see, and is on the record with the agent's turn. A budget per turn bounds them: `probe: {gate: 20, stages: 3, place: 1}`
(the defaults are 20 and 3; `probe: false` turns it off). `allow: [verilator, yosys]` gives
denied commands back to one agent, and `allow: all` lifts the deny list for it. With
`budget.prototype: true` the agent writes the Python prototype instead. The loop checks it with
`python -m flux_loop.golden_proto` and spells the RTL (D618). An agent that
ends a turn without writing its file is nudged to write it, twice at most. An agent whose
session outgrew the model's context window continues in a fresh session from the brief and its
last file.

