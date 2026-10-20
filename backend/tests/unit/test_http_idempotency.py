from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.exceptions import IdempotencyConflictError
from app.services.job_service import JobService


def test_http_ledger_claim_replay_and_restart(tmp_path):
    database = str(tmp_path / "jobs.db")
    service = JobService(database)
    assert service.claim_http_request("key", "payload")["claimed"]
    assert service.claim_http_request("key", "payload")["status"] is None
    service.finish_http_request("key", 201, b'{"uploaded":"one.wav"}', "application/json")
    replay = JobService(database).claim_http_request("key", "payload")
    assert not replay["claimed"]
    assert replay["status"] == 201
    assert replay["body"] == b'{"uploaded":"one.wav"}'
    with pytest.raises(IdempotencyConflictError):
        service.claim_http_request("key", "different request")


def test_http_ledger_concurrent_claims_execute_once(tmp_path):
    service = JobService(str(tmp_path / "jobs.db"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.claim_http_request, "concurrent", "same") for _ in range(2)]
        results = [future.result() for future in futures]
    assert sum(result["claimed"] for result in results) == 1


def test_job_correlation_is_persistent_and_not_overwritten(tmp_path):
    service = JobService(str(tmp_path / "jobs.db"))
    job = service.create_job("x", 5, None, "request")
    service.attach_correlation(job.job_id, "original-correlation")
    service.attach_correlation(job.job_id, "retry-correlation")
    assert service.get_correlation(job.job_id) == "original-correlation"


def test_brief_section_change_points_normalize_without_relaxing_spacing():
    from app.schemas.music import MusicJobCreate
    from pydantic import ValidationError
    request = MusicJobCreate(prompt="Calm then hopeful",duration_s=45,
        sections=[{"start_s":0,"mood":"calm"},{"start_s":12.5,"mood":"build"},{"start_s":30,"mood":"hopeful"}])
    assert [(section.start,section.end) for section in request.sections] == [(0,12.5),(12.5,30),(30,45)]
    with pytest.raises(ValidationError):
        MusicJobCreate(prompt="x",duration_s=5,sections=[{"start_s":0,"mood":"calm"},{"start_s":1,"mood":"hopeful"}])