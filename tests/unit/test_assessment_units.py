import json

import numpy as np
import pyloudnorm as pyln
import pytest
import soundfile as sf
from pydantic import ValidationError

from app.core.exceptions import IdempotencyConflictError, InvalidStateTransitionError
from app.models.job import JobStatus
from app.schemas.music import MusicJobCreate
from app.services.audio_processing import AudioProcessor
from app.services.job_service import JobService
from app.services.storage import ArtifactStorage
from app.utils.idempotency import request_hash
from app.validation.quality_checks import clipping_check, duration_check, loudness_check, reconstruction_error_db, sample_rate_check, true_peak_check


@pytest.mark.parametrize("payload", [
    {"prompt":"", "duration_s":5}, {"prompt":"x", "duration_s":0},
    {"prompt":"x", "duration_s":-1}, {"prompt":"x", "duration_s":float("nan")},
    {"prompt":"x", "duration_s":5, "output":{"sample_rate":44100}},
    {"prompt":"x", "duration_s":5, "output":{"formats":["mp3"]}},
    {"prompt":"x", "duration_s":5, "narration_url":"file:///private.wav"},
])
def test_request_validation(payload):
    with pytest.raises(ValidationError):
        MusicJobCreate.model_validate(payload)


@pytest.mark.parametrize("sections", [
    [{"start":-1,"end":5,"mood":"calm"}],
    [{"start":6,"end":7,"mood":"calm"}],
    [{"start":0,"end":3,"mood":"calm"},{"start":2,"end":5,"mood":"hopeful"}],
    [{"start":0,"end":1,"mood":"calm"},{"start":1,"end":5,"mood":"calm"}],
    [{"start":"bad","end":5,"mood":"calm"}],
])
def test_section_validation(sections):
    with pytest.raises(ValidationError):
        MusicJobCreate(prompt="x", duration_s=5, sections=sections)


def test_job_transitions_and_idempotency(tmp_path):
    service = JobService(str(tmp_path / "jobs.db"))
    payload = {"prompt":"x", "duration_s":5}
    first = service.create_job("x", 5, "same", request_hash(payload))
    assert service.create_job("x", 5, "same", request_hash(payload)).job_id == first.job_id
    with pytest.raises(IdempotencyConflictError):
        service.create_job("other", 5, "same", request_hash({"prompt":"other"}))
    service.transition(first.job_id, JobStatus.PROCESSING)
    service.transition(first.job_id, JobStatus.FAILED, error_code="TEST_FAILURE", error_message="Explicit failure")
    with pytest.raises(InvalidStateTransitionError):
        service.transition(first.job_id, JobStatus.SUCCEEDED)


def test_actual_wav_duration_rate_and_loudness(audio_fixtures):
    processor = AudioProcessor()
    good = audio_fixtures("valid")
    audio, rate = sf.read(good, dtype="float32")
    normalized = pyln.normalize.loudness(audio, pyln.Meter(rate).integrated_loudness(audio), -16)
    sf.write(good, normalized, rate, subtype="PCM_16")
    audio, rate = sf.read(good, dtype="float32")
    assert duration_check(audio, rate, 2, 0.05)["pass"]
    assert not duration_check(audio, rate, 3, 0.05)["pass"]
    assert sample_rate_check(sf.info(good).samplerate, 48000)["pass"]
    wrong_rate = audio_fixtures("wrong_rate", rate=44100)
    assert not sample_rate_check(sf.info(wrong_rate).samplerate, 48000)["pass"]
    assert loudness_check(audio, rate, processor)["pass"]
    assert not loudness_check(audio * 0.01, rate, processor)["pass"]


def test_real_peak_and_clipping_detection(audio_fixtures):
    processor = AudioProcessor()
    valid, _ = sf.read(audio_fixtures("quiet"), dtype="float32")
    clipped, _ = sf.read(audio_fixtures("clipped", amplitude=1, clipped=True), dtype="float32")
    assert true_peak_check(valid, processor)["pass"]
    assert not true_peak_check(clipped, processor)["pass"]
    assert clipping_check(valid, processor)["pass"]
    assert not clipping_check(clipped, processor)["pass"]


def test_reconstruction_detects_wrong_audio(audio_fixtures):
    audio, _ = sf.read(audio_fixtures("reconstruction"), dtype="float32")
    assert reconstruction_error_db(audio, audio.copy())["pass"]
    assert not reconstruction_error_db(audio, audio * 0.25)["pass"]


def test_ducking_calculation_and_silence():
    processor = AudioProcessor()
    rate = 48000
    times = np.arange(rate * 6) / rate
    music = (0.05 * np.sin(2 * np.pi * 440 * times)).astype(np.float32)
    voice = np.zeros_like(music)
    voice[rate:2 * rate] = 0.2 * np.sin(2 * np.pi * 180 * times[rate:2 * rate])
    ducked, metrics = processor.duck_music(music, voice, duck_db=12)
    assert metrics["speech_regions"]
    interior = slice(int(1.2 * rate), int(1.8 * rate))
    ratio = processor._rms(ducked[interior]) / processor._rms(music[interior])
    assert abs(20 * np.log10(ratio) + 12) < 0.1
    unmodified, silent = processor.duck_music(music, np.zeros_like(voice))
    assert not silent["speech_regions"]
    np.testing.assert_allclose(unmodified, music, atol=1e-6)


def test_loop_calculation_and_manifest_missing_files(tmp_path):
    processor = AudioProcessor()
    audio = (0.1 * np.sin(2 * np.pi * 220 * np.arange(48000 * 6) / 48000)).astype(np.float32)
    looped, validation = processor.generate_looped_mix(audio, bpm=120)
    assert len(looped) == len(audio)
    assert validation["start_aligned_to_bar"] and validation["end_aligned_to_bar"]
    assert validation["boundary_discontinuity"] < 0.25
    storage = ArtifactStorage(tmp_path)
    storage.create_job("missing")
    from app.services.audio_processing import AudioProcessingError
    with pytest.raises(AudioProcessingError) as captured:
        storage.verify_manifest("missing", {"mix":{"file":"mix.wav"}, "stems":[]})
    assert captured.value.code == "ARTIFACT_MISSING"


def test_corrupt_manifest_is_structured_error(tmp_path):
    from app.services.audio_processing import AudioProcessingError
    storage = ArtifactStorage(tmp_path)
    directory = storage.create_job("corrupt-json")
    (directory / "music_manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(AudioProcessingError) as captured:
        storage.read_json("corrupt-json", "music_manifest.json")
    assert captured.value.code == "ARTIFACT_CORRUPT"