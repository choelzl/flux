# `flux-llm`

Shared handling of raw LLM output: the `LLMProposer` protocol (`str -> str`), `InvalidLLMProposal`,
and `strip_markdown_fence`.

Exists because four packages had grown their own copies and they drifted — see
[docs/decisions.md](../../docs/decisions.md) D191 and D200.
