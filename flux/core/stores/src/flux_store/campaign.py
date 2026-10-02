"""`CampaignStore`: durable, resumable campaign state (D217).

Lives in the ResultStore's SQLite file on the same connection, so a trial row can foreign-key
its result row and both land in one transaction. The database is the checkpoint.

Derived, never stored: the budget ledger (sum over trials + top-up events) and the Pareto
frontier (a function of the ok trials), so they can never disagree with the trials.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from flux_evaluator_abi import Result

from .store import ResultStore


@dataclass(frozen=True, slots=True)
class BudgetGrant:
    """What a campaign may spend: evaluations, wall-clock seconds, dollars; None = unbounded.
    The store answers `remaining()` against it."""

    evaluations: int | None = None
    wall_clock_s: float | None = None
    usd: float | None = None

_CAMPAIGN_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id TEXT PRIMARY KEY,
    objective_hash TEXT NOT NULL,
    objective_json TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
    seq INTEGER NOT NULL,
    phase TEXT NOT NULL,
    stage TEXT,
    candidate_json TEXT NOT NULL,
    candidate_key TEXT NOT NULL,
    workload_hash TEXT NOT NULL,
    arch_hash TEXT,
    result_id INTEGER REFERENCES results(id),
    status TEXT NOT NULL,
    error TEXT,
    strategy_kind TEXT NOT NULL,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    wall_clock_s REAL NOT NULL DEFAULT 0.0,
    usd_cost REAL,
    created_at TEXT NOT NULL,
    UNIQUE (campaign_id, seq)
);

CREATE TABLE IF NOT EXISTS campaign_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
    kind TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trials_campaign ON trials(campaign_id, seq);
"""

# Trial statuses. `running` is the intent record: a `running` row found at load time belongs to
# a process that died mid-evaluation and is reclassified `interrupted` (D219).
TRIAL_STATUSES = ("running", "ok", "error", "refused", "constraint_violated", "interrupted")
CAMPAIGN_STATUSES = ("running", "paused", "stopped", "budget_exhausted", "done")


class CampaignStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Trial:
    """One recorded trial. `result` is reconstructed for ok trials (the pareto functions read it
    via `.result`); other statuses carry None."""

    seq: int
    phase: str
    stage: str | None
    candidate: dict[str, Any]
    candidate_key: str
    workload_hash: str
    arch_hash: str | None
    status: str
    result: Result | None
    result_id: int | None
    error: str | None
    cache_hit: bool
    wall_clock_s: float
    created_at: str = ""            # when the row was written, for the loop-level report

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "phase": self.phase,
            "stage": self.stage,
            "candidate": self.candidate,
            "candidate_key": self.candidate_key,
            "status": self.status,
            "result_id": self.result_id,
            "error": self.error,
            "cache_hit": self.cache_hit,
            "wall_clock_s": self.wall_clock_s,
        }


@dataclass(frozen=True, slots=True)
class RemainingBudget:
    """Granted minus derived spend, per dimension; None = that dimension is ungoverned."""

    evaluations: int | None
    wall_clock_s: float | None
    usd: float | None

    @property
    def exhausted(self) -> bool:
        if self.evaluations is not None and self.evaluations <= 0:
            return True
        if self.wall_clock_s is not None and self.wall_clock_s <= 0:
            return True
        if self.usd is not None and self.usd <= 0:
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluations": self.evaluations,
            "wall_clock_s": self.wall_clock_s,
            "usd": self.usd,
            "exhausted": self.exhausted,
        }


class CampaignStore:
    """Composes a `ResultStore` on the same SQLite file/connection. WAL journaling so a reader
    never sees a half-written trial and a killed writer never corrupts the file."""

    def __init__(self, db_path: str) -> None:
        self.results = ResultStore(db_path)
        self._conn: sqlite3.Connection = self.results._conn
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_CAMPAIGN_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self.results.close()

    def __enter__(self) -> "CampaignStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- campaign lifecycle ------------------------------------------------------------------

    def start_campaign(self, objective_doc: dict[str, Any], objective_hash: str,
                       campaign_id: str | None = None) -> tuple[str, bool]:
        """campaign_id == objective_hash (D220) unless the caller names the campaign (a
        document's id). Restarting the same one resumes it. Returns
        (campaign_id, created)."""
        self.results.put_document("objective", objective_doc)
        campaign_id = campaign_id or objective_hash
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO campaigns "
            "(campaign_id, objective_hash, objective_json, status, phase, created_at) "
            "VALUES (?, ?, ?, 'running', 'screen', ?)",
            (campaign_id, objective_hash, json.dumps(objective_doc), _now()),
        )
        created = cursor.rowcount == 1
        if created:
            self._append_event(campaign_id, "started", {})
        self._conn.commit()
        return campaign_id, created

    def list_campaigns(self) -> list[dict[str, Any]]:
        """Every campaign in this store: id, status, phase. Objective documents come from
        `campaign_row` per id."""
        rows = self._conn.execute(
            "SELECT campaign_id, status, phase, created_at FROM campaigns ORDER BY created_at"
        ).fetchall()
        return [
            {"campaign_id": r[0], "status": r[1], "phase": r[2], "created_at": r[3]}
            for r in rows
        ]

    def campaign_row(self, campaign_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT campaign_id, objective_hash, objective_json, status, phase, created_at "
            "FROM campaigns WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if row is None:
            raise CampaignStoreError(f"no campaign {campaign_id!r} in this store")
        return {
            "campaign_id": row[0],
            "objective_hash": row[1],
            "objective": json.loads(row[2]),
            "status": row[3],
            "phase": row[4],
            "created_at": row[5],
        }

    def set_status(self, campaign_id: str, status: str) -> None:
        assert status in CAMPAIGN_STATUSES, status
        self._conn.execute(
            "UPDATE campaigns SET status = ? WHERE campaign_id = ?", (status, campaign_id)
        )
        self._conn.commit()

    def set_phase(self, campaign_id: str, phase: str) -> None:
        self._conn.execute(
            "UPDATE campaigns SET phase = ? WHERE campaign_id = ?", (phase, campaign_id)
        )
        self._conn.commit()

    # -- events ------------------------------------------------------------------------------

    def _append_event(self, campaign_id: str, kind: str, detail: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO campaign_events (campaign_id, kind, detail_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (campaign_id, kind, json.dumps(detail), _now()),
        )

    def append_event(self, campaign_id: str, kind: str, detail: dict[str, Any]) -> None:
        self._append_event(campaign_id, kind, detail)
        self._conn.commit()

    def candidate_count(self, *, phase: str | None = None, status: str | None = None,
                        strategy_kind_like: str | None = None) -> int:
        """Distinct candidate keys over every campaign in this store, filtered."""
        clauses, params = ["1=1"], []
        if phase is not None:
            clauses.append("phase = ?"); params.append(phase)
        if status is not None:
            clauses.append("status = ?"); params.append(status)
        if strategy_kind_like is not None:
            clauses.append("strategy_kind LIKE ?"); params.append(strategy_kind_like)
        row = self._conn.execute(
            f"SELECT COUNT(DISTINCT candidate_key) FROM trials WHERE {' AND '.join(clauses)}",
            params).fetchone()
        return int(row[0]) if row else 0

    def events(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT kind, detail_json, created_at FROM campaign_events "
            "WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        ).fetchall()
        return [{"kind": k, "detail": json.loads(d), "created_at": c} for k, d, c in rows]

    # -- trials ------------------------------------------------------------------------------

    def classify_interrupted(self, campaign_id: str) -> int:
        """Relabel intent rows left by a dead process. Called at load; the relabeling is itself
        an event so the history says it happened."""
        cursor = self._conn.execute(
            "UPDATE trials SET status = 'interrupted' "
            "WHERE campaign_id = ? AND status = 'running'",
            (campaign_id,),
        )
        n = cursor.rowcount
        if n:
            self._append_event(campaign_id, "interrupted_trials_found", {"count": n})
        self._conn.commit()
        return n

    def begin_trial(
        self,
        campaign_id: str,
        *,
        phase: str,
        candidate: dict[str, Any],
        candidate_key: str,
        workload_hash: str,
        arch_hash: str | None,
        strategy_kind: str,
        stage: str | None = None,
    ) -> int:
        """The intent record, committed before evaluation starts so a crash shows as an
        interrupted trial. Returns the trial seq."""
        # D747: the next seq taken in the insert itself -- one statement holds the write lock, so
        # passes at once on their own connections never take the same one (a row was lost)
        row = self._conn.execute(
            "INSERT INTO trials (campaign_id, seq, phase, stage, candidate_json, "
            "candidate_key, workload_hash, arch_hash, status, strategy_kind, created_at) "
            "VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM trials WHERE campaign_id = ?), "
            "?, ?, ?, ?, ?, ?, 'running', ?, ?) RETURNING seq",
            (
                # default=str: a non-JSON object in the candidate is recorded as text rather
                # than failing the whole trial write.
                campaign_id, campaign_id, phase, stage,
                json.dumps(candidate, default=str), candidate_key,
                workload_hash, arch_hash, strategy_kind, _now(),
            ),
        ).fetchone()
        self._conn.commit()
        return int(row[0])

    def complete_trial(
        self,
        campaign_id: str,
        seq: int,
        *,
        status: str,
        result: Result | None,
        error: str | None,
        wall_clock_s: float,
        cache_hit: bool = False,
        existing_result_id: int | None = None,
    ) -> int | None:
        """Result insertion and trial completion in one transaction (D217).
        `existing_result_id` references a row the cache already holds. Returns the result row
        id, if any."""
        assert status in TRIAL_STATUSES and status != "running", status
        trial = self._conn.execute(
            "SELECT workload_hash, arch_hash, status FROM trials "
            "WHERE campaign_id = ? AND seq = ?",
            (campaign_id, seq),
        ).fetchone()
        if trial is None:
            raise CampaignStoreError(f"no trial seq={seq} in campaign {campaign_id!r}")
        if trial[2] != "running":
            raise CampaignStoreError(
                f"trial seq={seq} is {trial[2]!r}, not running — double completion is a bug"
            )
        workload_hash, arch_hash, mapping_hash = trial[0], trial[1], None

        result_id = existing_result_id
        try:
            if result is not None and result_id is None:
                cursor = self._conn.execute(
                    "INSERT INTO results (workload_hash, arch_hash, mapping_hash, evaluator, "
                    "result_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        workload_hash, arch_hash, mapping_hash,
                        result.provenance.evaluator, json.dumps(result.to_dict()), _now(),
                    ),
                )
                result_id = cursor.lastrowid
            usd = result.provenance.usd_cost if result is not None else None
            self._conn.execute(
                "UPDATE trials SET status = ?, result_id = ?, error = ?, wall_clock_s = ?, "
                "usd_cost = ?, cache_hit = ? WHERE campaign_id = ? AND seq = ?",
                (status, result_id, error, wall_clock_s, usd, 1 if cache_hit else 0,
                 campaign_id, seq),
            )
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        return result_id

    def trials(self, campaign_id: str, *, phase: str | None = None,
               status: str | None = None) -> list[Trial]:
        clauses, params = ["campaign_id = ?"], [campaign_id]
        if phase is not None:
            clauses.append("phase = ?")
            params.append(phase)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        # D774: each trial's result in the same query -- not one query per trial (a day's record
        # of 40k trials took 126k queries)
        rows = self._conn.execute(
            "SELECT t.seq, t.phase, t.stage, t.candidate_json, t.candidate_key, t.workload_hash, "
            "t.arch_hash, t.status, t.result_id, t.error, t.cache_hit, t.wall_clock_s, t.created_at, r.result_json "
            f"FROM trials t LEFT JOIN results r ON r.id = t.result_id WHERE {' AND '.join('t.' + c for c in clauses)} "
            "ORDER BY t.seq",
            params,
        ).fetchall()
        out: list[Trial] = []
        for r in rows:
            result = None
            if r[8] is not None and r[13] is not None:
                # Any status with a stored result gets it back, not just "ok": a
                # constraint_violated trial is still a real measurement (D264). `ok_trials`
                # still filters by status for ranking.
                result = Result.from_dict(json.loads(r[13]))
            out.append(Trial(
                seq=r[0], phase=r[1], stage=r[2],
                candidate=json.loads(r[3]), candidate_key=r[4], workload_hash=r[5],
                arch_hash=r[6], status=r[7], result=result, result_id=r[8], error=r[9],
                cache_hit=bool(r[10]), wall_clock_s=r[11],
                created_at=r[12] or "",
            ))
        return out

    def ok_trials(self, campaign_id: str, *, phase: str = "screen") -> list[Trial]:
        return self.trials(campaign_id, phase=phase, status="ok")

    def visited_keys(self, campaign_id: str) -> set[str]:
        """Candidate keys of every non-interrupted trial; an interrupted candidate was never
        measured and may be proposed again."""
        rows = self._conn.execute(
            "SELECT candidate_key FROM trials "
            "WHERE campaign_id = ? AND status != 'interrupted'",
            (campaign_id,),
        ).fetchall()
        return {r[0] for r in rows}

    def visited_keys_all(self) -> set[str]:
        """Candidate keys across every campaign in this store.

        For a generative strategy, which pays a model call per proposal, proposing something a
        sibling campaign already measured is waste (D300); per-campaign keys suit a grid.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT candidate_key FROM trials WHERE status != 'interrupted'"
        ).fetchall()
        return {r[0] for r in rows}

    # -- derived ledger ----------------------------------------------------------------------

    def spent(self, campaign_id: str) -> dict[str, Any]:
        evals, wall, usd = self._conn.execute(
            "SELECT "
            "  SUM(CASE WHEN phase = 'screen' AND cache_hit = 0 "
            "           AND status IN ('ok', 'error', 'refused', 'constraint_violated') "
            "      THEN 1 ELSE 0 END), "
            "  COALESCE(SUM(wall_clock_s), 0.0), "
            "  SUM(usd_cost) "
            "FROM trials WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        # usd stays None (unknown), not 0.0, when no backend reported a cost.
        return {"evaluations": int(evals or 0), "wall_clock_s": float(wall), "usd": usd}

    def remaining(self, campaign_id: str, budget: BudgetGrant) -> RemainingBudget:
        spent = self.spent(campaign_id)
        top_ups = {"evaluations": 0, "wall_clock_s": 0.0, "usd": 0.0}
        for event in self.events(campaign_id):
            if event["kind"] == "topped_up":
                for k, v in event["detail"].get("added", {}).items():
                    if k in top_ups and v:
                        top_ups[k] += v

        def _rem(granted, spent_v, top_up):
            if granted is None:
                return None
            return granted + top_up - (spent_v or 0)

        return RemainingBudget(
            evaluations=_rem(budget.evaluations, spent["evaluations"], top_ups["evaluations"]),
            wall_clock_s=_rem(budget.wall_clock_s, spent["wall_clock_s"], top_ups["wall_clock_s"]),
            usd=_rem(budget.usd, spent["usd"], top_ups["usd"]),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
