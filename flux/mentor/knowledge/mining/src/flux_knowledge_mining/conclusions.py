"""Conclusions: what the measurements MEAN, written by a model and kept checkable.

Mining answers "what happened" — this fabric, these numbers, this constraint refused 51 trials.
It cannot answer "why", because a causal claim is an inference and inference is not arithmetic.
The gap is real and was visible in this repo: the statement "arity, not switch count, sets the
critical path" earned its place in the design-guidance corpus, but a person wrote it by reading
measurements the miner had already produced. Nothing carried it back so a later run could use it.

A conclusion is therefore a DIFFERENT KIND of thing from a mined fact and is labelled as one:

  mined fact   computed from stored rows; wrong only if the arithmetic or the store is wrong
  conclusion   inferred from those rows by a model; can be plausible, confident and false

So the contract is stricter, not looser. Every conclusion must name the fact ids it was drawn
from, so a reader can go back to the evidence; must state what it does NOT establish, like every
other fact here; and is stored with the model that wrote it, because a conclusion from a 7B and a
conclusion from a frontier model are not the same claim. `supersedes` lets a later, better-evidenced
conclusion retire an earlier one without deleting the record of what was once believed.

NOT a mechanism for generating new physics. It summarises evidence already in the store, and a
conclusion whose cited facts do not exist is refused rather than stored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .mining import Fact
from .store import FactStore, fact_id

CONCLUSION_KIND = "conclusion"

_PROMPT = """You are reading measurements from a hardware design-space exploration and writing
down what they MEAN, so a later run does not have to rediscover it.

THE MEASUREMENTS (each has an id you must cite):
{facts}

Write at most {limit} conclusions. A conclusion is worth writing only if it would change what
someone tries next. Prefer one well-evidenced conclusion to three vague ones; writing none is a
valid answer if these measurements support none.

Each conclusion must be drawn ONLY from the measurements above. Do not use outside knowledge of
hardware, and do not restate a single measurement as if it were a conclusion: a conclusion joins
several measurements into something neither says alone.

EVERY NUMBER you write must appear in the measurements above. Do not round, rescale, convert
units, or compute a figure of your own: a conclusion whose numbers are not in the evidence is
refused, however reasonable it sounds. If you want to say something a number would support and
the number is not there, say it without the number.

Reply with a JSON array and nothing else. Each element:
{{"statement": "what these measurements show, in one sentence, in measured language",
  "because": "the specific pattern in the evidence that supports it",
  "not_established": "what these measurements do NOT license anyone to conclude",
  "from_facts": ["id", "id"],
  "actionable": "what a future search should do differently, or empty if nothing"}}"""


@dataclass
class Conclusion:
    statement: str
    because: str
    not_established: str
    from_facts: tuple[str, ...]
    actionable: str = ""
    model: str = "unknown"
    supersedes: tuple[str, ...] = field(default_factory=tuple)

    def to_fact(self, scope: str) -> Fact:
        """A conclusion travels as a Fact so every existing consumer renders it unchanged, with
        its inferred status inseparable from its statement rather than a field a reader might
        miss."""
        return Fact(
            kind=CONCLUSION_KIND,
            statement=f"{self.statement} (INFERRED by {self.model}, not measured)",
            evidence={"because": self.because, "actionable": self.actionable,
                      "from_facts": list(self.from_facts)},
            scope=scope,
            not_established=self.not_established,
            pointers={"from_facts": list(self.from_facts), "model": self.model,
                      "supersedes": list(self.supersedes)},
            caveats=("drawn by a model from measurements; the measurements are checkable and "
                     "this reading of them is not",),
        )


class InvalidConclusion(Exception):
    """A conclusion that cannot be stored: unparseable, missing its evidence, or citing facts
    that are not in the store."""


def draft_conclusions(facts: list[dict[str, Any]], ask, *, model: str = "unknown",
                      limit: int = 4, scope: str = "") -> list[Conclusion]:
    """Ask a model what the given facts mean. Returns [] rather than raising when the model
    declines or answers unusably: no conclusion is the correct outcome far more often than a
    forced one, and a search that cannot draw a lesson must still run.

    `facts` are dicts as `flux_mine_knowledge` returns them; each is given a short id the model
    cites, and a citation to an id it was not shown is refused.
    """
    if not facts:
        return []
    numbered = {f"F{i + 1}": f for i, f in enumerate(facts)}
    rendered = "\n".join(
        f"{fid}: [{f.get('kind', '?')}] {f.get('statement', '')}"
        + (f"\n     NOT established: {f['not_established']}" if f.get("not_established") else "")
        for fid, f in numbered.items())
    try:
        raw = ask(_PROMPT.format(facts=rendered, limit=limit))
    except Exception:  # noqa: BLE001 — an unreachable model costs a lesson, never the run
        return []
    shown = {fid: f"{f.get('statement', '')} {f.get('evidence', '')}"
             for fid, f in numbered.items()}
    refusals: list[str] = []
    drawn = parse_conclusions(raw, allowed_ids=set(numbered), model=model, limit=limit,
                              scope=scope, numbered_text=shown, rejected=refusals)
    for line in refusals:
        # Printed, not swallowed: a conclusion refused for citing numbers that are not in its
        # evidence is the most interesting thing the step can produce.
        print(f"    refused a conclusion: {line}")
    return drawn


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def unsupported_numbers(statement: str, evidence_texts: list[str]) -> set[str]:
    """Numbers in `statement` that appear in none of its cited evidence.

    Compared as PREFIXES so a conclusion may round: evidence of 534.6707230352188 supports
    "534.67" and "534". Bare small integers are exempt, because they are almost always counts the
    model is entitled to compute ("10 trials across two campaigns" is 4 + 6) rather than
    measurements it is quoting.
    """
    found = set()
    numbers = {n for text in evidence_texts for n in _NUMBER.findall(text)}
    for token in _NUMBER.findall(statement):
        if "." not in token and abs(int(token)) <= 100:
            continue
        if any(n.startswith(token) or token.startswith(n) for n in numbers):
            continue
        found.add(token)
    return found


def parse_conclusions(raw: str, *, allowed_ids: set[str], model: str = "unknown",
                      limit: int = 4, scope: str = "",
                      numbered_text: dict[str, str] | None = None,
                      rejected: list[str] | None = None) -> list[Conclusion]:
    """Validate a model's reply. Separated from the call so it is testable without a model.

    `numbered_text` maps each fact id to the text the model was shown, enabling the numeric check;
    omit it and only the structural gates apply. `rejected` collects one line per refusal, so a
    caller can report what was thrown away instead of silently returning fewer conclusions.
    """
    rejected = rejected if rejected is not None else []
    from flux_llm import strip_markdown_fence

    try:
        parsed = json.loads(strip_markdown_fence(raw).strip())
    except Exception:  # noqa: BLE001
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    out: list[Conclusion] = []
    for item in parsed[:limit]:
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement", "")).strip()
        cited = [str(x) for x in (item.get("from_facts") or []) if str(x) in allowed_ids]
        # Both gates are deliberate. An uncited conclusion cannot be checked, and a conclusion
        # with no stated limit is exactly the over-reach this package exists to prevent, so
        # neither is stored rather than being stored with a placeholder.
        if not statement or not cited or not str(item.get("not_established", "")).strip():
            continue
        unsupported = unsupported_numbers(statement, [numbered_text[c] for c in cited]) \
            if numbered_text else set()
        if unsupported:
            # The failure this exists for, observed on a real run: a conclusion asserting a peak
            # of "21 words/cycle" and values that were "discrete integers", against a store whose
            # measurements are 12.06, 13.51, 14.89 ... 18.85 and never 21. It cited real facts and
            # described them wrongly, which is the one failure mode that looks exactly like a
            # good conclusion. A number absent from the cited evidence is mechanically detectable,
            # so it is refused rather than stored beside measurements it contradicts.
            rejected.append(f"{statement[:60]}... cites {sorted(unsupported)}, not in its evidence")
            continue
        out.append(Conclusion(
            statement=statement,
            because=str(item.get("because", "")).strip(),
            not_established=str(item.get("not_established")).strip(),
            from_facts=tuple(cited),
            actionable=str(item.get("actionable", "")).strip(),
            model=model,
        ))
    return out


def store_conclusions(store: FactStore, conclusions: list[Conclusion], *, scope: str) -> list[str]:
    """Persist as facts of kind `conclusion`, idempotently by content like every other fact."""
    return store.put_facts([c.to_fact(scope) for c in conclusions])


def stored_conclusions(store: FactStore) -> list[dict[str, Any]]:
    return [s.fact for s in store.facts(kind=CONCLUSION_KIND)]
