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
    rendered = round_numbers("\n".join(
        f"{fid}: [{f.get('kind', '?')}] {f.get('statement', '')}"
        + (f"\n     NOT established: {f['not_established']}" if f.get("not_established") else "")
        for fid, f in numbered.items()))
    try:
        raw = ask(_PROMPT.format(facts=rendered, limit=limit))
    except Exception as exc:  # noqa: BLE001 — an unreachable model costs a lesson, never the run
        # SAID OUT LOUD. Returning [] in silence is indistinguishable from a model that had
        # nothing to conclude, and the two call for opposite responses. A real run timed out here
        # every time and reported only "drawing lessons...", so the step looked like it had run
        # and declined rather than never having finished (D314).
        print(f"    (no lessons: the model call failed — {type(exc).__name__}: {str(exc)[:70]})")
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
    if not drawn:
        return drawn
    # THE SECOND OPINION (D332). Everything above is mechanical — a number that is not in the
    # evidence, a word that overreaches. What no pattern catches is a claim that is well-formed,
    # cites real facts, and describes them wrongly. So the same evidence and the surviving claims
    # go to an independent pass, which reads what they MEAN.
    verdicts = cross_examine([c.statement for c in drawn], rendered, ask, model=model)
    kept = []
    for conclusion, (supported, why) in zip(drawn, verdicts):
        if supported:
            kept.append(conclusion)
        else:
            print(f"    a second pass rejected: {conclusion.statement[:60]}... — {why}")
    return kept


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

# Absolutes. A model is shown a SAMPLE of the store, so no sample licenses a claim about the whole
# space -- see `overreaching_claims`.
_ABSOLUTE = re.compile(
    r"\b(unreachable|unattainable|impossible|never|no fabric|no design|no configuration|"
    r"cannot be (?:achieved|reached|met)|not achievable|always|every (?:fabric|design)|"
    r"all (?:observed|tested|trials)|none of the)\b", re.I)


def balanced_evidence(facts: list[dict[str, Any]], *, limit: int = 18) -> list[dict[str, Any]]:
    """A sample of `facts` that represents every KIND present, rather than the first N of one.

    THE BUG THIS FIXES, from a real run. The caller ranked refusals first and truncated to 18. The
    store held 506 facts -- 261 refusals, 210 measured points, 35 frontier outcomes -- so the model
    was handed eighteen failures and not one success. It duly concluded that "a maximum throughput
    of 28 words per cycle is currently unreachable within the tested design space", while the same
    run's own results table listed thirty-one fabrics achieving exactly that. The conclusion was
    stored and fed to the next run.

    The model was not wrong about what it was shown. It was shown a biased sample, and a
    conclusion drawn from failures alone can only be pessimistic. So the quota is round-robin
    across kinds: every kind present contributes before any kind contributes twice.
    """
    if not facts:
        return []
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        by_kind.setdefault(str(fact.get("kind", "?")), []).append(fact)
    out: list[dict[str, Any]] = []
    while len(out) < limit and any(by_kind.values()):
        for kind in sorted(by_kind):
            if by_kind[kind] and len(out) < limit:
                out.append(by_kind[kind].pop(0))
    return out


def round_numbers(text: str, *, figures: int = 6) -> str:
    """Trim absurd precision from evidence BEFORE a model reads it.

    A conclusion quoted "534.6707230352188 MHz" and passed validation, because the number really
    was in the evidence: the check asks whether a number is supported, not whether it is sane.
    Thirteen significant figures on a place-and-route frequency is noise presented as measurement,
    and a model copies what it is given. Trimming at the source keeps the "every number must
    appear in the evidence" rule intact -- the check compares by prefix, so a rounded quote still
    matches its unrounded source.
    """
    def trim(match: re.Match) -> str:
        token = match.group(0)
        if "." not in token:
            return token
        whole = token.split(".")[0].lstrip("-")
        keep = max(0, figures - len(whole.lstrip("0") or ""))
        return f"{float(token):.{keep}f}".rstrip("0").rstrip(".") if keep else whole
    return _NUMBER.sub(trim, text)


def overreaching_claims(statement: str) -> set[str]:
    """Absolute words in a claim that a SAMPLE of the store cannot license.

    The model is shown at most a couple of dozen facts drawn from hundreds. Over that sample a
    statement like "X is unreachable" or "all observed trials failed" is not a conclusion but an
    extrapolation from what it happened to be handed, and it is stored with the same authority as
    a measurement. Sampled evidence supports claims about what WAS seen; it never supports a claim
    about what does not exist.

    Deliberately blunt. It will refuse the occasional true universal, which costs a lesson -- and
    the alternative cost a false one that fed forward into later runs.
    """
    return {m.group(0).lower() for m in _ABSOLUTE.finditer(statement)}


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


_EXAMINE_PROMPT = """You are checking someone else's work. Below are measurements, and claims
another model drew from them. For each claim, decide whether the MEASUREMENTS SHOWN SUPPORT IT.

THE MEASUREMENTS:
{facts}

THE CLAIMS:
{claims}

Judge each claim ONLY against the measurements above. A claim is unsupported if it states a number
the measurements do not show, describes a pattern they do not contain, or generalises to cases
they do not cover. A claim that is merely uninteresting is still supported. You are not being
asked whether the claim is plausible or whether it matches what you know about hardware — only
whether THESE measurements establish it.

Reply with a JSON array, one entry per claim, in order, and nothing else:
[{{"claim": 1, "supported": true, "why": "..."}}, ...]"""


def cross_examine(statements: list[str], evidence: str, ask,
                  *, model: str = "unknown") -> list[tuple[bool, str]]:
    """A SECOND model pass over claims a first model drew (docs/decisions.md D332).

    The pattern is `check_faithfulness`'s (D249): an independent examiner, a verdict per claim,
    and never a silent pass — an unparseable reply leaves every claim unjudged rather than
    quietly approved.

    WHY IT BELONGS HERE. Every conclusion this repo stores is written and checked by the same
    model, which is the arrangement `check_faithfulness` exists because nobody trusts. Three
    separate mechanical guards were added against false conclusions this session — a numeric
    check, an overreach check, a staleness check — and each caught the phrasing in front of it
    while the next paraphrase walked past. A judge reads what the claim MEANS, which is the thing
    regular expressions cannot.

    ONE call for all claims, not one per claim. Judging is a read of the same evidence, the local
    model costs about a minute per call, and a conclusions step that took four calls instead of
    one would be dropped for being slow — which is the surest way to have no check at all.

    Returns (supported, why) per claim, in order. On any failure every claim comes back
    supported with a reason saying the examination did not happen: this is an ADVISORY second
    opinion, and a judge that cannot run must not silently delete a study's findings.
    """
    from flux_llm import strip_markdown_fence

    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(statements, 1))
    unchecked = [(True, "not examined") for _ in statements]
    try:
        raw = ask(_EXAMINE_PROMPT.format(facts=evidence, claims=numbered))
        parsed = json.loads(strip_markdown_fence(raw).strip())
    except Exception:  # noqa: BLE001 — an examiner that cannot run is not a verdict
        return unchecked
    if not isinstance(parsed, list):
        return unchecked
    verdicts = list(unchecked)
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("claim", 0)) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(verdicts):
            verdicts[index] = (bool(entry.get("supported", True)),
                               str(entry.get("why", ""))[:160])
    return verdicts


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
        # An ENVELOPE, e.g. {"conclusions": [...]}, is the common shape and was silently
        # discarded: wrapped as a single item, it has no `statement`, so every conclusion inside
        # it was dropped and nothing was reported. A real run produced two accurate, well-cited
        # conclusions this way and stored neither (D314). Unwrap a lone list-valued key; a dict
        # that is itself one conclusion still works as before.
        lists = [v for v in parsed.values() if isinstance(v, list)]
        parsed = lists[0] if len(lists) == 1 and "statement" not in parsed else [parsed]
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
            # Reported, not skipped in silence. A malformed reply used to yield zero conclusions
            # and zero explanation, which reads exactly like "the model had nothing to say" and
            # sent the search looking in the wrong place entirely.
            missing = [name for name, ok in (("statement", statement), ("from_facts", cited),
                                             ("not_established",
                                              str(item.get("not_established", "")).strip()))
                       if not ok]
            rejected.append(f"{(statement or '(no statement)')[:60]}... missing {missing}")
            continue
        overreach = overreaching_claims(statement)
        if overreach:
            rejected.append(
                f"{statement[:60]}... claims {sorted(overreach)} from a sample of the store")
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
