import hashlib
import os
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.services.audio_processing import AudioProcessingError
from app.services.manifest import file_hash
from app.services.object_storage import OutputStore
from scripts.batch_runner import generate, wait_job


@pytest.fixture
def s3_client(assessment_client):
    from moto import mock_aws
    with mock_aws():
        worker = assessment_client.app.state.job_worker
        settings = worker.settings.model_copy(update={"storage_backend":"s3","s3_endpoint_url":"https://s3.amazonaws.com",
            "s3_public_endpoint_url":"https://s3.amazonaws.com","s3_bucket":"incubrix-test-outputs",
            "s3_access_key":SecretStr("testing"),"s3_secret_key":SecretStr("testing")})
        worker.output_store = OutputStore(settings,worker.storage)
        worker.output_store.initialize()
        yield assessment_client


def test_s3_publish_versioned_download_and_cache_restore(s3_client):
    client = s3_client
    job = generate(client,{"prompt":"Hopeful instrumental background","duration_s":5})
    assert job["status"] == "succeeded", job
    worker = client.app.state.job_worker
    record = job["result"]["storage"]
    assert record["backend"] == "s3"
    for relative, item in record["objects"].items():
        response = worker.output_store.client.get_object(Bucket=record["bucket"],Key=item["key"],VersionId=item["version_id"])
        data = response["Body"].read()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
    download = client.get(job["result"]["mix"]["url"],follow_redirects=False)
    assert download.status_code == 307
    assert "versionId=" in download.headers["location"] and "X-Amz-Signature=" in download.headers["location"]
    path = worker.storage.artifact(job["job_id"],"mix.wav")
    expected = file_hash(path)
    path.unlink()
    assert client.get(f"/api/v1/music/jobs/{job['job_id']}/mix",follow_redirects=False).status_code == 307
    assert client.get(f"/api/v1/music/jobs/{job['job_id']}/stems/drums",follow_redirects=False).status_code == 307
    assert client.get(f"/api/v1/music/jobs/{job['job_id']}/stems").status_code == 200
    package = client.get(f"/api/v1/jobs/{job['job_id']}/download/package")
    assert package.status_code == 200
    assert file_hash(path) == expected


def test_s3_failure_cannot_mark_job_successful(s3_client,monkeypatch):
    store = s3_client.app.state.job_worker.output_store
    def fail(**kwargs):
        raise OSError("Injected object-store failure")
    monkeypatch.setattr(store.client,"put_object",fail)
    job = generate(s3_client,{"prompt":"Storage failure test","duration_s":5})
    assert job["status"] == "failed"
    assert job["error"]["code"] == "STORAGE_PUBLICATION_FAILED"
    assert job["result"] is None


def test_s3_old_versions_survive_regeneration_and_child_failure_rolls_back(s3_client,monkeypatch):
    client = s3_client
    worker = client.app.state.job_worker
    job = generate(client,{"prompt":"Hopeful music","duration_s":5})
    assert job["status"] == "succeeded",job
    parent = job["job_id"]
    original = job["result"]["storage"]["objects"]["mix.wav"]
    response = client.post(f"/api/v1/music/jobs/{parent}/stems/drums/regenerate",json={"instruction":"More energetic drums"})
    assert wait_job(client,response.json()["job_id"])["status"] == "succeeded"
    after = client.get(f"/api/v1/jobs/{parent}").json()["result"]["storage"]["objects"]["mix.wav"]
    assert after["version_id"] != original["version_id"]
    old = worker.output_store.client.get_object(Bucket=worker.output_store.settings.s3_bucket,Key=original["key"],VersionId=original["version_id"])["Body"].read()
    assert hashlib.sha256(old).hexdigest() == original["sha256"]
    directory = worker.storage.job_dir(parent)
    published = client.get(f"/api/v1/jobs/{parent}").json()["result"]["storage"]["objects"]
    for relative in ("music_manifest.json","manifest.json"):
        assert file_hash(directory/relative) == published[relative]["sha256"], relative
    hashes = {str(path.relative_to(directory)):file_hash(path) for path in directory.rglob("*") if path.is_file()}
    publish = worker.output_store.publish
    def fail_child(job_id,manifest):
        if job_id != parent:
            raise AudioProcessingError("STORAGE_PUBLICATION_FAILED","Injected child publication failure")
        return publish(job_id,manifest)
    monkeypatch.setattr(worker.output_store,"publish",fail_child)
    response = client.post(f"/api/v1/music/jobs/{parent}/stems/drums/regenerate",json={"instruction":"Change drums again"})
    assert wait_job(client,response.json()["job_id"])["status"] == "failed"
    assert hashes == {str(path.relative_to(directory)):file_hash(path) for path in directory.rglob("*") if path.is_file()}
    assert client.get(f"/api/v1/jobs/{parent}").json()["result"]["storage"]["objects"]["mix.wav"]["version_id"] == after["version_id"]


def test_ready_reports_s3_outage(s3_client,monkeypatch):
    def fail(**kwargs):
        raise OSError("S3 unavailable")
    monkeypatch.setattr(s3_client.app.state.job_worker.output_store.client,"head_bucket",fail)
    response = s3_client.get("/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"


def test_live_minio_when_enabled(assessment_client):
    if os.environ.get("RUN_MINIO_TEST") != "1":
        pytest.skip("Live MinIO is enabled by the Docker test service; local S3 emulation is tested separately.")
    worker = assessment_client.app.state.job_worker
    settings = worker.settings.model_copy(update={"storage_backend":"s3","s3_bucket":"incubrix-test-"+uuid4().hex})
    worker.output_store = OutputStore(settings,worker.storage)
    worker.output_store.initialize()
    job = generate(assessment_client,{"prompt":"Live MinIO integration","duration_s":5})
    assert job["status"] == "succeeded",job
    import httpx
    response = assessment_client.get(job["result"]["mix"]["url"],follow_redirects=False)
    actual = httpx.get(response.headers["location"],trust_env=False,timeout=10)
    assert actual.status_code == 200
    item = job["result"]["storage"]["objects"]["mix.wav"]
    assert hashlib.sha256(actual.content).hexdigest() == item["sha256"]