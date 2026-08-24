"""Which of the tools that would produce the numbers are missing.

Without the tools every measurement fails quietly, and against a warm store the final table is
still fully populated from older rows, which looks like success (D288).
"""

from __future__ import annotations

import shutil


def missing_tools(required: dict[str, str]) -> dict[str, str]:
    """`{tool: what it is needed for}` for every one not on PATH."""
    return {tool: why for tool, why in required.items() if shutil.which(tool) is None}
