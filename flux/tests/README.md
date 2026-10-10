# tests/ — unit, integration, e2e, golden

`e2e/web_ui.py` runs the web interface in headless Firefox against its own temporary server,
users, and loop data. Run it from `flux/` with
`nix develop --command python3 tests/e2e/web_ui.py`. It exits nonzero on a failed check and
saves failure screenshots under the browser fixture's `shots/` folder.

The independent flows in `e2e/web_ui_general.py` cover deep-link reloads and browser history,
delayed file responses and retries, concurrent edits and unsaved drafts, literal rendering of
file and agent text, logout/re-login isolation, and admin restart confirmations and results.
Run these flows with:

```sh
FLUX_E2E_STEPS="reload and browser history,delayed files and retry,concurrent file edits,safe text rendering,logout and re-login,admin restart all" \
  nix develop --command python3 tests/e2e/web_ui.py
```

`FLUX_E2E_HOME=/absolute/path` chooses an isolated browser profile and upload fixture folder.
For Snap Firefox, use a path that Firefox can access, such as one under
`~/snap/firefox/common/`. No model or coding agent is needed by these flows.

`FLUX_E2E_STEPS="selected problem"` checks the problem picker, the filename below the task
name after starting another problem, the next start's default, and selections refreshed from
another view. `test_web_document_selection.py` verifies owner/admin starts use the selected
YAML and its matching record, update polled state while running and stopped, and leave other
problem files intact.

`FLUX_E2E_STEPS="login refused,login,upload,run history,live scroll"` checks agent prompt
viewers for live tasks and older runs, including fullscreen text and retained scroll positions.
`test_agent_live.py` checks that fresh and resumed agent inputs are saved in full at task start.
`test_agent_brief_context.py` checks full nearest-paper digests, missing-digest handling, task-first
briefs and exploration context containing only incumbent intent and measurements. The library,
novelty and prototype tests also exercise these changes through real brief assembly and fake agents.
Explore and Variations tests check that old best designs and cached prototypes are not used as
source seeds, that comparison intent/numbers remain available, and that earlier candidates survive
failed experiments. Variations also bypass incumbent tuning and prototype cost passes.

`e2e/web_ui_lifecycle.py` also covers fullscreen drafts (selection, scroll, Escape and navigation),
raw file output, reset warnings/cancellation and retained inputs/settings, and older starts'
logs, complete agent turns, design source, reloads and delayed responses. It uses real loop
records and API responses, without models or agents:

```sh
FLUX_E2E_STEPS="fullscreen draft and raw,reset confirmation,run history isolation" \
  nix develop --command python3 tests/e2e/web_ui.py
```

`FLUX_E2E_STEPS="admin usage totals"` checks the Usage tables' totals, raw-cost rounding,
summed time buckets, the displayed-loop subtotal, disk totals, mobile labels and range changes.

`FLUX_E2E_STEPS="admin token rates"` checks Admin Resources' separate input/output rates,
agent/model totals, flat averaging intervals, hover values on either side of a boundary, and
the interval label when switching history ranges, including 30 days for both machine and token
history. `test_web_history.py` checks month retention, progressive compaction, weighted averages,
peaks and outages across both tiers. Unit tests in `test_web_insights.py` and
`test_web_admin.py` cover overlapping and sub-second calls, window clipping, and retained
campaigns across starts and records without counting shared transcripts twice.

`FLUX_E2E_STEPS="ask conversations"` checks reply context, separate chats, retained drafts,
conversation collapse and keyboard expansion, collapsed state across refreshes, read-only viewers,
failed sends, long running-loop notes, activity polling without scroll/focus jumps, and the
fixed composer at desktop and phone widths. `test_web_asks.py` checks persisted reply branches,
model/agent prompts, permissions and complete conversation deletion.

`FLUX_E2E_STEPS="loop ownership"` checks rename and transfer dialogs, navigation, source retention,
the former owner's loss of access and shared-editor restrictions. `test_web_relocate.py` covers
records for multiple documents, retained logs and cached transcripts, variables and settings,
active-work refusal, collisions, metadata links, rollback and cloning admin-configured loops.
`FLUX_E2E_STEPS="admin permissions"` checks admin clone/transfer permission previews, the
unchecked default, opt-in preservation and regular-user restrictions. API tests also reject
forged opt-ins, preserve only execution permissions, and roll back failed permission saves.
`FLUX_E2E_STEPS="admin sharing"` checks admins adding, changing and removing other owners'
shares, same-name loop isolation and read-only sharing views for watchers and editors.
`FLUX_E2E_STEPS="user groups"` checks Users/Groups subtabs and remembered selection,
aligned group tables, long names and mobile layouts,
group creation/rename, group-wide Server access, member assignment, permission
checkboxes, group loop discovery and controls for viewers and runners. `test_web_groups.py`
checks legacy migration (including mixed access), stable group IDs, live revocation, group-wide server access,
cross-group sharing, independent permissions and all loop creation/execution entry points.

`FLUX_E2E_STEPS="admin impersonation"` checks View as eligibility, user navigation and files,
read-only enforcement, refresh persistence, the mobile return banner and audit attribution.
`test_web_impersonation.py` covers session isolation, permissions, settings, revocation,
server restarts, logout and returning after a target is disabled or the admin demoted.

`FLUX_E2E_STEPS="login refused,login,upload,reset loop"` checks reset warnings, cancellation,
Keep checkboxes, preserved workbench files and a full reset. `test_web_reset.py` also covers
selective preservation of history and caches, invalid options, active-work refusal and isolation.

`FLUX_E2E_STEPS="loop names"` checks configurator creation and upload validation. Naming tests
also cover the loader, CLI examples, rename and clone, and invalid paths and characters.

`FLUX_E2E_STEPS="dictionary metrics"` checks sparse dictionary columns, zero and missing
measurements, parent aggregates, relative values, expansion, shared Decision preferences, browser reloads and
the crafter's dictionary fields. `test_dictionary_metrics.py` covers parsing, inheritance,
configurable aggregates, recording, historical metadata and real loops that rank by a named test
or the parent mean.

`FLUX_E2E_STEPS="main measurements"` checks Settings' Visible/Main/% choices, loop-list and
decision summaries, mixed absolute/relative tables, navigation persistence, missing values,
multiple or no main metrics, and phone layouts. `test_web_measurement_summary.py` verifies
baseline and percentile references against the browser calculation, zero/negative references,
nonfinite values, references outside a paged result set, and owner/admin loop summaries.

Graph and measurement preferences belong to the project and are saved on the server.
`test_baseline_pass.py`, `test_baseline_selection.py` and `test_web_results.py` check that
pass 0 stays a reference: fresh and reused baselines cannot replace retained search decisions,
even with identical source, stale historical evidence or better baseline numbers. Legacy snapshots
and conclusions cannot select baselines, and live standings use retained search measurements.
`FLUX_E2E_STEPS="login refused,login,upload,compact tables,graphs,main measurements,dictionary metrics"`
checks shared Results/Decision settings, Pareto axes and focus, selected charts, hidden columns,
main metrics and percentage formats, including restoration after clearing browser storage.
The main-metric flow also checks that goal-free ranking objectives take precedence over earlier
goal constraints in summaries, settings and default charts; explicit selections still override them.
`test_web_result_preferences.py` covers sessions/server restarts, owner/editor/viewer/admin
permissions, concurrent partial updates, validation, CSRF, impersonation, isolation and
rename/transfer/clone/reset/delete behavior. `test_web_result_preferences_js.py` checks queued
saves, captured owner and choices, read-only exploration and recovery after failed saves.

`FLUX_E2E_STEPS="overview layout"` checks customization through Account settings only,
configuration section counts, full-width column controls, card selection, ordering and column
placement, 3–5 small-card limits, cancellation, save errors and retries, persistence across loops
and browser reloads, defaults, account isolation, always-visible alerts and phone layouts.
It also verifies that the mock-data preview uses real cards/charts and follows selection,
ordering and column changes before saving.
The expanded card catalog is checked with populated mock previews, saved layouts on empty loops,
main metrics and percentage formats, percentile fallback, zero references, missing goal-stage
measurements, shared Pareto preferences and narrow screens.
`test_web_overview_layout.py` covers validation, authentication, session/server persistence,
every available top/main card, read-only impersonation and accounts without loop permissions.
`FLUX_E2E_STEPS="overview ideas"` checks optional notebook loading, shared top/main data, recent
proposals and evidence, literal text, zero/pass-0 values, full-notebook navigation, loading failures,
empty notebooks, independent top/main selection and narrow screens.

`FLUX_E2E_STEPS="author progress"` checks creation/revision activity, live thinking and tool calls,
process liveness versus output age, endpoint retries, recovery after status-read failures,
preserved scroll positions/details, failed revisions with an existing document, navigation races
and phone layouts. `test_web_author_progress.py` runs a real author CLI with a controlled agent
that streams before completing, checks no-sandbox document validation and retained progress,
and refuses escaping log/journal links and malformed records.
`FLUX_E2E_STEPS="admin author containers"` checks creation/revision labels, active agent tasks
and View versus leftover Kill controls. `test_web_admin.py` checks authors without loop runs,
network helpers, stale containers for the same loop and refusal to kill attached tasks.

`FLUX_E2E_STEPS="live alt"` checks the optional Tree/Graph/Timeline layout, shared start/pass
selectors, baseline pass 0, command/stdin/stdout/stderr and prompt/conversation inspectors,
keyboard selection, pinned tasks across updates and view changes, returning to current activity,
older-start replay, missing data, retry, stream cleanup, narrow screens and navigation races.
`test_live_alt_model.py` checks sparse and parallel pass membership, root/child attribution,
current-task selection, setup, empty scopes and campaign selection across resumed starts.
Agent/tool unit tests check recorded commands and stdin delivery without duplicating large prompts.

`FLUX_E2E_STEPS="loop controls,admin restart all"` checks the two active-loop controls,
after-pass first presses, red NOW buttons, abandonment warnings/cancellation, and ordinary
after-pass actions in owner/admin lists. `test_web_loop_actions.py` covers real process exits,
remaining budgets, stop superseding restart, authorization at the boundary, retained requests
after a server restart, and bulk scheduling without restarting idle loops.

`FLUX_E2E_STEPS="ideas notebook"` checks the Ideas subtab before measurements exist, literal
note rendering, pass histories, failed attempts, Raw JSON, fullscreen, narrow screens and refresh.
`FLUX_E2E_STEPS="ideas navigation"` checks empty notebooks, zero values, missing pass numbers,
late fetches after navigation, fullscreen cleanup and deep-link reloads.
`FLUX_E2E_STEPS="ideas sharing"` checks watchers' owner-scoped requests, Raw access, a failed
refresh that preserves the current view, and a successful retry.
`test_ideas.py` covers persistence across passes/restarts, pending hypotheses, tool selection,
agent sidecars and repairs, prototype-to-design links, and real checks and measurements.
`test_ideas_resilience.py` covers parallel parts, build failures, interruption states, bounded
prompt memory, baseline exclusion, malformed agent files, and memory fallback when storage fails.
It also checks durable write retries, read recovery and removal of stale agent sidecars before repairs.
`test_web_ideas.py` checks read-only access, sharing and revocation, authentication, run isolation,
historical campaign/start boundaries, and preserving or clearing the notebook on reset.
`test_web_ideas_lifecycle.py` covers notebook preservation through rename/transfer, independent
clones, view-as access, share revocation and disabled accounts with existing sessions.

`FLUX_E2E_STEPS="partial agent usage,admin token rates"` also checks that interrupted-agent
usage is visibly partial and wholly missing usage is shown as unavailable. `test_agent_usage.py`
covers Claude's cumulative message snapshots, final-result precedence, repeated blocks,
OpenCode/Codex interruptions, and a real timed-out subprocess that reports more usage during
SIGTERM shutdown. `test_web_partial_usage.py` checks the resulting API totals and completeness.

`unit/test_web_api_lifecycle.py` checks live permission changes on the same login session:
revoking/restoring shares across history and download endpoints, downgrading editors before
pending mutations (without changing files or starting processes), and disabling an account.

See [docs/architecture.md](../../docs/architecture.md).

`unit/` has real tests for `flux-evaluator-abi` and `flux-store` (canonicalisation/hashing, record
type invariants, store round-trips/idempotency) without touching any external tool. `integration/`
holds the live checks that need a real tool or sandbox. Run with `nix develop --command python -m
pytest -q` from `flux/`. The conformance suite, the ZigZag and Timeloop tests (D958), the IR schema
tests and the unread golden simulator outputs (D959) are gone.
