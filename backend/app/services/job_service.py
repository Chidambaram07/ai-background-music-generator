import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.exceptions import IdempotencyConflictError, InvalidStateTransitionError, JobNotFoundError
from app.models.job import Job, JobStatus


ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.PROCESSING, JobStatus.CANCELLED},
    JobStatus.PROCESSING: {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}


class JobService:
    def __init__(self, database_path: str):
        self.database_path = database_path
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    duration_s REAL NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    request_hash TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    error_details_json TEXT,
                    specification_json TEXT,
                    result_json TEXT,
                    stem_id TEXT,
                    parent_job_id TEXT
                )"""
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "stem_id" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN stem_id TEXT")
            if "parent_job_id" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN parent_job_id TEXT")
            if "regeneration_instruction" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN regeneration_instruction TEXT")

            if "preserve_options_json" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN preserve_options_json TEXT")
            if "error_details_json" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN error_details_json TEXT")
            if "request_json" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN request_json TEXT")
            if "correlation_id" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN correlation_id TEXT")
            conn.execute("""CREATE TABLE IF NOT EXISTS http_idempotency (
                request_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
                response_status INTEGER, response_body BLOB, content_type TEXT,
                created_at TEXT NOT NULL
            )""")
            conn.commit()

    def claim_http_request(self, key: str, fingerprint: str) -> dict:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM http_idempotency WHERE request_key=?", (key,)).fetchone()
            if row:
                if row["request_hash"] != fingerprint:
                    raise IdempotencyConflictError()
                return {"claimed": False, "status": row["response_status"], "body": row["response_body"], "content_type": row["content_type"]}
            conn.execute("INSERT INTO http_idempotency (request_key,request_hash,created_at) VALUES (?,?,?)",
                         (key, fingerprint, datetime.now(timezone.utc).isoformat()))
            conn.commit()
        return {"claimed": True}

    def finish_http_request(self, key: str, status: int, body: bytes, content_type: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE http_idempotency SET response_status=?,response_body=?,content_type=? WHERE request_key=? AND response_status IS NULL",
                         (status, body, content_type, key))
            conn.commit()

    def attach_correlation(self, job_id: str, correlation_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE jobs SET correlation_id=COALESCE(correlation_id,?) WHERE job_id=?", (correlation_id, job_id))
            conn.commit()

    def get_correlation(self, job_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT correlation_id FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _dt(value: str) -> datetime:
        return datetime.fromisoformat(value)

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            job_id=row["job_id"], prompt=row["prompt"], duration_s=row["duration_s"],
            status=JobStatus(row["status"]), progress=row["progress"],
            created_at=JobService._dt(row["created_at"]), updated_at=JobService._dt(row["updated_at"]),
            idempotency_key=row["idempotency_key"], request_hash=row["request_hash"],
            error_code=row["error_code"], error_message=row["error_message"],
            error_details=json.loads(row["error_details_json"]) if row["error_details_json"] else {},
            stem_id=row["stem_id"],
            parent_job_id=row["parent_job_id"],
            regeneration_instruction=row["regeneration_instruction"],
            preserve_options=json.loads(row["preserve_options_json"]) if row["preserve_options_json"] else {},
        )

    def create_job(
        self,
        prompt: str,
        duration_s: float,
        idempotency_key: str | None,
        request_hash: str,
        *,
        stem_id: str | None = None,
        parent_job_id: str | None = None,
        regeneration_instruction: str | None = None,
        preserve_options: dict | None = None,
    ) -> Job:
        now = datetime.now(timezone.utc).isoformat()
        job_id = str(uuid4())
        with self._connect() as conn:
            if idempotency_key:
                existing = conn.execute("SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflictError()
                    return self._row_to_job(existing)
            try:
                conn.execute(
                    """INSERT INTO jobs (
    job_id,
    prompt,
    duration_s,
    status,
    progress,
    created_at,
    updated_at,
    idempotency_key,
    request_hash,
    stem_id,
    parent_job_id,
    regeneration_instruction,
    preserve_options_json
)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
    job_id,
    prompt,
    duration_s,
    JobStatus.QUEUED.value,
    0,
    now,
    now,
    idempotency_key,
    request_hash,
    stem_id,
    parent_job_id,
    regeneration_instruction,
    json.dumps(preserve_options or {}, separators=(",", ":")),
),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                if idempotency_key:
                    existing = conn.execute("SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
                    if existing:
                        if existing["request_hash"] != request_hash:
                            raise IdempotencyConflictError()
                        return self._row_to_job(existing)
                raise
        return self.get_job(job_id)

    def attach_specification(self, job_id: str, specification: dict) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE jobs SET specification_json=?, updated_at=? WHERE job_id=?", (json.dumps(specification, separators=(",", ":")), datetime.now(timezone.utc).isoformat(), job_id))
            conn.commit()

    def attach_request(self, job_id: str, payload: dict) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE jobs SET request_json=? WHERE job_id=? AND request_json IS NULL", (json.dumps(payload), job_id))
            conn.commit()

    def get_request(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT request_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def get_specification(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT specification_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row or not row["specification_json"]:
            return None
        return json.loads(row["specification_json"])

    def set_result(self, job_id: str, result: dict) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE jobs SET result_json=?, updated_at=? WHERE job_id=?", (json.dumps(result, separators=(",", ":")), datetime.now(timezone.utc).isoformat(), job_id))
            conn.commit()

    def update_result(self, job_id: str, updates: dict) -> None:
        current = self.get_result(job_id) or {}
        current.update(updates)
        self.set_result(job_id, current)

    def get_result(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT result_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row or not row["result_json"]:
            return None
        return json.loads(row["result_json"])

    def get_job(self, job_id: str) -> Job:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            raise JobNotFoundError(job_id)
        return self._row_to_job(row)

    def update_progress(self, job_id: str, progress: int) -> Job:
        job = self.get_job(job_id)
        if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            return job
        now = datetime.now(timezone.utc).isoformat()
        progress = max(0, min(100, progress))
        with self._connect() as conn:
            conn.execute("UPDATE jobs SET progress=?, updated_at=? WHERE job_id=?", (progress, now, job_id))
            conn.commit()
        return self.get_job(job_id)

    def transition(self, job_id: str, target: JobStatus, *, error_code: str | None = None, error_message: str | None = None, error_details: dict | None = None) -> Job:
        job = self.get_job(job_id)
        if target not in ALLOWED_TRANSITIONS[job.status]:
            raise InvalidStateTransitionError(job.status.value, target.value)
        progress = 100 if target == JobStatus.SUCCEEDED else job.progress
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, progress=?, updated_at=?, error_code=?, error_message=?, error_details_json=? WHERE job_id=?",
                (target.value, progress, now, error_code, error_message, json.dumps(error_details or {}, separators=(",", ":")), job_id),
            )
            conn.commit()
        return self.get_job(job_id)

    def active_stem_regeneration(self, parent_job_id: str, stem_id: str) -> Job | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE parent_job_id=? AND stem_id=? AND status IN ('queued','processing') ORDER BY created_at DESC LIMIT 1",
                (parent_job_id, stem_id),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def jobs_for_parent(self, parent_job_id: str) -> list[Job]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE parent_job_id=? ORDER BY created_at DESC",
                (parent_job_id,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def queued_or_processing_jobs(self) -> list[Job]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE status IN ('queued','processing') ORDER BY created_at").fetchall()
        return [self._row_to_job(row) for row in rows]
