"""The design-guidance corpus (docs/decisions.md D244): retrievable through the REAL BM25
index, self-qualifying at chunk granularity (paragraphs travel alone into prompts, so each
must carry its own provenance class), and injectable into generation prompts where the
verifier — not the guidance — stays the judge."""

from __future__ import annotations


from flux_knowledge.retrieval import knowledge_lookup


def _hits(query: str, k: int = 5):
    return knowledge_lookup(query, standard_id="design-guidance", k=k)


def test_multiport_techniques_are_retrievable_with_their_costs():
    hits = _hits("multiple write ports XOR RAM count")
    assert hits, "the corpus must be in the default index"
    texts = " ".join(h.chunk.text for h in hits)
    # the XOR cost structure and its read-before-write caveat, in our own words
    assert "W x ((W-1) + R)" in texts
    assert "read-before-write" in texts or "first performs a read" in texts


def test_the_selection_rule_refuses_to_rank_globally():
    hits = _hits("which multiport technique to choose LVT XOR flip-flop")
    texts = " ".join(h.chunk.text for h in hits)
    # the honest core: no technique dominates, and ranking is deferred to THIS repo's
    # real evaluators — remembered FPGA plots must not decide ASIC questions
    assert "no technique dominates" in texts.lower()
    assert "real evaluators" in texts


def test_sram_port_cost_guidance_points_to_real_quantification():
    hits = _hits("SRAM port count area cost bitcell")
    texts = " ".join(h.chunk.text for h in hits)
    assert "faster than linearly with port count" in texts
    # magnitude questions are deferred to the real CACTI path, never to a remembered constant
    assert "flux_characterize_memory_level" in texts


def test_measured_here_claims_carry_their_decision_pointers_and_scope():
    hits = _hits("lane parallelism area latency doubling measured")
    texts = " ".join(h.chunk.text for h in hits)
    assert "docs/decisions.md D225/D237" in texts  # the placed-area pins
    # and the boundary of the claim travels IN the same chunk
    assert "NOT established outside that range" in texts


def test_sources_are_cited_never_quoted():
    """The Verbeure blog and LaForest paper are cited by name; the corpus text is original
    (the provenance file records the license check — no license on the blog repo, so no
    verbatim ingestion)."""
    hits = _hits("multiport memories composing block RAMs sources")
    texts = " ".join(h.chunk.text for h in hits)
    assert "Verbeure" in texts and "LaForest" in texts
    assert "own words" in texts


def test_guidance_reaches_the_rtl_generation_prompt_only_when_given():
    from flux_chia_nodes.generate_rtl import flux_generate_rtl_module

    spec = {
        "schema_version": "0.1.0",
        "id": "t/guided",
        "module_name": "PassThrough",
        "ports": [
            {"name": "a", "dir": "in", "dtype": "int", "bits": 8},
            {"name": "y", "dir": "out", "dtype": "int", "bits": 8},
        ],
        "behavior": "y equals a.",
        "test_vectors": [{"inputs": {"a": 3}, "expected": {"y": 3}}],
    }

    class _Scripted:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def prompt(self, text: str):
            self.prompts.append(text)

            class _R:
                result = ("module PassThrough (\n  input logic signed [7:0] a,\n"
                          "  output logic signed [7:0] y\n);\n  assign y = a;\nendmodule\n")

            return _R()

    guidance = "Prefer true-precision widths; an int8 path needs an 8x8 multiplier."
    guided = _Scripted()
    result = flux_generate_rtl_module(spec, llm=guided, guidance=guidance)
    assert result.success
    assert "Relevant design guidance (advisory" in guided.prompts[0]
    assert guidance in guided.prompts[0]

    plain = _Scripted()
    assert flux_generate_rtl_module(spec, llm=plain).success
    assert "design guidance" not in plain.prompts[0]


def test_interconnect_guidance_separates_published_conditions_from_measured_throughput():
    """The Clos entry is the corpus's sharpest case of a claim that is true and routinely
    misread (docs/decisions.md D267): "strictly non-blocking" is a circuit-switching statement,
    and a chunk that travels into a prompt without that boundary invites paying for a middle
    stage twice over."""
    texts = " ".join(h.chunk.text for h in _hits("clos non-blocking middle stage sizing"))
    assert "CIRCUIT switching" in texts
    assert "gain arrives at m = n" in texts
    assert "1953" in texts  # the claim is attributed, not floated


def test_interconnect_guidance_states_routing_policy_as_a_design_variable():
    """The largest single effect this repo measured in the fabric study, and the one most
    likely to be omitted from a quoted throughput."""
    texts = " ".join(h.chunk.text for h in _hits("routing policy path selection throughput"))
    assert "8.92" in texts and "13.54" in texts
    assert "is not a number" in texts


def test_interconnect_guidance_refuses_to_generalise_its_own_frequency_table():
    """Arity-vs-frequency numbers are the most quotable thing in the file and the most
    node-specific; the chunk carrying them has to carry their scope too."""
    texts = " ".join(h.chunk.text for h in _hits("selector arity frequency 600 MHz switches"))
    assert "not a general law" in texts
    assert "Re-measure" in texts


def test_the_corpus_warns_that_a_screened_frequency_is_not_a_placed_one():
    """The finding the decision rung produced (docs/decisions.md D280), written where the
    proposer can retrieve it: composing per-block measurements prices gates and no wire, so a
    screened frequency is optimistic by a margin that grows with stages and inter-stage links —
    measured at 707 -> 430 MHz for the worst case in the study."""
    texts = " ".join(h.chunk.text for h in _hits("screened frequency placed whole fabric"))
    assert "707 -> 430" in texts or "707 -&gt; 430" in texts
    assert "prices the" in texts and "none of the wire" in texts
    assert "do not treat a composed frequency as a commitment" in texts
