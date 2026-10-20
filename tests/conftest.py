import numpy as np
import pytest
import soundfile as sf
from backend.tests.http_client import IdempotentTestClient as TestClient

from app.core.config import get_settings


@pytest.fixture
def assessment_client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("AUDIO_OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("AUDIO_PROVIDER", "procedural")
    monkeypatch.setenv("AUDIO_PROVIDER_FALLBACK", "false")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    get_settings.cache_clear()
    from app.main import app
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


@pytest.fixture
def audio_fixtures(tmp_path):
    def write(name, rate=48000, duration=2, amplitude=0.2, clipped=False):
        times = np.arange(round(rate * duration)) / rate
        audio = amplitude * np.sin(2 * np.pi * 440 * times)
        if clipped:
            audio = np.clip(audio * 20, -1, 1)
        path = tmp_path / f"{name}.wav"
        sf.write(path, audio, rate, subtype="PCM_16")
        return path
    return write