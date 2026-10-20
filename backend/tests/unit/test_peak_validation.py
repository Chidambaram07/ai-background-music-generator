import numpy as np

from app.services.audio_processing import AudioProcessingConfig, AudioProcessor
from app.validation.quality_checks import true_peak_check


def _tone(duration_s: float, sample_rate: int = 48000, amplitude: float = 0.2) -> np.ndarray:
    t = np.arange(int(duration_s * sample_rate), dtype=np.float32) / sample_rate
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def test_true_peak_validation_passes_below_limit():
    processor = AudioProcessor(AudioProcessingConfig())
    result = true_peak_check(_tone(1.0, amplitude=0.2), processor)
    assert result["pass"] is True


def test_true_peak_validation_fails_above_limit():
    processor = AudioProcessor(AudioProcessingConfig())
    result = true_peak_check(_tone(1.0, amplitude=0.99), processor)
    assert result["pass"] is False