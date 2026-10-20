from __future__ import annotations

from pathlib import Path

import numpy as np

from app.services.audio_processing import AudioProcessor
from app.validation.policy import MODULE8_CONFIG
from app.validation.quality_checks import clipping_check, duration_check, format_check, load_audio, sample_rate_check


class StemValidator:
    def __init__(self, processor: AudioProcessor):
        self.processor = AudioProcessor(MODULE8_CONFIG)

    def validate(self, job_dir: Path, expected_stem_ids: list[str], expected_duration: float) -> tuple[dict, dict[str, dict]]:
        present_stems = sorted(path.stem for path in job_dir.glob("*.wav") if path.stem not in {"mix", "mix_looped", "mix_ducked"})
        details: dict[str, dict] = {}
        legacy: dict[str, dict] = {}
        sample_counts: list[int] = []
        channel_counts: list[int] = []
        sample_rates: list[int] = []
        mix_reference = None
        if (job_dir / "mix.wav").is_file():
            mix_audio, mix_rate, mix_info = load_audio(job_dir / "mix.wav")
            mix_reference = (len(mix_audio), mix_rate, int(mix_info.channels))

        for stem_id in expected_stem_ids:
            path = job_dir / f"{stem_id}.wav"
            if not path.is_file():
                details[stem_id] = {
                    "exists": False,
                    "readable": False,
                    "pass": False,
                }
                legacy[stem_id] = details[stem_id]
                continue

            try:
                audio, sample_rate, info = load_audio(path)
            except (OSError, RuntimeError, ValueError) as exc:
                details[stem_id] = {"exists": True, "readable": False, "pass": False, "reason": str(exc)}
                legacy[stem_id] = details[stem_id]
                continue
            duration = duration_check(audio, sample_rate, expected_duration, self.processor.config.duration_tolerance_seconds)
            sample_rate_result = sample_rate_check(sample_rate, self.processor.config.target_sample_rate)
            format_result = format_check(info)
            clipping = clipping_check(audio, self.processor)
            non_empty = bool(np.max(np.abs(audio)) > 1e-7) if audio.size else False
            channels = int(info.channels)
            sample_counts.append(int(len(audio)))
            channel_counts.append(channels)
            sample_rates.append(int(sample_rate))
            detail = {
                "source": "actual_audio_file",
                "exists": True,
                "readable": True,
                "duration_s": duration["actual_s"],
                "duration": duration,
                "sample_rate_hz": int(sample_rate),
                "sample_rate": sample_rate_result,
                "format": format_result,
                "channels": channels,
                "non_empty": non_empty,
                "clipping": clipping,
                "pass": all((duration["pass"], sample_rate_result["pass"], format_result["pass"], clipping["pass"], non_empty)),
            }
            details[stem_id] = detail
            legacy[stem_id] = {
                "exists": True,
                "duration_s": duration["actual_s"],
                "sample_rate_hz": int(sample_rate),
                "channels": channels,
                "clipped_samples": clipping["clipped_samples"],
                "pass": detail["pass"],
            }

        missing = [stem_id for stem_id in expected_stem_ids if not details.get(stem_id, {}).get("exists")]
        extra = [stem_id for stem_id in present_stems if stem_id not in expected_stem_ids]
        aligned_samples = len(set(sample_counts)) <= 1 if sample_counts else False
        aligned_channels = len(set(channel_counts)) <= 1 if channel_counts else False
        aligned_sample_rates = len(set(sample_rates)) <= 1 if sample_rates else False
        matches_mix = bool(sample_counts) and (
            mix_reference is None or all(
                values == mix_reference for values in zip(sample_counts, sample_rates, channel_counts)
            )
        )
        pass_check = (
            bool(expected_stem_ids)
            and len(set(expected_stem_ids)) == len(expected_stem_ids) == len(present_stems)
            and not missing
            and not extra
            and all(detail.get("pass") for detail in details.values())
            and aligned_samples
            and aligned_channels
            and aligned_sample_rates
            and matches_mix
        )
        aggregate = {
            "source": "actual_audio_file",
            "expected_count": len(expected_stem_ids),
            "actual_count": len(present_stems),
            "expected_stems": expected_stem_ids,
            "present_stems": present_stems,
            "missing_stems": missing,
            "extra_stems": extra,
            "sample_alignment": {
                "aligned": aligned_samples and aligned_channels and aligned_sample_rates and matches_mix,
                "matches_mix": matches_mix,
                "mix_sample_count": mix_reference[0] if mix_reference else None,
                "sample_counts": sample_counts,
                "channel_counts": channel_counts,
                "sample_rates": sample_rates,
            },
            "details": details,
            "pass": pass_check,
        }
        return aggregate, legacy