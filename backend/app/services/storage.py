from __future__ import annotations

import json
import hashlib
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import soundfile as sf

from app.services.audio_processing import AudioProcessingError


class ArtifactStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    @property
    def uploads_dir(self) -> Path:
        return self.root.parent / "uploads"

    def job_dir(self, job_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", job_id):
            raise AudioProcessingError("INVALID_ARTIFACT_PATH", "Invalid job identifier.")
        directory = (self.root / job_id).resolve()
        if not directory.is_relative_to(self.root):
            raise AudioProcessingError("INVALID_ARTIFACT_PATH", "Job path escapes storage.")
        return directory

    def create_job(self, job_id: str) -> Path:
        directory = self.job_dir(job_id)
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise AudioProcessingError("ARTIFACT_CONFLICT", "Job output already exists.", {"job_id": job_id}) from exc
        return directory

    def artifact(self, job_id: str, relative: str, *, required: bool = True) -> Path:
        directory = self.job_dir(job_id)
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory) or path == directory:
            raise AudioProcessingError("INVALID_ARTIFACT_PATH", "Artifact path escapes its job.")
        if required and not path.is_file():
            raise AudioProcessingError("ARTIFACT_MISSING", "Referenced artifact does not exist.", {"job_id": job_id, "file": relative})
        return path

    def wav_info(self, job_id: str, relative: str) -> dict:
        path = self.artifact(job_id, relative)
        try:
            info = sf.info(path)
            if info.frames <= 0 or info.samplerate <= 0 or info.format != "WAV":
                raise ValueError("Expected nonempty WAV audio.")
            return {"sample_rate": info.samplerate, "duration_s": info.frames / info.samplerate, "channels": info.channels}
        except (RuntimeError, ValueError) as exc:
            raise AudioProcessingError("ARTIFACT_CORRUPT", "Cannot read WAV artifact.", {"file": relative}) from exc

    def write_json(self, job_id: str, relative: str, payload: dict) -> Path:
        path = self.artifact(job_id, relative, required=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(json.dumps(payload, indent=2, allow_nan=False).encode("utf-8"))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def read_json(self, job_id: str, relative: str) -> dict:
        path = self.artifact(job_id, relative)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
            return payload
        except (ValueError, UnicodeError) as exc:
            raise AudioProcessingError("ARTIFACT_CORRUPT", "Cannot read JSON artifact.", {"file": relative}) from exc

    def temporary_package(self, job_id: str):
        self.job_dir(job_id)
        directory = self.root / ".packages"
        directory.mkdir(parents=True, exist_ok=True)
        return tempfile.NamedTemporaryFile(prefix=f"incubrix_{job_id}_", suffix=".zip", delete=False, dir=directory)

    @contextmanager
    def parent_update(self, child_id: str, parent_id: str, after_snapshot=None):
        child = self.create_job(child_id)
        parent = self.job_dir(parent_id)
        backup = child / ".previous"
        shutil.copytree(parent, backup)
        try:
            yield
            shutil.copytree(parent, child, dirs_exist_ok=True)
            manifest_path = child / "music_manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["job_id"] = child_id
                manifest["source_parent_job_id"] = parent_id
                self.write_json(child_id, "music_manifest.json", manifest)
            if after_snapshot is not None:
                after_snapshot()
        except BaseException:
            previous_files = {path.relative_to(backup) for path in backup.rglob("*") if path.is_file()}
            for path in parent.rglob("*"):
                if path.is_file() and path.relative_to(parent) not in previous_files:
                    path.unlink()
            shutil.copytree(backup, parent, dirs_exist_ok=True)
            raise
        finally:
            shutil.rmtree(backup)

    def verify_manifest(self, job_id: str, manifest: dict) -> None:
        references = [manifest["mix"]["file"]]
        references.extend(stem["file"] for stem in manifest["stems"])
        references.extend(manifest["mix"].get("formats", {}).values())
        if manifest.get("ducking", {}).get("file"):
            references.append(manifest["ducking"]["file"])
        for relative in references:
            self.artifact(job_id, relative)
            if relative.endswith(".wav"):
                self.wav_info(job_id, relative)
        for item in [manifest["mix"], *manifest["stems"]]:
            if item.get("sha256"):
                with self.artifact(job_id, item["file"]).open("rb") as handle:
                    actual = hashlib.file_digest(handle, "sha256").hexdigest()
                if actual != item["sha256"]:
                    raise AudioProcessingError("ARTIFACT_HASH_MISMATCH", "Artifact has changed since manifest publication.", {"file": item["file"]})