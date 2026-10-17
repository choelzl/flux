# Tour

`flux serve` is a shared server with accounts. Each user has loops; each loop is a document and
its files, and runs on the server, sandboxed. You check, start and stop loops, follow them live,
and read their results in the browser. [Install](../start/install.md#web-app) starts one.

![A loop's Overview: its state, its decision against the limits, and the best designs so far](../assets/web/overview-light.webp#only-light)
![A loop's Overview: its state, its decision against the limits, and the best designs so far](../assets/web/overview-dark.webp#only-dark)

## Loops

The first page lists your loops: their state, how many designs were accepted and measured, and
the decision's number on the first objective (✓ or ✗ against its limit). Search, filter by
state, and sort by activity, name or decision. **Shared with me** lists loops others shared with
you.

## New loop

**New loop** offers five ways to make one:

| Way | What it does |
|---|---|
| **Configurator** | A form in six steps, the same as the [Loop crafter](../guide/loop-crafter.md) |
| **Create or upload** | An empty loop, or one from a folder, files or a `.zip` you drop or pick |
| **Direct edit** | Write the YAML yourself |
| **Agent** | An agent writes the loop from your prompt and files ([Write a loop with an agent](../start/ask.md)) |
| **Clone** | A copy of an existing loop, with its problem but none of its runs |

## A loop's page

A loop has six tabs, and **Start** (or **Stop** and **Restart** while it runs) in its header.

| Tab | What it shows |
|---|---|
| **Overview** | Its state, the decision against the limits, the best designs, a best-so-far chart, the last pass |
| **Live** | What the run does now, as a tree, a graph, a timeline or the log ([Live](live.md)) |
| **Results** | Every design measured, charts, ideas and the library ([Results](results.md)) |
| **Agents** | Every model and agent turn: prompt, reply, tool calls, time and tokens |
| **Files** | The loop's files and the agents' workbench ([Files and settings](files.md)) |
| **Settings** | Preferences, sharing, maintenance, and the problem itself ([Files and settings](files.md#settings)) |

**Start** asks for the passes (or "until I stop it") and whether to stop at the first
measurement. It runs `flux task check` first when the inputs changed; if the check fails, the
button reads **Start anyway**. A start resumes from the record. **Stop** and **Restart** take
effect after the current pass; press again to abandon the pass now.

## Talk

**Talk**, on every tab, opens a panel of conversations about the loop. **Ask** puts a question
(why it stalls, which design is best, what to try next) to an agent or Flux's model, which reads
the document, the files, the record and the log, and changes nothing. While the loop runs, a
note you send there reaches its next prompt, or answers an agent's open question.

## Everywhere

- **Theme:** system, light or dark, from the top bar; remembered in this browser.
- **Bell:** a run ends or fails, an agent asks a question, a loop is shared with you.
- **Raw** and **Fullscreen** on files, logs, results and agent turns.
- **Addresses** name the view, so a link opens it: `#/app/NAME/live/log`,
  `#/app/NAME/settings/problem?step=graph`.
- **On a phone** the account items move to the ☰ menu, and lists and tabs stack.

The [Preview app](../preview.md) runs the same pages in your browser on sample data.
