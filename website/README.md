# website/ -- the Flux documentation site

The public site is [MkDocs](https://www.mkdocs.org/) with the
[Material](https://squidfunk.github.io/mkdocs-material/) theme, deployed to GitHub Pages by
`.github/workflows/pages.yml` on every push to `main`.

| path | what it is |
|---|---|
| `mkdocs.yml` | the site configuration and the navigation |
| `docs/index.md`, `docs/guide/` | the hand-written pages: the home page and the guides (the site guides a new user; the applications are documented in the repository) |
| `docs/assets/` | the loop crafter (`crafter.js`, `crafter.css`) and the tool catalog it reads (`tools.json` = `flux tools --json`) |
| `overrides/` | theme overrides (the footer) |
| `site/` | the build output (git-ignored) |

## Preview it locally

The site itself needs no Nix: a plain Python environment with the same pinned Material version
the workflow installs.

```bash
python3 -m venv ~/.venvs/mkdocs
~/.venvs/mkdocs/bin/pip install "mkdocs-material==9.6.*"

# from the repository root
~/.venvs/mkdocs/bin/mkdocs serve -f website/mkdocs.yml            # live preview at http://127.0.0.1:8000
~/.venvs/mkdocs/bin/mkdocs build -f website/mkdocs.yml --strict   # what the workflow runs; output in website/site/
```

`--strict` turns every warning (a broken link, a page missing from the navigation) into an
error, as the deploy does, so run it before pushing.

## Adding a page

Write the Markdown under `docs/`, add it to `nav:` in `mkdocs.yml`, and build with `--strict`.
Link to other pages by relative path (`../guide/loop-shape.md`) and to repository files by
their GitHub URL, since only `docs/` is published.
