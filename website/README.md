# website/ -- the Flux documentation site

The public site is [MkDocs](https://www.mkdocs.org/) with the
[Material](https://squidfunk.github.io/mkdocs-material/) theme, deployed to GitHub Pages by
`.github/workflows/pages.yml` on every push to `main`.

The site has five tabs: Get started (install, the first loop, the loop crafter, a loop written
by an agent), Guides, Web app, Preview app, and Reference. Keep introductory material short;
explain choices in the guides and put field defaults and less common options in the reference.
Keep existing URLs: a section moved to another page leaves an anchor behind (see the end of
`docs/guide/parameters.md`). Public pages carry no decision numbers and no test paths.

| path | what it is |
|---|---|
| `mkdocs.yml` | the site configuration and the navigation |
| `docs/index.md` | the home page |
| `docs/start/`, `docs/guide/` | install, the runnable first loop, the guides; `guide/parameters.md` is the Document reference |
| `docs/web/` | the web app's pages, with screenshots in `docs/assets/web/` (light and dark, shown by `#only-light` / `#only-dark`) |
| `docs/reference/` | search policies, agent options, commands, presets, the CLI, refusals, the glossary |
| `docs/reference/cli.md` | generated: `python3 tests/unit/test_cli_reference.py` from `flux/` in the dev shell rewrites it, and the test fails when it is stale |
| `docs/assets/flux-theme.css` | the web app's colours mapped onto Material's variables |
| `docs/assets/` | the loop crafter: its model (`crafter-model.js`, shared with the web app's configurator), its page's script (`crafter-app.js`, which mounts the web app's configurator with no server), `crafter.css` and the tool catalog it reads (`tools.json` = `flux tools --json`) |
| `hooks.py` | publishes the web app's files the crafter and the Preview app import (`flux/interfaces/web/src/flux_web/static`, imports followed from `assets/crafter-app.js`, the app's `app.js` and `assets/preview/mock.js`) at `assets/app/`, straight from the app's folder |
| `docs/preview.md`, `docs/preview/app.html` | the Preview app tab: the whole web app in a frame, in the browser, over a mock; `/preview/#/app/adder8/live` deep-links into it |
| `docs/assets/preview/` | the mock (`mock.js`: `/api` answered from the fixtures, a recorded run replayed as the live stream, writes kept in memory), the tab's frame script and styles, a vendored js-yaml |
| `docs/preview/data/` | the Preview app's fixtures: recorded from a server over sample data and scrubbed by `tools/preview_record.py` (its docstring says how to record them again) |
| `overrides/` | theme overrides: the footer, the inline logo |
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

Also run the first-loop tutorial from its fenced code in a fresh temporary folder when changing
its document, scripts, or commands. Check its decision and output filenames, not just whether
the YAML parses. Configuration snippets on other pages are fragments: validate them merged
into a complete document using the current loader. Defaults come from `LoopRequest`, `Check`,
`Stage`, the objective model, and the public `document/surface.py` syntax.

## Adding a page

Write the Markdown under `docs/`, add it to `nav:` in `mkdocs.yml`, and build with `--strict`.
Link to other pages by relative path (`../guide/loop-shape.md`) and to repository files by
their GitHub URL, since only `docs/` is published.
