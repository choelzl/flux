# Results

A loop's **Results** tab holds every design measured across all its starts. The decision is
said in a line above four views: **Designs**, **Graphs**, **Ideas** and **Library**.

![Results › Designs: every design at its deepest stage, the decision first](../assets/web/results-light.webp#only-light)
![Results › Designs: every design at its deepest stage, the decision first](../assets/web/results-dark.webp#only-dark)

## Designs

A table of the designs, each **accepted** or **failed** by the limits at its deepest stage, the
decision first; a design still short of its deepest stage is **pending**. Columns sort; filter
all, accepted, pending or failed. A draft the gate refused is not a
result; Live and the report show those.

- **Absolute / Relative:** numbers as they are, or as percent change from the latest baseline
  (without one, from the accepted designs' 90th or 10th percentile). Hover a cell for the value
  and its reference.
- **Dictionary metrics:** a dictionary's column is its own name; **Tests ▸** unrolls its tests.
  A missing or empty test stays unmeasured, never zero.
- **Compare:** tick two designs to see every number at every stage, the change (green where
  better), and their sources' difference.
- **A design** opens with what it does not meet, its numbers at each stage, and its source.

### Reject a design

**Reject…** on a design (its owner, with a reason) marks it: it stays shown but is never
chosen. Tick its numbers to have them measured again or cleared; at rest, **Also hide the
design** takes it out of the results, listed under **Invalidated by hand**, where
**Re-evaluate** checks and measures those designs again in one run, and **Restore** counts them
again. A critic's rejection reads the same way.

## Graphs

- **Pareto front:** any two metrics at one stage or the deepest; accepted and failed coloured,
  the decision a diamond, the front joined. **Focus** fits the axes to the feasible front.
- **Improvement by design:** the best so far for each metric chosen, design after design. Baselines are gray
  reference lines.

The choices (metrics, axes, stage, scope) are saved with the loop.

![Results › Graphs: the front of two metrics and the best so far](../assets/web/results-graphs-light.webp#only-light)
![Results › Graphs: the front of two metrics and the best so far](../assets/web/results-graphs-dark.webp#only-dark)

## Ideas

The loop's ideas notebook: each idea a model or agent proposed, and the trials that evaluated
it. "Measured" means there is evidence, not that it improved anything.

## Library

The papers and notes the loop's knowledge read, who digested each, and the digest the prompts
read.

## Elsewhere

- **Overview** shows the decision against the limits, the best three designs, and a
  best-so-far chart. Its cards are chosen under **Account › Overview layout**.
- `flux report RECORD` writes the same results as an HTML report beside the record.
