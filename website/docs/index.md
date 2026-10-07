# Build a Flux loop

Flux tries designs, checks that they are correct, measures them, and chooses a result from your
objectives. A loop is a folder containing `problem.yaml` and the scripts or reference files it
uses. You choose whether designs come from a script, a model, or a coding agent.

**Start with [Create your first loop](guide/tutorial.md).** It builds a complete search from an
empty folder, uses only Python, and runs without an AI model or hardware tools.

| Next step | Guide |
|---|---|
| Define correctness, measurements, and what wins | [Configure a loop](guide/build-your-own.md) |
| Choose a search strategy or give an agent room to explore | [Search and agents](guide/loop-shape.md) |
| Build the document in a browser | [Loop crafter](guide/loop-crafter.md) |
| Look up a field, its default, or its allowed values | [Parameter reference](guide/parameters.md) |
| Start, stop, resume, and inspect a run | [Run and results](guide/run.md) |

The guide follows one working example. The reference keeps the less common settings separate
so you can look them up as your loop grows.

Source code and worked hardware problems live in the
[repository](https://github.com/choelzl/flux) and
[applications directory](https://github.com/choelzl/flux/tree/main/flux/applications).
