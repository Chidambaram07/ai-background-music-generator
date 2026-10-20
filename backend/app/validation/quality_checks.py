from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.optimize import least_squares

from app.services.audio_processing import AudioProcessor
from app.validation.policy import MODULE8_CONFIG


def load_audio(path: Path) -> tuple[np.ndarray, int, sf._SoundFileInfo]:
    audio, sample_rate = sf.read(path, dtype="float32")
    if not np.isfinite(audio).all():
        raise ValueError(f"{path.name} contains non-finite PCM samples.")
    return np.asarray(audio, dtype=np.float32), int(sample_rate), sf.info(path)


def duration_check(audio: np.ndarray, sample_rate: int, requested_duration: float, tolerance_seconds: float) -> dict:
    actual_duration = float(len(audio) / sample_rate) if sample_rate > 0 else 0.0
    error = abs(float(requested_duration) - actual_duration)
    return {
        "source": "actual_audio_file",
        "requested_s": round(float(requested_duration), 6),
        "actual_s": round(actual_duration, 6),
        "error_s": round(error, 6),
        "tolerance_s": round(float(tolerance_seconds), 6),
        "pass": bool(audio.size and sample_rate > 0 and np.isfinite(error) and error <= tolerance_seconds),
    }


def sample_rate_check(actual_sample_rate: int, expected_sample_rate: int) -> dict:
    return {
        "source": "actual_audio_file",
        "expected_hz": int(expected_sample_rate),
        "actual_hz": int(actual_sample_rate),
        "pass": int(actual_sample_rate) == int(expected_sample_rate),
    }


def format_check(info: sf._SoundFileInfo) -> dict:
    subtype = str(info.subtype or "")
    bit_depth = {"PCM_U8": 8, "PCM_S8": 8, "PCM_16": 16, "PCM_24": 24,
                 "PCM_32": 32, "FLOAT": 32, "DOUBLE": 64}.get(subtype)
    return {
        "source": "actual_audio_file",
        "container": str(info.format or ""),
        "encoding": subtype.split("_")[0] if subtype else "",
        "subtype": subtype,
        "bit_depth": bit_depth,
        "channels": int(info.channels),
        "pass": str(info.format or "").upper() == "WAV" and subtype == "PCM_16" and info.channels in (1, 2),
    }


def loudness_check(audio: np.ndarray, sample_rate: int, processor: AudioProcessor) -> dict:
    # Meter the actual samples at their actual header rate; do not repair them.
    measured = float(pyln.Meter(sample_rate).integrated_loudness(audio))
    if not np.isfinite(measured):
        raise ValueError("Rendered audio has no finite integrated loudness (silent or invalid audio).")
    difference = abs(measured - MODULE8_CONFIG.target_lufs)
    return {
        "source": "actual_audio_file",
        "target_lufs": MODULE8_CONFIG.target_lufs,
        "measured_lufs": round(measured, 3),
        "difference_lufs": round(difference, 3),
        "tolerance_lufs": MODULE8_CONFIG.loudness_tolerance_lufs,
        "pass": bool(np.isfinite(measured) and difference <= MODULE8_CONFIG.loudness_tolerance_lufs),
    }


def true_peak_check(audio: np.ndarray, processor: AudioProcessor) -> dict:
    measured = float(AudioProcessor(MODULE8_CONFIG)._true_peak_dbtp(audio))
    return {
        "source": "actual_audio_file",
        "limit_dbtp": MODULE8_CONFIG.true_peak_limit_dbtp,
        "measured_dbtp": round(measured, 3),
        "pass": bool(audio.size and np.isfinite(measured) and measured <= MODULE8_CONFIG.true_peak_limit_dbtp + 1e-6),
    }


def clipping_check(audio: np.ndarray, processor: AudioProcessor) -> dict:
    threshold = MODULE8_CONFIG.clipping_threshold
    clipped_samples = int(np.count_nonzero(np.abs(audio) >= threshold))
    return {
        "source": "actual_audio_file",
        "threshold": threshold,
        "clipped_samples": clipped_samples,
        "pass": bool(audio.size and np.isfinite(audio).all() and clipped_samples == 0),
    }


def _smooth_envelope(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    # Rectify before combining channels so anti-phase stereo cannot cancel.
    envelope = np.mean(np.abs(audio), axis=1, dtype=np.float32) if audio.ndim == 2 else np.abs(audio)
    kernel_size = max(5, int(round(sample_rate * 0.0015)))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = np.ones(kernel_size, dtype=np.float32) / kernel_size
    return np.convolve(envelope, kernel, mode="same") if envelope.size >= kernel_size else envelope


def _measure_fade_ms(
    audio: np.ndarray,
    sample_rate: int,
    *,
    expected_ms: float,
    from_start: bool,
) -> tuple[float, float, float, bool, float]:
    if audio.size == 0 or sample_rate <= 0:
        return 0.0, 0.0, 0.0, False, 1.0

    envelope = _smooth_envelope(audio, sample_rate)
    analysis_window_ms = max(80.0, min(600.0, expected_ms * 20.0))
    analysis_samples = min(len(envelope), max(64, int(round((analysis_window_ms / 1000.0) * sample_rate))))
    analysis = envelope[:analysis_samples] if from_start else envelope[-analysis_samples:]
    if analysis.size < 16:
        return 0.0, 0.0, 0.0, False, 1.0

    edge_samples = max(32, min(analysis.size, int(round((max(expected_ms * 3.0, 40.0) / 1000.0) * sample_rate))))
    local = analysis[:edge_samples] if from_start else analysis[-edge_samples:]
    if local.size < 16:
        return 0.0, 0.0, 0.0, False, 1.0

    plateau_window = max(16, int(round((max(expected_ms * 2.0, 20.0) / 1000.0) * sample_rate)))
    if from_start:
        start_level = float(np.median(local[:max(4, min(local.size // 8, 32))]))
        plateau_chunk = local[plateau_window:] if local.size > plateau_window else local[max(1, local.size // 3):]
        plateau = float(np.median(plateau_chunk)) if plateau_chunk.size else float(np.max(local))
        if plateau <= 1e-9:
            return 0.0, 0.0, start_level, False, 1.0
        if start_level >= 0.25 * plateau:
            return 0.0, plateau, start_level, False, 1.0
        threshold = 0.9 * plateau
        crossing = next((idx for idx, value in enumerate(local) if value >= threshold), None)
        if crossing is None:
            crossing = local.size - 1
        ramp = local[: crossing + 1]
        if ramp.size < 2:
            return 0.0, plateau, start_level, False, 1.0
        ideal = np.linspace(0.0, 1.0, ramp.size, endpoint=True, dtype=np.float32)
        normalized = np.clip(ramp / max(float(np.max(ramp)), 1e-6), 0.0, 1.0)
        score = float(np.mean(np.abs(normalized - ideal)))
        measured_ms = (crossing / sample_rate) * 1000.0
        rising = bool(np.all(np.diff(ramp) >= -0.01) and np.max(ramp) > 0.0)
        return measured_ms, plateau, start_level, rising, score

    end_level = float(np.median(local[-max(4, min(local.size // 8, 32)):]))
    plateau_chunk = local[:-plateau_window] if local.size > plateau_window else local[: max(1, local.size // 2)]
    plateau = float(np.median(plateau_chunk)) if plateau_chunk.size else float(np.max(local))
    if plateau <= 1e-9:
        return 0.0, 0.0, end_level, False, 1.0
    if end_level >= 0.25 * plateau:
        return 0.0, plateau, end_level, False, 1.0
    threshold = 0.9 * plateau
    reversed_local = local[::-1]
    crossing = next((idx for idx, value in enumerate(reversed_local) if value >= threshold), None)
    if crossing is None:
        crossing = reversed_local.size - 1
    ramp = reversed_local[: crossing + 1]
    if ramp.size < 2:
        return 0.0, plateau, end_level, False, 1.0
    # The tail is reversed above: both boundary ramps now rise from zero.
    ideal = np.linspace(0.0, 1.0, ramp.size, endpoint=True, dtype=np.float32)
    normalized = np.clip(ramp / max(float(np.max(ramp)), 1e-6), 0.0, 1.0)
    score = float(np.mean(np.abs(normalized - ideal)))
    measured_ms = (crossing / sample_rate) * 1000.0
    falling = bool(np.all(np.diff(ramp) >= -0.01) and np.max(ramp) > 0.0)
    return measured_ms, plateau, end_level, falling, score


def _measure_reference_fade_ms(
    audio: np.ndarray, reference: np.ndarray, sample_rate: int, *, from_start: bool,
) -> tuple[float, float, float, bool, float]:
    """Measure boundary attenuation against actual, unmastered stem-sum PCM.

    A musical tail need not have a flat envelope. Fit y = gain * x * ramp**p
    over the final/initial 200ms instead; duration, exponent and mastering gain
    are inferred from samples. No requested/configured fade enters the fit.
    """
    if audio.shape != reference.shape or not audio.size or sample_rate <= 0:
        return 0.0, 0.0, 0.0, False, 1.0
    count = min(len(audio) // 2, int(round(0.2 * sample_rate)))
    if count < 16 or not np.isfinite(reference).all():
        return 0.0, 0.0, 0.0, False, 1.0
    x = np.asarray(reference[:count] if from_start else reference[-count:][::-1], dtype=np.float64)
    y = np.asarray(audio[:count] if from_start else audio[-count:][::-1], dtype=np.float64)
    level = float(np.sqrt(np.mean(y * y)))
    if level <= 1e-7 or np.max(np.abs(x)) <= 1e-7:
        return 0.0, 0.0, 0.0, False, 1.0
    distance = np.arange(count, dtype=np.float64)
    if y.ndim == 2:
        distance = distance[:, None]

    def residual(parameters):
        duration_ms, exponent = parameters
        ramp = np.minimum(distance / max(duration_ms * sample_rate / 1000.0 - 1.0, 1.0), 1.0)
        model = x * ramp ** exponent
        energy = float(np.sum(model * model))
        gain = float(np.sum(model * y)) / max(energy, 1e-30)
        return ((gain * model - y) / level).ravel()

    # Start on both short and long ramps, never at an expected fade value.
    candidates = [least_squares(residual, [ms, 1.5],
                                bounds=([2000.0 / sample_rate, 0.25], [200.0, 8.0]),
                                max_nfev=60)
                  for ms in (4.0, 25.0, 100.0, 190.0)]
    fitted = min(candidates, key=lambda fit: float(np.sum(fit.fun ** 2)))
    measured_ms = float(fitted.x[0])
    score = float(np.sqrt(np.mean(fitted.fun ** 2)))
    ramp = np.minimum(distance / max(measured_ms * sample_rate / 1000.0 - 1.0, 1.0), 1.0) ** fitted.x[1]
    model = x * ramp
    gain = float(np.sum(model * y)) / max(float(np.sum(model * model)), 1e-30)
    fade_samples = min(count, max(1, int(round(measured_ms * sample_rate / 1000.0))))
    # As in the envelope estimator, the reference is the interior level,
    # not the already attenuated ramp's RMS (which can be very quiet).
    reference_level = abs(gain) * float(np.sqrt(np.mean(x[count // 2:] ** 2)))
    edge_level = float(np.sqrt(np.mean(y[:min(32, count)] ** 2)))
    scalar_gain = float(np.sum(x * y)) / max(float(np.sum(x * x)), 1e-30)
    # Test observability locally. A 10ms ramp in a quiet intro must not be
    # diluted by the other 190ms of much louder music in the fit window.
    local_error = float(np.sqrt(np.mean((y[:fade_samples] - gain * model[:fade_samples]) ** 2)))
    no_fade_error = float(np.sqrt(np.mean((y[:fade_samples] - scalar_gain * x[:fade_samples]) ** 2)))
    # Require a -40dB waveform-model match, as for stem reconstruction. A
    # successful optimizer or a ramp fitted entirely inside silence is not
    # evidence: the fade must explain attenuation that a scalar gain cannot.
    valid = bool(fitted.success and gain > 0.0 and score <= 0.01
                 and no_fade_error > max(2.0 * local_error, 1.0 / 32768.0))
    return measured_ms, reference_level, edge_level, valid, score


def fades_check(audio: np.ndarray, sample_rate: int, processor: AudioProcessor, *, reference_audio: np.ndarray | None = None) -> dict:
    expected_fade_in_ms = MODULE8_CONFIG.fade_in_ms
    expected_fade_out_ms = MODULE8_CONFIG.fade_out_ms
    def measure(from_start: bool):
        if reference_audio is not None:
            return _measure_reference_fade_ms(audio, reference_audio, sample_rate, from_start=from_start)
        return _measure_fade_ms(audio, sample_rate, expected_ms=expected_fade_in_ms if from_start else expected_fade_out_ms,
                                from_start=from_start)

    measured_fade_in_ms, fade_in_reference, first_env, fade_in_rising, fade_in_score = measure(True)
    measured_fade_out_ms, fade_out_reference, last_env, fade_out_rising, fade_out_score = measure(False)
    fade_in_samples = max(1, int(round((max(measured_fade_in_ms, 0.0) / 1000.0) * sample_rate)))
    fade_out_samples = max(1, int(round((max(measured_fade_out_ms, 0.0) / 1000.0) * sample_rate)))
    first_sample_abs = float(np.max(np.abs(audio[:1]))) if audio.size else 0.0
    last_sample_abs = float(np.max(np.abs(audio[-1:]))) if audio.size else 0.0
    start_window_rms = float(np.sqrt(np.mean(np.square(audio[:fade_in_samples], dtype=np.float64)))) if audio.size else 0.0
    end_window_rms = float(np.sqrt(np.mean(np.square(audio[-fade_out_samples:], dtype=np.float64)))) if audio.size else 0.0
    fade_tolerance_ms = max(5.0, expected_fade_in_ms * 0.75)
    fade_in_pass = (
        expected_fade_in_ms - fade_tolerance_ms <= measured_fade_in_ms <= expected_fade_in_ms + fade_tolerance_ms
        and first_sample_abs <= 0.01
        and fade_in_rising
        and fade_in_reference > max(first_env * 5.0, 0.001)
        and fade_in_score < 0.5
    )
    fade_out_pass = (
        expected_fade_out_ms - fade_tolerance_ms <= measured_fade_out_ms <= expected_fade_out_ms + fade_tolerance_ms
        and last_sample_abs <= 0.01
        and fade_out_rising
        and fade_out_reference > max(last_env * 5.0, 0.001)
        and fade_out_score < 0.5
    )
    pass_check = fade_in_pass and fade_out_pass
    return {
        "source": "actual_audio_file",
        "measurement_method": "actual_mix_and_stem_pcm_fit" if reference_audio is not None else "boundary_envelope",
        "expected_fade_in_ms": expected_fade_in_ms,
        "expected_fade_out_ms": expected_fade_out_ms,
        "tolerance_ms": fade_tolerance_ms,
        "fade_in_ms": round(measured_fade_in_ms, 3),
        "fade_out_ms": round(measured_fade_out_ms, 3),
        "first_sample_abs": round(first_sample_abs, 6),
        "last_sample_abs": round(last_sample_abs, 6),
        "start_window_rms": round(start_window_rms, 6),
        "end_window_rms": round(end_window_rms, 6),
        "fade_in_score": round(fade_in_score, 6),
        "fade_out_score": round(fade_out_score, 6),
        "fade_in_pass": fade_in_pass,
        "fade_out_pass": fade_out_pass,
        "pass": pass_check,
    }


def reconstruction_error_db(reference_audio: np.ndarray, reconstructed_audio: np.ndarray) -> dict:
    if (reference_audio.shape != reconstructed_audio.shape or not reference_audio.size
            or not np.isfinite(reference_audio).all() or not np.isfinite(reconstructed_audio).all()):
        return {
            "source": "actual_audio_file",
            "stem_reconstruction_error_db": 0.0,
            "rms_error": 0.0,
            "pass": False,
            "reason": "Reconstruction requires non-empty finite audio with identical sample/channel shapes.",
        }
    reference = np.asarray(reference_audio, dtype=np.float32)
    reconstructed = np.asarray(reconstructed_audio, dtype=np.float32)
    error = reference - reconstructed
    rms_error = float(np.sqrt(np.mean(np.square(error, dtype=np.float64))))
    rms_reference = float(np.sqrt(np.mean(np.square(reference, dtype=np.float64))))
    ratio = rms_error / max(rms_reference, 1e-12)
    error_db = -120.0 if ratio <= 1e-12 else float(20.0 * math.log10(ratio))
    return {
        "source": "actual_audio_file",
        "stem_reconstruction_error_db": round(error_db, 3),
        "rms_error": round(rms_error, 8),
        "pass": error_db <= -40.0,
    }