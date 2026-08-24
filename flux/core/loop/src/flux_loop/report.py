"""THE LOOP-LEVEL REPORT (docs/decisions.md D512): how a campaign moved, read from the record
and the objective vector on it -- never from problem code.

    flux report demo-nlu.db [--campaign 6571cd68] [--out report.html]

One page per campaign, drawn as inline SVG (no script, no network), for any application:

- **Frontier evolution**: the Pareto front over the first two objectives at the end of each
  pass, drawn as a family of fronts by time with the goal as a line, and the HYPERVOLUME each
  front dominates against the worst corner measured -- the campaign's improvement curve, one
  number per pass.
- **Best so far** on every objective over time, for the whole design and for each part.
- **Per part**, small multiples of the first objective over time, annotated with what moved
  it (the ledger: a sweep, an import, a depth pass, a contender, a redesign, a rest).
- The passes, their decisions and the standing, as a table.

A pass is what lies between two `conclusion` rows; the whole design is what the loop marked
`composed` (D511); a part's alone measurement is a row whose candidate names the part
(`subgoal`, or the `op` knob). Times are the rows' own (`created_at`, D512).
"""

from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from .ledger import Kind
from .objective import Objective, Objectives

__all__ = ["Report", "load", "render", "write"]


@dataclass
class Row:
    when: float                      # seconds since the epoch
    stage: str
    name: str
    part: str | None
    whole: bool
    metrics: dict[str, float]
    parts: tuple[str, ...] = ()      # of a whole: which parts it composes


def _parts_of(c: dict[str, Any]) -> tuple[str, ...]:
    """The parts a composed candidate was made of: the loop's mark (D511, a list), else the
    name `composed[a, b, ...]` of rows written before the mark said so."""
    marked = (c.get("meta") or {}).get("composed")
    if isinstance(marked, (list, tuple)):
        return tuple(str(p) for p in marked)
    name = str(c.get("name") or "")
    if name.startswith("composed[") and name.endswith("]"):
        return tuple(p.strip() for p in name[9:-1].split(",") if p.strip())
    return ()


@dataclass
class Report:
    campaign: str
    objective_doc: dict[str, Any]
    objectives: Objectives
    rows: list[Row]
    passes: list[tuple[float, dict[str, Any]]]     # (when, the conclusion)
    ledger: list[tuple[float, str, str, str]]      # (when, kind, part, digest)
    notes: list[str] = field(default_factory=list)

    @property
    def stage(self) -> str | None:
        """The stage the report reads: the goal's, else the deepest measured."""
        g = self.objectives.goal
        if g is not None and g.stage:
            return g.stage
        stages = [r.stage for r in self.rows]
        return stages[-1] if stages else None


def _when(text: str) -> float:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


def load(db: str, campaign: str | None = None, objectives: Objectives | None = None) -> Report:
    """The record's rows, passes and ledger for one campaign (`campaign`: an id prefix; the
    default is the latest). The objective vector: the given one, else the record's latest
    `decided:objectives` row, else the first two numeric metrics measured."""
    from flux_store import CampaignStore

    store = CampaignStore(db)
    try:
        campaigns = store.list_campaigns()
        if not campaigns:
            raise ValueError(f"{db}: no campaign in this record")
        if campaign:
            found = [c for c in campaigns if c["campaign_id"].startswith(campaign)]
            if not found:
                raise ValueError(f"{db}: no campaign starts with {campaign!r}; the record holds "
                                 + ", ".join(c["campaign_id"][:12] for c in campaigns))
            cid = found[-1]["campaign_id"]
        else:
            cid = campaigns[-1]["campaign_id"]
        row = store.campaign_row(cid) or {}
        objective_doc = row.get("objective") if isinstance(row.get("objective"), dict) else {}
        events = store.events(cid)
        notes: list[str] = []
        if objectives is None:
            docs = [e for e in events if e.get("kind") == "decided:objectives"]
            if docs:
                objectives = Objectives.from_doc((docs[-1].get("detail") or {}).get("objectives") or [])
        rows: list[Row] = []
        for t in store.trials(cid, status="ok"):
            if t.result is None or not t.stage or t.stage in ("gate", "admit", "prototype"):
                continue
            metrics = {}
            for k, e in t.result.metrics.items():
                try:
                    v = float(e.value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(v):
                    metrics[k] = v
            if not metrics:
                continue
            c = t.candidate or {}
            meta = c.get("meta") or {}
            part = c.get("subgoal") or c.get("op") or None
            parts = _parts_of(c)
            rows.append(Row(_when(t.created_at), t.stage, str(c.get("name") or "?"), part,
                            bool(meta.get("composed")) or bool(parts), metrics, parts))
        rows.sort(key=lambda r: r.when)
        # THE WHOLE is the composition of every part: an early pass composed two of seven,
        # and its numbers are not the design's
        full = max((len(r.parts) for r in rows if r.whole), default=0)
        for r in rows:
            if r.whole and r.parts and len(r.parts) < full:
                r.whole = False
        if objectives is None:
            seen: list[str] = []
            for r in rows:
                for k in r.metrics:
                    if k not in seen:
                        seen.append(k)
            objectives = Objectives(Objective(k) for k in seen[:2])
            notes.append("no objective vector on the record; the first two metrics measured stand in")
        passes = sorted(((_when(e.get("created_at") or ""), dict(e.get("detail") or {}))
                         for e in events if e.get("kind") == "conclusion"), key=lambda p: p[0])
        ledger = []
        for e in events:
            try:
                kind = Kind(e.get("kind"))
            except ValueError:
                continue
            d = e.get("detail") or {}
            ledger.append((_when(e.get("created_at") or ""), kind.value, str(d.get("op") or ""), str(d.get("digest") or "")))
        ledger.sort(key=lambda x: x[0])
        return Report(cid, objective_doc, objectives, rows, passes, ledger, notes)
    finally:
        store.close()


# ---- the numbers ------------------------------------------------------------------------
def _front(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The Pareto front of (cost1, cost2) pairs, lower is better on both, sorted by cost1."""
    pts = sorted(set(points))
    out: list[tuple[float, float]] = []
    best2 = math.inf
    for x, y in pts:
        if y < best2:
            out.append((x, y))
            best2 = y
    return out


def fronts_by_pass(rep: Report, second: int = 1) -> list[tuple[float, list[tuple[float, float]], float]]:
    """For each pass end (and the record's end): the front over the WHOLE design's rows on the
    report's stage up to then, and its hypervolume, over the first objective and the
    `second` one (D520: both pairs are valid, which leads depends on the experiment). Costs
    are the objectives' signed values (lower is better); the hypervolume is measured in the
    space where higher is better, against the WORST corner measured over the whole record
    (the same reference for every pass, so the curve compares passes) -- the goal is a line
    on the chart, not the reference, or the number would be zero until the goal is met."""
    if len(rep.objectives) <= second:
        return []
    o1, o2 = rep.objectives[0], rep.objectives[second]
    stage = rep.stage
    wholes = [r for r in rep.rows if r.whole and r.stage == stage and o1.value(r.metrics) is not None and o2.value(r.metrics) is not None]
    if not wholes:
        return []
    ends = [p[0] for p in rep.passes] + [wholes[-1].when + 1]
    c1 = [o1.signed(r.metrics) for r in wholes]
    c2 = [o2.signed(r.metrics) for r in wholes]
    # the reference sits one percent of the range beyond the worst corner, so the worst
    # measurement itself dominates a sliver rather than nothing (the volume is strict)
    reference = (-(max(c1) + 0.01 * max(max(c1) - min(c1), 1e-9)), -(max(c2) + 0.01 * max(max(c2) - min(c2), 1e-9)))
    from flux_frontier.pareto_uct import hypervolume

    out = []
    for end in ends:
        pts = [(o1.signed(r.metrics), o2.signed(r.metrics)) for r in wholes if r.when <= end]
        if not pts:
            continue
        front = _front(pts)
        hv = hypervolume([(-x, -y) for x, y in front], reference)
        if not out or out[-1][1] != front:
            out.append((end, front, hv))
        else:
            out[-1] = (end, front, hv)
    return out


def best_so_far(rep: Report, part: str | None, objective: Objective) -> list[tuple[float, float]]:
    """(when, value) each time the running best of `objective` improved, for the whole
    (`part` None) or a part, on the report's stage."""
    stage = rep.stage
    rows = [r for r in rep.rows if r.stage == stage and ((r.whole and part is None) or (not r.whole and r.part == part))]
    out: list[tuple[float, float]] = []
    best: float | None = None
    for r in rows:
        v = objective.value(r.metrics)
        if v is None:
            continue
        if best is None or (v > best if objective.direction == "maximize" else v < best):
            best = v
            out.append((r.when, v))
    return out


# ---- the page ---------------------------------------------------------------------------
_W, _H, _PAD = 640, 260, 44
_COLOURS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]


def _scale(values: list[float], lo_px: float, hi_px: float) -> Any:
    lo, hi = (min(values), max(values)) if values else (0.0, 1.0)
    if hi <= lo:
        hi = lo + 1.0
    return lambda v: lo_px + (v - lo) / (hi - lo) * (hi_px - lo_px)


def _axes(title: str, xlab: str, ylab: str, w: int = _W, h: int = _H) -> str:
    return (f'<text x="{_PAD}" y="18" class="t">{html.escape(title)}</text>'
            f'<line x1="{_PAD}" y1="{h - _PAD}" x2="{w - 12}" y2="{h - _PAD}" class="ax"/>'
            f'<line x1="{_PAD}" y1="{_PAD - 20}" x2="{_PAD}" y2="{h - _PAD}" class="ax"/>'
            f'<text x="{w - 12}" y="{h - _PAD + 16}" class="l" text-anchor="end">{html.escape(xlab)}</text>'
            f'<text x="{_PAD + 4}" y="{_PAD - 24}" class="l">{html.escape(ylab)}</text>')


def _ticks(scale: Any, values: list[float], axis: str, h: int = _H, fmt: str = "{:g}") -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    out = []
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        p = scale(v)
        if axis == "x":
            out.append(f'<text x="{p:.1f}" y="{h - _PAD + 12}" class="k" text-anchor="middle">{fmt.format(v)}</text>')
        else:
            out.append(f'<text x="{_PAD - 4}" y="{p:.1f}" class="k" text-anchor="end">{fmt.format(v)}</text>')
    return "".join(out)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d %H:%M")


def _svg_fronts(rep: Report, second: int = 1) -> str:
    fam = fronts_by_pass(rep, second)
    if not fam:
        return "<p class=note>no whole-design measurements on the report's stage yet</p>"
    o1, o2 = rep.objectives[0], rep.objectives[second]
    xs = [x for _t, f, _h in fam for x, _y in f]
    ys = [y for _t, f, _h in fam for _x, y in f]
    sx, sy = _scale([-v for v in xs], _PAD, _W - 12), _scale([-v for v in ys], _H - _PAD, _PAD)
    body = [_axes(f"frontier evolution: {o1.metric} against {o2.metric} ({rep.stage}), one front per pass",
                  o1.metric, o2.metric)]
    body.append(_ticks(sx, [-v for v in xs], "x") + _ticks(sy, [-v for v in ys], "y"))
    n = len(fam)
    for i, (end, front, hv) in enumerate(fam):
        shade = 0.25 + 0.75 * (i + 1) / n
        pts = " ".join(f"{sx(-x):.1f},{sy(-y):.1f}" for x, y in front)
        body.append(f'<polyline points="{pts}" fill="none" stroke="#1f77b4" stroke-opacity="{shade:.2f}" stroke-width="{1 + shade:.1f}"/>')
        for x, y in front:
            body.append(f'<circle cx="{sx(-x):.1f}" cy="{sy(-y):.1f}" r="2.5" fill="#1f77b4" fill-opacity="{shade:.2f}"><title>{_day(end)}: {o1.metric} {-x:g}, {o2.metric} {y:g}</title></circle>')
    if o1.goal is not None:
        gx = sx(o1.goal)
        body.append(f'<line x1="{gx:.1f}" y1="{_PAD - 20}" x2="{gx:.1f}" y2="{_H - _PAD}" class="goal"/>'
                    f'<text x="{gx + 3:.1f}" y="{_PAD - 8}" class="k">goal {o1.goal:g}</text>')
    svg1 = f'<svg viewBox="0 0 {_W} {_H}" class="chart">{"".join(body)}</svg>'
    # the hypervolume per pass
    ts = [t for t, _f, _h in fam]
    hs = [h for _t, _f, h in fam]
    tx, ty = _scale(ts, _PAD, _W - 12), _scale(hs + [0.0], _H - _PAD, _PAD)
    body = [_axes("hypervolume dominated against the worst corner measured, by pass end", "time", "hypervolume"),
            _ticks(ty, hs + [0.0], "y", fmt="{:.3g}")]
    body.append(f'<text x="{_PAD}" y="{_H - _PAD + 12}" class="k">{_day(ts[0])}</text>'
                f'<text x="{_W - 12}" y="{_H - _PAD + 12}" class="k" text-anchor="end">{_day(ts[-1])}</text>')
    pts = " ".join(f"{tx(t):.1f},{ty(h):.1f}" for t, h in zip(ts, hs))
    body.append(f'<polyline points="{pts}" fill="none" stroke="#d62728" stroke-width="1.5"/>')
    for t, h in zip(ts, hs):
        body.append(f'<circle cx="{tx(t):.1f}" cy="{ty(h):.1f}" r="2.5" fill="#d62728"><title>{_day(t)}: {h:.4g}</title></circle>')
    svg2 = f'<svg viewBox="0 0 {_W} {_H}" class="chart">{"".join(body)}</svg>'
    return svg1 + svg2


def _svg_best(rep: Report, part: str | None, objective: Objective, marks: list[tuple[float, str]] = (),
              w: int = _W, h: int = _H) -> str:
    series = best_so_far(rep, part, objective)
    if not series:
        return ""
    ts = [t for t, _v in series]
    vs = [v for _t, v in series]
    t0, t1 = min(ts + [m[0] for m in marks] if marks else ts), max(ts + [m[0] for m in marks] if marks else ts)
    sx, sy = _scale([t0, t1], _PAD, w - 12), _scale(vs + ([objective.goal] if objective.goal is not None else []), h - _PAD, _PAD)
    who = "the whole" if part is None else part
    body = [_axes(f"{who}: best {objective.metric} so far ({rep.stage})", "time", objective.metric, w, h),
            _ticks(sy, vs + ([objective.goal] if objective.goal is not None else []), "y", h)]
    body.append(f'<text x="{_PAD}" y="{h - _PAD + 12}" class="k">{_day(t0)}</text>'
                f'<text x="{w - 12}" y="{h - _PAD + 12}" class="k" text-anchor="end">{_day(t1)}</text>')
    if objective.goal is not None:
        gy = sy(objective.goal)
        body.append(f'<line x1="{_PAD}" y1="{gy:.1f}" x2="{w - 12}" y2="{gy:.1f}" class="goal"/>')
    # a step line: the best holds until the next improvement
    pts = []
    for i, (t, v) in enumerate(series):
        if i:
            pts.append(f"{sx(t):.1f},{sy(series[i - 1][1]):.1f}")
        pts.append(f"{sx(t):.1f},{sy(v):.1f}")
    pts.append(f"{sx(t1):.1f},{sy(vs[-1]):.1f}")
    body.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="#2ca02c" stroke-width="1.5"/>')
    for t, v in series:
        body.append(f'<circle cx="{sx(t):.1f}" cy="{sy(v):.1f}" r="2.5" fill="#2ca02c"><title>{_day(t)}: {v:g}</title></circle>')
    for t, kind in marks:
        x = sx(t)
        body.append(f'<line x1="{x:.1f}" y1="{h - _PAD}" x2="{x:.1f}" y2="{h - _PAD - 10}" class="mark"><title>{_day(t)}: {html.escape(kind)}</title></line>')
    return f'<svg viewBox="0 0 {w} {h}" class="chart">{"".join(body)}</svg>'


def render(rep: Report) -> str:
    objs = rep.objectives
    o1 = objs[0] if objs else None
    head = [f"<h1>{html.escape(rep.campaign[:12])} -- how the campaign moved</h1>",
            f"<p class=lead>objective: <b>{html.escape(objs.describe() or '(none)')}</b>; "
            f"{len(rep.rows)} measured rows on {len({r.stage for r in rep.rows})} stage(s), {len(rep.passes)} pass(es)"
            + (f"; the whole design: {sum(1 for r in rep.rows if r.whole)} measurements" if any(r.whole for r in rep.rows) else "")
            + ".</p>"]
    if rep.objective_doc:
        head.append(f"<p class=note>the campaign's objective document: <code>{html.escape(json.dumps(rep.objective_doc, sort_keys=True)[:300])}</code></p>")
    for n in rep.notes:
        head.append(f"<p class=note>{html.escape(n)}</p>")
    sections = ["<h2>Frontier evolution</h2>", _svg_fronts(rep)]
    for k in range(2, len(objs)):                       # D520: the other pairs beside the first
        sections.append(f"<h3>{html.escape(objs[0].metric)} against {html.escape(objs[k].metric)}</h3>")
        sections.append(_svg_fronts(rep, k))
    if o1 is not None:
        sections.append("<h2>Best so far</h2>")
        for o in objs:
            svg = _svg_best(rep, None, o)
            if svg:
                sections.append(svg)
        parts = sorted({r.part for r in rep.rows if r.part and not r.whole})
        if parts:
            sections.append(f"<h2>The parts: best {html.escape(o1.metric)} so far, and what moved it</h2>"
                            "<p class=note>ticks along the axis are the ledger's entries for the part: a sweep, an import, a depth pass, a contender, a redesign, a rest (hover)</p>")
            sections.append('<div class="grid">')
            for part in parts:
                marks = [(t, k) for t, k, p, _d in rep.ledger if p == part]
                svg = _svg_best(rep, part, o1, marks, w=420, h=200)
                sections.append(svg or f"<p class=note>{html.escape(part)}: no alone measurement on {html.escape(str(rep.stage))}</p>")
            sections.append("</div>")
    if rep.passes:
        sections.append("<h2>The passes</h2><table><tr><th>ended</th><th>decision</th><th>decided by</th>"
                        + "".join(f"<th>{html.escape(o.metric)}</th>" for o in objs) + "</tr>")
        shown = rep.passes[-40:]
        if len(rep.passes) > 40:
            sections.append(f"<tr><td colspan=99 class=note>{len(rep.passes) - 40} earlier pass(es) not shown</td></tr>")
        for when, con in shown:
            sections.append(f"<tr><td>{_day(when)}</td><td>{html.escape(str(con.get('decision') or ''))[:60]}</td>"
                            f"<td>{html.escape(str(con.get('decided_by') or ''))[:60]}</td>"
                            + "".join(f"<td>{con.get(o.metric, '') if con.get(o.metric) is None or not isinstance(con.get(o.metric), float) else format(con.get(o.metric), '.4g')}</td>" for o in objs)
                            + "</tr>")
        sections.append("</table>")
    style = ("body{font:14px/1.45 system-ui,sans-serif;margin:24px auto;max-width:1320px;padding:0 16px;color:#222;background:#fff}"
             "h1{font-size:20px}h2{font-size:16px;margin-top:28px}.lead{font-size:15px}.note{color:#666;font-size:13px}"
             ".chart{width:640px;max-width:100%;height:auto;display:inline-block;vertical-align:top;margin:4px 8px 4px 0}"
             ".grid .chart{width:420px}.t{font-size:12px;font-weight:600}.l{font-size:11px;fill:#444}.k{font-size:10px;fill:#666}"
             ".ax{stroke:#999;stroke-width:1}.goal{stroke:#d62728;stroke-dasharray:4 3;stroke-width:1}.mark{stroke:#9467bd;stroke-width:2}"
             "table{border-collapse:collapse;font-size:12px}td,th{border-bottom:1px solid #ddd;padding:3px 8px;text-align:left}"
             "@media (prefers-color-scheme: dark){body{color:#ddd;background:#111}.l,.k{fill:#aaa}td,th{border-color:#333}.note{color:#999}}")
    return (f"<!doctype html><html><head><meta charset=utf-8><title>Campaign {html.escape(rep.campaign[:12])} report</title>"
            f"<style>{style}</style></head><body>{''.join(head)}{''.join(sections)}</body></html>")


def write(db: str, out: str, campaign: str | None = None, objectives: Objectives | None = None) -> Report:
    rep = load(db, campaign, objectives)
    with open(out, "w") as f:
        f.write(render(rep))
    return rep


# ---- the closing grammar (D397 phase 4; D558: from the former flux_report package) -------------
# The sections every loop's report ends with: an answer-first banner, WHAT THIS RUN ESTABLISHED,
# NOT ESTABLISHED, REFUSED (N) capped with an honest "... and N more", the operator's notes. Each
# helper returns LINES (the caller prints, so a TUI can capture them the same way); the headers
# are written here once so no loop drifts into a synonym.

def banner(title: str, *, width: int = 79) -> str:
    """The answer-first rule: `══ THE ANSWER ═════...` out to `width` columns."""
    lead = f"══ {title.strip()} "
    return lead + "═" * max(0, width - len(lead))


def established(lessons: Sequence[str]) -> list[str]:
    """WHAT THIS RUN ESTABLISHED, one dash per lesson; [] when there are none."""
    if not lessons:
        return []
    return ["WHAT THIS RUN ESTABLISHED", *[f"  - {line}" for line in lessons]]


def not_established(items: Sequence[str]) -> list[str]:
    """NOT ESTABLISHED: what the run honestly cannot claim; [] when nothing is owed."""
    if not items:
        return []
    return ["NOT ESTABLISHED", *[f"  - {line}" for line in items]]


def refused(items: Sequence, *, cap: int = 6,
            render: Callable[[object], str] = str) -> list[str]:
    """REFUSED (N), each with its reason, capped -- the tail is counted, never
    silently dropped. `render` turns the loop's own refusal type into a line."""
    if not items:
        return []
    lines = [f"REFUSED ({len(items)})"]
    lines += [f"  - {render(item)}" for item in items[:cap]]
    if len(items) > cap:
        lines.append(f"  ... and {len(items) - cap} more")
    return lines


def notes(texts: Sequence[str]) -> list[str]:
    """The operator's guidance during the run (D388/D398): echoed so a typed line is
    visible in the same report its influence would show up in."""
    if not texts:
        return []
    return [f"OPERATOR GUIDANCE ({len(texts)} note(s), advisory; persisted in the "
            "campaign record)", *[f'  * "{t}"' for t in texts]]
