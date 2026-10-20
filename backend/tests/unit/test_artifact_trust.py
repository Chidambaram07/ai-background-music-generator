"""Adversarial tests: only mutations of rendered samples may change evidence."""
import json

import numpy as np
import pytest
import soundfile as sf

from app.schemas.specification import MusicSpecification
from app.services.audio_processing import AudioProcessingConfig, AudioProcessor
from app.validation.quality_checks import clipping_check, fades_check, reconstruction_error_db, true_peak_check
from app.validation.validator import ValidationEngine


SR = 48000
STEMS = ["drums", "bass", "piano", "pads"]


@pytest.fixture
def rendered(tmp_path):
    processor = AudioProcessor()
    spec = MusicSpecification.model_validate({
        "genre": "cinematic", "mood": ["calm"], "tempo": 90, "key": "C major",
        "instruments": ["Drums", "Bass", "Piano", "Pads"],
        "sections": [{"start": 0, "end": 3, "mood": "calm"}],
    })
    t = np.arange(3 * SR, dtype=np.float64) / SR
    for index, stem in enumerate(STEMS, 1):
        sf.write(tmp_path / f"{stem}.wav", 0.1 * np.sin(2 * np.pi * 110 * index * t), SR, subtype="PCM_16")
    processor.process_job(tmp_path, 3.0, STEMS)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    result = {"stems": [{"stem_id": stem} for stem in STEMS]}
    return tmp_path, spec, result, processor


def validate(rendered, result=None, processor=None):
    path, spec, original, default = rendered
    return ValidationEngine(processor or default).validate_job(
        path, spec, original if result is None else result, require_manifest_file=True)


def test_manifest_cached_measurements_and_stem_list_are_not_evidence(rendered):
    before = validate(rendered)
    assert before["valid"] is True, before
    path = rendered[0]
    lies = {"duration_s": 1, "sample_rate": 1, "channels": 99, "valid": True,
            "audio_validation": {"overall_status": "PASS", "measured_lufs": -16}}
    (path / "manifest.json").write_text(json.dumps(lies), encoding="utf-8")
    after = validate(rendered, {**lies, "stems": [], "validation_result": {"valid": True}})
    assert before["checks"] == after["checks"]
    assert after["checks"]["stems"]["actual_count"] == 4


def test_renderer_config_cannot_redefine_acceptance(rendered):
    permissive = AudioProcessor(AudioProcessingConfig(
        target_sample_rate=44100, target_lufs=-30, loudness_tolerance_lufs=100,
        true_peak_limit_dbtp=10, clipping_threshold=2, true_peak_oversample=1,
        duration_tolerance_seconds=100, fade_in_ms=150, fade_out_ms=150))
    assert validate(rendered)["checks"] == validate(rendered, processor=permissive)["checks"]
    clipped = np.ones(SR, dtype=np.float32)
    assert clipping_check(clipped, permissive)["pass"] is False
    assert true_peak_check(clipped, permissive)["pass"] is False
    assert fades_check(clipped, SR, permissive)["pass"] is False


def test_duration_is_measured_and_existing_50ms_limit_is_enforced(rendered):
    path = rendered[0] / "mix.wav"
    audio, sr = sf.read(path, dtype="float32")
    sf.write(path, audio[:-int(0.075 * sr)], sr, subtype="PCM_16")
    report = validate(rendered, {"duration_s": 3, "audio_validation": {"duration_status": "PASS"}})
    assert report["checks"]["duration"]["actual_s"] == 2.925
    assert report["checks"]["duration"]["pass"] is False


def test_all_stems_short_by_one_sample_do_not_align_to_mix(rendered):
    for stem in STEMS:
        path = rendered[0] / f"{stem}.wav"
        audio, sr = sf.read(path, dtype="float32")
        sf.write(path, audio[:-1], sr, subtype="PCM_16")
    report = validate(rendered)
    assert report["checks"]["stems"]["sample_alignment"]["aligned"] is False
    assert report["checks"]["stem_reconstruction"]["pass"] is False


def test_reconstruction_cannot_hide_extra_samples_by_truncation():
    assert reconstruction_error_db(np.ones(100), np.ones(101))["pass"] is False
    assert reconstruction_error_db(np.ones((100, 2)), np.ones(100))["pass"] is False


@pytest.mark.parametrize("filename", ["piano.wav", "mix.wav", "mix_looped.wav", "mix_ducked.wav", "manifest.json"])
def test_existing_but_unreadable_output_fails_closed(rendered, filename):
    (rendered[0] / filename).write_bytes(b"not a readable artifact")
    report = validate(rendered)
    assert report["valid"] is False
    assert filename in report["checks"]["required_outputs"]["unreadable_files"]


def test_loop_remeasures_seam_and_rejects_out_of_bounds_locators(rendered):
    path, _, result, processor = rendered
    loop = processor.process_looped_audio(path / "mix.wav", path / "mix_looped.wav", bpm=90)["loop"]
    result = {**result, "loop": {**loop, "status": "FAIL", "boundary_discontinuity": 99,
                                "start_aligned_to_bar": False, "end_aligned_to_bar": False}}
    report = validate(rendered, result)
    assert report["checks"]["looping"]["pass"] is True
    assert report["checks"]["looping"]["boundary_discontinuity"] < 0.25
    bad = {**result, "loop": {**loop, "loop_start_s": 100, "loop_end_s": 200}}
    assert validate(rendered, bad)["checks"]["looping"]["pass"] is False
    audio, sr = sf.read(path / "mix_looped.wav", dtype="float32")
    audio[0] = -0.7
    audio[int(round(loop["loop_end_s"] * sr)) - 1] = 0.7
    sf.write(path / "mix_looped.wav", audio, sr, subtype="PCM_16")
    assert validate(rendered, {**result, "loop": loop})["checks"]["looping"]["pass"] is False


def test_ducking_remeasures_actual_three_files_not_stored_attenuation(rendered):
    path, _, result, processor = rendered
    voice = np.zeros(3 * SR, dtype=np.float32)
    t = np.arange(int(0.7 * SR), dtype=np.float64) / SR
    voice[int(0.5 * SR):int(1.2 * SR)] = 0.2 * np.sin(2 * np.pi * 180 * t)
    voice_path = path.parent / f"{path.name}-voiceover.wav"
    sf.write(voice_path, voice, SR, subtype="PCM_16")
    processor.process_ducked_audio(path / "mix.wav", voice_path, path / "mix_ducked.wav")
    data = {**result, "ducking": {"voiceover_path": str(voice_path), "measured_duck_db": 99, "ducking_status": "FAIL"}}
    measured = validate(rendered, data)["checks"]["ducking"]
    assert measured["pass"] is True, measured
    assert 8 <= measured["ducking_db"] <= 15
    original, sr = sf.read(path / "mix.wav", dtype="float32")
    voice_samples, _ = sf.read(voice_path, dtype="float32")
    sf.write(path / "mix_ducked.wav", original, sr, subtype="PCM_16")
    data["ducking"].update(measured_duck_db=12, ducking_status="PASS")
    failed = validate(rendered, data)["checks"]["ducking"]
    assert failed["pass"] is False
    assert abs(failed["ducking_db"]) < 0.01


def test_silent_and_nonfinite_artifacts_never_pass(rendered):
    path = rendered[0] / "mix.wav"
    sf.write(path, np.zeros(3 * SR), SR, subtype="PCM_16")
    report = validate(rendered)
    assert report["valid"] is False
    json.dumps(report, allow_nan=False)
    sf.write(path, np.full(3 * SR, np.nan), SR, subtype="FLOAT")
    assert validate(rendered)["valid"] is False