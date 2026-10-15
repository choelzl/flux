"""Render agent Markdown under Node, with a timeout so malformed input cannot hang CI."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


STATIC = Path(__file__).parents[2] / "interfaces/web/src/flux_web/static"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

# D1095: the Markdown component (components/Markdown.js) drawn with the real htm onto plain objects -- a
# component called, an element {tag, attrs, children} -- so what it builds is read without a page
JS = r"""
const fs = require('fs'), vm = require('vm');
const [markdownPath, htmPath] = process.argv.slice(1);
const context = vm.createContext({ inputs: JSON.parse(fs.readFileSync(0, 'utf8')) });
const h = (tag, attrs, ...children) => typeof tag === 'function' ? tag({ ...(attrs || {}), children })
  : { tag, attrs, children: children.flat(Infinity).filter(c => c != null && c !== false && c !== '') };
(async () => {
  const htm = new vm.SourceTextModule(fs.readFileSync(htmPath, 'utf8'), { context });
  await htm.link(() => { throw new Error('htm imports nothing'); }); await htm.evaluate();
  const html = htm.namespace.default.bind(h);
  const mocks = { '../view.js': { html }, '../highlight.js': { guessLang: () => '', tokens: t => t ? [['', t]] : [] } };
  const mod = new vm.SourceTextModule(fs.readFileSync(markdownPath, 'utf8'), { context });
  await mod.link(specifier => new vm.SyntheticModule(Object.keys(mocks[specifier]), function () {
    for (const [name, value] of Object.entries(mocks[specifier])) this.setExport(name, value);
  }, { context }));
  await mod.evaluate();
  context.render = text => mod.namespace.Markdown({ text });
  const result = vm.runInContext('inputs.map(render)', context, { timeout: 1000 });
  process.stdout.write(JSON.stringify(result));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


def render(*inputs: str) -> list[dict]:
    result = subprocess.run(
        ["node", "--experimental-vm-modules", "-e", JS, str(STATIC / "components/Markdown.js"), str(STATIC / "vendor/htm.module.js")],
        input=json.dumps(inputs), capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def text(node: dict | str) -> str:
    return node if isinstance(node, str) else "".join(text(c) for c in node["children"])


def test_unfinished_table_lines_render_without_hanging():
    inputs = ["|", "| unfinished", "  | unfinished", "before\n| unfinished\nafter", "| one\n| two"]
    nodes = render(*inputs)
    assert [[text(b) for b in n["children"]] for n in nodes] == [
        ["|"], ["| unfinished"], ["  | unfinished"], ["before", "| unfinished after"], ["| one", "| two"],
    ]
    assert all(block["tag"] == "p" for n in nodes for block in n["children"])


def test_paragraph_fallback_preserves_following_markdown_blocks():
    node, = render("| unfinished\ncontinued **bold**\n# Heading\n- item\n> quote\n```text\ncode\n```\n| Col |\n| --- |\n| value |")
    blocks = node["children"]
    assert [b["tag"] for b in blocks] == ["p", "h3", "ul", "blockquote", "pre", "div"]
    assert [text(b) for b in blocks] == ["| unfinished continued bold", "Heading", "item", "quote", "code", "Colvalue"]
    assert blocks[0]["children"][1]["tag"] == "strong"
    assert blocks[-1]["children"][0]["tag"] == "table"
