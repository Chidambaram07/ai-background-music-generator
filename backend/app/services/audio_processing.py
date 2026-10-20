from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.signal import resample_poly

from app.schemas.specification import MusicSpecification


@dataclass(frozen=True)
class AudioProcessingConfig:
    target_sample_rate: int = 48000
    target_lufs: float = -16.0
    loudness_tolerance_lufs: float = 1.0
    true_peak_limit_dbtp: float = -1.0
    duration_tolerance_seconds: float = 0.1
    fade_in_ms: float = 10.0
    fade_out_ms: float = 10.0
    clipping_threshold: float = 0.9999
    true_peak_oversample: int = 4


class AudioProcessingError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


class AudioProcessor:
    STEM_NAMES = ("drums", "bass", "harmony", "melody")

    def __init__(self, config: AudioProcessingConfig | None = None):
        self.config = config or AudioProcessingConfig()

    @staticmethod
    def _as_float32(audio: np.ndarray) -> np.ndarray:
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim == 1:
            return audio
        if audio.ndim == 2:
            return audio
        raise AudioProcessingError("INVALID_AUDIO", "Unsupported audio dimensions.", {"ndim": int(audio.ndim)})

    @staticmethod
    def _duration(audio: np.ndarray, sample_rate: int) -> float:
        return float(len(audio) / sample_rate)

    def _resample(self, audio: np.ndarray, source_rate: int) -> np.ndarray:
        if source_rate == self.config.target_sample_rate:
            return audio.astype(np.float32, copy=True)
        if source_rate <= 0:
            raise AudioProcessingError("INVALID_SAMPLE_RATE", "Audio has an invalid sample rate.", {"sample_rate": source_rate})
        gcd = np.gcd(source_rate, self.config.target_sample_rate)
        up = self.config.target_sample_rate // gcd
        down = source_rate // gcd
        return resample_poly(audio, up, down, axis=0).astype(np.float32)

    def _fit_duration(self, audio: np.ndarray, requested_duration: float) -> np.ndarray:
        target_samples = max(1, int(round(requested_duration * self.config.target_sample_rate)))
        if len(audio) > target_samples:
            return audio[:target_samples].copy()
        if len(audio) < target_samples:
            pad = target_samples - len(audio)
            if audio.ndim == 1:
                return np.pad(audio, (0, pad))
            return np.pad(audio, ((0, pad), (0, 0)))
        return audio.copy()

    @staticmethod
    def _mono(audio: np.ndarray) -> np.ndarray:
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim == 1:
            return audio
        if audio.ndim == 2:
            return np.mean(audio, axis=1, dtype=np.float32)
        raise AudioProcessingError("INVALID_AUDIO", "Unsupported audio dimensions.", {"ndim": int(audio.ndim)})

    @staticmethod
    def _rms(seg: np.ndarray) -> float:
        if seg.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(seg, dtype=np.float64))))

    def detect_speech_regions(
        self,
        audio: np.ndarray,
        sample_rate: int | None = None,
        *,
        threshold_db: float | None = None,
        min_speech_duration: float = 0.2,
        min_silence_duration: float = 0.2,
    ) -> list[dict[str, float]]:
        mono = self._mono(audio)
        sr = int(sample_rate or self.config.target_sample_rate)
        if sr <= 0:
            raise AudioProcessingError("INVALID_SAMPLE_RATE", "Audio has an invalid sample rate.", {"sample_rate": sr})
        frame_length = max(256, int(round(0.025 * sr)))
        hop = max(128, frame_length // 2)
        if mono.size < frame_length:
            return []
        frames = []
        for frame_start in range(0, len(mono) - frame_length + 1, hop):
            frames.append(mono[frame_start:frame_start + frame_length])
        if not frames:
            return []
        rms = np.asarray([self._rms(frame) for frame in frames], dtype=np.float32)
        energy_db = 20.0 * np.log10(rms + 1e-8)
        finite = np.isfinite(energy_db)
        if not finite.any():
            return []
        noise_floor = float(np.median(energy_db[finite])) if finite.any() else -90.0
        speech_threshold = threshold_db if threshold_db is not None else max(noise_floor + 10.0, -38.0)
        speech_mask = energy_db >= speech_threshold
        if not speech_mask.any():
            return []

        segments: list[tuple[int, int]] = []
        start_idx: int | None = None
        for idx, is_speech in enumerate(speech_mask):
            if is_speech and start_idx is None:
                start_idx = idx
            elif not is_speech and start_idx is not None:
                segments.append((start_idx, idx))
                start_idx = None
        if start_idx is not None:
            segments.append((start_idx, len(speech_mask)))

        regions: list[dict[str, float]] = []
        for segment_start, segment_end in segments:
            start_s = (segment_start * hop) / sr
            end_s = ((segment_end * hop) + frame_length) / sr
            duration_s = max(0.0, end_s - start_s)
            if duration_s < min_speech_duration:
                continue
            if not regions:
                regions.append({"start_s": round(start_s, 6), "end_s": round(end_s, 6)})
                continue
            gap = start_s - regions[-1]["end_s"]
            if gap <= min_silence_duration:
                regions[-1]["end_s"] = round(max(regions[-1]["end_s"], end_s), 6)
            else:
                regions.append({"start_s": round(start_s, 6), "end_s": round(end_s, 6)})
        return regions

    def build_instrumental_validation(self, audio: np.ndarray, sample_rate: int | None = None) -> dict:
        sr = int(sample_rate or self.config.target_sample_rate)
        speech_regions = self.detect_speech_regions(audio, sr)
        vocal_detected = bool(speech_regions)
        return {
            "sample_rate": sr,
            "speech_regions": speech_regions,
            "vocal_detected": vocal_detected,
            "instrumental": not vocal_detected,
            "status": "PASS" if not vocal_detected else "FAIL",
            "summary": "No speech regions detected in the generated mix." if not vocal_detected else "Speech-like regions were detected in the generated mix.",
        }

    def build_timeline_manifest(self, specification: MusicSpecification, *, job_id: str | None = None) -> dict:
        return specification.timeline_manifest(job_id=job_id).model_dump(mode="python")

    @staticmethod
    def _default_section_name(index: int, total_sections: int) -> str:
        labels = ["INTRO", "BUILD", "MAIN", "OUTRO"]
        if total_sections <= len(labels):
            return labels[index] if index < len(labels) else f"SECTION_{index + 1}"
        if index < len(labels):
            return labels[index]
        return f"SECTION_{index + 1}"

    def generate_looped_mix(
        self,
        audio: np.ndarray,
        *,
        bpm: int,
        beats_per_bar: int = 4,
        sample_rate: int | None = None,
        duration_s: float | None = None,
    ) -> tuple[np.ndarray, dict]:
        array = self._as_float32(audio)
        sr = int(sample_rate or self.config.target_sample_rate)
        if bpm <= 0:
            raise AudioProcessingError("INVALID_BPM", "BPM must be positive.", {"bpm": bpm})
        if beats_per_bar <= 0:
            raise AudioProcessingError("INVALID_BEATS_PER_BAR", "beats_per_bar must be positive.", {"beats_per_bar": beats_per_bar})

        track_duration_s = float(duration_s if duration_s is not None else (len(array) / sr))
        beat_duration_s = 60.0 / bpm
        bar_duration_s = beat_duration_s * beats_per_bar
        full_bar_count = max(1, int(math.floor(track_duration_s / bar_duration_s)))
        loop_end_s = min(track_duration_s, full_bar_count * bar_duration_s)
        loop_start_s = max(0.0, loop_end_s - bar_duration_s)
        loop_start = int(round(loop_start_s * sr))
        loop_end = int(round(loop_end_s * sr))
        loop_end = max(loop_start + 1, min(len(array), loop_end))
        loop_segment = array[loop_start:loop_end].copy()

        fade_samples = min(max(64, int(round(0.08 * sr))), max(1, loop_segment.shape[0] // 4))
        if fade_samples >= loop_segment.shape[0]:
            fade_samples = max(1, loop_segment.shape[0] // 2)
        processed = array.copy()
        loop_head = loop_segment[:fade_samples]
        loop_tail = processed[loop_end - fade_samples:loop_end].copy()
        fade_out = np.linspace(1.0, 0.0, fade_samples, endpoint=True, dtype=np.float32)
        fade_in = np.linspace(0.0, 1.0, fade_samples, endpoint=True, dtype=np.float32)
        boundary_target = np.full_like(loop_tail, loop_head[0])
        processed[loop_end - fade_samples:loop_end] = (loop_tail * fade_out) + (boundary_target * fade_in)

        boundary_discontinuity = float(abs(processed[loop_end - 1] - processed[loop_start]))
        start_aligned = abs(loop_start_s % bar_duration_s) < 1e-2 or abs((loop_start_s % bar_duration_s) - bar_duration_s) < 1e-2
        end_aligned = abs(loop_end_s % bar_duration_s) < 1e-2 or abs((loop_end_s % bar_duration_s) - bar_duration_s) < 1e-2
        clipping_detected = self._detect_clipping(processed)
        status = "PASS" if (not clipping_detected and boundary_discontinuity < 0.25 and start_aligned and end_aligned) else "FAIL"

        validation = {
            "enabled": True,
            "loop_start_s": round(loop_start_s, 6),
            "loop_end_s": round(loop_end_s, 6),
            "loop_duration_s": round(float(loop_end_s - loop_start_s), 6),
            "bpm": int(bpm),
            "beats_per_bar": int(beats_per_bar),
            "start_aligned_to_bar": start_aligned,
            "end_aligned_to_bar": end_aligned,
            "boundary_discontinuity": round(float(boundary_discontinuity), 6),
            "clipping_detected": clipping_detected,
            "status": status,
            "sample_rate": sr,
            "duration_s": round(track_duration_s, 6),
        }
        return processed, validation

    def duck_music(
        self,
        music: np.ndarray,
        voiceover: np.ndarray,
        *,
        duck_db: float = 12.0,
        attack_ms: float = 50.0,
        release_ms: float = 300.0,
        music_sample_rate: int | None = None,
        voiceover_sample_rate: int | None = None,
    ) -> tuple[np.ndarray, dict]:
        music_array = self._as_float32(music)
        voice_array = self._as_float32(voiceover)
        if music_array.size == 0 or voice_array.size == 0:
            raise AudioProcessingError("INVALID_AUDIO", "Music and voiceover audio must not be empty.")

        music_rate = int(music_sample_rate or self.config.target_sample_rate)
        voice_rate = int(voiceover_sample_rate or self.config.target_sample_rate)
        if music_rate <= 0 or voice_rate <= 0:
            raise AudioProcessingError("INVALID_SAMPLE_RATE", "Music and voiceover require valid sample rates.", {"music_sample_rate": music_rate, "voiceover_sample_rate": voice_rate})

        music_resampled = self._resample(music_array, music_rate)
        voice_resampled = self._resample(voice_array, voice_rate)
        if music_resampled.shape[0] < 1 or voice_resampled.shape[0] < 1:
            raise AudioProcessingError("INVALID_AUDIO", "Audio processing requires at least one sample.")

        speech_regions = self.detect_speech_regions(voice_resampled, self.config.target_sample_rate)
        target_gain = 10.0 ** (-max(0.0, min(float(duck_db), 30.0)) / 20.0)
        attack_samples = max(1, int(round((max(attack_ms, 1.0) / 1000.0) * self.config.target_sample_rate)))
        release_samples = max(1, int(round((max(release_ms, 1.0) / 1000.0) * self.config.target_sample_rate)))

        gain_curve = np.ones(music_resampled.shape[0], dtype=np.float32)
        for region in speech_regions:
            start_idx = int(round(region["start_s"] * self.config.target_sample_rate))
            end_idx = int(round(region["end_s"] * self.config.target_sample_rate))
            start_idx = max(0, min(len(gain_curve), start_idx))
            end_idx = max(start_idx, min(len(gain_curve), end_idx))
            if start_idx == end_idx:
                continue
            attack_start = max(0, start_idx - attack_samples)
            if attack_start < start_idx:
                attack_len = start_idx - attack_start
                attack_curve = np.linspace(1.0, target_gain, attack_len, endpoint=False, dtype=np.float32)
                gain_curve[attack_start:start_idx] = attack_curve
            gain_curve[start_idx:end_idx] = target_gain
            release_end = min(len(gain_curve), end_idx + release_samples)
            if release_end > end_idx:
                release_len = release_end - end_idx
                release_curve = np.linspace(target_gain, 1.0, release_len, endpoint=False, dtype=np.float32)
                gain_curve[end_idx:release_end] = release_curve

        if music_resampled.ndim == 2:
            ducked = music_resampled * gain_curve[:, None]
        else:
            ducked = music_resampled * gain_curve
        ducked = np.clip(ducked, -1.0, 1.0).astype(np.float32)

        before_rms = 0.0
        during_rms = 0.0
        if speech_regions:
            before_segments = []
            after_segments = []
            for region in speech_regions:
                start_idx = int(round(region["start_s"] * self.config.target_sample_rate))
                end_idx = int(round(region["end_s"] * self.config.target_sample_rate))
                start_idx = max(0, min(len(music_resampled), start_idx))
                end_idx = max(start_idx, min(len(music_resampled), end_idx))
                if end_idx <= start_idx:
                    continue
                before_segments.append(music_resampled[start_idx:end_idx])
                after_segments.append(ducked[start_idx:end_idx])
            if before_segments:
                before_rms = float(np.sqrt(np.mean(np.square(np.concatenate([seg.ravel() for seg in before_segments], axis=0), dtype=np.float64))))
                during_rms = float(np.sqrt(np.mean(np.square(np.concatenate([seg.ravel() for seg in after_segments], axis=0), dtype=np.float64))))

        measured_duck_db = 0.0
        if before_rms > 0.0 and during_rms > 0.0:
            measured_duck_db = 20.0 * np.log10(before_rms / during_rms)
            measured_duck_db = float(np.clip(measured_duck_db, -30.0, 30.0))

        ducking_status = "NO_SPEECH"
        if speech_regions:
            if not self._detect_clipping(ducked):
                ducking_status = "PASS" if abs(measured_duck_db - duck_db) <= 3.5 else "FAIL"
            else:
                ducking_status = "FAIL"

        metadata = {
            "speech_regions": speech_regions,
            "duck_db": float(duck_db),
            "attack_ms": float(attack_ms),
            "release_ms": float(release_ms),
            "music_rms_before": round(before_rms, 6),
            "music_rms_during_speech": round(during_rms, 6),
            "measured_duck_db": round(measured_duck_db, 3),
            "ducking_status": ducking_status,
            "music_sample_rate": music_rate,
            "voiceover_sample_rate": voice_rate,
            "volume_curve": [
                {"time_s":round(index / self.config.target_sample_rate, 8), "gain":float(gain_curve[min(index, len(gain_curve)-1)]),
                 "gain_db":float(20 * np.log10(max(float(gain_curve[min(index, len(gain_curve)-1)]), 1e-12)))}
                for index in sorted({0, len(gain_curve), *[
                    max(0, min(len(gain_curve), sample)) for region in speech_regions for sample in (
                        round(region["start_s"] * self.config.target_sample_rate) - attack_samples,
                        round(region["start_s"] * self.config.target_sample_rate),
                        round(region["end_s"] * self.config.target_sample_rate),
                        round(region["end_s"] * self.config.target_sample_rate) + release_samples,
                    )
                ]})
            ],
            "curve_interpolation": "linear_gain",
        }
        return ducked, metadata

    def process_looped_audio(
        self,
        mix_path: str | Path,
        output_path: str | Path,
        *,
        bpm: int,
        beats_per_bar: int = 4,
    ) -> dict:
        audio, sample_rate = sf.read(mix_path, dtype="float32")
        looped, validation = self.generate_looped_mix(
            audio,
            bpm=bpm,
            beats_per_bar=beats_per_bar,
            sample_rate=sample_rate,
            duration_s=float(len(audio) / sample_rate),
        )
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, looped.astype(np.float32), sample_rate, subtype="PCM_16")
        serialized_audio, serialized_rate = sf.read(output, dtype="float32")
        if serialized_rate != sample_rate:
            raise AudioProcessingError("INVALID_SAMPLE_RATE", "Looped WAV was generated with an unexpected sample rate.", {"expected": sample_rate, "actual": serialized_rate})
        if validation["status"] != "PASS":
            raise AudioProcessingError("LOOP_VALIDATION_FAILED", "Looped WAV failed validation.", validation)
        return {
            "output_path": str(output),
            "loop": validation,
            "sample_rate": serialized_rate,
            "duration_s": float(len(serialized_audio) / serialized_rate),
        }

    def process_ducked_audio(
        self,
        music_path: str | Path,
        voiceover_path: str | Path,
        output_path: str | Path,
        *,
        duck_db: float = 12.0,
        attack_ms: float = 50.0,
        release_ms: float = 300.0,
    ) -> dict:
        music_audio, music_rate = sf.read(music_path, dtype="float32")
        voice_audio, voice_rate = sf.read(voiceover_path, dtype="float32")

        ducked, metadata = self.duck_music(
            music_audio,
            voice_audio,
            duck_db=duck_db,
            attack_ms=attack_ms,
            release_ms=release_ms,
            music_sample_rate=music_rate,
            voiceover_sample_rate=voice_rate,
        )

        final_mix = ducked.astype(np.float32, copy=True)
        final_mix = self._safe_peak_scale(final_mix)
        final_mix = self._limit_true_peak(final_mix)
        final_mix = self._safe_peak_scale(final_mix)

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, final_mix.astype(np.float32), self.config.target_sample_rate, subtype="PCM_16")
        serialized_audio, serialized_rate = sf.read(output, dtype="float32")
        requested_duration = self._duration(ducked, self.config.target_sample_rate)
        validation = self._validate(serialized_audio, requested_duration, require_loudness=False)
        validation["ducking_status"] = metadata["ducking_status"]
        validation["speech_regions"] = metadata["speech_regions"]
        validation["duck_db"] = metadata["duck_db"]
        validation["attack_ms"] = metadata["attack_ms"]
        validation["release_ms"] = metadata["release_ms"]
        validation["music_rms_before"] = metadata["music_rms_before"]
        validation["music_rms_during_speech"] = metadata["music_rms_during_speech"]
        validation["measured_duck_db"] = metadata["measured_duck_db"]
        validation["volume_curve"] = metadata["volume_curve"]
        validation["curve_interpolation"] = "linear_gain"
        validation["contains_narration"] = False
        validation["sample_rate_status"] = "PASS" if serialized_rate == self.config.target_sample_rate else "FAIL"
        validation["overall_status"] = "PASS" if validation["overall_status"] == "PASS" and validation["ducking_status"] in {"PASS", "NO_SPEECH"} else "FAIL"
        if validation["overall_status"] != "PASS":
            raise AudioProcessingError("AUDIO_VALIDATION_FAILED", "Processed ducked audio failed validation.", validation)
        return {
            "output_path": str(output),
            "ducking": validation,
            "speech_regions": metadata["speech_regions"],
            "measured_duck_db": metadata["measured_duck_db"],
            "duck_db": metadata["duck_db"],
            "ducking_status": metadata["ducking_status"],
        }

    def _apply_fades(self, audio: np.ndarray) -> np.ndarray:
        result = audio.copy()
        length = len(result)
        fade_in = min(length, int(round(self.config.fade_in_ms / 1000 * self.config.target_sample_rate)))
        fade_out = min(length, int(round(self.config.fade_out_ms / 1000 * self.config.target_sample_rate)))
        if fade_in > 0:
            curve = np.linspace(0.0, 1.0, fade_in, endpoint=True, dtype=np.float32)
            result[:fade_in] *= curve[:, None] if result.ndim == 2 else curve
        if fade_out > 0:
            curve = np.linspace(1.0, 0.0, fade_out, endpoint=True, dtype=np.float32)
            result[-fade_out:] *= curve[:, None] if result.ndim == 2 else curve
        return result

    @staticmethod
    def _sample_peak(audio: np.ndarray) -> float:
        return float(np.max(np.abs(audio))) if audio.size else 0.0

    def _true_peak_dbtp(self, audio: np.ndarray) -> float:
        if not audio.size:
            return float("-inf")
        oversample = self.config.true_peak_oversample
        if audio.ndim == 1:
            oversampled = resample_poly(audio, oversample, 1)
            peak = float(np.max(np.abs(oversampled)))
        else:
            peaks = [float(np.max(np.abs(resample_poly(audio[:, ch], oversample, 1)))) for ch in range(audio.shape[1])]
            peak = max(peaks, default=0.0)
        if peak <= 1e-12:
            return -120.0
        return float(20.0 * np.log10(peak))

    def _detect_clipping(self, audio: np.ndarray) -> bool:
        return bool(np.any(np.abs(audio) >= self.config.clipping_threshold))

    def _safe_peak_scale(self, audio: np.ndarray, max_sample_peak: float = 0.98) -> np.ndarray:
        peak = self._sample_peak(audio)
        if peak <= max_sample_peak or peak <= 1e-12:
            return audio
        return (audio * (max_sample_peak / peak)).astype(np.float32)

    def _limit_true_peak(self, audio: np.ndarray) -> np.ndarray:
        true_peak = self._true_peak_dbtp(audio)
        if true_peak <= self.config.true_peak_limit_dbtp:
            return audio
        # Leave a small safety margin for PCM-16 quantization after writing.
        target = self.config.true_peak_limit_dbtp - 0.05
        gain = 10 ** ((target - true_peak) / 20.0)
        return (audio * gain).astype(np.float32)

    def _measure_lufs(self, audio: np.ndarray) -> float:
        meter = pyln.Meter(self.config.target_sample_rate)
        measured = float(meter.integrated_loudness(audio))
        if not np.isfinite(measured):
            raise AudioProcessingError("INVALID_AUDIO", "Unable to measure integrated loudness.")
        return measured

    def _normalize_loudness(self, audio: np.ndarray) -> np.ndarray:
        measured = self._measure_lufs(audio)
        if measured < -120:
            raise AudioProcessingError("INVALID_AUDIO", "Cannot normalize silent audio.")
        gain_db = self.config.target_lufs - measured
        normalized = pyln.normalize.loudness(audio, measured, self.config.target_lufs).astype(np.float32)
        # Loudness normalization can create full-scale samples. Apply a safe
        # sample-peak guard before the true-peak check.
        return self._safe_peak_scale(normalized)

    def _validate(self, audio: np.ndarray, requested_duration: float, *, require_loudness: bool = False) -> dict:
        actual = self._duration(audio, self.config.target_sample_rate)
        duration_error = abs(actual - requested_duration)
        measured_lufs = self._measure_lufs(audio)
        true_peak = self._true_peak_dbtp(audio)
        clipping = self._detect_clipping(audio)
        loudness_error = abs(measured_lufs - self.config.target_lufs)
        duration_status = duration_error <= self.config.duration_tolerance_seconds
        sample_rate_status = True
        loudness_status = loudness_error <= self.config.loudness_tolerance_lufs if require_loudness else True
        loudness_status_label = "PASS" if loudness_status else "FAIL"
        if not require_loudness:
            loudness_status_label = "MEASURED"
        true_peak_status = true_peak <= self.config.true_peak_limit_dbtp + 1e-6
        clipping_status = not clipping
        overall = all((duration_status, sample_rate_status, loudness_status, true_peak_status, clipping_status))
        return {
            "requested_duration_seconds": round(float(requested_duration), 6),
            "actual_duration_seconds": round(actual, 6),
            "duration_error_seconds": round(duration_error, 6),
            "duration_tolerance_seconds": self.config.duration_tolerance_seconds,
            "duration_status": "PASS" if duration_status else "FAIL",
            "sample_rate": self.config.target_sample_rate,
            "sample_rate_status": "PASS" if sample_rate_status else "FAIL",
            "target_lufs": self.config.target_lufs,
            "measured_lufs": round(measured_lufs, 3),
            "loudness_error_lufs": round(loudness_error, 3),
            "loudness_tolerance_lufs": self.config.loudness_tolerance_lufs,
            "loudness_status": loudness_status_label,
            "loudness_required": require_loudness,
            "true_peak_dbTP": round(true_peak, 3),
            "true_peak_limit_dbTP": self.config.true_peak_limit_dbtp,
            "true_peak_status": "PASS" if true_peak_status else "FAIL",
            "clipping_detected": clipping,
            "clipping_status": "PASS" if clipping_status else "FAIL",
            "fade_in_ms": self.config.fade_in_ms,
            "fade_out_ms": self.config.fade_out_ms,
            "overall_status": "PASS" if overall else "FAIL",
        }

    def process_array(self, audio: np.ndarray, source_sample_rate: int, requested_duration: float, *, normalize_loudness: bool = False) -> tuple[np.ndarray, dict]:
        audio = self._as_float32(audio)
        if not np.isfinite(audio).all():
            raise AudioProcessingError("INVALID_AUDIO", "Audio contains non-finite samples.")
        input_clipping_detected = self._detect_clipping(audio)
        audio = self._resample(audio, source_sample_rate)
        audio = self._fit_duration(audio, requested_duration)
        audio = self._apply_fades(audio)
        # Never leave digital clipping in a processed file.
        audio = self._safe_peak_scale(audio)
        if normalize_loudness:
            audio = self._normalize_loudness(audio)
        audio = self._limit_true_peak(audio)
        audio = self._safe_peak_scale(audio)
        validation = self._validate(audio, requested_duration, require_loudness=normalize_loudness)
        validation["clipping_detected_before_processing"] = input_clipping_detected
        validation["clipping_corrected"] = input_clipping_detected and not validation["clipping_detected"]
        if validation["overall_status"] != "PASS":
            raise AudioProcessingError("AUDIO_VALIDATION_FAILED", "Processed audio did not satisfy validation requirements.", validation)
        return audio, validation

    def process_file(self, path: Path, requested_duration: float, *, normalize_loudness: bool = False) -> dict:
        audio, source_rate = sf.read(path, dtype="float32")
        processed, validation = self.process_array(audio, source_rate, requested_duration, normalize_loudness=normalize_loudness)
        sf.write(path, processed, self.config.target_sample_rate, subtype="PCM_16")
        # Validate the serialized WAV too; this catches conversion/writer issues.
        serialized, serialized_rate = sf.read(path, dtype="float32")
        final = self._validate(serialized, requested_duration, require_loudness=normalize_loudness)
        final["sample_rate_status"] = "PASS" if serialized_rate == self.config.target_sample_rate else "FAIL"
        final["clipping_detected_before_processing"] = validation["clipping_detected_before_processing"]
        final["clipping_corrected"] = validation["clipping_corrected"]
        if final["overall_status"] != "PASS":
            raise AudioProcessingError("AUDIO_VALIDATION_FAILED", "Serialized audio failed final validation.", final)
        return final

    def process_job(self, job_dir: Path, requested_duration: float, stem_ids: list[str] | tuple[str, ...] | None = None) -> dict:
        resolved_stem_ids = tuple(stem_ids or self.STEM_NAMES)
        stem_results: dict[str, dict] = {}
        for stem in resolved_stem_ids:
            path = job_dir / f"{stem}.wav"
            if not path.is_file():
                raise AudioProcessingError("INVALID_AUDIO", f"Required stem '{stem}' is missing.", {"stem_id": stem})
            stem_results[stem] = self.process_file(path, requested_duration, normalize_loudness=False)

        self.rebuild_mix(job_dir, requested_duration, resolved_stem_ids)
        mix_path = job_dir / "mix.wav"
        mix_validation = self.process_file(mix_path, requested_duration, normalize_loudness=True)
        return {"stems": stem_results, "mix": mix_validation, "overall_status": mix_validation["overall_status"]}

    def process_regenerated_stem(self, job_dir: Path, stem: str, requested_duration: float, stem_ids: list[str] | tuple[str, ...] | None = None) -> dict:
        resolved_stem_ids = tuple(stem_ids or self.STEM_NAMES)
        if stem not in resolved_stem_ids:
            raise AudioProcessingError("INVALID_STEM", f"Unsupported stem '{stem}'.", {"stem_id": stem})
        path = job_dir / f"{stem}.wav"
        if not path.is_file():
            raise AudioProcessingError("INVALID_AUDIO", f"Regenerated stem '{stem}' is missing.", {"stem_id": stem})
        stem_validation = self.process_file(path, requested_duration, normalize_loudness=False)
        self._normalize_job_stems_to_target_rate(job_dir, requested_duration, resolved_stem_ids)
        self.rebuild_mix(job_dir, requested_duration, resolved_stem_ids)
        mix_validation = self.process_file(job_dir / "mix.wav", requested_duration, normalize_loudness=True)
        return {"stem_id": stem, "stem_validation": stem_validation, "mix_validation": mix_validation, "overall_status": mix_validation["overall_status"]}

    def _normalize_job_stems_to_target_rate(self, job_dir: Path, requested_duration: float, stem_ids: list[str] | tuple[str, ...] | None = None) -> None:
        resolved_stem_ids = tuple(stem_ids or self.STEM_NAMES)
        for stem in resolved_stem_ids:
            path = job_dir / f"{stem}.wav"
            if not path.is_file():
                raise AudioProcessingError("INVALID_AUDIO", f"Required stem '{stem}' is missing.", {"stem_id": stem})
            _, rate = sf.read(path, dtype="float32")
            if rate != self.config.target_sample_rate:
                self.process_file(path, requested_duration, normalize_loudness=False)

    def mix_stem_arrays(self, stem_arrays: list[np.ndarray]) -> np.ndarray:
        if not stem_arrays:
            raise AudioProcessingError("INVALID_AUDIO", "At least one stem is required to build a mix.")
        mix = np.zeros_like(stem_arrays[0], dtype=np.float32)
        for audio in stem_arrays:
            mix = mix + audio
        peak = self._sample_peak(mix)
        if peak > 0.92:
            mix *= 0.92 / peak
        elif peak > 1e-9:
            mix *= 0.88 / peak
        mix = self._apply_fades(mix)
        return self._safe_peak_scale(mix)

    def rebuild_mix(self, job_dir: Path, requested_duration: float, stem_ids: list[str] | tuple[str, ...] | None = None) -> None:
        resolved_stem_ids = tuple(stem_ids or self.STEM_NAMES)
        arrays = []
        for stem in resolved_stem_ids:
            path = job_dir / f"{stem}.wav"
            if not path.is_file():
                raise AudioProcessingError("INVALID_AUDIO", f"Required stem '{stem}' is missing.", {"stem_id": stem})
            audio, rate = sf.read(path, dtype="float32")
            if rate != self.config.target_sample_rate:
                raise AudioProcessingError("INVALID_SAMPLE_RATE", f"Stem '{stem}' has sample rate {rate}; expected {self.config.target_sample_rate}.", {"stem_id": stem, "sample_rate": rate})
            arrays.append(self._fit_duration(audio, requested_duration))
        mix = self.mix_stem_arrays(arrays)
        sf.write(job_dir / "mix.wav", mix.astype(np.float32), self.config.target_sample_rate, subtype="PCM_16")
