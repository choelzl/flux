"""The problem document (D519): what a `problem.yaml` says, how it is loaded and
checked, and the vocabulary of its `flow:` -- a statement, a contract, parts, a gate (how a
candidate is checked), costed stages (how it is measured), objectives (what "better" means), a
budget, a `space:` of knobs. What a document cannot say is a command beside it (D798-D803).

Commands carry placeholders: `{artifact}` (the candidate written to a file), `{home}` (the
document's folder), `{workdir}`, `{name}`, `{part}`, `{python}` (this interpreter), and `{knob}`
for each knob of `space:`. A gate is named checks run in order (D652); each prints its failures,
`count_re` (one integer group) or `fail_re` (one match per failure) says how the loop counts them,
and a non-zero exit with nothing counted is one failure. A check that exits 3 says the candidate
did not build (D594).

`flux_loop.task.PromptProblem` runs a document.

The package (D890): `spec` (TaskSpec, read and checked, its sub-tasks) and `load` (from a folder
or a file) are its face; `keys` the document's keys and TaskError; `commands` the placeholders
and `flux ...` heads; `gate` and `stages` flow.test and flow.measure; `space` the knobs and
points; `flow` the boxes folded into roles; `surface` what a document says read into the
loop's forms, `layout` those forms written back; `library` the files read; `describe` the
drawing in words. Everything another module imports is re-exported here.
"""

from __future__ import annotations

from .keys import TaskError, DOCUMENT_KEYS, EXTENSIONS, DOCUMENT_FILE, ALT_SUFFIXES  # noqa: F401
from .commands import BUILTIN_SUBS, _flux_program_tools, _digest_of, _command, _knob_subs, _substitute  # noqa: F401
from .gate import BUILD_FAILED, Check, Gate, DEFAULT_COUNT_RE, _gate  # noqa: F401
from .stages import Stage  # noqa: F401
from .space import point_doc, _point_name, _write_point  # noqa: F401
from .flow import FLOW_BOXES  # noqa: F401
from .library import confined, read_input, LIBRARY_FOLDER, library_folders, own_library, library_on  # noqa: F401
from .describe import describe_orchestrate, describe_stage, describe_flow  # noqa: F401
from .spec import Part, TaskSpec, resolve, _rig_for, _leaf  # noqa: F401
from .load import ManyDocuments, documents_in, alt_name, record_name, loadable, load_task, task_in, request_for  # noqa: F401

__all__ = ["BUILD_FAILED", "BUILTIN_SUBS", "Check", "DOCUMENT_KEYS", "FLOW_BOXES", "Gate", "Part", "Stage", "TaskError", "TaskSpec", "describe_flow", "load_task", "read_input", "request_for", "resolve"]
