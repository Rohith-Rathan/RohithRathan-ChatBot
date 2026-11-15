"""SQLite record of processed Drive files and posted submissions (duplicate prevention)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Statuses that mean "never look at this file again".
FINAL_STATUSES = {"posted", "not_mine", "empty", "duplicate"}
MAX_ATTEMPTS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_files (
    file_id        TEXT PRIMARY KEY,
    file_name      TEXT NOT NULL,
    modified_time  TEXT,
    status         TEXT NOT NULL,
    dedupe_key     TEXT,
    entry_id       TEXT,
    analysis_json  TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posted_submissions (
    dedupe_key  TEXT PRIMARY KEY,
    file_id     TEXT NOT NULL,
    entry_id    TEXT,
    posted_at   TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def should_process(self, file_id: str) -> bool:
        row = self.conn.execute(
            "SELECT status, attempts FROM processed_files WHERE file_id = ?", (file_id,)
        ).fetchone()
        if row is None:
            return True
        if row["status"] in FINAL_STATUSES:
            return False
        return row["attempts"] < MAX_ATTEMPTS

    def record(self, file: dict, status: str, *, analysis=None, dedupe_key=None, entry_id=None, error=None):
        analysis_json = json.dumps(analysis.model_dump()) if analysis is not None else None
        self.conn.execute(
            """
            INSERT INTO processed_files
                (file_id, file_name, modified_time, status, dedupe_key, entry_id, analysis_json,
                 attempts, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
                file_name = excluded.file_name,
                modified_time = excluded.modified_time,
                status = excluded.status,
                dedupe_key = COALESCE(excluded.dedupe_key, processed_files.dedupe_key),
                entry_id = COALESCE(excluded.entry_id, processed_files.entry_id),
                analysis_json = COALESCE(excluded.analysis_json, processed_files.analysis_json),
                attempts = processed_files.attempts + excluded.attempts,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                file["id"], file["name"], file.get("modifiedTime"), status, dedupe_key, entry_id,
                analysis_json, 1 if status == "error" else 0, error, _now(),
            ),
        )
        self.conn.commit()

    def is_submission_posted(self, dedupe_key: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM posted_submissions WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
            is not None
        )

    def mark_submission_posted(self, dedupe_key: str, file_id: str, entry_id: str | None):
        self.conn.execute(
            "INSERT OR IGNORE INTO posted_submissions (dedupe_key, file_id, entry_id, posted_at) VALUES (?, ?, ?, ?)",
            (dedupe_key, file_id, entry_id, _now()),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
