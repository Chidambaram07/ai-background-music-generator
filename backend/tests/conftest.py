import os
import tempfile
import time

import pytest
from backend.tests.http_client import IdempotentTestClient as TestClient


def make_client(monkeypatch, tmp_path):
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=tmp_path)
    db.close()

    monkeypatch.setenv("DATABASE_PATH", db.name)
    monkeypatch.setenv("AUDIO_OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("AUDIO_PROVIDER", "procedural")
    monkeypatch.setenv("AUDIO_PROVIDER_FALLBACK", "false")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("JOB_SIMULATION_STEP_SECONDS", "0.01")
    monkeypatch.setenv("FRONTEND_ORIGINS", "http://localhost:5173")

    from app.core.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    return TestClient(app), db.name


@pytest.fixture
def client(monkeypatch, tmp_path):
    c, path = make_client(monkeypatch, tmp_path)

    with c:
        yield c

    # Windows may briefly keep SQLite handles open.
    # Retry deletion before giving up.
    for _ in range(10):
        try:
            os.unlink(path)
            break
        except FileNotFoundError:
            break
        except PermissionError:
            time.sleep(0.1)
    else:
        # Don't fail the test suite because of Windows file locking.
        pass