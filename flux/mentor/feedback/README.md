# knowledge/feedback/ -- typed operator guidance while a loop runs

A design loop runs for minutes to hours, and until now the only way to redirect it was to kill
it and edit the flags. This package is the channel for the other option: the operator types a
line into the running demo's terminal, and it reaches the model's next proposal prompt --
labelled for what it is ([docs/decisions.md D388](../../../../docs/decisions.md)).

What it is, deliberately:

- **Advisory free text only.** A note is a direction, not an instruction: it goes into the
  proposer prompt under a `HUMAN GUIDANCE` heading that says so, and every candidate the model
  proposes still passes the same gates, screens and confirmations as any other. No note can
  change a constraint, skip a rung, or stop the run.
- **A fourth provenance class.** The mentor layer keeps curated text, measured facts, and
  model-drawn conclusions under separate, honest headings; operator guidance is a fourth kind
  -- neither measured nor published -- and its heading declares that.
- **TTY-gated.** `FeedbackChannel` is active only when stdin is a real terminal; under CI,
  pipes, or redirection it is inert (no hint printed, nothing read), so nothing about a
  scripted run changes.
- **On the record.** Every note is persisted as a `human_note` event in the loop's campaign
  store and echoed into the report's lessons tagged `[human]` -- including on a model-free run,
  where the report says the note was received but reached no prompt. A resumed campaign reloads
  earlier notes, marked as from an earlier run.

Surface: `FeedbackChannel` (start / drain / close), `Note`, and `render_guidance(notes)` --
the labelled prompt block with newest-kept truncation that announces what it omitted.

First consumer: the prefetcher loop (`applications/prefetcher/`), which drains the channel
before each proposer call. No MCP/CHIA surface yet; a `flux_feedback_post` tool for
agent-delivered guidance is named future work in D388.
