# Models and coding agents

Flux asks a model for designs through any OpenAI-compatible chat server. It can also hand the
writing to a coding agent (OpenCode, Claude Code, Codex). Every run prints which model it uses
on its first line. Problems with no model in their flow (a `sweep` over a script) never call
one.

## Settings

| variable | what it does | default |
|---|---|---|
| `FLUX_LLM_MODEL` | the local model's name | `qwen3.8:latest` |
| `OLLAMA_BASE_URL` | the local server's root | `http://localhost:11434` |
| `FLUX_REMOTE_BASE_URL` | a server of your own; setting it switches to that server | (OpenRouter when remote is forced) |
| `FLUX_REMOTE_MODEL` | the model's name on that server | `deepseek/deepseek-chat-v3-0324:free` |
| `FLUX_REMOTE_API_KEY` | the server's key, only if it checks keys | none |
| `OPENROUTER_API_KEY` | the key for the default hosted server | none |
| `FLUX_LLM_REMOTE` | `1` forces the remote server, `0` forces the local one | set by the URL |
| `FLUX_LLM_TIMEOUT_S` | seconds one request may take (raise it for slow servers and long turns) | the client's |
| `FLUX_LLM_STREAM` | `0` turns streaming off (some proxies break it) | on |

`--model NAME` on `flux task run` and `flux ask` overrides the model name for one run. Keep keys
in the environment, never in a problem document or a repository:
`FLUX_REMOTE_API_KEY="$(cat ~/.config/flux/key)" flux task run ...`.

## Recipes

**Ollama on this machine.** Verified.

```bash
ollama pull qwen3.8:latest            # or any tag; then FLUX_LLM_MODEL=<tag>
flux task run applications/primes --passes 1
```

A 27B model on a CPU takes minutes per turn. Use a GPU, or a smaller model for the small problems.

**A server of your own** (LocalAI, llama.cpp `server`, vLLM). Verified with LocalAI serving
`qwen3.6-35b-a3b-apex`.

```bash
export FLUX_REMOTE_BASE_URL=https://my-server.example/v1 FLUX_REMOTE_MODEL=<name on the server>
export FLUX_REMOTE_API_KEY=...        # only if the server checks keys
export FLUX_LLM_TIMEOUT_S=1800        # long turns with tools
```

**Once per machine.** Every `flux` command reads `~/.config/flux/flux.env` (or `FLUX_CONFIG`):
`FLUX_*` and `OLLAMA_*` lines, `NAME=value`, set only where the shell did not set them. Keep the
key out of it: `FLUX_REMOTE_API_KEY_FILE=~/.config/flux/server.key` names a file (mode 600) whose
first line is the key.

```
FLUX_LLM_REMOTE=1
FLUX_REMOTE_BASE_URL=https://my-server.example
FLUX_REMOTE_MODEL=<name on the server>
FLUX_REMOTE_API_KEY_FILE=~/.config/flux/server.key
```

**OpenRouter.** This is the default hosted server when remote is forced without a URL. Not
verified in this project's recent runs.

```bash
export FLUX_LLM_REMOTE=1 OPENROUTER_API_KEY=... FLUX_REMOTE_MODEL=<an OpenRouter model id>
```

**Other OpenAI-compatible services** (OpenAI's API, Anthropic's OpenAI-compatible endpoint,
cloud inference services): set `FLUX_REMOTE_BASE_URL`, `FLUX_REMOTE_MODEL` and
`FLUX_REMOTE_API_KEY` the same way. Not verified. Flux asks for structured JSON output and offers
tools, and services differ in how much of that they accept. If a turn fails on the request
itself, try `--no-structured`.

## Which model for which problem

Measured in this project, with the hosted `qwen3.6-35b-a3b-apex` (a 35B mixture of experts):

| problem | result |
|---|---|
| `flux example python` (count primes) | passes on the first draft in seconds; later passes make it about 2x faster |
| `flux example rtl` (8-bit adder) | passes on the first draft; 4378 MHz on the synthesis screen, 28 s |
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
- hand the prototype to a coding agent; the loop checks each draft with `flux rtl proto` and
  tells the agent where it fails.

## Coding agents

A document can hand generation to a coding agent (`flow: {generate: opencode}`), and
`flux ask --author opencode|claude|codex` hands it the writing of the problem itself. The agent
uses its own model and configuration. The loop gives it a work directory, a brief and a time
limit, and records every agent turn (see `flux log`). The agent writes; it does not compile, simulate,
synthesize or test. The loop runs the gate and the stages, and a failure goes back to the agent's
session with the exact output (D673). The agent keeps a shell for reading, searching and
computing (`python3`, `pdftotext`), and simulating its draft with Icarus (`iverilog`, `vvp`, D685). The presets deny the design tools (verilator,
yosys, openroad, sta, the C compilers, make, `flux rtl`, `flux task`, ...) and `bash`/`sh` (D674).
Claude Code gets them as `--disallowedTools "Bash(yosys:*)" ...`. OpenCode gets them as
`permission.bash` rules merged into `OPENCODE_CONFIG_CONTENT`; an `--agent` of your own with
its own `bash` rules may override them. Codex has only the brief's word. A deny list is best
effort: `python3` can still start a tool.

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
`flux rtl proto` and spells the RTL (D618). An agent that
ends a turn without writing its file is nudged to write it, twice at most. An agent whose
session outgrew the model's context window continues in a fresh session from the brief and its
last file.

**Another executable, extra arguments.** A preset's executable can be renamed, and arguments
added to it, per machine in `~/.config/flux/flux.env` or per document:

```
FLUX_OPENCODE_BIN=~/.local/bin/opencode-dev     # also FLUX_CLAUDE_BIN, FLUX_CODEX_BIN
FLUX_OPENCODE_ARGS=--agent flux                 # an OpenCode agent defined in its config
```

`flow: {generate: {by: opencode, bin: oc, args: [--agent, flux]}}}` does the same in a
document (it wins). The executable must be a program on PATH or a path; a shell alias is not one.

The presets send the brief on the agent's stdin, and a resumed session's message too, so a brief
of any length reaches the agent. A `command` of your own gets it on stdin unless it names
`{prompt}`, `{prompt_file}` or `{answer}`.

OpenCode pointed at a LocalAI server reads its key from the environment
(`"apiKey": "{env:FLUX_REMOTE_API_KEY}"` in `~/.config/opencode/opencode.json`). With a thinking
model served by LocalAI, tell OpenCode the model's limits and keep its reasoning short. Without
that, it ended turn after turn on an empty reply, all of it reasoning, and never wrote the file:

```json
"models": {"<model>": {"limit": {"context": 58112, "output": 16384},
                       "options": {"reasoningEffort": "low"}}}
```

and `"timeout": 1800000` in the provider's `options`. Set `context` to the server's real window.
OpenCode's own system prompt and tools take about 10,000 tokens of it.

## When something goes wrong

- A run that needs a model asks its server first (`/v1/models`) and stops before the first pass
  when no server answers, the key is refused, or the model is not there. On Ollama the fix is
  `ollama pull <tag>`, or `OLLAMA_BASE_URL=http://<host>:11434` for an Ollama on another
  machine. `flux task check` shows the same line, and `flux selftest` runs a problem end to
  end.

- `flux log <record>` shows every model and agent turn: the prompt, the reply, the tool calls,
  and the error (D599). Start there.
- If a turn times out, raise `FLUX_LLM_TIMEOUT_S`.
- "Failed to parse tool call arguments as JSON" (a 500 from the server) means the server could
  not read the model's own tool call. The turn is retried without tools. If that fails too, the
  turn ends and the loop goes on (D596).
- If a reasoning model thinks until its budget runs out, the turn is asked again with thinking
  off. `--think` asks for reasoning on every turn.
- An agent run that says "exceeds the available context size" outgrew the server's window.
  Raise the server's context if you can. The loop starts a fresh session, but long tool output
  fills a window fast.
