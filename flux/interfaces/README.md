# interfaces/ — the CLI

`cli/` holds the `flux` console script, the one way in for people, scripts and coding agents
alike. [docs/agent-surface.md](../../docs/agent-surface.md) says how scripts and agents drive
it.

`flux task check <doc>` and `flux task run <doc>` drive the one loop from a problem document
(D519); `flux task run <doc> --json FILE` also writes the answer (the decision, the frontier,
what was refused, what is not established, the lessons, the report's lines and the
application's own `result`) for a script. `flux ask` starts from a prompt: an author, the model
or a coding agent, writes the document. `python rtl.py test|measure` are the two tools an RTL
document names. `flux report`, `flux status`, `flux stop`, `flux attach` and `flux run`
operate a campaign and its record (D512, D513, D524). The IR-and-evaluator commands (`flux
import`, `flux eval`, `flux replay`) are gone (D954).
