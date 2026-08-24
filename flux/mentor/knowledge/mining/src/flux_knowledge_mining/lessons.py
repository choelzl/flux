"""What earlier work already settled, in a form that can change the NEXT decision.

An orchestrator that sees only the current frontier rediscovers the shape of a space every run.
It has no way to know that a whole family was already ruled out, because the reason a candidate
failed lives in the store and never reaches the prompt (docs/decisions.md D297).

Two sources, and the difference between them is stated wherever they are shown:

  CONCLUSIONS  drawn by a model FROM measurements, and marked as inference
  REFUSALS     grouped constraint failures, which ARE measurements and are the cheapest possible
               guidance: they say where not to spend the next round

Deliberately NOT the measured points. Those usually already reach the prompt as a frontier
digest, and repeating them spends a budget that is charged on every decision.
"""

from __future__ import annotations

from collections import Counter
import re
from pathlib import Path
from typing import Any

from .conclusions import CONCLUSION_KIND
from .store import FactStore


def fact_store_path(campaign_db: str | Path) -> str:
    """Where a study's lessons live: beside the campaign store they were drawn from, so moving or
    deleting a study takes its conclusions with it rather than leaving orphaned claims behind."""
    return str(Path(campaign_db).with_suffix(".facts.db"))


# Words too generic to identify a metric: every metric here is "per cycle" of something.
_GENERIC = {"max", "min", "words", "per", "cycle", "cycles", "total", "value", "mhz", "bits"}


def _restates(statement: str, already: list[str], *, overlap: float = 0.6) -> bool:
    """Whether a conclusion is about the same OBSERVATION as one already in the brief.

    Compared on the distinctive tokens — measured values and campaign ids — rather than on words.
    Two conclusions saying the same thing in different prose share almost no phrasing: the pair
    that motivated this overlapped only 0.39 by word bag, while both cited
    `534.6707230352188` and the same two campaigns. What makes them one observation is the
    evidence they point at, not the sentence built around it.

    A statement citing nothing distinctive is never treated as a restatement — with no evidence
    in common there is nothing to compare, and merging on prose alone would collapse genuinely
    different findings that happen to share vocabulary.
    """
    def marks(text: str) -> set[str]:
        return {m for m in re.findall(r"\b[0-9][0-9a-f.]{4,}\b", text.lower())}

    mine = marks(statement)
    if not mine:
        return False
    for prior in already:
        theirs = marks(prior)
        if theirs and len(mine & theirs) / len(mine | theirs) >= overlap:
            return True
    return False


def _mentions(statement: str, metric: str) -> bool:
    """Whether a statement is talking about this metric, by its distinctive words.

    Full-name matching is too strict — a conclusion says "throughput" where the metric is
    `max_throughput_words_per_cycle` — and matching anything is too loose, which is how a claim
    about throughput came to be refuted by `mux_bits`.
    """
    lowered = statement.lower()
    words = [w for w in metric.lower().split("_") if len(w) > 3 and w not in _GENERIC]
    return any(w in lowered for w in words)


# Universality, in the several shapes a model writes it. Each was observed in this repo's own
# store; the last three were added after the first version dropped two false conclusions and left
# a third saying the same thing in different words.
_UNIVERSAL_MISS = re.compile(
    r"\b(?:no (?:configuration|candidate|design|fabric)|none of|not (?:reached|met) by any|"
    r"unsatisfied by all|every (?:configuration|candidate|design) fail|"
    r"consistently fail|(?:design )?space (?:consistently )?fails|"
    r"never (?:reached|met|exceeded|achieved))", re.I)


def contradicted_by(statement: str, achieved: dict[str, float]) -> str | None:
    """Why a stored conclusion is no longer true, or None if the store still supports it.

    WHY THIS EXISTS (docs/decisions.md D329). A conclusion is an INFERENCE, drawn once from
    whatever the store held that day, and then fed to every later run as settled. Two of them in
    this repo's own store said:

        "the highest throughput observed is 21 words/cycle ... no configuration in either
         campaign reached the 28 minimum"
        "the maximum observed max_throughput_words_per_cycle is 16.0, which is below the 28.0
         minimum ... unsatisfied by all"

    Both were true when written, over two campaigns. The store now holds thirty-three, with about
    fifty fabrics at exactly 28. The orchestrator was being told, as settled fact and at the top of
    every prompt, that its goal was unreachable — and it was choosing what to do next against that.

    The D314 guards catch a conclusion that OVERREACHES from its evidence. They cannot catch one
    that was sound and has since been overtaken, because nothing re-reads a conclusion after the
    measurements that refute it arrive. This does: a claim about the largest value of a metric is
    checked against the largest value the store now holds.

    Deliberately narrow. It matches claims of the form "the (highest|maximum) <metric> is <value>"
    and refuses them when the store exceeds that value. It says nothing about causal claims, which
    remain unfalsifiable by arithmetic and are the reason conclusions are labelled INFERRED
    wherever they are shown.
    """
    for metric, best in achieved.items():
        short = metric.replace("_", " ")
        for pattern in (rf"(?:highest|maximum|max)\b[^.]*?{re.escape(short)}[^.]*?([\d.]+)",
                        rf"(?:highest|maximum|max)\b[^.]*?{re.escape(metric)}[^.]*?([\d.]+)"):
            match = re.search(pattern, statement, re.I)
            if not match:
                continue
            try:
                claimed = float(match.group(1))
            except ValueError:
                continue
            if best > claimed * 1.001:
                return (f"claims a maximum {short} of {claimed:g}; the store now holds "
                        f"{best:g}")

    # UNIVERSAL non-achievement: "no configuration reached the 28 minimum", "unsatisfied by all".
    # Distinguished from the particular claim "several candidates failed to meet 28", which is
    # true and must survive — the difference is whether the claim is about EVERY design or about
    # some of them, and only the universal one is refuted by a single counterexample.
    if _UNIVERSAL_MISS.search(statement):
        # Only metrics the statement actually TALKS ABOUT. Comparing a throughput claim against
        # `mux_bits` finds a counterexample every time and explains nothing — the verdict was
        # right and the reason was noise, which is its own kind of wrong in a record meant to be
        # audited.
        relevant = {m: v for m, v in achieved.items() if _mentions(statement, m)}
        for value in sorted({float(v) for v in re.findall(r"\b(\d+(?:\.\d+)?)\b", statement)}):
            for metric, best in relevant.items():
                if value > 0 and best >= value:
                    return (f"claims nothing reached {value:g}; the store holds "
                            f"{metric}={best:g}")
    return None


def achieved_maxima(campaign_db: str | Path) -> dict[str, float]:
    """The largest value the store actually holds for each metric a conclusion might bound."""
    import json
    import sqlite3

    out: dict[str, float] = {}
    try:
        con = sqlite3.connect(f"file:{campaign_db}?mode=ro", uri=True)
    except sqlite3.Error:
        return out
    try:
        rows = con.execute("select result_json from results").fetchall()
    except sqlite3.Error:
        return out
    finally:
        con.close()
    for (raw,) in rows:
        try:
            metrics = json.loads(raw).get("metrics", {})
        except ValueError:
            continue
        for name, estimate in metrics.items():
            value = (estimate or {}).get("value")
            if isinstance(value, (int, float)):
                out[name] = max(out.get(name, float("-inf")), float(value))
    return out


def lessons_digest(campaign_db: str | Path, facts: list[dict[str, Any]] | None = None, *,
                   max_chars: int = 1400, max_conclusions: int = 4,
                   max_refusals: int = 3) -> str:
    """A short brief for an orchestrator. Capped because this is sent on EVERY decision, and on
    local inference the prompt is the whole cost of a call.

    `facts` are mined facts (from `mine_knowledge`); pass None to include conclusions only.
    """
    lines: list[str] = []
    store_path = Path(fact_store_path(campaign_db))
    if store_path.exists():
        try:
            achieved = achieved_maxima(campaign_db)
            with FactStore(store_path) as store:
                kept = 0
                seen_statements: list[str] = []
                # NEWEST FIRST (docs/decisions.md D329). `facts()` returns `ORDER BY mined_at`
                # — oldest first — so taking the first four gave the orchestrator the four oldest
                # inferences in the store on every single call. In this repo's own store those
                # were drawn from two campaigns when it now holds thirty-three, and three of the
                # four asserted that the throughput target had never been reached by anything.
                # About fifty fabrics reach it. The accurate, recent conclusions were never shown.
                #
                # This is the fix; the staleness and restatement checks below are the belt to its
                # braces, because a recent conclusion can be overtaken too.
                for stored in reversed(store.facts(kind=CONCLUSION_KIND)):
                    if kept >= max_conclusions:
                        break
                    fact = stored.fact
                    stale = contradicted_by(fact["statement"], achieved)
                    if stale:
                        # Dropped from the brief, not deleted from the store: what was once
                        # believed is part of the record, and a conclusion overtaken by later
                        # measurement is exactly the thing worth being able to look back at.
                        continue
                    if _restates(fact["statement"], seen_statements):
                        # One observation, one slot. Three conclusions about the same 534.67 MHz
                        # ceiling filled three of the four places in this brief, and the trade-off
                        # findings that would actually inform a choice were pushed out. A model
                        # that draws the same conclusion three times has still learned one thing.
                        continue
                    seen_statements.append(fact["statement"])
                    kept += 1
                    lines.append(f"- {fact['statement']}")
                    actionable = (fact.get("evidence") or {}).get("actionable")
                    if actionable:
                        lines.append(f"  so: {actionable}")
        except Exception:  # noqa: BLE001 — no lessons yet is the normal first-run case
            pass
    # What the screen got WRONG, where a real placement has settled it (D305). This is the one
    # thing that tells an orchestrator which estimates to distrust, and therefore which fabric is
    # worth spending a real placement on next: without it, choosing what to measure is a guess.
    try:
        with FactStore(store_path) as store:
            for stored in store.facts(kind="estimator_bias")[:max_conclusions]:
                lines.append(f"- {stored.fact['statement']}")
    except Exception:  # noqa: BLE001
        pass
    for message, count in _refusal_groups(facts or [], max_refusals):
        lines.append(f"- {count} group(s) of candidates were refused: {message}")
    if not lines:
        return "(nothing yet: this is the first run against this store)"
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars] + "\n  (truncated)"


def _refusal_groups(facts: list[dict[str, Any]], limit: int) -> list[tuple[str, int]]:
    counts: Counter = Counter()
    for fact in facts:
        if fact.get("kind") == "refusal_pattern":
            message = str((fact.get("evidence") or {}).get("message", ""))[:70]
            if message:
                counts[message] += 1
    return counts.most_common(limit)
