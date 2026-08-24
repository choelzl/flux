"""The design-guidance corpus (D244): retrievable through the real BM25 index, each chunk carrying
its own provenance class (paragraphs travel alone into prompts), and injectable into generation
prompts where the verifier stays the judge."""

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
    # no technique dominates; ranking is deferred to this repo's evaluators
    assert "no technique dominates" in texts.lower()
    assert "real evaluators" in texts


def test_sram_port_cost_guidance_points_to_real_quantification():
    hits = _hits("SRAM port count area cost bitcell")
    texts = " ".join(h.chunk.text for h in hits)
    assert "faster than linearly with port count" in texts
    # magnitude questions are deferred to a real measurement, never to a remembered constant
    assert "OpenROAD" in texts


def test_measured_here_claims_carry_their_decision_pointers_and_scope():
    hits = _hits("lane parallelism area latency doubling measured")
    texts = " ".join(h.chunk.text for h in hits)
    assert "docs/decisions.md D225/D237" in texts  # the placed-area pins
    # and the boundary of the claim travels IN the same chunk
    assert "NOT established outside that range" in texts


def test_sources_are_cited_never_quoted():
    """Sources are cited by name; the corpus text is original (the blog has no license)."""
    hits = _hits("multiport memories composing block RAMs sources")
    texts = " ".join(h.chunk.text for h in hits)
    assert "Verbeure" in texts and "LaForest" in texts
    assert "own words" in texts



def test_interconnect_guidance_separates_published_conditions_from_measured_throughput():
    """The Clos chunk carries its boundary: "strictly non-blocking" is a circuit-switching
    statement (D267)."""
    texts = " ".join(h.chunk.text for h in _hits("clos non-blocking middle stage sizing"))
    assert "CIRCUIT switching" in texts
    assert "gain arrives at m = n" in texts
    assert "1953" in texts  # the claim is attributed, not floated


def test_interconnect_guidance_states_routing_policy_as_a_design_variable():
    """Routing policy is stated as a design variable, with the fabric study's measured effect
    (the largest it found)."""
    texts = " ".join(h.chunk.text for h in _hits("routing policy path selection throughput"))
    assert "8.92" in texts and "13.54" in texts
    assert "is not a number" in texts


def test_interconnect_guidance_refuses_to_generalise_its_own_frequency_table():
    """Arity-vs-frequency numbers travel with their scope (they are node-specific)."""
    texts = " ".join(h.chunk.text for h in _hits("selector arity frequency 600 MHz switches"))
    assert "not a general law" in texts
    assert "Re-measure" in texts


def test_the_corpus_warns_that_a_screened_frequency_is_not_a_placed_one():
    """Retrievable: composed per-block measurements price no wire, so a screened frequency is
    optimistic, more so with more stages and links (D280)."""
    texts = " ".join(h.chunk.text for h in _hits("screened frequency placed whole fabric"))
    assert "707 -> 430" in texts or "707 -&gt; 430" in texts
    assert "prices the" in texts and "none of the wire" in texts
    assert "do not treat a composed frequency as a commitment" in texts
