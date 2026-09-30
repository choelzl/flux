"""Typed operator guidance for a running design loop (docs/decisions.md D388)."""

from .channel import (FeedbackChannel, InboxChannel, Joined, Note, drain_guidance, guidance_lesson, note_sink,
                      reload_notes, render_guidance, scripted_channel)

__all__ = ["FeedbackChannel", "InboxChannel", "Joined", "Note", "drain_guidance", "guidance_lesson", "note_sink",
           "reload_notes", "render_guidance", "scripted_channel"]
