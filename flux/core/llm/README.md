# `flux-llm`

Talking to a model, one way (D508): `Proposer.propose(prompt, *, schema, tools, budget) -> Reply`,
with `OpenAIChatProposer` for any OpenAI-compatible server (LocalAI, llama.cpp, Ollama's `/v1`,
OpenRouter -- `FLUX_LLM_REMOTE` decides whether a request leaves the machine) and
`ScriptedProposer` for tests. Beside them: the tools a turn may call (`Tool`, `ToolBudget`,
`Hop`, D505), the bounded ask-check-repair round (`ask_until`, `refine`, D450),
`strip_markdown_fence` and the knowledge-block budget (`fit_to_budget`).

Exists because four packages had grown their own copies of the model call and the fence stripper
and they drifted (D191, D200); by D508 the package itself had grown four proposer shapes and a
dispatcher that read signatures, and those went the same way.
