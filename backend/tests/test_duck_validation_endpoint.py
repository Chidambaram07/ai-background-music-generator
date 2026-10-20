"""Exercise the standalone duck-validation route with real generated WAVs."""
from pathlib import Path
import time

import numpy as np
import pytest
import soundfile as sf


def _completed(client, job_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            assert job["status"] == "succeeded", job
            return job
        time.sleep(0.02)
    pytest.fail(f"Job {job_id} did not finish")


@pytest.fixture
def generated_job(client):
    response = client.post("/api/v1/music/generate", json={"specification": {
        "genre": "ambient", "mood": ["calm"], "tempo": 72, "key": "C major",
        "instruments": ["piano", "soft synth", "bass"],
        "sections": [{"start": 0, "end": 12, "mood": "calm"}],
    }})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    _completed(client, job_id)
    return job_id


@pytest.fixture
def ducked_job(client, generated_job, tmp_path):
    sr = 48000
    voice = np.zeros(12 * sr, dtype=np.float32)
    t = np.arange(3 * sr, dtype=np.float64) / sr
    voice[sr:4 * sr] = 0.2 * np.sin(2 * np.pi * 180 * t) * np.hanning(len(t))
    voice_path = tmp_path / "voiceover.wav"
    sf.write(voice_path, voice, sr, subtype="PCM_16")
    response = client.post(f"/api/v1/music/jobs/{generated_job}/duck", json={
        "voiceover_path": str(voice_path), "duck_db": 12,
        "attack_ms": 50, "release_ms": 300,
    })
    assert response.status_code == 202, response.text
    child_id = response.json()["job_id"]
    child = _completed(client, child_id)
    return generated_job, child_id, voice_path, Path(child["result"]["output_path"])


@pytest.mark.parametrize("selection", ["parent", "child"])
def test_duck_validation_returns_measured_result(client, ducked_job, selection):
    parent_id, child_id, _, _ = ducked_job
    selected_id = parent_id if selection == "parent" else child_id
    response = client.get(f"/api/v1/music/jobs/{selected_id}/duck/validation")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["job_id"] == selected_id
    measured = payload["ducking"]
    assert measured["source"] == "actual_audio_file"
    assert measured["pass"] is True, measured
    assert measured["ducking_status"] == "PASS"
    assert measured["ducking_db"] == pytest.approx(12, abs=0.01)
    assert measured["recovers_after_speech"] is True
    assert payload["speech_regions"] == measured["speech_regions"]
    assert payload["speech_regions"]
    aggregate = client.get(f"/api/v1/music/jobs/{parent_id}/validation")
    assert aggregate.status_code == 200, aggregate.text
    assert {key: value for key, value in measured.items() if key != "ducking_status"} == aggregate.json()["checks"]["ducking"]


def test_duck_validation_remeasures_changed_wav_instead_of_cached_pass(client, ducked_job):
    parent_id, _, voice_path, ducked_path = ducked_job
    music, sr = sf.read(ducked_path.parent / "mix.wav", dtype="float32")
    voice, voice_sr = sf.read(voice_path, dtype="float32")
    assert voice_sr == sr
    # Remove actual attenuation while leaving persisted job PASS untouched.
    sf.write(ducked_path, music, sr, subtype="PCM_16")
    response = client.get(f"/api/v1/music/jobs/{parent_id}/duck/validation")
    assert response.status_code == 200, response.text
    measured = response.json()["ducking"]
    assert measured["pass"] is False
    assert measured["ducking_status"] == "FAIL"
    assert abs(measured["ducking_db"]) < 0.01


def test_duck_validation_keeps_missing_ducking_409(client, generated_job):
    response = client.get(f"/api/v1/music/jobs/{generated_job}/duck/validation")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VALIDATION_NOT_READY"


def test_duck_validation_keeps_nonterminal_job_409(client):
    service = client.app.state.job_service
    job = service.create_job("Not submitted to worker", 12, None, "duck-validation-pending")
    response = client.get(f"/api/v1/music/jobs/{job.job_id}/duck/validation")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VALIDATION_NOT_READY"


def test_duck_validation_keeps_unknown_job_404(client):
    response = client.get("/api/v1/music/jobs/unknown-duck-job/duck/validation")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"