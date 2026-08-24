# interfaces/ — the CLI

`cli/` holds the `flux` console script, the one way in for people, scripts and coding agents
alike. [docs/agent-surface.md](../../docs/agent-surface.md) says how scripts and agents drive
it.

`flux task check <doc>` and `flux task run <doc>` drive the one loop from a problem document
(D519); `flux task run <doc> --json FILE` also writes the answer (the decision, the frontier,
what was refused, what is not established, the lessons, the report's lines and the
application's own `result`) for a script. `flux ask` starts from a prompt: an author, the model
or a coding agent, writes the document. `flux rtl test|measure` are the two tools an RTL
document names. `flux report`, `flux status`, `flux stop`, `flux attach`, `flux run` and
`flux gc` operate a campaign and its record (D512, D513, D524); `flux import`,
`flux eval` and `flux replay` are the IR-and-evaluator path (validate and hash a document,
evaluate it through a named backend, replay a stored result).
