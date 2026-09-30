# Contributing to Flux

## Getting set up

```bash
cd flux && nix develop                 # every tool: Verilator, Yosys, OpenROAD, ChampSim, Python
python3 -m pytest -q -n auto tests/unit   # about 1,450 tests, a few minutes (heavy ones: -m heavy)
ruff check .                           # the lint CI runs (the pyflakes rules)
```

Without Nix, `pip install -e ./flux` gives the loop and the CLI (see the README), and the unit
tests that need no EDA tool run the same way. `tests/integration/` needs the tools, and in
places a model; CI runs it nightly.

## The design log, and the D-numbers in the code

Every non-trivial decision has a number, `D1` onward. [docs/decisions.md](docs/decisions.md)
folds the ones that still hold into what Flux does today, topic by topic, each point citing the
decisions it folds. A comment like
`# D593: a pass at rest explores` points at those. The comment says what the code does and the
entry says why, so read it before changing code it cites. A change of your own gets the next
number and a short entry under "Since the fold" in docs/decisions.md: the decision, its key reason or number, how it was verified.

## Where to change what

- A new problem is a document, not code: `flux new`, then [docs/cookbook.md](docs/cookbook.md).
- A search policy, a world or a role of your own can live beside the document, with no change
  to Flux ([docs/extending.md](docs/extending.md)).
- The loop itself: `flux/core/loop/README.md` maps the modules.
- A new application under `flux/applications/` needs a document, a README and a line in the
  tables of the README and `flux/README.md`. The website guides a new user and lists no
  applications.
  `tests/unit/test_rim_conformance.py` lists the applications.

## Before sending a change

- `ruff check .` and the unit tests pass.
- Anything user-visible was run live, and its entry says what was measured.
- Docs stay true: `docs/` for users, `website/` for the site (`mkdocs build --strict`), and the
  link checker finds nothing broken.
