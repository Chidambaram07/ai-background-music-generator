from app.validation.quality_checks import sample_rate_check


def test_sample_rate_validation_passes_for_48000_hz():
    result = sample_rate_check(48000, 48000)
    assert result["actual_hz"] == 48000
    assert result["pass"] is True


def test_sample_rate_validation_fails_for_44100_hz():
    result = sample_rate_check(44100, 48000)
    assert result["actual_hz"] == 44100
    assert result["pass"] is False