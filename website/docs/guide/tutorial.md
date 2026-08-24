# Tutorial: a new problem, explored by the loop

The tutorial walks through setting up a problem of your own and letting Flux explore it, with a
real run: a 16-bit integer square root in SystemVerilog, made fast and small on ASAP7.

- what each agentic role is and the flag that turns it on
- `flux selftest`, `flux new`, the golden model, the problem document block by block
- `flux task check`, the run, and what each pass did (the first design, a rest, an explored
  better one: 342 MHz and 45 um2, then 396 MHz and 25 um2)
- reading the results, making the run more agentic, and a space of knobs searched in phases

It lives in the repository, next to the example's two files:
[docs/tutorial.md](https://github.com/choelzl/flux/blob/main/docs/tutorial.md).
