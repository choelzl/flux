"""A turn said one way wherever it is read (the Agents tab, Talk, Live): its tokens "14,062 in · 522 out" (format.js,
re-exported by ui.js), the engine's "tokens (reported)" read back into counts; the question's line saying when the agent
decides, not a bare "54 min" (components/Ask.js); the work between two answers a neutral line, only its failed commands
red (components/Conversation.js). Drawn under Node with the real htm onto plain objects, as test_web_conversation does."""

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
const [static_] = process.argv.slice(1);
const context = vm.createContext({ Date });
const h = (tag, attrs, ...children) => typeof tag === 'function' ? tag({ ...(attrs || {}), children })
  : { tag, attrs: attrs || {}, children: children.flat(Infinity).filter(c => c != null && c !== false && c !== '') };
const find = (node, pred, out = []) => { if (node && typeof node === 'object') { if (pred(node)) out.push(node); (node.children || []).forEach(c => find(c, pred, out)); } return out; };
const text = node => typeof node === 'string' ? node : (node.children || []).map(text).join('');
const load = async (file, mocks) => {
  const mod = new vm.SourceTextModule(fs.readFileSync(`${static_}/${file}`, 'utf8'), { context });
  await mod.link(s => { if (!mocks[s]) throw new Error(`${file} imports ${s}`);
    return new vm.SyntheticModule(Object.keys(mocks[s]), function () { for (const [n, v] of Object.entries(mocks[s])) this.setExport(n, v); }, { context }); });
  await mod.evaluate();
  return mod.namespace;
};
(async () => {
  const htm = new vm.SourceTextModule(fs.readFileSync(`${static_}/vendor/htm.module.js`, 'utf8'), { context });
  await htm.link(() => { throw new Error('htm imports nothing'); }); await htm.evaluate();
  const html = htm.namespace.default.bind(h);
  const f = await load('format.js', {});
  const out = {
    tokens: [f.tokensInOut(14062, 522), f.tokensInOut(14062, 522, true), f.tokensInOut(100, null), f.tokensInOut(1234567, 0, true)],
    reported: [f.reportedTokens('12,345 in, 678 out'), f.reportedTokens('12,345 in · 678 out'), f.reportedTokens('— in, 9 out'), f.reportedTokens('none')],
  };
  const view = { html, cx: (...a) => a.filter(Boolean).join(' '), Glyph: () => '', Empty: ({ text }) => text, useEffect: () => {}, useState: v => [v, () => {}] };
  const ask = await load('components/Ask.js', { '../view.js': view, '../format.js': f, '../ui.js': { toast: () => {} }, './Act.js': { Act: () => '' } });
  const q = { question: 'Signed or unsigned?', asked: Date.now() / 1000 - 60, wait_s: 55 * 60 };
  out.waits = [ask.waits(q), ask.waits(q, true), ask.waits({ ...q, wait_s: 1 }), ask.waits({ question: 'x' })];
  const cv = await load('components/Conversation.js', { '../view.js': view, './Markdown.js': { Markdown: ({ text }) => text } });
  const steps = [{ k: 'tool', name: 'bash', call: 'bash: ls', out: 'a', at: 0 }, { k: 'tool', name: 'bash', call: 'bash: make', out: 'x', error: true, at: 5 },
    { k: 'text', text: 'Done.', at: 25 }];
  const said = find(cv.Conversation({ steps, k: 't' }), x => x.attrs.class === 'cv-work-said')[0];
  out.work = [text(said), find(said, x => x.attrs.class === 'bad').map(text)];
  process.stdout.write(JSON.stringify(out));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


def test_a_turn_is_said_one_way_and_only_what_failed_is_red():
    got = subprocess.run(["node", "--experimental-vm-modules", "-e", JS, str(STATIC)], capture_output=True, text=True, timeout=10)
    assert got.returncode == 0, got.stderr
    out = json.loads(got.stdout)
    assert out["tokens"] == ["14,062 in · 522 out", "14k in · 522 out", "100 in · — out", "1.23M in · 0 out"], out
    assert out["reported"] == [[12345, 678], [12345, 678], [None, 9], None], out
    assert out["waits"] == ["decides in 54 min", "decides itself in 54 min", "decided itself", None], out
    assert out["work"] == ["2 steps · 2 commands · 1 failed · 25s", ["1 failed"]], out
