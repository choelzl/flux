# Write a loop with an agent

Describe what you want, give it your files, and let an author write the loop: the document,
its checks and measurements, and a golden model where one fits. The author is Flux's model or a
coding agent. You review what it wrote, then run it.

## From the command line

```bash
flux ask "a signed 8x8 multiplier in SystemVerilog, the smallest that makes 1 GHz placed on ASAP7" \
  --file spec.pdf --author claude --no-run
```

The author writes the loop into `./out/ask_<slug>/` (`--dir DIR` to choose; the folder's name is
the loop's id). It runs `flux task check` on its own draft and repairs what the check refuses,
up to `--checks` times (default 3). An agent author also writes `decisions.json`: each decision
it took and a one-line reason.

Without `--no-run`, the loop then runs: after each pass the author reads the report and may
revise the document. `--passes N` caps the run; without it the run goes on until you stop it.
`flux ask --tui` (or no prompt) opens a setup screen for the prompt, files, author and passes.

| Option | Effect |
|---|---|
| `--file PATH` | An input: a spec, code, a reference, a PDF, tests (repeatable; a folder is copied whole) |
| `--author WHO` | `model` (default), `claude`, `codex`, `opencode`, or an agent of your own |
| `--hidden PATH` | A path in the loop hidden from AI: the author and the loop's agents may not read it (repeatable) |
| `--skill DIR` | A skill folder for the author and the designers (repeatable) |
| `--questions operator` | Let an agent author ask you a question, answered within `--wait-s` seconds (default 600) |
| `--no-run` | Write and check the loop; run nothing |
| `--repair` | The loop in `--dir` was refused: check it and repair it, writing nothing new (`--note` says what to do) |

The author gets the skills `flux-loop-creator` and `flux-loop-commands`, and reads the bundled
loops in `flux/applications/` for reference. See the [CLI](../reference/cli.md#flux-ask) for every
option.

## In the web app

**New loop › Agent** walks through four stages:

1. **Ask**: the loop's name, the agent, what the loop should do, and the files it should read.
   Mark a file **Hide from AI** to keep it from every agent (a gate's tests, for example).
   **Ask me when unclear** lets the agent ask you questions.
2. **Drafting**: its work as lines, what it read, wrote and ran. A question shows inline with
   its choices as buttons, a free answer, or **You decide**. If its check refuses the draft,
   **Try again** sends it another round, or write a note telling it what to do.
3. **Review**: its decisions, grouped by the configurator's steps, each with its reason.
   **Keep** one, **Change** it in the configurator at that step, or **Keep the rest**. A change
   asked of the agent shows as a diff on the document.
4. **Ready**: `flux task check` on the draft. **Run one cheap pass first** runs one pass that
   stops at the first measurement. **Make loop**, or **Open in configurator** to edit it first.

The draft exists as a loop from Drafting on; this browser remembers it, so you can come back.
A loop's **Settings › Problem › Agent** asks an agent to change an existing loop the same way.

## Check what it wrote

An author's loop is a first draft. Before a long run:

- Read the checks: do they test what you care about, on enough inputs?
- Read the measurements: the right workload, the right stage, `workers: 1` for timings?
- Read the objectives: the limit you meant, in the right direction?
- Run one pass (`flux task run FOLDER --passes 1`) and read the [report](../guide/run.md#read-the-result-and-full-output).

[Configure](../guide/build-your-own.md) explains each part of the document, and
[Knowledge, skills, hidden paths](../guide/knowledge.md) what the agents read.
