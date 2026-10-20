from __future__ import annotations

from pathlib import Path
from typing import Any
import json

import numpy as np
from scipy.signal import resample_poly

from app.schemas.specification import MusicSpecification
from app.services.audio_processing import AudioProcessor
from app.services.music_generation import resolve_stem_definitions
from app.validation.policy import MODULE8_CONFIG
from app.validation.quality_checks import clipping_check, duration_check, fades_check, format_check, load_audio, loudness_check, reconstruction_error_db, sample_rate_check, true_peak_check
from app.validation.stem_validator import StemValidator


class ValidationEngine:
    def __init__(self, processor: AudioProcessor):
        # Renderer settings are not permission to relax Module 8 requirements.
        self.processor = AudioProcessor(MODULE8_CONFIG)
        self.stem_validator = StemValidator(self.processor)

    @staticmethod
    def _attempt(check) -> dict:
        try:
            return check()
        except (OSError, RuntimeError, ValueError, TypeError, IndexError, OverflowError) as exc:
            return {"source": "actual_audio_file", "pass": False, "reason": str(exc)}

    @staticmethod
    def requested_duration(specification: MusicSpecification) -> float:
        return float(max(section.end for section in specification.sections))

    @staticmethod
    def expected_stem_ids(result: dict[str, Any]) -> list[str]:
        stems = result.get("stems") if isinstance(result.get("stems"), list) else []
        return [str(stem.get("stem_id")) for stem in stems if isinstance(stem, dict) and stem.get("stem_id")]

    def validate_required_outputs(self, job_dir: Path, expected_stem_ids: list[str], *, require_manifest_file: bool, ducking_enabled: bool, looping_enabled: bool) -> dict:
        required = ["mix.wav", *[f"{stem_id}.wav" for stem_id in expected_stem_ids]]
        if require_manifest_file:
            required.append("manifest.json")
        if ducking_enabled:
            required.append("mix_ducked.wav")
        if looping_enabled:
            required.append("mix_looped.wav")

        present: list[str] = []
        missing: list[str] = []
        unreadable: dict[str, str] = {}
        for filename in required:
            path = job_dir / filename
            if path.is_file():
                present.append(filename)
                try:
                    if path.suffix == ".wav":
                        audio, _, _ = load_audio(path)
                        if not audio.size:
                            raise ValueError("Audio file contains no samples.")
                    else:
                        # Only verify readability/syntax, not audio claims.
                        json.loads(path.read_text(encoding="utf-8"))
                except (OSError, RuntimeError, ValueError) as exc:
                    unreadable[filename] = str(exc)
            else:
                missing.append(filename)
        return {
            "source": "filesystem",
            "required_files": required,
            "present_files": present,
            "missing_files": missing,
            "unreadable_files": unreadable,
            "pass": not missing and not unreadable,
        }

    @staticmethod
    def _mono(audio: np.ndarray) -> np.ndarray:
        return np.mean(audio, axis=1, dtype=np.float32) if audio.ndim == 2 else np.asarray(audio, dtype=np.float32)

    @staticmethod
    def _rms(audio: np.ndarray) -> float:
        if audio.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))

    def _transition_continuity(self, audio: np.ndarray, sample_rate: int, speech_regions: list[dict[str, float]]) -> dict:
        mono = np.asarray(audio)
        if len(mono) < 2 or not speech_regions:
            return {"max_delta": 0.0, "pass": True}
        edge_samples = max(8, int(round(0.01 * sample_rate)))
        deltas: list[float] = []
        for region in speech_regions:
            for time_key in ("start_s", "end_s"):
                center = int(round(float(region.get(time_key, 0.0)) * sample_rate))
                lo = max(1, center - edge_samples)
                hi = min(len(mono), center + edge_samples)
                if hi - lo <= 1:
                    continue
                window = mono[lo:hi]
                deltas.append(float(np.max(np.abs(np.diff(window, axis=0)))))
        max_delta = max(deltas, default=0.0)
        return {"max_delta": round(max_delta, 6), "pass": max_delta < 0.35}

    def validate_ducking(self, job_dir: Path, ducking_result: dict[str, Any] | None) -> dict:
        path = job_dir / "mix_ducked.wav"
        enabled = bool(ducking_result) or path.is_file()
        if not enabled:
            return {
                "enabled": False,
                "source": "actual_audio_file",
                "pass": True,
                "reason": "Ducking not enabled for this job.",
            }
        if not path.is_file() or not isinstance(ducking_result, dict):
            return {
                "enabled": True,
                "source": "actual_audio_file",
                "pass": False,
                "reason": "Ducked output or voiceover source locator is missing.",
            }
        mix_path = job_dir / "mix.wav"
        voiceover_path = Path(str(ducking_result.get("voiceover_path", ""))) if ducking_result.get("voiceover_path") else None
        if not mix_path.is_file() or not voiceover_path or not voiceover_path.is_file():
            return {
                "enabled": True,
                "source": "actual_audio_file",
                "pass": False,
                "reason": "Original mix or voiceover source is missing for ducking validation.",
            }
        ducked_audio, sample_rate, info = load_audio(path)
        original_audio, original_rate, original_info = load_audio(mix_path)
        voiceover_audio, voiceover_rate, _ = load_audio(voiceover_path)
        if (original_rate != sample_rate or original_audio.shape != ducked_audio.shape
                or original_info.channels != info.channels):
            return {"enabled": True, "source": "actual_audio_file", "pass": False,
                    "reason": "Ducked WAV must match the original mix sample count, rate and channels."}
        if voiceover_rate != sample_rate:
            gcd = int(np.gcd(voiceover_rate, sample_rate))
            voiceover_audio = resample_poly(voiceover_audio, sample_rate // gcd, voiceover_rate // gcd, axis=0)

        speech_regions = self.processor.detect_speech_regions(voiceover_audio, sample_rate)
        mix_samples = len(ducked_audio)
        voiceover_mono = self._mono(voiceover_audio)
        if voiceover_mono.size > mix_samples:
            voiceover_track = voiceover_mono[:mix_samples].copy()
        else:
            voiceover_track = np.pad(voiceover_mono, (0, max(0, mix_samples - voiceover_mono.size)), mode="constant")

        before_segments = []
        during_segments = []
        recovery_segments = []
        recovery_reference = []
        mono_original = original_audio
        mono_ducked_music = ducked_audio
        for region in speech_regions:
            start_idx = max(0, min(len(mono_original), int(round(float(region["start_s"]) * sample_rate))))
            end_idx = max(start_idx, min(len(mono_original), int(round(float(region["end_s"]) * sample_rate))))
            if end_idx <= start_idx:
                continue
            before_segments.append(mono_original[start_idx:end_idx])
            during_segments.append(mono_ducked_music[start_idx:end_idx])
            # Test recovery after the standard 300ms release, not during it.
            recovery_start = end_idx + int(round(0.3 * sample_rate))
            recovery_end = min(len(mono_original), recovery_start + max(1, int(round(0.25 * sample_rate))))
            next_start = min((int(round(r["start_s"] * sample_rate)) for r in speech_regions
                              if r["start_s"] >= region["end_s"]), default=len(mono_original))
            recovery_end = min(recovery_end, next_start)
            if recovery_end > recovery_start:
                recovery_segments.append(mono_ducked_music[recovery_start:recovery_end])
                recovery_reference.append(mono_original[recovery_start:recovery_end])

        music_before = self._rms(np.concatenate(before_segments)) if before_segments else 0.0
        music_during = self._rms(np.concatenate(during_segments)) if during_segments else 0.0
        measured_duck_db = 0.0
        if music_before > 0.0 and music_during > 0.0:
            measured_duck_db = float(20.0 * np.log10(music_before / music_during))
        recovery_rms = self._rms(np.concatenate(recovery_segments)) if recovery_segments else 0.0
        recovery_reference_rms = self._rms(np.concatenate(recovery_reference)) if recovery_reference else 0.0
        recovers_after_speech = bool(recovery_reference_rms > 1e-9 and recovery_rms >= recovery_reference_rms * 0.75)
        sample_rate_result = sample_rate_check(sample_rate, self.processor.config.target_sample_rate)
        format_result = format_check(info)
        clipping = clipping_check(ducked_audio, self.processor)
        ducking_in_range = 8.0 <= measured_duck_db <= 15.0
        transition_metrics = self._transition_continuity(ducked_audio, sample_rate, speech_regions)
        no_speech_unchanged = not speech_regions and bool(np.max(np.abs(ducked_audio - original_audio)) <= 1.0 / 32768.0)
        pass_check = all((
            sample_rate_result["pass"],
            format_result["pass"],
            clipping["pass"],
            no_speech_unchanged or bool(speech_regions),
            no_speech_unchanged or ducking_in_range,
            no_speech_unchanged or music_before > music_during,
            no_speech_unchanged or recovers_after_speech,
            transition_metrics["pass"],
        ))
        return {
            "enabled": True,
            "no_speech_unchanged": no_speech_unchanged,
            "source": "actual_audio_file",
            "file_exists": True,
            "sample_rate": sample_rate_result,
            "format": format_result,
            "ducking_db": round(measured_duck_db, 3),
            "speech_regions": speech_regions,
            "music_rms_before": round(music_before, 6),
            "music_rms_during_speech": round(music_during, 6),
            "music_rms_after_speech": round(recovery_rms, 6),
            "transitions_smooth": transition_metrics["pass"],
            "transition_metrics": transition_metrics,
            "recovers_after_speech": recovers_after_speech,
            "clipping": clipping,
            "pass": pass_check,
        }

    def validate_looping(self, job_dir: Path, loop_result: dict[str, Any] | None, *, bpm: float = 0.0, beats_per_bar: int = 4) -> dict:
        path = job_dir / "mix_looped.wav"
        enabled = bool(loop_result) or path.is_file()
        if not enabled:
            return {
                "enabled": False,
                "source": "actual_audio_file",
                "pass": True,
                "reason": "Looping not enabled for this job.",
            }
        if not path.is_file() or not isinstance(loop_result, dict):
            return {
                "enabled": True,
                "source": "actual_audio_file",
                "pass": False,
                "reason": "Looped output or loop location is missing.",
            }
        audio, sample_rate, info = load_audio(path)
        original, original_rate, original_info = load_audio(job_dir / "mix.wav")
        sample_rate_result = sample_rate_check(sample_rate, self.processor.config.target_sample_rate)
        format_result = format_check(info)
        clipping = clipping_check(audio, self.processor)
        actual_duration = float(len(audio) / sample_rate) if sample_rate > 0 else 0.0
        bar_duration = 60.0 * beats_per_bar / bpm if bpm > 0 and beats_per_bar > 0 else 0.0
        expected_end = min(len(original) / original_rate, max(1, int((len(original) / original_rate) / bar_duration)) * bar_duration) if bar_duration else 0.0
        expected_start = max(0.0, expected_end - bar_duration)
        loop_start = float(loop_result.get("loop_start_s", expected_start))
        loop_end = float(loop_result.get("loop_end_s", expected_end))
        # Coordinates select the seam to test; persisted duration/status/jump
        # and bar-alignment flags are never used as evidence. Never clamp.
        loop_start_idx = int(round(loop_start * sample_rate))
        loop_end_idx = int(round(loop_end * sample_rate))
        in_bounds = 0 <= loop_start_idx < loop_end_idx <= len(audio)
        loop_duration = (loop_end_idx - loop_start_idx) / sample_rate
        bar_samples = sample_rate * 60.0 * beats_per_bar / bpm if bpm > 0 and beats_per_bar > 0 else 0.0
        start_aligned = bool(bar_samples and abs(loop_start_idx / bar_samples - round(loop_start_idx / bar_samples)) * bar_samples <= 1.0)
        end_aligned = bool(bar_samples and abs(loop_end_idx / bar_samples - round(loop_end_idx / bar_samples)) * bar_samples <= 1.0)
        matches_mix = audio.shape == original.shape and sample_rate == original_rate and info.channels == original_info.channels
        if in_bounds:
            tail_sample = np.asarray(audio[loop_end_idx - 1], dtype=np.float32)
            head_sample = np.asarray(audio[loop_start_idx], dtype=np.float32)
            boundary_discontinuity = float(np.max(np.abs(tail_sample - head_sample)))
        else:
            boundary_discontinuity = 1.0
        pass_check = all((
            sample_rate_result["pass"],
            format_result["pass"],
            clipping["pass"],
            in_bounds,
            matches_mix,
            abs(loop_start - expected_start) <= 1.0 / sample_rate,
            abs(loop_end - expected_end) <= 1.0 / sample_rate,
            start_aligned,
            end_aligned,
            loop_duration > 0.0,
            boundary_discontinuity < 0.25,
        ))
        return {
            "enabled": True,
            "source": "actual_audio_file",
            "file_exists": True,
            "sample_rate": sample_rate_result,
            "format": format_result,
            "actual_duration_s": round(actual_duration, 6),
            "loop_start_s": round(loop_start, 6),
            "loop_end_s": round(loop_end, 6),
            "loop_duration_s": round(loop_duration, 6),
            "boundary_discontinuity": round(boundary_discontinuity, 6),
            "start_aligned_to_bar": start_aligned,
            "end_aligned_to_bar": end_aligned,
            "matches_mix": matches_mix,
            "clipping": clipping,
            "pass": pass_check,
        }

    def validate_stem_reconstruction(self, job_dir: Path, expected_stem_ids: list[str], expected_duration: float) -> dict:
        mix_path = job_dir / "mix.wav"
        if not mix_path.is_file():
            return {
                "source": "actual_audio_file",
                "stem_reconstruction_error_db": 0.0,
                "rms_error": 0.0,
                "pass": False,
                "reason": "mix.wav is missing.",
            }

        actual_mix, actual_rate, _ = load_audio(mix_path)
        if not expected_stem_ids or actual_rate != self.processor.config.target_sample_rate:
            return {"source": "actual_audio_file", "pass": False, "reason": "Missing stems or non-compliant mix rate."}
        arrays: list[np.ndarray] = []
        for stem_id in expected_stem_ids:
            path = job_dir / f"{stem_id}.wav"
            if not path.is_file():
                return {
                    "source": "actual_audio_file",
                    "stem_reconstruction_error_db": 0.0,
                    "rms_error": 0.0,
                    "pass": False,
                    "reason": f"{stem_id}.wav is missing.",
                }
            audio, sample_rate, _ = load_audio(path)
            if sample_rate != actual_rate or audio.shape != actual_mix.shape:
                return {
                    "source": "actual_audio_file",
                    "stem_reconstruction_error_db": 0.0,
                    "rms_error": 0.0,
                    "pass": False,
                    "reason": f"{stem_id}.wav does not match the mix sample count, rate or channels.",
                }
            arrays.append(audio)

        # Reproduce the documented mastering transform on real stems only.
        # Never fit/repair the observed mix, nor hide errors by truncation.
        actual_duration = len(actual_mix) / actual_rate
        rebuilt_mix = self.processor.mix_stem_arrays(arrays)
        rebuilt_processed, _ = self.processor.process_array(
            rebuilt_mix,
            self.processor.config.target_sample_rate,
            actual_duration,
            normalize_loudness=True,
        )
        return reconstruction_error_db(actual_mix, rebuilt_processed)

    @staticmethod
    def _summarize(checks: dict[str, dict[str, Any]]) -> tuple[bool, dict[str, int]]:
        failed = sum(1 for check in checks.values() if isinstance(check, dict) and check.get("pass") is False)
        passed = sum(1 for check in checks.values() if isinstance(check, dict) and check.get("pass") is True)
        warnings = 0
        return failed == 0, {"passed": passed, "failed": failed, "warnings": warnings}

    def _legacy_audio_validation(self, *, duration: dict, sample_rate: dict, loudness: dict, true_peak: dict, clipping: dict, fades: dict, format_result: dict) -> dict:
        if any("reason" in check for check in (duration, sample_rate, loudness, true_peak, clipping, fades, format_result)):
            return {"overall_status": "FAIL", "reason": "One or more audio measurements could not be completed."}
        audio_pass = all((
            duration["pass"],
            sample_rate["pass"],
            format_result["pass"],
            loudness["pass"],
            true_peak["pass"],
            clipping["pass"],
            fades["pass"],
        ))
        return {
            "requested_duration_seconds": duration["requested_s"],
            "actual_duration_seconds": duration["actual_s"],
            "duration_error_seconds": duration["error_s"],
            "duration_tolerance_seconds": duration["tolerance_s"],
            "duration_status": "PASS" if duration["pass"] else "FAIL",
            "sample_rate": sample_rate["actual_hz"],
            "sample_rate_status": "PASS" if sample_rate["pass"] else "FAIL",
            "format": format_result["container"],
            "subtype": format_result["subtype"],
            "channels": format_result["channels"],
            "target_lufs": loudness["target_lufs"],
            "measured_lufs": loudness["measured_lufs"],
            "loudness_error_lufs": loudness["difference_lufs"],
            "loudness_tolerance_lufs": loudness["tolerance_lufs"],
            "loudness_status": "PASS" if loudness["pass"] else "FAIL",
            "true_peak_dbTP": true_peak["measured_dbtp"],
            "true_peak_limit_dbTP": true_peak["limit_dbtp"],
            "true_peak_status": "PASS" if true_peak["pass"] else "FAIL",
            "clipping_detected": not clipping["pass"],
            "clipped_samples": clipping["clipped_samples"],
            "clipping_status": "PASS" if clipping["pass"] else "FAIL",
            "fade_in_ms": fades["fade_in_ms"],
            "fade_out_ms": fades["fade_out_ms"],
            "format_status": "PASS" if format_result["pass"] else "FAIL",
            "overall_status": "PASS" if audio_pass else "FAIL",
        }

    def validate_job(self, job_dir: Path, specification: MusicSpecification, result: dict[str, Any], *, require_manifest_file: bool, operation_inputs: dict | None = None) -> dict:
        operation_inputs = operation_inputs or {}
        # Only original operation inputs (source locators / musical grid), not
        # their persisted measurements, may supplement the specification.
        result = dict(result)
        if "ducking" in operation_inputs:
            source = result.get("ducking") if isinstance(result.get("ducking"), dict) else {}
            result["ducking"] = {**source, **operation_inputs["ducking"]}
        if "loop" in operation_inputs and not result.get("loop"):
            result["loop"] = {"enabled": True}
        requested_duration = self.requested_duration(specification)
        expected_stem_ids = [stem.stem_id for stem in resolve_stem_definitions(specification.instruments)]
        mix_path = job_dir / "mix.wav"
        try:
            mix_audio, mix_sample_rate, mix_info = load_audio(mix_path)
            if not mix_audio.size:
                raise ValueError("mix.wav contains no samples.")
        except (OSError, RuntimeError, ValueError) as exc:
            checks = {name: {"source": "actual_audio_file", "pass": False, "reason": str(exc)}
                      for name in ("duration", "sample_rate", "format", "loudness", "true_peak", "clipping",
                                   "fades", "stems", "stem_reconstruction", "ducking", "looping")}
            checks["required_outputs"] = self.validate_required_outputs(
                job_dir, expected_stem_ids, require_manifest_file=require_manifest_file,
                ducking_enabled=bool(result.get("ducking")) or (job_dir / "mix_ducked.wav").is_file(),
                looping_enabled=bool(result.get("loop")) or (job_dir / "mix_looped.wav").is_file())
            _, summary = self._summarize(checks)
            return {"valid": False, "overall_status": "FAIL", "checks": checks, "summary": summary,
                    "audio_validation": {"overall_status": "FAIL", "reason": str(exc)}, "stems_validation": {}}
        mix_duration = float(len(mix_audio) / mix_sample_rate) if mix_sample_rate > 0 else requested_duration

        duration = duration_check(mix_audio, mix_sample_rate, requested_duration, self.processor.config.duration_tolerance_seconds)
        sample_rate_result = sample_rate_check(mix_sample_rate, self.processor.config.target_sample_rate)
        format_result = format_check(mix_info)
        loudness = self._attempt(lambda: loudness_check(mix_audio, mix_sample_rate, self.processor))
        true_peak = true_peak_check(mix_audio, self.processor)
        clipping = clipping_check(mix_audio, self.processor)
        stems, stems_legacy = self.stem_validator.validate(job_dir, expected_stem_ids, mix_duration)
        # Use observed stem PCM to separate natural musical envelope motion
        # from extra mix-boundary attenuation. Do not re-render or apply fades
        # to this reference, nor inherit reconstruction's PASS/FAIL.
        fade_reference = None
        if stems["pass"]:
            fade_reference = np.zeros_like(mix_audio, dtype=np.float64)
            for stem_id in expected_stem_ids:
                stem_audio, _, _ = load_audio(job_dir / f"{stem_id}.wav")
                fade_reference += stem_audio
        fades = fades_check(mix_audio, mix_sample_rate, self.processor, reference_audio=fade_reference)
        stem_reconstruction = self._attempt(lambda: self.validate_stem_reconstruction(job_dir, expected_stem_ids, mix_duration))
        ducking = self._attempt(lambda: self.validate_ducking(job_dir, result.get("ducking") if isinstance(result.get("ducking"), dict) else None))
        loop_inputs = operation_inputs.get("loop", {})
        looping = self._attempt(lambda: self.validate_looping(
            job_dir, result.get("loop") if isinstance(result.get("loop"), dict) else None,
            bpm=float(loop_inputs.get("bpm", specification.tempo)), beats_per_bar=int(loop_inputs.get("beats_per_bar", 4))))
        required_outputs = self.validate_required_outputs(
            job_dir,
            expected_stem_ids,
            require_manifest_file=require_manifest_file,
            ducking_enabled=bool(result.get("ducking")) or (job_dir / "mix_ducked.wav").is_file(),
            looping_enabled=bool(result.get("loop")) or (job_dir / "mix_looped.wav").is_file(),
        )

        checks = {
            "duration": duration,
            "sample_rate": sample_rate_result,
            "format": format_result,
            "loudness": loudness,
            "true_peak": true_peak,
            "clipping": clipping,
            "fades": fades,
            "stems": stems,
            "stem_reconstruction": stem_reconstruction,
            "required_outputs": required_outputs,
            "ducking": ducking,
            "looping": looping,
        }
        valid, summary = self._summarize(checks)
        audio_validation = self._legacy_audio_validation(
            duration=duration,
            sample_rate=sample_rate_result,
            loudness=loudness,
            true_peak=true_peak,
            clipping=clipping,
            fades=fades,
            format_result=format_result,
        )
        return {
            "valid": valid,
            "overall_status": "PASS" if valid else "FAIL",
            "checks": checks,
            "summary": summary,
            "audio_validation": audio_validation,
            "stems_validation": stems_legacy,
        }