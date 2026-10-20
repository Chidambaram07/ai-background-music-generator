from __future__ import annotations

import hashlib
import time
from importlib.metadata import version

import numpy as np
import pyloudnorm as pyln
import soundfile as sf

from app.services.audio_processing import AudioProcessingError, AudioProcessor
from app.services.storage import ArtifactStorage


def file_hash(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


class ManifestService:
    def __init__(self, storage: ArtifactStorage, processor: AudioProcessor):
        self.storage = storage
        self.processor = processor

    def publish(self, job_id, specification, result, request, validation, *, regenerated=None):
        directory = self.storage.job_dir(job_id)
        previous = result.get("manifest", {})
        previous_stems = {stem["name"]: stem for stem in previous.get("stems", [])}
        timeline = self.processor.build_timeline_manifest(specification, job_id=job_id)
        timeline["commercial_rights"] = {
            "rights_source": "local procedural synthesis", "license_name": None,
            "commercial_use_permitted": None, "third_party_material_used": False,
            "summary": "No third-party samples are used; commercial legal clearance has not been verified.",
        }
        mix, rate = sf.read(self.storage.artifact(job_id, "mix.wav"), dtype="float32")
        if not np.isfinite(mix).all():
            raise AudioProcessingError("ARTIFACT_CORRUPT", "Mix contains non-finite samples.")
        stems = []
        summed = np.zeros_like(mix, dtype=np.float64)
        for stem in result["stems"]:
            name = stem["stem_id"]
            relative = f"stems/{name}.wav"
            destination = self.storage.artifact(job_id, relative, required=False)
            destination.parent.mkdir(exist_ok=True)
            if name not in previous_stems or name == regenerated or not destination.exists():
                audio, stem_rate = sf.read(self.storage.artifact(job_id, stem["filename"]), dtype="float32")
                audio = self.processor._apply_fades(self.processor._apply_fades(audio))
                sf.write(destination, audio, stem_rate, subtype="PCM_24")
            audio, stem_rate = sf.read(destination, dtype="float32")
            if stem_rate != rate or audio.shape != mix.shape:
                raise AudioProcessingError("STEM_ALIGNMENT_FAILED", "Canonical stem does not match mix.", {"stem": name})
            summed += audio
            metadata = dict(previous_stems.get(name, {}))
            if not metadata or name == regenerated:
                source = stem.get("provenance") or {
                    "generation_method": "procedural_synthesis", "provider": "local", "provider_id": "procedural",
                    "model": None, "version": "local-renderer-2", "ai_generated": False,
                }
                metadata = {
                    "name": name, "file": relative, "gain_db": 0.0,
                    "generation_method": source["generation_method"], "provider": source["provider"],
                    "model": source["model"], "version": source["version"], "ai_generated": source["ai_generated"],
                    "provenance": source,
                    "role": stem.get("role"), "rendered_timbre": stem.get("rendered_timbre"),
                    "requested_instrument": stem.get("requested_instrument", stem.get("name")),
                    "plan_sha256": stem.get("plan_sha256"),
                    "seed": result.get("regenerated_seed", result.get("seed")),
                    "generation_job_id": result.get("regeneration_job_id", job_id),
                    "sha256": file_hash(destination),
                }
            stems.append(metadata)
        sources = [stem.get("provenance") or {
            "provider": stem["provider"], "model": stem["model"], "version": stem["version"],
            "generation_method": stem["generation_method"], "ai_generated": stem["ai_generated"],
        } for stem in stems]
        any_ai = any(source["ai_generated"] for source in sources)
        all_ai = bool(sources) and all(source["ai_generated"] for source in sources)

        def common(field):
            values = {source.get(field) for source in sources}
            return next(iter(values)) if len(values) == 1 else "mixed"

        warnings = list(dict.fromkeys(warning for source in sources for warning in source.get("warnings", [])))
        warnings.append("Subjective prompt similarity and commercial legal clearance are not proven.")
        if result.get("ducking") and not result["ducking"].get("speech_regions"):
            warnings.append("No speech was detected in narration; music was left unchanged and no narration was mixed into the output.")
        if result.get("provider_fallback"):
            warnings.append("The requested AI provider failed; explicit procedural fallback was used. See generation.fallback.")
        timeline["commercial_rights"] = {
            "rights_source": common("provider"), "license_name": common("licence"),
            "commercial_use_permitted": False if any(source.get("licence") == "CC-BY-NC-4.0" for source in sources) else None,
            "third_party_material_used": None if any_ai else False,
            "summary": "AI model terms apply; commercial clearance is not established." if any_ai else "No third-party samples are used by procedural synthesis; commercial legal clearance has not been verified.",
        }
        energy = float(np.sum(summed * summed))
        gain = float(np.sum(summed * mix)) / max(energy, 1e-30)
        if gain <= 0:
            raise AudioProcessingError("STEM_RECONSTRUCTION_FAILED", "Cannot reconstruct mix from stems.")
        residual = float(np.sqrt(np.mean((mix - summed * gain) ** 2)))
        reference = float(np.sqrt(np.mean(mix.astype(np.float64) ** 2)))
        error_db = float(20 * np.log10(max(residual, 1e-15) / max(reference, 1e-15)))
        measured_lufs = float(pyln.Meter(rate).integrated_loudness(mix))
        if not np.isfinite(measured_lufs):
            raise AudioProcessingError("ARTIFACT_CORRUPT", "Mix has no finite loudness.")
        formats = {"wav": "mix.wav"}
        if "mp3" in request["output"]["formats"]:
            try:
                sf.write(directory / "mix.mp3", mix, rate, format="MP3")
                decoded, mp3_rate = sf.read(directory / "mix.mp3")
                if mp3_rate != rate or not decoded.size or not np.isfinite(decoded).all():
                    raise ValueError("MP3 failed decoding validation")
            except (RuntimeError, ValueError) as exc:
                raise AudioProcessingError("MP3_ENCODING_FAILED", str(exc)) from exc
            formats["mp3"] = "mix.mp3"
        duration = len(mix) / rate
        ducking = result.get("ducking", {})
        manifest = {
            **timeline,
            "schema_version": "9.1", "job_id": job_id, "request": request,
            "rendering_plan": result.get("composition_plan"),
            "music": {"tempo_bpm": specification.tempo, "key": specification.key,
                      "sections": timeline["sections"], "timing_source": "renderer_grid_not_audio_estimation",
                      "beat_times": np.arange(0, duration, 60 / specification.tempo).tolist(),
                      "bar_times": np.arange(0, duration, 240 / specification.tempo).tolist()},
            "stems": stems,
            "mix": {"file": "mix.wav", "formats": formats, "sample_rate": rate,
                    "duration_s": duration, "loudness_lufs": measured_lufs,
                    "true_peak_dbtp": self.processor._true_peak_dbtp(mix),
                    "master_gain_db": float(20 * np.log10(gain)),
                    "reconstruction": "sum(stem * 10**(gain_db/20)) * 10**(master_gain_db/20)",
                    "sha256": file_hash(directory / "mix.wav")},
            "ducking": {"enabled": bool(ducking), "file": "mix_ducked.wav" if ducking else None,
                        "duck_db": ducking.get("duck_db"), "volume_curve": ducking.get("volume_curve", [])},
            "generation": {"seed": request["seed"], "provider": common("provider"), "model": common("model"),
                           "version": common("version"),
                           "method": common("generation_method"), "ai_generated": any_ai,
                           "all_stems_ai_generated": all_ai, "fallback": result.get("provider_fallback"),
                           "versions": {name: version(name) for name in ("numpy", "soundfile", "scipy", "pyloudnorm")},
                           "generation_time_s": result.get("generation_time_s"), "cost": None,
                           "timing_scope": "rendering_and_audio_processing_excluding_queue_planning_and_validation",
                           "updated_at_ns": time.time_ns()},
            "validation": {**validation, "duration_error_s": abs(duration - request["duration_s"]),
                           "sample_rate_valid": rate == request["output"]["sample_rate"],
                           "loudness_valid": abs(measured_lufs - request["output"]["target_lufs"]) <= 1,
                           "true_peak_valid": self.processor._true_peak_dbtp(mix) <= -1,
                           "clipping_detected": self.processor._detect_clipping(mix),
                           "stem_reconstruction_error_db": error_db,
                           "stem_checks": [{"name": item["name"], **self.storage.wav_info(job_id, item["file"])} for item in stems]},
            "licences": [{"source": source["provider"], "model": source["model"],
                          "third_party_samples": None if source["ai_generated"] else False,
                          "license": source.get("licence"), "legal_clearance_verified": False} for source in sources],
            "warnings": warnings,
        }
        manifest["validation"]["valid"] = bool(validation["valid"] and error_db <= -40 and rate == 48000 and len(stems) >= 3)
        self.storage.verify_manifest(job_id, manifest)
        self.storage.write_json(job_id, "music_manifest.json", manifest)
        result["manifest"] = manifest
        result["manifest_url"] = f"/api/v1/music/jobs/{job_id}/manifest"
        return manifest