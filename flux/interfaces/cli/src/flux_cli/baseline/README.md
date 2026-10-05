# __NAME__

A loop for Flux: this folder is the problem, and what its runs keep.

| part | what it is for |
|---|---|
| `problem.yaml` | the problem: what to design, the gate, the stages, the objectives, who works each part (`NAME.problem.yaml` beside it: another problem of the same loop) |
| the files it names | scripts, a golden model, a spec, tools -- read by the gate and the stages (`{home}` is this folder) |
| `library/` | papers and notes the loop reads: digested for its model and agents, cited in their prompts |
| `workbench/` | the agents' notes and tools, written by them and kept from run to run |
| `out/` | what its runs keep: the record of every design and measurement, the decided design, the caches |
| `runs/` | (on a server) the log, the answer, notes and questions |
| a folder with a `problem.yaml` | a sub-loop: it says only what differs from this one |

    flux task check __NAME__           # what is missing, what it needs
    flux task run __NAME__ --passes 1
