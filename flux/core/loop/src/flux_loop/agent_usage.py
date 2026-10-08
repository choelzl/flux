"""Reported agent usage, including Claude messages received before a timeout.

Claude emits multiple blocks and cumulative stream updates for one message ID. Keep
one snapshot per message, never sum its updates. A final result replaces these snapshots.
No text-length estimates stand in for tokens the agent has not reported.
"""

from __future__ import annotations

import json
import math
from typing import Any


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def _claude(usage: dict) -> dict[str, float]:
    out = {}
    inputs = [_number(usage.get(key)) for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
    if any(value is not None for value in inputs):
        out["tokens_in"] = sum(value or 0 for value in inputs)
    if (value := _number(usage.get("output_tokens"))) is not None:
        out["tokens_out"] = value
    if (value := _number(usage.get("cache_read_input_tokens"))) is not None:
        out["tokens_cached"] = value
    return out


def _sum_numbers(*values: Any) -> float | None:
    numbers = [_number(value) for value in values]
    return sum(value or 0 for value in numbers) if any(value is not None for value in numbers) else None


class AgentUsage:
    def __init__(self, output: str) -> None:
        self.output = output
        self.messages: dict[str, dict[str, float]] = {}
        self.current: dict[str, str] = {}
        self.total: dict[str, float] = {}
        self.final: dict[str, float] | None = None
        self.cost: float | None = None
        self.complete = False
        self.steps: set[str] = set()

    def _sum(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            if (value := _number(value)) is not None:
                self.total[key] = self.total.get(key, 0) + value

    def feed(self, event: dict) -> None:
        kind = event.get("type")
        if self.output == "claude":
            if kind == "result":
                self.cost = _number(event.get("total_cost_usd"))
                raw = event.get("usage")
                final = _claude(raw) if isinstance(raw, dict) else {}
                # Some crash results zero their usage. Preserve already reported messages.
                if final and not (event.get("is_error") and not any(final.values()) and self.messages):
                    self.final, self.complete = final, "tokens_in" in final and "tokens_out" in final
                    if self.cost is not None:
                        self.final["cost_usd"] = self.cost
                return
            parent = str(event.get("parent_tool_use_id") or "")
            raw = event.get("event") if kind == "stream_event" else event
            if not isinstance(raw, dict):
                return
            message = raw.get("message")
            if kind == "assistant" or raw.get("type") == "message_start":
                if not isinstance(message, dict):
                    return
                ident = message.get("id") or event.get("message_id") or self.current.get(parent)
                usage = message.get("usage")
                if ident:
                    self.current[parent] = str(ident)
            elif raw.get("type") == "message_delta":
                ident, usage = self.current.get(parent), raw.get("usage")
            elif raw.get("type") == "message_stop":
                self.current.pop(parent, None)
                return
            else:
                return
            if not ident or not isinstance(usage, dict):
                return
            snapshot = self.messages.setdefault(str(ident), {})
            # Fields can be omitted in deltas. Merge cumulative raw fields before
            # combining input + cache counts, which may arrive in separate updates.
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                if (value := _number(usage.get(key))) is not None:
                    snapshot[key] = max(snapshot.get(key, 0), value)
        elif self.output == "opencode" and kind == "step_finish":
            part = event.get("part") or {}
            if not isinstance(part, dict):
                return
            ident = part.get("id")
            if ident and str(ident) in self.steps:
                return
            if ident:
                self.steps.add(str(ident))
            tokens = part.get("tokens") or {}
            if not isinstance(tokens, dict):
                return
            cache = tokens.get("cache") or {}
            cache = cache if isinstance(cache, dict) else {}
            tin = _sum_numbers(tokens.get("input"), cache.get("read"), cache.get("write"))
            tout = _sum_numbers(tokens.get("output"), tokens.get("reasoning"))
            self._sum({"tokens_in": tin, "tokens_out": tout, "tokens_cached": cache.get("read"), "cost_usd": part.get("cost")})
            self.complete = part.get("reason") == "stop" and tin is not None and tout is not None
        elif self.output == "opencode" and kind == "step_start":
            self.complete = False
        elif self.output == "codex" and kind == "turn.completed":
            raw = event.get("usage") or {}
            if isinstance(raw, dict):
                self._sum({"tokens_in": raw.get("input_tokens"),
                           "tokens_out": _sum_numbers(raw.get("output_tokens"), raw.get("reasoning_output_tokens")),
                           "tokens_cached": raw.get("cached_input_tokens")})
                self.complete = _number(raw.get("input_tokens")) is not None and _number(raw.get("output_tokens")) is not None
        elif self.output == "codex" and kind == "turn.started":
            self.complete = False

    def values(self) -> dict[str, float]:
        if self.final is not None:
            return dict(self.final)
        if self.output != "claude":
            return dict(self.total)
        total: dict[str, float] = {}
        for snapshot in self.messages.values():
            for key, value in _claude(snapshot).items():
                total[key] = total.get(key, 0) + value
        if self.cost:
            total["cost_usd"] = self.cost
        return total

    def record(self, *, finished: bool) -> dict[str, Any]:
        values: dict[str, Any] = self.values()
        if self.output in {"claude", "opencode", "codex"}:
            values["tokens_complete"] = self.complete and (finished or self.final is not None)
        return values


def from_output(output: str, stdout: str) -> AgentUsage:
    tracker = AgentUsage(output)
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            tracker.feed(event)
    return tracker


def usage(output: str, stdout: str) -> dict[str, float]:
    """Public numeric totals; the transcript additionally records completeness."""
    return {key: value for key, value in from_output(output, stdout).values().items() if value}
