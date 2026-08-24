"""`flux_llm.propose` (D426): one call for every proposer shape. The loop used to probe
for schema support by calling and catching `TypeError`, which also swallowed a `TypeError`
raised inside the model call; the shape is now read from the signature, once per class."""

from __future__ import annotations

import pytest

from flux_llm import LLMProposer, StructuredProposer, accepts_schema, propose


class Plain:
    def __init__(self):
        self.calls = []

    def propose(self, prompt: str) -> str:
        self.calls.append((prompt, None))
        return "plain"


class Structured:
    def __init__(self):
        self.calls = []

    def propose(self, prompt: str, schema: dict | None = None) -> str:
        self.calls.append((prompt, schema))
        return "structured"


class Broken:
    def propose(self, prompt: str, schema: dict | None = None) -> str:
        raise TypeError("the model call itself failed")


def test_schema_goes_only_to_a_proposer_that_takes_one():
    p, s = Plain(), Structured()
    assert propose(p, "q", {"type": "object"}) == "plain"
    assert p.calls == [("q", None)]
    assert propose(s, "q", {"type": "object"}) == "structured"
    assert s.calls == [("q", {"type": "object"})]


def test_structured_off_or_no_schema_means_a_plain_call():
    s = Structured()
    propose(s, "q", {"type": "object"}, structured=False)
    propose(s, "q")
    assert s.calls == [("q", None), ("q", None)]


def test_a_type_error_inside_the_call_is_not_mistaken_for_no_schema_support():
    with pytest.raises(TypeError, match="model call itself"):
        propose(Broken(), "q", {"type": "object"})


def test_accepts_schema_reads_the_signature_and_the_protocols_agree():
    assert accepts_schema(Structured()) and not accepts_schema(Plain())
    assert isinstance(Plain(), LLMProposer) and isinstance(Structured(), LLMProposer)
    assert isinstance(Structured(), StructuredProposer)

    class Kwargs:
        def propose(self, prompt, **kw):
            return "kw"

    assert accepts_schema(Kwargs())
