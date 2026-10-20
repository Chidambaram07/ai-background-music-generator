from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Job:
    job_id: str
    prompt: str
    duration_s: float
    status: JobStatus
    progress: int
    created_at: datetime
    updated_at: datetime
    idempotency_key: str | None = None
    request_hash: str | None = None
    stem_id: str | None = None
    parent_job_id: str | None = None
    regeneration_instruction: str | None = None
    preserve_options: dict | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_details: dict | None = None
