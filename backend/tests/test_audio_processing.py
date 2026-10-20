from pathlib import Path

import numpy as np
import soundfile as sf

from app.services.audio_processing import AudioProcessingConfig, AudioProcessor


def processor() -> AudioProcessor:
    return AudioProcessor(AudioProcessingConfig())


def tone(duration=1.0, sr=44100, amplitude=0.2):
    t = np.arange(int(duration * sr), dtype=np.float32) / sr
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def test_duration_is_measured_and_trimmed_to_tolerance(tmp_path: Path):
    path = tmp_path / "long.wav"
    sf.write(path, tone(1.03), 44100, subtype="PCM_16")
    validation = processor().process_file(path, 1.0)
    assert validation["actual_duration_seconds"] == 1.0
    assert validation["duration_error_seconds"] == 0.0
    assert validation["duration_status"] == "PASS"


def test_44100_is_actually_resampled_to_48000(tmp_path: Path):
    path = tmp_path / "resample.wav"
    sf.write(path, tone(1.0, 44100), 44100, subtype="PCM_16")
    processor().process_file(path, 1.0)
    audio, sr = sf.read(path, dtype="float32")
    assert sr == 48000
    assert len(audio) == 48000


def test_existing_48000_is_preserved(tmp_path: Path):
    path = tmp_path / "native.wav"
    sf.write(path, tone(1.0, 48000), 48000, subtype="PCM_16")
    processor().process_file(path, 1.0)
    audio, sr = sf.read(path, dtype="float32")
    assert sr == 48000
    assert len(audio) == 48000


def test_mix_loudness_normalizes_toward_minus_16_lufs(tmp_path: Path):
    path = tmp_path / "loudness.wav"
    sf.write(path, tone(3.0, 44100, 0.05), 44100, subtype="PCM_16")
    validation = processor().process_file(path, 3.0, normalize_loudness=True)
    assert validation["loudness_status"] == "PASS"
    assert abs(validation["measured_lufs"] + 16.0) <= 1.0


def test_true_peak_is_kept_at_or_below_minus_1_dbtp(tmp_path: Path):
    path = tmp_path / "peak.wav"
    sf.write(path, tone(1.0, 48000, 0.99), 48000, subtype="PCM_16")
    validation = processor().process_file(path, 1.0)
    assert validation["true_peak_dbTP"] <= -1.0
    assert validation["true_peak_status"] == "PASS"


def test_clipping_is_detected_and_corrected(tmp_path: Path):
    path = tmp_path / "clipped.wav"
    audio = tone(1.0, 48000, 1.2)
    sf.write(path, audio, 48000, subtype="PCM_16")
    validation = processor().process_file(path, 1.0)
    processed, sr = sf.read(path, dtype="float32")
    assert sr == 48000
    assert validation["clipping_detected_before_processing"] is True
    assert validation["clipping_corrected"] is True
    assert validation["clipping_detected"] is False
    assert np.max(np.abs(processed)) < 1.0


def test_fades_do_not_change_duration(tmp_path: Path):
    path = tmp_path / "fades.wav"
    sf.write(path, tone(1.0, 44100, 0.3), 44100, subtype="PCM_16")
    validation = processor().process_file(path, 1.0)
    audio, sr = sf.read(path, dtype="float32")
    assert sr == 48000
    assert len(audio) == 48000
    assert validation["fade_in_ms"] == 10.0
    assert validation["fade_out_ms"] == 10.0
    assert abs(float(audio[0])) < 0.01
    assert abs(float(audio[-1])) < 0.01


def test_validation_endpoint_is_exposed(client):
    spec = {
        "genre": "cinematic",
        "mood": ["peaceful", "hopeful"],
        "tempo": 82,
        "key": "C major",
        "instruments": ["piano", "soft pads", "bass", "light percussion"],
        "sections": [{"start": 0, "end": 10, "mood": "peaceful"}],
    }
    response = client.post("/api/v1/music/generate", json={"specification": spec})
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    import time
    for _ in range(200):
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)
    assert body["status"] == "succeeded"
    assert body["result"]["sample_rate"] == 48000
    assert body["result"]["audio_validation"]["overall_status"] == "PASS"

    report = client.get(f"/api/v1/music/jobs/{job_id}/validation")
    assert report.status_code == 200
    data = report.json()
    assert data["overall_status"] == "PASS"
    assert data["audio_validation"]["sample_rate"] == 48000
    assert data["audio_validation"]["true_peak_dbTP"] <= -1.0
    assert set(data["stems"]) == {"piano", "soft_pads", "bass", "light_percussion"}
