from pathlib import Path
import time

import numpy as np
import soundfile as sf

import json


SR = 48000


def _speech_like_signal(duration_s: float, start_s: float, end_s: float, amplitude: float = 0.5) -> np.ndarray:
    total = int(duration_s * SR)
    audio = np.zeros(total, dtype=np.float32)
    start = int(start_s * SR)
    end = int(end_s * SR)
    t = np.arange(start, min(end, total), dtype=np.float32) / SR
    tone = np.sin(2 * np.pi * 180 * t)
    envelope = 0.5 - 0.5 * np.cos(2 * np.pi * (t - start / SR) / max((end - start) / SR, 1e-6))
    audio[start:end] = amplitude * tone * envelope
    return audio


def _voiceover_signal(duration_s: float = 12.0) -> np.ndarray:
    voiceover = np.zeros(int(duration_s * SR), dtype=np.float32)
    voiceover += _speech_like_signal(duration_s, 0.0, 4.5, amplitude=0.55)
    voiceover += _speech_like_signal(duration_s, 7.0, 9.0, amplitude=0.5)
    return voiceover


def wait_for_terminal(client, job_id, timeout=2):
    end = time.time() + timeout
    while time.time() < end:
        response = client.get(f"/api/v1/jobs/{job_id}")
        data = response.json()
        if data["status"] in {"succeeded", "failed", "cancelled"}:
            return data
        time.sleep(0.02)
    raise AssertionError("job did not reach a terminal state")


def create_succeeded_module3_job(client):
    response = client.post("/api/v1/music/generate", json={"specification": {
        "genre": "cinematic",
        "mood": ["peaceful", "hopeful"],
        "tempo": 82,
        "key": "C major",
        "instruments": ["piano", "soft pads", "bass", "light percussion"],
        "sections": [
            {"start": 0, "end": 10, "mood": "peaceful"},
            {"start": 10, "end": 20, "mood": "hopeful"},
        ],
    }})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    body = wait_for_terminal(client, job_id, timeout=5)
    assert body["status"] == "succeeded"
    return job_id


def test_ps1_validation_report_uses_backend_loop_validation(client):
    job_id = create_succeeded_module3_job(client)

    accepted = client.post(
        f"/api/v1/music/jobs/{job_id}/loop",
        json={"bpm": 82, "beats_per_bar": 4},
    )
    assert accepted.status_code == 202, accepted.text
    loop_job_id = accepted.json()["job_id"]
    loop_job = wait_for_terminal(client, loop_job_id, timeout=5)
    assert loop_job["status"] == "succeeded"

    loop_validation = client.get(f"/api/v1/music/jobs/{job_id}/loop/validation")
    assert loop_validation.status_code == 200, loop_validation.text
    loop_payload = loop_validation.json()["loop"]

    report_response = client.post(f"/api/v1/music/jobs/{job_id}/ps1-validation")
    assert report_response.status_code == 200, report_response.text
    report = report_response.json()

    a5 = next(item for item in report["requirements"] if item["requirement_id"] == "A5")
    assert a5["associated_job_id"] == loop_job_id
    assert a5["status"] == loop_payload["status"]
    assert a5["evidence"]["loop_start_s"] == loop_payload["loop_start_s"]
    assert a5["evidence"]["loop_end_s"] == loop_payload["loop_end_s"]
    assert a5["evidence"]["loop_duration_s"] == loop_payload["loop_duration_s"]
    assert a5["evidence"]["boundary_discontinuity"] == loop_payload["boundary_discontinuity"]
    assert report["job_id"] == job_id
    assert report["validated_at"]


def test_ps1_validation_report_export_downloads_backend_generated_json(client):
    job_id = create_succeeded_module3_job(client)

    response = client.get(f"/api/v1/music/jobs/{job_id}/ps1-validation/report")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment; filename=\"ps1-validation-" in response.headers["content-disposition"]

    payload = json.loads(response.text)
    assert payload["job_id"] == job_id
    assert len(payload["requirements"]) == 13
    assert next(item for item in payload["requirements"] if item["requirement_id"] == "M1")


def test_ps1_validation_report_distinguishes_measured_audio_from_unverified_rights(client, tmp_path: Path):
    job_id = create_succeeded_module3_job(client)

    loop_response = client.post(f"/api/v1/music/jobs/{job_id}/loop", json={"bpm": 82, "beats_per_bar": 4})
    assert loop_response.status_code == 202, loop_response.text
    loop_job_id = loop_response.json()["job_id"]
    assert wait_for_terminal(client, loop_job_id, timeout=5)["status"] == "succeeded"

    voiceover_path = tmp_path / "voiceover.wav"
    sf.write(voiceover_path, _voiceover_signal(), SR, subtype="PCM_16")

    duck_response = client.post(
        f"/api/v1/music/jobs/{job_id}/duck",
        json={"voiceover_path": str(voiceover_path), "duck_db": 12.0, "attack_ms": 50.0, "release_ms": 300.0},
    )
    assert duck_response.status_code == 202, duck_response.text
    duck_job_id = duck_response.json()["job_id"]
    assert wait_for_terminal(client, duck_job_id, timeout=5)["status"] == "succeeded"

    regeneration_response = client.post(f"/api/v1/music/jobs/{job_id}/stems/piano/regenerate")
    assert regeneration_response.status_code == 202, regeneration_response.text
    regeneration_job_id = regeneration_response.json()["job_id"]
    regeneration_payload = wait_for_terminal(client, regeneration_job_id, timeout=5)
    assert regeneration_payload["status"] == "succeeded"

    report_response = client.post(f"/api/v1/music/jobs/{job_id}/ps1-validation")
    assert report_response.status_code == 200, report_response.text
    report = report_response.json()

    statuses = {item["requirement_id"]: item["status"] for item in report["requirements"]}
    assert report["overall_status"] == "WARNING"
    assert statuses["M3"] == "PASS"
    assert statuses["M4"] == "WARNING"
    assert statuses["C1"] == "PASS"
    assert statuses["C2"] == "PASS"
    assert statuses["A4"] == "PASS"

    persisted = client.get(f"/api/v1/jobs/{job_id}").json()["result"]
    assert persisted["manifest"]["commercial_rights"]["commercial_use_permitted"] is None
    assert persisted["manifest"]["commercial_rights"]["third_party_material_used"] is False
    assert persisted["ps1_evidence"]["instrumental"]["instrumental"] is True
    assert persisted["ps1_evidence"]["prompt_variety"]["status"] == "PASS"
    assert persisted["ps1_evidence"]["duration_range"]["status"] == "PASS"
    assert persisted["ps1_evidence"]["stem_regeneration"]["status"] == "PASS"