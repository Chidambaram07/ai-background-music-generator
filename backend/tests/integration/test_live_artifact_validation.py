from pathlib import Path
import time

import soundfile as sf


def test_validation_api_does_not_return_cached_pass_after_wav_mutation(client):
    response = client.post("/api/v1/music/generate", json={"specification": {
        "genre": "cinematic", "mood": ["calm"], "tempo": 90, "key": "C major",
        "instruments": ["Drums", "Bass", "Piano", "Pads"],
        "sections": [{"start": 0, "end": 45, "mood": "calm"}],
    }})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    for _ in range(500):
        payload = client.get(f"/api/v1/jobs/{job_id}").json()
        if payload["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)
    assert payload["status"] == "succeeded", payload
    path = client.app.state.job_worker.storage.job_dir(job_id) / "mix.wav"
    audio, sr = sf.read(path, dtype="float32")
    sf.write(path, audio[:sr], sr, subtype="PCM_16")
    measured = client.get(f"/api/v1/music/jobs/{job_id}/validation")
    assert measured.status_code == 200, measured.text
    assert measured.json()["valid"] is False
    assert measured.json()["checks"]["duration"]["actual_s"] == 1.0
    assert payload["result"]["validation_result"]["valid"] is True