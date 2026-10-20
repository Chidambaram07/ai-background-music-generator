from pathlib import Path

import numpy as np
import soundfile as sf

from app.services.audio_processing import AudioProcessingConfig, AudioProcessor
from app.validation.quality_checks import clipping_check


def test_clipping_validation_fails_for_fixture():
    processor = AudioProcessor(AudioProcessingConfig())
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "clipped.wav"
    audio, sample_rate = sf.read(fixture, dtype="float32")
    result = clipping_check(audio, processor)
    assert sample_rate == 48000
    assert result["clipped_samples"] > 0
    assert result["pass"] is False


def test_clipping_validation_passes_for_normal_audio():
    processor = AudioProcessor(AudioProcessingConfig())
    t = np.arange(48000, dtype=np.float32) / 48000
    audio = (0.2 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
    result = clipping_check(audio, processor)
    assert result["clipped_samples"] == 0
    assert result["pass"] is True