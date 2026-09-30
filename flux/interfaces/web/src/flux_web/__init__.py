"""`flux serve` (D683): the web interface -- accounts, applications (a document and its files),
runs started in the sandbox, and each run followed live: its task tree, its log, its agent
turns, its results and report. FastAPI; the pages are plain JavaScript modules, no build."""

from .app import create_app

__all__ = ["create_app"]
