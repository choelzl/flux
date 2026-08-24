# The shape every loop shares

An application is one directory under `applications/`, and inside it one shape:

```
applications/<name>/
  lib/src/flux_<name>/      the domain library: the space, the gates, the flow
  demo.py                   the CLI: flags -> one request -> the printed report
  README.md                 the problem, the ladder, the standings, the flags
  experiments/              one-off studies that earned their keep
```

## Four roles, one loop

Every loop is the same four roles talking to each other -- and they are literally the
repository's top-level directories:

```mermaid
flowchart TB
    subgraph MENTOR["mentor -- what is known"]
        corpus["knowledge corpus<br/>(licensed, with provenance)"]
        record["campaign record<br/>measurements + lessons"]
    end
    subgraph ORCH["orchestrator -- spend the budget"]
        gates["gates: free refusals first"]
        search["search: enumerate / climb /<br/>propose (LLM role)"]
        frontier["frontier: both axes, whole"]
    end
    subgraph GEN["generator -- make candidates real"]
        gen["RTL / SystemC / configs / harnesses<br/>(LLM role: inventor + repairer)"]
    end
    subgraph EVAL["evaluator -- measure, never trust"]
        screen["cheap rung: orders,<br/>never quoted"]
        confirm["expensive rung: the only<br/>numbers the report quotes"]
    end
    human["human feedback<br/>(typed into the TUI, D388)"]

    corpus -- "guidance, fitted to budget" --> search
    record -- "reflection: measured facts,<br/>as arithmetic (D369)" --> search
    human -. "advisory notes; every candidate<br/>still passes the same gates" .-> search
    search --> gen
    gates -- "refused, with reason" --> report["decision-first report"]
    gen --> screen
    screen -- "ordering" --> frontier
    frontier --> confirm
    confirm --> report
    screen -. "every measurement" .-> record
    confirm -. "every measurement" .-> record
    report -. "conclusions, mined and<br/>labelled INFERENCE (D297)" .-> record
```

| role | directory | job | model's job title there |
|---|---|---|---|
| **mentor** | `mentor/` | hold what is known: the licensed knowledge corpus, the campaign record (`mentor/records`: results and conclusions), the laws extracted from it (`mentor/extract`), the operator's typed guidance | reader, author -- guidance is *fitted* to the prompt budget, never dumped |
| **orchestrator** | `orchestrator/` | spend the evaluation budget: gates first, then search strategies, then the frontier; the shared decision arithmetic (`orchestrator/decide`: corners, the knee, target-and-floor) picks the point | proposer -- one voice among enumerate/climb/solve, judged by the same gates |
| **generator** | `generator/` | turn a candidate into something a tool can run: RTL, SystemC, configs, harnesses | inventor and repairer -- every artifact verified against golden vectors before it counts |
| **evaluator** | `evaluator/` | measure on a ladder of rising cost; adapters behind one ABI, calibrated against references | none -- numbers come from tools or not at all |

The phases inside the orchestrator keep their old names and their old discipline:

| phase | what it does |
|---|---|
| **setup** | find the tools (refuse loudly if absent), load the inputs, open the cache |
| **gates** | free checks first: validity, proofs of impossibility, budgets -- anything that can refuse in microseconds runs before anything that costs seconds |
| **screen** | the cheap rung of a real tool: orders candidates, never quoted |
| **frontier** | both objectives, whole: every candidate better than everything cheaper |
| **confirm** | the expensive rung, spent on finalists spread *along* the frontier, plus the incumbent and reference |
| **report** | decision first; lessons; refusals with reasons; what is *not* established -- the closing sections share one grammar (`core/report`), so no loop drifts into a synonym |

## How a loop becomes an expert

The loop's knowledge is not a prompt that grows -- it is a record that compounds, and
every arrow in the flywheel is a mechanism with a decision-record entry behind it:

1. **Every measurement lands in the campaign record** (screen and confirm alike), keyed
   by the tool fingerprints and the code that ran -- so *resume means the record, read
   back* (D367): a second run starts where the first left off, and nothing measured is
   paid for twice.
2. **The record reflects as arithmetic, not memory** (D369): every one-knob pair the
   campaign has measured is a controlled experiment already paid for; its direction is
   computed fresh each run and handed to the first proposer prompt as *"what the record
   shows"* -- directions, not instructions.
3. **Conclusions are mined, labelled, and stored beside the data** (D297): what a run
   *meant* is written down as INFERENCE, distinct from what it measured, and the next
   run starts informed instead of rediscovering the shape of the space.
4. **Human feedback joins the same record** (D388, consumed by every model-role loop
   since D398): a note typed into the TUI reaches the next proposer prompt as HUMAN
   GUIDANCE -- advisory, persisted as a campaign event, echoed into the lessons; on a
   model-free run the lesson says honestly that the note reached no prompt.
5. **The knowledge corpus is curated, not scraped** (`mentor/knowledge`): licensed
   sources with provenance, mined into typed guidance, *fitted* to the model's context
   budget with what was dropped named -- a model reasoning from a narrowed view is told
   it is narrowed.
6. **The model is told what was refused, and why.** Refusals are not discarded; they are
   the cheapest teaching signal a search produces, and every proposer prompt carries
   them.

The result is the difference between an LLM *used by* a tool and an LLM *apprenticed
to* one: the roles give it jobs with acceptance tests, the record gives it the lab
notebook, and the gates make sure its confidence is never a substitute for a
measurement.

## The discipline that holds it together

Each rule was learned the expensive way, and each cites a numbered entry in the project's
[decision record](https://github.com/choelzl/flux/blob/main/docs/decisions.md):

- **Measured, not modelled.** A number in a report came from a real tool run, or it is
  labelled analytic. Screened numbers order; confirmed numbers get quoted.
- **A constraint is a refusal, not a low rank.** Below the floor, over the budget, failed its
  golden vectors: refused, with the reason recorded.
- **Two axes, one frontier.** Quality and cost are coordinates; the report lays the trade-off
  out and the decision rule -- a target, a floor, a preserved incumbent -- picks a point.
- **The model is a participant with a job title** -- proposer, inventor, repairer -- judged by
  the same gates as everything else, told what was measured and what was refused, and never
  the only path to a result.
- **Resume means the record, read back.** Caches are keyed by tool fingerprints and the code
  that ran; the campaign store re-seeds the next run, so nothing already measured is paid for
  twice.
