import io
import json
import pytest
import time
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf

from app.models.job import JobStatus


def wait_for_terminal(client, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        response = client.get(f"/api/v1/jobs/{job_id}")
        data = response.json()
        if data["status"] in {"succeeded", "failed", "cancelled"}:
            return data
        time.sleep(0.02)
    raise AssertionError("job did not reach a terminal state")


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}


def test_create_job_returns_202_and_queued(client):
    response = client.post("/api/v1/music/jobs", json={"prompt": "Calm cinematic background music", "duration_s": 45})
    assert response.status_code == 202
    data = response.json()
    assert data["job_id"]
    assert data["status"] == "queued"


def test_get_job_and_worker_succeeds(client):
    created = client.post("/api/v1/music/jobs", json={"prompt": "Energetic electronic", "duration_s": 30}).json()
    data = wait_for_terminal(client, created["job_id"])
    assert data["status"] == "succeeded"
    assert data["progress"] == 100


def test_empty_prompt(client):
    response = client.post("/api/v1/music/jobs", json={"prompt": "   ", "duration_s": 45})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_duration(client):
    response = client.post("/api/v1/music/jobs", json={"prompt": "Calm", "duration_s": 301})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DURATION"


def test_unknown_job(client):
    response = client.get("/api/v1/jobs/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_idempotency_returns_same_job(client):
    headers = {"Idempotency-Key": "demo-key-1"}
    payload = {"prompt": "Calm cinematic", "duration_s": 45}
    first = client.post("/api/v1/music/jobs", json=payload, headers=headers)
    second = client.post("/api/v1/music/jobs", json=payload, headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]


def test_idempotency_conflict(client):
    headers = {"Idempotency-Key": "demo-key-2"}
    client.post("/api/v1/music/jobs", json={"prompt": "Calm", "duration_s": 45}, headers=headers)
    response = client.post("/api/v1/music/jobs", json={"prompt": "Energetic", "duration_s": 45}, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_cancel_job(client):
    created = client.post("/api/v1/music/jobs", json={"prompt": "Slow ambient", "duration_s": 45}).json()
    response = client.post(f"/api/v1/jobs/{created['job_id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_invalid_state_transition(client):
    created = client.post("/api/v1/music/jobs", json={"prompt": "Test", "duration_s": 45}).json()
    time.sleep(0.15)
    response = client.post(f"/api/v1/jobs/{created['job_id']}/cancel")
    if response.status_code == 200:
        # Depending on scheduling, it may still be processing; a second cancellation is terminal and must fail.
        response = client.post(f"/api/v1/jobs/{created['job_id']}/cancel")
    assert response.status_code == 409

MODULE3_SPEC = {
    "genre": "cinematic",
    "mood": ["peaceful", "hopeful"],
    "tempo": 82,
    "key": "C major",
    "instruments": ["piano", "soft pads", "bass", "light percussion"],
    "sections": [
        {"start": 0, "end": 10, "mood": "peaceful"},
        {"start": 10, "end": 20, "mood": "hopeful"},
    ],
}

MODULE3_STEM_IDS = ["piano", "soft_pads", "bass", "light_percussion"]


def create_succeeded_module3_job(client):
    response = client.post("/api/v1/music/generate", json={"specification": MODULE3_SPEC})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    body = wait_for_terminal(client, job_id, timeout=60)
    assert body["status"] == "succeeded"
    return job_id


def _voiceover_signal(duration_s: float = 20.0, sample_rate: int = 48000) -> np.ndarray:
    total = int(duration_s * sample_rate)
    audio = np.zeros(total, dtype=np.float32)
    start = int(0.5 * sample_rate)
    end = int(4.0 * sample_rate)
    t = np.arange(start, end, dtype=np.float32) / sample_rate
    audio[start:end] = 0.55 * np.sin(2 * np.pi * 180 * t)
    return audio


def test_get_stems_for_succeeded_job(client):
    job_id = create_succeeded_module3_job(client)
    response = client.get(f"/api/v1/music/jobs/{job_id}/stems")
    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == job_id
    assert [item["stem_id"] for item in data["stems"]] == MODULE3_STEM_IDS
    assert [item["name"] for item in data["stems"]] == ["piano", "soft pads", "bass", "light percussion"]
    assert all("?v=" in item["url"] for item in data["stems"])
    assert "?v=" in data["mix_url"]


def test_get_stems_unknown_job(client):
    response = client.get("/api/v1/music/jobs/does-not-exist/stems")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_get_stems_non_succeeded_job(client):
    created = client.post("/api/v1/music/jobs", json={"prompt": "temporary", "duration_s": 45}).json()
    cancelled = client.post(f"/api/v1/jobs/{created['job_id']}/cancel")
    assert cancelled.status_code == 200
    response = client.get(f"/api/v1/music/jobs/{created['job_id']}/stems")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STEM_NOT_READY"


def test_regenerate_invalid_stem(client):
    job_id = create_succeeded_module3_job(client)
    response = client.post(f"/api/v1/music/jobs/{job_id}/stems/vocals/regenerate")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_STEM"


@pytest.mark.parametrize("stem_id", MODULE3_STEM_IDS)
def test_regenerate_single_stem_isolated(client, stem_id):
    import hashlib

    job_id = create_succeeded_module3_job(client)
    before = {}
    for name in [*MODULE3_STEM_IDS, "mix"]:
        audio = client.get(f"/api/v1/music/jobs/{job_id}/{('mix' if name == 'mix' else f'stems/{name}')}")
        assert audio.status_code == 200
        before[name] = hashlib.sha256(audio.content).hexdigest()

    before_listing = client.get(f"/api/v1/music/jobs/{job_id}/stems").json()
    before_urls = {item["stem_id"]: item["url"] for item in before_listing["stems"]}

    accepted = client.post(f"/api/v1/music/jobs/{job_id}/stems/{stem_id}/regenerate")
    assert accepted.status_code == 202
    regeneration_job_id = accepted.json()["job_id"]
    completed = wait_for_terminal(client, regeneration_job_id, timeout=60)
    assert completed["status"] == "succeeded"
    assert completed["stem_id"] == stem_id
    assert completed["parent_job_id"] == job_id

    after = {}
    for name in [*MODULE3_STEM_IDS, "mix"]:
        audio = client.get(f"/api/v1/music/jobs/{job_id}/{('mix' if name == 'mix' else f'stems/{name}')}")
        assert audio.status_code == 200
        after[name] = hashlib.sha256(audio.content).hexdigest()

    assert after[stem_id] != before[stem_id]
    for other in [name for name in MODULE3_STEM_IDS if name != stem_id]:
        assert after[other] == before[other]
    assert after["mix"] != before["mix"]

    after_listing = client.get(f"/api/v1/music/jobs/{job_id}/stems").json()
    after_urls = {item["stem_id"]: item["url"] for item in after_listing["stems"]}
    assert after_urls[stem_id] != before_urls[stem_id]
    for other in [name for name in MODULE3_STEM_IDS if name != stem_id]:
        assert after_urls[other] == before_urls[other]
    assert after_listing["mix_url"] != before_listing["mix_url"]


def test_download_package_includes_real_generated_artifacts(client, tmp_path: Path):
    job_id = create_succeeded_module3_job(client)

    loop_response = client.post(f"/api/v1/music/jobs/{job_id}/loop", json={"bpm": 82, "beats_per_bar": 4})
    assert loop_response.status_code == 202, loop_response.text
    assert wait_for_terminal(client, loop_response.json()["job_id"], timeout=60)["status"] == "succeeded"

    voiceover_path = tmp_path / "voiceover.wav"
    sf.write(voiceover_path, _voiceover_signal(), 48000, subtype="PCM_16")
    duck_response = client.post(
        f"/api/v1/music/jobs/{job_id}/duck",
        json={
            "voiceover_path": str(voiceover_path),
            "duck_db": 12.0,
            "attack_ms": 50.0,
            "release_ms": 300.0,
        },
    )
    assert duck_response.status_code == 202, duck_response.text
    assert wait_for_terminal(client, duck_response.json()["job_id"], timeout=60)["status"] == "succeeded"

    response = client.get(f"/api/v1/jobs/{job_id}/download/package")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    assert "attachment; filename=\"incubrix_" in response.headers["content-disposition"]

    root = client.app.state.job_worker.storage.job_dir(job_id)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "mix.wav" in names
        assert "mix_ducked.wav" in names
        assert "loop.wav" in names
        assert "manifest.json" in names
        assert {"stems/piano.wav", "stems/soft_pads.wav", "stems/bass.wav", "stems/light_percussion.wav"}.issubset(names)
        assert archive.read("mix.wav") == (root / "mix.wav").read_bytes()
        assert archive.read("mix_ducked.wav") == (root / "mix_ducked.wav").read_bytes()
        assert archive.read("loop.wav") == (root / "mix_looped.wav").read_bytes()
        assert json.loads(archive.read("manifest.json").decode("utf-8")) == client.get(f"/api/v1/jobs/{job_id}").json()["result"]["manifest"]


def test_download_package_skips_missing_optional_artifacts(client):
    job_id = create_succeeded_module3_job(client)

    response = client.get(f"/api/v1/jobs/{job_id}/download/package")
    assert response.status_code == 200, response.text

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "mix.wav" in names
        assert "manifest.json" in names
        assert "loop.wav" not in names
        assert "mix_ducked.wav" not in names
        assert "mix.mp3" not in names


def test_download_package_unknown_job_returns_404(client):
    response = client.get("/api/v1/jobs/does-not-exist/download/package")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"
