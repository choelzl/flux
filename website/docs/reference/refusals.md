# Common refusals

`flux task check` prints `not a task:` and the reason, and exits 2. The usual ones:

| Message (start) | Fix |
|---|---|
| `keys a document does not have: X; the keys are ...` | A misspelled or removed key; `flux task migrate` handles old ones. `id` is one: rename the folder instead |
| `` `statement` must be a non-empty string `` | Say what is to be made |
| `` `problem.yaml` names 1 path the loop folder does not have `` | Add the listed file to the folder, or fix the key that names it |
| `flow: X is not a box of the drawing` | Use one of the boxes in [flow settings](../guide/parameters.md#flow-settings) |
| `flow.critique is a critic on each box it judges` | Write `critic:` under `orchestrate`, `test`, `measure` or `select` ([critics](../guide/parameters.md#critics)) |
| `flow.test is never delegated to an agent: it establishes facts` | `test` and `measure` are commands only |
| `flow.X: K is not a setting of this box or an agent's` | An option on the wrong box ([agent options](agents.md)) |
| `flow.X: timeout_s is an agent's option, not model's` | Agent options need `by: <agent>` |
| `flow.generate is "model", {command: [...]}, {catalog: [...]} ...` | One form only; no `timeout_s` on generate |
| `flow.generate.agent.session: generate keeps one session per part` | Remove `session` from `generate` |
| `flow.dse is orchestrate` / `flow.extract is knowledge: {lessons: mined}` | Old names: `orchestrate` and `knowledge.lessons` |
| `flow.knowledge: who digests the papers is digest:` | Write `knowledge: {digest: claude}`, not `by:` |
| `... says {x}, which is neither a knob ... nor the loop's` | A misspelled placeholder, or a knob not in the space |
| `flow.dse.space knobs (...) are said by no command` | Name a `{knob}` or `{point}` in a command, or generate with a command |
| `flow.measure.X: a command stage needs metrics ... or metrics_re` | Say what the stage prints |
| `roles.orchestrator: P: K is not one of its fields` | An option the policy does not have; the message lists its fields ([search settings](search.md)) |
| `a task divides into parts of ONE artifact or into subtasks ..., not both` | Choose one |
| `budget keys [...] are not loop knobs` | See [budget](../guide/parameters.md#budget); `finalists` and `calibrate` belong in `flow` |
| `flow.dse: pareto needs two objectives` | Add a second objective or choose another policy |
| `baseline takes file OR command OR metrics` | One source per baseline |
| `` `problem.yaml` is not a mapping of keys `` | The file is a list or a single value; start from `flux new NAME` |

A missing file is listed with the key that names it and what the folder holds. A refused
document never runs; `flux task check FOLDER` shows the same message before you start.
