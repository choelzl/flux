"""Asking a model for prefetcher configurations, and giving it enough context to be legal.

The interconnect study learned this the hard way (D313, D319): a proposer handed a list of knob
names invents combinations the tool rejects, and the search then spends its budget discovering
that the model does not know the rules. Bingo's knobs are unusually coupled — `pattern_len` is
`region_size / 64` and nothing else, three tables are fixed 16-way, the PHT's tag has to survive
its own index — so the prompt states every constraint, in the same words `config.py` enforces
them, and shows a legal example.

Everything the model returns is still validated. A prompt that explains the rules reduces illegal
proposals; it does not make validation optional, and an illegal proposal is recorded as refused
with the reason rather than silently dropped, so a run reports how well the proposer actually did.
"""

from __future__ import annotations

import json
import random
from typing import Any, Callable

from .config import BingoConfig, DEFAULT, storage_bytes

#: Kept short deliberately: this is a constraint sheet, not a tutorial. Every rule here is one the
#: simulator or `config.py` actually enforces, phrased the way the error message phrases it.
RULES = """\
HARD RULES. A configuration breaking any of these ABORTS the simulator; it is not a bad design,
it is an invalid one:
  * bingo_pattern_len MUST equal bingo_region_size / 64, exactly. They are one knob, not two.
  * bingo_region_size is a power of two from 64 to 4096 (a region cannot cross a 4 KB page).
  * bingo_ft_size, bingo_at_size, bingo_pf_streamer_size are 16-way: each must be 16 * a power of
    two (16, 32, 64, 128, 256, 512, 1024, 2048).
  * bingo_pht_size must equal bingo_pht_ways * a power of two; bingo_pht_ways is a power of two.
  * bingo_max_addr_width >= bingo_min_addr_width; both are 0..30.
  * bingo_pc_width is 0..30, and bingo_pc_width + bingo_min_addr_width > 0.
  * The tag must survive the index: for each table, key_bits - log2(sets) >= 0, where the FT/AT
    key is 48 - log2(region_size), the streamer key is 64 - log2(region_size), and the PHT key is
    bingo_pc_width + bingo_max_addr_width.
  * bingo_l2c_thresh is a float in 0.0..1.0. It costs NO storage — it is free under an area cap.

WHAT THE KNOBS DO. region_size/pattern_len set the spatial footprint Bingo learns per region.
ft_size and at_size bound how many regions are being observed and accumulated at once. pht_size
and pht_ways are the learned-pattern memory, and are usually where both the speedup and the area
are. pf_streamer_size bounds outstanding prefetches. l2c_thresh gates how confident a pattern must
be before the L2 issues a prefetch: lower is more aggressive.
"""


class NoProposals(RuntimeError):
    """The model returned nothing usable. Distinct from "returned illegal configurations"."""


#: The output budget for a proposal reply, in tokens. The local model's default is 1,200, and a
#: request for twenty configurations -- eleven knobs each, ~120 tokens of JSON apiece -- truncated
#: the array mid-object, parsed to nothing, and the run reported "the model returned no parsable
#: configuration" for a model that had produced nineteen good ones. 5,000 holds about forty.
NUM_PREDICT = 5000

#: The most configurations one reply is asked for, so a single call never approaches the budget
#: even when a model pads its answer. Larger asks are chunked, and later chunks see earlier ones.
PER_CALL = 12


def truncation_reason(text: str) -> str | None:
    """Why a reply looks cut off, so a run can say "ask for fewer" instead of "no proposals"."""
    body = text.strip()
    if not body:
        return "the model returned nothing"
    if body.count("[") > body.count("]") or body.count("{") > body.count("}"):
        return (f"the reply opens more brackets than it closes ({len(body)} chars) -- the "
                "output budget ran out mid-array; fewer configurations per call")
    return None


def build_prompt(baseline_ipc: dict[str, float], count: int,
                 measured: list[tuple[BingoConfig, float, int]] | None = None,
                 problem: str | None = None, trace_profile: str | None = None,
                 max_storage: int | None = None, learned: str | None = None,
                 human: str | None = None) -> str:
    """The proposal prompt: the goal, the rules, the TRACES, what is known, and the output shape.

    `trace_profile` is what the workload actually looks like -- PC concentration, stride
    behaviour, what the current prefetcher still misses -- from `flux_prefetcher.profile`. A
    proposer that has never seen the traces tunes Bingo's table sizes by folklore; one that
    knows there are 23,000 PCs and that 84% of accesses repeat a delta pair has a reason to pick
    a number.
    """
    evidence = f"\n{trace_profile}\n" if trace_profile else ""
    known = ""
    if measured:
        rows = "\n".join(
            (f"  geomean {g:.4f}, {s} bytes: " if g else "  proposed, not yet measured: ")
            + json.dumps({k: v for k, v in c.knobs().items()})
            for c, g, s in measured[:12])
        known = f"\nALREADY MEASURED (geomean speedup, storage, configuration):\n{rows}\n"

    goal = problem or (
        "Find Bingo L2 prefetcher configurations that maximise geomean IPC speedup over the "
        "no-prefetcher baseline on three 5G baseband traces.")
    if max_storage is not None:
        # Told, not discovered: a proposer that learns the budget from twenty refusals has spent
        # its whole round learning it.
        goal += (f"\nHARD BUDGET: total Bingo storage must be at or under {max_storage:,} bytes "
                 "(the shipped configuration is 35,096 B). A configuration over it is refused "
                 "without being measured. The pattern history table dominates: storage is "
                 "roughly pht_size x pht_ways x (pattern_len + tag) bits.")

    return f"""{goal}
{evidence}{(learned + chr(10)) if learned else ""}{(human + chr(10)) if human else ""}
{RULES}
The no-prefetcher baseline IPC per trace is {json.dumps(baseline_ipc)}.
The configuration the project ships is {json.dumps(DEFAULT.knobs())} with
bingo_l2c_thresh = {DEFAULT.l2c_thresh}, costing {storage_bytes(DEFAULT)} bytes of storage.
{known}
Propose {count} DIFFERENT configurations worth measuring. Vary them meaningfully — a set that
differs only in one table size teaches the search almost nothing.

Reply with ONLY a JSON array of {count} objects, each with exactly these keys:
bingo_region_size, bingo_pattern_len, bingo_pc_width, bingo_min_addr_width, bingo_max_addr_width,
bingo_ft_size, bingo_at_size, bingo_pht_size, bingo_pht_ways, bingo_pf_streamer_size,
bingo_l2c_thresh. No prose, no markdown fence.
"""


def parse_proposals(text: str) -> list[BingoConfig]:
    """Turn the model's reply into configurations, skipping anything malformed.

    Malformed is not the same as illegal: a reply that is not JSON, or an object missing keys, has
    no configuration in it to refuse. Those are dropped here; configurations that parse but break
    a rule survive to be refused WITH their reason by the caller.
    """
    from flux_llm import strip_markdown_fence

    try:
        parsed = json.loads(strip_markdown_fence(text))
    except (ValueError, TypeError):
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []

    fields = set(BingoConfig.__dataclass_fields__)
    out: list[BingoConfig] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        kwargs: dict[str, Any] = {}
        for key, value in item.items():
            name = key[len("bingo_"):] if key.startswith("bingo_") else key
            if name in fields:
                kwargs[name] = float(value) if name == "l2c_thresh" else int(value)
        if fields - set(kwargs) - {"l2c_thresh"}:
            continue                                   # missing an integer knob: nothing to judge
        try:
            out.append(BingoConfig(**kwargs))
        except (TypeError, ValueError):
            continue
    return out


def llm_proposer(problem: str | None = None, model: str | None = None,
                 timeout_s: float | None = None, num_predict: int = NUM_PREDICT,
                 ask: Callable[[str], str] | None = None) -> Callable[..., list[BingoConfig]]:
    """A `propose(baseline, count, rng)` callable backed by a local model.

    Returns configurations whether or not they are legal — `run_study` validates and records the
    illegal ones as refusals, which is how a run can report that the proposer misunderstood the
    space rather than hiding it. `ask` is injectable so the chunking can be tested without a
    model.
    """
    if ask is None:
        from flux_llm import local_proposer

        ask = local_proposer(model=model, num_predict=num_predict,
                             **({"timeout_s": timeout_s} if timeout_s else {}))

    def propose(*, baseline, count: int, rng: random.Random,
                measured: list[tuple[BingoConfig, float, int]] | None = None,
                trace_profile: str | None = None,
                max_storage: int | None = None,
                learned: str | None = None,
                human: str | None = None) -> list[BingoConfig]:
        """`count` configurations, asked for in chunks the output budget can hold.

        Each chunk sees what the earlier chunks proposed, as "already measured" rows without a
        score, so a second chunk does not restate the first.
        """
        out: list[BingoConfig] = []
        seen = list(measured or [])
        last_failure = ""
        remaining = max(1, count)
        while remaining > 0:
            ask_for = min(PER_CALL, remaining)
            prompt = build_prompt(dict(baseline.ipc), ask_for, measured=seen or None,
                                  problem=problem, trace_profile=trace_profile,
                                  max_storage=max_storage, learned=learned, human=human)
            reply = ask(prompt)
            got = parse_proposals(reply)
            if not got:
                last_failure = truncation_reason(reply) or "no parsable configuration in the reply"
                break
            out.extend(got)
            seen = seen + [(c, 0.0, 0) for c in got]
            remaining -= len(got)
        if not out:
            raise NoProposals(f"the model returned no parsable configuration: {last_failure}")
        return out

    return propose
