# Server and admin

## Run a server

```bash
flux user add ada --admin       # the first account, on the server's machine
flux serve                      # http://127.0.0.1:8765/
```

| Option | Effect |
|---|---|
| `--host HOST`, `--port PORT` | Where it listens (default `127.0.0.1:8765`); put a TLS proxy in front for others |
| `--secure-cookie` | Send the session cookie over HTTPS only, behind a TLS proxy |
| `--data DIR` | The server's data (default `$XDG_DATA_HOME/flux/web`): accounts, keys, every user's loops |
| `--max-running N` | Runs at once per user |
| `--no-sandbox` | Runs on the host, not sandboxed: for a single trusted user only |

`flux user add` and `flux serve` must name the same data folder; both print it. Run them as the
user who should own the loops' files.

- **Data:** the data folder holds `flux-web.db`, `secret.key` (which encrypts keys and
  secrets) and every user's loops under `users/<name>/apps/`. Back up `flux-web.db` and
  `secret.key` together (**Admin › Maintenance** can) and the loop folders separately.
- **Limits:** a loop holds up to 100,000 files and 8 GB; one upload, 900 files and 256 MB.
- **Restarts:** a restarted server finds its running loops again; their containers keep
  running while it is down. `scripts/install-service.py` (from `flux/`) installs it as a user
  service that restarts only the server on updates.
- **Slow requests** (over 0.5 s; `FLUX_SLOW_S` sets it) are logged as `flux serve: slow: ...`.

## Accounts

`flux user` manages accounts from the server's machine:

```bash
flux user add bob --invite --url https://flux.example   # a link for bob to choose a password
flux user list
flux user role bob external
flux user link bob                                      # a password reset link
```

A link works once, for a week; there is no mail. In the app, **Admin › Users** does the same,
with each user's group, permissions and running limit, and **View as** to see the app as they
do (read-only).

- **Groups:** every user is in one. **Admin** administers the server; **Internal** users run on
  the server's model and agent settings beneath their own; **External** users bring their own
  settings and agent logins.
- **Permissions**, per user: create loops; run loops and agents; view, ask about, edit, run or
  share other members' loops. Reset, delete, rename and transfer stay with the owner and the
  admins.

## Your account

**Account** holds your **Agents** (each agent's login, **Test**, model and variables), your
usage, your password, desktop notifications, and the **Overview layout** used on every loop.

An agent is used only once its **Test** passed for whoever starts the loop. A login runs the
agent's own login command in the sandbox, in a small terminal; Claude Code's token is stored
encrypted and never shown again. An agent never runs on Flux's model: without settings of its
own, it uses its own configuration and login ([Models and agents](../guide/models.md)).

## Admin

![Admin › Agents: Flux's own model and each agent's settings](../assets/web/admin-agents-light.webp#only-light)
![Admin › Agents: Flux's own model and each agent's settings](../assets/web/admin-agents-dark.webp#only-dark)

| Tab | What it holds |
|---|---|
| **Loops** | Every user's loops; pause new starts, stop or restart them all after their pass; documents of an earlier form, with **Migrate all** |
| **Insights** | Failed starts and agent Tests, usage and cost by user and agent, endpoints, refused hosts, the audit trail |
| **Applications** | The bundled loops; **Use** copies one as your loop |
| **Resources** | CPUs, memory and disks over time, the sandbox containers, each loop's disk and caches |
| **Maintenance** | Scheduled clean-up tasks, each on or off, with **Run now** |
| **Sandbox** | The network (open, or an allowlist), the PATH directories mounted, what every user's home starts with |
| **Agents** | Flux's own model and each agent: program, login, model and key, seconds per turn, token prices; **+ Add agent** |
| **Users** | Users, groups and permissions, invitations, **View as** |

Keys are stored encrypted and never shown again. With nothing set, runs use the machine's own
configuration (`~/.config/flux/flux.env`, and each agent's own).

### Maintenance

The scheduled tasks clean scratch and leftover containers, compact the databases, condense
run logs, prune old records and agent folders, alert when a disk fills, and back up the
server's database. **Bring task journals up to date** converts journals written by an older
Flux, so Live can read them. Running loops are never touched, and every run is audited.

### Sandbox

Every run is sandboxed unless a loop's **Settings › Preferences › Advanced (admins)** says
otherwise. The network is open, or an allowlist of hosts, domains and address ranges; HTTP(S)
goes through the host's proxy, and raw TCP/UDP is a per-loop choice. The command line's
equivalents are `FLUX_SANDBOX_NET=allowlist`, `FLUX_SANDBOX_ALLOW` and
`FLUX_SANDBOX_RAW_NETWORK=1` ([Sandbox](../guide/run.md#sandbox)).

### Environment variables

Variables apply in order: the server's (**Admin › Agents › All agents**), the user's
(**Account**), the loop's (**Settings › Preferences**). Secrets are stored encrypted. The
sandbox's own variables, `PATH`, `HOME` and the model settings cannot be set this way.
