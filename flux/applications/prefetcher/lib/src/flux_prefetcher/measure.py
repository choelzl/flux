"""Measuring prefetcher designs: batches, rungs, caching, and the record left behind.

Everything here answers "what did the simulator say about this design" and nothing here decides
what to try next. That split is the point. `flow.py` sequences a study and `search.py` walks a
space; this module is the only one that knows a simulation takes minutes, that a wave costs what
its slowest member costs, or that a result is worth writing down.

It was carved out of `flow.py` when that file passed a thousand lines with a 320-line
`run_study`, and the carving is by responsibility rather than by size: a reader who wants to know
why a number is what it is comes here; one who wants to know why it was measured at all reads
the flow.
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import BingoConfig, is_valid, storage_bytes
from .objective import BENCHMARKS, Baseline, IncompleteMeasurement, score
from .study import ScoredConfig


#: A measurement job: one configuration against one trace.
Job = tuple[BingoConfig, str, list[str]]


def _identity(cfg: BingoConfig, trace: str, types: list[str], warm: int, sim: int,
              partner_knobs: dict[str, Any] | None = None,
              sources: dict[str, str] | None = None) -> str:
    """The cache key for one measurement. Everything that changes the number is in it.

    The partners' knobs included: two stacks differing only in `sms_pref_degree` are two designs
    with two IPCs, and a key that omitted them would serve one measurement for both.

    `sources` names the code behind any prefetcher in `types` that is not stock -- an invented
    design's header digest. That, and not the binary it was compiled into, is what the number
    depends on: the same stack measured on the stock simulator and on two rebuilds with different
    invention libraries installed agreed to the last digit, twelve identities over three binaries
    (D361). Keying on the binary threw all of it away every time the library changed.
    """
    knobs = ",".join(f"{k}={v}" for k, v in sorted(cfg.knobs().items()))
    extra = ",".join(f"{k}={v}" for k, v in sorted((partner_knobs or {}).items()))
    code = ",".join(f"{name}@{digest}" for name, digest in sorted((sources or {}).items())
                    if name in types)
    return (f"{trace}|{','.join(types) if types else 'none'}|{warm}/{sim}|"
            f"{knobs},bingo_l2c_thresh={cfg.l2c_thresh}" + (f"|{extra}" if extra else "")
            + (f"|src:{code}" if code else ""))


def _fingerprint(binary: Path) -> dict[str, str]:
    """What produced the numbers: the simulator binary's own hash.

    Not the version string -- ChampSim prints a compile date that is `Jan 1 1980` in this build.
    A content hash cannot lie about which binary ran, which is the whole point of keying a cache
    on it (D340). The cache is keyed on the STOCK binary's hash even when a rebuild with
    inventions installed is what runs: the rebuild carries the same sources for every stock
    prefetcher, and the invented ones enter the per-measurement identity by their own digest.
    """
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()[:16]
    return {"champsim": f"{binary.name}@{digest}"}


# ---- measurement -------------------------------------------------------------
def local_measure_batch(jobs: list[dict[str, Any]], *, parallelism: int = 16,
                        binary: str | None = None) -> list[dict[str, Any]]:
    """Run a batch of simulations on this machine, `parallelism` at a time.

    The default backend. A failure is returned as `{"error": ...}` in the job's slot rather than
    raised, because one configuration that crashes the simulator must not discard the batch of
    twenty that ran beside it -- but it is never returned as a number, either. A study that
    substitutes a plausible value for a failed measurement is a study that reports fiction.
    """
    from flux_evaluator_champsim_bingo import run_champsim

    def one(job: dict[str, Any]) -> dict[str, Any]:
        try:
            return run_champsim(
                job["config"], Path(job["trace"]), types=job["types"],
                warmup_instructions=job["warmup"], simulation_instructions=job["simulation"],
                binary=job.get("binary") or binary, timeout_s=job.get("timeout_s"),
                partner_knobs=job.get("partner_knobs"))
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc!s:.200}"}

    if not jobs:
        return []
    with cf.ThreadPoolExecutor(max_workers=max(1, min(parallelism, len(jobs)))) as pool:
        return list(pool.map(one, jobs))


class Measurer:
    """Cache-aware batch measurement: never runs what is already known.

    `MeasurementCache.get_or_measure` is one-at-a-time by construction, and these measurements are
    six minutes apiece, so the cache is consulted as a partition (what is known, what must run)
    and the batch is dispatched in one go. Cache hits cost nothing; misses go out together and
    come back together.
    """

    def __init__(self, cache, measure_batch: Callable[..., list[dict[str, Any]]],
                 *, warmup: int, simulation: int, parallelism: int,
                 on_progress: Callable[[str], None] | None = None,
                 binary: str | None = None, sources: dict[str, str] | None = None) -> None:
        self.cache = cache
        self.measure_batch = measure_batch
        #: Header digests of the invented prefetchers this binary carries, by name. Part of a
        #: measurement's identity when the design uses one; irrelevant when it does not.
        self.sources = dict(sources or {})
        #: The simulator every job of this rung runs on, carried IN the job. A backend that
        #: resolved its own binary ran the invention-loop's partners on the stock simulator,
        #: which had never heard of them: every one "exited 1", and the flow -- which had just
        #: built the right binary -- had no way to know it was not being used.
        self.binary = binary
        self.warmup, self.simulation = warmup, simulation
        self.parallelism = parallelism
        self.on_progress = on_progress or (lambda _msg: None)
        self.runs = 0                     # simulations actually executed, for the cost report
        self.hits = 0
        #: Named so a recorded trial says which fidelity produced it — the whole point of D351.
        self.rung = "decide" if simulation >= 100_000_000 else "screen"

    def ipc(self, wanted: list[tuple[BingoConfig, str, Path, list[str], dict[str, Any]]]
            ) -> dict[tuple[int, str], Any]:
        """Measure `(config, benchmark, trace, types, partner_knobs)` tuples.

        EVERY FIELD IS PER JOB, including the partner knobs. They used to be one argument for the
        whole batch, which meant candidates differing in their stack or their partners' settings
        could not share a wave -- so `compose` measured six partners as six waves of three
        simulations each, at a parallelism of eighteen. A wave costs what its slowest simulation
        costs, so that was roughly forty-five seconds doing seven seconds of work.
        """
        out: dict[tuple[int, str], Any] = {}
        jobs, slots = [], []
        for idx, (cfg, bench, trace, types, knobs) in enumerate(wanted):
            ident = _identity(cfg, bench, types, self.warmup, self.simulation, knobs,
                              self.sources)
            if self.cache is not None and self.cache.holds(ident):
                out[(idx, bench)] = self.cache.get_or_measure(ident, lambda: None)
                self.hits += 1
                continue
            jobs.append({"config": cfg, "trace": str(trace), "types": types,
                         "warmup": self.warmup, "simulation": self.simulation,
                         "partner_knobs": dict(knobs or {}), "binary": self.binary})
            slots.append((idx, bench, ident))

        if jobs:
            self.on_progress(
                f"measuring {len(jobs)} simulation(s), {self.parallelism} at a time "
                f"({self.hits} served from cache)")
            started = time.monotonic()
            got = self.measure_batch(jobs, parallelism=self.parallelism)
            self.runs += len(jobs)
            self.on_progress(f"  {len(jobs)} run(s) in {time.monotonic() - started:.0f}s")
            for (idx, bench, ident), result in zip(slots, got):
                value = result.get("error") if "error" in result else float(result["ipc"])
                out[(idx, bench)] = value
                if self.cache is not None and "error" not in result:
                    self.cache.get_or_measure(ident, lambda v=value: v)
        return out


from flux_records import Records


class Recorder(Records):
    """The prefetcher's record: `flux_records.Records` (which this class's frame became
    in D397, delegated back to it in D401) plus the Bingo-typed trial and read-back.

    What stays here is exactly what is domain-shaped: `trial` takes a BingoConfig and
    its stack and writes the per-metric method split (simulated IPC beside the analytic
    storage model), and `known` reads BingoConfigs back. Campaign lifecycle, phases,
    notes and the swallow-everything discipline live in the shared class.
    """

    def trial(self, cfg: BingoConfig, types: tuple[str, ...], *, rung: str, strategy: str,  # type: ignore[override]
              metrics: dict[str, float] | None, error: str | None, wall_s: float) -> None:
        """One measured or refused candidate."""
        if self.store is None:
            return
        try:
            from flux_evaluator_abi import (
                Bottleneck, Domain, Escalation, Estimate, Limiter, Method, Provenance, Result,
                Validity,
            )

            candidate = {"prefetcher": {"kind": "bingo", "types": list(types), **cfg.knobs(),
                                        "bingo_l2c_thresh": cfg.l2c_thresh}}
            key = _label(cfg, types)
            seq = self.store.begin_trial(
                self.campaign_id, phase=self._phase, candidate=candidate, candidate_key=key,
                workload_hash="+".join(BENCHMARKS),
                arch_hash=hashlib.sha256(key.encode()).hexdigest()[:16], mapping_hash=None,
                strategy_kind=strategy, seed=None, deterministic=True, rung=rung)
            result = None
            if metrics is not None:
                result = Result(
                    metrics={k: Estimate(value=v, ci_low=v, ci_high=v, unit="",
                                         method=Method.SIMULATED if k != "storage_bytes"
                                         else Method.ANALYTIC)
                             for k, v in metrics.items()},
                    validity=Validity(ok=True, checker_version="bingo@0.1", violations=()),
                    domain=Domain(in_domain=True),
                    bottleneck=Bottleneck(limiter=Limiter.MEMORY),
                    provenance=Provenance(evaluator="champsim_bingo@0.1",
                                          inputs={"rung": rung, "stack": "+".join(types)}),
                    escalation=Escalation(recommended=False))
            self.store.complete_trial(
                self.campaign_id, seq, status="ok" if error is None else "refused",
                result=result, error=error, wall_clock_s=wall_s)
        except Exception:                                                 # noqa: BLE001
            pass

    def known(self, *, rung: str = "screen", types: tuple[str, ...] = ("bingo",)
              ) -> list[tuple[BingoConfig, float]]:
        """What this campaign has already measured on `rung` for `types`, best first.

        A resumed campaign used to mean cache hits and nothing else: the first proposer call was
        as blind as on day one and the seed pool started from the shipped default, so a run
        that had found an 800 KB pattern table the day before began by rediscovering it or
        not. This is what "resumed" should mean -- the record, read back (D367). One entry
        per configuration, its best number.
        """
        if self.store is None or not self.campaign_id:
            return []
        best: dict[BingoConfig, float] = {}
        try:
            for t in self.store.trials(self.campaign_id, status="ok"):
                if t.rung != rung or t.result is None:
                    continue
                pf = (t.candidate or {}).get("prefetcher", {})
                if tuple(pf.get("types") or ()) != tuple(types):
                    continue
                est = t.result.metrics.get("geomean_speedup")
                if est is None:
                    continue
                cfg = BingoConfig.from_knobs(pf)
                best[cfg] = max(best.get(cfg, 0.0), float(est.value))
        except Exception:                                                 # noqa: BLE001
            return []
        return sorted(best.items(), key=lambda kv: -kv[1])



#: One design to measure: Bingo's knobs, the stack it runs in, and that stack's partner knobs.
Design = tuple[BingoConfig, tuple[str, ...], dict[str, Any]]


def _score_designs(designs: list[Design], traces: dict[str, Path], baseline: Baseline,
                   measurer: Measurer, provenance: str, refused: list[tuple[str, str]],
                   recorder: "Recorder | None" = None,
                   max_storage: int | None = None) -> list[ScoredConfig]:
    """Measure a batch of DIFFERENT designs in one wave, and score each.

    The heterogeneous form. `_score_all` is the special case where every design shares a stack;
    `compose` and `tune-partners` do not, and measuring them one at a time wasted most of the
    available parallelism.

    `max_storage` is the storage budget (D362): a design over it is refused HERE, before any
    simulation, with the reason recorded. Storage is the free analytic model, so this gate costs
    nothing and no phase can spend minutes measuring something that could not be built.
    """
    if max_storage is not None:
        within = []
        for cfg, types, knobs in designs:
            size = storage_bytes(cfg)
            if size > max_storage:
                why = f"over the storage budget: {size:,} B > {max_storage:,} B"
                refused.append((_label(cfg, types), why))
                if recorder:
                    recorder.trial(cfg, types, rung=measurer.rung, strategy=provenance,
                                   metrics=None, error=why, wall_s=0.0)
            else:
                within.append((cfg, types, knobs))
        designs = within
    wanted = [(cfg, bench, traces[bench], list(types), knobs)
              for cfg, types, knobs in designs for bench in BENCHMARKS]
    got = measurer.ipc(wanted)

    scored: list[ScoredConfig] = []
    per_design = len(BENCHMARKS)
    for i, (cfg, types, knobs) in enumerate(designs):
        ipc: dict[str, float] = {}
        failure = None
        for j, bench in enumerate(BENCHMARKS):
            value = got.get((i * per_design + j, bench))
            if isinstance(value, (int, float)) and value is not None:
                ipc[bench] = float(value)
            else:
                failure = f"{bench}: {value}"
        if failure is not None:
            refused.append((_label(cfg, types), f"simulation failed on {failure}"))
            if recorder:
                recorder.trial(cfg, types, rung=measurer.rung, strategy=provenance,
                               metrics=None, error=f"simulation failed on {failure}", wall_s=0.0)
            continue
        try:
            got_score = score(ipc, baseline, storage_bytes(cfg))
        except IncompleteMeasurement as exc:
            refused.append((_label(cfg, types), str(exc)))
            continue
        scored.append(ScoredConfig(
            config=cfg, provenance=provenance, types=types, score=got_score,
            partner_knobs=tuple(sorted((knobs or {}).items()))))
        if recorder:
            recorder.trial(cfg, types, rung=measurer.rung, strategy=provenance,
                           metrics={"geomean_speedup": got_score.geomean_speedup,
                                    "storage_bytes": float(got_score.storage_bytes),
                                    **{f"ipc_{b}": v for b, v in ipc.items()}},
                           error=None, wall_s=0.0)
    return scored


def _score_all(configs: list[BingoConfig], traces: dict[str, Path], baseline: Baseline,
               measurer: Measurer, provenance: str, refused: list[tuple[str, str]],
               types: tuple[str, ...] = ("bingo",),
               partner_knobs: dict[str, Any] | None = None,
               recorder: "Recorder | None" = None,
               max_storage: int | None = None) -> list[ScoredConfig]:
    """Many configurations, ONE stack. The common case, delegating to `_score_designs`."""
    return _score_designs([(cfg, types, dict(partner_knobs or {})) for cfg in configs],
                          traces, baseline, measurer, provenance, refused, recorder,
                          max_storage=max_storage)


def _label(cfg: BingoConfig, types: tuple[str, ...] = ("bingo",)) -> str:
    """A short, stable name for a configuration, for logs and refusal lists.

    The stack is part of the identity: `bingo` and `bingo+sms` running the same knobs are two
    different designs with two different measurements, and a label that dropped the partners
    would print them as one row.
    """
    partners = "".join(f"+{p}" for p in types if p != "bingo")
    return (partners.lstrip("+") and f"{partners.lstrip('+')}|" or "") + (
        f"bingo-r{cfg.region_size}-ft{cfg.ft_size}-at{cfg.at_size}"
            f"-pht{cfg.pht_size}x{cfg.pht_ways}-ps{cfg.pf_streamer_size}"
            f"-pc{cfg.pc_width}-a{cfg.min_addr_width}:{cfg.max_addr_width}"
            f"-t{cfg.l2c_thresh}")


def _dedupe_pairs(pairs: Iterable[tuple[BingoConfig, str]],
                  seen: set[BingoConfig]) -> list[tuple[BingoConfig, str]]:
    """Legal and not already tried, in proposal order, keeping who proposed each one.

    FILTERS ONLY. Marking is the caller's job, via `_mark`, because every caller takes a SLICE of
    this list — a wave of six or eight out of a hundred-odd candidates. A version of this that
    marked as it filtered burned the whole pool on the first slice: 125 neighbours went into
    `seen`, five were measured, and the next round reported "no unexplored neighbours of the
    leader" and stopped. The search looked like it had converged when it had barely started.
    """
    return [(cfg, who) for cfg, who in pairs if cfg not in seen and is_valid(cfg)]


def _mark(seen: set[BingoConfig], measured: Iterable[BingoConfig]) -> None:
    """Record what was actually measured. Never what was merely considered."""
    seen.update(measured)


def _dedupe(configs: Iterable[BingoConfig], seen: set[BingoConfig]) -> list[BingoConfig]:
    """Legal and not already tried, in order. Filters only — see `_dedupe_pairs`."""
    return [cfg for cfg in configs if cfg not in seen and is_valid(cfg)]


def _measure_baseline(traces: dict[str, Path], measurer: Measurer,
                      log: Callable[[str], None]) -> Baseline:
    """No-prefetcher IPC per trace: the denominator every speedup in this study divides by.

    Measured here rather than read from the project's `baseline/*.out` files on purpose. Those were
    produced by a binary this run may not be using, and a speedup computed against another
    toolchain's denominator is not a speedup, it is a comparison of two environments.
    `baseline/reference_ipc.json` still exists, and is used to CHECK this -- see `run_study`.
    """
    from .config import DEFAULT

    log("baseline: measuring no-prefetcher IPC on each trace")
    wanted = [(DEFAULT, bench, traces[bench], [], {}) for bench in BENCHMARKS]
    got = measurer.ipc(wanted)
    ipc = {}
    for i, bench in enumerate(BENCHMARKS):
        value = got.get((i, bench))
        if not isinstance(value, (int, float)) or value is None:
            raise IncompleteMeasurement(
                f"baseline measurement failed on {bench}: {value}. Every speedup this study "
                "reports divides by this number, so the run stops here rather than continuing "
                "against a denominator it had to invent.")
        ipc[bench] = float(value)
    log("  baseline IPC: " + ", ".join(f"{b}={v:.5f}" for b, v in ipc.items()))
    return Baseline(ipc=ipc)


def _profile_traces(traces: dict[str, Path], binary: Path, warmup: int, simulation: int,
                    parallelism: int, log: Callable[[str], None]) -> str:
    """The evidence page for every prompt in this study: static and dynamic, per trace.

    Static parsing is a few seconds per trace and needs no simulator. The dynamic view is one
    screen-rung run of the shipped Bingo per trace -- the run the study makes anyway as its
    incumbent, so this costs one extra wave and yields the number a design has to move: the
    share of would-be misses the current prefetcher does NOT catch.

    Failure is a missing section, never a dead study: a prompt without evidence is the prompt
    this study had until now.
    """
    import concurrent.futures as cf

    from flux_evaluator_champsim_bingo import run_champsim
    from .config import DEFAULT
    from .profile import dynamic_profile, profile_text, static_profile

    static, dynamic = [], []
    try:
        with cf.ThreadPoolExecutor(max_workers=len(traces)) as pool:
            static = list(pool.map(lambda b: static_profile(traces[b]), BENCHMARKS))
    except Exception as exc:                                              # noqa: BLE001
        log(f"  (no static trace profile: {type(exc).__name__}: {exc!s:.80})")

    def diagnose(bench: str):
        got = run_champsim(DEFAULT, traces[bench], types=["bingo"], warmup_instructions=warmup,
                           simulation_instructions=simulation, binary=binary, timeout_s=3600)
        return dynamic_profile(got["stats"], bench, "bingo (shipped bingo.ini)")

    try:
        with cf.ThreadPoolExecutor(max_workers=min(parallelism, len(traces))) as pool:
            dynamic = list(pool.map(diagnose, BENCHMARKS))
    except Exception as exc:                                              # noqa: BLE001
        log(f"  (no dynamic trace profile: {type(exc).__name__}: {exc!s:.80})")

    if not static and not dynamic:
        return ""
    text = profile_text(static, dynamic)
    for line in text.splitlines():
        if line.startswith("  *"):
            log(f"  profile: {line[4:120]}")
    return text

