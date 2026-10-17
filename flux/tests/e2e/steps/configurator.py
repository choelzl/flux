"""The configurator: New loop's ways, each box's settings on the Graph, drafts, the direct editor, the wizard on a phone.

Run through web_ui.py (D1171: the steps of the area `configurator`, tests/areas.py)."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path


def flows(r):
    b = r.b

    def new_loop_tabs():
        r.page("#/configure", "document.querySelector('.tabs')", "the New loop page")
        tabs = b.js("return [...document.querySelectorAll('#main .tabs [role=tab]')].map(t => t.textContent)")
        r.check("New loop combines empty creation and upload", tabs == ["Create or upload", "Configurator", "Agent", "Clone"], str(tabs))
        r.check("New loop has no folder panel (D827)", not b.js("return !!document.querySelector('details.folder-roles')"))
        b.wait("document.querySelector('.cfg .cfg-rail')", what="the configurator")
        r.clean("New loop › Configurator")
        r.button("Agent", "#main .tabs")
        b.wait("document.querySelector('#ag-who')", what="the agent form")
        opts = b.js("return [...document.querySelectorAll('#ag-who option')].map(o => [o.value, o.disabled])")
        ids = [o[0] for o in opts]
        r.check("the agent picker lists the agents installed here, then the model (D807)",
                ids[-1:] == ["model"] and set(ids) <= {"opencode", "claude", "codex", "model"}, str(opts))
        r.check("the Agent tab has its address", b.js("return location.hash") == "#/configure/agent")
        r.clean("New loop › Agent")
        # D719: one name, a calm checklist, and a loop started from an example
        r.page("#/configure?step=prompt", "document.querySelector('.cfg[data-step=prompt]')", "the configurator")
        r.check("the configurator has one name field, no separate application name",
                not b.js("return [...document.querySelectorAll('#main label')].some(l => l.textContent.trim().startsWith('Application name'))"))
        r.check("no command-line next steps", "Next steps" not in r.text())
        steps = b.js("return [...document.querySelectorAll('.cfg-rail button .t')].map(x => x.textContent)")
        r.check("the configurator is six steps (D826, D941)", steps == ["Prompt", "Check & Measure", "Objective", "Graph", "Extra", "Save"], str(steps))
        r.check("the steps down the left, each with its line; its fields label.stack (D1116)",
                b.js("return [...document.querySelectorAll('.cfg-rail button .sum')].every(x => x.textContent.trim()) && !!document.querySelector('.cfg-rail button.on[data-step=prompt]')"
                     " && !!document.querySelector('.cfg-body label.stack')"))
        r.check("the document beside the step, its lines lit (D1116)", b.js("return [...document.querySelectorAll('.cfg-docside pre.cfg-yaml div.lit')].some(d => d.textContent.startsWith('statement'))"))
        r.check("an untouched draft marks no step as wrong", not b.js("return !!document.querySelector('.cfg-rail button.bad')"))
        b.js("document.querySelector('.cfg-nav .cfg-next').click(); return 1")
        b.wait("document.querySelector('.cfg-rail button.on[data-step=checks]')", timeout=10, what="the Check & Measure step")
        r.check("the address follows the step (D1116)", b.js("return location.hash") == "#/configure?step=checks")
        r.check("Check & Measure: the checks, then the measurements (D941)",
                b.js("return [...document.querySelectorAll('.cfg-body h3')].map(x => x.textContent.trim().split(' ')[0])") == ["Checks", "Measurements"])
        b.click(".cfg-rail button[data-step=save]")
        b.wait("document.querySelector('.cfg[data-step=save] .cfg-ready')", timeout=10, what="the Save step")
        r.check("an untouched checklist is to-do, not errors", b.js("return !document.querySelector('.cfg-todo li.t-bad') && !!document.querySelector('.cfg-todo li.t-none')"))
        r.check("the last step shows the document and what is left to do", b.js("return !!document.querySelector('.cfg-yaml-fold pre.cfg-yaml').textContent && !document.querySelector('.cfg-docside')"))
        order = b.js("""const at = (css) => { const e = document.querySelector('.cfg-body ' + css); return e ? [...document.querySelectorAll('.cfg-body *')].indexOf(e) : -1; };
            return [at('.cfg-ready'), at('.cfg-todo'), at('.cfg-nav'), at('.cfg-yaml-fold'), document.querySelector('.cfg-yaml-fold').open]""")
        r.check("Save: where it stands, what is left, the actions, then the document (D941, D1116)",
                0 <= order[0] < order[1] < order[2] < order[3] and order[4] is True, str(order))
        b.click(".cfg-rail button[data-step=graph]")
        r.check("the drawing is its step's", b.wait("document.querySelector('.cfg-body svg.cfg-cycle [data-node=generate]')", timeout=10, what="the drawing") is not None)
        nodes = b.js("return [...document.querySelectorAll('.cfg-cycle [data-node]')].map(n => n.dataset.node).sort()")
        r.check("the Graph: five steps, Setup's four and the record; policy and calibration inside their steps (D1139)", nodes == sorted(
            ["orchestrate", "generate", "test", "measure", "select", "validate", "plan", "knowledge", "feedback", "records"]), str(nodes))
        b.js("document.querySelector('.cfg-cycle [data-node=measure]').dispatchEvent(new MouseEvent('click', {bubbles: true})); return 1")
        r.check("Measure holds the cheap-with-costly comparison", b.wait("document.querySelector('.cfg-panel [role=group][aria-label=\"Compare cheap with costly\"] button.on')",
                timeout=10, what="Measure's comparison") is not None)
        b.js("document.querySelector('.cfg-cycle [data-node=orchestrate]').dispatchEvent(new MouseEvent('click', {bubbles: true})); return 1")
        b.wait("document.querySelector('.cfg-panel [data-fc-field=dse]')", timeout=10, what="Search's policy")
        policy = b.js("const p = document.querySelector('[data-fc-field=dse]'); return [p.value, p.disabled, [...p.options].map(o => o.value), p.getBoundingClientRect().height]")
        r.check("Graph exposes DSE with the adaptive default and only its category's preferences", policy[0] == "none" and not policy[1]
                and policy[3] > 0 and policy[2] == ["none", "adaptive", "explore", "improve", "tune", "finetune", "variations"], str(policy))
        r.check("DSE has three policy categories", b.js("return [...document.querySelector('[data-fc-field=dse-group]').options].map(o => o.value)")
                == ["preferences", "algorithms", "delegated"])
        r.check("New loop has no Example tab (D767)", not b.js("return [...document.querySelectorAll('#main .tabs a, #main .tabs button')].some(t => t.textContent.trim() === 'Example')"))
        r.check("nor a way to make a loop from an example", r.api("/apps/from-example", "POST", {"name": "x", "kind": "sweep"})["status"] in (404, 405))
    r.step("new loop tabs", new_loop_tabs, setup=True)

    def dse_setting():
        import yaml

        made = r.api("/apps/new-empty", "POST", {"name": "dse_setting"})
        r.check("create a separate loop for DSE settings", made["status"] == 200, made["body"])
        doc = r.data / "users/bob/apps/dse_setting/problem.yaml"
        raw = yaml.safe_load(doc.read_text())
        raw["flow"] = {"orchestrate": {"by": "claude", "timeout_s": 321, "dse": "improve"}, "test": {"check": "true"}}
        doc.write_text(yaml.safe_dump(raw, sort_keys=False))

        def box(node):
            b.js("document.querySelector('.cfg-cycle [data-node=' + arguments[0] + ']').dispatchEvent(new MouseEvent('click', {bubbles: true})); return 1", node)
            b.wait(f"document.querySelector('.cfg-cycle [data-node={node}].sel')", timeout=10, what=f"the {node} box opened")

        def graph():
            r.page("#/app/dse_setting/settings/problem?step=graph", "document.querySelector('.cfg[data-step=graph] .cfg-cycle')", "the saved loop's Graph")
            box("orchestrate")
            b.wait("document.querySelector('[data-fc-field=dse]')", what="the DSE dropdown")

        def saved_ok(what):
            r.button("Save to problem.yaml", ".cfg-nav")
            r.dialog_button("Save")
            b.wait("document.querySelector('.cfg-status.t-ok') && !document.querySelector('.cfg-status.t-run')", timeout=20, what=what)

        graph()
        r.check("a step's address opens it: ?step=graph (D1116)", b.js("return !!document.querySelector('.cfg-rail button.on[data-step=graph]')"))
        r.check("the DSE dropdown loads the saved preference", b.js("return document.querySelector('[data-fc-field=dse]').value") == "improve")
        r.check("a saved preference selects its category", b.js("return document.querySelector('[data-fc-field=dse-group]').value") == "preferences")
        r.check("Search says its policy", "improve" in b.js("return document.querySelector('.cfg-cycle [data-node=orchestrate]').textContent"))
        b.js("const p = document.querySelector('[data-fc-field=dse]'); p.value = 'explore'; p.dispatchEvent(new Event('change')); return 1")
        r.check("the drawing follows the dropdown", "explore" in b.js("return document.querySelector('.cfg-cycle [data-node=orchestrate]').textContent"))
        for value in ("explore", "none"):
            b.js("const p = document.querySelector('[data-fc-field=dse]'); p.value = arguments[0]; p.dispatchEvent(new Event('change')); return 1", value)
            saved_ok("the DSE setting saved")
            saved = json.loads(r.api("/apps/dse_setting/document")["body"])
            expected = {"by": "claude", "timeout_s": 321}
            if value != "none":
                expected["dse"] = value
            r.check(f"saving DSE {value} preserves the orchestrator's settings", saved["raw"]["flow"]["orchestrate"] == expected
                    and saved["normal"] is not None, str(saved))
            graph()
            r.check(f"DSE {value} survives reloading the configurator", b.js("return document.querySelector('[data-fc-field=dse]').value") == value)

        def category(value, key="dse-group"):
            b.js("const p = document.querySelector('[data-fc-field=' + arguments[0] + ']'); p.value = arguments[1]; p.dispatchEvent(new Event('change')); return 1", key, value)

        category("algorithms")
        choices = b.js("return [...document.querySelector('[data-fc-field=dse]').options].map(o => o.value)")
        r.check("the algorithm category filters the policy dropdown", choices == ["sweep", "montecarlo", "anneal", "gradient", "genetic", "pareto"], str(choices))
        r.check("algorithms explain guidance without a parameter space", "no search runs" in r.text())
        b.js("const p = document.querySelector('[data-fc-field=dse]'); p.value = 'gradient'; p.dispatchEvent(new Event('change')); return 1")
        saved_ok("algorithm emphasis saved")
        saved = json.loads(r.api("/apps/dse_setting/document")["body"])
        r.check("algorithm emphasis preserves who orchestrates", saved["raw"]["flow"]["orchestrate"] == {"by": "claude", "timeout_s": 321, "dse": "gradient"}
                and saved["normal"] is not None, str(saved))
        graph()
        r.check("a saved algorithm selects its category and policy", b.js("return ['dse-group', 'dse'].map(k => document.querySelector('[data-fc-field=' + k + ']').value)") == ["algorithms", "gradient"])
        category("delegated")
        r.check("changing the category picks its first policy", b.js("return ['dse-group', 'dse'].map(k => document.querySelector('[data-fc-field=' + k + ']').value)") == ["delegated", "model"])
        r.check("model and agent choices have their own category", b.js("return [...document.querySelector('[data-fc-field=dse]').options].every(o => o.value === 'model' || o.value.startsWith('agent:'))"))
        b.js("const p = document.querySelector('[data-fc-field=dse]'); p.value = 'agent:codex'; p.dispatchEvent(new Event('change')); return 1")
        r.check("agent selection explains its setup and separate generation", "Needs the agent installed" in r.text() and "Design chooses who writes" in r.text())
        saved_ok("agent orchestrator saved")
        saved = json.loads(r.api("/apps/dse_setting/document")["body"])
        r.check("without a space the selected agent becomes the orchestrator", saved["raw"]["flow"]["orchestrate"] == {"by": "codex"}
                and saved["normal"] is not None, str(saved))
        graph()
        box("orchestrate")
        r.check("the saved agent is restored in Orchestrate: the choice agent, its menu codex", b.js("return [document.querySelector('.cfg-panel .cfg-who button.on').textContent,"
                " document.querySelector('.cfg-panel .cfg-who-bar select').value]") == ["agent", "codex"])
        b.click(".cfg-rail button[data-step=extra]")
        r.button("+ Setting", ".cfg-body")
        b.type("[aria-label='Setting']", "block")
        b.type("[aria-label='Its values']", "16, 32")
        b.click(".cfg-rail button[data-step=graph]")
        box("orchestrate")
        category("algorithms")
        r.check("adding settings changes the algorithm explanation to a real search", "Runs over the settings in Extra" in r.text())
        r.check("category and policy fields fit the page", b.js("return document.documentElement.scrollWidth <= innerWidth + 1"))
        r.clean("DSE settings")
    r.step("dse setting", dse_setting, setup=True)

    def library_box():
        """D1136: one Library box on the Graph -- what every prompt reads checked from the loop's files and folders, files
        added there and checked, a text, a method sheet, who sums up the papers, the lessons -- saved into flow.knowledge."""
        import yaml

        made = r.api("/apps/new-empty", "POST", {"name": "library_box"})
        r.check("create a loop for the Library", made["status"] == 200, made["body"])
        root = r.data / "users/bob/apps/library_box"
        (root / "rtl").mkdir(exist_ok=True)
        for f in ("rtl/top.sv", "rtl/alu.sv"):
            (root / f).write_text("module m; endmodule\n")
        (root / "golden.py").write_text("print('ok')\n")
        (root / "method.md").write_text("Pipeline first.\n")
        raw = yaml.safe_load((root / "problem.yaml").read_text())
        raw["flow"] = {"test": {"check": "true"}}
        (root / "problem.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
        setv = ("const [css, v] = arguments; const el = document.querySelector(css); el.value = v;"
                " el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true})); return 1")
        pick = ("const [who, what] = arguments; [...document.querySelector('.cfg-panel [role=group][aria-label=\"' + who + '\"]').querySelectorAll('button')]"
                ".find(b => b.textContent.trim() === what).click(); return 1")
        rows = "return [...document.querySelectorAll('.cfg-panel [data-sec=library] .cfg-lib-reads li')].map(l => [l.querySelector('code, span').textContent, l.querySelector('input').checked, l.querySelector('input').disabled])"

        r.page("#/app/library_box/settings/problem?step=graph", "document.querySelector('.cfg[data-step=graph] .cfg-cycle')", "the Graph")
        nodes = b.js("return [...document.querySelectorAll('.cfg-cycle [data-node]')].map(n => n.dataset.node)")
        r.check("one Knowledge box, no Digest or Lessons box (D1136, D1152)", "knowledge" in nodes and "digest" not in nodes and "lessons" not in nodes
                and "Knowledge" in b.js("return document.querySelector('.cfg-cycle [data-node=knowledge]').textContent")
                and b.js("return !!document.querySelector('.cfg-cycle [data-node=knowledge] .svg-pill.ai')"), str(nodes))
        b.js("document.querySelector('.cfg-cycle [data-node=knowledge]').dispatchEvent(new MouseEvent('click', {bubbles: true})); return 1")
        b.wait("document.querySelector('.cfg-panel [data-sec=library] .cfg-lib-reads li code')", timeout=10, what="the Library's files")
        got = b.js(rows)
        r.check("the Library lists the loop's files, a folder as one row, library/ and the shared library read while on",
                ["golden.py", False, False] in got and ["rtl/", False, False] in got and ["library/", True, True] in got
                and ["shared library", True, True] in got and not any(x[0].startswith("rtl/t") for x in got), str(got))
        r.check("the folder says its files", "folder, 2 files" in b.js("return document.querySelector('.cfg-panel [data-sec=library] .cfg-lib-reads').textContent"))
        b.js("[...document.querySelectorAll('.cfg-panel [data-sec=library] .cfg-lib-reads li')].find(l => l.textContent.startsWith('rtl/')).querySelector('input').click(); return 1")
        b.js(setv, ".cfg-panel [data-fc-field=knowledge-text]", "Widths are 8 bits.")
        b.js(setv, ".cfg-panel [data-fc-field=knowledge-sheet]", "method.md")
        b.js(pick, "Who works Digest papers", b.js("return [...document.querySelector('.cfg-panel [role=group][aria-label=\"Who works Digest papers\"]').querySelectorAll('button')].pop().textContent.trim()"))
        digest = b.js("const g = document.querySelector('.cfg-panel [role=group][aria-label=\"Who works Digest papers\"]'), on = g.querySelector('button.on').textContent.trim();"
                      " return on === 'agent' ? g.parentElement.querySelector('select').value : on")
        b.js(pick, "Who works Lessons", "mined")
        added = r.files / "library_box" / "notes.md"            # where the browser may read
        added.parent.mkdir(parents=True, exist_ok=True)
        added.write_text("Notes to read.\n")
        b.attach(".cfg-panel [data-sec=library] input.up-files", str(added))
        r.button("Add 1 file", ".cfg-panel")
        b.wait("[...document.querySelectorAll('.cfg-panel [data-sec=library] .cfg-lib-reads li')].some(l => l.textContent.startsWith('notes.md') && l.querySelector('input').checked)",
               timeout=20, what="the added file, checked")
        r.button("Save to problem.yaml", ".cfg-nav")
        r.dialog_button("Save")
        b.wait("document.querySelector('.cfg-status.t-ok') && !document.querySelector('.cfg-status.t-run')", timeout=20, what="the Library saved")
        saved = json.loads(r.api("/apps/library_box/document")["body"])
        know = (saved["raw"].get("flow") or {}).get("knowledge")
        r.check("the Library saves what is read, the text, the sheet, who digests and the lessons", know == {
            "files": ["rtl/", "notes.md"], "sheet": "method.md", "text": "Widths are 8 bits.", "digest": digest, "lessons": "mined"}
            and digest != "model" and saved["normal"] is not None, str(know))
        r.page("#/app/library_box/settings/problem?step=prompt", "document.querySelector('.cfg[data-step=prompt] .files-panel li')", "Prompt")
        r.check("Prompt › Files says what is read, without a toggle", not b.js("return !!document.querySelector('.cfg-reads')")
                and b.js("return [...document.querySelectorAll('.files-panel li')].some(l => l.textContent.includes('rtl/top.sv') && l.textContent.includes('read'))"))
        r.clean("the Library")
    r.step("library box", library_box, setup=True)

    def knowledge_skills():
        """D1152: the Knowledge box's Skills -- the loop's skills/ and the built-in flux-loop-commands, each with a
        switch, saved as `skills: {off: [...]}`; the whole section off is `skills: off`, in the document the Save step shows."""
        import yaml

        made = r.api("/apps/new-empty", "POST", {"name": "knowledge_skills"})
        r.check("create a loop for the Skills", made["status"] == 200, made["body"])
        root = r.data / "users/bob/apps/knowledge_skills"
        (root / "skills" / "rtl-style").mkdir(parents=True, exist_ok=True)
        (root / "skills" / "rtl-style" / "SKILL.md").write_text("---\nname: rtl-style\ndescription: how we write RTL\n---\nNo latches.\n")
        raw = yaml.safe_load((root / "problem.yaml").read_text())
        raw["flow"] = {"test": {"check": "true"}}
        (root / "problem.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
        skills = "return [...document.querySelectorAll('.cfg-panel [data-sec=skills] li')].map(l => [l.querySelector('code').textContent, l.querySelector('input').checked])"
        pick = ("const [sec, what] = arguments; [...document.querySelector('.cfg-panel [data-sec=' + sec + '] .cfg-bar [role=group]').querySelectorAll('button')]"
                ".find(b => b.textContent.trim() === what).click(); return 1")
        open_box = ("document.querySelector('.cfg-cycle [data-node=knowledge]').dispatchEvent(new MouseEvent('click', {bubbles: true})); return 1")

        def save(what):
            r.button("Save to problem.yaml", ".cfg-nav")
            r.dialog_button("Save")
            b.wait("document.querySelector('.cfg-status.t-ok') && !document.querySelector('.cfg-status.t-run')", timeout=20, what=what)
            return json.loads(r.api("/apps/knowledge_skills/document")["body"])

        r.page("#/app/knowledge_skills/settings/problem?step=graph", "document.querySelector('.cfg[data-step=graph] .cfg-cycle')", "the Graph")
        b.js(open_box)
        b.wait("document.querySelectorAll('.cfg-panel [data-sec=skills] li').length >= 2", timeout=10, what="the skills")
        r.check("Knowledge has five sections, Skills first (D1159: Hidden from AI)", b.js("return [...document.querySelectorAll('.cfg-panel [data-sec]')].map(x => x.dataset.sec)")
                == ["skills", "library", "hidden", "lessons", "workbench"])
        r.check("the loop's skill and the built-in one, both on", b.js(skills) == [["rtl-style", True], ["flux-loop-commands", True]], str(b.js(skills)))
        b.js("[...document.querySelectorAll('.cfg-panel [data-sec=skills] li')].find(l => l.textContent.includes('flux-loop-commands')).querySelector('input').click(); return 1")
        saved = save("one skill off saved")
        r.check("one skill off is `skills: {off: [...]}`", saved["raw"].get("skills") == {"off": ["flux-loop-commands"]} and saved["normal"] is not None,
                str(saved["raw"].get("skills")))
        r.page("#/app/knowledge_skills/settings/problem?step=graph", "document.querySelector('.cfg[data-step=graph] .cfg-cycle')", "the Graph again")
        b.js(open_box)
        b.wait("document.querySelectorAll('.cfg-panel [data-sec=skills] li').length >= 2", timeout=10, what="the skills again")
        r.check("read back: the built-in skill off", b.js(skills) == [["rtl-style", True], ["flux-loop-commands", False]], str(b.js(skills)))
        b.js(pick, "skills", "off")
        r.check("Skills off folds its list", not b.js("return !!document.querySelector('.cfg-panel [data-sec=skills] li')"))
        b.click(".cfg-rail button[data-step=save]")
        b.wait("document.querySelector('.cfg-yaml-fold')", timeout=10, what="the document")
        r.check("the document says `skills: off`", "skills: \"off\"" in b.js("return document.querySelector('.cfg-yaml-fold').textContent")
                or "skills: off" in b.js("return document.querySelector('.cfg-yaml-fold').textContent"))
        saved = save("Skills off saved")
        r.check("saved: `skills: off`", saved["raw"].get("skills") in ("off", False) and saved["normal"] is not None, str(saved["raw"].get("skills")))
        if os.environ.get("FLUX_E2E_SHOTS"):                # the Knowledge panel, every section on: light and dark, a desktop's and a phone's width
            raw = yaml.safe_load((root / "problem.yaml").read_text())
            raw.pop("skills", None)
            raw["flow"]["knowledge"] = {"lessons": "claude", "workbench": "codex"}
            (root / "problem.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
            out = Path(os.environ["FLUX_E2E_SHOTS"])
            out.mkdir(parents=True, exist_ok=True)
            for theme in ("light", "dark"):
                for width in (1280, 390):
                    b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone'; localStorage.setItem('flux-theme', arguments[1]);"
                         "f.style.cssText = `width:${arguments[0]}px;height:800px;border:0`; f.src = '/?know=' + Date.now() + '#/app/knowledge_skills/settings/problem?step=graph';"
                         "document.body.append(f); return 1", width, theme)
                    b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d && d.querySelector('.cfg-cycle [data-node=knowledge]'); })()", timeout=20)
                    b.js("const d = document.getElementById('phone').contentDocument; d.querySelector('.cfg-cycle [data-node=knowledge]')"
                         ".dispatchEvent(new MouseEvent('click', {bubbles: true})); return 1")
                    b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d.querySelectorAll('.cfg-panel [data-sec=skills] li').length >= 2; })()", timeout=20)
                    for part, sec, at in (("top", "", "start"), ("end", " [data-sec=workbench]", "end")):
                        b.js("const w = document.getElementById('phone').contentWindow; w.document.querySelector('.cfg-panel' + arguments[0])"
                             ".scrollIntoView({block: arguments[1]}); if (arguments[1] === 'start') w.scrollBy(0, -64); return 1", sec, at)
                        time.sleep(0.5)
                        (out / f"knowledge-{width}px-{theme}-{part}.png").write_bytes(
                            base64.b64decode(b.cmd("WebDriver:TakeScreenshot", {"id": b.find("#phone"), "full": False})["value"]))
            b.js("localStorage.removeItem('flux-theme'); return 1")
            b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-know={time.time()}#/"})
            b.wait("document.querySelector('#main')", timeout=20)
        r.clean("the Skills")
    r.step("knowledge skills", knowledge_skills, setup=True)

    def agent_settings():
        """D1142: Design's panel sets what its agent may run of the gate while designing (D1137: some checks, how often)
        and, under More, its time limit -- saved into flow.generate and read back into the same fields."""
        import yaml

        made = r.api("/apps/new-empty", "POST", {"name": "agent_settings"})
        r.check("create a loop for an agent's settings", made["status"] == 200, made["body"])
        root = r.data / "users/bob/apps/agent_settings"
        raw = yaml.safe_load((root / "problem.yaml").read_text())
        raw["flow"] = {"generate": "claude", "test": {"test": "true", "lint": "true"}}
        (root / "problem.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
        setv = ("const [css, v] = arguments; const el = document.querySelector(css); el.value = v;"
                " el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true})); return 1")
        checks = "return [...document.querySelectorAll('.cfg-panel [data-probe-check]')].map(i => [i.dataset.probeCheck, i.checked])"
        r.page("#/app/agent_settings/settings/problem?step=graph", "document.querySelector('.cfg-panel [data-probe] [data-probe-check]')", "Design's probe")
        r.check("Design's probe lists the gate's checks, every one on by default", b.js(checks) == [["test", True], ["lint", True]], str(b.js(checks)))
        r.check("an agent's rarer settings are folded under More", b.js("return !document.querySelector('.cfg-panel details.cfg-more').open"))
        b.js("document.querySelector('.cfg-panel [data-probe-check=test]').click(); return 1")
        b.js(setv, ".cfg-panel [data-probe] input[aria-label='Gate runs']", "4")
        b.js("document.querySelector('.cfg-panel details.cfg-more').open = true; return 1")
        b.js(setv, ".cfg-panel details.cfg-more input[aria-label='Time limit, s']", "900")
        if os.environ.get("FLUX_E2E_SHOTS"):
            Path(os.environ["FLUX_E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
            b.js("document.querySelector('.cfg-panel').scrollIntoView(); return 1")
            b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "agent-settings-design.png")
        r.button("Save to problem.yaml", ".cfg-nav")
        r.dialog_button("Save")
        b.wait("document.querySelector('.cfg-status.t-ok') && !document.querySelector('.cfg-status.t-run')", timeout=20, what="the agent's settings saved")
        saved = json.loads(r.api("/apps/agent_settings/document")["body"])
        gen = (saved["raw"].get("flow") or {}).get("generate")
        r.check("the probe and the time limit are saved on Design's agent (D1137, D1142)",
                gen == {"by": "claude", "timeout_s": 900, "probe": {"gate": {"checks": ["lint"], "runs": 4}}} and saved["normal"] is not None, str(gen))
        r.page("#/app/agent_settings/settings/problem?step=graph", "document.querySelector('.cfg-panel [data-probe] [data-probe-check]')", "Design's probe again")
        r.check("read back: the checks, the runs and the time limit", b.js(checks) == [["test", False], ["lint", True]]
                and b.js("return [document.querySelector(\".cfg-panel [data-probe] input[aria-label='Gate runs']\").value,"
                         " document.querySelector(\".cfg-panel details.cfg-more input[aria-label='Time limit, s']\").value]") == ["4", "900"])
        if os.environ.get("FLUX_E2E_SHOTS"):                # read back, More open: light and dark, a desktop's width and a phone's
            out = Path(os.environ["FLUX_E2E_SHOTS"])
            for theme in ("light", "dark"):
                for width in (1280, 390):
                    b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone'; localStorage.setItem('flux-theme', arguments[1]);"
                         "f.style.cssText = `width:${arguments[0]}px;height:900px;border:0`; f.src = '/?agent=' + Date.now() + '#/app/agent_settings/settings/problem?step=graph';"
                         "document.body.append(f); return 1", width, theme)
                    b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d && d.querySelector('.cfg-panel details.cfg-more'); })()", timeout=20)
                    b.js("const d = document.getElementById('phone').contentDocument; d.querySelector('.cfg-panel details.cfg-more').open = true;"
                         " d.querySelector('.cfg-panel').scrollIntoView(); return 1")
                    time.sleep(0.5)
                    (out / f"agent-settings-{width}px-{theme}.png").write_bytes(
                        base64.b64decode(b.cmd("WebDriver:TakeScreenshot", {"id": b.find("#phone"), "full": False})["value"]))
            b.js("localStorage.removeItem('flux-theme'); return 1")
            b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-agent={time.time()}#/"})
            b.wait("document.querySelector('#main')", timeout=20)
        r.clean("an agent's settings")
    r.step("agent settings", agent_settings, setup=True)

    def box_panels():
        """The UI review, waves 2-5 (D1203-K10): every box's panel one head -- its title, then who works it on a row of its
        own, the agent one choice ("agent") with a menu saying which; a step's pills on its second line, its name whole;
        Notes says what it does; script designs ask no repair or prototype; a long statement shown whole; a hidden path
        listed under Hidden only, counted "N hidden"; the preview wrapped; on a phone the Graph a list, Next beside Back
        and Save's lines one text each."""
        import yaml

        made = r.api("/apps/new-empty", "POST", {"name": "box_panels"})
        r.check("create a loop for the box panels", made["status"] == 200, made["body"])
        root = r.data / "users/bob/apps/box_panels"
        (root / "tests").mkdir(exist_ok=True)
        (root / "tests/t.py").write_text("x = 1\n")
        (root / "notes.md").write_text("# notes\n")
        raw = yaml.safe_load((root / "problem.yaml").read_text())
        raw.update({"statement": " ".join(["A long statement that goes on and says what matters, line after line."] * 9), "language": "python",
                    "flow": {"generate": "claude", "test": {"test": "true"}, "orchestrate": {"critic": "model"}, "select": {"critic": "model"},
                             "knowledge": {"hidden": ["tests/"]}}})
        (root / "problem.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
        r.page("#/app/box_panels/settings/problem?step=prompt", "document.querySelector('.cfg [data-fc-field=statement]')", "the Prompt step")
        area = b.js("const t = document.querySelector('[data-fc-field=statement]'); return [t.scrollHeight, t.clientHeight]")
        r.check("a long statement is shown whole, no line cut (configurator-15)", area[0] <= area[1] + 1, str(area))
        yaml_ = b.js("const p = document.querySelector('.cfg-docside pre.cfg-yaml'); return [p.scrollWidth, p.clientWidth]")
        r.check("the preview wraps its lines, no sideways scroll (configurator-32)", yaml_[0] <= yaml_[1] + 1, str(yaml_))
        r.check("the name is said one way (configurator-18)", b.js("return document.querySelector('[data-fc-field=id]').closest('label').querySelector('span').textContent") == "Name")
        b.click(".cfg-rail button[data-step=checks]")
        said = b.wait("document.querySelector('.cfg-critic-said') && document.querySelector('.cfg-critic-said').textContent", timeout=10, what="the critic said")
        r.check("Check & Measure says the critic on passing designs, set in Graph (configurator-29)", said.strip() == "Critic: off · set in Graph"
                and not b.js("return !!document.querySelector('.cfg-body [aria-label=\"The critic on a design that passed\"]')"), said)
        b.js("document.querySelector('.cfg-critic-said button').click(); return 1")
        b.wait("document.querySelector('.cfg[data-step=graph] .cfg-panel .cfg-who-bar[data-who=test]')", timeout=10, what="the Graph's Check box, opened from there")
        b.click(".cfg-rail button[data-step=graph]")
        b.wait("document.querySelector('.cfg-cycle [data-node=generate]')", timeout=10, what="the Graph")
        open_box = "document.querySelector('.cfg-cycle [data-node=' + arguments[0] + ']').dispatchEvent(new MouseEvent('click', {bubbles: true})); return 1"
        heads = {}
        for k in ("orchestrate", "generate", "test", "measure", "select", "validate", "plan", "feedback", "records"):
            b.js(open_box, k)
            b.wait(f"document.querySelector('.cfg-panel .cfg-who-bar[data-who={k}]')", timeout=10, what=f"{k}'s panel")
            heads[k] = b.js("const p = document.querySelector('.cfg-panel'), h = p.querySelector(':scope > .head');"
                            " return [!!h.querySelector('.actions'), h.nextElementSibling && h.nextElementSibling.classList.contains('cfg-who-bar')]")
        r.check("every box: its title, then who works it on its own row (configurator-9)", all(v == [False, True] for v in heads.values()), str(heads))
        b.js(open_box, "generate")
        b.wait("document.querySelector('.cfg-panel .cfg-who-bar[data-who=generate] select')", timeout=10, what="Design's agent menu")
        kinds = b.js("return [...document.querySelectorAll('.cfg-panel .cfg-who-bar .cfg-who button')].map(x => x.textContent)")
        which = b.js("return document.querySelector('.cfg-panel .cfg-who-bar select').value")
        r.check("the agent is one choice, the menu says which (configurator-10)", "agent" in kinds and "claude" not in kinds and which == "claude", f"{kinds} {which}")
        cut = b.js("""return [...document.querySelectorAll('.cfg-cycle g.hit')].map(g => {
            const t = g.querySelector('text.t').getBBox(), pills = [...g.querySelectorAll('.svg-pill rect')].map(x => x.getBBox());
            return pills.some(p => p.x < t.x + t.width && p.y < t.y + t.height && p.y + p.height > t.y) ? g.dataset.node : null; }).filter(Boolean)""")
        r.check("no pill covers a box's name (configurator-1)", not cut, str(cut))
        labels = "return [...document.querySelectorAll('.cfg-panel label.stack > span')].map(x => x.textContent)"
        r.check("a model or agent design asks its repairs and prototype", "Repairs per design" in b.js(labels) and "Prototype first" in b.js(labels), str(b.js(labels)))
        b.js("[...document.querySelectorAll('.cfg-panel .cfg-who-bar .cfg-who button')].find(x => x.textContent === 'script').click(); return 1")
        b.wait("!document.querySelector('.cfg-panel .cfg-who-bar select')", timeout=10, what="script chosen")
        got = b.js(labels)
        r.check("script designs ask no repair or prototype (configurator-8)", not {"Repairs per design", "Prototype first", "Repair by", "Revert after"} & set(got), str(got))
        b.js(open_box, "feedback")
        says = b.wait("document.querySelector('.cfg-panel .cfg-says') && document.querySelector('.cfg-panel .cfg-says').textContent", timeout=10, what="Notes' line")
        r.check("Notes says what it does, its switch on/off (configurator-28)", "Notes" in says
                and b.js("return [...document.querySelectorAll('.cfg-panel .cfg-who button')].map(x => x.textContent)") == ["on", "off"], says)
        b.js(open_box, "knowledge")
        b.wait("document.querySelector('.cfg-panel [data-sec=hidden] .cfg-hidden li')", timeout=10, what="Hidden from AI")
        reads = b.js("return [...document.querySelectorAll('.cfg-panel [data-sec=library] .cfg-lib-reads li code')].map(c => c.textContent)")
        r.check("a hidden path is listed under Hidden only, not as read (configurator-27)", "tests/" not in reads and "notes.md" in reads, str(reads))
        r.check("Hidden from AI counts in words (configurator-27)", b.js("return document.querySelector('.cfg-panel [data-sec=hidden] .cfg-bar').textContent").strip().endswith("1 hidden"))
        # a phone: the Graph a list, Next beside Back, Save's lines one text each (configurator-24)
        b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
             "f.style.cssText = 'width:390px;height:900px;border:0'; f.src = '/?boxes=' + Date.now() + '#/app/box_panels/settings/problem?step=graph'; document.body.append(f); return 1")
        d = "document.getElementById('phone').contentDocument"
        b.wait(f"(() => {{ const d = {d}; return d && d.querySelector('.cfg-cycle-list .cfg-lrow'); }})()", timeout=20, what="the phone's Graph")
        shown = b.js(f"const d = {d}; return [d.querySelector('.cfg-draw > svg.cfg-cycle').getBoundingClientRect().height, d.querySelectorAll('.cfg-cycle-list .cfg-lrow').length]")
        r.check("on a phone the Graph is a list of its ten boxes", shown == [0, 10], str(shown))
        b.js(f"{d}.querySelector('.cfg-cycle-list [data-node=select]').click(); return 1")
        b.wait(f"!!{d}.querySelector('.cfg-panel .cfg-who-bar[data-who=select]')", timeout=10, what="Choose opened from the list")
        nav = b.js(f"const d = {d}, top = s => Math.round(d.querySelector('.cfg-nav ' + s).getBoundingClientRect().top); return [top('button'), top('.cfg-next'), top('.cfg-save')]")
        r.check("Next on the first line, beside Back; the save under them", nav[0] == nav[1] and nav[2] > nav[1], str(nav))
        b.js(f"const m = {d}.querySelector('.cfg-stephead select'); m.value = 'save'; m.dispatchEvent(new Event('change')); return 1")
        b.wait(f"!!{d}.querySelector('.cfg-ready li')", timeout=10, what="Save")
        rows = b.js(f"""return [...{d}.querySelectorAll('.cfg-ready li, .cfg-todo li')].map(li => {{
            const g = li.querySelector('.g').getBoundingClientRect(), l = li.getBoundingClientRect(); return Math.round(g.top - l.top); }})""")
        r.check("Save's lines: each mark beside its words, not on a line of its own", rows and max(rows) < 12, str(rows))
        b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-boxes={time.time()}#/"})
        b.wait("document.querySelector('#main')", timeout=20)
        r.clean("box panels")
    r.step("box panels", box_panels, setup=True)

    def preset_named():
        """D1141: a measurement that runs a preset but lists only some of its numbers (a command of one's own to the
        configurator, so it round-trips) is named by its preset on Check & Measure, not "command"."""
        import yaml

        made = r.api("/apps/new-empty", "POST", {"name": "preset_named"})
        r.check("create a loop that measures with a preset", made["status"] == 200, made["body"])
        root = r.data / "users/bob/apps/preset_named"
        raw = yaml.safe_load((root / "problem.yaml").read_text())
        raw["language"] = "verilog"
        raw["flow"] = {"test": {"check": "true"}, "measure": {"screen": {
            "command": "fluxpreset rtl measure {artifact} --stage synth --clock-ps 333", "metrics": ["fmax_mhz", "area_um2"], "needs": ["yosys"]}}}
        raw["objectives"] = [{"metric": "area_um2", "direction": "minimize"}]
        (root / "problem.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
        r.page("#/app/preset_named/settings/problem?step=checks", "document.querySelector('.cfg[data-step=checks] .cfg-xrow')", "Check & Measure")
        line = b.js("return [...document.querySelectorAll('.cfg-xrow .what')].map(w => w.textContent).find(t => t.startsWith('screen'))")
        r.check("a preset's measurement says its preset (D1141)", bool(line) and "Synthesise and time" in line and "command" not in line, str(line))
        if os.environ.get("FLUX_E2E_SHOTS"):
            b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "preset-named-checks.png")
        r.clean("a preset's measurement")
    r.step("preset named", preset_named, setup=True)

    def kept_read_only():
        """D1136: a part the configurator keeps as written is read-only where it would be edited, with one notice and the way
        to Direct edit -- not fields whose typing the save drops."""
        import yaml

        made = r.api("/apps/new-empty", "POST", {"name": "kept_parts"})
        r.check("create a loop with kept parts", made["status"] == 200, made["body"])
        path = r.data / "users/bob/apps/kept_parts/problem.yaml"
        raw = yaml.safe_load(path.read_text())
        raw["flow"] = {"test": {"check": "true"}, "measure": {"bench": {"command": "true", "metrics": ["t"]}}}
        raw["objectives"] = [{"metric": "t", "direction": "minimize", "tie": 0.05}]
        raw["budget"] = {"steps": 3, "prototype_attempts": 12}
        path.write_text(yaml.safe_dump(raw, sort_keys=False))
        r.page("#/app/kept_parts/settings/problem?step=objective", "document.querySelector('.cfg[data-step=objective] .cfg-kept')", "the kept objectives")
        said = b.js("return document.querySelector('.cfg-body .cfg-kept').innerText")
        r.check("kept objectives: the notice, what is written, no field", "Kept as written: change it in Direct edit." in said and "tie: 0.05" in said
                and not b.js("return !!document.querySelector('.cfg-body input, .cfg-body select:not(.cfg-add)')"), said)
        r.check("its Direct edit is a link to the document", b.js("return document.querySelector('.cfg-kept a').getAttribute('href')").endswith("/settings/problem/edit"))
        b.click(".cfg-rail button[data-step=graph]")
        b.wait("document.querySelector('.cfg[data-step=graph] .cfg-cycle')", timeout=10, what="the Graph")
        b.js("document.querySelector('.cfg-cycle [data-node=records]').dispatchEvent(new MouseEvent('click', {bubbles: true})); return 1")
        b.wait("document.querySelector('.cfg-panel input')", timeout=10, what="the Record box")
        got = b.js("return [document.querySelectorAll('.cfg-body .cfg-kept').length, [...document.querySelectorAll('.cfg-panel input')].every(i => i.disabled)]")
        r.check("a kept budget: one notice on the Graph, its knobs read-only", got == [1, True], str(got))
        b.click(".cfg-rail button[data-step=save]")
        b.wait("document.querySelector('.cfg[data-step=save] .cfg-ready')", timeout=10, what="Save")
        r.button("Save to problem.yaml", ".cfg-nav")
        b.wait("document.querySelector('dialog.dlg[open]') || document.querySelector('.cfg-status.t-ok')", timeout=20, what="the save or its changes")
        if b.js("return !!document.querySelector('dialog.dlg[open]')"):
            r.dialog_button("Save")
        b.wait("document.querySelector('.cfg-status.t-ok') && !document.querySelector('.cfg-status.t-run')", timeout=20, what="the save")
        after = yaml.safe_load(path.read_text())
        r.check("the kept parts are saved as written", after["objectives"] == raw["objectives"] and after["budget"] == raw["budget"], str(after))
        r.clean("kept parts")
    r.step("kept read-only", kept_read_only, setup=True)

    def baseline_setting():
        import yaml

        made = r.api("/apps/new-empty", "POST", {"name": "baseline_setting"})
        r.check("create a loop for baseline settings", made["status"] == 200, made["body"])
        root = r.data / "users/bob/apps/baseline_setting"
        (root / "baseline.py").write_text("answer = 42\n")
        path = root / "problem.yaml"
        raw = yaml.safe_load(path.read_text())
        raw["flow"] = {"test": {"check": "true"}, "measure": {"measure": {"command": "true", "metrics": ["cost", "time_ms"]}}}
        raw["objectives"] = [{"metric": "cost", "direction": "minimize"}]
        path.write_text(yaml.safe_dump(raw, sort_keys=False))

        def extra():
            r.page("#/app/baseline_setting/settings/problem?step=extra", "document.querySelector('.cfg[data-step=extra]')", "the baseline configurator")
            b.wait("document.querySelector('[data-fc-field=baseline-mode]')", what="the baseline setting")

        def setv(key, value):
            b.js("const el = document.querySelector('[data-fc-field=' + arguments[0] + ']'); el.value = arguments[1];"
                 " el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input')); return 1", key, value)

        extra()
        r.check("baseline is off by default in Extra", b.js("return document.querySelector('[data-fc-field=baseline-mode]').value") == "off")
        for source, mode in (("project", "before"), ("file", "only"), ("command", "before"), ("metrics", "before")):
            setv("baseline-mode", mode)
            setv("baseline-source", source)
            expected = True if source == "project" else {"file": "baseline.py", "only": True} if source == "file" else {"command": "{python} {home}/baseline.py {artifact}"}
            if source == "file":
                setv("baseline-file", "baseline.py")
            if source == "command":
                setv("baseline-command", expected["command"])
            if source == "metrics":
                r.button("+ Metric", ".cfg-body")
                setv("baseline-metric-0", "cost")
                setv("baseline-value-0", "0")
                r.button("+ Metric", ".cfg-body")
                setv("baseline-metric-1", "time_ms")
                setv("baseline-value-1", "2.5")
                setv("baseline-stage-1", "measure")
                expected = {"metrics": [{"metric": "cost", "value": 0}, {"metric": "time_ms", "value": 2.5, "stage": "measure"}]}
            r.button("Save to problem.yaml", ".cfg-nav")
            r.dialog_button("Save")
            b.wait("!document.querySelector('.cfg-status.t-run') && document.querySelector('.cfg-status.t-ok')", timeout=20, what="baseline saved")
            saved = json.loads(r.api("/apps/baseline_setting/document")["body"])
            r.check(f"baseline {source} is saved as a valid setting", saved["raw"]["baseline"] == expected and saved["normal"] is not None, str(saved))
            extra()
            got = b.js("return ['baseline-mode', 'baseline-source'].map(k => document.querySelector('[data-fc-field=' + k + ']').value)")
            r.check(f"baseline {source} survives reloading the configurator", got == [mode, source], str(got))
            if source == "metrics":
                r.check("provided baseline values and stages survive reloading", b.js("return ['baseline-metric-0', 'baseline-value-0', 'baseline-stage-0', 'baseline-metric-1', 'baseline-value-1', 'baseline-stage-1'].map(k => document.querySelector('[data-fc-field=' + k + ']').value)") == ["cost", "0", "", "time_ms", "2.5", "measure"])
        r.clean("baseline settings")
    r.step("baseline setting", baseline_setting, setup=True)

    def draft_across_modes():
        """D912: one creation draft across the ways -- the name, the statement and a staged file survive
        Configurator -> Agent -> Configurator; a measurement added names its script in the files at once;
        Review says a missing file apart from the document; a save refused on step 1 says so beside its
        button and focuses the name."""
        setv = ("const [css, v] = arguments; const el = document.querySelector(css); el.value = v;"
                " el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true})); return 1")
        click = "const [css, t] = arguments; [...document.querySelectorAll(css)].find(x => x.textContent.trim().startsWith(t)).click(); return 1"
        r.page("#/configure?step=prompt", "document.querySelector('.cfg[data-step=prompt]')", "the configurator")
        b.js(setv, ".cfg [data-fc-field=id]", "drafty")
        b.js(setv, ".cfg [data-fc-field=statement]", "an adder that is small")
        b.js(setv, ".cfg select[aria-label=Language]", "python")
        b.js(click, ".files-panel button", "New file")
        b.wait("document.querySelector('dialog.dlg[open] .file-edit textarea')", what="the new file's editor")
        b.js(setv, "dialog.dlg[open] .file-edit input", "check.py")
        b.js(setv, "dialog.dlg[open] .file-edit textarea", "print('0 failing')")
        r.dialog_button("Save")
        b.wait("[...document.querySelectorAll('.files-panel li')].some(l => l.textContent.includes('check.py'))", timeout=10, what="the staged file")
        b.click(".cfg-rail button[data-step=checks]")             # D941: Check & Measure
        b.js(click, ".cfg-body button", "+ Measurement")
        b.wait("document.querySelector('.cfg-rail button[data-step=prompt] .sum').textContent.includes('missing')", timeout=10, what="a file missing, said by Prompt")
        b.click(".cfg-rail button[data-step=prompt]")
        named = b.wait("(document.querySelector('.files-panel .notice.t-bad') || {}).textContent", timeout=10, what="the measurement's script named")
        r.check("a measurement added names its script in the files at once (D912)", "bench.py" in named, named)
        b.click(".cfg-rail button[data-step=save]")
        ready = b.wait("(document.querySelector('.cfg-ready') || {}).innerText", timeout=10, what="where it stands")
        r.check("Save says a missing file apart, no 'complete' over it (D912)", "missing bench.py" in ready and "Looks complete" not in r.text(), ready)
        r.button("Agent", "#main .tabs")
        b.wait("document.querySelector('#ag-name')", what="the agent form")
        got = b.js("return [document.querySelector('#ag-name').value, document.querySelector('#ag-ask').value, document.querySelector('#main .attach').innerText]")
        r.check("the agent is told the draft's name, statement and files (D912)", got[0] == "drafty" and got[1] == "an adder that is small" and "check.py" in got[2], str(got))
        r.button("Configurator", "#main .tabs")
        b.wait("document.querySelector('.cfg[data-step=save]')", what="the configurator again, on the draft's step")
        r.check("back in the configurator: the draft and its staged file are kept (D912)",
                b.js("return document.querySelector('.cfg-yaml-fold pre.cfg-yaml').textContent.includes('an adder that is small')")
                and "drafty" in b.js("return document.querySelector('.draft-line').textContent") and "1 file staged" in b.js("return document.querySelector('.draft-line').textContent"))
        b.click(".cfg-rail button[data-step=prompt]")
        b.wait("[...document.querySelectorAll('.files-panel li')].some(l => l.textContent.includes('check.py'))", timeout=10, what="the staged file, still")
        b.js(setv, ".cfg [data-fc-field=id]", "")
        b.click(".cfg-rail button[data-step=objective]")
        b.js(click, ".cfg-nav button", "Create loop")
        said = b.wait("(document.querySelector('.cfg-nav .cfg-status.t-bad') || {}).textContent", timeout=10, what="the refusal beside the button")
        r.check("a create refused says why beside its button, on Prompt, the name focused (D912)",
                "name" in said.lower() and b.js("return document.activeElement && document.activeElement.dataset.fcField === 'id'")
                and b.js("return !!document.querySelector('.cfg[data-step=prompt]') && !!document.querySelector('.cfg-nav .cfg-status.t-bad').offsetParent"), said)
        b.js(click, ".draft-line button", "Discard draft")
        r.dialog_button("Discard")
        b.wait("!document.querySelector('.draft-line button')", timeout=10, what="the draft discarded")
        r.clean("draft across modes")
    r.step("draft across modes", draft_across_modes, setup=True)

    def empty_loop():
        r.page("#/configure/empty", "document.querySelector('#up-name')", "the old Empty loop link")
        r.check("old empty-loop links select the combined form", b.js("return document.querySelector('.config-modes .on').textContent") == "Create or upload")
        paths = [str(r.files / "sw/problem.yaml")]
        b.attach("#main input[type=file]:not([webkitdirectory])", "\n".join(paths))
        r.check("a file added is a row with where it goes (D1129)", b.js("return [...document.querySelectorAll('.up-rows li')].map(l => l.querySelector('.mono').textContent)") == ["problem.yaml"])
        r.button("Remove problem.yaml")
        r.check("removing the files returns to empty-loop creation", b.js("return !document.querySelector('.up-rows li')") and "Without files:" in r.text())
        b.type("#up-name", "blank")
        r.button("Create loop")
        b.wait("location.hash.startsWith('#/app/blank/settings/problem') && document.querySelector('.cfg')", timeout=20, what="the empty loop's configurator")
        files = [f["path"] for f in json.loads(r.api("/apps/blank/files")["body"])]
        r.check("an empty loop is the baseline, opened in its configurator", sorted(files) == ["README.md", "library", "problem.yaml"], str(files))
        r.clean("empty loop")
    r.step("empty loop", empty_loop, setup=True)

    def direct_edit():
        r.page("#/app/sw/settings/problem/edit", "document.querySelector('.editor textarea')", "Direct edit")
        r.check("Direct edit has no file list of its own: it links to Files (review loopwork-7)",
                b.js("""const a = document.querySelector('#main .direct-files a');
                  return !document.querySelector('#main .files-panel') && !!a && a.getAttribute('href') === '#/app/sw/files'"""))
        b.click(".direct-files a")
        b.wait("location.hash === '#/app/sw/files' && document.querySelector('#main .files-card ul.files')", what="the Files tab from Direct edit")
        r.page("#/app/sw/settings/problem/edit", "document.querySelector('.editor textarea')", "Direct edit")
        edit = "const [from, to] = arguments; const t = document.querySelector('.editor textarea'); t.value = t.value.replace(from, to); t.dispatchEvent(new Event('input')); return true;"
        b.js(edit, "timeout_s: 60", "timeout_s: 90")
        r.button("Save")
        b.wait("document.querySelector('dialog.dlg[open] pre.diff')", what="the diff before saving")
        r.check("the diff shows the edited line", b.js("return [...document.querySelectorAll('dialog.dlg[open] .d-add')].some(d => d.textContent.includes('timeout_s: 90'))"))
        r.dialog_button("Save")
        b.wait("!document.querySelector('dialog.dlg[open]')")
        for _ in range(50):                                              # the save may still be on its way
            got = r.api("/apps/sw/file?path=problem.yaml")
            if "timeout_s: 90" in got["body"]:
                break
            time.sleep(0.2)
        r.check("saved", "timeout_s: 90" in got["body"])
        r.check("a document that loads: no refusal shown", not b.js("return !!document.querySelector('#main .notice.t-bad')"))
        # a document the loader refuses: said on saving, not first at Start
        b.js(edit, "statement: >-", "statement: >- broken")
        r.button("Save")
        said = b.wait("document.querySelector('dialog.dlg[open]') && document.querySelector('dialog.dlg[open]').innerText", what="the save dialog")
        r.check("before saving, a document that does not load is said (D757)", "does not load" in said, said[:200])
        r.dialog_button("Save anyway")
        b.wait("document.querySelector('#main .notice.t-bad')", what="the loader's refusal")
        r.check("a refused document is said on saving", "does not load" in b.js("return document.querySelector('#main .notice.t-bad').textContent"))
        b.js("document.querySelectorAll('.toast .x').forEach(x => x.click()); window.__e2e.bad.splice(0); return 1")   # the warning was the point
        b.js(edit, "statement: >- broken", "statement: >-")
        r.button("Save")
        r.dialog_button("Save")
        b.wait("!document.querySelector('#main .notice.t-bad')", what="the refusal gone once fixed")
        r.clean("direct edit")
    r.step("direct edit", direct_edit, setup=True)

    def phone_wizard():
        """D913: on a phone the wizard's step and its first field are on the first screen -- a step menu
        in place of the steps down the left, the ways a menu -- at 390 and 320 pixels, every step, nothing wider
        than the screen, one action bar (D941: six steps; D1116: Save's first is where it stands). FLUX_E2E_SHOTS: each
        step at 390 to that folder, and on a desktop, light and dark."""
        r.login("bob")
        shots = Path(os.environ["FLUX_E2E_SHOTS"]) if os.environ.get("FLUX_E2E_SHOTS") else None
        if shots:
            shots.mkdir(parents=True, exist_ok=True)
        pre = json.loads(r.api("/apps/sw/preflight")["body"])            # D913b: Save says the loop's own check
        expect = ("passed" if pre["ok"] else "failed") if pre["checked"] else "not "
        ids = ["prompt", "checks", "objective", "graph", "extra", "save"]
        for h in ("#/configure", "#/app/sw/settings/problem"):
            for width in (390, 320):
                b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
                     "f.style.cssText = `width:${arguments[0]}px;height:800px;border:0`; f.src = '/' + arguments[1]; document.body.append(f); return 1", width, h)
                b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d && d.querySelector('.cfg-stephead select'); })()",
                       timeout=20, what=f"the wizard of {h} at {width}")
                for step, sid in enumerate(ids):
                    first = ("[...document.getElementById('phone').contentDocument.querySelectorAll('.cfg-body input, .cfg-body select, .cfg-body textarea,"
                             " .cfg-body button, .cfg-body summary, .cfg-body svg .hit, .cfg-body .cfg-ready li')].find(e => e.getBoundingClientRect().height > 0)")
                    b.js("""const [step] = arguments, w = document.getElementById('phone').contentWindow, d = w.document;
                        const m = d.querySelector('.cfg-stephead select'); m.value = step; m.dispatchEvent(new Event('change'));
                        w.scrollTo(0, 0); return 1""", sid)
                    b.wait(f"document.getElementById('phone').contentDocument.querySelector('.cfg-stepof').textContent === 'Step {step + 1} of 6' && {first}",
                           timeout=10, what=f"step {step + 1}")
                    got = b.js(f"""const w = document.getElementById('phone').contentWindow, d = w.document;
                        const head = d.querySelector('.cfg-stephead').getBoundingClientRect();
                        const first = {first};""" + """
                        const f = first ? first.getBoundingClientRect() : null;
                        return [d.querySelector('.cfg-stepof').textContent, Math.round(head.bottom), f ? Math.round(f.bottom) : -1,
                                d.documentElement.scrollWidth, w.innerWidth, d.querySelector('.cfg-rail').offsetParent === null];""", step)
                    ok = got[0] == f"Step {step + 1} of 6" and 0 < got[1] < 800 and 0 < got[2] <= 800 and got[3] <= got[4] + 1 and got[5]
                    r.check(f"phone wizard {h} at {width}px, step {step + 1}: the step and its first field on the first screen", ok, str(got))
                    if not ok and shots:                          # the phone's own screen, to see what pushed the field down
                        (shots / f"phone-wizard-{width}-step{step + 1}-{h.strip('#/').replace('/', '-')}.png").write_bytes(
                            base64.b64decode(b.cmd("WebDriver:TakeScreenshot", {"id": b.find("#phone"), "full": False})["value"]))
                    cut = b.js("""const d = document.getElementById('phone').contentDocument;
                        return [...d.querySelectorAll('.cfg-body label.stack > span')].filter(l => l.offsetParent && l.scrollWidth > l.clientWidth + 1).map(l => l.textContent)""")
                    r.check(f"phone wizard {h} at {width}px, step {step + 1}: no label cut short (D913b)", not cut, str(cut))
                    bars = b.js("return [...document.getElementById('phone').contentDocument.querySelectorAll('.cfg-nav')].filter(e => e.offsetParent).length")
                    r.check(f"phone wizard {h} at {width}px, step {step + 1}: one action bar (D913, D941)", bars == 1, str(bars))
                    if sid == "save" and h != "#/configure":
                        said = b.wait("(() => { const t = (document.getElementById('phone').contentDocument.querySelector('.cfg-ready') || {}).innerText || '';"
                                      " return !t.includes('reading the loop') && t; })()", timeout=10, what="the loop's check on Save")
                        line = " ".join(said.split("Checked", 1)[-1].split())
                        r.check(f"Save's Checked line is the loop's own check at {width}px (D913b)", line.startswith(expect), f"{pre} {said}")
                        b.js("const d = document.getElementById('phone').contentDocument, f = d.querySelector('.cfg-diff-fold'); f.open = true; return 1")
                        diff = b.wait("(() => { const t = (document.getElementById('phone').contentDocument.querySelector('.cfg-diff') || {}).innerText || '';"
                                      " return !t.includes('Reading') && t; })()", timeout=10, what="the changes")
                        r.check(f"Save's folded Changes read the save's preview at {width}px (D941)", "No change" in diff or " added" in diff, diff)
                    if shots and width == 390:
                        el = b.find("#phone")
                        png = b.cmd("WebDriver:TakeScreenshot", {"id": el, "full": False})["value"]
                        (shots / f"wizard-{'new' if h == '#/configure' else 'loop'}-step{step + 1}-390px.png").write_bytes(base64.b64decode(png))
        if shots:                                       # D941, D1116: every step on a desktop, light and dark, a new loop's and a loop's own
            for theme in ("light", "dark"):
                b.js("localStorage.setItem('flux-theme', arguments[0]); return 1", theme)
                for h in ("#/configure", "#/app/sw/settings/problem"):
                    b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
                         "f.style.cssText = 'width:1280px;height:1000px;border:0'; f.src = '/?shots=' + Date.now() + arguments[0]; document.body.append(f); return 1", h)
                    b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d && d.querySelector('.cfg-rail button'); })()", timeout=20)
                    for step, sid in enumerate(ids):
                        b.js("document.getElementById('phone').contentDocument.querySelector('.cfg-rail button[data-step=' + arguments[0] + ']').click(); return 1", sid)
                        time.sleep(0.6)
                        png = b.cmd("WebDriver:TakeScreenshot", {"id": b.find("#phone"), "full": False})["value"]
                        (shots / f"wizard-{'new' if h == '#/configure' else 'loop'}-step{step + 1}-desktop-{theme}.png").write_bytes(base64.b64decode(png))
            b.js("localStorage.removeItem('flux-theme'); return 1")
        b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-phone={time.time()}#/"})
        b.wait("document.querySelector('#main')", timeout=20)
        r.clean("phone wizard")
    r.step("phone wizard", phone_wizard, setup=True)
