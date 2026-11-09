"""SQLite persistence: run records plus the diversity history the engine reads.

One file, no ORM. Connections are created per call and closed, which keeps the
module safe to use from both the FastAPI event loop (via run_in_executor or
direct short calls) and Celery worker processes.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

_INIT_LOCK = threading.Lock()
_INITIALISED: set[str] = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    idempotency_key TEXT,
    topic           TEXT NOT NULL,
    status          TEXT NOT NULL,
    style           TEXT,
    angle           TEXT,
    audience        TEXT,
    art_direction   TEXT,
    palette         TEXT,
    composition     TEXT,
    title           TEXT,
    word_count      INTEGER,
    revisions       INTEGER DEFAULT 0,
    image_provider  TEXT,
    qc_passed       INTEGER,
    duration_ms     INTEGER,
    cost_usd        REAL,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    created_at      TEXT NOT NULL,
    payload         TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_idem    ON runs(idempotency_key);

CREATE TABLE IF NOT EXISTS diversity_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    topic_hash    TEXT,
    style         TEXT,
    angle         TEXT,
    audience      TEXT,
    art_direction TEXT,
    palette       TEXT,
    composition   TEXT
);
CREATE INDEX IF NOT EXISTS idx_div_id ON diversity_history(id DESC);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    key = str(db_path)
    if key not in _INITIALISED:
        with _INIT_LOCK:
            if key not in _INITIALISED:
                conn.executescript(SCHEMA)
                conn.commit()
                _INITIALISED.add(key)
    return conn


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    # --- diversity history ---

    def recent_selections(self, limit: int) -> list[dict[str, Any]]:
        """Newest first. Index 0 is the most recent run."""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT style, angle, audience, art_direction, palette, composition,"
                " topic_hash FROM diversity_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_selection(self, run_id: str, created_at: str, topic_hash: str,
                         selection: dict[str, str]) -> None:
        with _connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO diversity_history (run_id, created_at, topic_hash, style,"
                " angle, audience, art_direction, palette, composition)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, created_at, topic_hash, selection.get("style"),
                 selection.get("angle"), selection.get("audience"),
                 selection.get("art_direction"), selection.get("palette"),
                 selection.get("composition")),
            )
            conn.commit()

    # --- runs ---

    def upsert_run(self, record: dict[str, Any]) -> None:
        payload = dict(record)
        payload_json = json.dumps(payload.pop("payload", {}), default=str)
        cols = list(payload) + ["payload"]
        vals = [payload[c] for c in payload] + [payload_json]
        placeholders = ",".join("?" * len(cols))
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "run_id")
        with _connect(self.db_path) as conn:
            conn.execute(
                f"INSERT INTO runs ({','.join(cols)}) VALUES ({placeholders})"
                f" ON CONFLICT(run_id) DO UPDATE SET {updates}",
                vals,
            )
            conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        rec = dict(row)
        rec["payload"] = json.loads(rec.get("payload") or "{}")
        return rec

    def find_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE idempotency_key=? AND status='completed'"
                " ORDER BY created_at DESC LIMIT 1",
                (key,),
            ).fetchone()
        if row is None:
            return None
        rec = dict(row)
        rec["payload"] = json.loads(rec.get("payload") or "{}")
        return rec

    def stats(self) -> dict[str, Any]:
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) total,"
                " SUM(status='completed') completed,"
                " SUM(status='partial')   partial,"
                " SUM(status='failed')    failed,"
                " AVG(duration_ms)        avg_duration_ms,"
                " AVG(word_count)         avg_word_count,"
                " SUM(cost_usd)           total_cost_usd,"
                " AVG(revisions)          avg_revisions"
                " FROM runs"
            ).fetchone()
            styles = conn.execute(
                "SELECT style, COUNT(*) n FROM runs WHERE style IS NOT NULL"
                " GROUP BY style ORDER BY n DESC"
            ).fetchall()
            arts = conn.execute(
                "SELECT art_direction, COUNT(*) n FROM runs WHERE art_direction IS NOT NULL"
                " GROUP BY art_direction ORDER BY n DESC"
            ).fetchall()
            providers = conn.execute(
                "SELECT image_provider, COUNT(*) n FROM runs WHERE image_provider IS NOT NULL"
                " GROUP BY image_provider ORDER BY n DESC"
            ).fetchall()
        out = {k: row[k] for k in row.keys()}
        out["style_distribution"] = {r["style"]: r["n"] for r in styles}
        out["art_direction_distribution"] = {r["art_direction"]: r["n"] for r in arts}
        out["image_provider_distribution"] = {r["image_provider"]: r["n"] for r in providers}
        return out
