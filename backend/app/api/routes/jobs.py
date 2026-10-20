import json
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
#It is Jobs.py
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.core.http import IntegrationRoute
from app.core.exceptions import AppError
from app.models.job import JobStatus
from app.schemas.job import CancelResponse, JobError, JobResponse
from app.services.job_service import JobService
from app.services.storage import ArtifactStorage

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"], route_class=IntegrationRoute)


def _root_job_id(service: JobService, job_id: str) -> str:
    job = service.get_job(job_id)
    return job.parent_job_id or job.job_id


def _job_dir(job_id: str) -> Path:
    settings = get_settings()
    return ArtifactStorage(settings.audio_output_dir).job_dir(job_id)


def _package_artifacts(job_id: str, result: dict) -> tuple[list[tuple[Path, str]], dict | None]:
    job_dir = _job_dir(job_id)
    artifacts: list[tuple[Path, str]] = []

    static_artifacts = (
        ("mix.wav", "mix.wav"),
        ("mix.mp3", "mix.mp3"),
        ("mix_ducked.wav", "mix_ducked.wav"),
        ("mix_looped.wav", "loop.wav"),
    )
    for filename, archive_name in static_artifacts:
        path = job_dir / filename
        if path.is_file():
            artifacts.append((path, archive_name))

    stems = result.get("stems") if isinstance(result.get("stems"), list) else []
    for stem in stems:
        if not isinstance(stem, dict):
            continue
        filename = str(stem.get("filename") or "").strip()
        if not filename:
            continue
        path = job_dir / filename
        canonical_stem = job_dir / "stems" / Path(filename).name
        if canonical_stem.is_file():
            path = canonical_stem
        if path.is_file():
            artifacts.append((path, f"stems/{Path(filename).name}"))

    manifest = result.get("manifest") if isinstance(result.get("manifest"), dict) else None
    return artifacts, manifest


def _build_package(job_id: str, result: dict) -> Path:
    storage = ArtifactStorage(get_settings().audio_output_dir)
    canonical = storage.artifact(job_id, "music_manifest.json", required=False)
    if canonical.exists():
        storage.verify_manifest(job_id, storage.read_json(job_id, "music_manifest.json"))
    artifacts, manifest = _package_artifacts(job_id, result)
    if not artifacts and not manifest:
        raise AppError(
            "PACKAGE_NOT_READY",
            "No generated artifacts are available to package for this job.",
            False,
            {"job_id": job_id},
            409,
        )

    temp_file = storage.temporary_package(job_id)
    temp_path = Path(temp_file.name)
    temp_file.close()

    with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source_path, archive_name in artifacts:
            archive.write(source_path, archive_name)
        if manifest:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        if canonical.exists():
            archive.write(canonical, "music_manifest.json")

    return temp_path


def _response(job, service: JobService) -> JobResponse:
    error = None
    if job.error_code:
        error = JobError(code=job.error_code, message=job.error_message or "Job failed.", retryable=job.error_code not in {"WORKER_RESTART"}, details=job.error_details or {})
    result = service.get_result(job.job_id) if job.status == JobStatus.SUCCEEDED else None
    if result and "stems" in result:
        base = f"/api/v1/music/jobs/{job.job_id}"
        object_storage = result.get("storage",{}).get("backend") == "s3"
        result = {
            **result,
            "stems": [
                {
                    **stem,
                    "url": f"{base}/artifacts/stems/{stem.get('stem_id') or stem['name']}.wav" if object_storage else f"{base}/stems/{stem.get('stem_id') or stem['name']}",
                }
                for stem in result["stems"]
            ],
            "mix": {**result["mix"], "url": f"{base}/artifacts/mix.wav" if object_storage else f"{base}/mix"},
            "manifest_url": f"{base}/artifacts/music_manifest.json" if object_storage else result.get("manifest_url"),
        }
    return JobResponse(
        job_id=job.job_id, status=job.status, progress=job.progress,
        created_at=job.created_at, updated_at=job.updated_at,
        prompt=job.prompt, duration_s=job.duration_s, stem_id=job.stem_id, parent_job_id=job.parent_job_id, error=error, result=result,
    )


@router.get("/{job_id}", response_model=JobResponse, summary="Get the current state and progress of a music job")
async def get_job(job_id: str, request: Request) -> JobResponse:
    service: JobService = request.app.state.job_service
    return _response(service.get_job(job_id), service)


@router.post("/{job_id}/cancel", response_model=CancelResponse, summary="Cancel a queued or processing job")
async def cancel_job(job_id: str, request: Request) -> CancelResponse:
    service: JobService = request.app.state.job_service
    job = service.get_job(job_id)
    if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
        raise AppError("INVALID_STATE_TRANSITION", f"Cannot cancel a {job.status.value} job.", False, {"status": job.status.value}, 409)
    updated = service.transition(job_id, JobStatus.CANCELLED)
    return CancelResponse(job_id=updated.job_id, status=updated.status)


@router.get("/{job_id}/download/package", summary="Download a ZIP package of generated job artifacts")
async def download_job_package(job_id: str, request: Request):
    service: JobService = request.app.state.job_service
    requested_job = service.get_job(job_id)
    root_job_id = _root_job_id(service, requested_job.job_id)
    root_job = service.get_job(root_job_id)
    if root_job.status != JobStatus.SUCCEEDED:
        raise AppError(
            "PACKAGE_NOT_READY",
            "The package is available only after the generation job succeeds.",
            True,
            {"job_id": root_job_id, "status": root_job.status.value},
            409,
        )

    result = service.get_result(root_job_id) or {}
    request.app.state.job_worker.output_store.restore(root_job_id,result.get("storage",{}))
    zip_path = _build_package(root_job_id, result)
    download_name = f"incubrix_{root_job_id}_package.zip"
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=download_name,
        background=BackgroundTask(lambda path: Path(path).unlink(missing_ok=True), str(zip_path)),
    )
