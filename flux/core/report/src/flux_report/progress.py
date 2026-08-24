"""DSE progress as a picture: where the budget went, how fast it converged, what it bought (D373).

Three panels, the standard DSE story, rendered from the record a run already keeps:

  convergence   evaluation count against quality: every explored point faint, the running
                best as a bold step line ending in its number, the run's PHASES as labelled
                bands so "where did the budget go" is visible, the baseline dashed
  hypervolume   the two-objective view of the same trend: how much (quality x cost) volume
                the screened frontier held after each evaluation -- the curve flattening is
                the search converging, drawn only when the run explored both axes
  results       the objective space: cost against quality, the frontier stepped through the
                report's rung, screened-to-confirmed connectors showing what the cheap rung's
                optimism did to each finalist, the decision starred and named

Self-contained SVG, no plotting dependency: opens anywhere, every point carries a native
`<title>` tooltip, colours are the validated reference palette on an explicit light surface.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass
from pathlib import Path

# The validated reference palette (dataviz skill): adjacent categorical pair 1-2, chrome inks.
SCREEN_HUE = "#2a78d6"          # categorical 1 (blue): screened measurements + running best
CONFIRM_HUE = "#eb6834"         # categorical 2 (orange): confirmed rung + frontier
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BAND_A, BAND_B = "#f4f3ef", "#fcfcfb"     # alternating phase bands, well under the marks


@dataclass(frozen=True)
class Point:
    """One measured design, in measurement order."""

    quality: float
    cost: float
    label: str = ""
    confirmed: bool = False
    phase: str = ""


def _ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)
    first = math.ceil(lo / step) * step
    out, v = [], first
    while v <= hi + 1e-12:
        out.append(round(v, 10))
        v += step
    return out


def _fmt(v: float) -> str:
    if abs(v) >= 10000:
        return f"{v / 1000:.0f}k" if v % 1000 == 0 else f"{v:,.0f}"
    if v == int(v):
        return f"{int(v)}"
    return f"{v:.4g}"


def _hv(points: list[tuple[float, float]]) -> float:
    """Normalised 2-D hypervolume of maximise-(quality, -cost) points in [0,1]^2."""
    best_y = 0.0
    total = 0.0
    for x, y in sorted(points, key=lambda p: (-p[0], -p[1])):
        if y > best_y:
            total += x * (y - best_y)
            best_y = y
    return total


def render_progress(points: list[Point], *, out: str | Path, title: str,
                    quality_label: str, cost_label: str, refused: int = 0,
                    decision_label: str | None = None, log_cost: bool = True,
                    baseline_quality: float | None = None,
                    budget_cost: float | None = None,
                    trend_axis: str = "quality",
                    incumbent_label: str | None = None,
                    target_quality: float | None = None,
                    target_label: str = "target") -> Path:
    """Write the DSE-progress SVG for `points` (measurement order) and return the path.

    `trend_axis="cost"` makes the convergence panel track the COST axis per evaluation with a
    running MINIMUM -- the right trend when the study's objective is area- or storage-optimal
    under a constraint (the interconnect demo), where "best so far" means cheapest yet.
    """
    points = [p for p in points if p.quality == p.quality and p.cost == p.cost]
    if not points:
        raise ValueError("nothing to draw: no measured points")
    screened = [p for p in points if not p.confirmed]
    costs = {round(p.cost, 6) for p in points}
    with_hv = len(costs) > 1 and len(screened) > 2

    W, ML, MR = 880, 74, 24
    P1, P2, P3, GAP = 240, (110 if with_hv else 0), 260, 58
    MT, LEG = 46, 34
    H = MT + LEG + P1 + GAP + (P2 + GAP if with_hv else 0) + P3 + 48
    plot_w = W - ML - MR

    qs = [p.quality for p in points] + ([baseline_quality] if baseline_quality else []) \
        + ([target_quality] if target_quality is not None else [])
    qlo, qhi = min(qs), max(qs)
    qpad = (qhi - qlo) * 0.10 or abs(qhi) * 0.02 or 1.0
    qlo, qhi = qlo - qpad, qhi + qpad

    def cxv(cost: float) -> float:
        return math.log10(max(cost, 1e-9)) if log_cost else cost

    xs = [cxv(p.cost) for p in points] + ([cxv(budget_cost)] if budget_cost else [])
    xlo, xhi = min(xs), max(xs)
    xpad = (xhi - xlo) * 0.06 or 0.5
    xlo, xhi = xlo - xpad, xhi + xpad

    def sy(top: float, height: float, v: float, lo: float, hi: float) -> float:
        return top + height - (v - lo) / (hi - lo) * height

    def sx_seq(i: int) -> float:
        return ML + (i + 0.5) / len(points) * plot_w

    def sx_cost(c: float) -> float:
        return ML + (cxv(c) - xlo) / (xhi - xlo) * plot_w

    e = html.escape
    s: list[str] = []
    s.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
             f'font-family="system-ui, sans-serif" font-size="12">')
    s.append(f'<rect width="{W}" height="{H}" fill="{SURFACE}"/>')
    s.append(f'<text x="{ML}" y="24" font-size="15" font-weight="600" fill="{INK}">'
             f'{e(title)}</text>')
    ly = MT + 10
    s.append(f'<circle cx="{ML + 6}" cy="{ly}" r="3" fill="{SCREEN_HUE}" fill-opacity="0.7"/>'
             f'<text x="{ML + 15}" y="{ly + 4}" fill="{INK_2}">screened</text>')
    s.append(f'<circle cx="{ML + 92}" cy="{ly}" r="4.5" fill="{CONFIRM_HUE}" '
             f'stroke="{SURFACE}" stroke-width="2"/>'
             f'<text x="{ML + 102}" y="{ly + 4}" fill="{INK_2}">confirmed</text>')
    s.append(f'<line x1="{ML + 188}" y1="{ly}" x2="{ML + 214}" y2="{ly}" '
             f'stroke="{SCREEN_HUE}" stroke-width="2.5"/>'
             f'<text x="{ML + 220}" y="{ly + 4}" fill="{INK_2}">best so far</text>')
    s.append(f'<text x="{ML + 314}" y="{ly + 4}" fill="{INK}">&#9733;</text>'
             f'<text x="{ML + 328}" y="{ly + 4}" fill="{INK_2}">decision</text>')
    if incumbent_label:
        s.append(f'<rect x="{ML + 400}" y="{ly - 4}" width="8" height="8" fill="none" '
                 f'stroke="{INK_2}" stroke-width="1.5" transform="rotate(45 {ML + 404} {ly})"/>'
                 f'<text x="{ML + 416}" y="{ly + 4}" fill="{INK_2}">incumbent</text>')
    if refused:
        s.append(f'<text x="{W - MR}" y="{ly + 4}" text-anchor="end" fill="{MUTED}">'
                 f'{refused} refused before measurement</text>')

    def frame(top: float, height: float, lo: float, hi: float, ylabel: str,
              xlabel: str) -> None:
        for tick in _ticks(lo, hi):
            y = sy(top, height, tick, lo, hi)
            s.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{W - MR}" y2="{y:.1f}" '
                     f'stroke="{GRID}" stroke-width="1"/>')
            s.append(f'<text x="{ML - 8}" y="{y + 4:.1f}" text-anchor="end" '
                     f'fill="{MUTED}">{_fmt(tick)}</text>')
        s.append(f'<text x="{ML - 58}" y="{top + height / 2:.1f}" fill="{MUTED}" '
                 f'transform="rotate(-90 {ML - 58} {top + height / 2:.1f})" '
                 f'text-anchor="middle">{e(ylabel)}</text>')
        if xlabel:
            s.append(f'<text x="{ML + plot_w / 2:.1f}" y="{top + height + 32:.1f}" '
                     f'text-anchor="middle" fill="{MUTED}">{e(xlabel)}</text>')

    def dot(x: float, y: float, p: Point, faint: bool = False) -> str:
        tip = e(f"#{points.index(p) + 1} {p.phase or 'measured'} -- {p.label or 'design'}: "
                f"{quality_label} {p.quality:.4g}, {cost_label} {_fmt(p.cost)}")
        if p.confirmed:
            return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{CONFIRM_HUE}" '
                    f'stroke="{SURFACE}" stroke-width="2"><title>{tip}</title></circle>')
        r, op = (1.6, 0.45) if faint else (3, 0.7)
        return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{SCREEN_HUE}" '
                f'fill-opacity="{op}"><title>{tip}</title></circle>')

    # ---- panel 1: convergence, with phase bands --------------------------------------------
    minimize = trend_axis == "cost"
    if minimize:
        t_of = lambda p: p.cost                              # noqa: E731
        tlabel = cost_label
        tvals = [p.cost for p in points]
        tlo, thi = min(tvals), max(tvals)
        tpad = (thi - tlo) * 0.10 or abs(thi) * 0.02 or 1.0
        tlo, thi = tlo - tpad, thi + tpad
        t_base = None
    else:
        t_of = lambda p: p.quality                           # noqa: E731
        tlabel, tlo, thi, t_base = quality_label, qlo, qhi, baseline_quality
    top1 = MT + LEG
    bands: list[tuple[int, int, str]] = []
    for i, p in enumerate(points):
        name = p.phase or ""
        if bands and bands[-1][2] == name:
            bands[-1] = (bands[-1][0], i, name)
        else:
            bands.append((i, i, name))
    if len(bands) > 1:
        for j, (a, b, name) in enumerate(bands):
            x0 = ML + a / len(points) * plot_w
            x1 = ML + (b + 1) / len(points) * plot_w
            s.append(f'<rect x="{x0:.1f}" y="{top1}" width="{x1 - x0:.1f}" height="{P1}" '
                     f'fill="{BAND_A if j % 2 == 0 else BAND_B}"/>')
            if name and (x1 - x0) > 7 * len(name):
                s.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{top1 + 13}" '
                         f'text-anchor="middle" font-size="10.5" fill="{MUTED}">'
                         f'{e(name)}</text>')
    frame(top1, P1, tlo, thi, tlabel, "evaluations, in order")
    if target_quality is not None and not minimize:
        y = sy(top1, P1, target_quality, tlo, thi)
        s.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{W - MR}" y2="{y:.1f}" '
                 f'stroke="{CONFIRM_HUE}" stroke-width="1" stroke-dasharray="2 3"/>')
        s.append(f'<text x="{ML + 4}" y="{y - 4:.1f}" font-size="10.5" '
                 f'fill="{CONFIRM_HUE}">{e(target_label)}</text>')
    if t_base is not None:
        y = sy(top1, P1, t_base, tlo, thi)
        s.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{W - MR}" y2="{y:.1f}" '
                 f'stroke="{MUTED}" stroke-width="1" stroke-dasharray="5 4"/>')
        s.append(f'<text x="{W - MR}" y="{y - 4:.1f}" text-anchor="end" font-size="10.5" '
                 f'fill="{MUTED}">baseline</text>')
    faint = len(points) > 60
    best, path = (math.inf if minimize else -math.inf), []
    for i, p in enumerate(points):
        if not p.confirmed and ((t_of(p) < best) if minimize else (t_of(p) > best)):
            best = t_of(p)
            path.append((sx_seq(i), sy(top1, P1, best, tlo, thi)))
    for i, p in enumerate(points):
        s.append(dot(sx_seq(i), sy(top1, P1, t_of(p), tlo, thi), p, faint))
    if path:
        d = f'M{path[0][0]:.1f} {path[0][1]:.1f}'
        for (x, y) in path[1:]:
            d += f' H{x:.1f} V{y:.1f}'
        d += f' H{ML + plot_w}'
        s.append(f'<path d="{d}" fill="none" stroke="{SCREEN_HUE}" stroke-width="2.5"/>')
        s.append(f'<text x="{ML + plot_w - 4:.1f}" y="{path[-1][1] - 6:.1f}" '
                 f'text-anchor="end" font-weight="600" fill="{INK}">'
                 f'best {"(lowest) " if minimize else ""}{_fmt(best)}</text>')
    if decision_label:
        di = next((i for i, p in enumerate(points) if p.label == decision_label), None)
        if di is not None:
            s.append(f'<text x="{sx_seq(di):.1f}" '
                     f'y="{sy(top1, P1, t_of(points[di]), tlo, thi) + 5:.1f}" '
                     f'text-anchor="middle" font-size="13" fill="{INK}">&#9733;'
                     f'<title>{e("decision found here")}</title></text>')

    # ---- panel 2: hypervolume growth -------------------------------------------------------
    top2 = top1 + P1 + GAP
    if with_hv:
        clo = min(cxv(p.cost) for p in screened)
        chi = max(cxv(p.cost) for p in screened) or clo + 1
        span_q = (qhi - qlo) or 1.0
        span_c = (chi - clo) or 1.0
        seen: list[tuple[float, float]] = []
        curve: list[float] = []
        for p in points:
            if not p.confirmed:
                seen.append(((p.quality - qlo) / span_q, 1.0 - (cxv(p.cost) - clo) / span_c))
            curve.append(_hv(seen) if seen else 0.0)
        # 0..1 on purpose: the volume is of the NORMALISED unit square, so 1.0 means "the
        # frontier dominates the whole explored range" and the axis has an absolute reading.
        hlo, hhi = 0.0, 1.0
        frame(top2, P2, hlo, hhi, "hypervolume (0..1)", "")
        d = ""
        for i, v in enumerate(curve):
            x, y = sx_seq(i), sy(top2, P2, v, hlo, hhi)
            d += f'{"M" if not d else "L"}{x:.1f} {y:.1f}'
        s.append(f'<path d="{d}" fill="none" stroke="{SCREEN_HUE}" stroke-width="2"/>')
        s.append(f'<text x="{ML + plot_w - 4:.1f}" y="{sy(top2, P2, curve[-1], hlo, hhi) - 6:.1f}" '
                 f'text-anchor="end" font-weight="600" fill="{INK}">{curve[-1]:.2f}</text>')
        s.append(f'<text x="{ML + 4:.1f}" y="{top2 + 14:.1f}" '
                 f'font-size="10.5" fill="{MUTED}">screened (quality x cost) volume held by '
                 f'the frontier, of the normalised unit square -- flat means converged</text>')

    # ---- panel 3: the objective space ------------------------------------------------------
    top3 = top2 + (P2 + GAP if with_hv else 0)
    frame(top3, P3, qlo, qhi, quality_label,
          cost_label + (" (log scale)" if log_cost else ""))
    if baseline_quality is not None:
        y = sy(top3, P3, baseline_quality, qlo, qhi)
        s.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{W - MR}" y2="{y:.1f}" '
                 f'stroke="{MUTED}" stroke-width="1" stroke-dasharray="5 4"/>')
    if target_quality is not None:
        y = sy(top3, P3, target_quality, qlo, qhi)
        s.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{W - MR}" y2="{y:.1f}" '
                 f'stroke="{CONFIRM_HUE}" stroke-width="1" stroke-dasharray="2 3"/>')
        s.append(f'<text x="{ML + 4}" y="{y - 4:.1f}" font-size="10.5" '
                 f'fill="{CONFIRM_HUE}">{e(target_label)}</text>')
    if incumbent_label:
        inc = next((p for p in points if p.label == incumbent_label), None)
        if inc is not None:
            x, y = sx_cost(inc.cost), sy(top3, P3, inc.quality, qlo, qhi)
            s.append(f'<rect x="{x - 4.5:.1f}" y="{y - 4.5:.1f}" width="9" height="9" '
                     f'fill="none" stroke="{INK_2}" stroke-width="1.5" '
                     f'transform="rotate(45 {x:.1f} {y:.1f})">'
                     f'<title>incumbent: {e(incumbent_label)}</title></rect>')
            s.append(f'<text x="{x + 9:.1f}" y="{y + 14:.1f}" font-size="10.5" '
                     f'fill="{INK_2}">incumbent</text>')
    if budget_cost is not None:
        x = sx_cost(budget_cost)
        s.append(f'<line x1="{x:.1f}" y1="{top3}" x2="{x:.1f}" y2="{top3 + P3}" '
                 f'stroke="{MUTED}" stroke-width="1" stroke-dasharray="5 4"/>')
        s.append(f'<text x="{x - 4:.1f}" y="{top3 + 13}" text-anchor="end" font-size="10.5" '
                 f'fill="{MUTED}">budget</text>')
    # screened -> confirmed connectors: the screen's error, drawn per finalist
    by_label: dict[str, Point] = {}
    for p in screened:
        if p.label:
            by_label[p.label] = p
    for p in points:
        if p.confirmed and p.label in by_label:
            q = by_label[p.label]
            s.append(f'<line x1="{sx_cost(q.cost):.1f}" y1="{sy(top3, P3, q.quality, qlo, qhi):.1f}" '
                     f'x2="{sx_cost(p.cost):.1f}" y2="{sy(top3, P3, p.quality, qlo, qhi):.1f}" '
                     f'stroke="{MUTED}" stroke-width="1" stroke-opacity="0.6"/>')
    pool = [p for p in points if p.confirmed] or screened
    front: list[Point] = []
    for p in sorted(pool, key=lambda p: (p.cost, -p.quality)):
        if not front or p.quality > front[-1].quality:
            front.append(p)
    if len(front) > 1:
        d = f'M{sx_cost(front[0].cost):.1f} {sy(top3, P3, front[0].quality, qlo, qhi):.1f}'
        for p in front[1:]:
            d += f' H{sx_cost(p.cost):.1f} V{sy(top3, P3, p.quality, qlo, qhi):.1f}'
        # the achieved region, lightly shaded: everything below-right of the frontier is HAD
        shade = d + f' V{top3 + P3} H{sx_cost(front[0].cost):.1f} Z'
        s.append(f'<path d="{shade}" fill="{CONFIRM_HUE}" fill-opacity="0.05" stroke="none"/>')
        s.append(f'<path d="{d}" fill="none" stroke="{CONFIRM_HUE}" stroke-width="2" '
                 f'stroke-opacity="0.9"/>')
    for p in points:
        s.append(dot(sx_cost(p.cost), sy(top3, P3, p.quality, qlo, qhi), p, faint))
    if decision_label:
        hit = next((p for p in points if p.label == decision_label and p.confirmed),
                   next((p for p in points if p.label == decision_label), None))
        if hit is not None:
            x, y = sx_cost(hit.cost), sy(top3, P3, hit.quality, qlo, qhi)
            s.append(f'<text x="{x:.1f}" y="{y + 5:.1f}" text-anchor="middle" font-size="17" '
                     f'fill="{INK}">&#9733;<title>{e(decision_label)}</title></text>')
            anchor, tx = ("start", x + 10) if x < ML + plot_w * 0.7 else ("end", x - 10)
            s.append(f'<text x="{tx:.1f}" y="{y - 9:.1f}" text-anchor="{anchor}" '
                     f'font-weight="600" font-size="11" fill="{INK}">decision: '
                     f'{hit.quality:.4g} at {_fmt(hit.cost)}</text>')
    s.append('</svg>')
    out = Path(out)
    out.write_text("\n".join(s) + "\n")
    return out


def points_from_campaign(db: str, campaign_id: str | None = None,
                         *, quality_metric: str = "geomean_speedup",
                         cost_metric: str = "storage_bytes") -> tuple[list[Point], int]:
    """(points in trial order, refused count) from a campaign store -- the record IS the data."""
    from flux_store import CampaignStore

    store = CampaignStore(db)
    try:
        if campaign_id is None:
            rows = store.list_campaigns()
            if not rows:
                return [], 0
            campaign_id = sorted(rows, key=lambda r: r.get("created_at") or "")[-1]["campaign_id"]
        points, refused = [], 0
        for t in store.trials(campaign_id):
            if t.status != "ok" or t.result is None:
                refused += 1
                continue
            q = t.result.metrics.get(quality_metric)
            c = t.result.metrics.get(cost_metric)
            if q is None or c is None:
                continue
            points.append(Point(quality=float(q.value), cost=float(c.value),
                                label=t.candidate_key or "", confirmed=(t.rung == "decide"),
                                phase=t.phase or ""))
        return points, refused
    finally:
        store.close()


__all__ = ["Point", "points_from_campaign", "render_progress"]
