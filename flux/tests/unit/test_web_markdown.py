"""Render agent Markdown under Node, with a timeout so malformed input cannot hang CI."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


LOOPS = Path(__file__).parents[2] / "interfaces/web/src/flux_web/static/loops.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

JS = r"""
const fs = require('fs'), vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const context = vm.createContext({ inputs: JSON.parse(fs.readFileSync(0, 'utf8')) });
const h = (tag, attrs, ...children) => ({ tag, attrs, children: children.flat(Infinity) });
const mocks = { h, codeBlock: (text, lang, cls) => h('pre', { lang, class: cls }, text) };
const imports = new Map([...source.matchAll(/^import \{([^}]+)\} from "([^"]+)";/gm)]
  .map(m => [m[2], m[1].split(',').map(n => n.trim())]));
(async () => {
  const mod = new vm.SourceTextModule(source, { context });
  await mod.link(specifier => new vm.SyntheticModule(imports.get(specifier), function () {
    for (const name of imports.get(specifier)) this.setExport(name, mocks[name]);
  }, { context }));
  await mod.evaluate();
  context.render = mod.namespace.markdown;
  const result = vm.runInContext('inputs.map(render)', context, { timeout: 1000 });
  process.stdout.write(JSON.stringify(result));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


def render(*inputs: str) -> list[dict]:
    result = subprocess.run(
        ["node", "--experimental-vm-modules", "-e", JS, str(LOOPS)],
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
