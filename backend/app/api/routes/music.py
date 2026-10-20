from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, Body, File, Header, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi.responses import FileResponse, RedirectResponse

from app.core.config import get_settings
from app.core.http import IntegrationRoute
from app.core.exceptions import AppError
from app.models.job import JobStatus
from app.schemas.music import MusicGenerationRequest, MusicJobAccepted, MusicJobCreate, Ps1ValidationReport, StemListItem, StemListResponse, StemRegenerationAccepted, ValidationRequirement, VoiceoverDuckingRequest
from app.schemas.specification import MusicSpecification
from app.services.job_service import JobService
from app.services.storage import ArtifactStorage
from app.utils.idempotency import request_hash
from app.workers.job_worker import JobWorker

class StemRegenerationRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    preserve: dict[str, bool] = {}


class LoopRequest(BaseModel):
    bpm: int | None = Field(default=None, gt=0, le=240, description="Target BPM for loop alignment")
    beats_per_bar: int | None = Field(default=None, gt=0, le=8, description="Beats per bar for loop alignment")

router = APIRouter(prefix="/api/v1/music", tags=["music"], route_class=IntegrationRoute)
logger = logging.getLogger(__name__)

VALID_STEMS = ("drums", "bass", "harmony", "melody")
REQUIREMENT_DEFINITIONS = (
    ("M1", "Exact Length", "MUST_HAVE"),
    ("M2", "Genuine Independent Stems", "MUST_HAVE"),
    ("M3", "Instrumental", "MUST_HAVE"),
    ("M4", "Commercial Rights", "MUST_HAVE"),
    ("M5", "Professional Audio", "MUST_HAVE"),
    ("M6", "Integration", "MUST_HAVE"),
    ("C1", "Prompt Variety", "CORE"),
    ("C2", "Duration Range", "CORE"),
    ("A1", "Sections", "ADVANCED"),
    ("A2", "Shared Musical Structure", "ADVANCED"),
    ("A3", "Voiceover Ducking", "ADVANCED"),
    ("A4", "Single-Stem Regeneration", "ADVANCED"),
    ("A5", "Seamless Looping", "ADVANCED"),
)


def get_services(request: Request) -> tuple[JobService, JobWorker]:
    return request.app.state.job_service, request.app.state.job_worker


def _report_job_id(service: JobService, job_id: str) -> str:
    job = service.get_job(job_id)
    return job.parent_job_id or job.job_id


def _latest_matching_child(service: JobService, parent_job_id: str, *, match: str | None = None, stem_only: bool = False):
    for candidate in service.jobs_for_parent(parent_job_id):
        if stem_only and candidate.stem_id:
            return candidate
        if match and match in candidate.prompt.lower():
            return candidate
    return None


def _resolve_loop_validation(service: JobService, parent_job_id: str) -> tuple[str | None, dict[str, Any] | None]:
    parent_result = service.get_result(parent_job_id) or {}
    if isinstance(parent_result.get("loop"), dict):
        candidate = _latest_matching_child(service, parent_job_id, match="loop")
        return (candidate.job_id if candidate else parent_job_id), parent_result["loop"]

    candidate = _latest_matching_child(service, parent_job_id, match="loop")
    if not candidate:
        return None, None
    candidate_result = service.get_result(candidate.job_id) or {}
    loop_data = candidate_result.get("loop")
    return (candidate.job_id, loop_data) if isinstance(loop_data, dict) else (None, None)


def _resolve_ducking_validation(service: JobService, parent_job_id: str) -> tuple[str | None, dict[str, Any] | None]:
    parent_result = service.get_result(parent_job_id) or {}
    if isinstance(parent_result.get("ducking"), dict):
        candidate = _latest_matching_child(service, parent_job_id, match="duck")
        return (candidate.job_id if candidate else parent_job_id), parent_result["ducking"]

    candidate = _latest_matching_child(service, parent_job_id, match="duck")
    if not candidate:
        return None, None
    candidate_result = service.get_result(candidate.job_id) or {}
    ducking = candidate_result.get("ducking")
    return (candidate.job_id, ducking) if isinstance(ducking, dict) else (None, None)


def _ps1_evidence(result: dict[str, Any]) -> dict[str, Any]:
    evidence = result.get("ps1_evidence")
    return evidence if isinstance(evidence, dict) else {}


def _warning_requirement(requirement_id: str, name: str, category: str, summary: str, evidence: dict[str, Any], associated_job_id: str | None = None) -> ValidationRequirement:
    return ValidationRequirement(
        requirement_id=requirement_id,
        name=name,
        category=category,
        status="WARNING",
        summary=summary,
        evidence=evidence,
        associated_job_id=associated_job_id,
    )


def _measure_current_artifacts(service: JobService, worker: JobWorker, job_id: str) -> dict:
    root_id = _report_job_id(service, job_id)
    worker.output_store.restore(root_id,(service.get_result(root_id) or {}).get("storage",{}))
    specification = service.get_specification(root_id)
    if not specification:
        raise AppError("VALIDATION_NOT_READY", "No music specification is available for artifact validation.", False, {}, 409)
    return worker.validation_engine.validate_job(
        worker.engine._job_dir(root_id), MusicSpecification.model_validate(specification),
        service.get_result(root_id) or {}, require_manifest_file=True,
        operation_inputs=worker.validation_operation_inputs(root_id),
    )


def _build_ps1_validation_report(service: JobService, worker: JobWorker, job_id: str) -> Ps1ValidationReport:
    root_job_id = _report_job_id(service, job_id)
    root_job = service.get_job(root_job_id)
    if root_job.status != JobStatus.SUCCEEDED:
        raise AppError("VALIDATION_NOT_READY", "PS1 validation is available only after the music job succeeds.", True, {"status": root_job.status.value}, 409)

    result = service.get_result(root_job_id) or {}
    manifest = result.get("manifest") if isinstance(result.get("manifest"), dict) else {}
    generation_validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    measured = _measure_current_artifacts(service, worker, root_job_id)
    audio_validation = measured["audio_validation"]
    ps1_evidence = _ps1_evidence(result)
    loop_job_id, loop_validation = _resolve_loop_validation(service, root_job_id)
    duck_job_id, ducking_validation = _resolve_ducking_validation(service, root_job_id)
    if measured["checks"]["looping"].get("enabled") or loop_validation:
        loop_validation = {**measured["checks"]["looping"], "status": "PASS" if measured["checks"]["looping"]["pass"] else "FAIL"}
    if measured["checks"]["ducking"].get("enabled") or ducking_validation:
        ducking_validation = {**measured["checks"]["ducking"], "ducking_status": "PASS" if measured["checks"]["ducking"]["pass"] else "FAIL",
                              "measured_duck_db": measured["checks"]["ducking"].get("ducking_db", 0.0)}
    regeneration_job = _latest_matching_child(service, root_job_id, stem_only=True)
    regeneration_result = service.get_result(regeneration_job.job_id) if regeneration_job else None

    requirements: list[ValidationRequirement] = []

    duration_evidence = measured["checks"]["duration"]
    if "actual_s" in duration_evidence:
        requested_duration = duration_evidence["requested_s"]
        actual_duration = duration_evidence["actual_s"]
        duration_tolerance = duration_evidence["tolerance_s"]
        m1_status = "PASS" if duration_evidence["pass"] else "FAIL"
        requirements.append(ValidationRequirement(
            requirement_id="M1",
            name="Exact Length",
            category="MUST_HAVE",
            status="PASS" if m1_status == "PASS" else "FAIL",
            summary=f"Requested {requested_duration:.2f} sec, actual {actual_duration:.2f} sec, tolerance ±{duration_tolerance:.2f} sec.",
            evidence={
                "requested_duration_seconds": requested_duration,
                "actual_duration_seconds": actual_duration,
                "duration_error_seconds": float(audio_validation.get("duration_error_seconds", abs(actual_duration - requested_duration))),
                "duration_tolerance_seconds": duration_tolerance,
                "status": m1_status,
            },
            associated_job_id=root_job_id,
        ))
    else:
        requirements.append(ValidationRequirement(
            requirement_id="M1", name="Exact Length", category="MUST_HAVE", status="FAIL",
            summary="Rendered mix duration could not be measured.", evidence=duration_evidence, associated_job_id=root_job_id))

    independence = generation_validation.get("independence") if isinstance(generation_validation.get("independence"), dict) else {}
    if independence:
        independence_values = list(independence.values())
        m2_pass = all(value == "PASS" for value in independence_values)
        requirements.append(ValidationRequirement(
            requirement_id="M2",
            name="Genuine Independent Stems",
            category="MUST_HAVE",
            status="PASS" if m2_pass else "FAIL",
            summary=f"Recorded {len(independence_values)} stem-pair independence checks from the generated stems.",
            evidence={
                "sample_rate": generation_validation.get("sample_rate"),
                "duration_seconds": generation_validation.get("duration_seconds"),
                "independence": independence,
                "stems": generation_validation.get("stems", {}),
            },
            associated_job_id=root_job_id,
        ))
    else:
        requirements.append(_warning_requirement("M2", "Genuine Independent Stems", "MUST_HAVE", "No persisted stem-independence evidence is stored for this job.", {"available": False}, root_job_id))

    instrumental_evidence = ps1_evidence.get("instrumental") if isinstance(ps1_evidence.get("instrumental"), dict) else {}
    instrumental_pass = bool(instrumental_evidence) and not bool(instrumental_evidence.get("vocal_detected"))
    if instrumental_pass:
        requirements.append(ValidationRequirement(
            requirement_id="M3",
            name="Instrumental",
            category="MUST_HAVE",
            status="PASS",
            summary=f"Speech detector found {len(instrumental_evidence.get('speech_regions', []))} vocal regions in the generated mix.",
            evidence=instrumental_evidence,
            associated_job_id=root_job_id,
        ))
    else:
        requirements.append(_warning_requirement(
            "M3",
            "Instrumental",
            "MUST_HAVE",
            "No vocal-detection measurement is persisted in backend job results for this job.",
            {"available": False, "reason": "instrumental validation data is not stored by the backend"},
            root_job_id,
        ))

    commercial_rights = manifest.get("commercial_rights") if isinstance(manifest.get("commercial_rights"), dict) else {}
    if not commercial_rights and isinstance(ps1_evidence.get("commercial_rights"), dict):
        commercial_rights = ps1_evidence["commercial_rights"]
    commercial_pass = bool(commercial_rights) and bool(commercial_rights.get("commercial_use_permitted")) and not bool(commercial_rights.get("third_party_material_used"))
    if commercial_pass:
        requirements.append(ValidationRequirement(
            requirement_id="M4",
            name="Commercial Rights",
            category="MUST_HAVE",
            status="PASS",
            summary=f"License metadata '{commercial_rights.get('license_name')}' records backend-generated commercial-use permission.",
            evidence=commercial_rights,
            associated_job_id=root_job_id,
        ))
    else:
        requirements.append(_warning_requirement(
            "M4",
            "Commercial Rights",
            "MUST_HAVE",
            "No commercial-rights or license evidence is persisted in the generation result or manifest.",
            {"available": False, "reason": "license metadata is not present in backend job results"},
            root_job_id,
        ))

    if audio_validation:
        m5_status = str(audio_validation.get("overall_status", "FAIL"))
        requirements.append(ValidationRequirement(
            requirement_id="M5",
            name="Professional Audio",
            category="MUST_HAVE",
            status="PASS" if m5_status == "PASS" else "FAIL",
            summary=(
                f"{int(audio_validation.get('sample_rate', 0))} Hz, "
                f"{float(audio_validation.get('measured_lufs', 0.0)):.2f} LUFS, "
                f"{float(audio_validation.get('true_peak_dbTP', 0.0)):.2f} dBTP, "
                f"clipping {'detected' if bool(audio_validation.get('clipping_detected')) else 'not detected'}."
            ) if "measured_lufs" in audio_validation else "Rendered mix audio measurements could not be completed.",
            evidence=audio_validation,
            associated_job_id=root_job_id,
        ))
    else:
        requirements.append(_warning_requirement("M5", "Professional Audio", "MUST_HAVE", "No Module 5 audio-validation metrics are stored for this job.", {"available": False}, root_job_id))

    stems = result.get("stems") if isinstance(result.get("stems"), list) else []
    manifest_present = bool(manifest)
    requested_stem_count = measured["checks"]["stems"].get("expected_count", 0)
    integration_pass = measured["checks"]["required_outputs"]["pass"] and measured["checks"]["stems"]["pass"]
    requirements.append(ValidationRequirement(
        requirement_id="M6",
        name="Integration",
        category="MUST_HAVE",
        status="PASS" if integration_pass else "FAIL",
        summary=f"Measured {measured['checks']['stems'].get('actual_count', 0)} stem files against {requested_stem_count} requested instruments; outputs {'passed' if integration_pass else 'failed'} filesystem validation.",
        evidence={
            "job_status": root_job.status.value,
            "stems_count": measured["checks"]["stems"].get("actual_count", 0),
            "requested_instruments": manifest.get("instruments", []),
            "requested_stem_count": requested_stem_count,
            "genre": manifest.get("genre"),
            "mood": manifest.get("mood"),
            "manifest_present": manifest_present,
            "ducked_mix_available": isinstance(result.get("ducked_mix"), dict),
            "looped_mix_available": isinstance(result.get("looped_mix"), dict),
        },
        associated_job_id=root_job_id,
    ))

    prompt_variety = ps1_evidence.get("prompt_variety") if isinstance(ps1_evidence.get("prompt_variety"), dict) else {}
    if prompt_variety and prompt_variety.get("status") == "PASS":
        requirements.append(ValidationRequirement(
            requirement_id="C1",
            name="Prompt Variety",
            category="CORE",
            status="PASS",
            summary=f"Deterministic benchmark evaluated {int(prompt_variety.get('prompt_count', 0))} meaningfully different prompts and found {int(prompt_variety.get('distinct_signature_count', 0))} distinct outputs.",
            evidence=prompt_variety,
            associated_job_id=root_job_id,
        ))
    else:
        requirements.append(_warning_requirement(
            "C1",
            "Prompt Variety",
            "CORE",
            "No benchmark prompt-variety suite results are persisted for this job.",
            {"available": False, "reason": "benchmark prompt-validation evidence is not stored by the backend"},
            root_job_id,
        ))

    duration_range = ps1_evidence.get("duration_range") if isinstance(ps1_evidence.get("duration_range"), dict) else {}
    if duration_range and duration_range.get("status") == "PASS":
        requirements.append(ValidationRequirement(
            requirement_id="C2",
            name="Duration Range",
            category="CORE",
            status="PASS",
            summary=(
                f"Benchmark covered {duration_range.get('limits', {}).get('min_duration_seconds')}–{duration_range.get('limits', {}).get('max_duration_seconds')} seconds, "
                f"and the requested duration {float(duration_range.get('requested_duration_seconds', root_job.duration_s)):.2f} seconds is within range."
            ),
            evidence=duration_range,
            associated_job_id=root_job_id,
        ))
    else:
        requirements.append(_warning_requirement(
            "C2",
            "Duration Range",
            "CORE",
            "No cross-duration suite evidence is persisted for this job.",
            {"available": False, "requested_duration_seconds": root_job.duration_s},
            root_job_id,
        ))

    sections = manifest.get("sections") if isinstance(manifest.get("sections"), list) else []
    if sections:
        contiguous = True
        previous_end = 0.0
        for section in sections:
            start_s = float(section.get("start_s", section.get("start", previous_end)))
            end_s = float(section.get("end_s", section.get("end", start_s)))
            if start_s + 1e-6 < previous_end or end_s < start_s:
                contiguous = False
                break
            previous_end = end_s
        requirements.append(ValidationRequirement(
            requirement_id="A1",
            name="Sections",
            category="ADVANCED",
            status="PASS" if contiguous else "FAIL",
            summary=f"Manifest contains {len(sections)} timed sections ending at {float(manifest.get('duration_s', previous_end)):.2f} sec.",
            evidence={
                "duration_s": manifest.get("duration_s"),
                "sections": sections,
                "contiguous": contiguous,
            },
            associated_job_id=root_job_id,
        ))
    else:
        requirements.append(_warning_requirement("A1", "Sections", "ADVANCED", "No timeline sections are stored in the manifest for this job.", {"available": False}, root_job_id))

    structure_available = bool(manifest.get("bpm")) and bool(manifest.get("key")) and bool(manifest.get("beats_per_bar"))
    if structure_available:
        requirements.append(ValidationRequirement(
            requirement_id="A2",
            name="Shared Musical Structure",
            category="ADVANCED",
            status="PASS",
            summary=(
                f"Manifest records {int(manifest.get('bpm', 0))} BPM, {manifest.get('key')}, "
                f"{int(manifest.get('beats_per_bar', 0))} beats per bar."
            ),
            evidence={
                "bpm": manifest.get("bpm"),
                "key": manifest.get("key"),
                "genre": manifest.get("genre"),
                "mood": manifest.get("mood"),
                "instruments": manifest.get("instruments"),
                "beats_per_bar": manifest.get("beats_per_bar"),
                "beat_duration_s": manifest.get("beat_duration_s"),
                "bar_duration_s": manifest.get("bar_duration_s"),
                "sections": sections,
            },
            associated_job_id=root_job_id,
        ))
    else:
        requirements.append(_warning_requirement("A2", "Shared Musical Structure", "ADVANCED", "No complete manifest structure is persisted for this job.", {"available": False}, root_job_id))

    if ducking_validation:
        duck_status = str(ducking_validation.get("ducking_status", "WARNING"))
        requirement_status = "PASS" if duck_status == "PASS" else "WARNING" if duck_status == "NO_SPEECH" else "FAIL"
        requirements.append(ValidationRequirement(
            requirement_id="A3",
            name="Voiceover Ducking",
            category="ADVANCED",
            status=requirement_status,
            summary=(
                f"Ducking status {duck_status}, requested {float(ducking_validation.get('duck_db', 0.0)):.2f} dB, "
                f"measured {float(ducking_validation.get('measured_duck_db', 0.0)):.2f} dB."
            ),
            evidence=ducking_validation,
            associated_job_id=duck_job_id,
        ))
    else:
        requirements.append(_warning_requirement("A3", "Voiceover Ducking", "ADVANCED", "No ducking validation data is stored for this job.", {"available": False}, root_job_id))

    regeneration_evidence = ps1_evidence.get("stem_regeneration") if isinstance(ps1_evidence.get("stem_regeneration"), dict) else {}
    if not regeneration_evidence and regeneration_result and isinstance(regeneration_result.get("stem_regeneration"), dict):
        regeneration_evidence = regeneration_result["stem_regeneration"]
    if regeneration_job and regeneration_evidence:
        regeneration_status = "PASS" if regeneration_job.status == JobStatus.SUCCEEDED and regeneration_evidence.get("status") == "PASS" else "FAIL"
        requirements.append(ValidationRequirement(
            requirement_id="A4",
            name="Single-Stem Regeneration",
            category="ADVANCED",
            status=regeneration_status,
            summary=f"Regeneration job {regeneration_job.job_id} targeted stem {regeneration_job.stem_id or 'unknown'} and persisted stem and mix validation evidence.",
            evidence=regeneration_evidence,
            associated_job_id=regeneration_job.job_id,
        ))
    else:
        requirements.append(_warning_requirement("A4", "Single-Stem Regeneration", "ADVANCED", "No stem-regeneration job has been recorded for this music job.", {"available": False}, root_job_id))

    if loop_validation:
        loop_status = str(loop_validation.get("status", "FAIL"))
        requirements.append(ValidationRequirement(
            requirement_id="A5",
            name="Seamless Looping",
            category="ADVANCED",
            status="PASS" if loop_status == "PASS" else "FAIL",
            summary=(
                f"Loop {float(loop_validation.get('loop_start_s', 0.0)):.2f}–{float(loop_validation.get('loop_end_s', 0.0)):.2f} sec, "
                f"duration {float(loop_validation.get('loop_duration_s', 0.0)):.2f} sec, discontinuity {float(loop_validation.get('boundary_discontinuity', 0.0)):.6f}."
            ),
            evidence=loop_validation,
            associated_job_id=loop_job_id,
        ))
    else:
        requirements.append(_warning_requirement("A5", "Seamless Looping", "ADVANCED", "No loop validation data is stored for this job.", {"available": False}, root_job_id))

    passed_count = sum(1 for requirement in requirements if requirement.status == "PASS")
    warning_count = sum(1 for requirement in requirements if requirement.status == "WARNING")
    failed_count = sum(1 for requirement in requirements if requirement.status == "FAIL")
    overall_status = "FAIL" if failed_count else "WARNING" if warning_count else "PASS"
    percentage = round((passed_count / len(requirements)) * 100, 2) if requirements else 0.0

    return Ps1ValidationReport(
        job_id=root_job_id,
        overall_status=overall_status,
        passed_count=passed_count,
        warning_count=warning_count,
        failed_count=failed_count,
        percentage=percentage,
        validated_at=datetime.now(timezone.utc).isoformat(),
        requirements=requirements,
    )


@router.post("/generate", response_model=MusicJobAccepted, status_code=status.HTTP_202_ACCEPTED, summary="Generate independent music stems from a validated Module 2 specification")
async def generate_music(payload: MusicGenerationRequest, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> MusicJobAccepted:
    settings = get_settings()
    specification = payload.specification
    duration = max(section.end for section in specification.sections)
    if duration < settings.min_duration_seconds or duration > settings.max_duration_seconds:
        raise AppError("INVALID_DURATION", "The specification duration is outside the configured limits.", False, {"min": settings.min_duration_seconds, "max": settings.max_duration_seconds}, 422)
    if idempotency_key and len(idempotency_key) > 255:
        raise AppError("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key must be 255 characters or fewer.", False, {}, 422)

    service, worker = get_services(request)
    spec_dict = specification.model_dump()
    request_payload = {"specification": spec_dict}
    if payload.seed is not None:
        request_payload["seed"] = payload.seed
    job = service.create_job(
        prompt=f"Module 3 generation: {specification.genre}, {', '.join(specification.mood)}",
        duration_s=duration,
        idempotency_key=idempotency_key,
        request_hash=request_hash(request_payload),
    )
    # Only attach the validated specification. The worker never asks the LLM for audio instructions.
    if service.get_specification(job.job_id) is None:
        service.attach_specification(job.job_id, spec_dict)
    service.attach_request(job.job_id, MusicJobCreate(prompt=job.prompt, duration_s=duration, seed=settings.audio_seed if payload.seed is None else payload.seed).model_dump())
    await worker.submit(job.job_id)
    logger.info("Module 3 music generation accepted", extra={"job_id": job.job_id, "correlation_id": getattr(request.state, "correlation_id", None)})
    return MusicJobAccepted(job_id=job.job_id, status=job.status.value)


@router.post("/jobs", response_model=MusicJobAccepted, status_code=status.HTTP_202_ACCEPTED, summary="Plan, generate, validate and persist an asynchronous music job")
async def create_music_job(payload: MusicJobCreate, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> MusicJobAccepted:
    settings = get_settings()
    if len(payload.prompt) > settings.max_prompt_length:
        raise AppError("PROMPT_TOO_LONG", f"prompt must be {settings.max_prompt_length} characters or fewer.", False, {"max_length": settings.max_prompt_length}, 422)
    if payload.duration_s < settings.min_duration_seconds or payload.duration_s > settings.max_duration_seconds:
        raise AppError("INVALID_DURATION", f"duration_s must be between {settings.min_duration_seconds:g} and {settings.max_duration_seconds:g} seconds.", False, {"min": settings.min_duration_seconds, "max": settings.max_duration_seconds}, 422)
    if idempotency_key and len(idempotency_key) > 255:
        raise AppError("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key must be 255 characters or fewer.", False, {}, 422)
    service, worker = get_services(request)
    payload_dict = payload.model_dump()
    job = service.create_job(payload.prompt, payload.duration_s, idempotency_key, request_hash(payload_dict))
    service.attach_request(job.job_id, payload_dict)
    await worker.submit(job.job_id)
    return MusicJobAccepted(job_id=job.job_id, status=job.status.value)


def _generated_stems(service: JobService, job_id: str) -> list[dict]:
    result = service.get_result(job_id) or {}
    stems = result.get("stems") if isinstance(result.get("stems"), list) else []
    return [stem for stem in stems if isinstance(stem, dict)]


def _generated_stem_map(service: JobService, job_id: str) -> dict[str, dict]:
    stems = _generated_stems(service, job_id)
    stem_map: dict[str, dict] = {}
    for stem in stems:
        stem_id = str(stem.get("stem_id") or stem.get("name") or "").strip()
        if stem_id:
            stem_map[stem_id] = stem
    return stem_map


def _stem_path(service: JobService, job_id: str, stem_id: str) -> Path:
    stem_map = _generated_stem_map(service, job_id)
    stem = stem_map.get(stem_id)
    if not stem:
        raise AppError("INVALID_STEM", f"Unsupported stem '{stem_id}'.", False, {"stem_id": stem_id, "allowed": sorted(stem_map)}, 422)
    canonical = f"stems/{stem_id}.wav"
    if ArtifactStorage(get_settings().audio_output_dir).artifact(job_id, canonical, required=False).is_file():
        return _audio_path(job_id, canonical)
    return _audio_path(job_id, str(stem.get("filename") or f"{stem_id}.wav"))


def _cache_version(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _stem_url(request: Request, job_id: str, stem_id: str, path: Path) -> str:
    return f"{request.base_url}api/v1/music/jobs/{job_id}/stems/{stem_id}?v={_cache_version(path)}"


def _audio_path(job_id: str, filename: str) -> Path:
    settings = get_settings()
    if Path(filename).suffix.lower() != ".wav":
        raise AppError("AUDIO_NOT_FOUND", "Requested audio file does not exist.", False, {}, 404)
    storage = ArtifactStorage(settings.audio_output_dir)
    path = storage.artifact(job_id, filename)
    storage.wav_info(job_id, filename)
    return path


def _published_download(request: Request,job_id: str,relative: str):
    service,worker = get_services(request)
    record = (service.get_result(job_id) or {}).get("storage",{})
    if record.get("backend") == "s3":
        worker.storage.artifact(job_id,relative,required=False)
        return RedirectResponse(worker.output_store.download_url(relative,record),status_code=307)
    return None


@router.get("/jobs/{job_id}/manifest", summary="Download the measured music manifest")
async def download_manifest(job_id: str, request: Request):
    service, worker = get_services(request)
    root_id = _report_job_id(service, job_id)
    if service.get_job(root_id).status != JobStatus.SUCCEEDED:
        raise AppError("ARTIFACT_NOT_READY", "Job has not succeeded.", True, {}, 409)
    remote = _published_download(request,root_id,"music_manifest.json")
    if remote is not None:
        return remote
    path = worker.storage.artifact(root_id, "music_manifest.json")
    manifest = worker.storage.read_json(root_id, "music_manifest.json")
    worker.storage.verify_manifest(root_id, manifest)
    return FileResponse(path, media_type="application/json", filename="music_manifest.json")


@router.get("/jobs/{job_id}/artifacts/{artifact:path}", summary="Download a published job artifact")
async def download_artifact(job_id: str, artifact: str, request: Request):
    service, worker = get_services(request)
    job = service.get_job(job_id)
    if job.status != JobStatus.SUCCEEDED:
        raise AppError("ARTIFACT_NOT_READY","Job has not succeeded.",True,{},409)
    result = service.get_result(job_id) or {}
    record = result.get("storage",{})
    worker.storage.artifact(job_id,artifact,required=False)
    if record.get("backend") == "s3":
        return RedirectResponse(worker.output_store.download_url(artifact,record),status_code=307)
    raise AppError("ARTIFACT_NOT_FOUND","This artifact has not been published to object storage.",False,{},404)


@router.get("/jobs/{job_id}/mix.mp3", summary="Download the requested MP3 mix")
async def download_mp3(job_id: str, request: Request):
    service, worker = get_services(request)
    root_id = _report_job_id(service, job_id)
    if service.get_job(root_id).status != JobStatus.SUCCEEDED:
        raise AppError("ARTIFACT_NOT_READY", "Job has not succeeded.", True, {}, 409)
    remote = _published_download(request,root_id,"mix.mp3")
    if remote is not None:
        return remote
    return FileResponse(worker.storage.artifact(root_id, "mix.mp3"), media_type="audio/mpeg", filename="mix.mp3")


@router.get("/jobs/{job_id}/stems", response_model=StemListResponse, summary="List independent stems for a completed Module 3 job")
async def list_stems(job_id: str, request: Request) -> StemListResponse:
    service: JobService = request.app.state.job_service
    job = service.get_job(job_id)
    if job.status != JobStatus.SUCCEEDED:
        raise AppError("STEM_NOT_READY", "Stems are available only after the generation job succeeds.", True, {"status": job.status.value}, 409)
    specification = service.get_specification(job_id)
    if not specification:
        raise AppError("STEM_NOT_READY", "This job does not contain a Module 3 specification.", False, {}, 409)

    items: list[StemListItem] = []
    stem_map = _generated_stem_map(service, job_id)
    storage_record = (service.get_result(job_id) or {}).get("storage",{})
    if storage_record.get("backend") == "s3":
        return StemListResponse(job_id=job_id,stems=[StemListItem(stem_id=stem_id,name=str(stem.get("name") or stem_id),filename=f"{stem_id}.wav",url=f"{request.base_url}api/v1/music/jobs/{job_id}/artifacts/stems/{stem_id}.wav?v={storage_record['objects'][f'stems/{stem_id}.wav']['sha256']}") for stem_id,stem in stem_map.items()],mix_url=f"{request.base_url}api/v1/music/jobs/{job_id}/artifacts/mix.wav?v={storage_record['objects']['mix.wav']['sha256']}")
    for stem_id, stem in stem_map.items():
        path = _stem_path(service, job_id, stem_id)
        items.append(StemListItem(stem_id=stem_id, name=str(stem.get("name") or stem_id), filename=path.name, url=_stem_url(request, job_id, stem_id, path)))
    mix_path = _audio_path(job_id, "mix.wav")
    return StemListResponse(job_id=job_id, stems=items, mix_url=f"{request.base_url}api/v1/music/jobs/{job_id}/mix?v={_cache_version(mix_path)}")


@router.post(
    "/jobs/{job_id}/stems/{stem_id}/regenerate",
    response_model=StemRegenerationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Regenerate exactly one independent stem",
)
async def regenerate_stem(
    job_id: str,
    stem_id: str,
    request: Request,
    payload: dict = Body(default={}),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
) -> StemRegenerationAccepted:

    service, worker = get_services(request)

    available_stems = _generated_stem_map(service, job_id)
    if stem_id not in available_stems:
        raise AppError(
            "INVALID_STEM",
            f"Unsupported stem '{stem_id}'.",
            False,
            {
                "stem_id": stem_id,
                "allowed": sorted(available_stems),
            },
            422,
        )

    instruction = str(
        payload.get("instruction", "")
    ).strip()

    preserve = payload.get("preserve", {})

    if len(instruction) > 2000:
        raise AppError(
            "INSTRUCTION_TOO_LONG",
            "Regeneration instruction must be 2000 characters or fewer.",
            False,
            {"max_length": 2000},
            422,
        )

    parent = service.get_job(job_id)

    if parent.status != JobStatus.SUCCEEDED:
        raise AppError(
            "STEM_NOT_READY",
            "A stem can be regenerated only after the parent job succeeds.",
            True,
            {"status": parent.status.value},
            409,
        )

    specification = service.get_specification(job_id)

    if not specification:
        raise AppError(
            "STEM_NOT_READY",
            "The completed job has no Module 3 specification.",
            False,
            {},
            409,
        )

    existing = service.active_stem_regeneration(job_id, stem_id)

    if existing:
        return StemRegenerationAccepted(
            job_id=existing.job_id,
            stem_id=stem_id,
            status=existing.status.value,
        )

    # Store the actual user instruction in the regeneration job.
    if not instruction:
        instruction = (
            f"Regenerate the {stem_id} stem while preserving "
            "the current musical context."
        )

    request_payload = {
        "parent_job_id": job_id,
        "stem_id": stem_id,
        "instruction": instruction,
        "preserve": preserve,
    }

    regeneration_job = service.create_job(
        prompt=instruction,
        duration_s=parent.duration_s,
        idempotency_key=idempotency_key,
        request_hash=request_hash(request_payload),
        stem_id=stem_id,
        parent_job_id=job_id,
        regeneration_instruction=instruction,
        preserve_options=preserve,
    )

    # Save the regeneration metadata so the worker can use it.
    service.update_result(
        regeneration_job.job_id,
        {
            "instruction": instruction,
            "preserve": preserve,
        },
    )

    await worker.submit(regeneration_job.job_id)

    return StemRegenerationAccepted(
        job_id=regeneration_job.job_id,
        stem_id=stem_id,
        status=regeneration_job.status.value,
    )

@router.post("/upload", summary="Upload a voiceover or audio asset for downstream processing")
async def upload_audio(request: Request, file: UploadFile = File(...)) -> dict:
    if not file.filename:
        raise AppError("INVALID_FILE", "Uploaded file is missing a filename.", False, {}, 400)
    allowed = {".wav", ".mp3", ".m4a", ".flac", ".aac"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in allowed:
        raise AppError("INVALID_FILE", "Unsupported audio type. Use WAV, MP3, M4A, AAC, or FLAC.", False, {"allowed": sorted(allowed)}, 415)

    settings = get_settings()
    upload_root = ArtifactStorage(settings.audio_output_dir).uploads_dir
    upload_root.mkdir(parents=True, exist_ok=True)
    destination = upload_root / f"{uuid.uuid4().hex}{suffix}"
    contents = await file.read()
    destination.write_bytes(contents)
    return {
        "filename": file.filename,
        "path": str(destination),
        "size_bytes": len(contents),
        "url": f"{request.base_url}api/v1/music/files/{destination.name}",
    }


@router.post(
    "/jobs/{job_id}/duck",
    response_model=MusicJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create an asynchronous voiceover ducking job for the generated mix",
)
async def duck_music_job(
    job_id: str,
    payload: VoiceoverDuckingRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> MusicJobAccepted:
    service, worker = get_services(request)
    parent = service.get_job(job_id)
    if parent.status != JobStatus.SUCCEEDED:
        raise AppError("MUSIC_NOT_READY", "Voiceover ducking is available only after the parent music job succeeds.", True, {"status": parent.status.value}, 409)

    settings = get_settings()
    voice_path = Path(payload.voiceover_path).expanduser()
    if not voice_path.is_absolute():
        voice_path = (Path.cwd() / voice_path).resolve()
    if not voice_path.is_file():
        raise AppError("VOICEOVER_NOT_FOUND", "Voiceover file does not exist.", False, {"voiceover_path": str(voice_path)}, 404)

    music_path = Path(payload.music_path).expanduser() if payload.music_path else ArtifactStorage(settings.audio_output_dir).artifact(job_id, "mix.wav")
    if not music_path.is_absolute():
        music_path = (Path.cwd() / music_path).resolve()
    if not music_path.is_file():
        raise AppError("MUSIC_NOT_FOUND", "Music file does not exist.", False, {"music_path": str(music_path)}, 404)

    request_payload = {
        "parent_job_id": job_id,
        "voiceover_path": str(voice_path),
        "music_path": str(music_path),
        "duck_db": payload.duck_db,
        "attack_ms": payload.attack_ms,
        "release_ms": payload.release_ms,
    }
    duck_job = service.create_job(
        prompt="Module 6: voiceover ducking",
        duration_s=parent.duration_s,
        idempotency_key=idempotency_key,
        request_hash=request_hash(request_payload),
        parent_job_id=job_id,
    )
    service.update_result(
        duck_job.job_id,
        {
            "voiceover_path": str(voice_path),
            "music_path": str(music_path),
            "duck_db": payload.duck_db,
            "attack_ms": payload.attack_ms,
            "release_ms": payload.release_ms,
        },
    )
    await worker.submit(duck_job.job_id)
    return MusicJobAccepted(job_id=duck_job.job_id, status=duck_job.status.value)


@router.post("/jobs/{job_id}/loop", response_model=MusicJobAccepted, status_code=status.HTTP_202_ACCEPTED, summary="Create an async looped-version job for a completed music mix")
async def create_loop_job(job_id: str, payload: LoopRequest, request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> MusicJobAccepted:
    service, worker = get_services(request)
    parent = service.get_job(job_id)
    if parent.status != JobStatus.SUCCEEDED:
        raise AppError("MUSIC_NOT_READY", "Looping is available only after the parent music job succeeds.", True, {"status": parent.status.value}, 409)

    current = service.get_result(job_id) or {}
    resolved_bpm = payload.bpm or int(current.get("bpm", 100))
    resolved_beats_per_bar = payload.beats_per_bar or int(current.get("beats_per_bar", 4))
    request_payload = {"parent_job_id": job_id, "bpm": resolved_bpm, "beats_per_bar": resolved_beats_per_bar}
    loop_job = service.create_job(
        prompt=f"Module 7: looped mix for {job_id}",
        duration_s=parent.duration_s,
        idempotency_key=idempotency_key,
        request_hash=request_hash(request_payload),
        parent_job_id=job_id,
    )
    service.update_result(loop_job.job_id, {"bpm": resolved_bpm, "beats_per_bar": resolved_beats_per_bar, "music_job_id": job_id})
    await worker.submit(loop_job.job_id)
    return MusicJobAccepted(job_id=loop_job.job_id, status=loop_job.status.value)


@router.get("/jobs/{job_id}/loop", summary="Stream the looped mix for a music job")
async def get_looped_mix(job_id: str, request: Request):
    service: JobService = request.app.state.job_service
    job = service.get_job(job_id)
    if job.status != JobStatus.SUCCEEDED:
        raise AppError("AUDIO_NOT_READY", "The looped mix is available only after the loop job succeeds.", True, {"status": job.status.value}, 409)

    settings = get_settings()
    resolved_job_id = job.parent_job_id or job.job_id
    remote = _published_download(request,resolved_job_id,"mix_looped.wav")
    if remote is not None:
        return remote
    path = ArtifactStorage(settings.audio_output_dir).artifact(resolved_job_id, "mix_looped.wav", required=False)
    if not path.is_file():
        raise AppError("AUDIO_NOT_FOUND", "The looped mix has not been generated for this job yet.", False, {"job_id": job_id}, 404)
    return FileResponse(path, media_type="audio/wav", filename="mix_looped.wav")


@router.get("/jobs/{job_id}/loop/validation", summary="Return Module 7 loop validation metadata")
async def get_loop_validation(job_id: str, request: Request) -> dict:
    service: JobService = request.app.state.job_service
    job = service.get_job(job_id)
    if job.status != JobStatus.SUCCEEDED:
        raise AppError("VALIDATION_NOT_READY", "Loop validation is available only after the job succeeds.", True, {"status": job.status.value}, 409)

    result = service.get_result(job_id) or {}
    loop_data = result.get("loop")

    if not loop_data and job.parent_job_id:
        loop_data = next(
            (
                candidate_result.get("loop")
                for candidate in service.jobs_for_parent(job.parent_job_id)
                if (candidate_result := service.get_result(candidate.job_id)) and candidate_result.get("loop")
            ),
            None,
        )

    if not loop_data:
        for candidate in service.jobs_for_parent(job_id):
            candidate_result = service.get_result(candidate.job_id) or {}
            loop_data = candidate_result.get("loop")
            if loop_data:
                break

    if not loop_data:
        raise AppError("VALIDATION_NOT_READY", "This job does not contain Module 7 loop validation results.", False, {}, 409)

    measured = _measure_current_artifacts(service, request.app.state.job_worker, job_id)["checks"]["looping"]
    return {"job_id": job_id, "loop": {**measured, "status": "PASS" if measured["pass"] else "FAIL"}}


@router.get("/jobs/{job_id}/duck", summary="Stream the ducked mix for a music job")
async def get_ducked_mix(job_id: str, request: Request):
    service: JobService = request.app.state.job_service
    job = service.get_job(job_id)
    if job.status != JobStatus.SUCCEEDED:
        raise AppError("AUDIO_NOT_READY", "The ducked mix is available only after the parent job succeeds.", True, {"status": job.status.value}, 409)

    settings = get_settings()
    resolved_job_id = job.parent_job_id or job.job_id
    remote = _published_download(request,resolved_job_id,"mix_ducked.wav")
    if remote is not None:
        return remote
    path = ArtifactStorage(settings.audio_output_dir).artifact(resolved_job_id, "mix_ducked.wav", required=False)
    if not path.is_file():
        raise AppError("AUDIO_NOT_FOUND", "The ducked mix has not been generated for this job yet.", False, {"job_id": job_id, "resolved_job_id": resolved_job_id}, 404)
    return FileResponse(path, media_type="audio/wav", filename="mix_ducked.wav")


@router.get("/jobs/{job_id}/duck/validation", summary="Return Module 6 ducking validation metadata")
async def get_ducking_validation(job_id: str, request: Request) -> dict:
    service: JobService = request.app.state.job_service
    job = service.get_job(job_id)
    if job.status != JobStatus.SUCCEEDED:
        raise AppError("VALIDATION_NOT_READY", "Ducking validation is available only after the job succeeds.", True, {"status": job.status.value}, 409)
    result = service.get_result(job_id) or {}
    ducking = result.get("ducking")
    if not ducking:
        raise AppError("VALIDATION_NOT_READY", "This job does not contain Module 6 validation results.", False, {}, 409)
    measured = _measure_current_artifacts(service, request.app.state.job_worker, job_id)["checks"]["ducking"]
    return {"job_id": job_id, "ducking": {**measured, "ducking_status": "PASS" if measured["pass"] else "FAIL"},
        "speech_regions": measured.get("speech_regions", [])}


@router.get("/files/{filename}", summary="Stream an uploaded voiceover or audio asset")
async def get_uploaded_file(filename: str, request: Request):
    settings = get_settings()
    upload_root = ArtifactStorage(settings.audio_output_dir).uploads_dir
    path = (upload_root / filename).resolve()
    if upload_root not in path.parents and path != upload_root:
        raise AppError("INVALID_AUDIO_PATH", "Invalid uploaded audio path.", False, {}, 400)
    if not path.is_file():
        raise AppError("AUDIO_NOT_FOUND", "Uploaded audio file was not found.", False, {"filename": filename}, 404)
    return FileResponse(path, media_type="audio/wav", filename=filename)


@router.get("/jobs/{job_id}/validation", summary="Return Module 5 audio validation for a completed music job")
async def get_validation(job_id: str, request: Request) -> dict:
    service: JobService = request.app.state.job_service
    job = service.get_job(job_id)
    if job.status != JobStatus.SUCCEEDED:
        raise AppError("VALIDATION_NOT_READY", "Audio validation is available only after the job succeeds.", True, {"status": job.status.value}, 409)
    validation_result = _measure_current_artifacts(service, request.app.state.job_worker, job_id)
    validation = validation_result["audio_validation"]
    return {
        "job_id": job_id,
        "overall_status": validation_result["overall_status"],
        "valid": validation_result.get("valid") if validation_result else validation.get("overall_status", "FAIL") == "PASS",
        "summary": validation_result.get("summary") if validation_result else None,
        "checks": validation_result.get("checks") if validation_result else None,
        "validation_result": validation_result,
        "audio_validation": validation,
        "stems": validation_result["stems_validation"],
    }


@router.get(
    "/jobs/{job_id}/ps1-validation",
    response_model=Ps1ValidationReport,
    summary="Return the aggregated PS1 validation report for a completed music job",
)
async def get_ps1_validation(job_id: str, request: Request) -> Ps1ValidationReport:
    service: JobService = request.app.state.job_service
    return _build_ps1_validation_report(service, request.app.state.job_worker, job_id)


@router.post(
    "/jobs/{job_id}/ps1-validation",
    response_model=Ps1ValidationReport,
    summary="Rebuild the aggregated PS1 validation report for a completed music job",
)
async def run_ps1_validation(job_id: str, request: Request) -> Ps1ValidationReport:
    service: JobService = request.app.state.job_service
    report = _build_ps1_validation_report(service, request.app.state.job_worker, job_id)
    service.update_result(report.job_id, {"ps1_validation": report.model_dump(mode="json")})
    return report


@router.get(
    "/jobs/{job_id}/ps1-validation/report",
    summary="Download the aggregated PS1 validation report for a completed music job",
)
async def export_ps1_validation_report(job_id: str, request: Request) -> Response:
    service: JobService = request.app.state.job_service
    report = _build_ps1_validation_report(service, request.app.state.job_worker, job_id)
    filename = f"ps1-validation-{report.job_id}.json"
    return Response(
        content=json.dumps(report.model_dump(mode="json"), indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/jobs/{job_id}/stems/{stem}", summary="Stream an individual generated stem")
async def get_stem(job_id: str, stem: str, request: Request):
    service: JobService = request.app.state.job_service
    job = service.get_job(job_id)
    if job.status != JobStatus.SUCCEEDED:
        raise AppError("AUDIO_NOT_READY", "Audio is available only after the generation job succeeds.", True, {"status": job.status.value}, 409)
    if stem in _generated_stem_map(service,job_id):
        remote = _published_download(request,job_id,f"stems/{stem}.wav")
        if remote is not None:
            return remote
    path = _stem_path(service, job_id, stem)
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@router.get("/jobs/{job_id}/mix", summary="Stream the generated final mix")
async def get_mix(job_id: str, request: Request):
    service: JobService = request.app.state.job_service
    job = service.get_job(job_id)
    if job.status != JobStatus.SUCCEEDED:
        raise AppError("AUDIO_NOT_READY", "Audio is available only after the generation job succeeds.", True, {"status": job.status.value}, 409)
    remote = _published_download(request,job_id,"mix.wav")
    if remote is not None:
        return remote
    return FileResponse(_audio_path(job_id, "mix.wav"), media_type="audio/wav", filename="mix.wav")
