"""`CampaignStore` — durable, resumable campaign state (docs/decisions.md D217).

Lives in the ResultStore's own SQLite file, on the same connection, so a trial row can
foreign-key the result row it produced and the two land in ONE transaction — the whole
interruption-safety story. The database is the checkpoint; there is no separate checkpoint file
to drift from it.

Derived, never stored: the budget ledger (SUM over trials + top-up events) and the Pareto
frontier (a pure function of the ok trials). An interrupted process can therefore never leave
the ledger and the trials disagreeing — there is nothing to disagree.
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
    """What a campaign may spend (D334; the old campaign package's, kept here since D521 --
    the store answers `remaining()` against it): evaluations, wall-clock seconds, dollars;
    None = unbounded."""

    evaluations: int | None = None
    wall_clock_s: float | None = None
    usd: float | None = None

#: Schema v2 (D524): the accelerator era's nine trial columns, never written by the one loop
#: (mapping_hash, seed, deterministic, llm_model, prompt_sha256, response_sha256, used_fallback,
#: fallback_reason, stage_index), are dropped from every record on open; `PRAGMA user_version`
#: says which schema a file has, and each campaign gets a `migrated` event saying what went.
SCHEMA_VERSION = 2
DROPPED_COLUMNS = ("mapping_hash", "seed", "deterministic", "llm_model", "prompt_sha256", "response_sha256",
                   "used_fallback", "fallback_reason", "stage_index")


_V2_COLUMNS = ("id", "campaign_id", "seq", "phase", "stage", "candidate_json", "candidate_key", "workload_hash",
               "arch_hash", "result_id", "status", "error", "strategy_kind", "cache_hit", "wall_clock_s", "usd_cost",
               "created_at")


def _upgrade_schema(conn: sqlite3.Connection) -> list[str]:
    """A record written before D524 loses the nine columns and is marked schema 2; what was
    dropped is returned and put on each campaign as an event. ONE rewrite of the table (a
    v2 table filled from the old one, then swapped in), not one per column: SQLite's DROP
    COLUMN rewrites the whole table each time, and on `demo-nlu.db` (800 MB of candidate
    text) one column took twenty minutes on the home filesystem. Idempotent: a v2 file has
    nothing to drop."""
    try:
        have = [row[1] for row in conn.execute("PRAGMA table_info(trials)")]
    except sqlite3.Error:
        return []
    dropped = [c for c in DROPPED_COLUMNS if c in have]
    version = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
    if dropped:
        keep = [c for c in _V2_COLUMNS if c in have]
        cols = ", ".join(keep)
        body = _CAMPAIGN_SCHEMA[_CAMPAIGN_SCHEMA.index("CREATE TABLE IF NOT EXISTS trials ("):]
        body = body[:body.index(");") + 2].replace("CREATE TABLE IF NOT EXISTS trials (", "CREATE TABLE trials_v2 (")
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN")
            conn.execute(body)
            conn.execute(f"INSERT INTO trials_v2 ({cols}) SELECT {cols} FROM trials")
            conn.execute("DROP TABLE trials")
            conn.execute("ALTER TABLE trials_v2 RENAME TO trials")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trials_campaign ON trials(campaign_id, seq)")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
    if version < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        if dropped:
            for (cid,) in conn.execute("SELECT campaign_id FROM campaigns").fetchall():
                conn.execute(
                    "INSERT INTO campaign_events (campaign_id, kind, detail_json, created_at) VALUES (?, 'migrated', ?, ?)",
                    (cid, json.dumps({"schema": SCHEMA_VERSION, "dropped": dropped}), _now()))
    return dropped


def _rename_old_columns(conn: sqlite3.Connection) -> None:
    """A campaign file written before "rung" became "stage" (D466) keeps its data and
    gains the new column names on open. SQLite renames a column in place, so nothing is
    copied and nothing is lost; `CREATE TABLE IF NOT EXISTS` would otherwise leave the old
    file with the old columns and every query would miss."""
    try:
        have = {row[1] for row in conn.execute("PRAGMA table_info(trials)")}
    except sqlite3.Error:            # no trials table yet: the schema above just made it
        return
    for was, now in (("rung", "stage"), ("rung_index", "stage_index")):
        if was in have and now not in have:
            try:
                conn.execute(f"ALTER TABLE trials RENAME COLUMN {was} TO {now}")
            except sqlite3.Error:    # an older SQLite, or someone renamed it already
                pass


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
# a process that died mid-evaluation and is reclassified `interrupted` (docs/decisions.md D219).
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
    created_at: str = ""            # D512: when the row was written, for the loop-level report

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
        _rename_old_columns(self._conn)
        #: what opening this file dropped (D524); empty for a record already at schema 2
        self.upgraded: list[str] = _upgrade_schema(self._conn)
        self._conn.commit()

    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0] or 0)

    def close(self) -> None:
        self.results.close()

    def __enter__(self) -> "CampaignStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- campaign lifecycle ------------------------------------------------------------------

    def start_campaign(self, objective_doc: dict[str, Any], objective_hash: str,
                       campaign_id: str | None = None) -> tuple[str, bool]:
        """campaign_id == objective_hash (docs/decisions.md D220) unless the caller names the
        campaign (D524: a problem document's `campaign: {name: nlu}` -- keyed by the problem,
        readable, one campaign per document): restarting the same one resumes it rather than
        forking a sibling. Returns (campaign_id, created)."""
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

    def rename_campaign(self, old: str, new: str) -> None:
        """A campaign under a new id -- every trial and event follows, and a `renamed` event
        says where it came from (D524: `flux migrate --rename 6571cd68e823=nlu` puts the
        campaign the demo opened under the document's name)."""
        if not new or new == old:
            raise CampaignStoreError(f"rename {old!r}: the new id must be a different, non-empty string")
        if self._conn.execute("SELECT 1 FROM campaigns WHERE campaign_id = ?", (new,)).fetchone():
            raise CampaignStoreError(f"rename {old!r}: a campaign {new!r} already exists")
        if not self._conn.execute("SELECT 1 FROM campaigns WHERE campaign_id = ?", (old,)).fetchone():
            raise CampaignStoreError(f"rename {old!r}: no such campaign")
        try:
            self._conn.execute("PRAGMA foreign_keys = OFF")
            for table in ("trials", "campaign_events", "campaigns"):
                self._conn.execute(f"UPDATE {table} SET campaign_id = ? WHERE campaign_id = ?", (new, old))
            self._append_event(new, "renamed", {"from": old, "to": new})
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")

    def list_campaigns(self) -> list[dict[str, Any]]:
        """Every campaign in this store: id, status, phase — the enumeration knowledge mining
        needs (docs/decisions.md D243). Objective documents come from `campaign_row` per id."""
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
        """Distinct candidate keys over every campaign in this store, filtered (D442): what a
        tally asks without writing SQL against the schema."""
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

    def metric_maxima(self) -> dict[str, float]:
        """The largest value this store holds for each metric, over every stored result (D440):
        what a conclusion may claim as a maximum is bounded by this."""
        out: dict[str, float] = {}
        for (raw,) in self._conn.execute("SELECT result_json FROM results").fetchall():
            try:
                metrics = json.loads(raw).get("metrics", {})
            except ValueError:
                continue
            for name, estimate in metrics.items():
                value = (estimate or {}).get("value")
                if isinstance(value, (int, float)):
                    out[name] = max(out.get(name, float("-inf")), float(value))
        return out

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
        """The intent record, committed BEFORE evaluation starts — a crash after this point is
        detectable as an interrupted trial instead of silence. Returns the trial seq."""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM trials WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        seq = int(row[0])
        self._conn.execute(
            "INSERT INTO trials (campaign_id, seq, phase, stage, candidate_json, "
            "candidate_key, workload_hash, arch_hash, status, strategy_kind, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)",
            (
                # default=str: a candidate document carrying an object the caller kept for
                # itself must not make the whole trial vanish (the write is wrapped in a
                # try by every caller), so it is recorded as its text instead.
                campaign_id, seq, phase, stage,
                json.dumps(candidate, default=str), candidate_key,
                workload_hash, arch_hash, strategy_kind, _now(),
            ),
        )
        self._conn.commit()
        return seq

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
        """Result insertion and trial completion in ONE transaction — the atomicity D217 exists
        for. `existing_result_id` references a row the cache already holds (a hit stores nothing
        twice). Returns the result row id, if any."""
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
        rows = self._conn.execute(
            "SELECT seq, phase, stage, candidate_json, candidate_key, workload_hash, "
            "arch_hash, status, result_id, error, cache_hit, wall_clock_s, created_at "
            f"FROM trials WHERE {' AND '.join(clauses)} ORDER BY seq",
            params,
        ).fetchall()
        out: list[Trial] = []
        for r in rows:
            result = None
            if r[8] is not None:
                # Any status with a stored result gets it back, not just "ok"
                # (docs/decisions.md D264): a constraint_violated trial is a MEASUREMENT that
                # was paid for — the fabric really was placed and really ran at 483 MHz — and
                # withholding it here hid that number from every reader, including knowledge
                # mining. `ok_trials` still filters by status, so nothing that ranks or
                # escalates changes.
                stored = self.results.get_result(r[8])
                if stored is not None:
                    result = Result.from_dict(stored["result"])
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
        """Candidate keys of every non-interrupted trial — an interrupted candidate was never
        measured, so the strategy must be allowed to propose it again."""
        rows = self._conn.execute(
            "SELECT candidate_key FROM trials "
            "WHERE campaign_id = ? AND status != 'interrupted'",
            (campaign_id,),
        ).fetchall()
        return {r[0] for r in rows}

    def visited_keys_all(self) -> set[str]:
        """Candidate keys across EVERY campaign in this store.

        Per-campaign visitedness is right for a grid strategy: each campaign has its own space
        and re-screening a cached candidate is nearly free. It is wrong for a GENERATIVE one,
        which is spending a model call per proposal — proposing something a sibling campaign
        already measured costs that call and adds nothing, and the strategy cannot know it
        happened because its own campaign has never seen the candidate (docs/decisions.md D300).
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
        # usd stays None (unknown), never 0.0, when no backend ever reported a cost — an honest
        # "unknown" is different from a measured zero.
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
