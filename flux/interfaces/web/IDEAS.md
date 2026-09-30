# Web interface: ideas for later

Not built yet, roughly in the order they would help. D683-D684 hold what is built.

## Creating loops
- The loop crafter inside the app: build the document on its graph, then "Create" posts the YAML
  (`crafter.js` already exports `buildYaml`; serve it and add the button).
- Templates: `flux new --kind python|rtl|sweep|tune|rtl-sweep` from the page.
- Validate while typing: `flux task check` on save, its findings beside the editor.
- Clone an application, or start from another user's (with their consent).

## Following runs
- Compare runs: frontiers and best-so-far of two campaigns overlaid.
- The workbench browser: the agents' tools and notes per application, with their history.
- Notifications when a run ends, fails or an agent asks (browser, e-mail, a webhook).
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
