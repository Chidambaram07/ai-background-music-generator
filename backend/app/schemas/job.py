from datetime import datetime
from pydantic import BaseModel, Field
from app.models.job import JobStatus


class JobError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: dict = Field(default_factory=dict)


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    prompt: str
    duration_s: float
    stem_id: str | None = None
    parent_job_id: str | None = None
    error: JobError | None = None
    result: dict | None = None


class CancelResponse(BaseModel):
    job_id: str
    status: JobStatus
