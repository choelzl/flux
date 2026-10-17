# Knowledge, skills, hidden paths

What the model and the agents read besides the document, and what they may not read. The
fields are in the [Document reference](parameters.md#knowledge-settings).

## Files, notes and papers

```yaml
flow:
  knowledge:
    files: [spec.md, reference.py]
    text: Prefer implementations that are easy to maintain.
    digest: claude
    lessons: mined
```

- **`files`** and **`text`** reach every prompt and brief: a spec, reference code, a method
  note. A folder counts as each of its files; PDFs are read as text.
- **The library**: the loop's `library/` folder and, on a server, the shared library hold
  papers and notes. **`digest`** says who summarises them (`model` or an agent).
- **`lessons: mined`** reuses lessons drawn from the loop's record; an agent's name has that
  agent draw them.
- **`knowledge: off`** turns all reading off.

A model that keeps getting a design wrong usually lacks a fact, not effort: a method note with
the method and the facts (not a design) helps more than more passes.

## Skills

A skill is a folder with a `SKILL.md`: front matter with `name` and `description`, then the
instructions, and any files they use. Put skills in the loop's `skills/` folder and they are
read without a word in the document. The model gets an index and a `skill` tool; every coding
agent of the loop finds them in its work folder. The built-in `flux-loop-commands` skill (how
to read the loop's record with `flux loop ...`) comes with them.

```yaml
skills: [skills, ../shared-skills]           # these folders instead of skills/
skills: {"off": [flux-loop-commands]}        # skills/ and the built-in one, this one off
skills: "off"                                # none at all
```

`--skill DIR` on `flux task run` or `flux ask` adds a folder for one run.

## Hidden from AI

A gate is only as good as the agent's ignorance of it. An agent that can read the tests can
write a design that passes them and nothing else. `knowledge.hidden` names paths no agent and no
prompt may read:

```yaml
flow:
  knowledge:
    hidden: [tests/, golden.py, "params/*.json"]
```

The loop's own checks, measurements and scripts read these paths as ever, and a check's failure
text still reaches the agent. Paths are relative to the loop's folder; sub-loops hide their
parent's paths too. An agent's own `hidden:` option adds paths for that agent alone.

Each agent's brief names them as off limits, and its permission settings refuse them where the
agent supports it (Claude Code and OpenCode; Codex has only the brief). A script the agent
writes could still open a file, so this is no sandbox: for secrets, rely on the
[sandbox](run.md#sandbox).

In the web app, hide a path:

- in **Files**, from a file's or folder's **Actions › Hide from AI**;
- in the configurator's **Graph**, on the **Knowledge** box;
- when adding files to **New loop › Agent**, with **Hide from AI** on the file;
- with `flux ask --hidden PATH` on the command line.

## Workbench

`workbench/` beside the document is the agents' own folder: tools they build and notes they
keep, shared by the loop's agents and kept across runs. `knowledge.workbench` says who sums it
up for the next prompt when it changed (`off`, `model`, or an agent); by default, whoever
digests the papers. In the web app it is **Files › Workbench**.
