"""Module 8 acceptance policy, independent of renderer/environment settings.

Requests define intended duration, instruments and loop locations, never measured
properties. Changing the renderer's settings must not redefine compliance.
"""

from app.services.audio_processing import AudioProcessingConfig


MODULE8_CONFIG = AudioProcessingConfig(
    target_sample_rate=48000,
    target_lufs=-16.0,
    loudness_tolerance_lufs=1.0,
    true_peak_limit_dbtp=-1.0,
    duration_tolerance_seconds=0.05,
    fade_in_ms=10.0,
    fade_out_ms=10.0,
    clipping_threshold=0.9999,
    true_peak_oversample=4,
)