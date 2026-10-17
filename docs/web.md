# The web interface

The web app's user documentation is on the site:
[Tour](https://choelzl.github.io/flux/web/), [Live](https://choelzl.github.io/flux/web/live/), [Results](https://choelzl.github.io/flux/web/results/),
[Files and settings](https://choelzl.github.io/flux/web/files/) and [Server and admin](https://choelzl.github.io/flux/web/admin/). The
[Preview app](https://choelzl.github.io/flux/preview/) runs it in a browser on sample data.

This page keeps what a contributor needs.

## Testing the web UI

- `python3 tests/check.py` (from `flux/`, dev shell): ruff, unit, heavy and browser tests in
  parallel (~4 min); `tests/check.py unit e2e` for some. `--area live,files` runs only those
  areas' unit files and browser steps (`tests/areas.py`, `--list` for the areas); `--changed`
  only the areas the uncommitted changes touch (`--changed main~3`: since that commit).
- `python3 tests/e2e/web_ui.py`: its own `flux serve` with three users, headless Firefox through
  every page (login, New loop, upload, tabs, Files, Direct edit, variables, sharing, start/stop,
  watcher, admin, dark theme, agent Test with a stand-in Codex, phone width). Fails on script
  errors or red notices; screenshots in `~/snap/firefox/common/flux-e2e/shots/` (`FLUX_E2E_HOME`).
  `FLUX_E2E_SANDBOX=1` sandboxes the loops; `FLUX_E2E_STEPS="login refused,login,invitation,insights"`
  runs a few steps (the first two log in).
- Live's task model (`static/live/events.js`, a journal replayed) is tested under node: `tests/unit/test_live_events.py`.
