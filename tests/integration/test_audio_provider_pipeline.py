import os

import pytest

from app.core.config import Settings
from app.services.audio_processing import AudioProcessingError
from app.services.audio_providers import select_audio_provider
from scripts.batch_runner import measure_job, wait_job


SPECIFICATION = {
    "genre": "cinematic", "mood": ["hopeful"], "tempo": 90, "key": "C major",
    "instruments": ["drums", "bass", "harmony", "melody"],
    "sections": [{"start": 0, "end": 5, "mood": "hopeful"}],
}


def test_ai_configuration_with_explicit_fallback_preserves_pipeline(assessment_client):
    client = assessment_client
    worker = client.app.state.job_worker
    worker.engine.provider = select_audio_provider(Settings(_env_file=None, audio_provider="musicgen_melody"))
    worker.engine.fallback_to_procedural = True
    response = client.post("/api/v1/music/generate", json={"specification": SPECIFICATION, "seed": 42})
    assert response.status_code == 202
    completed = wait_job(client, response.json()["job_id"])
    assert completed["status"] == "succeeded", completed
    manifest = completed["result"]["manifest"]
    assert manifest["generation"]["fallback"]["code"] == "AI_MODEL_TERMS_REQUIRED"
    assert manifest["generation"]["provider"] == "local"
    assert manifest["generation"]["ai_generated"] is False
    assert not manifest["generation"]["all_stems_ai_generated"]
    assert all(not stem["ai_generated"] for stem in manifest["stems"])
    assert any("fallback" in warning for warning in manifest["warnings"])
    measured = measure_job(worker.storage.job_dir(completed["job_id"]), {"duration_s": 5})
    assert all(measured["validation"].values()), measured
    download = client.get(f"/api/v1/music/jobs/{completed['job_id']}/manifest")
    assert download.status_code == 200
    worker.engine.fallback_to_procedural = False
    regenerate = client.post(f"/api/v1/music/jobs/{completed['job_id']}/stems/drums/regenerate", json={"instruction":"More energetic drums"})
    child = wait_job(client, regenerate.json()["job_id"])
    assert child["status"] == "succeeded", child
    updated = client.get(f"/api/v1/music/jobs/{completed['job_id']}/manifest").json()
    assert updated["generation"]["fallback"]["code"] == "AI_MODEL_TERMS_REQUIRED"
    assert updated["generation"]["ai_generated"] is False
    assert all(stem["rendered_timbre"].startswith("synthetic_") for stem in updated["stems"])


def test_ai_configuration_without_fallback_returns_failed_job(assessment_client):
    client = assessment_client
    worker = client.app.state.job_worker
    worker.engine.provider = select_audio_provider(Settings(_env_file=None, audio_provider="musicgen_melody"))
    worker.engine.fallback_to_procedural = False
    response = client.post("/api/v1/music/generate", json={"specification": SPECIFICATION, "seed": 42})
    completed = wait_job(client, response.json()["job_id"])
    assert completed["status"] == "failed", completed
    assert completed["error"]["code"] == "AI_MODEL_TERMS_REQUIRED"
    assert completed["result"] is None
    assert not (worker.storage.job_dir(completed["job_id"]) / "music_manifest.json").exists()


def test_real_musicgen_model_pipeline_when_explicitly_enabled(assessment_client):
    if os.environ.get("RUN_REAL_AUDIO_PROVIDER_TEST") != "1":
        pytest.skip("Live AI inference is opt-in; no model download, external call, or GPU allocation in deterministic tests.")
    pytest.importorskip("torch", reason="Optional MusicGen runtime is not installed")
    pytest.importorskip("transformers", reason="Optional MusicGen runtime is not installed")
    settings = Settings()
    if not settings.audio_model_research_acknowledged:
        pytest.skip("MusicGen research/non-commercial terms have not been acknowledged")
    provider = select_audio_provider(settings.model_copy(update={"audio_provider": "musicgen_melody"}))
    try:
        provider._load()
    except AudioProcessingError as error:
        if error.code == "AI_PROVIDER_UNAVAILABLE":
            pytest.skip("MusicGen weights or model access are unavailable; no live AI claim is made")
        raise
    worker = assessment_client.app.state.job_worker
    worker.engine.provider = provider
    worker.engine.fallback_to_procedural = False
    response = assessment_client.post("/api/v1/music/generate", json={"specification": SPECIFICATION, "seed": 42})
    assert response.status_code == 202, response.text
    completed = wait_job(assessment_client, response.json()["job_id"], timeout=settings.audio_model_timeout_seconds * 4 + 60)
    assert completed["status"] == "succeeded", completed
    manifest = completed["result"]["manifest"]
    assert manifest["generation"]["all_stems_ai_generated"]
    assert manifest["generation"]["fallback"] is None
    assert all(stem["model"] == settings.audio_model and stem["ai_generated"] for stem in manifest["stems"])
    measured = measure_job(worker.storage.job_dir(completed["job_id"]), {"duration_s": 5})
    assert all(measured["validation"].values()), measured