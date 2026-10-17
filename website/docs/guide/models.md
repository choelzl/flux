# Models and agents

Flux talks to two kinds of AI, configured apart:

- **Flux's own model**: any OpenAI-compatible chat endpoint, for the loop's own calls (proposing
  points, writing designs with `generate: model`, reviewing, digesting papers).
- **Coding agents**: Claude Code, Codex, OpenCode, or one a server adds. Each runs on its own
  endpoint, model and login, never on Flux's model.

A loop whose designs come from a script, with no `model` or agent in its `flow`, needs neither.
`flux selftest` says which model and endpoint a run would use, and whether each agent is ready.

## Flux's model

By default Flux asks a local [Ollama](https://ollama.com/) (`OLLAMA_BASE_URL`, the model in
`FLUX_LLM_MODEL`). A remote OpenAI-compatible endpoint (LocalAI, vLLM, llama.cpp's server, a
hosted service) uses:

```bash
export FLUX_REMOTE_BASE_URL=https://your-model-server.example/v1
export FLUX_REMOTE_MODEL=your-model-name
export FLUX_REMOTE_API_KEY=your-key          # only if the endpoint checks keys
flux selftest
```

| Variable | What it does |
|---|---|
| `FLUX_LLM_MODEL` | The local model's name |
| `OLLAMA_BASE_URL` | The local server's root |
| `FLUX_REMOTE_BASE_URL` | An endpoint of your own; setting it switches to it |
| `FLUX_REMOTE_MODEL` | The model's name on that endpoint |
| `FLUX_REMOTE_API_KEY`, `FLUX_REMOTE_API_KEY_FILE` | Its key, or a file whose first line is the key |
| `FLUX_LLM_REMOTE` | `1` forces the remote endpoint, `0` the local one |
| `FLUX_LLM_TIMEOUT_S` | Seconds one request may take; raise it for slow servers and long turns |
| `FLUX_LLM_STREAM` | `0` turns streaming off, for proxies that break it |

`--model NAME` on `flux task run` and `flux ask` overrides the model for one run. If a service
refuses the structured output Flux asks for, try `--no-structured`.

## Settings that last

Every `flux` command reads `~/.config/flux/flux.env` (or the file `FLUX_CONFIG` names):
`NAME=value` lines, used only where the shell did not set the variable. Keep the key in its own
file (mode 600) rather than in flux.env:

```text
FLUX_REMOTE_BASE_URL=https://your-model-server.example/v1
FLUX_REMOTE_MODEL=your-model-name
FLUX_REMOTE_API_KEY_FILE=~/.config/flux/server.key
```

Keep keys in the environment or the web app's settings, never in a loop's document.

## Recipes

**Ollama on this machine:**

```bash
ollama pull qwen3:4b                  # any tag; then
export FLUX_LLM_MODEL=qwen3:4b
flux task run flux/applications/primes --passes 1     # from the repository root
```

A large model on a CPU takes minutes per turn; use a GPU, or a smaller model for small
problems. For an Ollama on another machine, set `OLLAMA_BASE_URL=http://HOST:11434`.

**A server of your own** (LocalAI, vLLM, llama.cpp's server): set `FLUX_REMOTE_BASE_URL`,
`FLUX_REMOTE_MODEL` and, if it checks keys, `FLUX_REMOTE_API_KEY`, as above. Raise
`FLUX_LLM_TIMEOUT_S` (for example to `1800`) for long turns with tools.

**OpenRouter:** `FLUX_LLM_REMOTE=1`, `OPENROUTER_API_KEY`, and `FLUX_REMOTE_MODEL` set to an
OpenRouter model id. With `FLUX_LLM_REMOTE=1` and no `FLUX_REMOTE_BASE_URL`, Flux asks OpenRouter.

**OpenCode on a server of your own:** in OpenCode's configuration
(`~/.config/opencode/opencode.json`), read the key as `"apiKey": "{env:FLUX_AGENT_API_KEY}"`
and set `FLUX_OPENCODE_API_KEY` (in flux.env, or OpenCode's card in the web app). Tell OpenCode
the model's real limits, and keep a thinking model's reasoning short, or it can end turn after
turn on an empty reply:

```json
"models": {"MODEL": {"limit": {"context": 58112, "output": 16384},
                     "options": {"reasoningEffort": "low"}}}
```

and `"timeout": 1800000` in the provider's `options`. OpenCode's own prompt and tools take about
10,000 tokens of the window.

## Coding agents

An agent must be installed and logged in where Flux runs (inside the sandbox, with its own
home: `flux login --home ... -- claude /login`, or the web app's **Account › Agents**). Check
one with:

```bash
flux agent test claude          # its program and login
flux agent test claude --live   # also one short question
```

| Variable | What it does |
|---|---|
| `FLUX_<NAME>_BIN` | The agent's program, e.g. `FLUX_CLAUDE_BIN`, `FLUX_CODEX_BIN`, `FLUX_OPENCODE_BIN` |
| `FLUX_<NAME>_ARGS` | Extra arguments, added before the prompt |
| `FLUX_<NAME>_BASE_URL`, `FLUX_<NAME>_MODEL`, `FLUX_<NAME>_API_KEY` | The agent's own endpoint, model and key |
| `FLUX_<NAME>_OAUTH_TOKEN` | A login token, for agents that take one |
| `FLUX_<NAME>_TIMEOUT_S` | Seconds per turn (default 1800); a document's `timeout_s` wins |

None of `FLUX_REMOTE_*`, `FLUX_LLM_*` or `OPENROUTER_API_KEY` reaches an agent's turn. Without
settings of its own, an agent runs on its own configuration file and login. In the web app,
each agent's card says "not configured" when it has neither.

An agent writes; it does not compile, simulate or test. The loop runs the checks and
measurements, and the agent checks its file mid-turn with `flux probe`, within a budget. See
[Agent options](../reference/agents.md) for `probe`, `allow`, `timeout_s` and the rest.

## Which model for which problem

The loop does not make a weak model strong: it keeps what passes, refuses what does not, and
turns each failure into the next prompt. In this project's runs, a mid-size model wrote a
correct Python loop or a small adder on its first draft, but hard numeric hardware (a
floating-point function within 1 ULP) needed more than RTL from a plain prompt. What helped:

- a prototype stage (`budget.prototype: true` with a golden model): the algorithm proven in
  Python on every input, then spelled as RTL by the loop;
- a method note in `flow.knowledge`: the method and the facts, not a design;
- a coding agent writing the prototype, told by the loop where it fails.

## When something goes wrong

- **No answer:** a run that needs a model asks its endpoint first and stops before the first
  pass when it does not answer, refuses the key, or lacks the model. `flux task check` shows
  the same line.
- **Start with the log:** `flux log RECORD` shows every model and agent turn: the prompt, the
  reply, the tool calls, and the error.
- **Timeouts:** raise `FLUX_LLM_TIMEOUT_S` for the model, `timeout_s` for an agent.
- **Context:** an agent that outgrows its endpoint's context window continues in a fresh
  session; raise the window if you can.
