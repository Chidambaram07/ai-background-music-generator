import json
from pathlib import Path

import pytest

from scripts.batch_runner import generate, measure_job, regeneration_check


@pytest.mark.e2e
@pytest.mark.parametrize("duration", [5, 6, 10, 30, 45, 60, 180, 300])
def test_real_duration_outputs(assessment_client, duration):
    request = {"prompt":"Cinematic hopeful instrumental background", "duration_s":duration}
    job = generate(assessment_client, request)
    assert job["status"] == "succeeded", job
    metrics = measure_job(assessment_client.app.state.job_worker.storage.job_dir(job["job_id"]), request)
    assert all(metrics["validation"].values()), metrics


@pytest.mark.e2e
@pytest.mark.parametrize("case", json.loads((Path(__file__).parents[1] / "fixtures/assessment_requests.json").read_text())[:10], ids=lambda case:case["test_id"])
def test_prompt_outputs(assessment_client, case):
    job = generate(assessment_client, case["request"])
    assert job["status"] == "succeeded", job
    metrics = measure_job(assessment_client.app.state.job_worker.storage.job_dir(job["job_id"]), case["request"])
    assert all(metrics["validation"].values()), metrics
    assert metrics["warnings"]


@pytest.mark.e2e
def test_drums_hash_regeneration(assessment_client):
    job = generate(assessment_client, {"prompt":"Cinematic hopeful music", "duration_s":45})
    assert job["status"] == "succeeded", job
    result = regeneration_check(assessment_client, job["job_id"], assessment_client.app.state.job_worker.storage.job_dir(job["job_id"]))
    assert result["hashes_before"]["drums"] != result["hashes_after"]["drums"]