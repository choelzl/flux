"""Typed operator guidance for a running design loop (docs/decisions.md D388)."""

from .channel import (FeedbackChannel, Note, drain_guidance, reload_notes,
                      render_guidance, scripted_channel)

__all__ = ["FeedbackChannel", "Note", "drain_guidance", "reload_notes", "render_guidance", "scripted_channel"]
