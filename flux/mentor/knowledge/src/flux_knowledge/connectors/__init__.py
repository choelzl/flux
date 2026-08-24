"""Ingestion connectors: one module per source format. `adoc.py` is the only one so far
(RISC-V ISA manual's AsciiDoc source) — add a new module here per new source format, not a
branch inside an existing one, matching this repo's one-adapter-per-backend convention elsewhere
(evaluators/, frontends/).
"""

from __future__ import annotations
