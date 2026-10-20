from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import nullcontext
from pathlib import Path

import soundfile as sf

from app.core.config import Settings
from app.core.logging import correlation_context, job_context
from app.core.llm import LLMService
from app.models.job import JobStatus
from app.services.audio_processing import AudioProcessingConfig, AudioProcessingError, AudioProcessor
from app.services.job_service import JobService
from app.services.music_generation import MusicGenerationEngine
from app.services.audio_providers import select_audio_provider
from app.services.narration import NarrationDownloader
from app.services.object_storage import OutputStore
from app.services.manifest import ManifestService
from app.schemas.music import MusicJobCreate
from app.services.specification_service import SpecificationService
from app.schemas.specification import MusicSpecification
from app.validation import ValidationEngine

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(self, job_service: JobService, settings: Settings):
        self.job_service = job_service
        self.settings = settings
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._stopped = False
        self.engine = MusicGenerationEngine(
            settings.audio_output_dir,
            sample_rate=settings.audio_sample_rate,
            seed=settings.audio_seed,
            provider=select_audio_provider(settings),
            fallback_to_procedural=settings.audio_provider_fallback,
        )
        self.audio_processor = AudioProcessor(
            AudioProcessingConfig(
                target_sample_rate=settings.audio_processing_sample_rate,
                target_lufs=settings.audio_target_lufs,
                loudness_tolerance_lufs=settings.audio_loudness_tolerance_lufs,
                true_peak_limit_dbtp=settings.audio_true_peak_limit_dbtp,
                duration_tolerance_seconds=settings.audio_duration_tolerance_seconds,
                fade_in_ms=settings.audio_fade_in_ms,
                fade_out_ms=settings.audio_fade_out_ms,
            )
        )
        self.llm_service = LLMService(settings)
        self.specification_service = SpecificationService(settings)
        self.validation_engine = ValidationEngine(self.audio_processor)
        self.storage = self.engine.storage
        self.manifests = ManifestService(self.storage, self.audio_processor)
        self.output_store = OutputStore(settings,self.storage)

    def _build_ps1_evidence(self, *, job_id: str, requested_duration: float, job_dir: Path, manifest: dict) -> dict:
        mix_path = job_dir / "mix.wav"
        mix_audio, mix_rate = sf.read(mix_path, dtype="float32")
        return {
            "music_job_id": job_id,
            "instrumental": self.audio_processor.build_instrumental_validation(mix_audio, mix_rate),
            "commercial_rights": manifest.get("commercial_rights") if isinstance(manifest, dict) else None,
            "prompt_variety": self.llm_service.build_prompt_variety_evidence(),
            "duration_range": self.specification_service.build_duration_range_evidence(requested_duration),
        }

    def _manifest_path(self, job_id: str) -> Path:
        return self.engine._job_dir(job_id) / "manifest.json"

    def _write_manifest(self, job_id: str, manifest: dict) -> None:
        self.storage.write_json(job_id, "manifest.json", manifest)

    def validation_operation_inputs(self, job_id: str) -> dict:
        """Recover operation intent, never cached measurement/status evidence."""
        inputs = {}
        for child in self.job_service.jobs_for_parent(job_id):
            child_result = self.job_service.get_result(child.job_id) or {}
            if child.prompt.startswith("Module 6") and "ducking" not in inputs:
                inputs["ducking"] = {"voiceover_path": child_result.get("voiceover_path")}
            if child.prompt.startswith("Module 7") and "loop" not in inputs:
                inputs["loop"] = {key: child_result[key] for key in ("bpm", "beats_per_bar") if key in child_result}
        return inputs

    def _refresh_validation(self, job_id: str, specification: MusicSpecification, result: dict) -> dict:
        manifest = result.get("manifest") if isinstance(result.get("manifest"), dict) else self.audio_processor.build_timeline_manifest(specification, job_id=job_id)
        validation_result = self.validation_engine.validate_job(
            self.engine._job_dir(job_id),
            specification,
            result,
            require_manifest_file=False,
            operation_inputs=self.validation_operation_inputs(job_id),
        )
        manifest["validation"] = validation_result
        manifest["licences"] = {"commercial_rights": manifest.get("commercial_rights")}
        result["manifest"] = manifest
        self._write_manifest(job_id, manifest)

        validation_result = self.validation_engine.validate_job(
            self.engine._job_dir(job_id),
            specification,
            result,
            require_manifest_file=True,
            operation_inputs=self.validation_operation_inputs(job_id),
        )
        manifest["validation"] = validation_result
        manifest["licences"] = {"commercial_rights": manifest.get("commercial_rights")}
        result["manifest"] = manifest
        result["validation_result"] = validation_result
        result["audio_validation"] = validation_result["audio_validation"]
        result["stems_validation"] = validation_result["stems_validation"]
        job = self.job_service.get_job(job_id)
        stored_request = self.job_service.get_request(job_id) or MusicJobCreate(prompt=job.prompt, duration_s=job.duration_s, seed=result.get("seed", self.settings.audio_seed)).model_dump()
        manifest = self.manifests.publish(job_id, specification, result, stored_request, validation_result, regenerated=result.pop("regenerated_stem", None))
        validation_result["valid"] = manifest["validation"]["valid"]
        validation_result["overall_status"] = "PASS" if validation_result["valid"] else "FAIL"
        self._write_manifest(job_id, manifest)
        return validation_result

    def _revalidate_parent_job(self, parent_job_id: str) -> tuple[dict, dict]:
        specification_json = self.job_service.get_specification(parent_job_id)
        if not specification_json:
            raise ValueError("The parent Module 3 job does not contain a specification.")
        specification = MusicSpecification.model_validate(specification_json)
        parent_result = self.job_service.get_result(parent_job_id) or {}
        validation_result = self._refresh_validation(parent_job_id, specification, parent_result)
        parent_result["ps1_evidence"] = self._build_ps1_evidence(
            job_id=parent_job_id,
            requested_duration=self.validation_engine.requested_duration(specification),
            job_dir=self.engine._job_dir(parent_job_id),
            manifest=parent_result["manifest"],
        )
        self.job_service.set_result(parent_job_id, parent_result)
        if validation_result["valid"]:
            parent_result["storage"] = self.output_store.publish(parent_job_id,parent_result["manifest"])
            self.job_service.set_result(parent_job_id,parent_result)
        return parent_result, validation_result

    async def start(self) -> None:
        self._stopped = False
        self._task = asyncio.create_task(self._run(), name="music-job-worker")
        for job in self.job_service.queued_or_processing_jobs():
            if job.status == JobStatus.PROCESSING:
                self.job_service.transition(job.job_id, JobStatus.FAILED, error_code="WORKER_RESTART", error_message="The worker restarted while this job was processing.")
            else:
                await self.queue.put(job.job_id)

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def submit(self, job_id: str) -> None:
        await self.queue.put(job_id)

    async def _run(self) -> None:
        while not self._stopped:
            job_id = await self.queue.get()
            parent_id = None
            previous_result = None
            try:
                job = self.job_service.get_job(job_id)
                parent_id = job.parent_job_id
                if parent_id and job.status == JobStatus.QUEUED:
                    previous_result = self.job_service.get_result(parent_id)
                    await asyncio.to_thread(self.output_store.restore,parent_id,(previous_result or {}).get("storage",{}))
                    def publish_child():
                        child_result = self.job_service.get_result(job_id) or {}
                        child_manifest = self.storage.read_json(job_id,"music_manifest.json")
                        child_result["storage"] = self.output_store.publish(job_id,child_manifest)
                        self.job_service.set_result(job_id,child_result)
                    transaction = self.storage.parent_update(job_id, parent_id,after_snapshot=publish_child)
                else:
                    transaction = nullcontext()
                with transaction:
                    await self.process_music_job(job_id)
                if parent_id and self.job_service.get_job(job_id).status == JobStatus.PROCESSING:
                    self.job_service.transition(job_id, JobStatus.SUCCEEDED)
            except Exception as exc:
                if parent_id and previous_result is not None:
                    self.job_service.set_result(parent_id, previous_result)
                logger.exception("Unhandled worker error", extra={"job_id": job_id,"correlation_id":self.job_service.get_correlation(job_id)})
                try:
                    current = self.job_service.get_job(job_id)
                    if current.status == JobStatus.PROCESSING:
                        if isinstance(exc, AudioProcessingError):
                            self.job_service.transition(job_id, JobStatus.FAILED, error_code=exc.code, error_message=str(exc), error_details=exc.details)
                        else:
                            self.job_service.transition(job_id, JobStatus.FAILED, error_code="MUSIC_GENERATION_FAILED", error_message=str(exc))
                except Exception:
                    logger.exception("Unable to mark job failed", extra={"job_id": job_id,"correlation_id":self.job_service.get_correlation(job_id)})
            finally:
                self.queue.task_done()

    async def process_music_job(self, job_id: str) -> None:
        correlation_token = correlation_context.set(self.job_service.get_correlation(job_id))
        job_token = job_context.set(job_id)
        try:
            await self._process_music_job(job_id)
        finally:
            correlation_context.reset(correlation_token)
            job_context.reset(job_token)

    async def _process_music_job(self, job_id: str) -> None:
        job = self.job_service.get_job(job_id)
        if job.status == JobStatus.CANCELLED:
            return
        if job.status == JobStatus.QUEUED:
            self.job_service.transition(job_id, JobStatus.PROCESSING)
        elif job.status != JobStatus.PROCESSING:
            return

        if job.parent_job_id and (job.prompt.startswith("Module 6") or "voiceover duck" in job.prompt.lower() or "ducking" in job.prompt.lower()):
            await self.process_voiceover_ducking(job_id)
            return

        if job.parent_job_id and (job.prompt.startswith("Module 7") or "loop" in job.prompt.lower()):
            await self.process_loop_job(job_id)
            return

        if job.stem_id and job.parent_job_id:
            specification_json = self.job_service.get_specification(job.parent_job_id)
            if not specification_json:
                raise ValueError("The parent Module 3 job does not contain a specification.")
            spec = MusicSpecification.model_validate(specification_json)
            loop = asyncio.get_running_loop()

            def update(progress: int) -> None:
                self.job_service.update_progress(job_id, progress)

            regeneration_options = await self.llm_service.interpret_stem_instruction(job.prompt or "", job.stem_id)
            logger.info(
                "Interpreted stem instruction",
                extra={
                    "job_id": job_id,
                    "stem_id": job.stem_id,
                    "instruction": job.prompt,
                    "options": regeneration_options,
                },
            )

            result = await asyncio.to_thread(
                lambda: self.engine.regenerate_stem(
                    spec,
                    job.parent_job_id,
                    job.stem_id,
                    job.job_id,
                    job.prompt,
                    job.preserve_options or {},
                    update,
                    regeneration_options,
                    base_seed=(self.job_service.get_result(job.parent_job_id) or {}).get("seed", self.settings.audio_seed),
                ),
            )
            stem_ids = [stem_info["stem_id"] for stem_info in (self.job_service.get_result(job.parent_job_id) or {}).get("stems", []) if isinstance(stem_info, dict) and stem_info.get("stem_id")]
            processed = await asyncio.to_thread(
                self.audio_processor.process_regenerated_stem,
                self.engine._job_dir(job.parent_job_id),
                job.stem_id,
                job.duration_s,
                stem_ids,
            )
            result.update({
                "sample_rate": self.audio_processor.config.target_sample_rate,
                "audio_validation": processed["stem_validation"],
                "mix_validation": processed["mix_validation"],
            })
            stem_regeneration_evidence = {
                "stem_id": job.stem_id,
                "parent_job_id": job.parent_job_id,
                "job_id": job.job_id,
                "instruction": job.prompt,
                "preserve": job.preserve_options or {},
                "stem_validation": processed["stem_validation"],
                "mix_validation": processed["mix_validation"],
                "status": "PASS" if processed["overall_status"] == "PASS" else "FAIL",
            }
            result["stem_regeneration"] = stem_regeneration_evidence
            parent_ps1_evidence = dict((self.job_service.get_result(job.parent_job_id) or {}).get("ps1_evidence", {}))
            parent_ps1_evidence["stem_regeneration"] = stem_regeneration_evidence
            result["ps1_evidence"] = parent_ps1_evidence
            self.job_service.set_result(job_id, result)

            parent_result = self.job_service.get_result(job.parent_job_id) or {}
            parent_result["regenerated_stem"] = job.stem_id
            parent_result["regenerated_seed"] = result["seed"]
            parent_result["regeneration_job_id"] = job_id
            if result.get("provider_fallback"):
                parent_result["provider_fallback"] = result["provider_fallback"]
            for stem in parent_result.get("stems", []):
                if stem.get("stem_id") == job.stem_id:
                    stem["provenance"] = result["provenance"]
                    stem["rendered_timbre"] = result["rendered_timbre"]
            parent_result["sample_rate"] = self.audio_processor.config.target_sample_rate
            parent_result["audio_validation"] = processed["mix_validation"]
            parent_result["stems_validation"] = {
                **parent_result.get("stems_validation", {}),
                job.stem_id: processed["stem_validation"],
            }
            parent_ps1_evidence = dict(parent_result.get("ps1_evidence", {}))
            parent_ps1_evidence["stem_regeneration"] = stem_regeneration_evidence
            parent_result["ps1_evidence"] = parent_ps1_evidence
            if parent_result.get("ducking", {}).get("voiceover_path"):
                voiceover = parent_result["ducking"]["voiceover_path"]
                ducked = self.audio_processor.process_ducked_audio(self.storage.artifact(job.parent_job_id, "mix.wav"), voiceover, self.storage.artifact(job.parent_job_id, "mix_ducked.wav", required=False))
                parent_result["ducking"] = {**ducked["ducking"], "voiceover_path": voiceover}
            if parent_result.get("loop"):
                looped = self.audio_processor.process_looped_audio(self.storage.artifact(job.parent_job_id, "mix.wav"), self.storage.artifact(job.parent_job_id, "mix_looped.wav", required=False), bpm=spec.tempo)
                parent_result["loop"] = looped["loop"]
            self.job_service.set_result(job.parent_job_id, parent_result)
            parent_result, validation_result = self._revalidate_parent_job(job.parent_job_id)
            parent_ps1_evidence = dict(parent_result.get("ps1_evidence", {}))
            parent_ps1_evidence["stem_regeneration"] = stem_regeneration_evidence
            parent_result["ps1_evidence"] = parent_ps1_evidence
            self.job_service.set_result(job.parent_job_id, parent_result)
            if not validation_result["valid"]:
                raise AudioProcessingError("VALIDATION_FAILED", "Regenerated audio failed validation.", {"validation": validation_result})
            logger.info(
                "Stem regeneration completed",
                extra={"job_id": job_id, "parent_job_id": job.parent_job_id, "stem_id": job.stem_id},
            )
            return

        specification_json = self.job_service.get_specification(job_id)
        if not specification_json:
            request = MusicJobCreate.model_validate(self.job_service.get_request(job_id) or {"prompt": job.prompt, "duration_s": job.duration_s})
            sections = request.sections or [{"start": 0, "end": job.duration_s, "mood": "focused"}]
            planned = await self.specification_service.create_specification(job.prompt, {"sections": sections, "tempo": request.tempo_bpm, "key": request.key, "instruments": ["drums", "bass", "piano", "pads"]})
            specification_json = planned.model_dump()
            self.job_service.attach_specification(job_id, specification_json)

        spec = MusicSpecification.model_validate(specification_json)
        request_data = self.job_service.get_request(job_id) or MusicJobCreate(prompt=job.prompt, duration_s=job.duration_s, seed=self.settings.audio_seed).model_dump()
        started = time.perf_counter()
        loop = asyncio.get_running_loop()

        def update(progress: int) -> None:
            self.job_service.update_progress(job_id, progress)

        result = await asyncio.to_thread(
            lambda: self.engine.generate(spec, job_id, update, seed=request_data["seed"], minimum_stems=3),
        )
        processed = await asyncio.to_thread(
            self.audio_processor.process_job,
            self.engine._job_dir(job_id),
            job.duration_s,
            [stem_info["stem_id"] for stem_info in result.get("stems", []) if isinstance(stem_info, dict) and stem_info.get("stem_id")],
        )
        result["sample_rate"] = self.audio_processor.config.target_sample_rate
        result["generation_time_s"] = time.perf_counter() - started
        result["manifest"] = self.audio_processor.build_timeline_manifest(spec, job_id=job_id)
        if request_data.get("narration_url"):
            voiceover = await asyncio.to_thread(NarrationDownloader(self.settings, self.storage).resolve, request_data["narration_url"])
            ducked = await asyncio.to_thread(lambda: self.audio_processor.process_ducked_audio(self.storage.artifact(job_id, "mix.wav"), voiceover, self.storage.artifact(job_id, "mix_ducked.wav", required=False)))
            result["ducking"] = {**ducked["ducking"], "voiceover_path": str(voiceover)}
        if request_data.get("loopable"):
            looped = await asyncio.to_thread(lambda: self.audio_processor.process_looped_audio(self.storage.artifact(job_id, "mix.wav"), self.storage.artifact(job_id, "mix_looped.wav", required=False), bpm=spec.tempo))
            result["loop"] = looped["loop"]
        result["bpm"] = result["manifest"]["bpm"]
        result["key"] = result["manifest"]["key"]
        validation_result = self._refresh_validation(job_id, spec, result)
        result["ps1_evidence"] = self._build_ps1_evidence(job_id=job_id, requested_duration=job.duration_s, job_dir=self.engine._job_dir(job_id), manifest=result["manifest"])
        self.job_service.set_result(job_id, result)
        if not validation_result["valid"]:
            raise AudioProcessingError("VALIDATION_FAILED", "Rendered audio failed Module 8 validation.", {"validation": validation_result})
        result["storage"] = await asyncio.to_thread(self.output_store.publish,job_id,result["manifest"])
        self.job_service.set_result(job_id,result)
        if self.job_service.get_job(job_id).status == JobStatus.CANCELLED:
            return
        self.job_service.transition(job_id, JobStatus.SUCCEEDED)
        logger.info("Music generation completed", extra={"job_id": job_id})

    async def process_loop_job(self, job_id: str) -> None:
        job = self.job_service.get_job(job_id)
        if job.status == JobStatus.CANCELLED:
            return
        if job.status == JobStatus.QUEUED:
            self.job_service.transition(job_id, JobStatus.PROCESSING)
        elif job.status != JobStatus.PROCESSING:
            return

        current = self.job_service.get_result(job_id) or {}
        parent_job_id = job.parent_job_id or job.job_id
        manifest = (self.job_service.get_result(parent_job_id) or {}).get("manifest") or {}
        bpm = int(current.get("bpm", (manifest or {}).get("bpm", 100)))
        beats_per_bar = int(current.get("beats_per_bar", (manifest or {}).get("beats_per_bar", 4)))
        mix_path = self.storage.artifact(parent_job_id, "mix.wav")
        if not mix_path.is_file():
            raise ValueError(f"Music mix not found for loop generation: {mix_path}")

        loop_result = self.audio_processor.process_looped_audio(mix_path, self.storage.artifact(parent_job_id, "mix_looped.wav", required=False), bpm=bpm, beats_per_bar=beats_per_bar)
        self.job_service.set_result(job_id, {**current, "loop": loop_result["loop"], "output_path": loop_result["output_path"], "sample_rate": loop_result["sample_rate"], "duration_s": loop_result["duration_s"]})

        parent_result = self.job_service.get_result(parent_job_id) or {}
        parent_result["loop"] = loop_result["loop"]
        parent_result["looped_mix"] = {"path": loop_result["output_path"], "url": f"/api/v1/music/jobs/{parent_job_id}/loop"}
        self.job_service.set_result(parent_job_id, parent_result)
        _, validation = self._revalidate_parent_job(parent_job_id)
        if not validation["valid"]:
            raise AudioProcessingError("VALIDATION_FAILED", "Looped audio failed validation.", {"validation": validation})
        logger.info("Loop creation completed", extra={"job_id": job_id, "parent_job_id": parent_job_id})

    async def process_voiceover_ducking(self, job_id: str) -> None:
        job = self.job_service.get_job(job_id)
        if job.status == JobStatus.CANCELLED:
            return
        if job.status == JobStatus.QUEUED:
            self.job_service.transition(job_id, JobStatus.PROCESSING)
        elif job.status != JobStatus.PROCESSING:
            return

        current = self.job_service.get_result(job_id) or {}
        voiceover_path = current.get("voiceover_path")
        music_path = current.get("music_path")
        duck_db = float(current.get("duck_db", 12.0))
        attack_ms = float(current.get("attack_ms", 50.0))
        release_ms = float(current.get("release_ms", 300.0))

        if not voiceover_path or not music_path:
            raise ValueError("Voiceover ducking requires both voiceover_path and music_path to be set.")

        output_path = self.storage.artifact(job.parent_job_id or job.job_id, "mix_ducked.wav", required=False)
        result = self.audio_processor.process_ducked_audio(
            music_path,
            voiceover_path,
            output_path,
            duck_db=duck_db,
            attack_ms=attack_ms,
            release_ms=release_ms,
        )
        self.job_service.set_result(job_id, {**current, "ducking": result["ducking"], "output_path": result["output_path"], "speech_regions": result["speech_regions"], "measured_duck_db": result["measured_duck_db"], "duck_db": result["duck_db"], "ducking_status": result["ducking_status"]})

        if job.parent_job_id:
            parent_result = self.job_service.get_result(job.parent_job_id) or {}
            parent_result["ducking"] = result["ducking"]
            parent_result["ducked_mix"] = {"path": result["output_path"], "url": f"/api/v1/music/jobs/{job.parent_job_id}/duck"}
            parent_result["ducking"]["voiceover_path"] = str(voiceover_path)
            parent_result["ducking"]["music_path"] = str(music_path)
            self.job_service.set_result(job.parent_job_id, parent_result)
            _, validation = self._revalidate_parent_job(job.parent_job_id)
            if not validation["valid"]:
                raise AudioProcessingError("VALIDATION_FAILED", "Ducked audio failed validation.", {"validation": validation})

        logger.info("Voiceover ducking completed", extra={"job_id": job_id, "parent_job_id": job.parent_job_id})
