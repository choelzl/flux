# Glossary

The words Flux uses are defined on the site: [Glossary](https://choelzl.github.io/flux/reference/glossary/).
The terms below are the code's own, for contributors; the structure is in
[architecture.md](architecture.md).

**Step command.** A command a document names for what it cannot say in prose or numbers -- a
check, a measurement, a generator, a search (`orchestrate: {command}`), a composition -- often
`python -m flux_<app>.steps ...` from a package beside it. There are no worlds (D803).

**Mentor, knowledge.** What the model reads besides the problem: a methods sheet
(`flow.knowledge: {sheet: ...}`), a library of papers, facts mined from past records, and the
record read back. The mentor is the fourth role, next to orchestrator, generator and
evaluator.

**Role.** One of four jobs in the loop: orchestrator (what next), generator (draft),
evaluator (measure), knowledge/mentor (what to read). Each has a model half and a
pre-written half; `--role` swaps them.

**D-numbers.** Entries of the design log, [decisions.md](decisions.md): D1, D2, and so on,
each a decision with its reason and what was checked. Code comments and older documents cite
them.

