"""The invention round: design a NEW L2 prefetcher, build it, and measure whether it wins.

The code-space stage of the world (review 2 step R3, docs/decisions.md D539: it was the CHIA
node `flux_invent_prefetcher`, an engine of its own outside the loop; it is the world's now,
called from `World.prepare` before the simulator is built, so a design invented THIS run is on
THIS run's compose menu). Everything else in the prefetcher study searches parameters of
prefetchers somebody already wrote; this writes one. It is affordable for an unusual reason: a
ChampSim rebuild costs seconds while one evaluation costs three simulations of about six
minutes, so the compiler is nearly free feedback and a candidate can be repaired several times
for less than the price of measuring it once.

THE ROUND, and which parts are model-authored:

  reference  the best stack the kept library's own records claim, RE-MEASURED    <- the verdict
  generate   a model writes ONE header containing the whole class                <- model
  install    the .cc stub, the dispatch branch, the knob declarations            <- mechanical
  build      real g++ through the simulator's own Makefile                       <- the verdict
  repair     the FIRST compiler diagnostic, fed back, bounded                    <- model
  measure    the same evaluator and traces the rest of the study uses            <- the other verdict
  repair     a design that ran but emitted nothing: the counters' diagnosis, once <- model

Registration is mechanical on purpose (D48's rule for RTL, applied unchanged): a model editing a
300-line dispatch it did not write has many ways to break every OTHER prefetcher in the build.

WHAT COUNTS AS SUCCESS is not "it compiles". Any address a prefetcher emits is legal and the cache
absorbs it, so there are no test vectors to pass -- a design that builds and runs can still be
useless or actively harmful (`bop` and `dspatch` are both SLOWER than no prefetcher at all). The
verdict is measured geomean IPC speedup against the same no-prefetcher baseline everything else
is quoted against. What is still the round's own and not the loop's: its designs are not loop
candidates yet (a later step); the numbers it keeps are the screen's, and the loop's confirm
stage is what quotes a design that earned the menu.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .invented import DEFAULT_REFERENCE_STACK, INVENTED_DIR, keep, next_name, reference_stacks


def _ipc_and_stats(binary: Path, designs: list[tuple[str, list[str], dict[str, int]]],
                   traces: dict[str, Path], warmup: int, simulation: int,
                   parallelism: int) -> dict[str, dict[str, Any]]:
    """Measure several stacks on every trace in ONE wave; return `{label: {bench: run}}`.

    One wave, because a wave costs what its slowest member costs: measuring "alone" and then
    "with the stack" as two waves paid twice for the same six-minute floor. Through
    `local_measure_batch`, so the invention round and the study share one definition of how a
    batch of simulations is run.
    """
    from . import measure
    from .config import DEFAULT
    from .partners import defaults_for_stack

    jobs, slots = [], []
    for label, types, knobs in designs:
        merged = {**defaults_for_stack(tuple(types)), **knobs}
        for bench, trace in traces.items():
            jobs.append({"config": DEFAULT, "trace": str(trace), "types": list(types),
                         "warmup": warmup, "simulation": simulation, "partner_knobs": merged,
                         "timeout_s": 3600})
            slots.append((label, bench))
    got = measure.local_measure_batch(jobs, parallelism=parallelism, binary=str(binary))
    out: dict[str, dict[str, Any]] = {label: {} for label, _, _ in designs}
    for (label, bench), run in zip(slots, got):
        if "error" not in run:
            out[label][bench] = run
    return out


def _geomean_of(runs: dict[str, Any], base: dict[str, Any], benchmarks) -> float | None:
    from .objective import geomean

    if any(b not in runs or b not in base for b in benchmarks):
        return None
    return geomean([runs[b]["ipc"] / base[b]["ipc"] for b in benchmarks])


def _why_inert(runs: dict[str, Any]) -> str:
    """Say what an inert design DID, from the simulator's own counters.

    "Exactly 1.0000" only says nothing changed. The counters say why, and the two common cases
    want different fixes: zero prefetches ISSUED means the emit path never executes (a confidence
    that never arms, a delta compared against an address, a table keyed by PC that a stream of
    twenty thousand PCs clears every few accesses); prefetches issued but none USEFUL means the
    addresses are wrong (a line number pushed where a byte address was expected, or a stride
    applied to the wrong base).
    """
    issued = sum(r.get("stats", {}).get("L2C_prefetch_issued", 0.0) for r in runs.values())
    useful = sum(r.get("stats", {}).get("L2C_prefetch_useful", 0.0) for r in runs.values())
    if issued == 0:
        return ("issued ZERO prefetches -- the emit path never executes. Check the confidence "
                "threshold is reachable, that you store the last LINE per entry and compute "
                "delta = line - last (not line minus a delta), and that the table is not being "
                "cleared every few accesses by a stream of ~20,000 distinct PCs")
    if useful == 0:
        return (f"issued {issued:.0f} prefetches and NONE were useful -- the addresses are wrong. "
                "pref_addr takes full BYTE addresses: push (line << LOG2_BLOCK_SIZE), and apply "
                "the stride to the current line, not to a stale one")
    return f"issued {issued:.0f} prefetches, {useful:.0f} useful, but IPC did not move"


def _reference_candidates(library, explicit: list[str] | None) -> list[list[str]]:
    """The stacks worth measuring as the reference: what the kept records claim is best, or
    the one the caller names (`reference_stacks` in `invented.py` is the rule)."""
    if explicit:
        return [list(explicit)]
    return [list(s) for s in reference_stacks(list(library))]


def _compile(name: str, proposal, *, tree_src: Path, library, scratch: Path, tag: str,
             ask: Callable[[str], str], repair_attempts: int, say: Callable[[str], None]):
    """Install the library and the design into a fresh tree, build, repair compile errors.

    The kept library goes in beside the candidate because the reference stack may name a kept
    design: a candidate is measured beside the stack it is asked to beat, and that stack has to
    exist in the binary it runs on. Returns `(proposal, result)`; `result` is None when nothing
    was built, and `result.ok` says whether the last build succeeded.
    """
    from flux_llm import refine

    from flux_codegen_champsim_prefetcher import (
        build, install, parse_proposal, repair_prompt, stage_tree,
    )

    # The bounded ask-check-repair round is shared (D450, `flux_llm.refine`); what is this
    # function's own is the staging, the install and the build that IS the check.
    held: dict[str, Any] = {"result": None, "round": 0}

    def compiles(candidate) -> Any:
        held["round"] += 1
        tree = stage_tree(tree_src, scratch / f"{tag}{held['round']}")
        for design in library:
            install(design.name, design.header, design.knobs, tree)
        install(name, candidate.header, candidate.knobs, tree)
        held["result"] = build(tree)
        return "" if held["result"].ok else (held["result"].first_error, "compile error")

    def parse(reply: str):
        got = parse_proposal(name, reply)
        if got is None:
            say("  the repair returned no header")
        return got

    got = refine(
        proposal, check=compiles, ask=ask, parse=parse,
        repair=lambda candidate, why, _reply: repair_prompt(candidate, why),
        attempts=max(1, repair_attempts),
        on_attempt=lambda n, _c, why: say(f"  compile failed ({n}/{repair_attempts}): "
                                          f"{why[:110]}"))
    result = held["result"]
    if got.ok:
        say(f"  built in {result.elapsed_s:.0f}s"
            + (f" (after {got.attempts - 1} repair(s))" if got.attempts > 1 else ""))
    return got.value, result


def _screen(binary: Path, name: str, knobs: dict[str, int], stack: list[str], *, traces,
            warmup: int, simulation: int, parallelism: int, base, benchmarks
            ) -> dict[str, Any] | None:
    """Measure a design alone and beside the stack, in one wave. None if it did not run."""
    runs = _ipc_and_stats(binary, [("alone", [name], knobs), ("paired", stack + [name], knobs)],
                          traces, warmup, simulation, parallelism)
    alone, paired = runs["alone"], runs["paired"]
    if len(alone) != len(benchmarks):
        return None
    solo = _geomean_of(alone, base, benchmarks)
    # EXACTLY 1.0 means the design changed nothing: it either issued no prefetches or only
    # ones the cache already held. That is a different failure from "a worse idea" -- it is
    # almost always a logic bug (a confidence counter that never arms, a table that clears
    # every access) and it is handed back for a fix rather than shown as a near miss.
    inert = all(abs(alone[b]["ipc"] / base[b]["ipc"] - 1.0) < 1e-6 for b in benchmarks)
    return {"solo": solo, "with_stack": _geomean_of(paired, base, benchmarks), "inert": inert,
            "why_inert": _why_inert(alone) if inert else ""}


def invent(
    rounds: int = 4,
    *,
    ask: Callable[[str], str] | None = None,
    say: Callable[[str], None] | None = None,
    repair_attempts: int = 3,
    inert_repairs: int = 1,
    problem: str | None = None,
    traces_dir: str | None = None,
    source_tree: str | None = None,
    workers: int = 12,
    llm_model: str | None = None,
    num_predict: int = 2400,
    scratch_root: str | None = None,
    keep_dir: str | None = None,
    confirm_best: bool = True,
    reference_stack: list[str] | None = None,
) -> dict[str, Any]:
    """Invent L2 prefetchers, compile them, and measure them against the study's own baseline.

    Each round asks for a design that beats the best thing measured so far -- starting from the
    tallest stack the kept library's records claim, re-measured. Designs that fail to compile
    are repaired from the compiler's first diagnostic, up to `repair_attempts` times; designs
    that compile but emit nothing are handed the counters' diagnosis, up to `inert_repairs`
    times; designs that run are measured on the cheap stage and ranked. Only a design that beat
    the reference on the screen is confirmed at full length, and D351's lesson stands: a
    screened number orders candidates and must not be quoted.

    `ask` is the model (`prompt -> text`): the loop's proposer when the world calls this, or
    the one built from `llm_model` when a node does. `say` is the log.

    Returns every attempt with what happened to it, so a run that produced nothing usable says
    what went wrong rather than reporting an empty list.
    """
    from flux_codegen_champsim_prefetcher import (
        build, build_prompt, check_name, inert_repair_prompt, install, parse_proposal,
        stage_tree, truncation_reason, unbuildable_reason,
    )
    from flux_evaluator_champsim_bingo import resolve_binary, resolve_source_tree

    from . import invented as inv
    from .objective import BENCHMARKS
    from .staging import scratch_root as default_scratch, stage_traces

    started = time.monotonic()
    log: list[str] = []
    parallelism = max(1, int(workers))

    def _say(message: str) -> None:
        log.append(message)
        if say is not None:
            say(message)
        else:
            print(message, flush=True)

    # WHERE THE SOURCE GOES: anything that compiled is written out, winner or not (`keep`).
    kept: Path | None = Path(keep_dir) if keep_dir else INVENTED_DIR
    try:
        kept.mkdir(parents=True, exist_ok=True)
    except OSError:
        kept = None

    tree_src = Path(source_tree) if source_tree else resolve_source_tree()
    from .flow import DEFAULT_TRACES

    root = Path(traces_dir) if traces_dir else DEFAULT_TRACES
    missing = [b for b in BENCHMARKS if not (root / f"{b}.simout_champsim.gz").is_file()]
    if missing:
        return {"error": f"no trace for {missing} under {root}", "log": log}
    traces = stage_traces({b: root / f"{b}.simout_champsim.gz" for b in BENCHMARKS}, log=_say)
    from flux_evaluator_champsim_bingo.adapter import DECIDE, SCREEN

    warmup, simulation = SCREEN
    if ask is None:
        # A C++ header needs more output than this repository's usual JSON proposal: the
        # first live run truncated all three designs at the 1200-token default; the prompt
        # asks for under 70 lines instead, which is what the worked example needs.
        import flux_llm

        proposer = flux_llm.OpenAIChatProposer(llm_model, num_predict=num_predict)
        ask = lambda prompt: proposer.propose(prompt).text  # noqa: E731
    scratch_dir = Path(scratch_root) if scratch_root else (default_scratch() or Path("/tmp"))

    # THE REFERENCE IS THE BEST STACK THE LIBRARY CAN VOUCH FOR, re-measured (D360, D361):
    # the stock stack plus, for each kept design, the stack it was measured beside with it
    # included; measured in ONE wave on a simulator that carries the whole library, and the
    # tallest is the number to beat.
    library = inv.library(kept) if kept else []
    reference_binary = resolve_binary()
    if library:
        built = inv.build_binary(library, source_tree=tree_src, cache_dir=scratch_dir, log=_say)
        if built is not None:
            reference_binary = built
            inv.register(library)
        else:
            _say("  the library did not build; the reference is the stock stack")
            library = []
    candidates = _reference_candidates(library, reference_stack)
    _say(f"reference: measuring {len(candidates)} stack(s) on {reference_binary.name}")
    runs = _ipc_and_stats(reference_binary, [("none", [], {})]
                          + [("+".join(c), c, {}) for c in candidates],
                          traces, warmup, simulation, parallelism)
    base = runs["none"]
    measured = {"+".join(c): _geomean_of(runs["+".join(c)], base, BENCHMARKS) for c in candidates}
    measured = {k: v for k, v in measured.items() if v is not None}
    if not measured:
        return {"error": "could not measure the no-prefetcher baseline or any reference stack; "
                         "nothing to compare inventions against", "log": log}
    for label, value in measured.items():
        _say(f"  {label}: geomean {value:.4f}")
    best_label = max(measured, key=measured.get)
    stack = best_label.split("+")
    beat = measured[best_label]
    ref = runs[best_label]
    _say(f"  {best_label} reaches geomean {beat:.4f}; that is the number to beat")

    # THE EVIDENCE: what the traces look like, and what the reference stack still misses on
    # them; the dynamic half comes free from the reference run's counters.
    from .profile import dynamic_profile, profile_text, static_profile

    trace_profile = ""
    try:
        static = [static_profile(traces[b]) for b in BENCHMARKS]
        dynamic = [dynamic_profile(ref[b]["stats"], b, best_label) for b in BENCHMARKS]
        trace_profile = profile_text(static, dynamic)
        for line in trace_profile.splitlines():
            if line.startswith("  *"):
                _say(f"  profile: {line[4:120]}")
    except Exception as exc:                                              # noqa: BLE001
        _say(f"  (no trace profile: {type(exc).__name__}: {exc!s:.80})")

    attempts: list[dict[str, Any]] = []
    history: list[tuple[str, str, float]] = []
    best_name, best_geomean = best_label, beat
    screen_kw = dict(traces=traces, warmup=warmup, simulation=simulation,
                     parallelism=parallelism, base=base, benchmarks=BENCHMARKS)
    compile_kw = dict(tree_src=tree_src, library=library, ask=ask,
                      repair_attempts=repair_attempts, say=_say)

    # NAMES MUST NOT COLLIDE WITH WHAT IS KEPT (`next_name`): numbering continues past every
    # design ever kept, whether or not it still earns a menu slot.
    first_name = next_name(kept) if kept is not None else "invented1"
    first = int(first_name[len("invented"):])
    for index in range(first, first + max(1, rounds)):
        name = f"invented{index}"
        try:
            check_name(name)
        except Exception as exc:                                          # noqa: BLE001
            attempts.append({"name": name, "outcome": "bad name", "detail": str(exc)})
            continue

        _say(f"\nround {index - first + 1}/{rounds}: asking for `{name}` to beat {best_name} "
             f"({best_geomean:.4f})")
        reply = ask(build_prompt(name, beat=best_name, beat_geomean=best_geomean,
                                 already_tried=history, problem=problem,
                                 trace_profile=trace_profile or None))
        proposal = parse_proposal(name, reply)
        if proposal is None:
            why = truncation_reason(reply) or "no C++ header in the reply"
            _say(f"  {why}")
            attempts.append({"name": name, "outcome": "no header", "detail": why,
                             "reply_chars": len(reply)})
            history.append((name, why, 0.0))
            continue
        _say(f"  idea: {proposal.rationale[:140]}")
        blocked = unbuildable_reason(proposal)
        if blocked:
            # Caught in microseconds rather than after a sixty-second compile plus a whole
            # generation spent repairing it.
            _say(f"  cannot build: {blocked}")
            attempts.append({"name": name, "outcome": "rejected before building",
                             "detail": blocked, "idea": proposal.rationale})
            history.append((name, blocked, 0.0))
            continue

        with tempfile.TemporaryDirectory(dir=scratch_root or None) as scratch:
            proposal, result = _compile(name, proposal, scratch=Path(scratch), tag="tree",
                                        **compile_kw)
            if result is None or not result.ok:
                attempts.append({"name": name, "outcome": "did not compile",
                                 "detail": (result.first_error if result else ""),
                                 "idea": proposal.rationale})
                history.append((name, "did not compile", 0.0))
                continue

            # BOTH ways it can win: measured alone, and measured beside the stack -- the L2
            # slot runs several prefetchers at once, and on these traces composition was worth
            # roughly eight times what parameter tuning was.
            screened = _screen(result.binary, name, proposal.knobs, stack, **screen_kw)

            # AN INERT DESIGN IS HANDED BACK, not written off: the counters say which of two
            # bugs it has, and either is a few lines' fix. Bounded, because a design still
            # inert after a fix is a lost idea.
            logic_repairs = 0
            while screened is not None and screened["inert"] and logic_repairs < inert_repairs:
                logic_repairs += 1
                _say(f"  inert: {screened['why_inert'][:150]}")
                _say(f"  asking for a logic fix ({logic_repairs}/{inert_repairs})")
                repaired = parse_proposal(name, ask(inert_repair_prompt(proposal,
                                                                        screened["why_inert"])))
                if repaired is None:
                    _say("  the fix returned no header")
                    break
                blocked = unbuildable_reason(repaired)
                if blocked:
                    _say(f"  the fix cannot build: {blocked}")
                    break
                repaired, fixed = _compile(name, repaired, scratch=Path(scratch),
                                           tag=f"fix{logic_repairs}-", **compile_kw)
                if fixed is None or not fixed.ok:
                    _say("  the fix did not compile; keeping the version that ran")
                    break
                rescreened = _screen(fixed.binary, name, repaired.knobs, stack, **screen_kw)
                if rescreened is None:
                    _say("  the fix crashed at run time; keeping the version that ran")
                    break
                proposal, screened = repaired, rescreened
                if not screened["inert"]:
                    _say("  the fix emits: measured again")

        if screened is None:
            _say("  built, but did not run on every trace")
            attempts.append({"name": name, "outcome": "crashed or timed out",
                             "idea": proposal.rationale})
            history.append((name, "built but crashed at run time", 0.0))
            continue

        solo, with_stack, inert = screened["solo"], screened["with_stack"], screened["inert"]
        why_inert = screened["why_inert"]
        got = max([solo] + ([with_stack] if with_stack else []))
        how = "alone" if with_stack is None or solo >= with_stack else f"with {best_label}"
        if inert:
            _say(f"  still inert: {why_inert[:150]}")
        _say(f"  geomean {solo:.4f} alone" +
             (f", {with_stack:.4f} with {best_label}" if with_stack
              else f", crashed beside {best_label}") +
             f" -- {'BEATS ' + best_name if got > best_geomean else 'does not beat ' + best_name}")
        if kept is not None:
            try:
                path = keep(kept, name, proposal.header, {
                    "knobs": proposal.knobs, "idea": proposal.rationale,
                    "geomean_alone": round(solo, 5),
                    "geomean_with_stack": round(with_stack, 5) if with_stack else None,
                    "reference_stack": stack, "reference_geomean": round(beat, 5),
                    "logic_repairs": logic_repairs,
                    "stage": "screen", "warmup": warmup, "simulation": simulation})
                _say(f"  kept: {path}")
            except OSError as exc:                                        # noqa: BLE001
                _say(f"  (could not keep the source: {exc})")
        attempts.append({"name": name, "outcome": "measured",
                         "geomean_speedup": round(got, 5),
                         "source": str(kept / f"{name}.h") if kept else None,
                         "geomean_alone": round(solo, 5),
                         "geomean_with_stack": round(with_stack, 5) if with_stack else None,
                         "reference_stack": stack,
                         "best_as": how, "beats_reference": got > beat, "inert": inert,
                         "logic_repairs": logic_repairs,
                         "idea": proposal.rationale,
                         "knobs": proposal.knobs, "header": proposal.header})
        note = (f"INERT, a logic bug not a bad idea: {why_inert}"
                if inert else (proposal.rationale[:70] or "no rationale") + f" [{how}]")
        history.append((name, note, got))
        if got > best_geomean:
            best_name, best_geomean = name, got

    winners = [a for a in attempts if a.get("outcome") == "measured"]

    # CONFIRM THE WINNER, or the round is just climbing a proxy (D351): only a champion that
    # BEAT the reference on the screen earns six minutes of confirmation, and never an inert one.
    confirmation: dict[str, Any] | None = None
    contenders = [a for a in winners if not a.get("inert") and a["geomean_speedup"] > beat]
    if confirm_best and winners and not contenders:
        _say("\nnothing beat the reference on the screen; skipping confirmation")
    if confirm_best and contenders:
        champion = max(contenders, key=lambda a: a["geomean_speedup"])
        name = champion["name"]
        _say(f"\nconfirming `{name}` at full length ({DECIDE[0]:,} + {DECIDE[1]:,} instructions)")
        with tempfile.TemporaryDirectory(dir=scratch_root or None) as scratch:
            tree = stage_tree(tree_src, Path(scratch) / "confirm")
            for design in library:
                install(design.name, design.header, design.knobs, tree)
            install(name, champion["header"], champion["knobs"], tree)
            rebuilt = build(tree)
            if not rebuilt.ok:
                _say(f"  could not rebuild it: {rebuilt.first_error[:110]}")
            else:
                full = _ipc_and_stats(rebuilt.binary, [
                    ("none", [], {}), ("stack", stack, {}), ("alone", [name], champion["knobs"]),
                    ("paired", stack + [name], champion["knobs"])],
                    traces, *DECIDE, parallelism)
                full_base, full_stack = full["none"], full["stack"]
                full_alone, full_pair = full["alone"], full["paired"]
                complete = all(len(x) == len(BENCHMARKS)
                               for x in (full_base, full_stack, full_alone))
                if complete:
                    def g(d):
                        return _geomean_of(d, full_base, BENCHMARKS)
                    confirmation = {
                        "name": name,
                        "reference_stack": stack,
                        "reference": round(g(full_stack), 5),
                        "alone": round(g(full_alone), 5),
                        "with_stack": round(g(full_pair), 5) if g(full_pair) else None,
                        "screened_with_stack": champion.get("geomean_with_stack"),
                    }
                    best_confirmed = max(x for x in (confirmation["alone"],
                                                     confirmation["with_stack"]) if x)
                    confirmation["beats_reference"] = best_confirmed > confirmation["reference"]
                    _say(f"  {best_label} {confirmation['reference']:.5f} | {name} alone "
                         f"{confirmation['alone']:.5f} | together "
                         f"{confirmation['with_stack']} -- "
                         f"{'BEATS it' if confirmation['beats_reference'] else 'does not beat it'}")
                else:
                    _say("  confirmation did not complete on every trace")

    return {
        "confirmation": confirmation,
        "reference": {"name": best_label, "geomean_speedup": round(beat, 5),
                      "candidates": {k: round(v, 5) for k, v in measured.items()}},
        "best": max(winners, key=lambda a: a["geomean_speedup"]) if winners else None,
        "beat_the_reference": bool(winners) and max(
            a["geomean_speedup"] for a in winners) > beat,
        "attempts": attempts,
        "compiled": sum(1 for a in attempts if a.get("outcome") == "measured"),
        "rounds": rounds,
        "screened_only": confirmation is None,
        "wall_clock_s": round(time.monotonic() - started, 1),
        "log": log,
    }


__all__ = ["DEFAULT_REFERENCE_STACK", "invent"]
