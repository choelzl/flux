"""D1126: an agent's conversation (components/Conversation.js) keeps a working box one opened while the turn grows --
drawn under Node with the real htm onto plain objects, as test_web_markdown does."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).parents[2] / "interfaces/web/src/flux_web/static"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

JS = r"""
const fs = require('fs'), vm = require('vm');
const [convPath, htmPath] = process.argv.slice(1);
const context = vm.createContext({});
const h = (tag, attrs, ...children) => typeof tag === 'function' ? tag({ ...(attrs || {}), children })
  : { tag, attrs: attrs || {}, children: children.flat(Infinity).filter(c => c != null && c !== false && c !== '') };
const find = (node, pred, out = []) => { if (node && typeof node === 'object') { if (pred(node)) out.push(node); (node.children || []).forEach(c => find(c, pred, out)); } return out; };
(async () => {
  const htm = new vm.SourceTextModule(fs.readFileSync(htmPath, 'utf8'), { context });
  await htm.link(() => { throw new Error('htm imports nothing'); }); await htm.evaluate();
  const html = htm.namespace.default.bind(h);
  const mocks = { '../view.js': { html, cx: (...a) => a.filter(Boolean).join(' '), Glyph: () => '' }, './Markdown.js': { Markdown: ({ text }) => text } };
  const mod = new vm.SourceTextModule(fs.readFileSync(convPath, 'utf8'), { context });
  await mod.link(s => new vm.SyntheticModule(Object.keys(mocks[s]), function () { for (const [n, v] of Object.entries(mocks[s])) this.setExport(n, v); }, { context }));
  await mod.evaluate();
  const { Conversation } = mod.namespace;
  const step = i => ({ k: 'tool', name: 'bash', call: `bash: step ${i}`, at: i });
  const all = [{ k: 'text', text: 'Reading the spec.' }, ...Array.from({ length: 30 }, (_x, i) => step(i))];
  // the live window: the last 10 steps of `n` (offset = the steps before it), the turn still at work
  const draw = (n, live = true, extra = []) => { const steps = [...all.slice(0, n), ...extra], shown = steps.slice(-10);
    return find(Conversation({ steps: shown, k: 'task7', offset: steps.length - shown.length, live }), x => x.tag === 'details' && /cv-work/.test(x.attrs.class)); };
  const out = {};
  const [first] = draw(20);
  first.attrs.onToggle({ currentTarget: { open: true } });           // a person opens the working box
  out.after_one_more = draw(21).map(d => d.attrs.open);               // the window rolled by one step
  out.after_ten_more = draw(31).map(d => d.attrs.open);
  out.ended = draw(31, false, [{ k: 'text', text: 'Done.' }]).map(d => d.attrs.open);   // its answer came: still open
  process.stdout.write(JSON.stringify(out));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


def test_an_opened_working_box_stays_open_while_the_turn_grows():
    got = subprocess.run(["node", "--experimental-vm-modules", "-e", JS, str(STATIC / "components/Conversation.js"),
                          str(STATIC / "vendor/htm.module.js")], capture_output=True, text=True, timeout=10)
    assert got.returncode == 0, got.stderr
    out = json.loads(got.stdout)
    assert out == {"after_one_more": [True], "after_ten_more": [True], "ended": [True]}, out
