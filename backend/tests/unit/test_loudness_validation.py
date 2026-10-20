import numpy as np

from app.services.audio_processing import AudioProcessingConfig, AudioProcessor
from app.validation.quality_checks import loudness_check


def _tone(duration_s: float, sample_rate: int = 48000, amplitude: float = 0.2) -> np.ndarray:
    t = np.arange(int(duration_s * sample_rate), dtype=np.float32) / sample_rate
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_loudness_validation_passes_for_normalized_audio():
    processor = AudioProcessor(AudioProcessingConfig())
    processed, _ = processor.process_array(_tone(3.0, amplitude=0.05), 48000, 3.0, normalize_loudness=True)
    result = loudness_check(processed, 48000, processor)
    assert result["pass"] is True


def test_loudness_validation_fails_outside_tolerance():
    processor = AudioProcessor(AudioProcessingConfig())
    result = loudness_check(_tone(3.0, amplitude=0.005), 48000, processor)
    assert result["pass"] is False