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
- Compare runs: frontiers and best-so-far of two campaigns overlaid.
- A pass timeline: what each pass tried, admitted and refused, from the record.
- Probes on the agent-turn view: which gate and stage checks the agent ran, and their results.
- Resume / "one more pass" on an ended run; a run's options edited before it restarts.

## Accounts and operations
- API tokens so `flux` on a laptop can drive the server (start, follow, fetch results).
- Groups: share an application with read or run access.
- Quotas: CPU-hours, runs and storage per user; the admin sees usage.
- A server policy that users bring their own model key (`--user-model-required`).
- A "test the endpoint" button (with care: the server would fetch a URL a user names).
- OIDC login beside local accounts.
- Each user's runs as their own OS user (or a subuid range), not the server's.
- Clean-up: old runs' logs and traces, per user and per age (`flux gc` for the server).
- Backups of the server's data and every record.

## Presentation
- Render the standings as tables and charts instead of key/value lines.
- Syntax highlighting in the editor (a small highlighter, still no build step).
- A dark/light switch beside the system preference.
