"""The vocabulary of a problem document: its keys, the files it lives in, the error it raises."""

from __future__ import annotations


class TaskError(ValueError):
    """The document is not a task: the message names the field and what it should be."""


#: What a nested sub-task takes from its parent when it does not say (D455). `subtasks` is
#: deliberately absent: a child that inherited it would divide again, forever.
_INHERITED = ("contract", "language", "gate", "stages", "objectives", "knowledge", "skills",
              "params", "budget", "space", "ladder",
              "flow")


#: Every top-level key a problem document may say; any other is refused with the nearest
#: real key (D590).
DOCUMENT_KEYS = frozenset({
    "statement", "contract", "language", "parts",
    "flow", "subtasks", "max_subtasks", "objectives",
    "budget", "params", "ladder",
    "skills", "baseline"})
#: The fields `flow`'s boxes are read into (D775): the loop's own, never a document's key.
_LIFTED_KEYS = frozenset({"gate", "stages", "space", "seeds", "knowledge"})

#: set by the loader, never written: a sub-document's record name, `<parent>/<child>` (D455)
_INTERNAL_KEYS = frozenset({"_record", "_lifted", "_inherited"})

#: the file extension a language's candidates are written with (D628); another language `x`
#: writes `.x`
EXTENSIONS = {"systemverilog": ".sv", "verilog": ".v", "vhdl": ".vhd", "python": ".py", "text": ".txt",
              "yaml": ".yaml", "json": ".json", "c": ".c", "cpp": ".cpp", "c++": ".cpp", "cuda": ".cu", "opencl": ".cl", "rust": ".rs",
              "markdown": ".md", "shell": ".sh", "bash": ".sh", "chisel": ".scala", "scala": ".scala"}


#: D786: a problem is a folder; its document is this file in it, and the folder's name is its id.
DOCUMENT_FILE = "problem.yaml"
#: D787: beside it, `NAME.problem.yaml` -- another problem of the same loop, its record `<id>.NAME`.
ALT_SUFFIXES = (".problem.yaml", ".problem.yml")
