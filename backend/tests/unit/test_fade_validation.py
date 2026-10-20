from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.services.audio_processing import AudioProcessingConfig, AudioProcessor
from app.validation.quality_checks import fades_check, load_audio


def _base_tone(duration_s: float = 1.0, sample_rate: int = 48000, amplitude: float = 0.3) -> np.ndarray:
    t = np.arange(int(duration_s * sample_rate), dtype=np.float32) / sample_rate
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _apply_linear_fades(audio: np.ndarray, sample_rate: int, fade_in_ms: float, fade_out_ms: float) -> np.ndarray:
    result = audio.copy()
    fade_in_samples = max(1, int(round((fade_in_ms / 1000.0) * sample_rate)))
    fade_out_samples = max(1, int(round((fade_out_ms / 1000.0) * sample_rate)))
    result[:fade_in_samples] *= np.linspace(0.0, 1.0, fade_in_samples, endpoint=True, dtype=np.float32)
    result[-fade_out_samples:] *= np.linspace(1.0, 0.0, fade_out_samples, endpoint=True, dtype=np.float32)
    return result


def test_fades_check_passes_for_10ms_fade():
    processor = AudioProcessor(AudioProcessingConfig(fade_in_ms=10.0, fade_out_ms=10.0))
    audio = _apply_linear_fades(_base_tone(), 48000, 10.0, 10.0)
    result = fades_check(audio, 48000, processor)
    assert result["pass"] is True
    assert result["fade_in_pass"] is True
    assert result["fade_out_pass"] is True


def test_fades_check_fails_for_very_long_fade():
    processor = AudioProcessor(AudioProcessingConfig(fade_in_ms=10.0, fade_out_ms=10.0))
    audio = _apply_linear_fades(_base_tone(), 48000, 150.0, 150.0)
    result = fades_check(audio, 48000, processor)
    assert result["pass"] is False
    assert result["fade_in_pass"] is False
    assert result["fade_out_pass"] is False


def test_fades_check_fails_when_no_fade_exists():
    processor = AudioProcessor(AudioProcessingConfig(fade_in_ms=10.0, fade_out_ms=10.0))
    audio = _base_tone()
    result = fades_check(audio, 48000, processor)
    assert result["pass"] is False


def test_zero_edge_samples_do_not_guarantee_fade_pass():
    processor = AudioProcessor(AudioProcessingConfig(fade_in_ms=10.0, fade_out_ms=10.0))
    audio = _base_tone()
    audio[0] = 0.0
    audio[-1] = 0.0
    result = fades_check(audio, 48000, processor)
    assert result["first_sample_abs"] == 0.0
    assert result["last_sample_abs"] == 0.0
    assert result["pass"] is False


def test_natural_decay_is_not_classified_as_short_fade():
    processor = AudioProcessor(AudioProcessingConfig(fade_in_ms=10.0, fade_out_ms=10.0))
    audio = _base_tone()
    tail = np.linspace(1.0, 0.0, 48000 // 3, endpoint=True, dtype=np.float32)
    audio[-tail.size:] *= tail
    audio[0] = 0.0
    result = fades_check(audio, 48000, processor)
    assert result["pass"] is False
    assert result["fade_out_pass"] is False


def test_fades_check_passes_for_process_array_output_with_realistic_10ms_fade():
    processor = AudioProcessor(AudioProcessingConfig(fade_in_ms=10.0, fade_out_ms=10.0))
    t = np.arange(int(45.0 * 48000), dtype=np.float32) / 48000
    audio = (0.28 * np.sin(2 * np.pi * 220 * t) + 0.18 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
    processed, _ = processor.process_array(audio, 48000, 45.0, normalize_loudness=True)
    result = fades_check(processed, 48000, processor)
    assert result["pass"] is True
    assert result["fade_in_pass"] is True
    assert result["fade_out_pass"] is True


def test_fades_check_passes_for_the_real_generated_mix_artifact():
    processor = AudioProcessor(AudioProcessingConfig(fade_in_ms=10.0, fade_out_ms=10.0))
    artifact_path = Path(__file__).resolve().parents[2] / "audio/generated/6198c92e-a799-4949-a6fc-913c655dadcb/mix.wav"
    audio, sample_rate, _ = load_audio(artifact_path)
    result = fades_check(audio, sample_rate, processor)
    assert result["pass"] is True
    assert result["fade_in_pass"] is True
    assert result["fade_out_pass"] is True


def test_real_tail_decay_is_not_counted_as_mix_fade():
    job_dir = Path(__file__).resolve().parents[2] / "audio/generated/d6ae2e5c-08da-4bef-a680-875ba9f963e5"
    audio, sample_rate, _ = load_audio(job_dir / "mix.wav")
    reference = sum(load_audio(path)[0].astype(np.float64) for path in job_dir.glob("*.wav") if not path.stem.startswith("mix"))
    result = fades_check(audio, sample_rate, AudioProcessor(), reference_audio=reference)
    assert result["pass"] is True, result
    assert abs(result["fade_in_ms"] - 10.0) < 0.25
    assert abs(result["fade_out_ms"] - 10.0) < 0.25


@pytest.mark.parametrize("fade_ms", [0.0, 0.5, 1.0, 10.0, 30.0, 150.0, 333.0])
def test_pcm_fit_measures_ramp_instead_of_copying_expected_value(fade_ms):
    sample_rate = 48000
    t = np.arange(sample_rate, dtype=np.float64) / sample_rate
    # Deliberately nonstationary source: its own decay must cancel in the fit.
    reference = (np.sin(2 * np.pi * 173 * t) + 0.4 * np.sin(2 * np.pi * 337 * t)) * (1.1 - t)
    audio = reference.copy()
    if fade_ms:
        count = int(round(fade_ms * sample_rate / 1000))
        audio[:count] *= np.linspace(0, 1, count) ** 2
        audio[-count:] *= np.linspace(1, 0, count) ** 2
    audio[0] = audio[-1] = 0.0
    result = fades_check(audio, sample_rate, AudioProcessor(), reference_audio=reference)
    assert result["pass"] is (fade_ms == 10.0), result
    if 0 < fade_ms < 200:
        assert abs(result["fade_in_ms"] - fade_ms) < 0.25
        assert abs(result["fade_out_ms"] - fade_ms) < 0.25


def test_mismatched_reference_cannot_manufacture_fade_evidence():
    audio = _apply_linear_fades(_base_tone(), 48000, 10, 10)
    unrelated = np.random.default_rng(8).normal(0, 0.1, len(audio))
    assert fades_check(audio, 48000, AudioProcessor(), reference_audio=unrelated)["pass"] is False


def test_silent_boundaries_do_not_make_a_reference_fade_observable():
    reference = _base_tone()
    reference[:1440] = reference[-1440:] = 0.0
    audio = reference * 0.4
    result = fades_check(audio, 48000, AudioProcessor(), reference_audio=reference)
    assert result["pass"] is False


def test_pcm_fit_is_gain_invariant_and_handles_antiphase_pcm16(tmp_path):
    reference = _base_tone().astype(np.float64)
    audio = _apply_linear_fades(reference, 48000, 10, 10) * 0.37
    stereo = np.column_stack((audio, -audio))
    path = tmp_path / "stereo.wav"
    sf.write(path, stereo, 48000, subtype="PCM_16")
    serialized, sr, _ = load_audio(path)
    result = fades_check(serialized, sr, AudioProcessor(), reference_audio=np.column_stack((reference, -reference)))
    assert result["pass"] is True, result


def test_quiet_ambient_boundary_uses_interior_reference_not_ramp_rms(tmp_path):
    from app.schemas.specification import MusicSpecification
    from app.services.music_generation import MusicGenerationEngine
    from app.validation.validator import ValidationEngine

    spec = MusicSpecification.model_validate({
        "genre": "ambient", "mood": ["calm"], "tempo": 72, "key": "C major",
        "instruments": ["piano", "soft synth"],
        "sections": [{"start": 0, "end": 12, "mood": "calm"}],
    })
    engine = MusicGenerationEngine(str(tmp_path))
    result = engine.generate(spec, "quiet-fade")
    job_dir = tmp_path / "quiet-fade"
    processor = AudioProcessor()
    processor.process_job(job_dir, 12.0, [stem["stem_id"] for stem in result["stems"]])
    report = ValidationEngine(processor).validate_job(job_dir, spec, result, require_manifest_file=False)
    assert report["checks"]["fades"]["pass"] is True, report["checks"]["fades"]