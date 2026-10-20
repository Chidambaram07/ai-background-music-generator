import math
from pathlib import Path

import numpy as np
import soundfile as sf

from app.schemas.specification import MusicSpecification, TimelineManifest, TimelineSection


def test_timeline_manifest_validates_section_timing():
    bad = TimelineManifest(
        bpm=100,
        genre="cinematic",
        mood=["calm"],
        instruments=["piano", "pads"],
        stem_count=2,
        key="C major",
        beats_per_bar=4,
        sections=[
            TimelineSection(name="INTRO", start_s=0.0, end_s=15.0, duration_s=15.0),
            TimelineSection(name="BUILD", start_s=15.0, end_s=30.0, duration_s=15.0),
            TimelineSection(name="MAIN", start_s=28.0, end_s=45.0, duration_s=17.0),
        ],
        duration_s=45.0,
    )
    assert bad.sections[2].start_s == 28.0
    assert abs(bad.beat_duration_s - 0.6) < 1e-9
    assert abs(bad.bar_duration_s - 2.4) < 1e-9

    try:
        TimelineManifest(
            bpm=100,
            genre="cinematic",
            mood=["calm"],
            instruments=["piano"],
            stem_count=1,
            key="C major",
            beats_per_bar=4,
            sections=[
                TimelineSection(name="INTRO", start_s=0.0, end_s=10.0, duration_s=10.0),
                TimelineSection(name="BUILD", start_s=5.0, end_s=20.0, duration_s=15.0),
            ],
            duration_s=20.0,
        )
    except ValueError:
        return
    raise AssertionError("overlapping sections should be rejected")


def test_music_job_generates_manifest_and_looped_wav(client):
    spec = {
        "genre": "cinematic",
        "mood": ["calm", "hopeful"],
        "tempo": 100,
        "key": "C major",
        "instruments": ["piano", "pads", "bass"],
        "sections": [
            {"start": 0, "end": 15, "mood": "calm"},
            {"start": 15, "end": 30, "mood": "hopeful"},
            {"start": 30, "end": 45, "mood": "hopeful"},
        ],
    }
    response = client.post("/api/v1/music/generate", json={"specification": spec})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]

    for _ in range(3000):
        payload = client.get(f"/api/v1/jobs/{job_id}")
        if payload.status_code == 200 and payload.json()["status"] == "succeeded":
            break
        import time
        time.sleep(0.02)

    job_payload = client.get(f"/api/v1/jobs/{job_id}").json()
    assert "manifest" in job_payload["result"], job_payload
    manifest = job_payload["result"]["manifest"]
    assert manifest["bpm"] == 100
    assert abs(manifest["beat_duration_s"] - 0.6) < 1e-6
    assert abs(manifest["bar_duration_s"] - 2.4) < 1e-6
    assert len(manifest["sections"]) == 3

    loop_response = client.post(f"/api/v1/music/jobs/{job_id}/loop", json={"bpm": 100, "beats_per_bar": 4})
    assert loop_response.status_code == 202, loop_response.text
    loop_job = loop_response.json()
    assert loop_job["status"] == "queued"

    for _ in range(3000):
        payload = client.get(f"/api/v1/jobs/{loop_job['job_id']}")
        if payload.status_code == 200 and payload.json()["status"] == "succeeded":
            break
        import time
        time.sleep(0.02)

    loop_download = client.get(f"/api/v1/music/jobs/{job_id}/loop")
    assert loop_download.status_code == 200, loop_download.text
    assert loop_download.headers["content-type"].startswith("audio/wav")
    data, sample_rate = sf.read(io:=__import__('io').BytesIO(loop_download.content), dtype="float32")
    assert sample_rate == 48000
    assert abs(float(len(data) / sample_rate) - 45.0) < 0.2

    validation = client.get(f"/api/v1/music/jobs/{job_id}/loop/validation").json()
    assert validation["loop"]["enabled"] is True
    assert validation["loop"]["status"] == "PASS"


def test_generation_manifest_preserves_explicit_configuration(client):
    spec = {
        "genre": "documentary",
        "mood": ["focused"],
        "tempo": 73,
        "key": "F# minor",
        "instruments": ["Piano", "Pads", "Bass"],
        "sections": [
            {"name": "INTRO", "start": 0, "end": 8.14, "mood": "focused"},
            {"name": "BUILD", "start": 8.14, "end": 20.35, "mood": "focused"},
            {"name": "MAIN", "start": 20.35, "end": 32.56, "mood": "focused"},
            {"name": "OUTRO", "start": 32.56, "end": 37.0, "mood": "focused"},
        ],
    }
    response = client.post("/api/v1/music/generate", json={"specification": spec})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]

    for _ in range(3000):
        payload = client.get(f"/api/v1/jobs/{job_id}")
        if payload.status_code == 200 and payload.json()["status"] == "succeeded":
            break
        import time
        time.sleep(0.02)

    job_payload = client.get(f"/api/v1/jobs/{job_id}").json()
    manifest = job_payload["result"]["manifest"]
    assert job_payload["duration_s"] == 37.0
    assert manifest["duration_s"] == 37.0
    assert manifest["bpm"] == 73
    assert manifest["key"] == "F# minor"
    assert manifest["genre"] == "documentary"
    assert manifest["mood"] == ["focused"]
    assert manifest["instruments"] == ["Piano", "Pads", "Bass"]
    assert manifest["stem_count"] == 3
    assert manifest["sections"][-1]["end_s"] == 37.0
    assert [stem["stem_id"] for stem in job_payload["result"]["stems"]] == ["piano", "pads", "bass"]
    assert [stem["name"] for stem in job_payload["result"]["stems"]] == ["Piano", "Pads", "Bass"]
