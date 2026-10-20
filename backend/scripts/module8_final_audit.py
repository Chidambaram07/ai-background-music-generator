"""Generate a fresh real-worker job, retain its files, and audit actual PCM.

Run from backend with PYTHONPATH=. using the project virtual environment.
No cached audio measurements are used by this script. macOS `say` supplies a
new spoken voiceover; ffmpeg provides an independent EBU R128 cross-check.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.signal import resample_poly

from app.core.config import Settings
from app.models.job import JobStatus
from app.schemas.specification import MusicSpecification
from app.services.audio_processing import AudioProcessingConfig, AudioProcessingError, AudioProcessor
from app.services.job_service import JobService
from app.validation.validator import ValidationEngine
from app.workers.job_worker import JobWorker


def independent_stem_model(job_dir: Path, stem_ids: list[str]) -> dict:
    """Fit the documented two mix-master fades to actual stem/mix PCM.

    Stem WAVs have already been faded. rebuild_mix applies another fade and
    process_file applies the final one, plus global mastering gain. A raw sum
    therefore is not the serialized mix. Do not normalize or modify that mix.
    """
    mix, sr = sf.read(job_dir / "mix.wav", dtype="float64")
    summed = sum(sf.read(job_dir / f"{stem}.wav", dtype="float64")[0] for stem in stem_ids)
    edge = min(sr // 5, len(mix) // 2)

    def fit(start: bool) -> dict:
        x = summed[:edge] if start else summed[-edge:][::-1]
        y = mix[:edge] if start else mix[-edge:][::-1]
        best = None
        for ms in np.arange(1.0, 81.0, 0.25):
            n = int(round(sr * ms / 1000))
            curve = np.ones(edge)
            curve[:n] = np.linspace(0, 1, n) ** 2
            model = x * curve
            gain = float(np.dot(model, y) / max(np.dot(model, model), 1e-30))
            error = float(np.sqrt(np.mean((y - gain * model) ** 2)))
            if best is None or error < best[0]:
                best = (error, ms, gain)
        return {"fitted_ms": best[1], "rms_error": best[0], "gain": best[2]}

    curve = np.ones(len(mix))
    n = int(round(sr * 0.01))
    curve[:n] *= np.linspace(0, 1, n) ** 2
    curve[-n:] *= np.linspace(1, 0, n) ** 2
    model = summed * curve
    gain = float(np.dot(model, mix) / max(np.dot(model, model), 1e-30))
    error = np.sqrt(np.mean((mix - gain * model) ** 2))
    relative = error / max(np.sqrt(np.mean(mix ** 2)), 1e-30)
    return {"source": "actual_stem_and_mix_pcm", "model": "raw stem sum * two 10ms mix fades * fitted scalar gain",
            "error_db": float(20 * np.log10(max(relative, 1e-30))), "gain": gain,
            "fade_in_fit": fit(True), "fade_out_fit": fit(False)}


async def main() -> None:
    audit_id = str(uuid4())
    root = Path.cwd()
    evidence_dir = root / "data" / f"module8-audit-{audit_id}"
    evidence_dir.mkdir(parents=True)
    settings = Settings(database_path=str(evidence_dir / "jobs.db"), audio_output_dir=str(root / "audio/generated"))
    service = JobService(settings.database_path)
    worker = JobWorker(service, settings)
    spec = MusicSpecification.model_validate({
        "genre": "cinematic", "mood": ["calm"], "tempo": 90, "key": "C major",
        "instruments": ["Drums", "Bass", "Piano", "Pads"],
        "sections": [{"start": 0, "end": 45, "mood": "calm"}],
    })
    job = service.create_job("Module 3 generation: cinematic calm", 45.0, None, audit_id)
    service.attach_specification(job.job_id, spec.model_dump())
    worker_errors = []
    try:
        await worker.process_music_job(job.job_id)
    except AudioProcessingError as exc:
        worker_errors.append({"operation": "generation", "code": exc.code, "message": str(exc)})
        service.transition(job.job_id, JobStatus.FAILED, error_code=exc.code, error_message=str(exc))
    job_dir = Path(settings.audio_output_dir) / job.job_id

    loop_job = service.create_job("Module 7: looped mix audit", 45.0, None, str(uuid4()), parent_job_id=job.job_id)
    service.set_result(loop_job.job_id, {"bpm": 90, "beats_per_bar": 4})
    try:
        await worker.process_music_job(loop_job.job_id)
    except (AudioProcessingError, ValueError) as exc:
        worker_errors.append({"operation": "looping", "message": str(exc)})

    # A real, newly spoken input, not stored ducking measurements or a tone.
    aiff = evidence_dir / "spoken-voiceover.aiff"
    voice_path = evidence_dir / "voiceover.wav"
    subprocess.run(["say", "-r", "150", "-o", str(aiff),
                    "This is a fresh audio verification. The background music should become quieter while this voice is speaking. "
                    "After this sentence ends, the music should return to its original level."], check=True)
    spoken, voice_rate = sf.read(aiff, dtype="float32")
    if spoken.ndim == 2:
        spoken = np.mean(spoken, axis=1)
    gcd = int(np.gcd(voice_rate, 48000))
    spoken = resample_poly(spoken, 48000 // gcd, voice_rate // gcd)
    spoken *= 0.25 / max(float(np.max(np.abs(spoken))), 1e-12)
    voice = np.zeros(45 * 48000, dtype=np.float32)
    offset = 2 * 48000
    voice[offset:offset + min(len(spoken), len(voice) - offset)] = spoken[:len(voice) - offset]
    sf.write(voice_path, voice, 48000, subtype="PCM_16")
    duck_job = service.create_job("Module 6: voiceover ducking audit", 45.0, None, str(uuid4()), parent_job_id=job.job_id)
    service.set_result(duck_job.job_id, {"music_path": str(job_dir / "mix.wav"), "voiceover_path": str(voice_path),
                                         "duck_db": 12.0, "attack_ms": 50.0, "release_ms": 300.0})
    try:
        await worker.process_music_job(duck_job.job_id)
    except (AudioProcessingError, ValueError) as exc:
        worker_errors.append({"operation": "ducking", "message": str(exc)})

    result = service.get_result(job.job_id) or {}
    inputs = worker.validation_operation_inputs(job.job_id)
    validator = ValidationEngine(worker.audio_processor)
    report = validator.validate_job(job_dir, spec, result, require_manifest_file=True, operation_inputs=inputs)
    mix, sr = sf.read(job_dir / "mix.wav", dtype="float64")
    with wave.open(str(job_dir / "mix.wav"), "rb") as handle:
        header = {"sample_rate_hz": handle.getframerate(), "bit_depth": handle.getsampwidth() * 8,
                  "channels": handle.getnchannels(), "frames": handle.getnframes()}
    oversampled = resample_poly(mix, 4, 1, axis=0)
    actual_peak = float(20 * np.log10(np.max(np.abs(oversampled))))
    stems = {}
    stem_ids = [item["stem_id"] for item in result["stems"]]
    for stem in stem_ids:
        audio, stem_rate = sf.read(job_dir / f"{stem}.wav", dtype="float64")
        stems[stem] = {"frames": len(audio), "duration_s": len(audio) / stem_rate,
                       "sample_rate_hz": stem_rate, "shape_matches_mix": audio.shape == mix.shape}

    ffmpeg = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(job_dir / "mix.wav"),
                             "-filter_complex", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True, check=True)
    (evidence_dir / "ffmpeg-ebur128.txt").write_text(ffmpeg.stderr, encoding="utf-8")
    final_summary = ffmpeg.stderr.rsplit("Summary:", 1)[-1]
    ff_lufs = re.search(r"I:\s*([-\d.]+) LUFS", final_summary)
    ff_peak = re.search(r"Peak:\s*([-\d.]+) dBFS", final_summary)
    forged = copy.deepcopy(result)
    forged.update(stems=[], sample_rate=1, duration_s=1, manifest={"valid": True, "measured_lufs": 100},
                  validation_result={"valid": True}, audio_validation={"overall_status": "PASS"})
    forged["ducking"] = {"voiceover_path": str(voice_path), "measured_duck_db": 99, "ducking_status": "FAIL"}
    forged["loop"] = {**result.get("loop", {}), "status": "FAIL", "boundary_discontinuity": 99,
                      "loop_duration_s": 999, "start_aligned_to_bar": False, "end_aligned_to_bar": False}
    forged_report = validator.validate_job(job_dir, spec, forged, require_manifest_file=True, operation_inputs=inputs)
    other = ValidationEngine(AudioProcessor(AudioProcessingConfig(target_lufs=-30, true_peak_limit_dbtp=10,
                              target_sample_rate=44100, clipping_threshold=2, duration_tolerance_seconds=100)))
    other_report = other.validate_job(job_dir, spec, result, require_manifest_file=True, operation_inputs=inputs)
    paths = [*job_dir.glob("*.wav"), job_dir / "manifest.json", voice_path]
    checks = report["checks"]
    summary = {
        "audit_id": audit_id, "job_id": job.job_id, "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_dir": str(job_dir), "voiceover_path": str(voice_path), "voiceover_source": "new macOS spoken synthesis",
        "duration_s": len(mix) / sr, "duration_error_s": abs(len(mix) / sr - 45.0),
        "wav_header": header, "format": sf.info(job_dir / "mix.wav").format,
        "subtype": sf.info(job_dir / "mix.wav").subtype, "loudness_lufs": float(pyln.Meter(sr).integrated_loudness(mix)),
        "true_peak_dbtp": actual_peak, "clipped_sample_count": int(np.count_nonzero(np.abs(mix) >= 0.9999)),
        "measured_fade_in_ms": checks["fades"].get("fade_in_ms"), "measured_fade_out_ms": checks["fades"].get("fade_out_ms"),
        "stem_count": len(stems), "stems": stems, "stem_reconstruction_error_db": checks["stem_reconstruction"].get("stem_reconstruction_error_db"),
        "independent_stem_pcm_model": independent_stem_model(job_dir, stem_ids),
        "ducking_attenuation_db": checks["ducking"].get("ducking_db"), "ducking": checks["ducking"],
        "looping": checks["looping"], "required_outputs": checks["required_outputs"],
        "ffmpeg_loudness_lufs": float(ff_lufs[1]) if ff_lufs else None,
        "ffmpeg_true_peak_dbtp": float(ff_peak[1]) if ff_peak else None,
        "metadata_forgery_leaves_checks_unchanged": report["checks"] == forged_report["checks"],
        "renderer_config_change_leaves_checks_unchanged": report["checks"] == other_report["checks"],
        "sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        "worker_errors": worker_errors, "overall_status": report["overall_status"], "validation": report,
    }
    evidence_path = evidence_dir / "report.json"
    evidence_path.write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in {"validation", "sha256"}}, indent=2, allow_nan=False))
    print(f"FULL_EVIDENCE_REPORT={evidence_path}")


if __name__ == "__main__":
    asyncio.run(main())