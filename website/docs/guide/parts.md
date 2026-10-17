# Split a larger design

A design too large to write or check in one piece can be split: into **parts** of one artifact,
or into **sub-loops**, each a loop of its own whose decided designs a parent composes. Use one or
the other, never both. The fields are in the
[Document reference](parameters.md#parts-and-sub-loops).

## Parts of one design

```yaml
parts:
  decoder: the 8b10b decoder
  encoder: the 8b10b encoder
```

`parts` is a list of names, a map of names to descriptions, or `decompose` to let the
orchestrator divide the goal itself (into at most 8). Names are a letter, then letters, digits,
`_` and `-`. Each part is drafted and checked on its own, then the parts are joined in order,
separated by a blank line, and the whole is measured.

Parts suit a model or agent generator that writes one file in pieces. `budget.parallel_parts`
drafts several at once; `budget.regenerate` (or `--regenerate PART`) drafts a part again
instead of reusing the admitted one.

## Sub-loops

```yaml
subtasks: [ops/recip, ops/exp]
flow:
  generate: {command: "{python} {home}/compose.py {parts} {artifact}"}
```

Each folder in `subtasks` holds its own `problem.yaml`, named by its folder (no `id`). A child
inherits the parent's `contract`, `language`, `flow` (checks, measurements, space and knowledge
included), `objectives`, `skills`, `params`, `budget` and `ladder` unless it says them; `flow`,
`budget` and `params` merge key by key, the child's winning. It never inherits `statement`
(each child says its own), `parts`, `subtasks`, `baseline`, or the parent's
`generate: {command}`.

Children share the parent's `out/` and `workbench/`; each child's record is `<parent>/<child>`.
`subtasks: decompose` lets the orchestrator divide the problem (at most `max_subtasks`, default
4), and `budget.max_depth` (default 3) bounds the nesting.

## Composing the whole

Once every child has decided, the parent's generator command composes their designs into the
whole. `{parts}` is a JSON file mapping each child's name to the path of its decided design:

```python
import json, sys
parts = json.load(open(sys.argv[1]))                       # {"recip": "/.../recip.sv", ...}
open(sys.argv[2], "w").write("\n".join(open(p).read() for p in parts.values()))
```

The whole goes straight to the measurements; it is not checked again, so write and test the
composition as carefully as a generator. Without a composition command, the parent composes
nothing. A parent that generates with a model or agent can instead draft for its children.

The [composition contract](../reference/commands.md#composition-command) has the details, and
the [NLU example](https://github.com/choelzl/flux/tree/main/flux/applications/nlu) shows a
larger loop split into child folders.
