import json
from pathlib import Path

import numpy as np
import soundfile as sf

from app.schemas.specification import MusicSpecification
from app.services.audio_processing import AudioProcessingConfig, AudioProcessor
from app.validation.validator import ValidationEngine


STEMS = ["drums", "bass", "piano", "pads"]


def _spec() -> MusicSpecification:
    return MusicSpecification.model_validate({
        "genre": "cinematic",
        "mood": ["calm"],
        "tempo": 90,
        "key": "C major",
        "instruments": ["Drums", "Bass", "Piano", "Pads"],
        "sections": [{"start": 0, "end": 45, "mood": "calm"}],
    })


def _result() -> dict:
    return {
        "stems": [{"stem_id": stem, "filename": f"{stem}.wav", "name": stem} for stem in STEMS],
        "mix": {"filename": "mix.wav", "name": "mix"},
    }


def _write_valid_job(tmp_path: Path, processor: AudioProcessor, duration_s: float = 45.0) -> None:
    stem_arrays = []
    for index, stem in enumerate(STEMS, start=1):
        t = np.arange(int(duration_s * 48000), dtype=np.float32) / 48000
        audio = (0.12 * np.sin(2 * np.pi * (55.0 * index) * t)).astype(np.float32)
        processed, _ = processor.process_array(audio, 48000, duration_s, normalize_loudness=False)
        sf.write(tmp_path / f"{stem}.wav", processed, 48000, subtype="PCM_16")
        stem_arrays.append(processed)
    mix = processor.mix_stem_arrays(stem_arrays)
    processed_mix, _ = processor.process_array(mix, 48000, duration_s, normalize_loudness=True)
    sf.write(tmp_path / "mix.wav", processed_mix, 48000, subtype="PCM_16")
    (tmp_path / "manifest.json").write_text(json.dumps({"job_id": "job-123"}), encoding="utf-8")


def test_validator_passes_for_valid_independent_stems(tmp_path: Path):
    processor = AudioProcessor(AudioProcessingConfig())
    validator = ValidationEngine(processor)
    _write_valid_job(tmp_path, processor)
    result = validator.validate_job(tmp_path, _spec(), _result(), require_manifest_file=True)
    assert result["valid"] is True
    assert result["checks"]["stem_reconstruction"]["pass"] is True
    assert result["checks"]["required_outputs"]["pass"] is True


def test_validator_fails_when_required_output_is_missing(tmp_path: Path):
    processor = AudioProcessor(AudioProcessingConfig())
    validator = ValidationEngine(processor)
    _write_valid_job(tmp_path, processor)
    (tmp_path / "manifest.json").unlink()
    result = validator.validate_job(tmp_path, _spec(), _result(), require_manifest_file=True)
    assert result["valid"] is False
    assert result["checks"]["required_outputs"]["pass"] is False


def test_validator_fails_when_stem_reconstruction_mismatches(tmp_path: Path):
    processor = AudioProcessor(AudioProcessingConfig())
    validator = ValidationEngine(processor)
    _write_valid_job(tmp_path, processor)
    audio, sample_rate = sf.read(tmp_path / "piano.wav", dtype="float32")
    sf.write(tmp_path / "piano.wav", (audio * 0.1).astype(np.float32), sample_rate, subtype="PCM_16")
    result = validator.validate_job(tmp_path, _spec(), _result(), require_manifest_file=True)
    assert result["valid"] is False
    assert result["checks"]["stem_reconstruction"]["pass"] is False


def test_validator_fails_when_ducking_is_outside_range(tmp_path: Path):
    processor = AudioProcessor(AudioProcessingConfig())
    validator = ValidationEngine(processor)
    _write_valid_job(tmp_path, processor)
    sf.write(tmp_path / "mix_ducked.wav", sf.read(tmp_path / "mix.wav", dtype="float32")[0], 48000, subtype="PCM_16")
    result = validator.validate_job(
        tmp_path,
        _spec(),
        {
            **_result(),
            "ducking": {
                "speech_regions": [{"start_s": 0.0, "end_s": 2.0}],
                "measured_duck_db": 16.0,
                "music_rms_before": 0.2,
                "music_rms_during_speech": 0.15,
                "attack_ms": 50.0,
                "release_ms": 300.0,
                "ducking_status": "FAIL",
            },
        },
        require_manifest_file=True,
    )
    assert result["valid"] is False
    assert result["checks"]["ducking"]["pass"] is False


def test_validator_fails_when_loop_metadata_is_invalid(tmp_path: Path):
    processor = AudioProcessor(AudioProcessingConfig())
    validator = ValidationEngine(processor)
    _write_valid_job(tmp_path, processor)
    sf.write(tmp_path / "mix_looped.wav", sf.read(tmp_path / "mix.wav", dtype="float32")[0], 48000, subtype="PCM_16")
    result = validator.validate_job(
        tmp_path,
        _spec(),
        {
            **_result(),
            "loop": {
                "loop_start_s": 12.0,
                "loop_end_s": 12.0,
                "loop_duration_s": 0.0,
                "start_aligned_to_bar": False,
                "end_aligned_to_bar": False,
                "boundary_discontinuity": 0.5,
                "status": "FAIL",
            },
        },
        require_manifest_file=True,
    )
    assert result["valid"] is False
    assert result["checks"]["looping"]["pass"] is False