"""THE PROTOTYPE LANGUAGE (docs/decisions.md D514, review step 5.2): `python-int`, the
numpy-integer subset a prototype is written in so that it transcribes to hardware 1:1.

Three things belong to the language and to no application: `vectorize` (a prototype written
for one input -- `if`/`elif`, an early `return`, `min`/`max` on values -- made array form,
D486), `rules` (the hardware subset: integers only, `for` over a constant range, tables as
1-D constant arrays, division by a constant; a construct outside it named, D468/D478), and
`family` (knobs declared as module constants with a `SPACE` of choices, every member run and
the cheapest that passes chosen, D479; the judge is the problem's, as source). They lived in
the NLU package; nothing in them is FP16.
What IS an application's: the toolkit a prototype may call, the check that judges it, the
transpiler that spells it, the cost that ranks a family's members.
"""

from .family import bind, configurations, describe_search, family_harness, parse_family_output, space_of
from .rules import hardware_subset_violations
from .vectorize import VectorizeError, relocate_lines, vectorize

__all__ = ["VectorizeError", "bind", "configurations", "describe_search", "family_harness",
           "hardware_subset_violations", "parse_family_output", "relocate_lines", "space_of", "vectorize"]
