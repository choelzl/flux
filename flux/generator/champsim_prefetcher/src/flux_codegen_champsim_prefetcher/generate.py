"""Asking a model for a prefetcher, and giving it enough to write one that compiles.

THE TARGET IS SMALL, which is why this is worth attempting at all. `Prefetcher` has three pure
virtuals and only one of them does anything:

    virtual void invoke_prefetcher(uint64_t pc, uint64_t address, uint8_t cache_hit,
                                   uint8_t type, std::vector<uint64_t> &pref_addr) = 0;

The simulator's own `stride.cc` is 140 lines. That is a far smaller surface than the RTL and
SystemC generation this repository already does, and unlike those two there are NO test vectors to
satisfy: any address a prefetcher emits is legal, the cache simply absorbs it. The only verdict is
whether IPC improved, which the study already measures.

WHAT THE MODEL WRITES AND WHAT IT DOES NOT. One header, containing the whole class inline. The
`.cc` that gives the vtable a home, the dispatch branch in `multi.l2c_pref` and the knob
declarations in `knobs.cc` are all mechanical — see `harness.install`. That is D48's rule for RTL
applied unchanged, and it matters more here: a model editing a 300-line dispatch it did not write
has many ways to break every OTHER prefetcher in the build.

THE COMPILER IS THE FEEDBACK. A rebuild is seconds against six minutes for one evaluation, so a
candidate can be repaired several times and still cost less than measuring it once. Only the FIRST
diagnostic is fed back: g++ reports a cascade after the first structural mistake, and four hundred
lines of consequences bury the cause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .harness import class_name_for

#: The interface, verbatim from `inc/prefetcher.h`, plus the two things a generated class needs
#: that are not in it: where the block-address shift comes from, and how knobs are reached.
INTERFACE = '''\
Every L2 prefetcher subclasses this (inc/prefetcher.h, verbatim):

    class Prefetcher {
    protected:
       std::string type;
    public:
       Prefetcher(std::string _type) {type = _type;}
       virtual void invoke_prefetcher(uint64_t pc, uint64_t address, uint8_t cache_hit,
                                      uint8_t type, std::vector<uint64_t> &pref_addr) = 0;
       virtual void dump_stats() = 0;
       virtual void print_config() = 0;
    };

`invoke_prefetcher` is called on every L2 access. Push the FULL BYTE ADDRESSES you want prefetched
into `pref_addr`; the cache does the rest. `address` is a byte address, `LOG2_BLOCK_SIZE` (from
champsim.h) is 6, so `address >> LOG2_BLOCK_SIZE` is the cache-line number and
`line << LOG2_BLOCK_SIZE` turns one back into an address. `cache_hit` is 1 on a hit. Prefetching
across a 4 KB page boundary is wasted work: the simulator drops those.
'''

#: What a working one looks like. Real, compiled, and measured: this beat `stride` on the study's
#: traces (1.0069 against 1.0066) and added +0.0026 to Bingo, so it is an existence proof rather
#: than a sketch.
EXAMPLE = '''\
A complete, working example — per-PC stride detection with a confidence counter:

    #ifndef EXSTRIDE_H
    #define EXSTRIDE_H
    #include <cstdint>
    #include <unordered_map>
    #include <vector>
    #include <iostream>
    #include "prefetcher.h"
    #include "champsim.h"

    namespace knob { extern uint32_t exstride_degree; extern uint32_t exstride_max_trackers; }

    class ExstridePrefetcher : public Prefetcher
    {
       struct T { uint64_t last; int64_t stride; uint32_t conf; };
       std::unordered_map<uint64_t, T> t_;
       uint64_t issued_ = 0;
    public:
       ExstridePrefetcher(std::string type) : Prefetcher(type) {}
       void invoke_prefetcher(uint64_t pc, uint64_t address, uint8_t, uint8_t,
                              std::vector<uint64_t> &pref_addr)
       {
          uint64_t line = address >> LOG2_BLOCK_SIZE;
          auto it = t_.find(pc);
          if (it == t_.end()) {
             if (t_.size() >= knob::exstride_max_trackers) t_.clear();
             t_[pc] = {line, 0, 0};
             return;
          }
          int64_t d = (int64_t)line - (int64_t)it->second.last;
          if (d != 0 && d == it->second.stride) { if (it->second.conf < 3) it->second.conf++; }
          else { it->second.stride = d; it->second.conf = 0; }
          it->second.last = line;
          if (it->second.conf >= 2 && it->second.stride != 0)
             for (uint32_t k = 1; k <= knob::exstride_degree; k++) {
                pref_addr.push_back((line + k * it->second.stride) << LOG2_BLOCK_SIZE);
                issued_++;
             }
       }
       void dump_stats() { std::cout << "exstride_issued " << issued_ << std::endl; }
       void print_config() { std::cout << "exstride_degree " << knob::exstride_degree << std::endl; }
    };
    #endif
'''

RULES = '''\
HARD RULES — breaking any of these means it does not compile, or does not link:

  * Emit ONE header and nothing else. No .cc, no markdown prose outside the code.
  * Define every method INLINE in the class body. Nothing is compiled separately.
  * Guard it: #ifndef NAME_H / #define NAME_H / #endif, upper-cased from the name you are given.
  * The class MUST be named exactly as instructed and MUST inherit `public Prefetcher`.
  * The constructor takes `std::string type` and passes it to `Prefetcher(type)`.
  * Implement all three virtuals. `dump_stats` and `print_config` may just print.
  * Declare every knob you use as `namespace knob { extern uint32_t NAME; }` at file scope, and
    list those knobs in your reply so they can be declared for you. They are `uint32_t` only.
  * Include what you use: <cstdint>, <vector>, <iostream>, plus any container you touch, and
    "prefetcher.h" and "champsim.h". `std::find`/`std::sort` need <algorithm> — the most common
    way a generated prefetcher fails to compile.
  * C++11. No C++14/17 features, no external libraries, no threads, no file I/O, no rand().
  * Keep per-access work small and memory bounded — this runs on every L2 access for 250 million
    instructions, and an unbounded map will exhaust the machine before it finishes.
  * KEEP IT UNDER 70 LINES. This is a hard budget, not a style note: the local model generates at
    a few tokens a second, so a design twice this long takes twice as long to write and is no more
    likely to be a good idea. The worked example below is 35 lines and beat `stride`.
  * No comments explaining what the code obviously does. Spend the budget on the mechanism.
'''


@dataclass
class PrefetcherProposal:
    """One generated design: its name, its header, its knobs, and why the model chose it."""

    name: str
    header: str
    knobs: dict[str, int] = field(default_factory=dict)
    rationale: str = ""

    @property
    def class_name(self) -> str:
        return class_name_for(self.name)


def build_prompt(name: str, *, beat: str, beat_geomean: float,
                 already_tried: list[tuple[str, str, float]] | None = None,
                 problem: str | None = None, trace_profile: str | None = None) -> str:
    """The generation prompt: the interface, a working example, the rules, the TRACES, the target.

    `trace_profile` is the evidence: what the access stream looks like and what the reference
    stack still misses, from `flux_prefetcher.profile`. Without it the model designs for a
    one-sentence description of the workload written from a folder name, and every design it
    produced that way was a per-PC stride tracker -- for a trace with 23,000 PCs where constant
    strides are 23% of accesses.
    """
    history = ""
    if already_tried:
        rows = "\n".join(f"  {n}: geomean {g:.4f} — {note}" for n, note, g in already_tried[-6:])
        history = (f"\nALREADY TRIED (do not repeat these; the number is geomean IPC speedup over "
                   f"no prefetcher):\n{rows}\n")

    goal = problem or (
        "Design an L2 cache prefetcher for 5G baseband workloads: FFT-heavy signal processing "
        "with large strided array traversals, channel-estimation matrix work, and bursty "
        "packet buffers.")

    evidence = f"\n{trace_profile}\n" if trace_profile else ""
    return f"""{goal}
{evidence}
TARGET: beat `{beat}`, which reaches geomean {beat_geomean:.4f} IPC speedup over no prefetcher on
these traces. A design that merely matches it is not interesting; find a different idea, not a
reparameterisation.

YOU HAVE TWO WAYS TO WIN, and the second is easier. Your prefetcher will be measured BOTH alone
and running ALONGSIDE `{beat}` — the L2 slot takes several prefetchers at once. So you can either
beat it outright, or catch what it MISSES: a design that is weak by itself but complementary can
still win. On these traces, adding a prefetcher was worth about eight times more than tuning an
existing one's parameters.

WHAT IS ALREADY THERE, so you do not rebuild it: `bingo` is spatial — it learns which offsets
within a memory region get touched together. `sms` is also spatial, keyed on the PC that first
touched a region. `stride` follows constant per-PC deltas. Between them, constant strides and
dense within-page patterns are covered. What is NOT covered is the opening: irregular or
pointer-chasing access, patterns that repeat across pages rather than within one, correlations
between addresses that no fixed delta relates, and anything needing history longer than one step.

{INTERFACE}
{RULES}
{EXAMPLE}
{history}
Write a prefetcher called `{name}` — class `{class_name_for(name)}`, guard `{name.upper()}_H`,
knobs prefixed `{name}_`.

Reply with exactly two sections and nothing else:

IDEA: one or two sentences on the mechanism and why it should beat the target on this workload.

```cpp
...the complete header...
```

Then a final line listing your knobs and their defaults, as `KNOBS: name=value, name=value`.
"""


_KNOBS_RE = re.compile(r"^\s*KNOBS:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_IDEA_RE = re.compile(r"^\s*IDEA:\s*(.+?)(?=\n\s*```|\Z)", re.MULTILINE | re.DOTALL | re.IGNORECASE)


def truncation_reason(reply: str) -> str | None:
    """Why this reply looks CUT OFF rather than merely wrong, or None.

    Worth distinguishing, because the two have different fixes and look identical from the
    parser's side. The first live run of this loop reported "the model returned no header" three
    times; the replies each held a perfectly good header that stopped mid-statement, because
    `DEFAULT_NUM_PREDICT` is 1200 tokens — sized for this repository's JSON proposals, and about a
    third of what a C++ prefetcher needs. Nothing in "no header" pointed at an output budget.
    """
    if not reply.strip():
        return "the model returned nothing at all"
    if reply.count("```") % 2 == 1:
        return "the reply opens a code fence and never closes it — output budget exhausted"
    if "#ifndef" in reply and "#endif" not in reply:
        return "the header opens an include guard and never closes it — output budget exhausted"
    if reply.count("{") > reply.count("}") + 2:
        return "the code has far more opening braces than closing ones — likely truncated"
    return None


def parse_proposal(name: str, reply: str) -> PrefetcherProposal | None:
    """Pull the header, the knobs and the idea out of a reply, or None if there is no header.

    Deliberately tolerant about everything except the header: a model that forgets the KNOBS line
    has still written a prefetcher, and one that wraps its code in a fence has done nothing wrong.
    A reply with no C++ in it has nothing to build.
    """
    fenced = re.findall(r"```(?:cpp|c\+\+|c)?\s*\n(.*?)```", reply, re.DOTALL)
    header = next((block for block in fenced if "class" in block and "Prefetcher" in block), None)
    if header is None:
        # unfenced: take from the guard or the first include to the last #endif
        start = re.search(r"^\s*#(ifndef|pragma once|include)", reply, re.MULTILINE)
        end = reply.rfind("#endif")
        if start is None or end < 0:
            return None
        header = reply[start.start():end + len("#endif")]
    header = header.strip() + "\n"
    if "class" not in header or "Prefetcher" not in header:
        return None

    knobs: dict[str, int] = {}
    match = _KNOBS_RE.search(reply)
    if match:
        for pair in match.group(1).split(","):
            if "=" not in pair:
                continue
            key, _, value = pair.partition("=")
            key, value = key.strip(), value.strip()
            if re.fullmatch(r"[a-z][a-z0-9_]*", key) and re.fullmatch(r"\d{1,9}", value):
                knobs[key] = int(value)
    # A knob the header needs but the KNOBS line omitted still has to be declared, or the build
    # fails at LINK time with an undefined reference that names no cause. Two places to look, and
    # both matter: the `namespace knob { extern uint32_t NAME; }` block is the authoritative
    # declaration, and `knob::NAME` catches anything used without being declared there.
    for declared in re.findall(r"extern\s+uint32_t\s+([a-z][a-z0-9_]*)\s*;", header):
        knobs.setdefault(declared, 4)
    for referenced in re.findall(r"knob::([a-z][a-z0-9_]*)", header):
        knobs.setdefault(referenced, 4)

    idea = _IDEA_RE.search(reply)
    return PrefetcherProposal(name=name, header=header, knobs=knobs,
                              rationale=(idea.group(1).strip()[:400] if idea else ""))


def repair_prompt(proposal: PrefetcherProposal, error: str) -> str:
    """Feed the compiler's own first diagnostic back. Nothing else changes."""
    return f"""Your prefetcher `{proposal.name}` did not compile.

The first error from g++ (C++11):

    {error}

{RULES}
Fix it and reply with the corrected complete header in one ```cpp fence, then the KNOBS line.
Change as little as possible — this is a compile fix, not a redesign.

Your previous version:

```cpp
{proposal.header}```
"""


def inert_repair_prompt(proposal: PrefetcherProposal, diagnosis: str) -> str:
    """Feed back what the simulator's counters said about a design that compiled but did nothing.

    A compile error names a line; an inert design names nothing, and the first loop printed the
    diagnosis and moved on to the next idea (D360). The counters distinguish the two common
    bugs -- an emit path that never executes, and addresses that are wrong -- and either is a
    few lines' fix, which is cheaper than a fresh design and far cheaper than measuring one.
    """
    return f"""Your prefetcher `{proposal.name}` compiled and ran on every trace, but it changed nothing.

What the simulator's own counters say:

    {diagnosis}

This is a LOGIC bug, not a bad idea: keep the mechanism, fix the path that should emit
prefetches. Typical causes: a confidence counter that can never reach its threshold; a delta
compared against an address instead of a line number; a table keyed by PC that a stream of
~20,000 distinct PCs evicts before any entry is reused; pushing a line number where a BYTE
address (line << LOG2_BLOCK_SIZE) was expected.

{RULES}
Reply with the corrected complete header in one ```cpp fence, then the KNOBS line.
Change as little as possible -- this is a repair, not a redesign.

Your previous version:

```cpp
{proposal.header}```
"""


#: The three pure virtuals, and enough of each signature to tell an override from a near-miss.
_REQUIRED = (
    ("invoke_prefetcher", ("uint64_t", "uint8_t", "std::vector<uint64_t>")),
    ("dump_stats", ()),
    ("print_config", ()),
)


def unbuildable_reason(proposal: "PrefetcherProposal") -> str | None:
    """Why this header cannot possibly build, or None. Checked BEFORE spending a compile.

    One live round was lost to `invalid new-expression of abstract class type` — a pure virtual
    left unimplemented, usually because a signature drifted. The compiler catches it in sixty
    seconds and the repair costs another generation; this catches it in microseconds and can say
    which method is missing, which is more than the compiler's message does.

    Deliberately shallow: it looks for the shapes that make a class abstract or unregisterable,
    not for whether the design is any good. That question is answered by measuring it.
    """
    header = proposal.header
    if f"class {proposal.class_name}" not in header:
        return f"the class is not named {proposal.class_name}"
    if "public Prefetcher" not in header:
        return f"{proposal.class_name} does not inherit `public Prefetcher`"
    for method, fragments in _REQUIRED:
        if method not in header:
            return f"`{method}` is not implemented, so the class stays abstract"
        for fragment in fragments:
            if fragment not in header:
                return (f"`{method}` does not take {fragment} — a signature that does not match "
                        "the base class overrides nothing and leaves the class abstract")
    if f"{proposal.class_name}(" not in header:
        return "there is no constructor taking the prefetcher's type string"
    return None
