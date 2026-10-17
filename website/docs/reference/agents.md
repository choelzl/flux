# Agent options

The options a coding agent takes in a document. Which boxes an agent may work is in the
[Document reference](../guide/parameters.md#flow-settings); where its model and login come from
is in [Models and agents](../guide/models.md).

## Options

Beside `by: <agent>` on any box an agent may work, or on a [critic](../guide/parameters.md#critics):

| Option | Default | Purpose |
|---|---|---|
| `timeout_s` | `1800` (or `FLUX_<NAME>_TIMEOUT_S`) | Most seconds for one turn |
| `session` | `turn` | `turn` or `pass`: a fresh session each turn, or one per pass; refused on `generate` |
| `questions` | `decide` | Who answers a question the agent asks: `decide` (nobody), `model`, `operator` |
| `max_questions` | `2` | Question exchanges per draft |
| `wait_s` | `300` | Seconds to wait for the operator's answer |
| `probe` | `{gate: 20, stages: 3}` | Checks per turn the agent may run with `flux probe`; per stage `{bench: 2}`; `false` turns them off |
| `allow` | `[]` | Give back denied raw tools, e.g. `[yosys]`, or `all` |
| `hidden` | `[]` | More paths this agent may not read, beside `knowledge.hidden` ([Hidden from AI](../guide/parameters.md#hidden-from-ai)) |
| `bin` | The agent on PATH (or `FLUX_<NAME>_BIN`) | The program to run |
| `args` | None (or `FLUX_<NAME>_ARGS`) | Extra arguments, added before the prompt |
| `output` | `text` | Your own agent: how its output is read (`text`, `opencode`, `claude`, `codex`) |
| `resume` | The preset's | Your own agent: the command that resumes a session (`{session}`, `{answer}`) |
| `name` | The program's | Your own agent: its name in the record |

## What an agent may run

An agent never compiles, simulates or tests by itself: those programs are denied (verilator,
yosys, openroad, sta, klayout, champsim, gcc, g++, cc, c++, clang, clang++, make, cmake,
ninja, pytest, bash, sh, `flux rtl`, `flux task`, `flux run`) unless `allow` gives them back.
It checks its file through the loop's own gate and stages with `flux probe`; `probe: false` keeps it
from running any of them itself (the loop alone checks and measures), and `budget.tools_at_once`
makes its probes wait for a free slot like the loop's own runs. The agent's
model is set on the machine (`FLUX_<NAME>_MODEL` or the web's Agents settings), not in the
document. Your own agent is `by: {command: [my-agent, "{prompt_file}", "{artifact}"]}`
([agent contract](commands.md#agent-command)).
