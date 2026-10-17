"""Admin: its pages, Maintenance, Insights, permissions and sharing, groups, restarts, Resources, usage, view-as.

Run through web_ui.py (D1171: the steps of the area `admin`, tests/areas.py)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from harness import DOCUMENT, file_api, loop, make_sort_fit, put, WATCH


def flows(r):
    b = r.b
    watch = WATCH
    sort_fit = make_sort_fit(r)

    def admin():
        r.login("ada")
        tabs = ["", "insights", "applications", "resources", "sandbox", "agents", "users", "audit", "models"]
        for t in tabs:
            r.page(f"#/admin{'/' + t if t else ''}", "document.querySelector('#main .tabs')", f"admin {t or 'loops'}")
            b.wait("!document.querySelector('#main .skeleton')", timeout=30, what=f"admin {t or 'loops'} loaded")
            r.clean(f"admin › {t or 'loops'}")
        r.page("#/admin/resources", "document.querySelector('#main .tchart')", "the resources over time")
        r.check("every loop's tokens per second, in and out, over time (D838)", "Tokens in /s" in r.text()
                and "Tokens out /s" in r.text())
        r.check("the resources page has no notes on how sizes and samples are kept",
                "kept for a minute" not in r.text() and "the highest in each step" not in r.text())
        said = b.js("""const svg = document.querySelector('#main .tchart-svg'); if (!svg) return null;
            const b = svg.getBoundingClientRect();
            svg.dispatchEvent(new PointerEvent('pointermove', {clientX: b.left + b.width * 0.6, clientY: b.top + b.height / 2, bubbles: true}));
            const tip = svg.parentNode.querySelector('.tchart-tip'); return tip && !tip.hidden ? tip.textContent : '';""")
        r.check("hovering a chart shows its time and value in a bubble", said is None or bool(said), repr(said))
        heads = b.js("return [...document.querySelectorAll('#main .card')].filter(c => /^(Disk per loop|Sandbox containers)/.test(c.querySelector('h2')?.textContent || '') && c.querySelector('table'))"
                     ".map(c => [c.querySelector('h2').textContent, !!c.querySelector('table.sortable th button.th-sort'), !!c.querySelector('tfoot tr.sum') || !/^Disk/.test(c.querySelector('h2').textContent)])")
        r.check("Resources: the containers and disk per loop sort by a header, the sum stays under the rows (D989)",
                bool(heads) and all(x[1] and x[2] for x in heads), str(heads))
        r.page("#/admin/models", "document.querySelector('.set-tabs')", "the model settings")
        tabs = b.js("return [...document.querySelectorAll('.set-tabs [role=tab]')].map(t => t.textContent.replace(' •', ''))")
        r.check("the model settings have a tab per tool, an agent's where it is installed, no Other (D721, D807, D817)",
                tabs[:1] == ["Flux"] and "Other" not in tabs and tabs[-2:] == ["All agents", "+ Add agent"]
                and set(tabs[1:-3]) <= {"OpenCode", "Claude Code", "Codex"}, str(tabs))
        r.button("All agents", ".set-tabs")
        shown = b.js("return [...document.querySelectorAll('.set-group')].filter(f => f.offsetParent).map(f => f.querySelector('legend').textContent)")
        r.check("a tab shows its own groups only", shown == ["Variables for all runs and agents"], str(shown))
        r.page("#/admin/audit", "document.querySelector('#insights-part select[aria-label=What]')", "the audit")   # D723
        opts = b.js("return [...document.querySelectorAll('#insights-part select[aria-label=What] option')].map(o => [o.value, o.textContent])")
        values = [v for v, _ in opts if v]
        r.check("the audit's What offers groups only (D733)", "Users and sign-in" in values and "Runs" in values
                and "login" not in values and not b.js("return !!document.querySelector('#insights-part select[aria-label=What] optgroup')"), str(opts))
        b.js("const s = document.querySelector('#insights-part select[aria-label=What]'); s.value = 'Users and sign-in'; s.dispatchEvent(new Event('change')); return true;")
        whats = b.js("return [...document.querySelectorAll('#insights-part tbody tr')].map(t => t.children[2].textContent.split(' · ')[0])")
        r.check("a group shows its kinds together", "login" in whats and set(whats) <= {"login", "login refused", "add user", "change user", "change password"}, str(whats))
        b.js("const s = document.querySelector('#insights-part select[aria-label=What]'); s.value = ''; s.dispatchEvent(new Event('change'));"
             "const w = document.querySelector('#insights-part select[aria-label=Who]'); w.value = 'bob'; w.dispatchEvent(new Event('change')); return true;")
        whos = b.js("return [...document.querySelectorAll('#insights-part tbody tr')].map(t => t.children[1].textContent)")
        r.check("the audit narrows to one user", whos and set(whos) == {"bob"}, str(whos))
        r.page("#/admin", "document.querySelector('#main .tabs')", "admin loops")
        b.wait("document.querySelector('#main').innerText.includes('sw')", what="every loop listed")
        r.check("the admin sees bob's loop", "bob" in r.text())
    r.step("admin", admin, setup=True)

    def maintenance_options():
        r.login("ada")
        r.page("#/admin/maintenance", "document.querySelector('.mt-table')", "Admin › Maintenance")
        titles = ["Check database integrity", "Validate loop documents", "Back up server database"]
        for title in titles:
            r.check(f"{title}: available, schedule initially off", b.js("const row = [...document.querySelectorAll('.mt-table tbody tr')].find(r => r.querySelector('strong').textContent === arguments[0]); return row && row.classList.contains('off')", title))
        b.js("[...document.querySelectorAll('.mt-table tbody tr')].find(r => r.textContent.includes('Back up server database')).querySelectorAll('button')[1].click(); return 1")
        b.wait("document.querySelector('dialog[open] .mt-edit')", what="backup options")
        warning = b.js("return document.querySelector('dialog[open]').textContent")
        r.check("backup options say which files are copied and which are excluded", "flux-web.db" in warning and "secret.key" in warning and "not included" in warning)
        b.type("dialog[open] input[data-k=keep_backups]", "2")
        b.js("document.querySelector('dialog[open] input[data-k=keep_backups]').dispatchEvent(new Event('change', {bubbles: true})); return 1")
        b.wait("document.querySelector('dialog[open] [data-state=saved]')", what="backup retention saved")
        cfg = json.loads(r.api("/admin/maintenance")["body"])
        r.check("backup retention autosaves", next(t for t in cfg["tasks"] if t["key"] == "backups")["params"]["keep_backups"] == 2)
        r.dialog_button("Close")
        b.wait("!document.querySelector('dialog[open]')", what="backup options closed")
        b.js("[...document.querySelectorAll('.mt-table tbody tr')].find(r => r.textContent.includes('Back up server database')).querySelector('button').click(); return 1")
        b.wait("[...document.querySelectorAll('.mt-table tbody tr')].some(r => r.textContent.includes('saved backups/server-'))", what="backup result")
        r.check("Run shows the saved backup location", True)
        r.clean("Maintenance")
        r.login("bob")
        r.page("#/app/sw/settings/loop", "document.querySelector('.mt-table')", "Loop maintenance")
        text = r.text()
        r.check("owners can check their loop's database and document, while backups stay admin-only", all(t in text for t in titles[:2]) and titles[2] not in text)
    r.step("maintenance options", maintenance_options, setup=True)

    def insights():
        """D766: Admin › Insights -- the failed start above with its why, the agents' turns, the disk by user."""
        r.login("ada")
        r.page("#/admin/insights", "document.querySelector('#main .subtabs')", "Admin › Insights")
        cards, per = {}, {}
        first = {"Failures": "Failures", "Usage": "Usage", "Endpoints": "Endpoints"}
        for label in ("Failures", "Usage", "Endpoints"):      # D819: a sub-tab each
            r.button(label, "#main .subtabs")
            b.wait(f"[...document.querySelectorAll('#insights-part .card h2')].some(x => x.textContent === '{first[label]}')"
                   " && !document.querySelector('#insights-part .skeleton')", timeout=20, what=label)   # D920: the disk measured apart
            got = b.js("const o = {}; for (const c of document.querySelectorAll('#insights-part .card')) { const h = c.querySelector('h2'); if (h) o[h.textContent] = c.textContent; } return o")
            per[label] = sorted(got)
            cards.update(got)
            if label == "Usage":       # D989: its tables sort by a header, as Users
                sortable = b.js("return [...document.querySelectorAll('#insights-part .card')].filter(c => c.querySelector('table')).every(c => [...c.querySelectorAll('table')].every(t => t.classList.contains('sortable') && t.querySelector('th button.th-sort')))")
                r.check("Insights: Usage's tables sort by a header (D989)", sortable is True, str(sortable))
        r.check("Insights: a sub-tab each, a box or two together (D819)", per == {"Failures": ["Failures"], "Usage": ["Disk now", "Usage"],
                "Endpoints": ["Endpoints", "Network refused"]}, str(per))
        r.check("Insights: the failed start with why it stopped", "bob/broken" in cards.get("Failures", "") and "not on PATH" in cards.get("Failures", ""),
                cards.get("Failures", "")[:300])
        r.check("Insights: the current disk usage, each user", all(u in cards.get("Disk now", "") for u in ("ada", "bob")), cards.get("Disk now", "")[:300])
        b.js("const s = document.querySelector('#main select[aria-label=\"Over the last\"]'); s.value = '30'; s.dispatchEvent(new Event('change')); return 1")
        b.wait("document.querySelector('#main select[aria-label=\"Over the last\"]') && document.querySelector('#main select[aria-label=\"Over the last\"]').value === '30' "
               "&& document.querySelectorAll('#insights-part .card').length >= 2", timeout=15, what="30 days")
        r.check("Insights: over 30 days", True)
        # D850, D859: two refused hosts on record -- so the table is there to sort and to remove from
        now = time.time()
        with open(r.data / "network-refused.jsonl", "a") as fh:
            for host, n in (("few.example", 1), ("many.example", 3)):
                for i in range(n):
                    fh.write(json.dumps({"t": now - 60 * (i + 1), "host": host, "port": 443, "app": "sw", "user": "bob"}) + "\n")
        r.page("#/admin/insights", "document.querySelector('#main .subtabs')", "Admin › Insights")
        r.button("Endpoints", "#main .subtabs")
        b.wait("[...document.querySelectorAll('#insights-part .card h2')].some(x => x.textContent === 'Network refused') && "
               "document.querySelector('#insights-part .card:last-child tbody tr')", timeout=20, what="the refused hosts")
        hosts = "return [...[...document.querySelectorAll('#insights-part .card')].find(c => c.querySelector('h2').textContent === 'Network refused').querySelectorAll('tbody tr')].map(t => t.cells[0].textContent)"
        sort = "[...document.querySelectorAll('#insights-part .th-sort')].find(x => x.textContent.startsWith(arguments[0])).click(); return 1"
        r.check("Insights: refused hosts, most refused first", b.js(hosts)[:2] == ["many.example:443", "few.example:443"], str(b.js(hosts)))
        b.js(sort, "Times")
        r.check("Insights: a column's header sorts it, again the other way (D859)", b.js(hosts)[:2] == ["few.example:443", "many.example:443"], str(b.js(hosts)))
        b.js(sort, "Host")
        r.check("Insights: by host, A to Z first", b.js(hosts)[:2] == ["few.example:443", "many.example:443"], str(b.js(hosts)))
        sort_fit("Insights › Endpoints")
        if os.environ.get("FLUX_E2E_SHOTS"):
            Path(os.environ["FLUX_E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
            b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "insights-sorted-desktop.png")
        b.js(sort, "Host")
        b.js("const c = [...document.querySelectorAll('#insights-part .card')].find(c => c.querySelector('h2').textContent === 'Network refused');"
             " [...c.querySelectorAll('tbody tr')].find(t => t.cells[0].textContent === 'many.example:443').querySelector('button.bin').click(); return 1")
        r.dialog_button("Remove")
        b.wait("!document.querySelector('dialog.dlg[open]')", timeout=10)
        b.wait("![...document.querySelectorAll('#insights-part tbody tr')].some(t => t.cells[0].textContent === 'many.example:443')",
               timeout=10, what="the removed host gone from the page")   # its removal answered, the page drawn again
        left = [n["key"] for n in json.loads(r.api("/admin/insights?days=30")["body"])["network"]]
        r.check("Insights: a removed host leaves the list (D850)", "many.example:443" not in left and "few.example:443" in left, str(left))
        b.js("localStorage.removeItem('flux-insights-net-sort'); localStorage.removeItem('flux-insights-ep-sort'); return 1")
        r.page("#/admin/sandbox", "document.querySelector('#stderr-masks')", "Sandbox, with Hidden output (D896)")
        b.js("document.querySelector('#stderr-masks').scrollIntoView({block: 'center'}); return 1")
        b.js("const t = document.querySelector('#stderr-masks'); t.value = 'stale arg0\\n/^ERROR rmcp/'; t.dispatchEvent(new Event('change')); return 1")
        got = b.wait("(() => { const t = document.querySelector('#stderr-masks'), m = t.closest('.card').querySelector('.save-mark'), c = document.querySelector('.save-check');"
                     "if (!m || m.dataset.state !== 'saved') return false; if (!c) return 'no check';"
                     "const r = t.getBoundingClientRect(), k = c.getBoundingClientRect();"
                     "return k.left >= r.right - 40 && k.right <= r.right && k.bottom <= r.bottom && k.top >= r.bottom - 40 ? 'in its corner' : [r.right, r.bottom, k.left, k.top].join(' '); })()",
                     timeout=10, what="the masks saved")
        r.check("a text area saved shows the check in its corner (D939)", got == "in its corner", str(got))
        if os.environ.get("FLUX_E2E_SHOTS"):
            b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "autosave-check-textarea.png")
        r.check("the admin's stderr masks save as they change (D850)",
                json.loads(r.api("/admin/masks")["body"])["masks"] == ["stale arg0", "/^ERROR rmcp/"])
        r.api("/admin/masks", "PUT", {"masks": []})
        r.clean("Admin › Insights")
    r.step("insights", insights, setup=True)

    def admin_permissions():
        source, kept, dropped = "ui-permission-source", "ui-permission-kept", "ui-permission-dropped"
        mount = r.files / "special-mount"
        mount.mkdir()
        permissions = {"sandbox": False, "raw_network": True, "allow": ["example.com"],
                       "mounts": [{"host": str(mount), "inside": "/mnt/special", "mode": "ro"}]}
        r.login("ada")
        made = r.api("/apps/from-text", "POST", {"name": source, "filename": "problem.yaml", "text": DOCUMENT})
        r.check("admin permissions fixture created", made["status"] == 200, made["body"])
        try:
            saved = r.api(f"/apps/{source}/advanced", "PUT", {**permissions, "memory": "8g", "parallel": True})
            r.check("fixture has mount, sandbox and network overrides", saved["status"] == 200, saved["body"])

            def open_clone(owner=""):
                r.page("#/configure/clone", "document.querySelector('#clone-from')", "clone picker")
                b.js("const s = document.querySelector('#clone-from'); s.value = JSON.stringify([arguments[0], arguments[1]]); s.dispatchEvent(new Event('change', {bubbles: true})); return 1", owner, source)
                r.button("Clone…")
                b.wait("document.querySelector('dialog[open] #clone-to')", what="clone dialog")

            for name, keep in ((dropped, False), (kept, True)):
                open_clone()
                r.check(f"{name}: admin clone asks with default unchecked", b.js("const c = document.querySelector('#clone-keep-permissions'); return c && !c.checked"))
                r.check(f"{name}: permission summary identifies overrides", b.js("const t = document.querySelector('dialog').textContent; return t.includes('/mnt/special') && t.includes('off (host)') && t.includes('example.com') && t.includes('Raw TCP/UDP: on')"))
                b.type("#clone-to", name)
                if keep:
                    b.click("#clone-keep-permissions")
                r.dialog_button("Clone")
                b.wait(f"location.hash === '#/app/{name}' && document.querySelector('#main .tabs')", what="clone created")
                settings = json.loads(r.api(f"/apps/{name}/env")["body"])["advanced"]
                r.check(f"{name}: clone respects permission choice", settings == (permissions if keep else {}), str(settings))

            # A loop with no overrides does not show an irrelevant permission choice.
            r.page(f"#/app/{dropped}/settings", "document.querySelector('#loop-transfer')", "default clone settings")
            b.click("#loop-transfer")
            b.wait("document.querySelector('dialog[open] #loop-transfer-user')", what="default transfer dialog")
            r.check("default loops omit the permission checkbox", b.js("return !document.querySelector('#transfer-keep-permissions')"))
            r.dialog_button("Cancel")

            # Preview fresh overrides, even after the Settings page has already loaded.
            r.api(f"/apps/{dropped}/advanced", "PUT", permissions)
            for name, keep in ((dropped, False), (kept, True)):
                if name != dropped:
                    r.page(f"#/app/{name}/settings", "document.querySelector('#loop-transfer')", "admin ownership settings")
                b.click("#loop-transfer")
                b.wait("document.querySelector('dialog[open] #transfer-keep-permissions')", what="admin transfer permissions")
                r.check(f"{name}: admin transfer asks with default unchecked", b.js("return !document.querySelector('#transfer-keep-permissions').checked"))
                b.js("const s = document.querySelector('#loop-transfer-user'); s.value = 'cy'; s.dispatchEvent(new Event('change', {bubbles: true})); return 1")
                if keep:
                    b.click("#transfer-keep-permissions")
                r.dialog_button("Transfer")
                b.wait(f"location.hash === '#/u/cy/app/{name}/settings' && document.querySelector('#loop-transfer')", timeout=90, what="admin transfer completed",
                       said="JSON.stringify([location.hash, !!document.querySelector('dialog[open]'), [...document.querySelectorAll('.toast')].map(t => t.textContent), (window.__e2e || {}).bad])")
                settings = json.loads(r.api(f"/apps/{name}/env?owner=cy")["body"])["advanced"]
                r.check(f"{name}: transfer respects permission choice", settings == (permissions if keep else {}), str(settings))

            r.api(f"/apps/{source}/shares", "PUT", {"user": "bob", "perm": "watch"})
            r.clean("admin permissions")
            r.login("bob")
            open_clone("ada")
            r.check("regular clones cannot opt into special permissions", b.js("return !document.querySelector('#clone-keep-permissions')"))
            r.dialog_button("Cancel")
            r.clean("regular clone permissions")
        finally:
            for owner, names in (("ada", (source, kept, dropped)), ("cy", (kept, dropped))):
                r.login(owner)
                r.page("#/", "document.querySelector('#main table.list, #main .empty')", "permission fixture cleanup")
                for name in names:
                    if r.api(f"/apps/{name}")["status"] == 200:
                        deleted = r.api(f"/apps/{name}", "DELETE")
                        r.check(f"{owner}/{name}: permission fixture removed", deleted["status"] == 200, deleted["body"])
            r.login("bob")

    r.step("admin permissions", admin_permissions)

    def admin_sharing():
        def login(user):
            # Use a fresh page for each account, closing requests from the preceding session.
            b.go(f"{r.url}/#/login")
            b.cmd("WebDriver:Refresh", {})
            r.login(user)

        with loop(r, "ui-admin-sharing") as name:
            login("ada")
            own = r.api("/apps/from-text", "POST", {"name": name, "filename": "problem.yaml", "text": DOCUMENT})
            r.check("admin's same-named loop created", own["status"] == 200, own["body"])
            try:
                def settings():
                    r.page(f"#/u/bob/app/{name}/settings", "document.querySelector('.loop-sharing #share-user')", "admin sharing on another owner's loop")

                settings()
                r.check("admin sees the owner's shares and can choose recipients", b.js("return ![...document.querySelector('#share-user').options].some(o => o.value === 'bob') && [...document.querySelector('#share-user').options].some(o => o.value === 'cy')"))
                b.js("const s = document.querySelector('#share-user'); s.value = 'cy'; s.dispatchEvent(new Event('change', {bubbles: true})); return 1")
                r.button("Share", ".loop-sharing")
                b.wait("document.querySelector('.loop-sharing select[aria-label=\"What cy may do\"]')", what="watch share saved")
                shared = json.loads(r.api(f"/apps/{name}/shares?owner=bob")["body"])
                r.check("admin shares the selected owner's loop", shared["shares"] == [{"user": "cy", "perm": "watch"}])
                r.check("same-named admin loop keeps its own sharing", json.loads(r.api(f"/apps/{name}/shares")["body"])["shares"] == [])
                r.clean("admin adds a share")

                def shared_settings(permission):
                    login("cy")
                    r.page(f"#/u/bob/app/{name}/settings", "document.querySelector('.loop-sharing')", f"{permission} sharing view")
                    r.check(f"{permission} recipient sees sharing without management controls", b.js("return !document.querySelector('.loop-sharing select, .loop-sharing button') && document.querySelector('.loop-sharing').textContent.includes('cy')"))
                    r.clean(f"{permission} sharing view")

                shared_settings("watch")
                login("ada")
                settings()
                b.js("const s = document.querySelector('.loop-sharing select[aria-label=\"What cy may do\"]'); window.__shareBefore = s; s.value = 'edit'; s.dispatchEvent(new Event('change')); return 1")
                b.wait("!window.__shareBefore.isConnected && document.querySelector('.loop-sharing select[aria-label=\"What cy may do\"]')?.value === 'edit'", what="edit share saved")
                r.check("admin can change another owner's share", json.loads(r.api(f"/apps/{name}/shares?owner=bob")["body"])["shares"] == [{"user": "cy", "perm": "edit"}])
                shared_settings("edit")
                login("ada")
                settings()
                r.button("Remove", ".loop-sharing")
                b.wait("!document.querySelector('.loop-sharing select[aria-label=\"What cy may do\"]') && document.querySelector('#share-user option[value=cy]')", what="share removed")
                r.check("admin can revoke another owner's share", json.loads(r.api(f"/apps/{name}/shares?owner=bob")["body"])["shares"] == [])
                r.clean("admin removes a share")
                login("cy")
                r.check("revoked recipient loses loop access", r.api(f"/apps/{name}?owner=bob")["status"] == 403)
            finally:
                login("ada")
                r.page("#/", "document.querySelector('#main table.list, #main .empty')", "admin sharing fixture cleanup")
                deleted = r.api(f"/apps/{name}", "DELETE")
                r.check("admin sharing fixture removed", deleted["status"] == 200, deleted["body"])

    r.step("admin sharing", admin_sharing)

    def user_groups():
        def login(user):
            b.go(f"{r.url}/#/login")
            b.cmd("WebDriver:Refresh", {})
            r.login(user)

        with loop(r, "ui-group-access") as name:
            login("ada")
            original = {u["name"]: u for u in json.loads(r.api("/users")["body"]) if u["name"] in ("bob", "cy")}
            try:
                r.page("#/admin/users", "document.querySelector('#users-subtabs')", "users and groups administration")
                r.button("Users", "#users-subtabs")
                b.wait("document.querySelector('table.users')", what="users subtab")
                r.check("user controls have a separate subtab", b.js("return document.querySelector('#main .tabs .on').textContent === 'Users' && document.querySelectorAll('#users-subtabs [role=tab]').length === 2 && !document.querySelector('.user-groups')"))
                r.check("Server access controls are absent from Users", b.js("return ![...document.querySelectorAll('table.users th')].some(th => th.textContent === 'Server access') && ![...document.querySelectorAll('#users-part select')].some(s => (s.getAttribute('aria-label') || '').includes('Server access'))"))
                r.button("Groups", "#users-subtabs")
                b.wait("document.querySelector('.user-groups #group-name')", what="groups subtab")
                r.check("groups subtab contains no user table", b.js("return !document.querySelector('table.users') && document.querySelector('#users-subtabs [aria-selected=true]').textContent === 'Groups'"))
                r.check("groups use aligned columns and a labelled creation section", b.js("""return JSON.stringify([...document.querySelectorAll('table.groups thead th')].map(t => t.textContent)) === JSON.stringify(['Group', 'Members', 'Server access', 'Actions'])
                  && document.querySelectorAll('table.groups .tag').length === 1
                  && document.querySelector('table.groups .tag').textContent === 'Server admin'
                  && document.querySelector('.group-create label[for=group-name]')
                  && document.querySelector('.group-create label[for=group-server-access]');"""))
                b.type("#group-name", "E2E Team")
                r.check("new groups require an explicit Server access choice", b.js("return document.querySelector('#group-server-access').value === ''"))
                b.js("const select = document.querySelector('#group-server-access'); select.value = 'server'; select.dispatchEvent(new Event('change')); return 1")
                r.button("Add group", ".user-groups")
                b.wait("document.querySelector('.user-groups button[title=\"Rename E2E Team\"]')", what="group created")
                group = next(g for g in json.loads(r.api("/groups")["body"])["groups"] if g["name"] == "E2E Team")
                r.check("new groups have no members or admin powers", group["members"] == 0 and not group["admin"])
                b.click('.user-groups button[title="Rename E2E Team"]')
                b.wait("document.querySelector('dialog[open] #group-rename')", what="group rename dialog")
                b.type("#group-rename", "E2E Research")
                r.dialog_button("Rename")
                b.wait("document.querySelector('.user-groups button[title=\"Rename E2E Research\"]')", what="group renamed")
                renamed = next(g for g in json.loads(r.api("/groups")["body"])["groups"] if g["name"] == "E2E Research")
                r.check("group rename preserves its ID", renamed["id"] == group["id"])
                r.check("group table keeps names, counts and controls in their columns", b.js("""const row = document.querySelector('table.groups tr[data-group="' + arguments[0] + '"]');
                  return row.cells[0].textContent === 'E2E Research' && row.cells[1].textContent === '0'
                    && row.cells[2].querySelector('select').value === 'server' && row.cells[3].querySelector('button').textContent === 'Rename…';""", group["id"]))
                b.js("window.__longGroup = document.querySelector('table.groups tbody .strong'); window.__groupLabel = window.__longGroup.textContent; window.__longGroup.textContent = 'A'.repeat(60); return 1")
                try:
                    for width in (700, 390, 320):
                        b.cmd("WebDriver:SetWindowRect", {"width": width, "height": 900})
                        r.check(f"groups table and creation controls fit at {width}px with long names", b.js("""return document.documentElement.scrollWidth <= innerWidth + 1
                          && [...document.querySelectorAll('.user-groups select, .user-groups input, .user-groups button')].every(e => e.getBoundingClientRect().right <= innerWidth + 1);"""))
                    r.check("groups table shows column labels on phones", b.js("""const cell = document.querySelector('table.groups tbody tr').cells[2];
                      return cell.dataset.label === 'Server access' && getComputedStyle(cell, '::before').content === '"Server access"';"""))
                finally:
                    b.js("window.__longGroup.textContent = window.__groupLabel; return 1")
                    b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                r.page("#/admin", "document.querySelector('.ctl-grid')", "admin loops")
                r.page("#/admin/users", "document.querySelector('.user-groups #group-name')", "remembered groups subtab")
                r.check("groups selection survives navigation and group updates", b.js("return document.querySelector('#users-subtabs [aria-selected=true]').textContent === 'Groups' && !document.querySelector('table.users')"))
                r.button("Users", "#users-subtabs")
                b.wait("document.querySelector('table.users')", what="member administration")
                for user in ("bob", "cy"):
                    b.js("const s = document.querySelector(`tr[data-user=${arguments[0]}] select[aria-label=\"${arguments[0]}'s group\"]`); window.__groupSelect = s; s.value = String(arguments[1]); s.dispatchEvent(new Event('change')); return 1", user, group["id"])
                    b.wait("!window.__groupSelect.isConnected && document.querySelector('table.users')", what=f"{user} membership saved")
                r.button("Groups", "#users-subtabs")
                b.wait("document.querySelector('.user-groups select[aria-label=\"Server access for E2E Research\"]')", what="group server access")
                r.check("group table shows updated member counts", b.js("return document.querySelector('table.groups tr[data-group=\"' + arguments[0] + '\"]').cells[1].textContent === '2'", group["id"]))
                for value, expected in (("own", "external"), ("server", "internal")):
                    b.js("const select = document.querySelector('.user-groups select[aria-label=\"Server access for E2E Research\"]'); window.__groupAccess = select; select.value = arguments[0]; select.dispatchEvent(new Event('change')); return 1", value)
                    b.wait("!window.__groupAccess.isConnected && document.querySelector('.user-groups select[aria-label=\"Server access for E2E Research\"]')", what="group policy saved")
                    members = {u["name"]: u["credential_mode"] for u in json.loads(r.api("/users")["body"]) if u["name"] in ("bob", "cy")}
                    r.check(f"group {value} access applies to every member", members == {"bob": expected, "cy": expected}, members)
                    r.check(f"group {value} access survives refresh", b.js("return document.querySelector('.user-groups select[aria-label=\"Server access for E2E Research\"]').value") == value)
                r.button("Users", "#users-subtabs")
                b.wait("document.querySelector('table.users')", what="user permissions after group access")
                r.check("user permissions use a compact labelled Select button", b.js("const button = [...document.querySelectorAll('tr[data-user=cy] button')].find(b => b.getAttribute('aria-label') === \"Select cy's permissions\"); return button && button.textContent === 'Select'"))
                r.button("Select", "tr[data-user=cy]")
                b.wait("document.querySelector('dialog[open] input[data-permission=view_others]')", what="member permissions")
                r.check("members start with own-loop permissions only", b.js("return document.querySelector('[data-permission=create_loops]').checked && document.querySelector('[data-permission=run_loops]').checked && !document.querySelector('[data-permission=view_others]').checked && !document.querySelector('[data-permission=edit_others]').checked"))
                b.click("[data-permission=view_others]")
                b.click("[data-permission=create_loops]")
                r.dialog_button("Save")
                b.wait("!document.querySelector('dialog[open]') && !document.querySelector('tr[data-user=cy] button[disabled]')", what="permissions saved")
                saved = next(u for u in json.loads(r.api("/users")["body"]) if u["name"] == "cy")
                r.check("per-user permissions save without changing credentials", saved["permissions"]["view_others"] and not saved["permissions"]["create_loops"] and saved["credential_mode"] == original["cy"]["credential_mode"])
                r.clean("groups administration")
                login("cy")
                r.page("#/", "document.querySelector('.group-loops table')", "group loop discovery")
                r.check("group loops show the owner and authorized source", b.js("return document.querySelector('.group-loops').textContent.includes('bob') && document.querySelector('.group-loops').textContent.includes(arguments[0])", name))
                r.check("accounts without create permission have no New loop button", b.js("return !document.querySelector('#main a[href=\"#/configure\"]')"))
                r.page(f"#/u/bob/app/{name}", "document.querySelector('#main .tabs')", "group viewer")
                r.check("view permission does not show Start, Check or Leave", b.js("return ![...document.querySelectorAll('#main .page-head button')].some(b => /^(Start|Check|Leave)/.test(b.textContent))"))
                r.page(f"#/u/bob/app/{name}/settings", "document.querySelector('.loop-sharing')", "group viewer settings")
                r.check("view-only group members cannot share or transfer", b.js("return !document.querySelector('#share-user, #loop-transfer')"))
                r.clean("group viewer")
                login("ada")
                r.api("/users/cy", "PATCH", {"permissions": {"run_others": True}})
                login("cy")
                r.page(f"#/u/bob/app/{name}", "document.querySelector('#main .tabs')", "group runner")
                r.check("run permission shows Start while editing stays disabled", b.js("return [...document.querySelectorAll('#main .page-head button')].some(b => b.textContent.startsWith('Start'))") and not json.loads(r.api(f"/apps/{name}?owner=bob")["body"])["can_edit"])
                r.clean("group runner")
            finally:
                login("ada")
                for user, settings in original.items():
                    restored = r.api(f"/users/{user}", "PATCH", {"group_id": settings["group_id"], "permissions": settings["permissions"]})
                    r.check(f"{user}: group fixture permissions restored", restored["status"] == 200, restored["body"])

    r.step("user groups", user_groups)

    def admin_restart():
        with loop(r, "ui-admin-restart") as name:
            r.login("ada")
            apps = r.api("/admin/apps")
            base = next(a for a in json.loads(apps["body"]) if a["name"] == name)
            fixtures = [{**base, "running": True, "passes": 5, "options": {"passes": 10, "screen_only": True}},
                        {**base, "owner": "ada", "user": "ada", "name": "unlimited", "running": True, "options": {"passes": None}}]
            controls = json.loads(r.api("/admin/controls")["body"])
            # Only mock process lifecycle responses here. Unit tests launch real stand-in processes.
            b.js("""const [apps, controls] = arguments; window.__restartFetch = window.fetch;
              window.__restartCalls = 0; window.__restartPaused = false; window.__restartEmpty = false; window.__restartFailed = false;
              window.fetch = async (u, o) => {
                const path = new URL(String(u), location.href).pathname;
                let body;
                if (path === '/api/admin/apps') body = window.__restartEmpty ? [] : apps;
                else if (path === '/api/admin/controls') body = {...controls, paused: window.__restartPaused ? 'maintenance' : null};
                else if (path === '/api/admin/restart-all') {
                  window.__restartCalls++;
                  window.__restartOptions=JSON.parse(o.body);
                  body = {scheduled: {'ada/unlimited': {passes: null}}, restarted:{}, skipped: {},
                    failed: window.__restartFailed ? {'bob/ui-admin-restart': 'still stopping; no replacement was launched'} : {}};
                  if (!window.__restartFailed) body.scheduled['bob/ui-admin-restart'] = {passes: 5};
                } else return window.__restartFetch(u, o);
                return new Response(JSON.stringify(body), {status: 200, headers: {'Content-Type': 'application/json'}});
              }; return 1;""", fixtures, controls)
            try:
                def page():
                    r.page("#/admin", "document.querySelector('.ctl-grid')", "admin restart controls")

                page()
                r.button("Restart all", ".ctl-grid")
                b.wait("document.querySelector('dialog.dlg[open]')", what="restart confirmation")
                text = b.js("return document.querySelector('dialog.dlg[open]').innerText")
                r.check("restart confirmation lists remaining finite budget and unlimited settings", "bob/ui-admin-restart: 5 of 10 passes remaining · screen only" in text
                        and "ada/unlimited: run forever" in text, text)
                r.check("restart confirmation explains finishing the pass and retained data", "finish their current pass" in text and "Files, results and logs are kept" in text)
                r.dialog_button("Cancel")
                r.check("canceling restart sends no request", b.js("return window.__restartCalls") == 0)
                r.button("Restart all", ".ctl-grid")
                r.dialog_button("Restart loops")
                b.wait("window.__restartCalls === 1 && [...document.querySelectorAll('.toast')].some(t => t.textContent.includes('2 loops scheduled to restart'))", what="restart result")
                r.check("confirming restart requests the pass boundary and reports scheduling", b.js("return window.__restartOptions.now===false"))
                page()
                b.js("window.__restartFailed = true; return 1")
                r.button("Restart all", ".ctl-grid")
                r.dialog_button("Restart loops")
                b.wait("document.querySelector('dialog.dlg[open]')?.innerText.includes('Restart results')", what="partial restart result")
                r.check("restart failures identify the affected loop", "bob/ui-admin-restart: still stopping" in b.js("return document.querySelector('dialog.dlg[open]').innerText"))
                r.dialog_button("Close")
                b.js("window.__restartPaused = true; return 1")
                page()
                disabled = "return [...document.querySelectorAll('.ctl-grid button')].find(b => b.textContent === 'Restart all').disabled"
                r.check("restart is disabled while starts are paused", b.js(disabled))
                b.js("window.__restartPaused = false; window.__restartEmpty = true; return 1")
                page()
                r.check("restart is disabled without active loops", b.js(disabled))
                r.check("Stop all is disabled too with none running, as Restart all is (review shell-2)",
                        b.js(disabled.replace("'Restart all'", "'Stop all'")) and "Running: 0" in b.js("return document.querySelector('.ctl-grid').innerText"))
                r.clean("admin restart controls")
            finally:
                b.js("window.fetch = window.__restartFetch; return 1")

    r.step("admin restart all", admin_restart)

    def admin_author_containers():
        r.login("ada")
        b.js("""window.__containerFetch = window.fetch; window.fetch = async (u, o) => {
          const response = await window.__containerFetch(u, o);
          if (new URL(String(u), location.href).pathname !== '/api/admin/resources') return response;
          const body = await response.json(); body.error = null; body.engine = 'podman';
          body.containers = ['creating loop', 'revising loop', 'active task', null].map((activity, i) => ({
            name: 'flux-00000' + i, state: 'running', status: 'Up', user: 'bob', loop: 'example',
            activity, orphan: !activity, cpu: 1, mem: 1024, pids: 2}));
          return new Response(JSON.stringify(body), {headers:{'Content-Type':'application/json'}});
        }; return 1;""")
        try:
            r.page("#/admin/resources", "[...document.querySelectorAll('tr')].some(tr => tr.textContent.includes('creating loop'))", "author containers")
            rows = b.js("""return [...document.querySelectorAll('tr')].filter(tr => tr.textContent.includes('flux-00000'))
              .map(tr => ({state:tr.querySelector('.pill').textContent, actions:[...tr.querySelectorAll('button, a.btn')].map(b => b.textContent)}));""")
            r.check("Admin identifies creation, revision and other live agent containers", [row["state"] for row in rows] ==
                    ["creating loop", "revising loop", "active task", "left behind"], rows)
            r.check("active agent containers offer View instead of leftover Kill or after-pass Stop", all(row["actions"] == ["View"] for row in rows[:3]), rows)
            r.check("only a container without an active task offers Kill", rows[3]["actions"] == ["Kill"], rows)
            r.clean("admin author containers")
        finally:
            b.js("window.fetch = window.__containerFetch; return 1")

    r.step("admin author containers", admin_author_containers)

    def admin_token_rates():
        r.login("ada")
        b.js("""window.__rateFetch = window.fetch; window.__rateRequests = []; window.__historyRequests = [];
          window.__rateSmall = false; window.fetch = async (u, o) => {
            const url = new URL(String(u), location.href);
            const response = body => new Response(JSON.stringify(body), {status: 200, headers: {'Content-Type': 'application/json'}});
            if (url.pathname === '/api/admin/history') {
              window.__historyRequests.push(Number(url.searchParams.get('hours')));
              return response({sampling: true, samples: []});
            }
            if (url.pathname !== '/api/admin/token-rate') return window.__rateFetch(u, o);
            const hours = Number(url.searchParams.get('hours')), size = hours * 3600 / 180;
            window.__rateRequests.push(hours);
            return response({hours, bucket_seconds: size,
              samples: [0, 30, 0, 20].map((agent, i) => ({start_t: 1000 + i * size, t: 1000 + (i + 1) * size,
                in_agent: agent, in_model: [0, 10, 0, 5][i], out_agent: window.__rateSmall ? .003 : agent / 10,
                out_model: window.__rateSmall ? .001 : [0, 1, 0, .5][i]}))});
          }; return 1;""")
        try:
            r.page("#/admin/resources", "document.querySelector('.token-rate-note')", "resource token rates")
            r.button("1 h", ".choice")
            b.wait("document.querySelector('.token-rate-note')?.textContent.includes('20s')", what="20-second token averages")
            captions = b.js("return [...document.querySelectorAll('.tchart figcaption')].slice(0, 2).map(e => e.textContent)")
            r.check("input and output use separate series and sum agents plus model", "all 25/s" in captions[0]
                    and "agents 20/s" in captions[0] and "Flux's model 5.0/s" in captions[0]
                    and "all 2.5/s" in captions[1] and "agents 2.0/s" in captions[1], captions)
            paths = b.js("return [...document.querySelectorAll('.tchart-svg path.ln')].map(p => p.getAttribute('d'))")
            r.check("D1085: bucket averages are a smooth curve over every interval, the first included", len(paths) == 6
                    and all(p.startswith("M62.0,") and p.count("C") == 5 and "H" not in p and "V" not in p and "L" not in p for p in paths), paths)
            def hover(fraction):
                return b.js("""const svg = document.querySelector('.tchart-svg'), box = svg.getBoundingClientRect();
                  svg.dispatchEvent(new PointerEvent('pointermove', {clientX: box.left + (62 + 350 * arguments[0]) / 420 * box.width,
                    clientY: box.top + box.height / 2, bubbles: true}));
                  return svg.parentNode.querySelector('.tchart-tip').innerText;""", fraction)
            active = hover(1.9 / 4)
            idle = hover(2.1 / 4)
            r.check("hover reads the containing interval, without bridging into idle time", "all 40/s" in active
                    and "all 0.0/s" in idle and " – " in active, [active, idle])
            r.check("D1085: resource rates explain live and completed turns and cached inputs", "as they go" in r.text()
                    and "when a turn ends" in r.text() and "cached inputs included" in r.text())
            b.js("window.__rateSmall = true; return 1")
            r.button("6 h", ".choice")
            b.wait("document.querySelector('.token-rate-note')?.textContent.includes('2m')", what="two-minute token averages")
            r.check("changing the history range requests and labels the right averaging interval", b.js("return window.__rateRequests").count(6) >= 1)
            small = b.js("return document.querySelectorAll('.tchart figcaption')[1].textContent")
            r.check("small nonzero rates stay visible instead of rounding to zero", "all 0.004/s" in small
                    and "agents 0.003/s" in small and "Flux's model 0.001/s" in small, small)
            r.button("30 d", ".choice")
            b.wait("document.querySelector('.token-rate-note')?.textContent.includes('4h')", what="monthly token averages")
            r.check("30 days requests both machine and token history", b.js("return window.__rateRequests.includes(720) && window.__historyRequests.includes(720)"))
            r.check("the monthly range is selected", b.js("return document.querySelector('.choice button.on')?.textContent") == "30 d")
            r.button("7 d", ".choice")
            b.wait("document.querySelector('.choice button.on')?.textContent === '7 d'", what="return from month to week")
            b.wait("window.__rateRequests.includes(168) && window.__historyRequests.includes(168)", timeout=30, what="both weekly histories requested",
                   said="JSON.stringify([window.__rateRequests, window.__historyRequests])")
            r.check("switching back requests both weekly histories", True)
            r.clean("resource token rate intervals")
        finally:
            b.js("window.fetch = window.__rateFetch; return 1")
            r.page("#/admin", "document.querySelector('.ctl-grid')", "leave resource fixture")

    r.step("admin token rates", admin_token_rates)

    def admin_usage_totals():
        r.login("ada")
        b.js("""window.__usageFetch = window.fetch;
          localStorage.setItem('flux-insights-part', 'usage'); localStorage.setItem('flux-insights-days', '7');
          window.fetch = async (u, o) => {
            const url = new URL(String(u), location.href);
            const response = body => new Response(JSON.stringify(body), {status: 200, headers: {'Content-Type': 'application/json'}});
            if (url.pathname === '/api/admin/insights/disk') return response({at: 1, disk: [
              {user: 'ada', home: 100, loops: 200, count: 2, total: 300, largest: null},
              {user: 'bob', home: 50, loops: 100, count: 1, total: 150, largest: null}]});
            if (url.pathname !== '/api/admin/insights' || url.searchParams.get('part') !== 'usage') return window.__usageFetch(u, o);
            const days = Number(url.searchParams.get('days')), zero = {turns: [0], tokens: [0], cost: [0]};
            const usage = days === 7 ? {bucket: 'day', days: ['2026-10-01', '2026-10-02'], users: {
              ada: {turns: [1, 0], tokens: [100, 0], cost: [0.104, 0]},
              bob: {turns: [0, 2], tokens: [0, 250], cost: [0, 0.104]}}, agents: {
              claude: {turns: [1, 1], tokens: [100, 150], cost: [0.104, 0.052]},
              codex: {turns: [0, 1], tokens: [0, 100], cost: [0, 0.052]}}, top: [
              {user: 'ada', app: 'alpha', turns: 1, tokens: 100, cost: 0.104, seconds: 3},
              {user: 'bob', app: 'beta', turns: 1, tokens: 50, cost: 0.052, seconds: 7}]} :
              {bucket: 'hour', days: ['00:00'], users: days === 1 ? {ada: zero} : {},
                agents: days === 1 ? {claude: zero} : {}, top: []};
            return response({range: {start: 1, end: 2}, usage});
          }; return 1;""")
        try:
            r.page("#/admin/insights", "document.querySelectorAll('#insights-part tfoot.usage-total').length === 4", "usage totals")
            footers = b.js("return [...document.querySelectorAll('#insights-part tfoot.usage-total tr')].map(tr => [...tr.cells].map(c => c.textContent.trim()))")
            r.check("user and agent totals sum raw costs before rounding", footers[:2] == [
                    ["Total", "3", "350", "$0.21", ""], ["Total", "3", "350", "$0.21", ""]], footers)
            r.check("highest-usage loop totals cover only the displayed loops", footers[2] == ["Total shown", "2", "150", "$0.16", "10s"], footers[2])
            r.check("disk totals include home, loop sizes, loop counts and total size", footers[3] == ["Total", "150 B", "300 B (3)", "", "450 B", ""], footers[3])
            r.check("total sparklines sum matching time buckets", b.js("return [...document.querySelectorAll('#insights-part tfoot .spark')].map(s => [...s.children].map(i => i.style.height))")
                    == [["6px", "16px"], ["6px", "16px"]])
            b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
            r.check("usage totals retain column labels and fit a phone", b.js("""const c = document.querySelector('tfoot.usage-total td[data-label="Tokens"]');
              return getComputedStyle(c).textAlign === 'left' && getComputedStyle(c, '::before').content === '"Tokens"'
                && document.documentElement.scrollWidth <= innerWidth + 1;"""))
            b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
            pick = "const s = document.querySelector('#insights-range'); s.value = arguments[0]; s.dispatchEvent(new Event('change')); return 1"
            b.js(pick, "1")
            b.wait("document.querySelectorAll('#insights-part .card:first-child tfoot').length === 2 && document.querySelector('#insights-part tfoot td[data-label=Tokens]')?.textContent === '0'", what="zero-usage range totals")
            r.check("changing the range recomputes zero-valued totals", b.js("return [...document.querySelectorAll('#insights-part .card:first-child tfoot tr')].map(tr => [...tr.cells].map(c => c.textContent.trim()))")
                    == [["Total", "0", "0", "$0.00", ""], ["Total", "0", "0", "$0.00", ""]])
            b.js(pick, "30")
            b.wait("document.querySelector('#insights-part .card:first-child')?.textContent.includes('No turns in this period')", what="empty usage range")
            r.check("an empty range has no stale usage totals", b.js("return document.querySelectorAll('#insights-part .card:first-child tfoot').length") == 0)
            r.clean("admin usage totals")
        finally:
            b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
            b.js("window.fetch = window.__usageFetch; return 1")
            r.page("#/admin", "document.querySelector('.ctl-grid')", "leave usage fixture")

    r.step("admin usage totals", admin_usage_totals)

    def admin_impersonation():
        from flux_web.store import Store

        with loop(r, "ui-view-as") as name:
            put(r, name, "visible.txt", "the user's file")
            r.login("ada")
            for user, password in (("ui-view-disabled", "disabled user secret"), ("ui-view-invited", None)):
                made = r.api("/users", "POST", {"name": user, "password": password})
                if made["status"] != 200:
                    raise AssertionError(made)
            r.api("/users/ui-view-disabled", "PATCH", {"disabled": True})
            b.js("localStorage.setItem('flux-users-part', 'users'); return 1")
            r.page("#/admin/users", "document.querySelector('tr[data-user=bob]')", "admin view-as action")
            r.check("View as is unavailable for self, disabled and invited users", b.js("""const button = name =>
              [...document.querySelectorAll('tr[data-user="' + name + '"] button')].find(b => b.textContent === 'View as');
              return !button('ada') && button('ui-view-disabled').disabled && button('ui-view-invited').disabled;"""))
            r.button("View as", "tr[data-user=bob]")
            b.wait("document.querySelector('#who .me')?.textContent === 'bob' && !document.querySelector('#impersonation').hidden", what="bob's view")
            b.js(watch)
            r.check("the banner identifies the user and read-only mode", b.js("return document.querySelector('#impersonation').textContent.includes('Viewing as bob · Read-only')"))
            b.wait(f"document.querySelector('#main a[href=\"#/app/{name}\"]')", what="viewed user's loop list")
            r.check("viewed user has their loops and no admin navigation", name in r.text()
                    and not b.js("return [...document.querySelectorAll('#nav a')].some(a => a.textContent === 'Admin')"))
            r.check("read-only: no Start, Stop or Restart on the list's rows (review shell-3)",
                    not b.js("return [...document.querySelectorAll('#main table.list tbody button')].some(x => /^(Start|Stop|Restart)$/.test(x.textContent))"))
            r.page(f"#/app/{name}", "document.querySelector('#main .tabs')", "viewed user's loop")
            r.check("read-only: the loop's head offers no Start (review shell-3)",
                    not b.js("return [...document.querySelectorAll('#main .page-head button')].some(x => /^(Start|Stop|Restart)$/.test(x.textContent))"))
            r.page(f"#/app/{name}/files", "document.querySelector('.files-card')", "viewed user's files")
            contents = file_api(r, name, "visible.txt")
            r.check("view-as reads the user's actual files", contents["status"] == 200 and contents["body"] == "the user's file")
            denied = file_api(r, name, "visible.txt", "PUT", "a forbidden edit")
            r.check("view-as API changes are refused", denied["status"] == 403 and "read-only" in denied["body"]
                    and file_api(r, name, "visible.txt")["body"] == "the user's file")
            b.cmd("WebDriver:Refresh", {})
            b.wait("document.querySelector('#who .me')?.textContent === 'bob' && !document.querySelector('#impersonation').hidden", what="view-as after refresh")
            b.js(watch)
            r.check("refresh keeps the view-as account", json.loads(r.api("/me")["body"])["impersonator"] == "ada")
            b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
            r.check("Return to admin stays visible on a phone", b.js("""const box = document.querySelector('#impersonation'), button = box.querySelector('button');
              return button.getBoundingClientRect().height > 0 && box.getBoundingClientRect().right <= innerWidth
                && document.documentElement.scrollWidth <= innerWidth + 1;"""))
            b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
            r.button("Return to admin", "#impersonation")
            b.wait("document.querySelector('#who .me')?.textContent === 'ada' && document.querySelector('tr[data-user=bob]')", what="return to admin")
            b.js(watch)
            r.check("return restores admin navigation and removes the banner", b.js("return document.querySelector('#impersonation').hidden && [...document.querySelectorAll('#nav a')].some(a => a.textContent === 'Admin')"))
            r.button("View as", "tr[data-user=bob]")
            b.wait("document.querySelector('#who .me')?.textContent === 'bob' && !document.querySelector('#impersonation').hidden", what="view-as before revocation")
            store = Store(r.data)
            store.set_user("bob", disabled=True)
            try:
                r.page("#/account", "document.querySelector('form.login') && !document.querySelector('#impersonation').hidden", "disabled viewed account")
                r.check("the return banner survives a disabled viewed account", b.js("return document.querySelector('#impersonation').textContent.includes('Viewing as bob')"))
                r.button("Return to admin", "#impersonation")
                b.wait("document.querySelector('#who .me')?.textContent === 'ada' && document.querySelector('tr[data-user=bob]')", what="return from disabled viewed account")
                b.js(watch)
                r.check("return recovers the original admin without a new login", json.loads(r.api("/me")["body"])["role"] == "admin")
            finally:
                store.set_user("bob", disabled=False)
            audit = json.loads(r.api("/audit")["body"])
            r.check("view-as start and return identify the real admin in audit", {(a["user"], a["action"], a["detail"]) for a in audit if "view as" in a["action"]}
                    >= {("ada", "view as user", "bob"), ("ada", "return from view as", "bob")})
            r.clean("admin impersonation")

    r.step("admin impersonation", admin_impersonation)
