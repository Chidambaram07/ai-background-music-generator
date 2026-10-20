from pathlib import Path

import numpy as np
import soundfile as sf

from app.services.audio_processing import AudioProcessingConfig, AudioProcessor
from app.validation.stem_validator import StemValidator


STEMS = ["drums", "bass", "piano", "pads"]


def _write_stem(path: Path, frequency: float, duration_s: float = 1.0, sample_rate: int = 48000):
    t = np.arange(int(duration_s * sample_rate), dtype=np.float32) / sample_rate
    audio = (0.2 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)
    sf.write(path, audio, sample_rate, subtype="PCM_16")


def test_stem_validator_passes_for_valid_stems(tmp_path: Path):
    processor = AudioProcessor(AudioProcessingConfig())
    validator = StemValidator(processor)
    for index, stem in enumerate(STEMS, start=1):
        _write_stem(tmp_path / f"{stem}.wav", 110.0 * index)
    result, legacy = validator.validate(tmp_path, STEMS, 1.0)
    assert result["pass"] is True
    assert result["actual_count"] == 4
    assert all(item["pass"] for item in legacy.values())


def test_stem_validator_fails_for_missing_stem(tmp_path: Path):
    processor = AudioProcessor(AudioProcessingConfig())
    validator = StemValidator(processor)
    for stem in STEMS[:-1]:
        _write_stem(tmp_path / f"{stem}.wav", 220.0)
    result, _ = validator.validate(tmp_path, STEMS, 1.0)
    assert result["pass"] is False
    assert result["missing_stems"] == ["pads"]


def test_stem_validator_fails_for_wrong_stem_duration(tmp_path: Path):
    processor = AudioProcessor(AudioProcessingConfig())
    validator = StemValidator(processor)
    for stem in STEMS:
        _write_stem(tmp_path / f"{stem}.wav", 220.0, duration_s=1.0)
    _write_stem(tmp_path / "pads.wav", 220.0, duration_s=0.5)
    result, legacy = validator.validate(tmp_path, STEMS, 1.0)
    assert result["pass"] is False
    assert legacy["pads"]["pass"] is False