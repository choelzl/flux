"""The prototype language `python-int`: the numpy-integer subset a prototype is written in so
it transcribes to hardware 1:1 (D514).

Language-level, application-free: `vectorize` (a single-input prototype with `if`/`elif`,
early `return`, `min`/`max` made array form), `rules` (the hardware subset: integers only, `for`
over a constant range, 1-D constant tables, division by a constant) and `family` (module
constants with a `SPACE` of choices; every member runs and the cheapest passing one is chosen).
An application supplies the toolkit, the judging check, the transpiler and the family cost.
"""

from .family import bind, configurations, describe_search, family_harness, parse_family_output, space_of
from .rules import hardware_subset_violations
from .vectorize import VectorizeError, relocate_lines, vectorize

__all__ = ["VectorizeError", "bind", "configurations", "describe_search", "family_harness",
           "hardware_subset_violations", "parse_family_output", "relocate_lines", "space_of", "vectorize"]
