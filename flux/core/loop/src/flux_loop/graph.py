"""The loop as a graph of typed nodes (D425): the slides' drawing, in code.

Every phase the loop runs is one of these nodes; the timing tree, the task list and
the log color by the node's role, and a node that calls the model carries the
`model` mark. A problem never names a stage itself -- it plugs into the nodes.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FLOW", "GROUPS", "NODES", "Node", "node"]


@dataclass(frozen=True)
class Node:
    name: str          # the phase prefix, exactly as the timing tree shows it
    role: str          # io | mentor | orchestrator | generator | evaluator
    model: bool        # USUALLY calls the model -- a property, never the name: DSE may
                       # plan with or without one, and the tree marks actual model use
    doc: str


NODES: dict[str, Node] = {n.name: n for n in (
    Node("input", "io", False, "the task: statement, contract, gate, objectives, budget"),
    Node("output", "io", False, "the decision with its evidence, the report"),
    Node("knowledge", "mentor", False, "what is provided: the sheet, the library, the contract"),
    Node("records", "mentor", False, "what was measured and refused; resumed from, written back"),
    Node("extract", "mentor", False, "laws and duels read out of the record"),
    Node("feedback", "mentor", False, "the operator's notes, drained into prompts"),
    Node("gate", "orchestrator", False, "free refusals first: tools, validity, cooldowns"),
    Node("propose", "orchestrator", True, "the plan: which part next, which method, how to decompose"),
    Node("DSE", "orchestrator", False, "the search itself: the main loop over parts and steps"),
    Node("frontier", "orchestrator", False, "both objectives, whole"),
    Node("decide", "orchestrator", False, "the decision rule, applied"),
    # the LEFT drawing's coarse boxes are the right drawing's groups: named too, so a
    # stage that encloses a group is a node of the graph as well
    Node("generation", "generator", False, "the Generation box: the sub-loop of generate, test, repair, critique"),
    Node("evaluation", "evaluator", False, "the Evaluation box: template-fill, the costed stages, frontier, decide"),
    Node("generate", "generator", True, "a candidate: prototype, then target code"),
    Node("template-fill", "generator", False, "code the framework writes: tables, wrappers, composition"),
    Node("repair", "generator", True, "edits driven by a failure, on the text that exists"),
    Node("test", "evaluator", False, "correctness: build, fast vectors, the exhaustive judge"),
    Node("critique", "evaluator", True, "the adversary: a critique of a candidate, a plan or a decision"),
    Node("analytical", "evaluator", False, "the fast costed stage"),
    Node("simulation", "evaluator", False, "the slow costed stage"),
    Node("calibrate", "evaluator", False, "the fast stage corrected against the slow one"),
)}

#: The drawing's grouped boxes, in flow order.
GROUPS: tuple[tuple[str, ...], ...] = (
    ("input", "gate"),
    ("knowledge", "extract", "records", "feedback"),
    ("propose", "DSE"),
    ("generate", "template-fill"),
    ("test",),
    ("repair",),
    ("critique",),
    ("analytical", "frontier", "simulation", "calibrate"),
    ("decide", "output"),
)

#: The generation sub-loop and the outer loop, as node sequences.
FLOW = {
    "outer": ("input", "gate", "records", "knowledge", "feedback", "propose", "DSE",
              "template-fill", "analytical", "frontier", "simulation", "calibrate",
              "decide", "output"),
    "generation": ("generate", "test", "repair", "critique"),
}


def node(name: str) -> Node | None:
    """The node a phase name belongs to, by its prefix (before ':')."""
    head = name.split(":", 1)[0].strip()
    return NODES.get(head)


# The graph is the ONE table of roles (D427): the timing tree, the task list and the
# log color a phase by its node, through flux_profile.role_of. Older loops' phase
# names stay in flux_profile's own word list; a node here wins over that list.
try:
    from flux_profile import register_roles as _register_roles
except Exception:  # noqa: BLE001  -- flux_profile absent: nothing to color
    pass
else:
    _register_roles({n.name: n.role for n in NODES.values()})
