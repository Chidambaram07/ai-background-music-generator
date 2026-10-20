import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.services.manifest import file_hash
from scripts.batch_runner import generate, measure_job, narration_fixture


@pytest.mark.parametrize("duration", [0,-1,301])
def test_invalid_duration(assessment_client, duration):
    response = assessment_client.post("/api/v1/music/jobs", json={"prompt":"x", "duration_s":duration})
    assert response.status_code == 422
    assert response.json()["error"]["code"]


@pytest.mark.parametrize("case", json.loads((Path(__file__).parents[1] / "fixtures/assessment_requests.json").read_text())[10:], ids=lambda case:case["test_id"])
def test_vague_imitation_and_vocal_requests(assessment_client, case):
    job = generate(assessment_client, case["request"])
    assert job["status"] == "succeeded", job
    manifest = job["result"]["manifest"]
    assert manifest["warnings"]
    assert all(item["generation_method"] == "procedural_synthesis" for item in manifest["stems"])
    assert not any(item["name"] in {"vocals","voice","singer"} for item in manifest["stems"])
    assert all(measure_job(assessment_client.app.state.job_worker.storage.job_dir(job["job_id"]), case["request"])["validation"].values())


def test_concurrent_and_duplicate_requests_preserve_files(assessment_client):
    client = assessment_client
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(generate, client, {"prompt":"Calm music", "duration_s":5}, "one")
        second = pool.submit(generate, client, {"prompt":"Energetic electronic", "duration_s":6}, "two")
        jobs = [first.result(), second.result()]
    assert all(job["status"] == "succeeded" for job in jobs), jobs
    assert jobs[0]["job_id"] != jobs[1]["job_id"]
    first_dir = client.app.state.job_worker.storage.job_dir(jobs[0]["job_id"])
    before = file_hash(first_dir / "mix.wav")
    repeated = generate(client, {"prompt":"Calm music", "duration_s":5}, "one")
    assert repeated["job_id"] == jobs[0]["job_id"]
    assert file_hash(first_dir / "mix.wav") == before


@pytest.mark.parametrize("kind", ["normal","long-silence","no-speech"])
def test_narration(assessment_client, kind):
    response = assessment_client.post("/api/v1/music/upload", files={"file":(f"{kind}.wav", narration_fixture(kind), "audio/wav")})
    assert response.status_code == 200, response.text
    job = generate(assessment_client, {"prompt":"Calm narration music", "duration_s":6, "narration_url":response.json()["url"]})
    if kind == "no-speech":
        assert job["status"] == "succeeded",job
        assert job["result"]["validation_result"]["checks"]["ducking"]["no_speech_unchanged"]
    else:
        assert job["status"] == "succeeded", job
        assert job["result"]["manifest"]["ducking"]["enabled"]


def test_loopable_request(assessment_client):
    job = generate(assessment_client, {"prompt":"Looping electronic background", "duration_s":6, "tempo_bpm":120, "loopable":True})
    assert job["status"] == "succeeded", job
    assert job["result"]["validation_result"]["checks"]["looping"]["pass"]