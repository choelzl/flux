"""Panel content builders: pure functions from a bus snapshot to lines of text.

Kept curses-free on purpose -- every panel is testable as (state in, lines out), and
the curses shell only clips, pads and paints. Panel numbering follows the btop
convention the TUI advertises: 1 task, 2 timing, 3 results, 4 log, 5 feedback,
6 mentor, 9 info (7 and 8 open).
"""

from __future__ import annotations

import time
from typing import Any

PANELS = {
    "1": "task", "2": "timing", "3": "results", "4": "log", "5": "feedback",
    "6": "mentor", "9": "info",          # 7 and 8 are left open for what comes next
}


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def _fmt_dur(seconds: float) -> str:
    """Durations in readable units (D418), shared with the timing table."""
    try:
        from flux_profile import human_s

        return f"{human_s(seconds):>6}"
    except Exception:  # noqa: BLE001
        return f"{seconds:5.1f}s"


def _fmt_params(params: dict[str, Any], width: int = 46) -> str:
    if not params:
        return ""
    text = " ".join(f"{k}={v}" for k, v in params.items())
    return text if len(text) <= width else text[: width - 1] + "…"


def _status(t: dict[str, Any]) -> str:
    if t["t1"] is None:
        return "running"
    return "ok" if t["ok"] else "FAIL"


def _task_line(t: dict[str, Any], now: float, width: int, started_at: float | None = None) -> str:
    """One task, one line (D418f, D487): when it started, its duration, its status, the
    name indented by its depth in the tree, the closing note, then `why` after a dash,
    truncated -- the details region carries it in full."""
    dur = (t["t1"] or now) - t["t0"]
    when = _fmt_elapsed(t["t0"] - started_at) if started_at is not None else ""
    indent = "  " * int(t.get("depth", 0) or 0)
    head = f"{when:>7} {_fmt_dur(dur).strip():>6} {_status(t):>7}  {indent}{t['name']}"
    if t["note"]:
        head += f"  [{t['note']}]"
    if t["why"]:
        head += f" — {t['why']}"
    return head[:width]


def _kv_lines(values: dict[str, Any], max_block: int) -> list[str]:
    """Short values as `k = v`, long text (a prompt, a reply, a command line) as an
    indented block."""
    long_values = {k: v for k, v in values.items()
                   if isinstance(v, str) and (len(v) > 60 or "\n" in v)}
    lines = [f"{k} = {v}" for k, v in values.items() if k not in long_values]
    for k, v in long_values.items():
        block = v.splitlines() or [""]
        lines.append(f"{k} ({len(v)} chars, {len(block)} lines):")
        for ln in block[:max_block]:
            lines.append("  │ " + ln)
        if len(block) > max_block:
            lines.append(f"  │ … ({len(block) - max_block} more lines)")
    return lines


def task_details(t: dict[str, Any], now: float, width: int = 96,
                 max_block: int = 4000) -> list[str]:
    """The selected task in full: status, timing, why, then the parameters, then what
    the task PRODUCED (D470: the model's reply, a verdict, a build error) under an
    `output` rule -- short ones as `k = v`, long text as an indented block. The app
    scrolls this region on its own (PgUp/PgDn), so nothing is elided but a
    pathological block."""
    dur = (t["t1"] or now) - t["t0"]
    lines = [f"{_status(t)} · {_fmt_dur(dur).strip()}" + (f" · {t['note']}" if t["note"] else "")]
    if t["why"]:
        lines.append(f"why: {t['why']}")
    if not t["params"]:
        lines.append("(no parameters)")
    lines += _kv_lines(t["params"], max_block)
    output = t.get("output") or {}
    if output:
        lines.append("── output ──" + (" (so far, still running)" if t["t1"] is None else ""))
        lines += _kv_lines(output, max_block)
    elif t["t1"] is None:
        lines.append("── output ── (still running)")
    return [ln[:width] for ln in lines]


def task_rows(snap: dict[str, Any], cursor: int | None = None,
              now: float | None = None, width: int = 96, recent: int = 40,
              list_rows: int = 10, detail_rows: int | None = None,
              detail_scroll: int = 0, detail_hscroll: int = 0, clamp: dict | None = None,
              selected: int | None = None
              ) -> tuple[list[str], list[int | None], list[int], list[str | None]]:
    """The task tab as three FIXED regions (D418f) -- no panel scrolling, one key
    per job, the mode on screen:

        now:      what is running, as a breadcrumb (outermost › innermost)
        recent:   a fixed-height list, one line per task, ↑/↓ move the selection
                  (clamped), click selects; the header says `following` (the
                  selection tracks the running task) or `pinned` (you chose one;
                  Esc returns to following)
        details:  the selected task in full, PgUp/PgDn scroll THIS region only

    Two states, and selection IS the mode (D418h, Cedric's model): BROWSING -- no task
    selected; ↑/↓ move the highlight (`cursor`) through the list and the details pane
    previews the highlighted task; None = follow the running task -- and SELECTED --
    Enter or a click chose `selected`; the arrows scroll and pan the details; Esc
    unselects. Returns (lines, task-id-per-line, order): list rows carry their task
    id; `order` is every id in display order."""
    now = now or time.time()
    tasks = snap["tasks"]
    lines: list[str] = []
    ids: list[int | None] = []
    roles: list[str | None] = []
    def put(text: str, tid: int | None = None, role: str | None = None) -> None:
        lines.append(text[:width])
        ids.append(tid)
        roles.append(role)

    running = [t for t in tasks if t["t1"] is None and t["kind"] != "mark"]
    # The tree in time order (D487, Cedric: the timing tab's history merged in here --
    # indentation, when, dur/ok kept): every task the buffer still holds, parents before
    # their children, an "…" row where a parent's oldest finished children were pruned.
    shown: list[dict[str, Any]] = []
    for t in tasks:
        if t["kind"] == "mark":
            continue
        shown.append(t)
        if int(t.get("pruned", 0) or 0):
            shown.append({"id": -t["id"] - 1, "gap": True, "parent": t["id"], "count": int(t["pruned"]),
                          "depth": int(t.get("depth", 0) or 0) + 1})
    shown.reverse()                       # most recent at the top (Cedric); a gap row then sits
                                          # just above its parent, below the children that remain
    if not shown:
        put("now: (no task reported yet)")
        return lines, ids, [], roles
    order = [t["id"] for t in shown if not t.get("gap")]
    if selected is not None and selected not in order:
        selected = None
    focus_id = selected if selected is not None else cursor
    following = focus_id is None or focus_id not in order
    if following:
        focus_id = running[-1]["id"] if running else order[0]      # the innermost running task

    crumb = " › ".join(t["name"] for t in running) if running else "(between tasks)"
    put(f"now: {crumb}")
    if selected is not None:
        mode, lmark, dmark = "a task is selected · ⏎ back to the list", " ", "▌"
        lkeys, dkeys = "⏎ close · click select", "↑↓ scroll · ←→ pan · ⏎ close"
    else:
        mode = "following the running task" if following else "browsing"
        lmark, dmark = "▌", " "
        lkeys, dkeys = "↑↓ browse · ⏎/click select", "⏎ to scroll"
    # the pane the arrows act on is marked: selection IS the mode (D418h)
    put(f"{lmark}── tasks ({lkeys}) ── {mode} " + "─" * width)
    # the list window: whole rows, the highlight kept inside it
    sel_i = next(i for i, t in enumerate(shown) if t["id"] == focus_id)
    lo = max(0, min(sel_i - list_rows // 2, len(shown) - list_rows))
    hi = min(len(shown), lo + list_rows)
    if lo > 0:
        put(f"{'':>23}↑ {lo} more")
    started_at = snap.get("started_at")
    for t in shown[lo:hi]:
        if t.get("gap"):
            put(f"{'':>7} {'':>6} {'':>7}  {'  ' * t['depth']}… {t['count']} earlier, pruned", None, "dim")
            continue
        put(("▸ " if t["id"] == focus_id else "  ") + _task_line(t, now, width - 2, started_at),
            t["id"], task_role(t, tasks))
    if hi < len(shown):
        put(f"{'':>23}↓ {len(shown) - hi} more")
    for _ in range(list_rows - (hi - lo)):             # keep the details at a fixed row
        put("")
    chosen = next(t for t in shown if t["id"] == focus_id and not t.get("gap"))
    put(f"{dmark}── details: {chosen['name']} ({dkeys}) " + "─" * width)
    raw = task_details(chosen, now, width + detail_hscroll)
    detail_hscroll = _clamp_h(detail_hscroll, raw, width, clamp)
    detail = [ln[detail_hscroll:] if detail_hscroll < len(ln) else "" for ln in raw]
    if detail_hscroll:
        detail = [f"⇠ col {detail_hscroll}"] + detail
    if detail_rows is not None:
        # a pane FOLLOWING a running task shows its end -- the live tail -- as it grows
        window = _window(detail, detail_rows, detail_scroll, clamp,
                         tail=selected is None and following and chosen.get("t1") is None)
        for ln in window:
            put(ln)
    else:
        for ln in detail:
            put(ln)
    return lines, ids, order, roles


#: A scroll offset of TAIL means THE END: the window sticks to the last lines as they are
#: added (D506, Cedric: "live tail doesn't tail properly -- scroll is slower than lines
#: added"). A pane following a running task shows its end; a reader who scrolls past the end
#: stays at the end; ↑ from the end lands on a fixed line again.
TAIL = -1


def _window(detail: list[str], rows: int, scroll: int, clamp: dict | None, *,
            tail: bool = False) -> list[str]:
    """The `rows` lines of `detail` a pane shows for `scroll`, with the above/below markers.
    `clamp` gets `dscroll` (the offset, or TAIL when the window is at the end) and `dmax`
    (the last offset) for the key handler."""
    dmax = max(0, len(detail) - rows)
    at_end = tail or scroll < 0 or scroll >= dmax
    off = dmax if at_end else max(0, scroll)
    if clamp is not None:
        clamp["dscroll"] = TAIL if at_end else off        # D487: no scrolling into the void
        clamp["dmax"] = dmax
    window = detail[off:off + rows]
    if off > 0:
        window[0] = f"↑ {off} more lines above"
    if off + rows < len(detail):
        window[-1] = f"↓ {len(detail) - off - rows} more lines below"
    return window



def task_role(t: dict[str, Any], tasks: list[dict[str, Any]]) -> str | None:
    """A task's role for coloring (D418k): by its name, except that a model call
    takes the role of its caller -- the nearest earlier task one depth level up --
    with the "+model" marker."""
    try:
        from flux_profile import role_of
    except Exception:  # noqa: BLE001
        return None
    own = role_of(t["name"])
    if own != "model":
        return own
    for prev in reversed([x for x in tasks if x["id"] < t["id"] and x["kind"] != "mark"]):
        if prev.get("depth", 0) == t.get("depth", 0) - 1:
            pr = role_of(prev["name"])
            return f"{pr if pr not in (None, 'model') else ''}+model"
    return "+model"


def task_history_rows(snap: dict[str, Any], limit: int = 40
                      ) -> tuple[list[str], list[str | None]]:
    """The compact task history table (timing tab): when, duration, ok/FAIL, task,
    why, params-in-brief; stage headlines threaded through as section lines. Each
    task row carries its role so the app paints it (Cedric: the history too)."""
    tasks = snap["tasks"]
    if not tasks:
        return [], []
    lines = [f"{'when':>8} {'dur':>7} {'ok':>4}  task"]
    roles: list[str | None] = [None]
    shown = 0
    for t in reversed(tasks):
        if t["t1"] is None:
            continue
        if t["kind"] == "mark":
            lines.append(f"{_fmt_elapsed(t['t0'] - snap['started_at']):>8} "
                         f"{'':>7} {'':>4}  ── {t['name']} ──")
            roles.append(None)
            shown += 1
        else:
            ok = "ok" if t["ok"] else "FAIL"
            why = f" — {t['why']}" if t["why"] else ""
            p = _fmt_params({k: v for k, v in t["params"].items()
                             if not (isinstance(v, str) and len(v) > 60)})
            indent = "  " * int(t.get("depth", 0) or 0)
            lines.append(f"{_fmt_elapsed(t['t0'] - snap['started_at']):>8} "
                         f"{_fmt_dur(t['t1'] - t['t0']):>7} {ok:>4}  {indent}{t['name']}{why}"
                         + (f"  [{p}]" if p else "")
                         + (f"  {t['note']}" if t["note"] else ""))
            roles.append(task_role(t, tasks))
            shown += 1
        if shown >= limit:
            break
    return lines, roles


def timing_rows(snap: dict[str, Any], folded: set[tuple[str, ...]] | None
                ) -> tuple[list[str], list[tuple[str, ...] | None], list[bool], list[str | None]]:
    """The timing tab with FOLDING (D418d): (lines, path-per-line, has-children-per-
    line). Tree rows carry their path so the app can move a cursor over them and
    fold/unfold; header, history and derived lines carry None."""
    try:
        from flux_profile import report_rows

        rows = report_rows(total_s=snap.get("elapsed_s"), folded=folded)
    except Exception as exc:  # noqa: BLE001 -- the panel reports, never crashes the UI
        rows = [{"text": f"(flux_profile unavailable: {exc})", "path": None,
                 "has_children": False}]
    if [r["text"] for r in rows] == ["(no timing recorded)"]:
        rows = []
    lines = [r["text"] for r in rows]
    paths: list[tuple[str, ...] | None] = [r["path"] for r in rows]
    kids = [bool(r.get("has_children")) for r in rows]
    roles: list[str | None] = [r.get("role") for r in rows]
    if lines:                       # the color legend lives in the info tab (D418k)
        lines.insert(0, "↑↓ row · space/⏎ fold · - fold all · + unfold all · click")
        paths.insert(0, None)
        kids.insert(0, False)
        roles.insert(0, None)
    # the task history that followed here is the task tab's tree now (D487)
    if not lines:
        return ["(no phases recorded yet)"], [None], [False], [None]
    return lines, paths, kids, roles


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
    history = task_history_rows(snap)[0]
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


def standings_lines(st: dict[str, Any]) -> tuple[list[str], list[str | None]]:
    """The loop's live standings (D418l): where the campaign stands NOW -- proven
    parts, the best refused score per part, what is untried, how many judged."""
    head = (f"standings · {st.get('at', '')} · step {st.get('step', 0)}/"
            f"{st.get('steps', '?')} · {st.get('judged', 0)} judged · ")
    # A search pass stands on what the gate admitted, not on parts proven (D446).
    lines = [head + (f"{st.get('gated', 0)} gated, {st.get('refused', 0)} refused"
                     if st.get("searching")
                     else f"{st.get('proven', 0)}/{st.get('parts_total', '?')} proven")]
    roles: list[str | None] = ["dim"]
    try:
        from flux_profile import BEST_SO_FAR, PROVEN, STANDINGS, TRYING
    except Exception:  # noqa: BLE001
        PROVEN, BEST_SO_FAR, TRYING, STANDINGS = "proven", "best so far", "trying", {}   # type: ignore[assignment]
    # D497 (Cedric: "what is the target / what result we want / what is the problem we work
    # on now"): the objective, what the pass is doing, the whole design's last numbers --
    # then the parts as sub-objectives, each with its constraint and numbers
    obj = st.get("objective") or {}
    for key, label in (("goal", "OBJECTIVE"), ("plan", "PLAN"), ("now", "NOW"), ("composed", "WHOLE")):
        if obj.get(key):
            lines.append(f"  {label:<12} {obj[key]}")
            roles.append("warn" if key == "now" else None)
    if obj:
        lines.append(f"  {'part':<12} {'status':<11} {'constraint and numbers'}")
        roles.append("dim")
    for part in st.get("parts", []):
        state = part.get("state", "")
        nums = f"   {part['numbers']}" if part.get("numbers") else ""
        if state == PROVEN:
            lines.append(f"  {part['part']:<12} {state:<11} {part.get('name', '')}{nums}")
        elif state == TRYING:
            score = (f"prototype at {part['prototype_score']:g} over"
                     if "prototype_score" in part else "no attempt measured yet")
            via = f"   via {part['via']}" if part.get("via") else ""
            lines.append(f"  {part['part']:<12} {state:<11} {score}{via}{nums}")
        elif state == BEST_SO_FAR:
            lines.append(f"  {part['part']:<12} {state}  score {part.get('score', 0):g}"
                         f"   {part.get('name', '')}{nums}")
        else:
            lines.append(f"  {part['part']:<12} {state or 'not yet tried'}{nums}")
        roles.append(STANDINGS.get(state, "dim"))     # the loop's words, colored by one table (D443)
    if st.get("measured"):
        noun = "design(s)" if st.get("searching") else "composed design(s)"
        lines.append(f"  {'measured':<12} {st['measured']} {noun} on the chain")
        roles.append(None)
    designs = st.get("designs") or []
    if designs:                                        # D573: a search pass stands on its designs
        groups = list(dict.fromkeys(d.get("deliverable") or "" for d in designs))
        lines.append(f"  {'design':<26} {'stage':<9} numbers (top 3 of the frontier per deliverable, best first; ◆ the decision)")
        roles.append("dim")
        for g in groups:
            if len(groups) > 1:
                lines.append(f"  {g}:")
                roles.append("dim")
            for d in designs:
                if (d.get("deliverable") or "") != g:
                    continue
                mark = "◆ " if d.get("decision") else "  "
                lines.append(f"  {mark}{d.get('name', '?'):<24} {d.get('stage', ''):<9} {d.get('numbers', '')}")
                roles.append(STANDINGS.get(PROVEN, "dim") if d.get("decision") else None)
    chart = front_lines(st.get("front") or [], st.get("axes") or [])
    lines += chart
    roles += [None] * len(chart)
    return lines, roles


def front_lines(points: list[dict[str, Any]], axes: list[str], width: int = 44, height: int = 7) -> list[str]:
    """The current FRONT as a small chart (D532, review step 13): every whole-design
    measurement on the deepest stage as a point over the first two objectives, the
    decision marked `◆`, the rest `·`; the axes' ranges on the frame. Nothing when there
    are fewer than two points on different coordinates."""
    pts = [p for p in points if isinstance(p, dict) and isinstance(p.get("x"), (int, float)) and isinstance(p.get("y"), (int, float))]
    if len(pts) < 2 or len(axes) < 2:
        return []
    xs, ys = [float(p["x"]) for p in pts], [float(p["y"]) for p in pts]
    if max(xs) == min(xs) and max(ys) == min(ys):
        return []
    x0, x1 = min(xs), max(xs) if max(xs) > min(xs) else min(xs) + 1.0
    y0, y1 = min(ys), max(ys) if max(ys) > min(ys) else min(ys) + 1.0
    grid = [[" "] * width for _ in range(height)]
    for p in pts:
        col = int(round((float(p["x"]) - x0) / (x1 - x0) * (width - 1)))
        row = int(round((float(p["y"]) - y0) / (y1 - y0) * (height - 1)))
        row = height - 1 - row                          # the second objective grows upward
        mark = "◆" if p.get("decision") else "·"
        if grid[row][col] != "◆":
            grid[row][col] = mark
    out = [f"  the front on the {pts[0].get('stage', '?')} stage: {axes[0]} (→) against {axes[1]} (↑); ◆ the decision, {len(pts)} point(s)"]
    for i, row in enumerate(grid):
        label = f"{y1:g}" if i == 0 else (f"{y0:g}" if i == height - 1 else "")
        out.append(f"  {label:>9} │{''.join(row)}│")
    out.append(f"  {'':>9} └{'─' * width}┘")
    out.append(f"  {'':>9}  {x0:<{width // 2}g}{x1:>{width - width // 2}g}")
    return out


def result_sections(st: dict[str, Any]) -> list[dict[str, str]]:
    """Each part of the standings as an openable section (D487, Cedric: "clicking on a result
    should show its artifact"): the prototype it stands on, its RTL, the report that judged
    it, the tests it is judged by."""
    out = []
    for part in st.get("parts", []):
        state = part.get("state", "")
        score = f" score {part.get('score', 0):g}" if "score" in part else (
            f" prototype at {part['prototype_score']:g} over" if state == "trying" and "prototype_score" in part else "")
        title = f"{part.get('part', '?')}: {state or 'not yet tried'}{score}  {part.get('name', '')}"
        chunks = []
        proto = part.get("prototype")
        if proto:
            head = "prototype (verified, 0 over)" if state == "proven" else (
                f"prototype (best refused, {part.get('prototype_score', '?')} over)"
                if "prototype_score" in part else "prototype")
            chunks.append(f"── {head} ──\n{proto}")
        if part.get("report"):
            chunks.append(f"── report ──\n{part['report']}")
        art = part.get("artifact")
        if art:
            chunks.append(f"── RTL ({art.count(chr(10)) + 1} lines) ──\n{art}")
        if part.get("tests"):
            chunks.append(f"── tests ──\n{part['tests']}")
        out.append({"title": title, "text": "\n\n".join(chunks) or "(nothing yet: no attempt has been judged)"})
    for d in st.get("designs", []):                   # D573: a design opens on its numbers, knobs and text
        title = f"{'◆ ' if d.get('decision') else ''}{d.get('name', '?')} on {d.get('stage', '?')}: {d.get('numbers', '')}"
        chunks = []
        if d.get("knobs"):
            chunks.append(f"── knobs ──\n{d['knobs']}")
        art = d.get("artifact")
        if art:
            chunks.append(f"── RTL ({art.count(chr(10)) + 1} lines) ──\n{art}")
        out.append({"title": title, "text": "\n\n".join(chunks) or "(measured; no text to show)"})
    return out


def results_browse(snap: dict[str, Any], cursor: int | None = None, *,
                   selected: int | None = None, width: int = 96, list_rows: int = 8,
                   detail_rows: int | None = None, detail_scroll: int = 0,
                   detail_hscroll: int = 0, clamp: dict | None = None) -> tuple[list[str], list[int | None], list[int], list[str | None]]:
    """The results tab (D487, Cedric: "colors on the results and a table like before made
    more sense"): the coloured standings table exactly as D418l drew it, its part rows
    clickable -- a click or Enter on a part shows its artifacts below the table (prototype,
    report, RTL, tests) where the report and measurements otherwise are; Esc closes.
    Returns (lines, part-index-per-line, order, roles)."""
    st = (snap.get("standings") or {}).get("standings") or {}
    sections = result_sections(st)
    # the run's report and measurements are one more browsable entry (D498: the same
    # navigation as the task tab -- ↑↓ browse, a preview below, ⏎/click pin, ⏎ close)
    plain, pr = results_rows(snap)
    head = standings_lines(st)[0] if st else []
    report_lines = [ln for ln in plain if ln not in head and not (st and ln in standings_lines(st)[0])]
    landed = bool((snap.get("results") or []))
    sections = sections + [{"title": "run report and measurements",
                            "text": "\n".join(report_lines) or "(the report lands here when the run finishes)",
                            "report": True}]
    n_parts = len(sections) - 1
    lines: list[str] = []
    ids: list[int | None] = []
    roles: list[str | None] = []
    order = list(range(len(sections)))
    if selected is not None and selected not in order:
        selected = None
    focus = selected if selected is not None else cursor
    if st:
        tl, tr = standings_lines(st)
        part_no = 0
        for k, (ln, role) in enumerate(zip(tl, tr)):
            if k == 0 or part_no >= n_parts or not ln.strip() or ln.lstrip().split()[0] in ("OBJECTIVE", "NOW", "WHOLE", "part", "measured"):
                lines.append(ln[:width]); ids.append(None); roles.append(role)
                continue
            mark = "▸" if part_no == focus else " "
            lines.append((mark + ln[1:])[:width]); ids.append(part_no); roles.append(role)
            part_no += 1
    rep_no = n_parts
    mark = "▸" if rep_no == focus else " "
    status = "landed" if landed else "not yet -- lands when the run finishes"
    lines.append(f"{mark} {'report':<12} {status}"[:width]); ids.append(rep_no)
    roles.append("ok" if landed else "dim")
    lines.append(""); ids.append(None); roles.append(None)
    if selected is None:
        # BROWSING: a preview of the highlighted entry (the report when nothing is highlighted)
        sec = sections[focus] if focus is not None and focus in order else sections[rep_no]
        lines.append(f"▌── {sec['title']} (↑↓ browse · ⏎/click open) " + "─" * width)
        ids.append(None); roles.append("dim")
        raw = str(sec.get("text", "")).splitlines() or [""]
        if detail_rows is not None and len(raw) > detail_rows:
            raw = raw[:max(1, detail_rows - 1)] + [f"↓ {len(raw) - detail_rows + 1} more lines (⏎ to open and scroll)"]
        for ln in raw:
            lines.append(ln[:width]); ids.append(None); roles.append(None)
        return lines, ids, order, roles
    sec = sections[selected]
    lines.append(f"▌── {sec['title']} (↑↓ scroll · ←→ pan · ⏎ close) " + "─" * width)
    ids.append(None); roles.append("dim")
    raw = str(sec.get("text", "")).splitlines() or [""]
    detail_hscroll = _clamp_h(detail_hscroll, raw, width, clamp)
    body = [ln[detail_hscroll:] if detail_hscroll < len(ln) else "" for ln in raw]
    if detail_hscroll:
        body = [f"⇠ col {detail_hscroll}"] + body
    if detail_rows is not None:
        body = _window(body, detail_rows, detail_scroll, clamp)
    for ln in body:
        lines.append(ln[:width]); ids.append(None); roles.append(None)
    return lines, ids, order, roles


def results_rows(snap: dict[str, Any]) -> tuple[list[str], list[str | None]]:
    """The results tab's plain form (D418l): live standings first, the closing report when
    it has landed, then the measurements table."""
    lines: list[str] = []
    roles: list[str | None] = []
    st = (snap.get("standings") or {}).get("standings")
    if st:
        l2, r2 = standings_lines(st)
        lines += l2 + [""]
        roles += r2 + [None]
    report = _tidy(list(snap["results"]))
    if report:
        lines += report
        roles += [None] * len(report)
    elif not st:
        lines.append("(no results yet -- standings appear after the first step; "
                     "the report lands when the run finishes)")
        roles.append("dim")
    else:
        lines.append("(the report lands here when the run finishes)")
        roles.append("dim")
    meas = snap["measurements"]
    if meas:
        lines += ["", f"─── measurements ({len(meas)} so far, newest last) ───"]
        roles += [None, "dim"]
        keys = list(meas[-1].keys())[:6]
        lines.append("  " + "  ".join(f"{k:>12.12}" for k in keys))
        roles.append("dim")
        for row in meas[-12:]:
            lines.append("  " + "  ".join(_cell(row.get(k)) for k in keys))
            roles.append(None)
    return lines, roles



def _clamp_h(hscroll: int, raw: list[str], width: int, clamp: dict | None) -> int:
    """A horizontal offset no further than the longest line minus a margin (D487, Cedric:
    "scrolling in the void ... the ui is capped but not the scroll counter"); reported back
    through `clamp` so the caller's counter follows."""
    longest = max((len(ln) for ln in raw), default=0)
    h = max(0, min(hscroll, max(0, longest - 1)))        # never past the text itself
    if clamp is not None:
        clamp["dhscroll"] = h
    return h


def _cell(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:>12.3f}"
    return f"{str(v):>12.12}"


def _log_role(text: str) -> str | None:
    """A log line's color: by the role of its first word, like every other tab; the
    TUI's own and captured-stderr lines are dimmed; admissions read as evaluator."""
    t = text.strip()
    if not t:
        return None
    if t.startswith(("[tui]", "[stderr]", "[task]")):
        return "dim"
    try:
        from flux_profile import role_of
    except Exception:  # noqa: BLE001
        return None
    # the loop's log words are role words in flux_profile's one table (D441)
    return role_of(t.split(":", 1)[0].split(" ", 1)[0])


def log_rows(snap: dict[str, Any], filter_text: str = ""
             ) -> tuple[list[str], list[str | None]]:
    """The log tab (D418l): every line stamped with the active-run time it arrived,
    colored by role, and filtered by a substring when one is set -- the header says
    what the filter is and how much it kept."""
    try:
        from flux_profile import human_s
    except Exception:  # noqa: BLE001
        def human_s(x: float) -> str:  # type: ignore[misc]
            return f"{x:.0f}s"
    texts = list(snap["log"])
    stamps = list(snap.get("log_stamps") or [])
    if len(stamps) < len(texts):
        stamps = [0.0] * (len(texts) - len(stamps)) + stamps
    rows = list(zip(texts, stamps))
    if filter_text:
        kept = [(t, st) for t, st in rows if filter_text.lower() in t.lower()]
    else:
        kept = rows
    lines: list[str] = []
    roles: list[str | None] = []
    if filter_text:
        lines.append(f"filter: {filter_text} — {len(kept)} of {len(rows)} lines · esc clears")
        roles.append("dim")
    else:
        lines.append(f"{len(rows)} lines · / filters")
        roles.append("dim")
    if not kept:
        lines.append("(quiet so far)" if not rows else "(nothing matches)")
        roles.append(None)
        return lines, roles
    for text, stamp in kept:
        lines.append(f"{human_s(stamp):>6} │ {text}")
        roles.append(_log_role(text))
    return lines, roles



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


#: The slides' legend, one line per role, in the role's own color (D418k follow-up).
ROLE_LINES = (
    ("io", "input, output -- the boundary: requirements, constraints, traces"),
    ("mentor", "knowledge, records, extract, feedback -- the memory"),
    ("orchestrator", "gate, DSE, propose, frontier, decide -- the policy"),
    ("generator", "template-fill, LLM-gen, repair -- the authors"),
    ("evaluator", "test, analytical, simulation, physical, calibrate -- the judges"),
    ("(model)", "highlighted on a phase of any role that calls the model"),
)


def info_rows(snap: dict[str, Any], info: dict[str, Any], notes: list[Any]
              ) -> tuple[list[str], list[str | None]]:
    """The info tab with a role per line where a line names a role, so the app can
    paint the legend in the legend's colors."""
    lines = info_panel(snap, info, notes)
    roles: list[str | None] = [None] * len(lines)
    in_legend = False                      # only the legend's lines are painted: the
    for i, line in enumerate(lines):       # identity block names a `model` too
        if line.startswith("colors:"):
            in_legend = True
            continue
        if in_legend:
            for role, _what in ROLE_LINES:
                if line.startswith(f"  {role:<14}"):
                    roles[i] = "+model" if role == "(model)" else role
    return lines, roles


def mentor_rows(snap: dict[str, Any], cursor: int | None = None, *,
                selected: int | None = None, width: int = 96, list_rows: int = 8,
                detail_rows: int | None = None, detail_scroll: int = 0,
                detail_hscroll: int = 0, clamp: dict | None = None) -> tuple[list[str], list[int | None], list[int]]:
    """The mentor tab (D418m): the sections the loop published -- knowledge the
    prompts carry, library excerpts, the record's read-back and duels, conclusions,
    refusals, proven and best, operator notes -- browsed like the task tab: ↑/↓ move
    through the section list, Enter (or a click) opens one, then the arrows scroll
    and pan its text, Esc steps back. Returns (lines, section-index-per-line, order)."""
    sections = ((snap.get("standings") or {}).get("mentor") or {}).get("sections") or []
    return _browse(sections, "mentor: what this run knows", cursor, selected=selected,
                   width=width, list_rows=list_rows, detail_rows=detail_rows,
                   detail_scroll=detail_scroll, detail_hscroll=detail_hscroll, clamp=clamp,
                   empty="(the mentor's sections appear once the loop has read its record)")


def _browse(sections: list[dict[str, Any]], heading: str, cursor: int | None = None, *,
            selected: int | None = None, width: int = 96, list_rows: int = 8,
            detail_rows: int | None = None, detail_scroll: int = 0,
            detail_hscroll: int = 0, empty: str = "(nothing)",
            clamp: dict | None = None) -> tuple[list[str], list[int | None], list[int]]:
    """A list of titled sections with one open below it -- the mentor tab's layout, shared
    with the results tab (D487)."""
    lines: list[str] = []
    ids: list[int | None] = []

    def put(text: str, sid: int | None = None) -> None:
        lines.append(text[:width])
        ids.append(sid)

    if not sections:
        put(empty)
        return lines, ids, []
    order = list(range(len(sections)))
    if selected is not None and selected not in order:
        selected = None
    focus = selected if selected is not None else (cursor if cursor in order else 0)
    if selected is not None:
        mode, lmark, dmark = "a section is open · ⏎ closes", " ", "▌"
        dkeys = "↑↓ scroll · ←→ pan · ⏎ close"
    else:
        mode, lmark, dmark = "browsing · ⏎/click opens", "▌", " "
        dkeys = "⏎ to scroll"
    put(f"{lmark}── {heading} ── {mode} " + "─" * width)
    lo = max(0, min(focus - list_rows // 2, len(sections) - list_rows))
    hi = min(len(sections), lo + list_rows)
    if lo > 0:
        put(f"{'':>4}↑ {lo} more")
    for i in range(lo, hi):
        sec = sections[i]
        n = len(sec.get("text", ""))
        size = f"{n / 1000:.1f}k chars" if n >= 1000 else f"{n} chars"
        put(("▸ " if i == focus else "  ") + f"{sec.get('title', '?'):<44} {size:>11}", i)
    if hi < len(sections):
        put(f"{'':>4}↓ {len(sections) - hi} more")
    for _ in range(list_rows - (hi - lo)):
        put("")
    sec = sections[focus]
    put(f"{dmark}── {sec.get('title', '?')} ({dkeys}) " + "─" * width)
    raw = str(sec.get("text", "")).splitlines() or [""]
    detail_hscroll = _clamp_h(detail_hscroll, raw, width, clamp)
    body = [ln[detail_hscroll:] if detail_hscroll < len(ln) else "" for ln in raw]
    if detail_hscroll:
        body = [f"⇠ col {detail_hscroll}"] + body
    if detail_rows is not None:
        for ln in _window(body, detail_rows, detail_scroll, clamp):
            put(ln)
    else:
        for ln in body:
            put(ln)
    return lines, ids, order



def info_panel(snap: dict[str, Any], info: dict[str, Any],
               notes: list[Any]) -> list[str]:
    """The run's identity (D393, trimmed D418j): what is running, on what inputs,
    with which model, how many passes -- the facts that live nowhere else on screen.
    State the bar shows (loop, think, elapsed) is not repeated; the keys are listed
    once, one line each, without explanations -- a reference, not a manual. Keys a
    tab owns (folding, task selection) are stated in that tab's own header."""
    lines = ["this run:"]
    for k, v in info.items():
        if not k.startswith("_"):        # internal keys (run counter) render below
            lines.append(f"  {k:<16} {v}")
    lines.append(f"  {'runs':<16} {info.get('_runs', 1)}")
    if "model" not in info:              # a demo that names its model already said it
        try:
            from flux_llm import default_local_model

            lines.append(f"  {'model':<16} {default_local_model()}")
        except Exception:  # noqa: BLE001
            pass
    if notes:
        lines.append(f"  {'feedback notes':<16} {len(notes)} this session")
    meas = snap.get("measurements") or []
    if meas:
        lines.append(f"  {'measurements':<16} {len(meas)} rows (tab 3)")
    lines += [
        "",
        "keys:",
        f"  {'1-6 · 9 · click':<16} tabs",
        f"  {'↑↓←→ PgUp PgDn':<16} scroll / pan",
        f"  {'f':<16} feedback",
        f"  {'t':<16} think",
        f"  {'r':<16} loop",
        f"  {'q · qq':<16} quit when done · abandon a run",
        f"  {'/':<16} filter the log (esc clears)",
        "",
        "colors: phases and tasks are painted by the role that runs them",
    ]
    lines += [f"  {role:<14} {what}" for role, what in ROLE_LINES]
    return lines


def build(panel: str, snap: dict[str, Any], notes: list[Any],
          feedback_enabled: bool, info: dict[str, Any] | None = None) -> list[str]:
    if panel == "task":
        return task_rows(snap)[0]
    if panel == "timing":
        return timing_panel(snap)
    if panel == "results":
        return results_rows(snap)[0]
    if panel == "log":
        return log_rows(snap)[0]
    if panel == "feedback":
        return feedback_panel(notes, feedback_enabled)
    if panel == "mentor":
        return mentor_rows(snap, None)[0]
    return info_panel(snap, info or {}, notes)
