# Files and settings

## Files

A loop's **Files** tab lists its folder: the document, your scripts, `library/`, and what Flux
writes (`out/`, `runs/`). Open a file to view, edit, save or download it; **Raw** and
**Fullscreen** show the whole of it. Drop files or folders onto the list, or use **Add files**
and **Add folder**.

![Files: the loop's folder beside the open document](../assets/web/files-light.webp#only-light)
![Files: the loop's folder beside the open document](../assets/web/files-dark.webp#only-dark)

- **Actions** on a file or folder: **Rename…**, **Move…**, **Delete…**, and **Hide from AI**
  (or **Show to AI**), which adds the path to `knowledge.hidden`
  ([Knowledge, skills, hidden paths](../guide/knowledge.md#hidden-from-ai)).
- The loop's `.gitignore` files hide what they ignore; **show ignored** lists them greyed.
- **Workbench** lists the agents' own folder: the tools and notes they keep across runs.

## Settings

A loop's **Settings** has two parts, **Preferences** and **Problem**.

### Preferences

- **Measurements:** per metric, whether it is shown, whether it is a main one (in lists and
  summaries), and whether it shows as change from the baseline. Charts and the decision always
  use every measurement.
- **Environment variables:** the loop's own, over your account's and the server's.
- **Sharing:** share the loop with another user to **watch** (runs, log, results, files) or
  **edit** (also the files, the document, start and stop). An editor's runs use the owner's
  record, model settings and limits.
- **Name & ownership:** rename the loop (1 to 60 letters, digits, `_` and `-`), or transfer it
  to another user. The loop must be stopped.
- **Advanced (admins):** whether the loop runs in the sandbox, its memory, CPU and process
  limits, parallel work, extra hosts it may reach, raw TCP/UDP, host folders mounted inside,
  and extra Nixpkgs or Nixchip packages. Everyone else sees what they are.
- **Maintenance:** run a clean-up on this loop now, and **Rerun baseline**, which has the next
  start measure pass 0 again.
- **Reset or delete:** **Reset** clears what the runs generated (you choose what to keep: the
  workbench, results and history, caches); the document, your files and the settings stay.
  **Delete** removes the whole loop. Both need the loop stopped and cannot be undone.

### Problem

- **Configurator:** the document in six steps, as the [Loop crafter](../guide/loop-crafter.md)
  shows it. A save shows its diff first; what the form cannot express is kept as written and
  listed on Save.
- **Direct edit:** the YAML as written, saved with its diff. A document that does not load is
  said before it is saved.
- **Agent:** say what should change; an agent revises a copy of the loop's files and shows its
  diff (not while the loop runs).
- **Check** runs `flux task check` on the document as it stands.
