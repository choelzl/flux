"""Measuring prefetcher designs: batches, stages, caching, and the record left behind.

Everything here answers "what did the simulator say about this design" and nothing here decides
what to try next. That split is the point. `world.py` sequences a study, `flow.py` holds its
policies and `search.py` walks a space; this module is the only one that knows a simulation
takes minutes, that a wave costs what its slowest member costs, or what a measurement's identity
is. The record is the loop's (D446): every measured candidate lands there through
`measure_batch`, with the storage model tagged analytic beside the simulated speedup.
"""

from __future__ import annotations

import hashlib
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
    from flux_loop.pool import run_parallel

    return [r if exc is None else {"error": f"{type(exc).__name__}: {exc!s:.200}"}
            for r, exc in run_parallel(jobs, one, max(1, int(parallelism)))]


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
        #: The simulator every job of this stage runs on, carried IN the job. A backend that
        #: resolved its own binary ran the invention-loop's partners on the stock simulator,
        #: which had never heard of them: every one "exited 1", and the flow -- which had just
        #: built the right binary -- had no way to know it was not being used.
        self.binary = binary
        self.warmup, self.simulation = warmup, simulation
        self.parallelism = parallelism
        self.on_progress = on_progress or (lambda _msg: None)
        #: Named so a recorded trial says which fidelity produced it — the whole point of D351.
        self.stage = "decide" if simulation >= 100_000_000 else "screen"
        from flux_cache import CachedBatch

        self._batch = CachedBatch(cache, on_progress=self.on_progress, noun="simulation",
                                  parallel=parallelism)

    @property
    def runs(self) -> int:
        """Simulations actually executed, for the cost report."""
        return self._batch.runs

    @property
    def hits(self) -> int:
        return self._batch.hits

    def ipc(self, wanted: list[tuple[BingoConfig, str, Path, list[str], dict[str, Any]]]
            ) -> dict[tuple[int, str], Any]:
        """Measure `(config, benchmark, trace, types, partner_knobs)` tuples.

        EVERY FIELD IS PER JOB, including the partner knobs. They used to be one argument for the
        whole batch, which meant candidates differing in their stack or their partners' settings
        could not share a wave -- so `compose` measured six partners as six waves of three
        simulations each, at a parallelism of eighteen. A wave costs what its slowest simulation
        costs, so that was roughly forty-five seconds doing seven seconds of work.

        The cache partition, the store-back and the progress lines are `flux_cache.CachedBatch`'s
        (D436); a value is the IPC, or the error text, and only IPCs are stored.
        """
        def measure(items: list[tuple]) -> list[Any]:
            jobs = [{"config": cfg, "trace": str(trace), "types": types,
                     "warmup": self.warmup, "simulation": self.simulation,
                     "partner_knobs": dict(knobs or {}), "binary": self.binary}
                    for cfg, _bench, trace, types, knobs in items]
            got = self.measure_batch(jobs, parallelism=self.parallelism)
            return [r.get("error") if "error" in r else float(r["ipc"]) for r in got]

        values = self._batch.run(
            list(wanted),
            identity=lambda w: _identity(w[0], w[1], w[3], self.warmup, self.simulation, w[4],
                                         self.sources),
            measure=measure, is_error=lambda v: isinstance(v, str))
        return {(idx, w[1]): v for idx, (w, v) in enumerate(zip(wanted, values))}


#: One design to measure: Bingo's knobs, the stack it runs in, and that stack's partner knobs.
Design = tuple[BingoConfig, tuple[str, ...], dict[str, Any]]


def _score_designs(designs: list[Design], traces: dict[str, Path], baseline: Baseline,
                   measurer: Measurer, provenance: str, refused: list[tuple[str, str]]
                   ) -> list[ScoredConfig]:
    """Measure a batch of DIFFERENT designs in one wave, and score each.

    The heterogeneous form: `compose` and `tune-partners` mix stacks and partner knobs in one
    wave, and measuring them one at a time wasted most of the available parallelism. The
    storage budget (D362) is not applied here: the loop's gate (`World.judge`) refuses a design
    over it before it reaches a wave, so nothing here can spend minutes on what could not be built.
    """
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
            continue
        try:
            got_score = score(ipc, baseline, storage_bytes(cfg))
        except IncompleteMeasurement as exc:
            refused.append((_label(cfg, types), str(exc)))
            continue
        scored.append(ScoredConfig(
            config=cfg, provenance=provenance, types=types, score=got_score,
            partner_knobs=tuple(sorted((knobs or {}).items()))))
    return scored


def _label(cfg: BingoConfig, types: tuple[str, ...] = ("bingo",)) -> str:
    """A short, stable name for a configuration, for logs and refusal lists.

    The stack is part of the identity: `bingo` and `bingo+sms` running the same knobs are two
    different designs with two different measurements, and a label that dropped the partners
    would print them as one row. A stack WITHOUT Bingo -- an invented prefetcher measured alone
    (D361) -- is named by its stack: Bingo's knobs are not in the picture.
    """
    if "bingo" not in types:
        return "+".join(types) or "none"
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


def _measure_baseline(traces: dict[str, Path], measurer: Measurer,
                      log: Callable[[str], None]) -> Baseline:
    """No-prefetcher IPC per trace: the denominator every speedup in this study divides by.

    Measured here rather than read from the project's `baseline/*.out` files on purpose. Those were
    produced by a binary this run may not be using, and a speedup computed against another
    toolchain's denominator is not a speedup, it is a comparison of two environments.
    `baseline/reference_ipc.json` still exists, and is used to CHECK this -- see `flow.check_against_reference`.
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
    screen-stage run of the shipped Bingo per trace -- the run the study makes anyway as its
    incumbent, so this costs one extra wave and yields the number a design has to move: the
    share of would-be misses the current prefetcher does NOT catch.

    Failure is a missing section, never a dead study: a prompt without evidence is the prompt
    this study had until now.
    """

    from flux_evaluator_champsim_bingo import run_champsim
    from .config import DEFAULT
    from .profile import dynamic_profile, profile_text, static_profile

    from flux_loop.pool import run_parallel

    static, dynamic = [], []
    got = run_parallel(list(BENCHMARKS), lambda b: static_profile(traces[b]), max(1, len(traces)))
    bad = next((exc for _r, exc in got if exc is not None), None)
    if bad is None:
        static = [r for r, _exc in got]
    else:
        log(f"  (no static trace profile: {type(bad).__name__}: {bad!s:.80})")

    def diagnose(bench: str):
        got = run_champsim(DEFAULT, traces[bench], types=["bingo"], warmup_instructions=warmup,
                           simulation_instructions=simulation, binary=binary, timeout_s=3600)
        return dynamic_profile(got["stats"], bench, "bingo (shipped bingo.ini)")

    got = run_parallel(list(BENCHMARKS), diagnose, max(1, min(int(parallelism), len(traces))))
    bad = next((exc for _r, exc in got if exc is not None), None)
    if bad is None:
        dynamic = [r for r, _exc in got]
    else:
        log(f"  (no dynamic trace profile: {type(bad).__name__}: {bad!s:.80})")

    if not static and not dynamic:
        return ""
    text = profile_text(static, dynamic)
    for line in text.splitlines():
        if line.startswith("  *"):
            log(f"  profile: {line[4:120]}")
    return text


def known_configs(records: Any, *, stage: str = "screen", types: tuple[str, ...] = ("bingo",)
                  ) -> list[tuple[BingoConfig, float]]:
    """What this campaign has already measured on `stage` for `types`, best first, read back
    from the loop's record (D367; the world's `Recorder` used to do this): one entry per
    configuration, its best geomean. A resumed run's seed pool and its first proposer prompt
    start from here instead of from the shipped default."""
    if records is None or getattr(records, "store", None) is None:
        return []
    best: dict[BingoConfig, float] = {}
    try:
        for row in records.known_rows(stage=stage):
            cand = row.candidate or {}
            pf = cand.get("prefetcher") or (cand.get("knobs") or {}).get("prefetcher") or {}
            if tuple(pf.get("types") or ()) != tuple(types):
                continue
            value = (row.metrics or {}).get("geomean_speedup")
            if value is None:
                continue
            cfg = BingoConfig.from_knobs(pf)
            best[cfg] = max(best.get(cfg, 0.0), float(value))
    except Exception:                                                     # noqa: BLE001
        return []
    return sorted(best.items(), key=lambda kv: -kv[1])
