"""flux_tui -- a btop-style curses UI for any Flux loop.

Panels on number keys (1 task, 2 timing, 3 results, 4 log, 5 feedback, 6 mentor, 9 info), a
one-line feedback prompt feeding the loop's guidance seam, and an event bus the loop writes
from its worker thread. Panel content and the line editor are pure functions, tested without
a terminal.
"""

from .events import BusWriter, EventBus
from .input import LineEditor, TuiFeedback
from .panels import PANELS, build
from .app import demo_run, demo_tui, run_tui
from .setup import SetupForm, run_setup

__all__ = ["BusWriter", "EventBus", "LineEditor", "TuiFeedback", "PANELS", "build",
           "demo_run", "demo_tui", "run_tui", "SetupForm", "run_setup"]
