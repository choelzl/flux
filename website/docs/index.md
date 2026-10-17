---
hide:
  - navigation
  - toc
---

# Flux

**Flux searches for better designs and keeps only what it can prove.** It makes designs with a
script, a model or a coding agent, checks each one against your tests, measures the ones that
pass with real tools, and decides from your objectives. It is built for hardware and software
design-space exploration: RTL on an open 7 nm flow, Python and C code, cache prefetchers, or
any program with settings to tune.

![A loop's results in the web app: every design measured, the decision marked](assets/web/results-light.webp#only-light)
![A loop's results in the web app: every design measured, the decision marked](assets/web/results-dark.webp#only-dark)

<div class="grid cards" markdown>

-   **First loop**

    ---

    A complete loop from an empty folder, in Python only: no model, no hardware tools.

    [First loop →](guide/tutorial.md)

-   **Loop crafter**

    ---

    Build the document in your browser, step by step, with the web app's own configurator.

    [Loop crafter →](guide/loop-crafter.md)

-   **Write a loop with an agent**

    ---

    Say what you want, give it your files, and let an agent write and check the loop.

    [Write a loop with an agent →](start/ask.md)

</div>

## How a loop works

A loop is a folder: a document, `problem.yaml`, and the scripts it names. Each pass, Flux
chooses what to try, makes a design, runs your **checks** (a design that fails is never
measured), runs the **measurements** cheapest first, and keeps the design that best meets your
**objectives**. Every design, number and refusal goes on the record, so a run can stop and
resume, and a result can be traced to what produced it.

## Where to go next

| To | Read |
|---|---|
| Install Flux, the web app and the hardware tools | [Install](start/install.md) |
| Define the checks, measurements and what wins | [Configure](guide/build-your-own.md) |
| Choose a search, or give an agent room to explore | [Search and agents](guide/loop-shape.md) |
| Follow runs and results in the browser | [Web app](web/index.md) |
| Look up a field, a preset, a command | [Reference](guide/parameters.md) |

The source, and worked hardware problems, are in the
[repository](https://github.com/choelzl/flux) and its
[applications](https://github.com/choelzl/flux/tree/main/flux/applications).
