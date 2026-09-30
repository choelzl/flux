# Web interface: ideas for later

Not built yet, roughly in the order they would help. D683-D684 hold what is built.

## Creating loops
- The configurator (D686) keeps aside what it cannot say: grow it to edit an agent's own
  settings, objectives with a stage or tie, a failure pattern, parts with statements.
- Keep the document's comments when the configurator saves (a round-trip YAML editor).
- Validate while typing: `flux task check` on save, its findings beside the editor.
- Clone an application, or start from another user's (with their consent).

## Following runs
- Notifications beyond the page (D688 has the page, the bell, the desktop): e-mail or a webhook.
- The workbench's history: how its tools and notes changed, run by run.
- Compare two loops: frontiers and best-so-far overlaid.
- A pass timeline: what each pass tried, admitted and refused, from the record.
- The log split by start (jump to a start's line).
- Probes on the agent-turn view: which gate and stage checks the agent ran, and their results.
- Remember a loop's start options (the last ones as the dialog's defaults).

## Accounts and operations
- Groups: share an application with read or run access.
- Quotas: CPU-hours, runs and storage per user; the admin sees usage.
- A server policy that users bring their own model key (`--user-model-required`).
- A "test the endpoint" button (with care: the server would fetch a URL a user names).
- Clean-up: old runs' logs and traces, per user and per age (`flux gc` for the server).
- Backups of the server's data and every record.

## Presentation
- Keyboard shortcuts (`g l` for loops, `/` to focus a filter).

Not wanted: API tokens, SSO/OIDC, a separate OS user per account.
