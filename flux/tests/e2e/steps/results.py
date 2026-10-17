"""Results: the tables, labels, rejected results, the graphs, the ideas notebook, the keyboard alone.

Run through web_ui.py (D1171: the steps of the area `results`, tests/areas.py)."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from harness import CHARTS, GRAPHS_RESULTS, loop, make_sort_fit, WATCH


def flows(r):
    b = r.b
    watch = WATCH
    sort_fit = make_sort_fit(r)


    def compact_tables():
        r.preferences(reset=True)
        fixture = json.loads(json.dumps(GRAPHS_RESULTS))
        metrics = ["area_um2", "fmax_mhz", "time_ms", "power_w", "long_measurement_name_for_latency", "another_long_measurement_for_throughput"]
        fixture["metrics"] = metrics
        stage = "long_measurement_stage_with_confirmation"
        fixture["stages"] = [stage]
        for i, d in enumerate(fixture["designs"]):
            d["name"] += "-a-long-generated-design-name-with-implementation-details"
            d["numbers"].update(dict.fromkeys(metrics[2:], i + 0.25))
            d["shown"], d["stages"] = stage, {stage: d["numbers"]}
            d["meets"]["fmax_mhz"] = d["numbers"]["fmax_mhz"] >= 1000
        fixture["invalidated"] = [{"id": 7, "at": "2026-10-12T10:00:00+00:00", "reason": "the bench was misconfigured", "by": "bob",
                                   "names": ["w-old"], "trials": 3, "restored": False}]          # D1001
        b.js("window.__compactFetch = window.fetch.bind(window); const fixture = arguments[0]; localStorage.setItem('flux-results-compact', 'false'); localStorage.removeItem('flux-results-relative'); localStorage.removeItem('flux-results:[\"bob\",\"bob\",\"sw\"]');"
             " localStorage.setItem('flux-results:[\"bob\",\"bob\",\"sw\"]', JSON.stringify({hidden: [JSON.stringify(['', fixture.designs[0].base, fixture.designs[0].key])]}));"
             " window.fetch = (u, o) => String(u) === '/api/apps/sw' ? window.__compactFetch(u, o).then(async r => { const info = await r.json();"
             " return new Response(JSON.stringify({...info, state: {...info.state, last_active: 1000}}), {headers: {'Content-Type': 'application/json'}}); })"
             " : String(u).startsWith('/api/apps/sw/state') ? window.__compactFetch(u, o).then(async r =>"
             " new Response(JSON.stringify({...await r.json(), last_active: 1000}), {headers: {'Content-Type': 'application/json'}}))"
             " : String(u).startsWith('/api/apps/sw/results')"
             " ? Promise.resolve(new Response(JSON.stringify(fixture), {headers: {'Content-Type': 'application/json'}}))"
             " : window.__compactFetch(u, o); return 1", fixture)
        try:
            r.page("#/app/sw/results", "document.querySelector('table.designs th.measurement-head')", "wide results table")
            r.check("D1001, D1007: designs invalidated by hand are listed under the decision with why, Re-evaluate and Restore",
                    b.js("const l = document.querySelector('.invalidated-list'); return !!l && l.textContent.includes('Invalidated by hand (1)') && l.textContent.includes('the bench was misconfigured') && ['Re-evaluate', 'Restore'].every(t => [...l.querySelectorAll('button')].some(x => x.textContent === t))"))
            r.check("old row-hiding preferences are ignored and every design remains visible", b.js("return document.querySelectorAll('table.designs tbody tr').length === 7 && !document.querySelector('.hide-design, .hidden-rows')"))
            r.check("measurement headers are angled and keep their full labels", b.js("const th = document.querySelector('th[data-label=long_measurement_name_for_latency]'); return getComputedStyle(th.querySelector('.measurement-label')).transform !== 'none' && th.title.includes('long_measurement_name_for_latency')"))
            # D1032: a label turns about the middle of its line's end, which sits on the column's right edge -- where its numbers end
            got = b.js("""const th = document.querySelector('th[data-label=long_measurement_name_for_latency]'), l = th.querySelector('.measurement-label'),
                  head = th.querySelector('.measurement-heading');
                  const col = [...th.parentElement.children].indexOf(th), cell = document.querySelector('table.designs tbody tr').children[col];
                  const cs = getComputedStyle(cell), numberEnd = cell.getBoundingClientRect().right - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth);
                  const text = l.querySelector('.measurement-text') || l, range = document.createRange(); range.selectNodeContents(text);
                  return [Math.round(range.getBoundingClientRect().right - numberEnd), Math.round(head.getBoundingClientRect().right - numberEnd)];""")
            # D1032, D1033: the rendered text's rightmost point -- its last letter's top corner -- on the numbers' right edge
            r.check("a diagonal header's text ends where its column's numbers end", abs(got[0]) <= 1 and abs(got[1]) <= 1, str(got))
            r.check("long diagonal labels are shortened without losing their endings or accessible names", b.js("const th = document.querySelector('th[data-label=another_long_measurement_for_throughput]'), label = th.querySelector('button'); return label.textContent.length <= 20 && label.textContent.endsWith('throughput') && label.getAttribute('aria-label') === 'another_long_measurement_for_throughput' && document.querySelector('table.designs thead').getBoundingClientRect().height < 160"))
            b.click("table.designs tbody input[type=checkbox]")
            r.check("results are always compact regardless of the old preference, with no toggle", b.js("const t = document.querySelector('table.designs'), name = t.querySelector('.table-design-name'); return !document.querySelector('.compact-table') && getComputedStyle(name).maxWidth !== 'none' && t.querySelectorAll('th').length === 9 && t.getBoundingClientRect().width <= t.parentElement.clientWidth + 1"))
            r.check("status badges remain accessible; units and metadata stay in tooltips", b.js("const badge = document.querySelector('table.designs .verdict-badge'), name = document.querySelector('table.designs .table-design-name').closest('td'); return badge.getAttribute('aria-label') === 'accepted' && !!badge.querySelector('.g-ok') && name.title.includes('long_measurement_stage') && name.title.includes('2026-10-06') && document.querySelector('table.designs td.num').title.includes('µm²')"))
            r.check("measurement cells are plain values without checkmarks or repeated units", b.js("return [...document.querySelectorAll('table.designs td.num')].every(td => /^[-+0-9.e]+$/.test(td.textContent.trim())) && !document.querySelector('table.designs .measurement-verdict, table.designs .measurement-unit')"))
            r.check("passing and failing measurements use distinct colors and explicit tooltip status", b.js("const good = document.querySelector('table.designs td.meets'), bad = document.querySelector('table.designs td.misses'); return getComputedStyle(good).color !== getComputedStyle(bad).color && good.title.includes('meets the limit') && bad.title.includes('misses the limit')"))
            b.click(".bar .relative-values")
            relative = b.js("const rows = [...document.querySelectorAll('table.designs tbody tr')]; const row = n => rows.find(x => x.innerText.includes(n));"
                            " const cell = (n, i) => row(n).querySelectorAll('td.num')[i]; return [cell('w-good', 0).innerText.trim(), cell('w-good', 0).title,"
                            " cell('w-good', 1).innerText.trim(), cell('dec-1', 0).innerText.trim(), cell('dec-1', 0).title,"
                            " cell('w-baseline', 0).innerText.trim(), document.querySelector('table.designs tbody input').checked]")
            r.check("relative uses baseline first and excludes failed designs from percentile references",
                    relative[0] == "-22.5%" and "baseline (w-baseline" in relative[1] and "31" in relative[1]
                    and relative[2] == "+9.1%" and relative[3] == "—" and "no baseline or accepted designs" in relative[4]
                    and relative[5] == "0%" and relative[6], str(relative))
            r.check("relative percentile references ignore pending designs and describe their accepted population", b.js("const row = [...document.querySelectorAll('table.designs tbody tr')].find(r => r.innerText.includes('enc-22')); const cells = row.querySelectorAll('td.num'); return cells[0].innerText === '-20%' && cells[0].title.includes('P10 10') && cells[0].title.includes('1 accepted design)') && cells[1].innerText === '-26.7%' && cells[1].title.includes('P90 1500')"))
            b.click(".bar .relative-values")
            r.check("absolute restores original measurements", b.js("return document.querySelector('table.designs tbody td.num').innerText.trim() === '31'"))
            alignment = b.js("return [...document.querySelectorAll('table.designs th.measurement-head')].map(th => { const end = th.querySelector('.th-sort').getBoundingClientRect(), col = th.getBoundingClientRect();"
                             " return [th.dataset.label, end.right - (col.right - parseFloat(getComputedStyle(th).paddingRight)), end.bottom - document.querySelector('table.designs tbody').getBoundingClientRect().top]; })")
            r.check("angled labels end above their own measurement columns", all(-2 <= delta <= 15 and below < -3 for _, delta, below in alignment), str(alignment))
            edges = b.js("const area = document.querySelector('table.designs').parentElement, box = area.getBoundingClientRect(); return [...area.querySelectorAll('.measurement-label')].map(x => { const r = x.getBoundingClientRect(); return [r.left - box.left, box.right - r.right]; })")
            r.check("diagonal headers fit inside the scroll area's edges", all(left >= 0 and right >= 0 for left, right in edges), str(edges))
            b.shot(r.shots / "compact-results.png", full=True)
            sort_fit("compact result headers")
            keyboard = b.js("const button = document.querySelector('th[data-label=fmax_mhz] button.th-sort'); button.focus(); return document.querySelector('th[data-label=fmax_mhz]').getAttribute('aria-sort')")
            b.cmd("WebDriver:ElementSendKeys", {"id": b.find('th[data-label=fmax_mhz] button.th-sort'), "text": "\ue007"})
            r.check("measurement header keyboard sorting keeps focus on its label", b.js("return document.querySelector('th[data-label=fmax_mhz]').getAttribute('aria-sort')") != keyboard
                    and b.js("return document.activeElement.dataset.key") == "fmax_mhz")
            r.check("upright measurement arrows still sort when clicked", b.js("const th = document.querySelector('th[data-label=area_um2]'), before = th.getAttribute('aria-sort'); th.querySelector('.measurement-sort-arrow').click(); return document.querySelector('th[data-label=area_um2]').getAttribute('aria-sort') !== before"))
            b.shot(r.shots / "compact-results-sorted.png", full=True)
            r.page("#/app/sw/settings", "document.querySelector('.measurement-options')", "measurement Preferences")
            b.click('.measurement-options input[data-metric="area_um2"]')
            r.page("#/app/sw/results", "document.querySelector('table.designs')", "hidden measurement")
            r.check("hiding a metric removes its header and cells, preserving every design", b.js("return document.querySelectorAll('table.designs tbody tr').length === 7 && document.querySelectorAll('table.designs th.measurement-head').length === 5 && !document.querySelector('th[data-label=area_um2]') && [...document.querySelectorAll('table.designs tbody tr')].every(tr => tr.cells.length === 8) && !document.querySelector('.hide-design') && document.querySelector('.show-hidden-columns').textContent === 'Hidden 1'"))
            r.check("hiding the sorted measurement restores the default design order", b.js("return !document.querySelector('table.designs th[aria-sort=ascending], table.designs th[aria-sort=descending]') && document.querySelector('table.designs tbody tr').innerText.includes('w-good')"))
            r.page("#/", "document.querySelector('#main table.list, #main .empty')", "loops before restoring columns")
            r.page("#/app/sw/results", "document.querySelector('table.designs')", "remembered measurement visibility")
            r.check("hidden measurements remain hidden after reopening Results", b.js("return document.querySelectorAll('table.designs tbody tr').length === 7 && document.querySelectorAll('table.designs th.measurement-head').length === 5 && !document.querySelector('th[data-label=area_um2]') && !document.querySelector('.show-hidden-columns').hidden"))
            separate = b.ajs("const done = arguments[arguments.length - 1]; import('/static/results/preferences.js').then(({resultPreferences}) => done([{name: 'other'}, {name: 'sw', owner: 'another-user'}].map(ctx => (resultPreferences(ctx).read().hiddenMetrics || []).includes('area_um2')))).catch(e => done(String(e))); ")
            r.check("measurement visibility is isolated by loop and owner", separate == [False, False], str(separate))
            r.page("#/app/sw/settings", "document.querySelector('.measurement-options')", "restore a measurement")
            r.check("hidden measurements can be restored individually", b.js("const box = document.querySelector('.measurement-options input[data-metric=area_um2]'), wasHidden = !box.checked; box.click(); return wasHidden"))
            r.page("#/app/sw/results", "document.querySelector('table.designs')", "restored columns")
            r.check("restoring a measurement updates Results", b.js("return document.querySelectorAll('table.designs th.measurement-head').length === 6 && document.querySelector('.show-hidden-columns').hidden"))
            r.page("#/app/sw/settings", "document.querySelector('.measurement-options')", "hide all measurements")
            b.js("for (const box of document.querySelectorAll('.measurement-options input[data-metric]')) if (box.checked) box.click(); return 1")
            r.page("#/app/sw/results", "document.querySelector('table.designs')", "all measurements hidden")
            r.check("hiding every metric keeps the Design and Status columns and all rows", b.js("return document.querySelectorAll('table.designs th').length === 3 && document.querySelectorAll('table.designs tbody tr').length === 7 && document.querySelector('.show-hidden-columns').textContent === 'Hidden 6'"))
            b.click(".show-hidden-columns")
            r.preferences()
            r.check("Hidden is a short toggle that reveals ignored columns without selecting them", b.js("return document.querySelectorAll('table.designs th.hidden-measurement').length === 6 && document.querySelector('.show-hidden-columns').textContent === 'Hidden 6' && document.querySelector('.show-hidden-columns').getAttribute('aria-pressed') === 'true' && window.__savedResults.hiddenMetrics.length === 6"))
            b.click(".show-hidden-columns")
            r.check("toggling Hidden off hides the ignored columns again", b.js("return !document.querySelector('table.designs th.measurement-head') && document.querySelector('.show-hidden-columns').getAttribute('aria-pressed') === 'false'"))
            r.page("#/app/sw/settings", "document.querySelector('.measurement-options')", "restore all measurements")
            b.js("for (const box of document.querySelectorAll('.measurement-options input[data-metric]')) if (!box.checked) box.click(); return 1")
            r.page("#/app/sw/results", "document.querySelector('table.designs')", "all measurements restored")
            r.preferences()
            r.check("selecting all metrics restores columns permanently and hides the empty toggle", b.js("return document.querySelectorAll('table.designs th.measurement-head').length === 6 && document.querySelector('.show-hidden-columns').hidden && window.__savedResults.hiddenMetrics.length === 0"))
            b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
            r.page("#/app/sw/settings", "document.querySelector('.measurement-options')", "mobile measurement Preferences")
            r.check("the measurement picker fits the phone width even with long metric names", b.js("const p = document.querySelector('.measurement-options'), b = p.getBoundingClientRect(); return b.width > 0 && b.left >= 0 && b.right <= innerWidth && p.scrollWidth <= p.clientWidth + 1"))
            b.shot(r.shots / "measurement-picker-mobile.png", full=True)
            r.page("#/app/sw/results", "document.querySelector('table.designs')", "mobile Results")
            r.check("small screens retain aligned measurement columns in a scroll area", b.js("const t = document.querySelector('table.designs'), area = t.parentElement; return getComputedStyle(t).display === 'table' && getComputedStyle(t.tHead).display === 'table-header-group' && getComputedStyle(t.rows[1].cells[0]).display === 'table-cell' && getComputedStyle(area).overflowX === 'auto' && area.clientWidth < innerWidth && area.scrollWidth > area.clientWidth"))
            sort_fit("compact result headers at 390px")
            r.check("the last diagonal header remains visible when scrolled to the right", b.js("const area = document.querySelector('table.designs').parentElement; area.scrollLeft = area.scrollWidth; const label = area.querySelector('th:last-child .measurement-label').getBoundingClientRect(); return label.right <= area.getBoundingClientRect().right && label.left >= area.getBoundingClientRect().left"))
            b.js("document.querySelector('table.designs').parentElement.scrollLeft = 0; return 1")
            b.shot(r.shots / "compact-results-mobile.png", full=True)
            b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
            b.click(".bar .relative-values")
            r.page("#/app/sw", "document.querySelector('.best-n')", "Decision's best designs")
            r.check("Decision is always compact, with a separate status column", b.js("return !document.querySelector('.compact-table') && !!document.querySelector('.best-n .measurement-label') && document.querySelector('.best-n .table-design-name').scrollWidth > document.querySelector('.best-n .table-design-name').clientWidth && document.querySelector('.best-n td.status-column').querySelector('.verdict-badge') && document.querySelector('.best-n .table-design-name').closest('td').title.includes('long_measurement_stage')"))
            r.check("Decision retains every measurement column with readable shortened headers", b.js("const table = document.querySelector('.best-n'); return table.querySelectorAll('th.measurement-head').length === 6 && [...table.querySelectorAll('th.measurement-head')].some(th => th.title === 'another_long_measurement_for_throughput' && th.textContent.endsWith('throughput'))"))
            r.check("Decision shares relative preference and uses the complete measured population", b.js("return document.querySelector('.best-table-head .relative-values').getAttribute('aria-pressed') === 'true' && [...document.querySelectorAll('.best-n td.num')].some(td => td.textContent === '-22.5%' && td.title.includes('baseline (w-baseline'))"))
            b.shot(r.shots / "compact-decision.png", full=True)
            got = b.js("return [...document.querySelectorAll('.best-group')].map(g => [g.querySelector('h3').textContent, g.querySelector('table.best-n').dataset.part || '',"
                       " [...g.querySelectorAll('tbody tr')].map(tr => tr.querySelector('.table-design-name').textContent)])")
            r.check("Best ranks the whole designs, the decision first, then each part's own best under its name (review results-23)",
                    [x[:2] for x in got] == [["Best 3 · whole", ""], ["Best 2 · decoder", "decoder"], ["Best 2 · encoder", "encoder"]]
                    and got[0][2][0].startswith("w-good") and all(not n.startswith(("dec", "enc")) for n in got[0][2]), str(got))
            r.check("Decision measurement cells use colors and contain no extra checkmarks", b.js("const good = document.querySelector('.best-n td.meets'), badge = document.querySelector('.best-n .verdict-badge.t-ok'); return getComputedStyle(good).color === getComputedStyle(badge).color && good.title.includes('meets the limit') && [...document.querySelectorAll('.best-n td.num, .num-cell')].every(td => !/[✓✗]/.test(td.textContent))"))
            r.page("#/app/sw/settings", "document.querySelector('.measurement-options')", "Decision measurement Preferences")
            b.click('.measurement-options input[data-metric="long_measurement_name_for_latency"]')
            r.page("#/app/sw", "document.querySelector('.best-n')", "Decision with an ignored column")
            r.check("Decision can hide measurement columns while retaining its designs", b.js("return document.querySelectorAll('.best-n:not([data-part]) th.measurement-head').length === 5 && document.querySelectorAll('.best-n:not([data-part]) tbody tr').length === 3 && ![...document.querySelectorAll('.best-n:not([data-part]) th.measurement-head')].some(th => th.title === 'long_measurement_name_for_latency')"))
            r.page("#/app/sw/results", "document.querySelector('table.designs')", "measurement choices shared with Results")
            r.check("Decision's hidden measurements also apply to Results", b.js("return document.querySelectorAll('table.designs th.measurement-head').length === 5 && !document.querySelector('th[data-label=long_measurement_name_for_latency]') && document.querySelectorAll('table.designs tbody tr').length === 7"))
            b.click(".show-hidden-columns")
            r.page("#/app/sw", "document.querySelector('.best-n')", "the shared Hidden toggle")
            r.check("the Hidden toggle is shared with Decision and keeps ignored columns dimmed", b.js("return document.querySelectorAll('.best-n:not([data-part]) th.measurement-head').length === 6 && document.querySelectorAll('.best-n:not([data-part]) th.hidden-measurement').length === 1 && document.querySelector('.show-hidden-columns').getAttribute('aria-pressed') === 'true'"))
            b.click(".show-hidden-columns")
            r.preferences()
            r.check("Decision can hide ignored columns again without clearing their selection", b.js("return document.querySelectorAll('.best-n:not([data-part]) th.measurement-head').length === 5 && window.__savedResults.hiddenMetrics.includes('long_measurement_name_for_latency')"))
            r.clean("compact tables")
        finally:
            b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
            r.preferences(reset=True)
            b.js("window.fetch = window.__compactFetch; delete window.__compactFetch; localStorage.removeItem('flux-results-compact'); localStorage.removeItem('flux-results-relative'); localStorage.removeItem('flux-results:[\"bob\",\"bob\",\"sw\"]'); return 1")
    r.step("compact tables", compact_tables, setup=True)

    def design_labels():
        r.preferences(reset=True)
        fixture = json.loads(json.dumps(GRAPHS_RESULTS))
        for i, d in enumerate(fixture["designs"]):
            prefix = d["part"] or "sw-a-very-long-application-name"
            d["name"] = d["base"] = f"{prefix}#{'baseline' if d.get('baseline') else i + 42}"
        single = {**fixture, "designs": [d for d in fixture["designs"] if not d["part"]], "total": 3,
                  "counts": {"accepted": 2, "pending": 0, "failed": 1}}
        b.js("window.__labelFetch = window.fetch.bind(window); window.__labelFixture = arguments[0];"
             " localStorage.removeItem('flux-results:[\"bob\",\"bob\",\"sw\"]');"
             " window.fetch = (u, o) => String(u) === '/api/apps/sw' ? window.__labelFetch(u, o).then(async r => { const info = await r.json();"
             " return new Response(JSON.stringify({...info, state: {...info.state, last_active: 1000}}), {headers: {'Content-Type': 'application/json'}}); })"
             " : String(u).startsWith('/api/apps/sw/results') ? Promise.resolve(new Response(JSON.stringify(window.__labelFixture), {headers: {'Content-Type': 'application/json'}}))"
             " : String(u).startsWith('/api/apps/sw/design?') ? (window.__labelRequest = String(u), Promise.resolve(new Response('{\"artifact\": null}', {headers: {'Content-Type': 'application/json'}})))"
             " : window.__labelFetch(u, o); return 1", single)
        try:
            r.page("#/app/sw/results", "document.querySelector('table.designs')", "short design IDs")
            r.check("single-design tables omit the app name and display complete IDs", b.js("return [...document.querySelectorAll('table.designs .table-design-name')].map(s => s.textContent).join() === '#42,#43,#baseline' && [...document.querySelectorAll('table.designs .design-id')].every(s => getComputedStyle(s).maxWidth === 'none' && s.scrollWidth <= s.clientWidth + 1)"))
            r.check("short design labels preserve full tooltips and accessible names", b.js("const b = document.querySelector('table.designs .open-design'); return b.title.includes('sw-a-very-long-application-name#42') && b.getAttribute('aria-label') === b.title && b.closest('td').title.includes('sw-a-very-long-application-name#42')"))
            b.click("table.designs .open-design")
            b.wait("document.querySelector('.detail-head h2')", what="the original design opened")
            r.check("opening a short ID still requests and displays the original design", b.js("return new URL(window.__labelRequest, location.origin).searchParams.get('design') === 'sw-a-very-long-application-name#42' && document.querySelector('.detail-head h2').textContent === 'sw-a-very-long-application-name#42'"))
            r.page("#/", "document.querySelector('#main table.list, #main .empty')", "home before part labels")
            b.js("window.__labelFixture = arguments[0]; return 1", fixture)
            r.page("#/app/sw/results", "document.querySelector('table.designs')", "short labels with parts")
            r.check("mixed designs show part#ID without brackets or repeated part subtitles", b.js("return [...document.querySelectorAll('table.designs .table-design-name')].map(s => s.textContent).join() === 'whole#42,whole#43,decoder#44,decoder#45,encoder#46,encoder#47,whole#baseline' && !document.querySelector('table.designs .table-part')"))
            r.check("Design sorting orders the displayed parts and IDs", b.js("document.querySelector('th[data-label=Design] button').click(); return [...document.querySelectorAll('table.designs .table-design-name')].map(s => s.textContent).slice(0, 2).join() === 'decoder#44,decoder#45'"))
            r.page("#/app/sw", "document.querySelector('.best-n')", "short Decision table labels")
            r.check("Decision and Results share the same complete design labels", b.js("return [...document.querySelectorAll('.best-n .table-design-name')].every(s => /^(whole|decoder|encoder)#/.test(s.textContent) && !s.textContent.includes('[') && s.scrollWidth <= s.clientWidth + 1 && s.title.includes('#'))"))
            b.shot(r.shots / "short-design-labels.png", full=True)
            got = b.ajs("""const done = arguments[arguments.length - 1]; import('/static/results/preferences.js').then(({designLabels}) => {
                const labels = ds => [...designLabels(ds, 'app').values()];
                done([labels([{name:'app#9', group:'app'}, {name:'app#baseline', group:'whole'}]),
                  labels([{name:'part#2', part:'part', group:'part'}]),
                  labels([{name:'decoder#2', group:'decoder'}]),
                  labels([{name:'alpha#2', group:'alpha'}, {name:'beta#2', group:'beta'}]),
                  labels([{base:'app#9', name:'app#9·abcdef', key:'abcdef0'}, {base:'app#9', name:'app#9·abcdef', key:'abcdef1'}]),
                  labels([{name:'custom name'}])]); }).catch(e => done(String(e)));""")
            r.check("labels handle inferred groups, baseline, custom names and colliding ID hashes",
                    got == [["#9", "#baseline"], ["part#2"], ["decoder#2"], ["alpha#2", "beta#2"], ["#9·abcdef0", "#9·abcdef1"], ["custom name"]], str(got))
            r.clean("design labels")
        finally:
            b.js("window.fetch = window.__labelFetch; delete window.__labelFetch; delete window.__labelFixture; delete window.__labelRequest; localStorage.removeItem('flux-results:[\"bob\",\"bob\",\"sw\"]'); return 1")
    r.step("design labels", design_labels, setup=True)

    def rejected_results():
        """D1043: a rejected result stays in Results, marked, its cleared value struck; Reject… asks
        per measurement whether to measure it again or clear it."""
        fixture = json.loads(json.dumps(GRAPHS_RESULTS))
        bad = next(d for d in fixture["designs"] if d["name"] == "w-bad")
        bad.update(verdict="rejected", eligible=False, rejected={"id": 7, "at": "2026-10-13T10:00:00Z", "why": "the bench read the wrong clock", "by": "bob", "again": []},
                   cleared=[{"stage": "confirm", "metric": "area_um2", "value": 12.5}])
        del bad["stages"]["confirm"]["area_um2"]
        fixture["counts"]["rejected"] = 1
        b.js("window.__rejFetch = window.fetch.bind(window); window.__rejFixture = arguments[0]; window.__rejSent = null;"
             " localStorage.removeItem('flux-results:[\"bob\",\"bob\",\"sw\"]');"
             " window.fetch = (u, o) => String(u).startsWith('/api/apps/sw/results/reject') ? (window.__rejSent = JSON.parse(o.body), Promise.resolve(new Response('{\"ok\": \"w-good rejected\", \"remeasure\": false}', {headers: {'Content-Type': 'application/json'}})))"
             " : String(u).startsWith('/api/apps/sw/results') ? Promise.resolve(new Response(JSON.stringify(window.__rejFixture), {headers: {'Content-Type': 'application/json'}}))"
             " : String(u).startsWith('/api/apps/sw/design?') ? Promise.resolve(new Response('{\"artifact\": null}', {headers: {'Content-Type': 'application/json'}}))"
             " : window.__rejFetch(u, o); return 1", fixture)
        try:
            r.page("#/app/sw/results", "document.querySelector('table.designs')", "rejected results")
            r.check("a rejected result stays in the table, marked, its cleared value struck through", b.js(
                "const tr = [...document.querySelectorAll('table.designs tbody tr')].find(t => t.textContent.includes('w-bad'));"
                " return !!tr && tr.classList.contains('rejected') && !!tr.querySelector('.verdict-badge.t-warn') && tr.querySelector('td.cleared s')?.textContent === '12.5'"
                " && [...document.querySelectorAll('.bar .choice button')].some(c => c.textContent === 'Rejected 1')"))
            b.js("[...document.querySelectorAll('table.designs tbody tr')].find(t => t.textContent.includes('w-bad')).querySelector('.open-design').click(); return 1")
            b.wait("document.querySelector('.rejected-blk')", what="the rejection in the detail")
            r.check("its detail says why, by whom, what was cleared, and offers Restore -- not Reject", b.js(
                "const k = document.querySelector('.rejected-blk'); return k.textContent.includes('the bench read the wrong clock') && k.textContent.includes('bob')"
                " && k.textContent.includes('cleared confirm area_um2') && [...k.querySelectorAll('button')].some(x => x.textContent === 'Restore')"
                " && !document.querySelector('.reject-design') && document.querySelector('.detail-head .pill.t-warn')?.textContent === 'rejected · bob'"))
            b.js("[...document.querySelectorAll('table.designs tbody tr')].find(t => t.textContent.includes('w-good')).querySelector('.open-design').click(); return 1")
            b.wait("document.querySelector('.reject-design')", what="Reject on an accepted design")
            b.click(".reject-design")
            b.wait("document.querySelector('dialog[open] .reject-form')", what="the reject dialog")
            r.check("the dialog lists each measurement with measure again or clear, and Also hide at rest", b.js(
                "const d = document.querySelector('dialog[open]'); return d.querySelectorAll('.reject-measurements tbody tr').length === 2"
                " && [...d.querySelectorAll('.reject-how option')].map(o => o.value).slice(0, 2).join() === 'again,clear' && !d.querySelector('.reject-hide').disabled"))
            b.js("const d = document.querySelector('dialog[open]'), r = d.querySelector('.reject-reason'); r.value = 'area read from the wrong corner'; r.dispatchEvent(new Event('input', {bubbles: true}));"
                 " const row = [...d.querySelectorAll('.reject-measurements tbody tr')].find(t => t.textContent.includes('area_um2'));"
                 " row.querySelector('input').click(); const s = row.querySelector('select'); s.value = 'clear'; s.dispatchEvent(new Event('change', {bubbles: true}));"
                 " d.querySelector('.dlg-actions .primary').click(); return 1")
            b.wait("window.__rejSent", what="the rejection sent")
            r.check("Reject sends why, the value to clear and nothing to measure again", b.js("return JSON.stringify(window.__rejSent)") ==
                    json.dumps({"key": "k-w-good", "name": "w-good", "reason": "area read from the wrong corner", "hide": False,
                                "clear": [{"stage": "confirm", "metric": "area_um2"}], "again": []}, separators=(",", ":")))
            r.clean("rejected results")
        finally:
            b.js("window.fetch = window.__rejFetch; delete window.__rejFetch; delete window.__rejFixture; delete window.__rejSent; localStorage.removeItem('flux-results:[\"bob\",\"bob\",\"sw\"]'); return 1")
    r.step("rejected results", rejected_results, setup=True)

    def keyboard_journey():
        """D929: Tab and Enter alone -- a Results header sorts (a button, aria-sort), a result opens its
        design and source, two are ticked and compared; a Live branch opens and folds (a button,
        aria-expanded), the focus kept through the redraw; an agent's turn opens from its row."""
        r.login("bob")

        def tab_to(cond, what, back=False, limit=400):
            for _ in range(limit):
                if back:
                    b.window_focus()
                    b.cmd("WebDriver:PerformActions", {"actions": [{"type": "key", "id": "kb", "actions": [
                        {"type": "keyDown", "value": b.SHIFT}, {"type": "keyDown", "value": b.TAB}, {"type": "keyUp", "value": b.TAB}, {"type": "keyUp", "value": b.SHIFT}]}]})
                    b.cmd("WebDriver:ReleaseActions", {})
                    b.wait("!window.__shift", timeout=5, what="Shift let go")   # an Enter with Shift held is no button's click
                else:
                    b.keys(b.TAB)
                if b.js(f"const a = document.activeElement; return !!a && ({cond});"):
                    return True
            raise AssertionError(f"Tab never reached {what}")
        # two designs to compare, whatever the steps before left: one pass more each until there are
        for _ in range(4):
            if len(json.loads(r.api("/apps/sw/results")["body"]).get("designs") or []) >= 2:
                break
            asked = time.time()
            r.api("/apps/sw/start", "POST", {"passes": 1, "screen_only": False})
            for _ in range(240):
                st = json.loads(r.api("/apps/sw/state")["body"])
                if not st.get("running") and (st.get("last_active") or 0) >= asked:
                    break
                time.sleep(0.5)
        r.page("#/app/sw/results", "document.querySelectorAll('#main table.designs tbody tr').length >= 2", "Results")
        b.js("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0);"
             " for (const k of ['keydown', 'keyup']) document.addEventListener(k, e => { if (e.key === 'Shift') window.__shift = k === 'keydown'; }, true); return 1")
        metric = b.js("return document.querySelector('#main table.designs th button.th-sort[data-key]:not([data-key=name]):not([data-key=verdict]):not([data-key=stage])').dataset.key")
        tab_to(f"a.matches('button.th-sort') && a.dataset.key === {json.dumps(metric)}", f"the {metric} header")
        b.keys(b.ENTER)
        got = b.js(f"""const a = document.activeElement; return [a.matches('button.th-sort') && a.dataset.key === {json.dumps(metric)},
            a.closest('th').getAttribute('aria-sort'), document.querySelectorAll('#main table.designs th[aria-sort=none]').length > 0]""")
        r.check("keyboard: Enter on a Results header sorts it, aria-sort said, the focus kept (D929)", got == [True, "descending", True], str(got))
        tab_to("a.matches('button.open-design')", "a result's open button")
        name = b.js("return document.activeElement.textContent")
        b.keys(b.ENTER)
        b.wait(f"(() => {{ const d = document.querySelector('#main .detail'); return d && d.querySelector('h2') && d.querySelector('h2').textContent === {json.dumps(name)}"
               " && [...d.querySelectorAll('h3')].some(x => x.textContent === 'Design'); })()", timeout=45, what="the design opened, its source shown")
        r.check("keyboard: Enter on a result opens its design and source (D929)", True)
        for n in (1, 2):
            tab_to("a.matches('#main table.designs tbody input[type=checkbox]') && !a.checked", f"compare box {n}")
            b.keys(b.ENTER)
        r.check("keyboard: Enter ticks two results to compare (D929)", b.js("return document.querySelectorAll('#main table.designs tbody input:checked').length") == 2)
        tab_to("a.matches('button') && a.textContent === 'Compare 2/2'", "the Compare button", back=True)
        # D929: Enter on Compare, the keyboard only, never a click -- the window focused first (keys())
        b.js("window.__clicked = false; document.activeElement.addEventListener('click', () => { window.__clicked = true; }, {once: true}); return 1")
        b.keys(b.ENTER)
        b.wait("window.__clicked", timeout=10, what="Compare clicked by its Enter -- " + str(b.js("return [document.activeElement.textContent, document.hasFocus()]")))
        said = b.wait("document.querySelector('dialog.dlg[open]') && [...document.querySelectorAll('dialog.dlg[open] h3')].map(x => x.textContent).join('|')",
                      timeout=45, what="the comparison, Compare clicked by its Enter")
        r.check("keyboard: Enter on Compare shows the two compared, their source (D929)", said.startswith("Source"), said)
        b.keys(b.ENTER)                                         # the dialog's Close has the focus
        b.wait("!document.querySelector('dialog.dlg[open]')", timeout=5, what="the comparison closed by Enter")
        # Live: a branch is a button that says whether it is open
        r.page("#/app/sw/live", "document.querySelector('[aria-label=\"Live pass\"]')", "Live")
        b.js("const s = document.querySelector('[aria-label=\"Live pass\"]'); s.value = 'all'; s.dispatchEvent(new Event('change', {bubbles: true})); return 1")
        b.wait("document.querySelector('.alt-tree button.alt-fold[aria-expanded]')", timeout=30, what="Live's tree, a fold in it")
        b.js("document.activeElement && document.activeElement.blur(); return 1")
        tab_to("a.matches('.alt-tree button.alt-fold[aria-expanded]')", "a fold of the tree")
        key, was = b.js("return [document.activeElement.dataset.fold, document.activeElement.getAttribute('aria-expanded')]")
        b.keys(b.ENTER)
        now = b.js("const a = document.activeElement; return [a.dataset.fold, a.getAttribute('aria-expanded')]")
        b.keys(b.ENTER)
        back = b.js("const a = document.activeElement; return [a.dataset.fold, a.getAttribute('aria-expanded')]")
        r.check("keyboard: Enter opens and folds a Live branch, aria-expanded said, the focus kept (D929)",
                now == [key, "true" if was == "false" else "false"] and back == [key, was], f"{key} {was} {now} {back}")
        # Agents: a turn opens from its row's button (the sweep has no agent: its turns stood in for)
        turn = json.dumps({"turns": [{"k": 1, "agent": "opencode", "kind": "agent", "ts": time.time() - 60, "seconds": 3, "ok": True, "prompt": "write it"}]})
        b.js("const real = window.fetch.bind(window), body = arguments[0];"
             " window.fetch = (u, o) => String(u).startsWith('/api/apps/sw/turns') ? Promise.resolve(new Response(body, {status: 200,"
             " headers: {'Content-Type': 'application/json'}})) : real(u, o); return 1", turn)
        r.page("#/app/sw/agents", "document.querySelector('#main button.open-turn')", "Agents, a turn stood in for")
        b.js("document.activeElement && document.activeElement.blur(); return 1")
        tab_to("a.matches('button.open-turn')", "a turn's button")
        b.keys(b.ENTER)
        b.wait("(document.querySelector('#main .detail h2') || {}).textContent === 'opencode'", timeout=45, what="the turn opened")
        r.check("keyboard: Enter on a turn's button opens it (D929)", True)
        b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-keys={time.time()}#/"})   # a reload: the real fetch again
        b.wait("document.querySelector('#main')", timeout=20)
        b.js(WATCH)
        r.clean("keyboard journey")
    r.step("keyboard journey", keyboard_journey, setup=True)

    def graphs():
        """D914-D916 on a loop of parts, its results stood in for in the page (the fetch answered with
        GRAPHS_RESULTS): a design failing a requirement off its axes is never the best, no feasible front
        where none qualifies, parts never pooled for a whole, each part's colour its own and the same
        after a stage change. FLUX_E2E_SHOTS: Graphs at a desktop's width and at 390, light and dark."""
        r.login("bob")
        stub = ("const w = arguments[0] ? document.getElementById('phone').contentWindow : window, real = w.fetch.bind(w), body = arguments[1];"
                " const said = (t) => Promise.resolve(new w.Response(t, {status: 200, headers: {'Content-Type': 'application/json'}}));"
                " w.fetch = (u, o) => String(u).startsWith('/api/apps/sw/results') ? said(body)"
                " : String(u).startsWith('/api/apps/sw/design?') ? said('{\"artifact\": null}') : real(u, o); return 1")
        payload = json.dumps(GRAPHS_RESULTS)
        r.page("#/", "document.querySelector('#main table.list, #main .empty')", "the loops")
        r.preferences(reset=True)
        b.js(stub, False, payload)
        b.go(f"{r.url}/#/app/sw/results/graphs")
        b.wait("document.querySelector('#main svg.chart.pareto')", timeout=15, what="the stood-in graphs")
        baseline = b.js("return [...document.querySelectorAll('#main svg.pareto .pt.baseline, #main svg.best-chart .pt.baseline')].map(p => [p.tagName, p.getAttribute('data-name'), p.querySelector('title').textContent])")
        refs = b.js("return [...document.querySelectorAll('#main svg.pareto .baseline-ref, #main svg.best-chart .baseline-ref')].map(p => [p.tagName, Number(p.dataset.value), p.querySelector('title').textContent])")
        r.check("baseline is a reference line on both graphs, never a search point", not baseline and len(refs) == 3
                and sorted(p[1] for p in refs) == [40, 40, 1100]
                and all(p[0] == "line" and "w-baseline" in p[2] and "Baseline (pass 0)" in p[2] for p in refs), str(refs))
        r.check("baseline has a shared graph legend", b.js("return (document.querySelector('#main .graphs-ctl [data-baseline]') || {}).textContent === 'Baseline (pass 0)'"))
        r.check("baseline lines are faint and have no inline labels", b.js("return !document.querySelector('#main .baseline-label') && [...document.querySelectorAll('#main svg.pareto .baseline-ref, #main svg.best-chart .baseline-ref')].every(p => Number(getComputedStyle(p).strokeOpacity) === 0.4)"))
        alone = b.ajs("const done = arguments[arguments.length - 1], fixture = arguments[0];"
                      " " + CHARTS + ".then(C => { const d = fixture.designs.filter(d => d.baseline), metric = {metric: 'area_um2', direction: 'minimize'};"
                      " const best = C.bestChart(C.designPoints(d, metric), metric, []), pareto = C.paretoChart(d, 'area_um2', 'fmax_mhz', '', fixture.objective_list);"
                      " done([best, pareto].map(el => [el.querySelectorAll('.baseline-ref').length, el.querySelectorAll('svg.best-chart .pt, svg.pareto .pt').length, /NaN|Infinity/.test(el.innerHTML)])); }).catch(e => done(String(e)));", GRAPHS_RESULTS)
        r.check("baseline-only graphs draw finite reference lines without search points", alone == [[2, 0, False], [3, 0, False]], str(alone))
        focused = b.ajs("""const done = arguments[arguments.length - 1]; """ + CHARTS + """.then(C => {
          const d = (name, x, y, extra = {}) => ({name, group: 'whole', eligible: true, verdict: 'accepted', shown: 'confirm',
            numbers: {x, y}, stages: {confirm: {x, y}}, first: '2026-10-01T00:00:00Z', ...extra});
          const rows = [d('a', 10, 20), d('b', 20, 10), d('bad', 1e6, 1e6), d('failed', 0, 0, {eligible: false}),
            d('part', -1e6, -1e6, {group: 'part'}), d('base', 2e6, 2e6, {baseline: true})];
          const objectives = ['x', 'y'].map(metric => ({metric, direction: 'minimize', goal: 3e6}));
          const plot = (rows, focus, stage = '') => C.paretoChart(rows, 'x', 'y', stage, objectives, null, {scope: 'whole', focus, legend: false});
          const geometry = el => {
            const point = name => el.querySelector('.pt[data-name="' + name + '"]');
            const x = name => Number(point(name).getAttribute('cx')), y = name => Number(point(name).getAttribute('cy'));
            return {span: Math.abs(x('a') - x('b')), ySpan: Math.abs(y('a') - y('b')),
              front: [...el.querySelectorAll('.on-front')].map(p => p.dataset.name),
              all: el.querySelectorAll('.pt').length, badOutside: x('bad') > 546 && y('bad') < 14,
              clipped: !!el.querySelector('.pareto-plot[clip-path]') && !!el.querySelector('clipPath rect')};
          };
          const full = geometry(plot(rows, false)), focus = geometry(plot(rows, true));
          const single = plot([d('one', 0, 0), d('bad', 1e6, 1e6)], true);
          const point = single.querySelector('.on-front');
          const none = plot([d('failed', 0, 0, {eligible: false}), d('bad', 1e6, 1e6, {eligible: false})], true);
          const baseline = plot(rows.filter(d => d.baseline), true);
          rows[0].stages.screen = {x: 10, y: 30}; rows[1].stages.screen = {x: 5, y: 20};
          const stage = plot(rows, true, 'screen');
          done({full, focus, single: [Number(point.getAttribute('cx')), Number(point.getAttribute('cy')), /NaN|Infinity/.test(single.innerHTML)],
            none: [none.querySelector('.pareto-focus-said').textContent, !!none.querySelector('.on-front'), /NaN|Infinity/.test(none.innerHTML)],
            baseline: [baseline.querySelectorAll('.baseline-ref').length, /NaN|Infinity/.test(baseline.innerHTML)],
            stage: [...stage.querySelectorAll('.on-front')].map(p => p.dataset.name)});
        }).catch(e => done({error: String(e)}));""")
        r.check("Focus fits only the scoped front, excluding distant designs, baselines and goals", focused.get("focus", {}).get("front") == ["a", "b"]
                and focused["focus"]["span"] > 400 and focused["focus"]["ySpan"] > 200 and focused["full"]["span"] < 1
                and focused["focus"]["badOutside"] and focused["focus"]["clipped"] and focused["focus"]["all"] == focused["full"]["all"] == 5, focused)
        r.check("Focus handles a single zero-valued front, unavailable fronts, baselines and stage changes", focused.get("single") == [304, 140, False]
                and focused["none"] == [" · full range (no feasible front)", False, False] and focused["baseline"] == [2, False]
                and focused["stage"] == ["b"], focused)
        b.click(".pareto-focus")
        r.check("Focus toggles on with keyboard focus retained", b.js("return document.querySelector('.pareto-focus').getAttribute('aria-pressed') === 'true' && document.activeElement === document.querySelector('.pareto-focus') && document.querySelector('.pareto-focus-said').textContent.includes('focused on front')"))
        b.click(".pareto-focus")
        r.check("Focus toggles off to restore the full view", b.js("return document.querySelector('.pareto-focus').getAttribute('aria-pressed') === 'false' && !document.querySelector('.pareto-focus-said')"))
        card = ("[...document.querySelectorAll('#main .card')].find(x => (x.querySelector('h2') || {}).textContent === arguments[0])")
        area = b.js(f"const c = {card}; const f = [...c.querySelectorAll('figure')].find(x => x.querySelector('figcaption strong').textContent === 'area_um2');"
                    " return [f.querySelector('.best-said').textContent, f.querySelector('svg').getAttribute('aria-label')]", "Improvement by design")
        r.check("a design failing a requirement off the axis is never the best (D914)", area[0] == "31", str(area))
        front = b.js(f"const c = {card}; return [...c.querySelectorAll('.pt.on-front')].map(p => p.getAttribute('data-name'))", "Pareto front")
        r.check("the feasible front is the whole's that meet every requirement (D914)", front == ["w-good"], str(front))
        hollow = b.js("const p = document.querySelector('#main svg.chart.pareto .pt[data-name=\"w-bad\"]'); return [p.getAttribute('class'), p.querySelector('title').textContent]")
        r.check("a miss is drawn, hollow, its reason in its details (D914)", "failed" in hollow[0] and "fmax_mhz 800 is below 1000" in hollow[1], str(hollow))
        b.js("document.querySelector('#main svg.chart.pareto .pt[data-name=\"w-bad\"]').dispatchEvent(new MouseEvent('click', {bubbles: true})); return 1")
        said = b.wait("(document.querySelector('#main .graphs .detail-card h2') || {}).textContent", timeout=10, what="the point's design")
        r.check("a plot point opens its design's detail in Graphs (D916)", said == "w-bad", said)
        b.js("document.querySelector('#main .graphs').__e2e = 1; return 1")
        r.button("Results", ".subrow .subtabs")
        b.wait("document.querySelector('#main table.designs')", timeout=10)
        r.check("back in Results the same design stays selected (D916)", b.js(
            "return (document.querySelector('#main .split.results .detail-card h2') || {}).textContent === 'w-bad'"
            " && (document.querySelector('#main table.designs tr.sel') || {}).innerText.includes('w-bad')"))
        b.click(".bar .relative-values")
        r.check("switching measurement values preserves the selected design", b.js("return document.querySelector('table.designs tr.sel').innerText.includes('w-bad') && document.querySelector('.detail-card h2').textContent === 'w-bad'"))
        b.click(".bar .relative-values")
        r.button("Graphs", ".subrow .subtabs")
        b.wait("document.querySelector('#main svg.chart.pareto')", timeout=10)
        r.check("the graphs kept, not built again, the detail with them (D916)", b.js("const g = document.querySelector('#main .graphs');"
                " return g.__e2e === 1 && (g.querySelector('.detail-card h2') || {}).textContent === 'w-bad'"))

        def colours():
            return b.js("""const pick = (g) => { const p = document.querySelector(`#main svg.chart.pareto .pt:not(.on-front)[data-group="${g}"]`); return p ? getComputedStyle(p).stroke : null; };
                const key = (g) => { const k = document.querySelector(`#main .graphs-ctl .key-item[data-group="${g}"] .pt`); return k ? getComputedStyle(k).fill : null; };
                return { whole: pick('whole'), decoder: pick('decoder'), encoder: pick('encoder'), kwhole: key('whole'), kdecoder: key('decoder'), kencoder: key('encoder') };""")
        before = colours()
        r.check("each part its own colour, the legend's the same (D915)", len({before["whole"], before["decoder"], before["encoder"]}) == 3 and None not in before.values()
                and (before["whole"], before["decoder"], before["encoder"]) == (before["kwhole"], before["kdecoder"], before["kencoder"]), str(before))
        b.js(f"const s = {card}.querySelectorAll('select')[2]; s.value = 'screen'; s.dispatchEvent(new Event('change')); return 1", "Pareto front")
        said = b.js(f"return {card}.querySelector('.front-said').textContent", "Pareto front")
        r.check("no whole measured at a stage: said, the parts not pooled (D914)", "parts are not pooled" in said
                and not b.js(f"return {card}.querySelector('.pt.on-front')", "Pareto front"), said)
        after = colours()
        r.check("the parts keep their colours across a stage change (D915)", (after["decoder"], after["encoder"]) == (before["decoder"], before["encoder"]), f"{before} {after}")
        b.js("const c = [...document.querySelectorAll('#main .scope .choice button')].find(x => x.textContent === 'decoder'); c.click(); return 1")
        said = b.js(f"return {card}.querySelector('.front-said').textContent", "Pareto front")
        r.check("no feasible front when none qualifies (D914)", said == "No feasible design yet" and not b.js(
            f"return {card}.querySelector('.pt.on-front, path.front')", "Pareto front"), said)
        best = b.js(f"return [...{card}.querySelectorAll('figure')].map(f => [f.querySelector('.best-said').textContent, !!f.querySelector('path.best')])", "Improvement by design")
        r.check("before a feasible design, no best line (D914)", all(x == ["No feasible design yet", False] for x in best), str(best))

        r.button("Results", ".subrow .subtabs")
        b.wait("document.querySelector('table.designs')", what="Results to hide a column")
        r.page("#/app/sw/settings", "document.querySelector('.measurement-options')", "graph measurement Preferences")
        b.click('.measurement-options input[data-metric="area_um2"]')
        r.page("#/app/sw/results/graphs", "document.querySelector('#main svg.chart.pareto')", "Graphs after measurement Preferences")
        b.wait("document.querySelector('#main svg.chart.pareto')", what="graph preferences")
        b.js(f"const c = {card}; for (const [i, v] of [[0, 'area_um2'], [1, 'fmax_mhz']]) {{ const s = c.querySelectorAll('select')[i]; s.value = v; s.dispatchEvent(new Event('change')); }} return 1", "Pareto front")
        b.js(f"const c = {card}, s = c.querySelector('select'); s.value = 'screen'; s.dispatchEvent(new Event('change')); for (const button of c.querySelectorAll('.chips button')) if (button.classList.contains('on') !== (button.textContent === 'fmax_mhz')) button.click(); return 1", "Improvement by design")
        preferences = """const cards = [...document.querySelectorAll('#main .card')], c = title => cards.find(c => c.querySelector('h2')?.textContent === title);
            return [[...c('Pareto front').querySelectorAll('select')].map(s => s.value),
              c('Improvement by design').querySelector('select').value,
              [...c('Improvement by design').querySelectorAll('.chips button.on')].map(b => b.textContent),
              document.querySelector('#main .scope .choice button.on').textContent];"""
        expected = [["area_um2", "fmax_mhz", "screen"], "screen", ["fmax_mhz"], "decoder"]
        r.check("graph controls change axes, stages, selected measurements and scope", b.js(preferences) == expected)
        b.click(".pareto-focus")
        r.check("Focus stays enabled while an empty front uses the full range", b.js("return document.querySelector('.pareto-focus').getAttribute('aria-pressed') === 'true' && document.querySelector('.pareto-focus-said').textContent.includes('no feasible front')"))
        r.page("#/", "document.querySelector('#main table.list, #main .empty')", "home before a browser reload")
        r.preferences()
        b.js("localStorage.clear(); window.__preferencesReload = 1; location.reload(); return 1")
        b.wait("!window.__preferencesReload && document.querySelector('#who') && document.querySelector('#main table.list, #main .empty')", what="reloaded browser document")   # home drawn: its first render cannot land after the next page
        b.js(WATCH)
        b.js(stub, False, payload)
        r.page("#/app/sw/results/graphs", "document.querySelector('#main svg.chart.pareto')", "remembered graphs")
        r.check("graph preferences restore from the server with browser storage cleared", b.js(preferences) == expected, str(b.js(preferences)))
        r.preferences()
        r.check("Pareto Focus is remembered across a full browser reload", b.js("return document.querySelector('.pareto-focus').getAttribute('aria-pressed') === 'true' && window.__savedResults.graphs.paretoFocus === true"))
        r.button("Results", ".subrow .subtabs")
        b.wait("document.querySelector('table.designs')", what="remembered columns after reload")
        r.check("hidden columns survive a full browser reload while all rows remain visible", b.js("return document.querySelectorAll('table.designs tbody tr').length === 7 && document.querySelectorAll('table.designs th.measurement-head').length === 1 && !document.querySelector('th[data-label=area_um2]') && document.querySelector('.show-hidden-columns').textContent === 'Hidden 1'"))
        b.click(".show-hidden-columns")
        r.preferences()
        r.check("the Hidden toggle preserves ignored columns and the saved graph preferences", b.js("const p = window.__savedResults; return p.hiddenMetrics.join() === 'area_um2' && p.showHiddenMetrics && p.graphs.x === 'area_um2' && p.graphs.metrics.join() === 'fmax_mhz'"))
        r.page("#/", "document.querySelector('#main table.list, #main .empty')", "home before reloading the Hidden toggle")
        r.preferences()
        b.js("localStorage.clear(); window.__preferencesReload = 1; location.reload(); return 1")
        b.wait("!window.__preferencesReload && document.querySelector('#who') && document.querySelector('#main')", what="reloaded Hidden preference")
        b.js(WATCH)
        b.js(stub, False, payload)
        r.page("#/app/sw/results", "document.querySelector('table.designs')", "remembered Hidden toggle")
        r.preferences()
        r.check("the Hidden toggle survives a reload without forgetting ignored metrics", b.js("return document.querySelector('.show-hidden-columns').getAttribute('aria-pressed') === 'true' && document.querySelectorAll('table.designs th.measurement-head').length === 2 && document.querySelector('th[data-label=area_um2]').classList.contains('hidden-measurement') && window.__savedResults.hiddenMetrics.includes('area_um2')"))
        r.page("#/", "document.querySelector('#main table.list, #main .empty')", "home before an empty graph selection")
        r.preferences(patch={"graphs": {"metrics": []}})
        r.page("#/app/sw/results/graphs", "document.querySelector('#main svg.chart.pareto')", "no selected measurements")
        r.check("an intentionally empty graph selection remains empty", b.js(f"const c = {card}; return !c.querySelector('figure') && !c.querySelector('.chips button.on') && c.textContent.includes('Pick a metric to chart')", "Improvement by design"))
        r.page("#/", "document.querySelector('#main table.list, #main .empty')", "home before obsolete preferences")
        r.preferences(reset=True)
        r.preferences(patch={"graphs": {"x": "removed", "y": "removed", "paretoStage": "removed", "timeStage": "removed", "metrics": ["removed"], "scope": "removed"}})
        r.page("#/app/sw/results/graphs", "document.querySelector('#main svg.chart.pareto')", "obsolete graph settings")
        r.check("removed metrics, stages and scopes fall back to usable defaults", b.js(preferences) == [["fmax_mhz", "area_um2", ""], "", ["area_um2"], "Whole"], str(b.js(preferences)))
        b.js("localStorage.removeItem('flux-results:[\"bob\",\"bob\",\"sw\"]'); return 1")
        r.preferences(reset=True)
        r.clean("graphs")
        shots = Path(os.environ["FLUX_E2E_SHOTS"]) if os.environ.get("FLUX_E2E_SHOTS") else None
        if not shots:
            return
        shots.mkdir(parents=True, exist_ok=True)
        for width in (1240, 390):
            for theme in ("light", "dark"):
                b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
                     "f.style.cssText = `width:${arguments[0]}px;height:860px;border:0`; f.src = '/#/'; document.body.append(f); return 1", width)
                b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d && d.querySelector('#main') && d.readyState === 'complete'; })()", timeout=20)
                b.js(stub, True, payload)
                b.js("const w = document.getElementById('phone').contentWindow; w.document.documentElement.dataset.theme = arguments[0]; w.location.hash = '#/app/sw/results/graphs'; return 1", theme)
                b.wait("document.getElementById('phone').contentDocument.querySelector('#main svg.chart.pareto')", timeout=20, what=f"Graphs at {width}")
                for part, y in (("top", 0), ("charts", 700)):          # the decision, scope and legend; then the charts
                    b.js("document.getElementById('phone').contentWindow.scrollTo(0, arguments[0]); return 1", y)
                    time.sleep(0.6)
                    png = b.cmd("WebDriver:TakeScreenshot", {"id": b.find("#phone"), "full": False})["value"]
                    (shots / f"graphs-{'desktop' if width > 1000 else str(width) + 'px'}-{theme}-{part}.png").write_bytes(base64.b64decode(png))
        b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-graphs={time.time()}#/"})
        b.wait("document.querySelector('#main')", timeout=20)
    r.step("graphs", graphs, setup=True)

    def ideas_notebook():
        with loop(r, "ui-ideas-notebook") as name:
            title = "<img src=x onerror=alert(1)>"
            data = {"campaign": "fixture", "ideas": [
                {"id": "idea-tested", "part": "core", "title": title, "hypothesis": "A faster arithmetic approach",
                 "test": "Check corners; measure cycles", "status": "measured", "evaluations": [
                     {"pass": 1, "design": "core#1", "stage": "gate", "status": "refused", "metrics": {},
                      "error": "incorrect corner case", "at": "2026-10-09T10:00:00Z"},
                     {"pass": 2, "design": "core#2", "stage": "bench", "status": "ok", "metrics": {"cycles": 8},
                      "error": "", "at": "2026-10-09T10:05:00Z"},
                     {"pass": 3, "design": "core#3", "stage": "bench", "status": "ok",
                      "metrics": {f"sim_us.test_with_a_long_name_{i}": 1234.5678 + i for i in range(12)},
                      "error": "first line of a long failure\n" + "x" * 3000, "at": "2026-10-09T10:09:00Z"}]},
                {"id": "idea-future", "part": "core", "title": "Try a lookup table", "hypothesis": "Compare the area trade-off",
                 "test": "", "status": "proposed", "evaluations": []}]}
            b.js("""window.__ideasFetch = window.fetch; window.__ideasData = arguments[1];
              const path = '/api/apps/' + arguments[0] + '/ideas';
              window.fetch = async (u, o) => new URL(String(u), location.href).pathname === path
                ? new Response(JSON.stringify(window.__ideasData), {headers: {'Content-Type': 'application/json'}})
                : window.__ideasFetch(u, o); return 1;""", name, data)
            try:
                r.page(f"#/app/{name}/results/ideas", "document.querySelector('.ideas-table')", "Ideas")
                r.check("Ideas is accessible before any measured design", b.js("return document.querySelector('.subtabs .on').textContent === 'Ideas' && document.querySelectorAll('.ideas-table > tbody > tr').length === 2"))
                r.check("idea notes render literally and pending ideas stay untested", b.js("return !document.querySelector('.ideas-view img') && document.querySelector('.ideas-table').textContent.includes(arguments[0]) && document.querySelector('.ideas-table').textContent.includes('Not tested yet')", title))
                b.click(".ideas-table .idea-evidence")
                r.check("idea history opens in a full-width row under the idea, with failures, passes and measured numbers (D1145)", b.js("const row = document.querySelector('.ideas-table > tbody > tr.idea-evals-row'), td = row && row.cells[0]; return td.colSpan === 4 && td.getBoundingClientRect().width > 0.9 * document.querySelector('.ideas-table').getBoundingClientRect().width && row.textContent.includes('incorrect corner case') && row.textContent.includes('cycles 8') && [...document.querySelectorAll('.idea-evaluations tbody tr')].map(r => r.cells[0].textContent).join(',') === '1,2,3'"))
                if os.environ.get("FLUX_E2E_SHOTS"):
                    b.shot(os.path.join(os.environ["FLUX_E2E_SHOTS"], "ideas-evaluations.png"))
                r.check("a long evaluation neither widens the notebook nor shows every number and its whole error at once (D1042)", b.js(
                    "const w = document.querySelector('.ideas-view > .scroll-x'), row = document.querySelectorAll('.idea-evaluations tbody tr')[2];"
                    "return w.scrollWidth <= w.clientWidth + 1 && row.querySelectorAll(':scope td > .idea-metrics > .idea-metric').length === 6"
                    " && row.textContent.includes('+6 more') && !row.querySelector('.idea-error').open && row.getBoundingClientRect().height < 260"))
                b.click(".raw-view")
                b.wait("document.querySelector('dialog.fullscreen-view .raw-content')", what="raw notebook")
                r.check("raw notebook includes full evaluation data", b.js("return JSON.parse(document.querySelector('dialog .raw-content').textContent).ideas[0].evaluations[1].metrics.cycles === 8"))
                r.button("Close", "dialog.fullscreen-view")
                b.click(".fullscreen-button")
                b.wait("document.querySelector('dialog.fullscreen-view .ideas-table')", what="fullscreen notebook")
                r.check("fullscreen shows the open evaluation history", b.js("return !!document.querySelector('dialog .idea-evals-row')"))
                r.button("Close", "dialog.fullscreen-view")
                b.cmd("WebDriver:SetWindowRect", {"width": 480, "height": 900})
                r.check("notebook tables scroll within their view on a narrow screen", b.js("return document.documentElement.scrollWidth <= innerWidth + 2"))
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("window.__ideasData.ideas.push({id:'idea-next', part:'core', title:'Another alternative', hypothesis:'Try a different structure', test:'', status:'proposed', evaluations:[]}); return 1")
                r.button("Refresh")
                b.wait("document.querySelectorAll('.ideas-table > tbody > tr:not(.idea-evals-row)').length === 3", what="refreshed ideas")
                r.clean("ideas notebook")
            finally:
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("window.fetch = window.__ideasFetch; return 1")

    r.step("ideas notebook", ideas_notebook)

    def ideas_navigation():
        with loop(r, "ui-ideas-navigation") as name:
            stub = """window.__ideasNavigationFetch = window.fetch;
              window.__ideasNavigationData = {campaign:null, ideas:[]}; window.__ideasNavigationDeferred = false;
              const path = '/api/apps/' + arguments[0] + '/ideas';
              window.fetch = async (u, o) => {
                if (new URL(String(u), location.href).pathname !== path) return window.__ideasNavigationFetch(u, o);
                if (window.__ideasNavigationDeferred) await new Promise(resolve => {window.__ideasNavigationRelease = resolve;});
                return new Response(JSON.stringify(window.__ideasNavigationData), {headers:{'Content-Type':'application/json'}});
              }; return 1;"""
            b.js(stub, name)
            try:
                r.page(f"#/app/{name}/results/ideas", "document.querySelector('.ideas-view .empty')", "empty Ideas")
                r.check("an empty notebook explains how to collect ideas", "No ideas yet" in r.text())
                b.click(".raw-view")
                b.wait("document.querySelector('dialog .raw-content')", what="empty raw notebook")
                r.check("empty Raw is valid JSON", b.js("return JSON.parse(document.querySelector('dialog .raw-content').textContent).ideas.length === 0"))
                r.button("Close", "dialog.fullscreen-view")
                b.js("""window.__ideasNavigationData = {campaign:'fixture', ideas:[{id:'idea-zero', part:'', title:'Zero cycles',
                  hypothesis:'Check a zero-cost candidate', test:'', status:'measured', evaluations:[{pass:null, design:'d#1',
                  stage:'bench', status:'ok', metrics:{cycles:0}, error:'', at:'2026-10-09T10:00:00Z'}]}]}; return 1;""")
                r.button("Refresh")
                b.wait("document.querySelector('.ideas-table')", what="first recorded idea")
                b.click(".ideas-table .idea-evidence")
                r.check("zero measurements display and older trials tolerate missing pass numbers", b.js("return document.querySelector('.idea-evaluations tbody tr').cells[0].textContent === '—' && document.querySelector('.idea-evaluations').textContent.includes('cycles 0')"))
                b.js("window.__ideasNavigationDeferred = true; return 1")
                r.button("Refresh")
                b.wait("!!window.__ideasNavigationRelease", what="delayed notebook request")
                r.button("Files", "#main .tabs")
                b.wait("document.querySelector('.files-card')", what="Files while Ideas is delayed")
                b.ajs("const done = arguments[arguments.length-1]; window.__ideasNavigationRelease(); window.__ideasNavigationDeferred = false; requestAnimationFrame(() => requestAnimationFrame(() => done(true)))")
                r.check("late notebook responses cannot replace another tab", b.js("return !!document.querySelector('.files-card') && !document.querySelector('.ideas-view')"))
                r.button("Results", "#main .tabs")
                b.wait("document.querySelector('.subtabs button')", what="Results subtabs")
                r.button("Ideas", "#main .subtabs")
                b.wait("document.querySelector('.ideas-table')", what="return to Ideas")
                b.click(".fullscreen-button")
                b.wait("document.querySelector('dialog.fullscreen-view[open]')", what="Ideas fullscreen")
                r.page(f"#/app/{name}/files", "document.querySelector('.files-card')", "Files from fullscreen")
                r.check("leaving Ideas closes its fullscreen viewer", b.js("return !document.querySelector('dialog.fullscreen-view')"))
                r.page(f"#/app/{name}/results/ideas", "document.querySelector('.ideas-table')", "Ideas before reload")
                b.js("window.__ideasNavigationReload = true; location.reload(); return 1")
                b.wait("!window.__ideasNavigationReload && document.querySelector('.ideas-view')", what="Ideas deep-link reload")
                b.js(watch)
                r.check("Ideas deep links survive a full browser reload", b.js("return location.hash.endsWith('/results/ideas') && document.querySelector('.subtabs .on').textContent === 'Ideas' && !!document.querySelector('.ideas-view .empty')"))
                r.clean("ideas navigation")
            finally:
                b.js("if(window.__ideasNavigationRelease) window.__ideasNavigationRelease(); if(window.__ideasNavigationFetch) window.fetch=window.__ideasNavigationFetch; return 1")

    r.step("ideas navigation", ideas_navigation)

    def ideas_sharing():
        with loop(r, "UI_Ideas-Shared") as name:
            shared = r.api(f"/apps/{name}/shares", "PUT", {"user": "cy", "perm": "watch"})
            r.check("notebook fixture is shared for watching", shared["status"] == 200, shared["body"])
            r.login("cy")
            b.js("""window.__sharedIdeasFetch=window.fetch; window.__sharedIdeasCalls=[]; window.__sharedIdeasFail=false;
              window.__sharedIdeasData={campaign:'fixture',ideas:[{id:'idea-owner',part:'',title:'Owner hypothesis',
                hypothesis:'Compare two approaches',test:'',status:'proposed',evaluations:[]}]};
              const path='/api/apps/'+arguments[0]+'/ideas';
              window.fetch=async(u,o={})=>{
                const url=new URL(String(u),location.href);
                if(url.pathname!==path) return window.__sharedIdeasFetch(u,o);
                window.__sharedIdeasCalls.push({owner:url.searchParams.get('owner'),method:o.method||'GET'});
                return new Response(JSON.stringify(window.__sharedIdeasFail?{detail:'Temporary notebook error'}:window.__sharedIdeasData),
                  {status:window.__sharedIdeasFail?503:200,headers:{'Content-Type':'application/json'}});
              }; return 1;""", name)
            try:
                r.page(f"#/u/bob/app/{name}/results/ideas", "document.querySelector('.ideas-table')", "shared Ideas")
                r.check("watchers' notebook requests name the owner", b.js("return window.__sharedIdeasCalls.length===1 && window.__sharedIdeasCalls[0].owner==='bob' && window.__sharedIdeasCalls[0].method==='GET'"))
                b.click(".raw-view")
                b.wait("document.querySelector('dialog .raw-content')", what="shared raw notebook")
                r.check("watchers can inspect raw owner data", b.js("return JSON.parse(document.querySelector('dialog .raw-content').textContent).ideas[0].id==='idea-owner'"))
                r.button("Close", "dialog.fullscreen-view")
                b.js("window.__sharedIdeasFail=true; return 1")
                r.button("Refresh")
                b.wait("[...document.querySelectorAll('.toast.t-bad')].some(t=>t.textContent.includes('Temporary notebook error'))", what="failed notebook refresh")
                r.check("a failed refresh keeps the current notebook and allows retry", b.js("return document.querySelector('.ideas-table').textContent.includes('Owner hypothesis') && [...document.querySelectorAll('#main button')].some(b=>b.getAttribute('aria-label')==='Refresh'&&!b.disabled)"))
                expected = b.js("""const bad=window.__e2e.bad.splice(0); document.querySelectorAll('.toast.t-bad .x').forEach(x=>x.click());
                  window.__sharedIdeasFail=false; window.__sharedIdeasData.ideas[0].title='Updated owner hypothesis'; return bad;""")
                r.check("the failed refresh produces one clear error notice", expected == ["Temporary notebook error"], expected)
                r.button("Refresh")
                b.wait("document.querySelector('.ideas-table').textContent.includes('Updated owner hypothesis')", what="recovered notebook refresh")
                r.check("refresh recovery keeps all requests scoped to the owner", b.js("return window.__sharedIdeasCalls.length===3 && window.__sharedIdeasCalls.every(c=>c.owner==='bob'&&c.method==='GET')"))
                r.clean("ideas sharing and refresh recovery")
            finally:
                b.js("window.fetch=window.__sharedIdeasFetch; return 1")

    r.step("ideas sharing", ideas_sharing)
