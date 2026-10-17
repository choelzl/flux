"""The frame of every page: dark, a phone's width, screenshots for review by eye (FLUX_E2E_SHOTS).

Run through web_ui.py (D1171: the steps of the area `shell`, tests/areas.py)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from harness import WATCH


def flows(r):
    b = r.b

    def dark():
        r.page("#/", "document.querySelector('button.theme')", "the theme button")
        for _ in range(3):                              # system -> light -> dark, as a user clicks it
            if b.js("return document.documentElement.dataset.theme === 'dark'"):
                break
            b.click("button.theme")
        r.check("the theme button reaches dark", b.js("return document.documentElement.dataset.theme") == "dark")
        b.js("window.__before_reload = 1; location.reload(); return true;")
        b.wait("!window.__before_reload && document.body && document.querySelector('#who button.theme')", what="the page again")
        b.js(WATCH)
        r.check("dark is remembered across a reload", b.js("return document.documentElement.dataset.theme") == "dark")
        bg = b.js("return getComputedStyle(document.body).backgroundColor")
        r.check("dark: the page is dark", bg and sum(int(x) for x in bg[bg.index('(') + 1:bg.index(')')].split(",")[:3]) < 150, bg)
        for h in ("#/", "#/u/bob/app/sw", "#/u/bob/app/sw/results", "#/admin/resources", "#/account"):
            r.page(h, "document.querySelector('#main')", h)
            b.wait("!document.querySelector('#main .skeleton')", timeout=20)
            r.clean(f"dark {h}")
        # D856: the Talk button's text is the theme's ink on its accent, not white on light blue
        r.login("bob")
        b.js("localStorage.setItem('flux-theme', 'dark'); return true;")
        r.page("#/app/sw", "document.querySelector('.ask-fab')", "the Talk button")
        got = b.js("""const s = getComputedStyle(document.querySelector('.ask-fab'));
            const lum = c => { const v = c.match(/\\d+/g).slice(0, 3).map(x => { x = x / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
                               return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
            const [a, b] = [lum(s.color), lum(s.backgroundColor)].sort((x, y) => y - x); return (a + 0.05) / (b + 0.05);""")
        dark_on = b.js("return document.documentElement.dataset.theme === 'dark' || (!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: dark)').matches)")
        r.check("dark: the Talk button reads (contrast 4.5:1 or more)", dark_on and got >= 4.5, f"dark {dark_on}, {got:.2f}:1")
        b.js("localStorage.setItem('flux-theme', 'system'); return true;")
    r.step("dark", dark, setup=True)

    def phone():
        """At a phone's width nothing scrolls sideways (D754). D856: measured in a frame of exactly that
        width -- the browser's own window does not go below 500 pixels, so the check used to run at 500."""
        pages = [("bob", h) for h in ("#/", "#/configure", "#/app/sw", "#/app/sw/live", "#/app/sw/live/log", "#/app/sw/results", "#/app/sw/results/graphs",
                                      "#/app/sw/files", "#/app/sw/settings", "#/account")]
        pages += [("ada", h) for h in ("#/admin", "#/admin/insights", "#/admin/users", "#/admin/sandbox", "#/admin/maintenance", "#/admin/audit", "#/admin/models",
                                       "#/admin/agents")]
        who = None
        for user, h in pages:
            if user != who:
                r.login(user)
                who = user
            if h in ("#/account", "#/admin/agents"):          # D924: on the agent's tab -- its three states and its Test
                b.js("try { localStorage.setItem(arguments[0], 'Codex'); } catch (_) {} return 1", "flux-models-tab-" + ("me" if h == "#/account" else "server"))
            for width in (390, 320):
                b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
                     "f.style.cssText = `width:${arguments[0]}px;height:800px;border:0`; f.src = '/' + arguments[1]; document.body.append(f); return 1", width, h)
                b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d && d.querySelector('#main') "
                       "&& !d.querySelector('#main .skeleton') && d.readyState === 'complete'; })()", timeout=20, what=f"{h} at {width}")
                time.sleep(0.8)
                got = b.js("""const w = document.getElementById('phone').contentWindow, d = w.document;
                    const over = [...d.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect();
                        return r.width > 0 && r.right > w.innerWidth + 1 && w.getComputedStyle(e).position !== 'fixed'
                          && !e.closest('pre, code, .cm-editor, .drawer:not(.open), .scroll-x'); })
                        .filter((e, i, all) => !all.some(p => p !== e && p.contains(e))).slice(0, 3)
                        .map(e => e.tagName.toLowerCase() + '.' + [...e.classList].join('.') + ' ' + Math.round(e.getBoundingClientRect().right));
                    return [d.documentElement.scrollWidth, w.innerWidth, over];""")
                r.check(f"phone {h} at {width}px: nothing wider than the screen", got[0] <= got[1] + 1 and not got[2], f"{got}")
                if h == "#/account":
                    try:                                  # its connections come after the page's first draw
                        b.wait("!!document.getElementById('phone').contentDocument.querySelector('.agent-conn[data-agent=codex]')", timeout=20)
                    except AssertionError:
                        pass
                    seen = b.js("const d = document.getElementById('phone').contentDocument, c = d.querySelector('.agent-conn[data-agent=codex]');"
                                "return c ? [c.getBoundingClientRect().height > 0, [...c.querySelectorAll('dt')].map(x => x.textContent),"
                                " !![...c.querySelectorAll('button')].find(x => x.textContent.trim() === 'Test')] : null")
                    r.check(f"phone {h} at {width}px: the agent's states and Test connection on its tab (D924)",
                            bool(seen) and seen[0] and seen[1] == ["Installation", "Connection", "Verification"] and seen[2], str(seen))
            b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-phone={time.time()}#/"})   # a reload: the page is the app again
            b.wait("document.querySelector('#main')", timeout=20)
            r.clean(f"phone {h}")
    r.step("phone", phone)

    def screens():
        """FLUX_E2E_SHOTS=<dir>: whole-page screenshots of the pages one reviews by eye, to that folder."""
        out = Path(os.environ["FLUX_E2E_SHOTS"])
        out.mkdir(parents=True, exist_ok=True)
        pages = [("ada", "#/admin"), ("ada", "#/admin/agents"), ("ada", "#/admin/agents?OpenCode"), ("ada", "#/admin/resources"), ("ada", "#/admin/sandbox"), ("ada", "#/admin/maintenance"),
                 ("ada", "#/admin/users"), ("bob", "#/account"), ("bob", "#/"), ("bob", "#/app/sw"), ("bob", "#/app/sw/live"), ("bob", "#/app/sw/live/graph"),
                 ("bob", "#/app/sw/live/log"), ("bob", "#/app/sw/results"), ("bob", "#/app/sw/results/ideas"), ("bob", "#/app/sw/agents"),
                 ("bob", "#/app/sw/files"), ("bob", "#/app/sw/settings")]
        who = None
        for user, h in pages:
            if user != who:
                r.login(user)
                who = user
            tab = h.split("?", 1)[1] if "?" in h else None
            r.page(h.split("?")[0], "document.querySelector('#main')", h)
            b.wait("!document.querySelector('#main .skeleton')", timeout=20)
            if tab:
                r.button(tab, ".set-tabs")
                b.js("document.querySelectorAll('#main details').forEach(d => d.open = true); return 1")
            time.sleep(1.0)
            h = h.replace("?", "-")
            b.shot(out / f"{user}-{h.strip('#/').replace('/', '-') or 'loops'}.png", full=True)
            b.js("document.documentElement.dataset.theme = 'dark'; return 1")     # each page in dark too
            time.sleep(0.4)
            b.shot(out / f"{user}-{h.strip('#/').replace('/', '-') or 'loops'}-dark.png", full=True)
            b.js("delete document.documentElement.dataset.theme; return 1")
        for h in ("#/configure?step=graph", "#/app/sw/settings/problem?step=graph", "#/app/sw/settings/problem?step=extra",
                  "#/app/sw/settings/problem?step=prompt", "#/app/sw/settings/problem?step=checks", "#/app/sw/settings/problem?step=objective",
                  "#/app/sw/settings/problem?step=save"):   # the configurator
            if who != "bob":
                r.login("bob")
                who = "bob"
            r.page(h, "document.querySelector('.cfg .cfg-body')", h)
            time.sleep(1.0)
            name = f"bob-{h.strip('#/').replace('/', '-').replace('?', '-').replace('=', '-')}"
            b.shot(out / f"{name}.png", full=True)
            if True:                                                        # D1139: the Graph in dark too, every step
                b.js("document.documentElement.dataset.theme = 'dark'; return 1")
                time.sleep(0.5)
                b.shot(out / f"{name}-dark.png", full=True)
                b.js("delete document.documentElement.dataset.theme; return 1")
        r.page("#/configure/upload", "document.querySelector('#up-name')", "Create or upload")    # D1129: the rows, where each goes
        b.attach("#main input.up-files", "\n".join(str(f) for f in sorted((r.files / "sw").iterdir()) if f.is_file()))
        time.sleep(1.0)
        b.shot(out / "bob-configure-upload.png", full=True)
        r.check(f"screenshots in {out}", True)
    if os.environ.get("FLUX_E2E_SHOTS"):
        r.step("screens", screens)
