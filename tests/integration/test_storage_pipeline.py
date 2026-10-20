import io
import json
import zipfile

from app.services.music_generation import MusicGenerationEngine
from scripts.batch_runner import generate, measure_job


def test_complete_pipeline_and_manifest(assessment_client):
    client = assessment_client
    request = {"prompt":"Cinematic hopeful music", "duration_s":6, "seed":42, "output":{"formats":["wav","mp3"]}}
    job = generate(client, request)
    assert job["status"] == "succeeded", job
    directory = client.app.state.job_worker.storage.job_dir(job["job_id"])
    measured = measure_job(directory, request)
    assert all(measured["validation"].values()), measured
    manifest_response = client.get(f"/api/v1/music/jobs/{job['job_id']}/manifest")
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["request"]["seed"] == 42
    assert manifest["generation"]["cost"] is None
    assert manifest["generation"]["ai_generated"] is False
    assert manifest["commercial_rights"]["commercial_use_permitted"] is None
    assert manifest["music"]["beat_times"] and manifest["music"]["bar_times"]
    assert len(manifest["stems"]) >= 3
    assert not (directory / "mix_ducked.wav").exists()
    assert client.get(f"/api/v1/music/jobs/{job['job_id']}/mix.mp3").status_code == 200
    package = client.get(f"/api/v1/jobs/{job['job_id']}/download/package")
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert "music_manifest.json" in archive.namelist()
        assert json.loads(archive.read("music_manifest.json"))["job_id"] == job["job_id"]
        import hashlib
        for stem in manifest["stems"]:
            assert hashlib.sha256(archive.read(stem["file"])).hexdigest() == stem["sha256"]


def test_validation_failure_cannot_succeed(assessment_client, monkeypatch):
    worker = assessment_client.app.state.job_worker
    original = worker.validation_engine.validate_job
    def reject(*args, **kwargs):
        result = original(*args, **kwargs)
        result["valid"] = False
        result["overall_status"] = "FAIL"
        return result
    monkeypatch.setattr(worker.validation_engine, "validate_job", reject)
    job = generate(assessment_client, {"prompt":"Test rejection", "duration_s":5})
    assert job["status"] == "failed"
    assert job["error"]["code"] == "VALIDATION_FAILED"
    assert job["result"] is None


def test_provider_timeout_is_structured_failure(assessment_client, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise TimeoutError("Injected provider timeout")
    monkeypatch.setattr(MusicGenerationEngine, "generate", fail)
    job = generate(assessment_client, {"prompt":"Provider timeout", "duration_s":5})
    assert job["status"] == "failed"
    assert job["error"]["code"] == "MUSIC_GENERATION_FAILED"
    assert "timeout" in job["error"]["message"].lower()
    assert job["result"] is None
    assert "Unhandled worker error" in caplog.text


def test_corrupt_download_has_structured_error(assessment_client):
    job = generate(assessment_client, {"prompt":"Calm background", "duration_s":5})
    assert job["status"] == "succeeded"
    directory = assessment_client.app.state.job_worker.storage.job_dir(job["job_id"])
    (directory / "mix.wav").write_bytes(b"corrupt audio")
    response = assessment_client.get(f"/api/v1/music/jobs/{job['job_id']}/mix")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ARTIFACT_CORRUPT"


def test_regeneration_failure_rolls_back_parent(assessment_client, monkeypatch):
    from scripts.batch_runner import wait_job
    from app.services.manifest import file_hash
    client = assessment_client
    job = generate(client, {"prompt":"Cinematic hopeful background", "duration_s":6})
    assert job["status"] == "succeeded", job
    worker = client.app.state.job_worker
    directory = worker.storage.job_dir(job["job_id"])
    before = {str(path.relative_to(directory)):file_hash(path) for path in directory.rglob("*") if path.is_file()}
    def fail(*args, **kwargs):
        raise RuntimeError("Injected validation failure after replacing stem")
    monkeypatch.setattr(worker, "_revalidate_parent_job", fail)
    response = client.post(f"/api/v1/music/jobs/{job['job_id']}/stems/drums/regenerate", json={"instruction":"Different drums"})
    completed = wait_job(client, response.json()["job_id"])
    assert completed["status"] == "failed" and completed["result"] is None
    after = {str(path.relative_to(directory)):file_hash(path) for path in directory.rglob("*") if path.is_file()}
    assert before == after
    assert client.get(f"/api/v1/music/jobs/{job['job_id']}/manifest").status_code == 200


def test_module3_seed_and_downloaded_stem_reconstruction(assessment_client):
    import numpy as np
    import soundfile as sf
    from scripts.batch_runner import wait_job

    client = assessment_client
    specification = {"genre":"cinematic", "mood":["peaceful","hopeful"], "tempo":90, "key":"D major",
                     "instruments":["drums","bass","harmony","melody"],
                     "sections":[{"start":0,"end":3,"mood":"peaceful"},{"start":3,"end":6,"mood":"hopeful"}]}
    outputs = []
    for seed in (42, 43, 42):
        response = client.post("/api/v1/music/generate", json={"specification":specification, "seed":seed})
        assert response.status_code == 202, response.text
        job = wait_job(client, response.json()["job_id"])
        assert job["status"] == "succeeded", job
        manifest = job["result"]["manifest"]
        assert manifest["generation"]["seed"] == manifest["rendering_plan"]["seed"] == seed
        assert manifest["rendering_plan"]["key"] == "D major" and manifest["rendering_plan"]["bpm"] == 90
        mix_response = client.get(job["result"]["mix"]["url"])
        mix, rate = sf.read(io.BytesIO(mix_response.content), dtype="float32")
        assert rate == 48000 and len(mix) == 288000
        rebuilt = np.zeros_like(mix, dtype=np.float64)
        stem_bytes = {}
        for stem, metadata in zip(job["result"]["stems"], manifest["stems"], strict=True):
            downloaded = client.get(stem["url"])
            assert downloaded.status_code == 200
            audio, stem_rate = sf.read(io.BytesIO(downloaded.content), dtype="float32")
            assert stem_rate == rate and audio.shape == mix.shape
            rebuilt += audio * 10 ** (metadata["gain_db"] / 20)
            stem_bytes[stem["stem_id"]] = downloaded.content
            assert metadata["rendered_timbre"].startswith("synthetic_")
        rebuilt *= 10 ** (manifest["mix"]["master_gain_db"] / 20)
        error = 20 * np.log10(max(float(np.sqrt(np.mean((rebuilt - mix) ** 2))), 1e-15) / float(np.sqrt(np.mean(mix.astype(np.float64) ** 2))))
        assert error <= -40, error
        assert not manifest["generation"]["ai_generated"]
        outputs.append(stem_bytes)
    assert outputs[0] == outputs[2]
    assert outputs[0] != outputs[1]