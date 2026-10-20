import numpy as np

from app.validation.quality_checks import duration_check


def test_duration_validation_passes_for_exact_45_seconds():
    audio = np.zeros(48000 * 45, dtype=np.float32)
    result = duration_check(audio, 48000, 45.0, 0.1)
    assert result["requested_s"] == 45.0
    assert result["actual_s"] == 45.0
    assert result["error_s"] == 0.0
    assert result["pass"] is True


def test_duration_validation_fails_for_wrong_duration():
    audio = np.zeros(48000 * 44, dtype=np.float32)
    result = duration_check(audio, 48000, 45.0, 0.1)
    assert result["actual_s"] == 44.0
    assert result["pass"] is False