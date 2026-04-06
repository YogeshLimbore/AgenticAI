"""
storage/database.py — SQLite-backed persistent memory
Replaces the crash-prone flat JSON file with ACID-safe storage.
Features:
  - Applied jobs tracking (no duplicate applications)
  - Blacklist management
  - Feedback / outcome recording
  - Daily plans
  - Analytics queries
"""

from __future__ import annotations

import datetime
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Generator, List, Optional

from utils.logger import get_logger

log = get_logger("storage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS applied_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key     TEXT UNIQUE NOT NULL,         -- title__company (normalized)
    title       TEXT NOT NULL,
    company     TEXT NOT NULL,
    keyword     TEXT DEFAULT '',
    score       INTEGER DEFAULT 0,
    jd_summary  TEXT DEFAULT '',
    applied_at  TEXT NOT NULL,                -- ISO date
    outcome     TEXT DEFAULT 'pending'        -- pending|interview|rejected|no_response|offer
);

CREATE INDEX IF NOT EXISTS idx_applied_key ON applied_jobs(job_key);
CREATE INDEX IF NOT EXISTS idx_applied_date ON applied_jobs(applied_at);

CREATE TABLE IF NOT EXISTS blacklist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company     TEXT UNIQUE NOT NULL,
    reason      TEXT DEFAULT '',
    added_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key     TEXT NOT NULL,
    title       TEXT NOT NULL,
    company     TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    notes       TEXT DEFAULT '',
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date       TEXT UNIQUE NOT NULL,
    focus_keywords  TEXT DEFAULT '[]',       -- JSON array
    skip_keywords   TEXT DEFAULT '[]',       -- JSON array
    min_salary      INTEGER DEFAULT 0,
    notes           TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS stats (
    key     TEXT PRIMARY KEY,
    value   INTEGER DEFAULT 0
);

INSERT OR IGNORE INTO stats(key, value) VALUES
    ('total_applied', 0),
    ('total_interviews', 0),
    ('total_rejections', 0),
    ('total_no_response', 0),
    ('total_offers', 0);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()
        log.info(f"Database ready: {db_path}")

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")   # safe concurrent writes
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Applied jobs ──────────────────────────────────────────────────────

    @staticmethod
    def _job_key(title: str, company: str) -> str:
        return f"{title.strip().lower()}__{company.strip().lower()}"

    def mark_applied(self, title: str, company: str, score: int = 0,
                     keyword: str = "", jd_summary: str = "") -> bool:
        key = self._job_key(title, company)
        today = datetime.date.today().isoformat()
        try:
            with self._conn() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO applied_jobs
                       (job_key, title, company, keyword, score, jd_summary, applied_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (key, title, company, keyword, score, jd_summary, today),
                )
                conn.execute(
                    "UPDATE stats SET value = value + 1 WHERE key = 'total_applied'"
                )
            log.debug(f"Marked applied: {title} @ {company}")
            return True
        except Exception as e:
            log.error(f"mark_applied failed: {e}")
            return False

    def is_already_applied(self, title: str, company: str) -> bool:
        key = self._job_key(title, company)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM applied_jobs WHERE job_key = ?", (key,)
            ).fetchone()
        return row is not None

    def get_applied_jobs(self, days: int = 30) -> List[Dict]:
        cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM applied_jobs WHERE applied_at >= ? ORDER BY applied_at DESC",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_outcome(self, title: str, company: str, outcome: str):
        key = self._job_key(title, company)
        with self._conn() as conn:
            conn.execute(
                "UPDATE applied_jobs SET outcome = ? WHERE job_key = ?",
                (outcome, key),
            )

    # ── Blacklist ─────────────────────────────────────────────────────────

    def blacklist_company(self, company: str, reason: str = ""):
        today = datetime.date.today().isoformat()
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO blacklist(company, reason, added_at) VALUES(?,?,?)",
                    (company.strip(), reason, today),
                )
            log.info(f"Blacklisted: {company} ({reason})")
        except Exception as e:
            log.error(f"blacklist_company failed: {e}")

    def is_blacklisted(self, company: str) -> bool:
        name = company.strip().lower()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM blacklist WHERE LOWER(company) = ?", (name,)
            ).fetchone()
        return row is not None

    def get_blacklist(self) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT company FROM blacklist").fetchall()
        return [r["company"] for r in rows]

    # ── Feedback ──────────────────────────────────────────────────────────

    VALID_OUTCOMES = {"interview", "rejected", "no_response", "offer"}
    OUTCOME_STAT = {
        "interview":   "total_interviews",
        "rejected":    "total_rejections",
        "no_response": "total_no_response",
        "offer":       "total_offers",
    }

    def record_feedback(self, title: str, company: str,
                        outcome: str, notes: str = ""):
        if outcome not in self.VALID_OUTCOMES:
            log.warning(f"Invalid outcome '{outcome}'. Use: {self.VALID_OUTCOMES}")
            return
        key = self._job_key(title, company)
        today = datetime.date.today().isoformat()

        # All three writes share ONE connection/transaction so they either
        # all commit or all roll back — no partial state possible.
        # (Previously update_outcome() opened its own connection, meaning the
        # outcome update could commit while the feedback row or stat failed.)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO feedback(job_key, title, company, outcome, notes, recorded_at)
                   VALUES(?,?,?,?,?,?)""",
                (key, title, company, outcome, notes, today),
            )
            # Inline the outcome update instead of calling self.update_outcome()
            # to keep everything in the same transaction.
            conn.execute(
                "UPDATE applied_jobs SET outcome = ? WHERE job_key = ?",
                (outcome, key),
            )
            stat = self.OUTCOME_STAT.get(outcome)
            if stat:
                # Use a parameterised query — never interpolate into SQL strings.
                conn.execute(
                    "UPDATE stats SET value = value + 1 WHERE key = ?", (stat,)
                )
        log.info(f"Feedback recorded: {title} @ {company} → {outcome}")

    # ── Plans ─────────────────────────────────────────────────────────────

    def set_today_plan(self, focus_keywords: List[str] = None,
                       skip_keywords: List[str] = None,
                       min_salary: int = 0, notes: str = ""):
        import json as _json
        today = datetime.date.today().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO plans
                   (plan_date, focus_keywords, skip_keywords, min_salary, notes)
                   VALUES(?,?,?,?,?)""",
                (today,
                 _json.dumps(focus_keywords or []),
                 _json.dumps(skip_keywords or []),
                 min_salary, notes),
            )
        log.info(f"Plan set for {today}: {notes or focus_keywords}")

    def get_today_plan(self) -> Optional[Dict]:
        import json as _json
        today = datetime.date.today().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM plans WHERE plan_date = ?", (today,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["focus_keywords"] = _json.loads(d["focus_keywords"])
        d["skip_keywords"] = _json.loads(d["skip_keywords"])
        return d

    # ── Stats ─────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM stats").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def get_today_count(self) -> int:
        today = datetime.date.today().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM applied_jobs WHERE applied_at = ?", (today,)
            ).fetchone()
        return row["c"] if row else 0

    # ── Adaptive threshold ────────────────────────────────────────────────

    def compute_optimal_threshold(self, current: int = 60) -> int:
        """
        Analyze feedback outcomes to suggest an optimal threshold.
        - If interview rate < 5%: lower threshold by 5 (apply more)
        - If interview rate > 20%: raise threshold by 5 (be more selective)
        """
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM applied_jobs WHERE outcome != 'pending'"
            ).fetchone()["c"]
            interviews = conn.execute(
                "SELECT COUNT(*) as c FROM applied_jobs WHERE outcome = 'interview'"
            ).fetchone()["c"]

        if total < 20:
            log.debug("Not enough data for threshold optimization (need 20+ outcomes)")
            return current

        rate = interviews / total
        if rate < 0.05:
            new = max(40, current - 5)
            log.info(f"Low interview rate ({rate:.0%}) → lowering threshold {current}→{new}")
        elif rate > 0.20:
            new = min(85, current + 5)
            log.info(f"High interview rate ({rate:.0%}) → raising threshold {current}→{new}")
        else:
            new = current
            log.info(f"Interview rate {rate:.0%} is healthy — keeping threshold at {current}")
        return new
