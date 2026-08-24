"""`flux_invent_prefetcher` — design a NEW L2 prefetcher, build it, and measure whether it wins.

The code-space rung. Everything else in the prefetcher study searches parameters of prefetchers
somebody already wrote; this writes one. It is affordable for an unusual reason: a ChampSim
rebuild costs seconds while one evaluation costs three simulations of about six minutes, so the
compiler is nearly free feedback and a candidate can be repaired several times for less than the
price of measuring it once.

THE LOOP, and which parts are model-authored:

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
absorbs it, so there are no test vectors to pass — a design that builds and runs can still be
useless or actively harmful (`bop` and `dspatch` are both SLOWER than no prefetcher at all). The
verdict is measured geomean IPC speedup against the same no-prefetcher baseline everything else
is quoted against.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from chia.base.ChiaFunction import ChiaFunction


#: The stock stack the study composes first (D352/D353): bingo+sms+stride reaches 1.0515 at its
#: shipped defaults and 1.0640 tuned, against Bingo's 1.0439. It is the floor of the reference,
#: not the reference: every kept invention that earned a place beside it makes a taller stack,
#: and the loop asks the next design to beat the tallest one it can measure (D361).
DEFAULT_REFERENCE_STACK = ("bingo", "sms", "stride")


def _ipc_and_stats(binary: Path, designs: list[tuple[str, list[str], dict[str, int]]],
                   traces: dict[str, Path], warmup: int, simulation: int,
                   parallelism: int) -> dict[str, dict[str, Any]]:
    """Measure several stacks on every trace in ONE wave; return `{label: {bench: run}}`.

    One wave, because a wave costs what its slowest member costs: measuring "alone" and then
    "with the stack" as two waves paid twice for the same six-minute floor. Through
    `local_measure_batch`, so the invention loop and the study share one definition of how a
    batch of simulations is run -- the node used to carry two thread pools of its own.
    """
    from flux_prefetcher.config import DEFAULT
    from flux_prefetcher.measure import local_measure_batch
    from flux_prefetcher.partners import defaults_for_stack

    jobs, slots = [], []
    for label, types, knobs in designs:
        merged = {**defaults_for_stack(tuple(types)), **knobs}
        for bench, trace in traces.items():
            jobs.append({"config": DEFAULT, "trace": str(trace), "types": list(types),
                         "warmup": warmup, "simulation": simulation, "partner_knobs": merged,
                         "timeout_s": 3600})
            slots.append((label, bench))
    got = local_measure_batch(jobs, parallelism=parallelism, binary=str(binary))
    out: dict[str, dict[str, Any]] = {label: {} for label, _, _ in designs}
    for (label, bench), run in zip(slots, got):
        if "error" not in run:
            out[label][bench] = run
    return out


def _geomean_of(runs: dict[str, Any], base: dict[str, Any], benchmarks) -> float | None:
    from flux_prefetcher.objective import geomean

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
    """The stacks worth measuring as the reference: what the kept records claim is best.

    Each kept design that earned its place was measured beside a stack, and that stack WITH the
    design in it is the tallest thing its record vouches for. The stock stack is always among
    the candidates, so a library whose claims do not hold up costs a wave and nothing else.
    """
    if explicit:
        return [list(explicit)]
    stacks: list[list[str]] = [list(DEFAULT_REFERENCE_STACK)]
    for design in library:
        beside = list(design.reference_stack or DEFAULT_REFERENCE_STACK)
        stack = beside + ([design.name] if design.name not in beside else [])
        if stack not in stacks:
            stacks.append(stack)
    return stacks


def _compile(name: str, proposal, *, tree_src: Path, library, scratch: Path, tag: str,
             ask: Callable[[str], str], repair_attempts: int, say: Callable[[str], None]):
    """Install the library and the design into a fresh tree, build, repair compile errors.

    The kept library goes in beside the candidate because the reference stack may name a kept
    design: a candidate is measured beside the stack it is asked to beat, and that stack has to
    exist in the binary it runs on. Returns `(proposal, result)`; `result` is None when nothing
    was built, and `result.ok` says whether the last build succeeded.
    """
    from flux_codegen_champsim_prefetcher import (
        build, install, parse_proposal, repair_prompt, stage_tree,
    )

    result = None
    for attempt in range(1, max(1, repair_attempts) + 1):
        tree = stage_tree(tree_src, scratch / f"{tag}{attempt}")
        for design in library:
            install(design.name, design.header, design.knobs, tree)
        install(name, proposal.header, proposal.knobs, tree)
        result = build(tree)
        if result.ok:
            say(f"  built in {result.elapsed_s:.0f}s"
                + (f" (after {attempt - 1} repair(s))" if attempt > 1 else ""))
            return proposal, result
        say(f"  compile failed ({attempt}/{repair_attempts}): {result.first_error[:110]}")
        if attempt == repair_attempts:
            break
        repaired = parse_proposal(name, ask(repair_prompt(proposal, result.first_error)))
        if repaired is None:
            say("  the repair returned no header")
            break
        proposal = repaired
    return proposal, result


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


@ChiaFunction()
def flux_invent_prefetcher(
    *,
    rounds: int = 4,
    repair_attempts: int = 3,
    inert_repairs: int = 1,
    problem: str | None = None,
    traces_dir: str | None = None,
    source_tree: str | None = None,
    parallelism: int = 12,
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
    times; designs that run are measured on the cheap rung and ranked. Only a design that beat
    the reference on the screen is confirmed at full length, and D351's lesson stands: a
    screened number orders candidates and must not be quoted.

    Returns every attempt with what happened to it, so a run that produced nothing usable says
    what went wrong rather than reporting an empty list.
    """
    from flux_codegen_champsim_prefetcher import (
        build, build_prompt, check_name, inert_repair_prompt, install, parse_proposal,
        stage_tree, truncation_reason, unbuildable_reason,
    )
    from flux_evaluator_champsim_bingo import resolve_binary, resolve_source_tree
    from flux_llm import local_proposer
    from flux_prefetcher.invented import build_binary, library as kept_library, register
    from flux_prefetcher.objective import BENCHMARKS
    from flux_prefetcher.staging import scratch_root as default_scratch, stage_traces

    started = time.monotonic()
    log: list[str] = []

    # WHERE THE SOURCE GOES. A build tree is a temporary directory and the header dies with it,
    # so the first run that produced a design beating bingo (+0.00296 as a partner) left nothing
    # behind but a number in a log. Anything that compiled is written out, winner or not: the
    # ones that lost are the record of what was already tried.
    kept = Path(keep_dir) if keep_dir else (
        Path(__file__).resolve().parents[4] / "applications" / "prefetcher" / "invented")
    try:
        kept.mkdir(parents=True, exist_ok=True)
    except OSError:
        kept = None

    def say(message: str) -> None:
        log.append(message)
        print(message, flush=True)

    tree_src = Path(source_tree) if source_tree else resolve_source_tree()
    # DEFAULT_TRACES, not a second parents[N] count. The first attempt here computed the path
    # itself, went one level too far, and looked for the traces beside the repository instead of
    # inside it. `flux_prefetcher.flow` already resolves this and is checked by a test.
    from flux_prefetcher.flow import DEFAULT_TRACES

    root = Path(traces_dir) if traces_dir else DEFAULT_TRACES
    missing = [b for b in BENCHMARKS if not (root / f"{b}.simout_champsim.gz").is_file()]
    if missing:
        return {"error": f"no trace for {missing} under {root}", "log": log}
    traces = stage_traces({b: root / f"{b}.simout_champsim.gz" for b in BENCHMARKS}, log=say)
    from flux_evaluator_champsim_bingo.adapter import DECIDE, SCREEN

    warmup, simulation = SCREEN
    # A C++ header needs more output than this repository's usual JSON proposal:
    # `DEFAULT_NUM_PREDICT` is 1200 tokens and the first live run truncated all three designs
    # mid-statement at exactly that ceiling. It cannot simply be raised without limit either --
    # the local model generates at a few tokens a second, so 4000 tokens is about seventeen
    # minutes for ONE design. The prompt asks for under 70 lines instead, which is what the
    # worked example needs and what beat `stride`.
    ask = local_proposer(model=llm_model, num_predict=num_predict)
    scratch_dir = Path(scratch_root) if scratch_root else (default_scratch() or Path("/tmp"))

    # THE REFERENCE IS THE BEST STACK THE LIBRARY CAN VOUCH FOR, re-measured. "Beat Bingo" was
    # the wrong question once composition was understood, and "beat bingo+sms+stride" became
    # the wrong one once a kept invention had earned its place beside that stack: the study's
    # best confirmed design is bingo+sms+invented2, and a loop still asking for something that
    # adds to bingo+sms+stride scored every new idea beside the partner the study had moved
    # past (D360). The candidates are the stock stack plus, for each kept design, the stack it
    # was measured beside with it included; they are measured in ONE wave on a simulator that
    # carries the whole library, and the tallest is the number to beat.
    library = kept_library(kept) if kept else []
    reference_binary = resolve_binary()
    if library:
        built = build_binary(library, source_tree=tree_src, cache_dir=scratch_dir, log=say)
        if built is not None:
            reference_binary = built
            register(library)
        else:
            say("  the library did not build; the reference is the stock stack")
            library = []
    candidates = _reference_candidates(library, reference_stack)
    say(f"reference: measuring {len(candidates)} stack(s) on {reference_binary.name}")
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
        say(f"  {label}: geomean {value:.4f}")
    best_label = max(measured, key=measured.get)
    stack = best_label.split("+")
    beat = measured[best_label]
    ref = runs[best_label]
    say(f"  {best_label} reaches geomean {beat:.4f}; that is the number to beat")

    # THE EVIDENCE: what the traces look like, and what the reference stack still misses on
    # them. The dynamic half comes free from the reference run just made -- its counters are
    # already in hand -- so no extra simulation is spent on it.
    from flux_prefetcher.profile import dynamic_profile, profile_text, static_profile

    trace_profile = ""
    try:
        static = [static_profile(traces[b]) for b in BENCHMARKS]
        dynamic = [dynamic_profile(ref[b]["stats"], b, best_label) for b in BENCHMARKS]
        trace_profile = profile_text(static, dynamic)
        for line in trace_profile.splitlines():
            if line.startswith("  *"):
                say(f"  profile: {line[4:120]}")
    except Exception as exc:                                              # noqa: BLE001
        say(f"  (no trace profile: {type(exc).__name__}: {exc!s:.80})")

    attempts: list[dict[str, Any]] = []
    history: list[tuple[str, str, float]] = []
    best_name, best_geomean = best_label, beat
    screen_kw = dict(traces=traces, warmup=warmup, simulation=simulation,
                     parallelism=parallelism, base=base, benchmarks=BENCHMARKS)
    compile_kw = dict(tree_src=tree_src, library=library, ask=ask,
                      repair_attempts=repair_attempts, say=say)

    # NAMES MUST NOT COLLIDE WITH WHAT IS KEPT. Rounds used to be named invented1, invented2, ...
    # from 1 every run, so a run's first design overwrote the kept invented1.h from an earlier
    # run -- and, worse, a binary built with both would have two classes of one name. Numbering
    # continues from the highest kept index, so every design ever kept has a name of its own.
    existing = [int(m.group(1)) for f in (kept.glob("invented*.json") if kept else [])
                if (m := re.fullmatch(r"invented(\d+)", f.stem))]
    first = max(existing, default=0) + 1
    for index in range(first, first + max(1, rounds)):
        name = f"invented{index}"
        try:
            check_name(name)
        except Exception as exc:                                          # noqa: BLE001
            attempts.append({"name": name, "outcome": "bad name", "detail": str(exc)})
            continue

        say(f"\nround {index - first + 1}/{rounds}: asking for `{name}` to beat {best_name} "
            f"({best_geomean:.4f})")
        reply = ask(build_prompt(name, beat=best_name, beat_geomean=best_geomean,
                                 already_tried=history, problem=problem,
                                 trace_profile=trace_profile or None))
        proposal = parse_proposal(name, reply)
        if proposal is None:
            why = truncation_reason(reply) or "no C++ header in the reply"
            say(f"  {why}")
            attempts.append({"name": name, "outcome": "no header", "detail": why,
                             "reply_chars": len(reply)})
            history.append((name, why, 0.0))
            continue
        say(f"  idea: {proposal.rationale[:140]}")
        blocked = unbuildable_reason(proposal)
        if blocked:
            # Caught in microseconds rather than after a sixty-second compile plus a whole
            # generation spent repairing it. One live round went to `invalid new-expression of
            # abstract class type`, which is this check's exact case.
            say(f"  cannot build: {blocked}")
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

            # BOTH ways it can win. Measured alone, and measured beside the stack -- the L2 slot
            # runs several prefetchers at once, and on these traces composition was worth
            # roughly eight times what parameter tuning was. A design that is weak by itself
            # but catches what the stack misses is a better answer than a slightly-better
            # bingo, and the loop was previously unable to notice one.
            screened = _screen(result.binary, name, proposal.knobs, stack, **screen_kw)

            # AN INERT DESIGN IS HANDED BACK, not written off. The counters say which of two
            # bugs it has, and either is a few lines' fix -- cheaper than a fresh design and
            # far cheaper than measuring one. The first loop printed the diagnosis and moved
            # on (D360). Bounded, because a design still inert after a fix is a lost idea.
            logic_repairs = 0
            while screened is not None and screened["inert"] and logic_repairs < inert_repairs:
                logic_repairs += 1
                say(f"  inert: {screened['why_inert'][:150]}")
                say(f"  asking for a logic fix ({logic_repairs}/{inert_repairs})")
                repaired = parse_proposal(name, ask(inert_repair_prompt(proposal,
                                                                        screened["why_inert"])))
                if repaired is None:
                    say("  the fix returned no header")
                    break
                blocked = unbuildable_reason(repaired)
                if blocked:
                    say(f"  the fix cannot build: {blocked}")
                    break
                repaired, fixed = _compile(name, repaired, scratch=Path(scratch),
                                           tag=f"fix{logic_repairs}-", **compile_kw)
                if fixed is None or not fixed.ok:
                    say("  the fix did not compile; keeping the version that ran")
                    break
                rescreened = _screen(fixed.binary, name, repaired.knobs, stack, **screen_kw)
                if rescreened is None:
                    say("  the fix crashed at run time; keeping the version that ran")
                    break
                proposal, screened = repaired, rescreened
                if not screened["inert"]:
                    say("  the fix emits: measured again")

        if screened is None:
            say("  built, but did not run on every trace")
            attempts.append({"name": name, "outcome": "crashed or timed out",
                             "idea": proposal.rationale})
            history.append((name, "built but crashed at run time", 0.0))
            continue

        solo, with_stack, inert = screened["solo"], screened["with_stack"], screened["inert"]
        why_inert = screened["why_inert"]
        got = max([solo] + ([with_stack] if with_stack else []))
        how = "alone" if with_stack is None or solo >= with_stack else f"with {best_label}"
        if inert:
            say(f"  still inert: {why_inert[:150]}")
        say(f"  geomean {solo:.4f} alone" +
            (f", {with_stack:.4f} with {best_label}" if with_stack
             else f", crashed beside {best_label}") +
            f" — {'BEATS ' + best_name if got > best_geomean else 'does not beat ' + best_name}")
        if kept is not None:
            try:
                (kept / f"{name}.h").write_text(proposal.header)
                (kept / f"{name}.json").write_text(json.dumps({
                    "name": name, "knobs": proposal.knobs, "idea": proposal.rationale,
                    "geomean_alone": round(solo, 5),
                    "geomean_with_stack": round(with_stack, 5) if with_stack else None,
                    "reference_stack": stack,
                    "reference_geomean": round(beat, 5),
                    "logic_repairs": logic_repairs,
                    "rung": "screen", "warmup": warmup, "simulation": simulation,
                }, indent=2) + "\n")
                say(f"  kept: {kept / (name + '.h')}")
            except OSError as exc:                                        # noqa: BLE001
                say(f"  (could not keep the source: {exc})")
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

    # CONFIRM THE WINNER, or the loop is just climbing a proxy. The first version stopped here,
    # and the design it declared a winner (+0.0012 on the screen) turned out to be actively
    # HARMFUL at full length: 0.99368 alone, and it cost bingo 0.0049 as a partner. A screen
    # exists to order candidates cheaply; whether an invention is worth having is a question only
    # the expensive rung answers (D351).
    confirmation: dict[str, Any] | None = None
    # Only a champion that BEAT the reference on the screen earns six minutes of confirmation.
    # A run once confirmed an inert design: its "with the stack" number equalled the stack's,
    # which tied for best, and the tie went to a prefetcher that issued nothing. The screen's
    # verdict is not to be quoted, but "did not even beat it here" is enough to save the rung.
    contenders = [a for a in winners if not a.get("inert") and a["geomean_speedup"] > beat]
    if confirm_best and winners and not contenders:
        say("\nnothing beat the reference on the screen; skipping confirmation")
    if confirm_best and contenders:
        champion = max(contenders, key=lambda a: a["geomean_speedup"])
        name = champion["name"]
        say(f"\nconfirming `{name}` at full length ({DECIDE[0]:,} + {DECIDE[1]:,} instructions)")
        with tempfile.TemporaryDirectory(dir=scratch_root or None) as scratch:
            tree = stage_tree(tree_src, Path(scratch) / "confirm")
            for design in library:
                install(design.name, design.header, design.knobs, tree)
            install(name, champion["header"], champion["knobs"], tree)
            rebuilt = build(tree)
            if not rebuilt.ok:
                say(f"  could not rebuild it: {rebuilt.first_error[:110]}")
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
                    say(f"  {best_label} {confirmation['reference']:.5f} | {name} alone "
                        f"{confirmation['alone']:.5f} | together "
                        f"{confirmation['with_stack']} — "
                        f"{'BEATS it' if confirmation['beats_reference'] else 'does not beat it'}")
                else:
                    say("  confirmation did not complete on every trace")

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
