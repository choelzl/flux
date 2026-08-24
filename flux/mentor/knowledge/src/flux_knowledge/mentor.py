"""What the mentor role hands a run, as declared SOURCES rather than hand-composed strings (D449).

Every study's knowledge was a per-problem string: NLU's `mentor_sections` concatenated a method
sheet, a library lookup and a record read-back by hand, wrapped each in its own `try`, and its
`prompt_prefix` ran the LIBRARY LOOKUP AGAIN -- once per generation turn, a BM25 pass over the
corpus each time, for text that cannot change during a run. macarray and the mapping study each
wrote a one-line version of the same thing.

So a problem DECLARES its sources and this assembles them:

    Mentor([Corpus("methods sheet", knowledge_text()),
            Library(queries=[...]),
            RecordReadback(stage="screen", metric="fmax_mhz", knobs=("style", "method"))])

`sections(state)` is what an observer browses (the mentor tab, D418m); `prefix(state, keys=...)`
is the static block a prompt leads with, so the model server's prefix cache is reused turn after
turn (D422). A STATIC source (a sheet, the library) is read once per Mentor and memoised; a
dynamic one (the record, the operator's notes) is re-read every time, because that is the half
that changes while the run is going. A source that fails contributes nothing to a prompt and says
so in the tab: knowledge is help, never a gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol, Sequence, runtime_checkable

__all__ = ["Corpus", "KnowledgeSource", "Library", "Mentor", "Mined", "Notes",
           "RecordReadback"]

#: What a section is cut to when the window runs out (D548), said out loud rather than silently.
CLIPPED = "\n  ... (clipped: the model's context window is full)"


@runtime_checkable
class KnowledgeSource(Protocol):
    """One thing the mentor knows. `key` addresses it (`Mentor.text`, `prefix(keys=...)`),
    `title` names it for a reader, `static` says whether it can change during a run."""

    key: str
    title: str
    static: bool

    def render(self, state: Any) -> str:
        """The text, or "" when there is nothing to say. Never raises for the caller's sake:
        `Mentor` catches, but a source that can answer partially should."""
        ...


@dataclass(frozen=True)
class Corpus:
    """A sheet the problem holds: methods, conventions, the contract a design must meet."""

    title: str
    text: str
    key: str = "sheet"
    static: bool = True

    def render(self, state: Any) -> str:
        return self.text


@dataclass(frozen=True)
class Library:
    """The operator's own papers, retrieved lexically (D407/D443): a few targeted lookups
    against the local index, deduplicated, source-cited, each excerpt WHOLE (D548: knowledge
    lives in the model's context window, not in a prompt budget -- nothing here is clipped to
    a character count; the mentor stops adding sources when the window's share is spent). No
    index, no block -- a run is the same without papers. `queries` may be a callable over the
    state for a problem whose queries depend on what it is working on."""

    queries: Sequence[str] | Callable[[Any], Sequence[str]]
    title: str = "knowledge: library excerpts"
    key: str = "library"
    static: bool = True
    standard_id: str | None = "library"
    k_per_query: int = 2

    def render(self, state: Any) -> str:
        from .context import library_context

        queries = self.queries(state) if callable(self.queries) else self.queries
        return library_context(queries, standard_id=self.standard_id, k_per_query=self.k_per_query)


@dataclass(frozen=True)
class RecordReadback:
    """The flywheel's read-back half (D445): what earlier runs of this campaign concluded and
    the head-to-head verdicts over `knobs` on `metric` at `stage`. "" on a fresh run."""

    stage: str
    metric: str
    knobs: tuple[str, ...] | None = None
    metric_label: str | None = None
    title: str = "extracted: record read-back and duels"
    key: str = "record"
    static: bool = False
    top: int = 5
    higher_is_better: bool = True
    conclusion: Callable[[dict[str, Any]], str | None] | None = None
    extra: Callable[[Any], list[str]] | None = None
    framing: str | None = None

    def render(self, state: Any) -> str:
        from flux_records.extract import READ_BACK_FRAMING, record_read_back

        return record_read_back(
            getattr(state, "records", None), stage=self.stage, metric=self.metric,
            knobs=self.knobs, metric_label=self.metric_label, top=self.top,
            higher_is_better=self.higher_is_better, conclusion=self.conclusion,
            extra=self.extra, framing=self.framing or READ_BACK_FRAMING)


@dataclass(frozen=True)
class Mined:
    """Knowledge's AI half (D462): what was EXTRACTED from the data this project already
    produced, rather than what a person fed in.

    `flux_records.mining` computes typed facts from the campaign store -- measured points,
    observed ratios, refusal patterns, frontier outcomes -- and `lessons_digest` adds the
    conclusions a model drew from those facts, dropping any that later measurement has
    overtaken and any that restate one already shown (D329). Every statement arrives with the
    boundary of what it does NOT establish, which is the whole reason mined knowledge is safe
    to put in a prompt.

    `db` empty means this run's own campaign store, so a problem switching this source on needs
    no configuration. STATIC by default and therefore mined once per run and memoised: mining
    reads the store with real queries, and D449's whole point was that an expensive lookup must
    not re-run every turn. The half that changes while a run is going is `RecordReadback`.
    """

    db: str = ""
    calibration: tuple[str, ...] = ()
    max_facts: int = 0            # D548: 0 = every fact mined (was 8: mined knowledge cut to a prompt)
    key: str = "mined"
    title: str = "Mined from this project's own data"
    static: bool = True

    def render(self, state: Any) -> str:
        db = self.db or str(getattr(getattr(state, "request", None), "db", "") or "")
        if not db:
            return ""
        from flux_records.mining import (lessons_digest, mine_knowledge,
                                            render_facts_for_prompt)

        mined = mine_knowledge([db], list(self.calibration) or None)
        blocks = []
        if mined.facts:
            # The statistical half: what the stored measurements THEMSELVES say, each with the
            # boundary of what it does not establish (D245).
            blocks.append(render_facts_for_prompt(mined.facts, max_facts=int(self.max_facts) or len(mined.facts)))
        # The model's half: conclusions it drew from those facts, minus any that later
        # measurement has overtaken or that restate one already shown (D329).
        digest = lessons_digest(db, [f.to_dict() for f in mined.facts]).strip()
        if digest and not digest.startswith("(nothing yet"):
            blocks.append("Drawn from those facts (INFERENCE, not measurement):\n" + digest)
        return "\n\n".join(b for b in blocks if b).strip()


@dataclass(frozen=True)
class Notes:
    """What the operator has typed, this run and earlier ones (D388/D403). The loop already
    puts fresh guidance in front of the model; this is the standing list a reader browses."""

    title: str = "operator notes"
    key: str = "notes"
    static: bool = False

    def render(self, state: Any) -> str:
        notes = [getattr(n, "text", str(n)) for n in getattr(state, "human_notes", [])]
        return "\n".join(f"* {n}" for n in notes)


@dataclass
class Mentor:
    """A problem's sources, assembled. Order is priority: the window's share is spent front to
    back, so a sheet a design cannot be written without is never crowded out by the library.

    The bound is the MODEL'S CONTEXT WINDOW, not a prompt budget (D548, Cedric: "knowledge
    should be in the context, not in the prompt"): `share` of the window the proposer states
    (`context_length()`, at its calibrated characters per token) may hold knowledge; a proposer
    that states no window, or no proposer at all, leaves the sources whole. `budget` (characters)
    overrides both when set."""

    sources: Sequence[KnowledgeSource]
    budget: int = 0            # characters; 0 = `share` of the model's window
    share: float = 0.5         # D548: the share of the window knowledge may hold
    _memo: dict[str, str] = field(default_factory=dict, repr=False)

    def source(self, key: str) -> KnowledgeSource | None:
        return next((s for s in self.sources if s.key == key), None)

    def text(self, key: str, state: Any) -> str:
        """One source's text, memoised when the source is static. `""` when it has nothing
        to say, when it is not declared, or when reading it failed."""
        src = self.source(key)
        if src is None:
            return ""
        got, _why = self._render(src, state)
        return got

    def _render(self, src: KnowledgeSource, state: Any) -> tuple[str, str]:
        """(text, why-it-is-missing). A failure is the source's problem, never the run's."""
        if src.static and src.key in self._memo:
            return self._memo[src.key], ""
        try:
            got = src.render(state) or ""
        except Exception as exc:  # noqa: BLE001 -- knowledge is help, not a gate
            return "", f"{type(exc).__name__}: {exc!s:.120}"
        if src.static:
            self._memo[src.key] = got
        return got, ""

    def sections(self, state: Any, *, keys: Iterable[str] | None = None, focus: str | None = None
                 ) -> list[tuple[str, str]]:
        """(title, text) per source that has something to say, in declaration order and under
        the budget -- what the mentor tab shows. A source that could not be read appears as
        `(unavailable: ...)`, because a reader comparing prompts needs to know the library was
        missing rather than empty."""
        wanted = list(keys) if keys is not None else None
        out: list[tuple[str, str]] = []
        left = self._budget(state)
        for src in self.sources:
            if wanted is not None and src.key not in wanted:
                continue
            got, why = self._render(src, state)
            if why:
                out.append((src.title, f"(unavailable: {why})"))
                continue
            if not got.strip():
                continue
            if len(got) > left:
                got = self._compact(src, got, max(0, left), state, focus)
            out.append((src.title, got))
            left -= len(got)
            if left <= 0:
                break
        return out

    def prefix(self, state: Any, *, keys: Iterable[str] | None = None,
               header: str | None = None, focus: str | None = None) -> str:
        """The same sections as ONE static prompt block, titles included so the model can tell
        a paper from a measurement. `keys` picks which sources belong in the static part: the
        record's read-back usually does not, since it changes as the run measures things and
        would break the server's prefix cache every turn (D422)."""
        blocks = [f"{title}\n{text}" for title, text in self.sections(state, keys=keys, focus=focus)
                  if not text.startswith("(unavailable:")]
        if not blocks:
            return ""
        return ((header + "\n\n") if header else "") + "\n\n".join(blocks)

    def _compact(self, src: KnowledgeSource, got: str, room: int, state: Any,
                 focus: str | None = None) -> str:
        """A source over the room left (D549, D550): repeats dropped first; then, given the
        run's focus (the part in hand), the paragraphs nearest it kept whole; then the model
        half when the run asks for it (`budget.compact: llm`) and a proposer is there -- one
        call per source, room and focus, memoised -- then the cut, said out loud."""
        from .compact import condense, densify, select_relevant

        n = len(got)
        dense = densify(got)
        if len(dense) <= room:
            return dense + f"\n  (compacted {n} -> {len(dense)} chars: repeats dropped)"
        picked = select_relevant(dense, room - 120, focus or "")
        if picked is not None:
            text, kept, total = picked
            return text + f"\n  (compacted {n} -> {len(text)} chars: the {kept} of {total} paragraphs nearest {focus!r} kept)"
        how = str(getattr(getattr(state, "request", None), "compact", "rules") or "rules")
        proposer = getattr(state, "proposer", None)
        if how == "llm" and proposer is not None:
            key = f"{src.key}@{room}@{focus or ''}"
            if key not in self._memo:
                short = condense(dense, room - 80, proposer, title=src.title)
                if short is not None:
                    self._memo[key] = short
            if key in self._memo:
                return self._memo[key] + f"\n  (compacted {n} -> {len(self._memo[key])} chars by the model)"
        return dense[:room] + CLIPPED

    def _budget(self, state: Any) -> int:
        """Characters of knowledge this run may carry: `budget` when set, else `share` of the
        proposer's context window at its characters per token; no window known, no bound."""
        if self.budget > 0:
            return self.budget
        proposer = getattr(state, "proposer", None)
        share = float(getattr(getattr(state, "request", None), "knowledge_share", self.share) or self.share)
        try:
            window = proposer.context_length() if proposer is not None else None
        except Exception:  # noqa: BLE001 -- a window the proposer cannot state is no bound
            window = None
        if not window:
            return 1 << 31
        return max(1000, int(window * share * float(getattr(proposer, "chars_per_token", 2.0) or 2.0)))
