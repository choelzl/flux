"""The site's CLI page (website/docs/reference/cli.md) is `flux --help` and every command's, as argparse
prints them at 80 columns with the design log's decision numbers left out. Regenerate it after changing
a command's arguments:

    python3 tests/unit/test_cli_reference.py     (from flux/, in the dev shell; it writes the page)
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

PAGE = Path(__file__).resolve().parents[3] / "website" / "docs" / "reference" / "cli.md"
_D = re.compile(r"\bD\d{2,4}(?:-D?\d{2,4})?(?: step)?")

HEAD = """# CLI

Every `flux` command's own help, as `flux COMMAND --help` prints it. The checks and measurements
Flux ships are a separate command, `fluxpreset` (see [Presets](presets.md)).
"""


def _clean(text: str | None) -> str | None:
    """Text without decision numbers: `(D825)` goes, `(D1236; the original kept)` keeps its words."""
    if not text:
        return text
    text = re.sub(r" ?\((?:D\d+(?:[-,] ?D?\d+)*(?: step)?)\)", "", text)
    text = re.sub(r"\(D\d+(?: step)?[;:,] ?", "(", text)
    return re.sub(r",? D\d+(?: step)?\)", ")", text)


def _walk(parser: argparse.ArgumentParser, path: list[str], out: list[tuple[list[str], argparse.ArgumentParser]]):
    parser.description, parser.epilog = _clean(parser.description), _clean(parser.epilog)
    out.append((path, parser))
    for action in parser._actions:
        action.help = _clean(action.help)
        if isinstance(action, argparse._SubParsersAction):
            for choice in action._choices_actions:
                choice.help = _clean(choice.help)
            for name, sub in action.choices.items():
                _walk(sub, [*path, name], out)


def page() -> str:
    from flux_cli.main import build_parser

    os.environ["COLUMNS"] = "80"
    found: list[tuple[list[str], argparse.ArgumentParser]] = []
    _walk(build_parser(), ["flux"], found)
    parts = [HEAD]
    for path, parser in found:
        parser.prog = " ".join(path)
        level = "##" if len(path) <= 2 else "###"
        parts.append(f"{level} {' '.join(path)}\n\n```text\n{parser.format_help().rstrip()}\n```\n")
    return "\n".join(parts)


def test_the_cli_page_is_the_commands_own_help():
    assert PAGE.read_text() == page(), "regenerate: python3 tests/unit/test_cli_reference.py"


def test_no_decision_number_reaches_the_page():
    assert not _D.search(page())


if __name__ == "__main__":
    PAGE.write_text(page())
