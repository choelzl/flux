"""flux_tui -- a btop-style curses UI for any Flux loop (docs/decisions.md D390).

Panels on number keys (1 task, 2 timing, 3 results, 4 log, 5 feedback, 6 help), a
one-line feedback prompt feeding the loops' existing guidance seam (D388), and an
event bus the loop writes from its worker thread. The curses shell is thin; panel
content and the line editor are pure functions, tested without a terminal.
"""

from .events import BusWriter, EventBus
from .input import LineEditor, TuiFeedback
from .panels import PANELS, build
from .app import demo_run, demo_tui, run_tui

__all__ = ["BusWriter", "EventBus", "LineEditor", "TuiFeedback", "PANELS", "build",
           "demo_run", "demo_tui", "run_tui"]
