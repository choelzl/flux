"""Panel content builders: pure functions from a bus snapshot to lines of text.

Kept curses-free on purpose -- every panel is testable as (state in, lines out), and
the curses shell only clips, pads and paints. Panel numbering follows the btop
convention the TUI advertises: 1 task, 2 timing, 3 results, 4 log, 5 feedback, 6 help.
"""

from __future__ import annotations

import time
from typing import Any

PANELS = {
    "1": "task", "2": "timing", "3": "results", "4": "log", "5": "feedback", "6": "info",
}


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def _fmt_dur(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:5.1f}s"
    return f"{_fmt_elapsed(seconds):>6}"


def _fmt_params(params: dict[str, Any], width: int = 46) -> str:
    if not params:
        return ""
    text = " ".join(f"{k}={v}" for k, v in params.items())
    return text if len(text) <= width else text[: width - 1] + "…"


def _detail_block(t: dict[str, Any], now: float, label: str,
                  started_at: float) -> list[str]:
    """One task in DEPTH: every parameter on its own line, long text (an LLM prompt,
    a command line) rendered as an indented block rather than truncated to a cell."""
    dur = (t["t1"] or now) - t["t0"]
    status = ("running" if t["t1"] is None
              else ("ok" if t["ok"] else "FAILED"))
    lines = [f"{label}: {t['name']}",
             f"  status:   {status}    duration: {_fmt_dur(dur).strip()}    "
             f"started at +{_fmt_elapsed(t['t0'] - started_at)}"]
    if t["why"]:
        lines.append(f"  why:      {t['why']}")
    if t["note"]:
        lines.append(f"  note:     {t['note']}")
    long_params = {k: v for k, v in t["params"].items()
                   if isinstance(v, str) and len(v) > 60}
    short = {k: v for k, v in t["params"].items() if k not in long_params}
    if short:
        lines.append("  params:")
        for k, v in short.items():
            lines.append(f"    {k} = {v}")
    for k, v in long_params.items():
        lines.append(f"  {k} ({len(v)} chars):")
        block = v.splitlines() or [""]
        head, tail = block[:24], block[-6:] if len(block) > 30 else []
        for ln in head:
            lines.append("    │ " + ln)
        if tail:
            lines.append(f"    │ … ({len(block) - len(head) - len(tail)} lines elided)")
            for ln in tail:
                lines.append("    │ " + ln)
    return lines


def task_panel(snap: dict[str, Any], now: float | None = None) -> list[str]:
    """THE CURRENT TASK, in depth (D391): what is running right now, why, for how
    long, and everything the loop attached to it -- an LLM task shows its actual
    prompt, a tool task its trace/config. Between tasks it shows the last finished
    one the same way, so the panel is never empty mid-run. The history table lives
    in the timing tab (2)."""
    now = now or time.time()
    lines = []
    state = ("FAILED: " + snap["error"] if snap.get("error")
             else "finished" if snap["finished"] else "running")
    # active run time only: frozen when finished, and idle between reruns not counted
    lines.append(f"state: {state}    elapsed: {_fmt_elapsed(snap['elapsed_s'])}")
    tasks = snap["tasks"]
    if not tasks:
        lines.append("current: (no task reported yet)")
        return lines

    stage = next((t for t in reversed(tasks) if t["kind"] == "mark"), None)
    if stage:
        lines.append(f"stage: {stage['name']}"
                     + (f" — {stage['why']}" if stage["why"] else "")
                     + f"    (since +{_fmt_elapsed(stage['t0'] - snap['started_at'])})")
    lines.append("")

    running = [t for t in tasks if t["t1"] is None]
    if running:
        for i, t in enumerate(running):
            label = "CURRENT" if len(running) == 1 else f"CURRENT {i + 1}/{len(running)}"
            lines += _detail_block(t, now, label, snap["started_at"])
            lines.append("")
    else:
        last = next((t for t in reversed(tasks)
                     if t["t1"] is not None and t["kind"] != "mark"), None)
        lines.append("(between tasks)")
        lines.append("")
        if last is not None:
            lines += _detail_block(last, now, "LAST FINISHED", snap["started_at"])
    lines.append("")
    lines.append("history and per-phase totals: tab 2 (timing)")
    return lines


def task_history_lines(snap: dict[str, Any], limit: int = 40) -> list[str]:
    """The compact task history table (timing tab): when, duration, ok/FAIL, task,
    why, params-in-brief; stage headlines threaded through as section lines."""
    tasks = snap["tasks"]
    if not tasks:
        return []
    lines = [f"{'when':>8} {'dur':>7} {'ok':>4}  task"]
    shown = 0
    for t in reversed(tasks):
        if t["t1"] is None:
            continue
        if t["kind"] == "mark":
            lines.append(f"{_fmt_elapsed(t['t0'] - snap['started_at']):>8} "
                         f"{'':>7} {'':>4}  ── {t['name']} ──")
            shown += 1
        else:
            ok = "ok" if t["ok"] else "FAIL"
            why = f" — {t['why']}" if t["why"] else ""
            p = _fmt_params({k: v for k, v in t["params"].items()
                             if not (isinstance(v, str) and len(v) > 60)})
            lines.append(f"{_fmt_elapsed(t['t0'] - snap['started_at']):>8} "
                         f"{_fmt_dur(t['t1'] - t['t0']):>7} {ok:>4}  {t['name']}{why}"
                         + (f"  [{p}]" if p else "")
                         + (f"  {t['note']}" if t["note"] else ""))
            shown += 1
        if shown >= limit:
            break
    return lines


def timing_panel(snap: dict[str, Any]) -> list[str]:
    """Per-phase totals (flux_profile, both clocks) followed by the task history
    table -- where the time went in aggregate, then call by call."""
    try:
        from flux_profile import report_lines

        lines = report_lines(total_s=snap.get("elapsed_s"))
    except Exception as exc:  # noqa: BLE001 -- the panel reports, never crashes the UI
        lines = [f"(flux_profile unavailable: {exc})"]
    if lines == ["(no timing recorded)"]:
        lines = []          # the history below says more than that line does
    history = task_history_lines(snap)
    if history:
        lines += ([""] if lines else []) + ["task history (newest first):"] + history
    return lines or ["(no phases recorded yet)"]


def _tidy(lines: list[str]) -> list[str]:
    """Collapse runs of blank lines and strip the edges -- a report pasted through a
    capture arrives with printf spacing that reads as bloat in a panel."""
    out: list[str] = []
    for line in lines:
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line.rstrip())
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def results_panel(snap: dict[str, Any]) -> list[str]:
    lines = _tidy(list(snap["results"]))
    if not lines:
        lines = ["(no results yet -- the loop's report lands here when it finishes)"]
    meas = snap["measurements"]
    if meas:
        lines += ["", f"─── measurements ({len(meas)} so far, newest last) ───"]
        keys = list(meas[-1].keys())[:6]
        lines.append("  " + "  ".join(f"{k:>12.12}" for k in keys))
        for row in meas[-12:]:
            lines.append("  " + "  ".join(_cell(row.get(k)) for k in keys))
    return lines


def _cell(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:>12.3f}"
    return f"{str(v):>12.12}"


def log_panel(snap: dict[str, Any]) -> list[str]:
    return list(snap["log"]) or ["(quiet so far)"]


def feedback_panel(notes: list[Any], enabled: bool) -> list[str]:
    if not enabled:
        return ["feedback is disabled for this run (no model consumes it here)."]
    lines = [
        "Type in the prompt line below and press Enter; the note reaches the loop at",
        "its next drain point as HUMAN GUIDANCE -- advisory, every candidate still",
        "passes the same gates (D388).",
        "",
        f"notes this run: {len(notes)}",
    ]
    for n in notes[-20:]:
        lines.append(f"  · {getattr(n, 'text', n)}")
    return lines


def info_panel(snap: dict[str, Any], info: dict[str, Any],
               notes: list[Any]) -> list[str]:
    """The run's identity (D393): everything that used to crowd the header or exists
    only in the shell command -- what is running, on what inputs, with which model,
    how many passes -- plus a compact key reference at the tail. Demos fill `info`;
    the TUI adds what it knows live."""
    lines = ["this run:"]
    for k, v in info.items():
        if not k.startswith("_"):        # internal keys (run counter) render below
            lines.append(f"  {k:<16} {v}")
    lines.append(f"  {'runs':<16} {info.get('_runs', 1)} "
                 f"(r toggles looping; resuming loops continue their study)")
    lines.append(f"  {'active time':<16} {_fmt_elapsed(snap.get('elapsed_s', 0.0))}")
    try:
        from flux_llm import default_local_model, think_override

        ov = think_override()
        think = {None: "proposer default", True: "ON (t to cycle)",
                 False: "OFF (t to cycle)"}[ov]
        lines.append(f"  {'model':<16} {default_local_model()}   reasoning: {think}")
    except Exception:  # noqa: BLE001
        pass
    if notes:
        lines.append(f"  {'feedback notes':<16} {len(notes)} this session")
    meas = snap.get("measurements") or []
    if meas:
        lines.append(f"  {'measurements':<16} {len(meas)} rows (tab 3)")
    lines += [
        "",
        "keys:  1-6 tabs · ↑/↓ PgUp/PgDn scroll · f feedback (Esc/⏎) ·",
        "       t think cycle · r loop on/off (on: rerun forever; off: hold at end) ·",
        "       q quit (qq abandons)",
        "",
        "The TUI renders the loop's own reporting; nothing here is data the loop",
        "does not already write to its provenance.",
    ]
    return lines


def build(panel: str, snap: dict[str, Any], notes: list[Any],
          feedback_enabled: bool, info: dict[str, Any] | None = None) -> list[str]:
    if panel == "task":
        return task_panel(snap)
    if panel == "timing":
        return timing_panel(snap)
    if panel == "results":
        return results_panel(snap)
    if panel == "log":
        return log_panel(snap)
    if panel == "feedback":
        return feedback_panel(notes, feedback_enabled)
    return info_panel(snap, info or {}, notes)
