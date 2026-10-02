"""The configurator's document side (D686): an existing document as the crafter reads it back
-- as written (`yaml.safe_load`) and as the loader takes it (`TaskSpec.to_dict`), neither
running any of its code -- and the configurator's YAML saved with the keys it keeps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

KEPT_HEADER = "# Kept as written: the configurator does not edit these (D686).\n"


def views(path: Path) -> dict[str, Any]:
    """{raw, normal, error}: `normal` is None when the loader refuses the document; `raw` is None
    too when it is not YAML (or JSON) at all -- said as the loader says it, never raised (D710)."""
    text = path.read_text()
    try:
        raw = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError):
        raw = None
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("the document is not a mapping of keys")
    try:
        from flux_loop import load_task

        normal, error = load_task(str(path)).to_dict(), ""
    except Exception as exc:  # noqa: BLE001 -- the configurator shows why and reads what it can
        normal, error = None, str(exc)
    return {"raw": raw, "normal": normal, "error": error}


def merged(text: str, raw: dict[str, Any], kept: list[str]) -> str:
    """The configurator's YAML, then every kept key exactly as the old document had it."""
    new = yaml.safe_load(text) or {}
    if not isinstance(new, dict):
        raise ValueError("the configurator's document is not a mapping")
    # D775: a kept part of `flow` (flow.test, flow.measure, ...) the configurator writes back itself, in place
    keep = {k: raw[k] for k in kept if k in raw and not k.startswith("flow")}
    clash = sorted(set(keep) & set(new))
    if clash:
        raise ValueError(f"the configurator wrote {', '.join(clash)}, which it was to keep")
    if not keep:
        return text
    return text.rstrip("\n") + "\n\n" + KEPT_HEADER + yaml.safe_dump(keep, sort_keys=False, allow_unicode=True, width=100)
