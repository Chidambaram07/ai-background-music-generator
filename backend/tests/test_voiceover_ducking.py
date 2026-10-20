from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.services.audio_processing import AudioProcessingConfig, AudioProcessor


SR = 48000


def _speech_like_signal(duration_s: float, start_s: float, end_s: float, amplitude: float = 0.35) -> np.ndarray:
    total = int(duration_s * SR)
    audio = np.zeros(total, dtype=np.float32)
    start = int(start_s * SR)
    end = int(end_s * SR)
    t = np.arange(start, min(end, total), dtype=np.float32) / SR
    tone = np.sin(2 * np.pi * 180 * t)
    envelope = 0.5 - 0.5 * np.cos(2 * np.pi * (t - start / SR) / max((end - start) / SR, 1e-6))
    audio[start:end] = amplitude * tone * envelope
    return audio


def _music_like_signal(duration_s: float, *, amplitude: float = 0.2) -> np.ndarray:
    t = np.arange(int(duration_s * SR), dtype=np.float32) / SR
    chord = np.sin(2 * np.pi * 110 * t) + 0.6 * np.sin(2 * np.pi * 220 * t)
    return (amplitude * chord).astype(np.float32)


def test_detect_speech_regions_for_speech_then_silence(tmp_path: Path):
    voiceover = np.zeros(int(10 * SR), dtype=np.float32)
    voiceover += _speech_like_signal(10.0, 0.0, 4.5, amplitude=0.5)
    voiceover += _speech_like_signal(10.0, 7.0, 9.0, amplitude=0.45)
    processor = AudioProcessor(AudioProcessingConfig())
    regions = processor.detect_speech_regions(voiceover, SR)
    assert len(regions) >= 2
    assert regions[0]["start_s"] < 5.0
    assert regions[0]["end_s"] > 3.0
    assert regions[-1]["start_s"] < 9.0


def test_ducked_audio_reduces_music_during_speech(tmp_path: Path):
    music = _music_like_signal(10.0)
    voiceover = np.zeros_like(music)
    voiceover += _speech_like_signal(10.0, 0.0, 4.5, amplitude=0.5)
    processor = AudioProcessor(AudioProcessingConfig())
    ducked, metadata = processor.duck_music(music, voiceover, duck_db=12.0, attack_ms=50.0, release_ms=300.0)
    assert metadata["ducking_status"] == "PASS"
    assert 8.0 <= metadata["measured_duck_db"] <= 16.0
    assert metadata["speech_regions"]
    assert np.max(np.abs(ducked)) <= 1.0


@pytest.mark.parametrize("duck_db", [8.0, 12.0, 15.0])
def test_ducking_targets_are_measured_from_audio(duck_db: float):
    music = _music_like_signal(12.0, amplitude=0.25)
    voiceover = np.zeros_like(music)
    voiceover += _speech_like_signal(12.0, 1.0, 4.5, amplitude=0.6)
    processor = AudioProcessor(AudioProcessingConfig())
    ducked, metadata = processor.duck_music(music, voiceover, duck_db=duck_db, attack_ms=60.0, release_ms=220.0)
    assert abs(metadata["measured_duck_db"] - duck_db) <= 3.5
    assert metadata["ducking_status"] == "PASS"
    assert np.max(np.abs(ducked)) <= 1.0


def test_no_speech_leaves_music_essentially_unchanged():
    music = _music_like_signal(8.0, amplitude=0.2)
    voiceover = np.zeros_like(music)
    processor = AudioProcessor(AudioProcessingConfig())
    ducked, metadata = processor.duck_music(music, voiceover, duck_db=12.0)
    assert metadata["speech_regions"] == []
    assert abs(float(np.mean(ducked - music))) < 1e-5
    assert metadata["ducking_status"] == "NO_SPEECH"


def test_ducking_api_job_creates_ducked_mix(client, tmp_path):
    music = _music_like_signal(12.0, amplitude=0.18)
    voiceover = np.zeros_like(music)
    voiceover += _speech_like_signal(12.0, 0.5, 4.0, amplitude=0.6)
    generation = client.post("/api/v1/music/generate", json={"specification": {
        "genre": "ambient",
        "mood": ["calm"],
        "tempo": 72,
        "key": "C major",
        "instruments": ["piano", "soft synth", "bass"],
        "sections": [{"start": 0, "end": 12, "mood": "calm"}],
    }})
    job_id = generation.json()["job_id"]

    import time
    for _ in range(200):
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] == "succeeded":
            break
        time.sleep(0.02)

    music_path = str(client.app.state.job_worker.storage.job_dir(job_id) / "mix.wav")
    voiceover_path = tmp_path / "voiceover_for_duck_test.wav"
    sf.write(voiceover_path, voiceover.astype(np.float32), SR, subtype="PCM_16")

    response = client.post(f"/api/v1/music/jobs/{job_id}/duck", json={
        "voiceover_path": str(voiceover_path),
        "music_path": music_path,
        "duck_db": 12.0,
        "attack_ms": 50.0,
        "release_ms": 300.0,
    })
    assert response.status_code == 202
    duck_job_id = response.json()["job_id"]

    for _ in range(200):
        body = client.get(f"/api/v1/jobs/{duck_job_id}").json()
        if body["status"] == "succeeded":
            break
        time.sleep(0.02)

    assert body["status"] == "succeeded"
    assert body["result"]["ducking"]["ducking_status"] in {"PASS", "NO_SPEECH"}
    assert (client.app.state.job_worker.storage.job_dir(job_id) / "mix_ducked.wav").exists()


def test_ducked_output_contains_music_only_not_original_voiceover(tmp_path: Path):
    music = _music_like_signal(12.0, amplitude=0.18)
    voiceover = np.zeros_like(music)
    voiceover += _speech_like_signal(12.0, 0.5, 4.0, amplitude=0.8)

    processor = AudioProcessor(AudioProcessingConfig())
    ducked, _ = processor.duck_music(music, voiceover, duck_db=12.0, attack_ms=50.0, release_ms=300.0)

    music_path = tmp_path / "mix.wav"
    voiceover_path = tmp_path / "voiceover.wav"
    output_path = tmp_path / "mix_ducked.wav"
    sf.write(music_path, music.astype(np.float32), SR, subtype="PCM_16")
    sf.write(voiceover_path, voiceover.astype(np.float32), SR, subtype="PCM_16")

    result = processor.process_ducked_audio(music_path, voiceover_path, output_path, duck_db=12.0, attack_ms=50.0, release_ms=300.0)
    assert result["ducking_status"] in {"PASS", "NO_SPEECH"}

    final_audio, final_sr = sf.read(output_path, dtype="float32")
    assert final_sr == SR
    assert abs(float(len(final_audio) / SR) - 12.0) < 1e-3

    serialized_music, _ = sf.read(music_path,dtype="float32")
    serialized_voice, _ = sf.read(voiceover_path,dtype="float32")
    expected, _ = processor.duck_music(serialized_music,serialized_voice,duck_db=12.0,attack_ms=50.0,release_ms=300.0)
    np.testing.assert_allclose(final_audio,expected,atol=1.0/32768)
    assert not result["ducking"]["contains_narration"]


def test_ducked_mix_download_route_uses_parent_job_directory(client, tmp_path):
    music = _music_like_signal(12.0, amplitude=0.18)
    voiceover = np.zeros_like(music)
    voiceover += _speech_like_signal(12.0, 0.5, 4.0, amplitude=0.6)
    generation = client.post("/api/v1/music/generate", json={"specification": {
        "genre": "ambient",
        "mood": ["calm"],
        "tempo": 72,
        "key": "C major",
        "instruments": ["piano", "soft synth", "bass"],
        "sections": [{"start": 0, "end": 12, "mood": "calm"}],
    }})
    job_id = generation.json()["job_id"]

    import time
    for _ in range(200):
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] == "succeeded":
            break
        time.sleep(0.02)

    music_path = str(client.app.state.job_worker.storage.job_dir(job_id) / "mix.wav")
    voiceover_path = tmp_path / "voiceover_for_duck_download_test.wav"
    sf.write(voiceover_path, voiceover.astype(np.float32), SR, subtype="PCM_16")

    response = client.post(f"/api/v1/music/jobs/{job_id}/duck", json={
        "voiceover_path": str(voiceover_path),
        "music_path": music_path,
        "duck_db": 12.0,
        "attack_ms": 50.0,
        "release_ms": 300.0,
    })
    duck_job_id = response.json()["job_id"]

    for _ in range(200):
        body = client.get(f"/api/v1/jobs/{duck_job_id}").json()
        if body["status"] == "succeeded":
            break
        time.sleep(0.02)

    download = client.get(f"/api/v1/music/jobs/{duck_job_id}/duck")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("audio/wav")
    assert download.content.startswith(b"RIFF")
