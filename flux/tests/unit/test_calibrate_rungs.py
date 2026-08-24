"""What a cheap stage got wrong, where a costly one settled it (docs/decisions.md D464).

The drawing's `calibrate (CI)` box: the slow evaluator corrects the fast one. The interconnect
study computed this by hand (D305) and the campaign path calibrates screening its own way
(D222); this is the loop's own, for any problem whose chain has two stages that measured the same
design.

What these pin: the ratio is computed from the pass's own measurements per metric with its spread
and count, one shared design is not enough to claim a bias, the finding reaches the report, the
record and the problem, a correction never rewrites a measurement, and calibration that fails is
a lost finding rather than a failed run.
"""

from __future__ import annotations

from flux_loop import Bias, Candidate, LoopRequest, Problem, Scored, Verdict, bias, run_loop


class Two(Problem):
    """An estimate that reads low, and a placement that settles it."""

    name = "two"

    def __init__(self, widths=(1, 2, 3, 4), *, factor: float = 1.2) -> None:
        self.widths = list(widths)
        self.factor = factor
        self.seen: list[list[Bias]] = []

    def objective(self, request):
        return {"study": "two"}

    def search(self, state):
        yield [Candidate(name=f"w{w}", artifact="", knobs={"width": w}) for w in self.widths]

    def build(self, cand, subgoal, state):
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return ["estimate", "placement"]

    def analytic_stages(self):
        return frozenset({"estimate"})

    def finalists(self, front, state, stage=""):
        return list(front)

    def frontier(self, scored, state):
        return list(scored)

    def measure(self, cand, stage, state):
        fmax = 100.0 * float(cand.knobs["width"])
        return {"fmax_mhz": fmax if stage == "estimate" else fmax * self.factor}

    def calibrated(self, biases, state):
        self.seen.append(list(biases))


def _request(tmp_path, **kw):
    kw.setdefault("steps", 1)
    return LoopRequest(db=str(tmp_path / "c.db"), prototype=False, compute=False,
                       critique_rounds=0, **kw)


def test_the_costly_stage_says_what_the_cheap_one_reads(tmp_path):
    problem = Two()
    out = run_loop(problem, _request(tmp_path), log=lambda _m: None)
    assert problem.seen, "the problem was handed the comparison"
    (b,) = problem.seen[0]
    assert b.metric == "fmax_mhz" and b.stage == "estimate" and b.against == "placement"
    assert round(b.ratio, 6) == 1.2 and b.n == 4 and b.spread < 1e-9
    assert any("measures fmax_mhz 1.2x what the estimate stage predicts" in l
               for l in out.lessons), out.lessons


def test_one_shared_design_is_not_a_bias():
    """A single pair cannot tell a systematic bias from a lucky design."""
    def scored(name, stage, value):
        return Scored(Candidate(name=name, artifact="", knobs={"w": 1}), stage,
                      {"fmax_mhz": value}, {})

    one = [scored("a", "estimate", 100.0), scored("a", "placement", 120.0)]
    assert bias(one, fast="estimate", against="placement") == []
    two = one + [Scored(Candidate(name="b", artifact="", knobs={"w": 2}), "estimate",
                        {"fmax_mhz": 200.0}, {}),
                 Scored(Candidate(name="b", artifact="", knobs={"w": 2}), "placement",
                        {"fmax_mhz": 260.0}, {})]
    (got,) = bias(two, fast="estimate", against="placement")
    assert round(got.ratio, 3) == 1.25 and got.n == 2 and round(got.spread, 3) == 0.071


def test_a_correction_is_an_estimate_and_never_rewrites_a_measurement(tmp_path):
    problem = Two()
    out = run_loop(problem, _request(tmp_path), log=lambda _m: None)
    estimates = {s.candidate.name: s.metrics["fmax_mhz"] for s in out.scored
                 if s.stage == "estimate"}
    placed = {s.candidate.name: s.metrics["fmax_mhz"] for s in out.scored
              if s.stage == "placement"}
    assert estimates["w1"] == 100.0, "the estimate is still what the model said"
    assert placed["w1"] == 120.0, "and the placement is still what the tool said"
    (b,) = problem.seen[0]
    assert b.apply(estimates["w1"]) == 120.0, "correcting is something a caller does"


def test_the_record_keeps_what_was_calibrated(tmp_path):
    from flux_records import Records

    db = str(tmp_path / "c.db")
    run_loop(Two(), _request(tmp_path), log=lambda _m: None)
    rec = Records(db, objective={"study": "two"}, log=lambda _m: None)
    kept = rec.recall("calibration")
    assert kept and kept[-1]["metric"] == "fmax_mhz" and kept[-1]["n"] == 4
    assert round(kept[-1]["ratio"], 6) == 1.2


def test_a_problem_that_cannot_use_the_calibration_does_not_fail_the_run(tmp_path):
    said: list[str] = []

    class Broken(Two):
        def calibrated(self, biases, state):
            raise RuntimeError("the correction is broken")

    out = run_loop(Broken(), _request(tmp_path), log=said.append)
    assert any("could not use the calibration" in m for m in said), said
    assert out.decision is not None and out.decision.stage == "placement"


def test_nothing_to_compare_says_nothing(tmp_path):
    class Disjoint(Two):
        def finalists(self, front, state, stage=""):
            return []          # nothing climbs, so no design has two numbers

    out = run_loop(Disjoint(), _request(tmp_path), log=lambda _m: None)
    assert not any("predicts" in l for l in out.lessons), out.lessons
