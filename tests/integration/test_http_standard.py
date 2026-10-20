import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
import soundfile as sf

from app.core.logging import JsonFormatter
from app.core.http import IntegrationRoute
from scripts.batch_runner import wait_job


def test_every_post_has_integration_route_and_requires_key(assessment_client):
    client = assessment_client
    for route in client.app.routes:
        if "POST" in getattr(route, "methods", set()):
            assert isinstance(route, IntegrationRoute), route.path
    client.auto_idempotency = False
    response = client.post("/api/v1/specifications", json={"prompt":"x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"


def test_specification_replay_and_concurrency_execute_once(assessment_client, monkeypatch):
    from app.services.specification_service import SpecificationService
    original = SpecificationService.create_specification
    calls = []
    async def count(*args, **kwargs):
        calls.append(True)
        return await original(*args, **kwargs)
    monkeypatch.setattr(SpecificationService, "create_specification", count)
    client = assessment_client
    def submit():
        return client.post("/api/v1/specifications", json={"prompt":"Cinematic hopeful music"}, headers={"Idempotency-Key":"spec-once"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [future.result() for future in [pool.submit(submit), pool.submit(submit)]]
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert len(calls) == 1
    conflict = client.post("/api/v1/specifications", json={"prompt":"different"}, headers={"Idempotency-Key":"spec-once"})
    assert conflict.status_code == 409


def test_multipart_upload_replay_ignores_random_boundary(assessment_client):
    client = assessment_client
    buffer = io.BytesIO()
    sf.write(buffer, np.zeros(48000), 48000, format="WAV")
    first = client.post("/api/v1/music/upload", files={"file":("speech.wav",buffer.getvalue(),"audio/wav")}, headers={"Idempotency-Key":"upload-once"})
    repeated = client.post("/api/v1/music/upload", files={"file":("speech.wav",buffer.getvalue(),"audio/wav")}, headers={"Idempotency-Key":"upload-once"})
    assert first.status_code == repeated.status_code == 200
    assert first.json() == repeated.json()
    assert repeated.headers["X-Idempotency-Replayed"] == "true"
    assert len(list(client.app.state.job_worker.storage.uploads_dir.glob("*.wav"))) == 1


def test_job_cancel_replay_and_worker_correlation(assessment_client, caplog):
    client = assessment_client
    response = client.post("/api/v1/music/jobs", json={"prompt":"Hopeful instrumental", "duration_s":5}, headers={"Idempotency-Key":"corr-job", "X-Correlation-Id":"test-correlation-42"})
    job_id = response.json()["job_id"]
    assert client.app.state.job_service.get_correlation(job_id) == "test-correlation-42"
    completed = wait_job(client, job_id)
    assert completed["status"] == "succeeded", completed
    records = [record for record in caplog.records if record.getMessage() == "Music generation completed"]
    assert records
    formatted = json.loads(JsonFormatter().format(records[-1]))
    assert formatted["correlation_id"] == "test-correlation-42"
    assert formatted["job_id"] == job_id
    created = client.post("/api/v1/music/jobs", json={"prompt":"To cancel", "duration_s":5}).json()
    first = client.post(f"/api/v1/jobs/{created['job_id']}/cancel", headers={"Idempotency-Key":"cancel-once"})
    repeated = client.post(f"/api/v1/jobs/{created['job_id']}/cancel", headers={"Idempotency-Key":"cancel-once"})
    assert first.status_code == repeated.status_code == 200
    assert first.json() == repeated.json()


@pytest.mark.parametrize("method,path,status", [("GET","/missing",404),("DELETE","/health",405)])
def test_framework_errors_are_structured_and_correlated(assessment_client, method, path, status):
    response = assessment_client.request(method,path,headers={"X-Correlation-Id":"error-correlation"})
    assert response.status_code == status
    assert response.headers["X-Correlation-Id"] == "error-correlation"
    assert set(response.json()["error"]) == {"code","message","retryable","details"}


def test_ready_detects_dead_worker(assessment_client):
    worker = assessment_client.app.state.job_worker
    assessment_client.portal.call(worker.stop)
    response = assessment_client.get("/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "NOT_READY"


def test_ready_database_failure_is_a_retryable_503(assessment_client,monkeypatch):
    def unavailable():
        raise OSError("Injected database failure")
    monkeypatch.setattr(assessment_client.app.state.job_service,"queued_or_processing_jobs",unavailable)
    response = assessment_client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.json()["error"]["retryable"]


def test_unexpected_post_error_is_structured_and_keeps_correlation(assessment_client,monkeypatch):
    from app.services.specification_service import SpecificationService
    async def fail(*args,**kwargs):
        raise RuntimeError("Private implementation details must not be exposed")
    monkeypatch.setattr(SpecificationService,"create_specification",fail)
    response = assessment_client.post("/api/v1/specifications",json={"prompt":"x"},headers={"X-Correlation-Id":"server-error-correlation"})
    assert response.status_code == 500
    assert response.headers["X-Correlation-Id"] == "server-error-correlation"
    assert response.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert "Private implementation" not in response.text


def test_worker_failure_logs_keep_original_request_correlation(assessment_client,monkeypatch,caplog):
    def fail(*args,**kwargs):
        raise TimeoutError("Injected render failure")
    monkeypatch.setattr(assessment_client.app.state.job_worker.engine,"generate",fail)
    response = assessment_client.post("/api/v1/music/jobs",json={"prompt":"Failure log","duration_s":5},headers={"X-Correlation-Id":"failed-job-correlation"})
    job = wait_job(assessment_client,response.json()["job_id"])
    assert job["status"] == "failed"
    record = next(record for record in caplog.records if record.getMessage() == "Unhandled worker error")
    formatted = json.loads(JsonFormatter().format(record))
    assert formatted["correlation_id"] == "failed-job-correlation"
    assert formatted["job_id"] == job["job_id"]