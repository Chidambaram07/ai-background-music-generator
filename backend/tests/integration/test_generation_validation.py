from pathlib import Path


def test_generation_validation_persists_measured_manifest_and_passes(client):
    response = client.post("/api/v1/music/generate", json={"specification": {
        "genre": "cinematic",
        "mood": ["peaceful", "hopeful"],
        "tempo": 90,
        "key": "C major",
        "instruments": ["piano", "soft pads", "bass", "light percussion"],
        "sections": [
            {"start": 0, "end": 22.5, "mood": "peaceful"},
            {"start": 22.5, "end": 45, "mood": "hopeful"},
        ],
    }})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]

    import time
    for _ in range(3000):
        payload = client.get(f"/api/v1/jobs/{job_id}").json()
        if payload["status"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.02)

    assert payload["status"] == "succeeded", payload
    validation_result = payload["result"]["validation_result"]
    assert validation_result["valid"] is True
    assert validation_result["checks"]["duration"]["pass"] is True
    assert validation_result["checks"]["sample_rate"]["actual_hz"] == 48000
    assert validation_result["checks"]["format"]["subtype"] == "PCM_16"
    assert validation_result["checks"]["stems"]["pass"] is True
    assert validation_result["checks"]["stem_reconstruction"]["pass"] is True

    manifest = payload["result"]["manifest"]
    assert manifest["validation"]["valid"] is True
    manifest_path = client.app.state.job_worker.storage.job_dir(job_id) / "manifest.json"
    assert manifest_path.exists()

    validation_response = client.get(f"/api/v1/music/jobs/{job_id}/validation")
    assert validation_response.status_code == 200, validation_response.text
    body = validation_response.json()
    assert body["valid"] is True
    assert body["checks"]["loudness"]["measured_lufs"] == validation_result["checks"]["loudness"]["measured_lufs"]