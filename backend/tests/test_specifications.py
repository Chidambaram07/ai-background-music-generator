from app.core.llm import LLMService


async def valid_spec(self, prompt):
    return {
        "genre": "cinematic",
        "mood": ["peaceful", "hopeful"],
        "tempo": 90,
        "key": "C major",
        "instruments": ["piano", "soft pads"],
        "sections": [
            {"start": 0, "end": 20, "mood": "peaceful"},
            {"start": 20, "end": 45, "mood": "hopeful"},
        ],
    }


def test_specification_endpoint_uses_validated_mock(client, monkeypatch):
    monkeypatch.setattr(LLMService, "generate_specification", valid_spec)
    response = client.post("/api/v1/specifications", json={"prompt": "Peaceful nature documentary becoming hopeful"})
    assert response.status_code == 200
    data = response.json()["specification"]
    assert data["genre"] == "cinematic"
    assert data["tempo"] == 90
    assert data["sections"][-1]["end"] == 45


def test_specification_endpoint_applies_explicit_overrides(client, monkeypatch):
    monkeypatch.setattr(LLMService, "generate_specification", valid_spec)
    response = client.post(
        "/api/v1/specifications",
        json={
            "prompt": "Peaceful nature documentary becoming hopeful",
            "genre": "Documentary",
            "mood": ["Focused"],
            "tempo": 73,
            "key": "F# minor",
            "instruments": ["Piano", "Pads", "Bass"],
            "sections": [
                {"name": "INTRO", "start": 0, "end": 8.14, "mood": "Focused"},
                {"name": "BUILD", "start": 8.14, "end": 20.35, "mood": "Focused"},
                {"name": "MAIN", "start": 20.35, "end": 32.56, "mood": "Focused"},
                {"name": "OUTRO", "start": 32.56, "end": 37, "mood": "Focused"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["specification"]
    assert data["genre"] == "Documentary"
    assert data["mood"] == ["Focused"]
    assert data["tempo"] == 73
    assert data["key"] == "F# minor"
    assert data["instruments"] == ["Piano", "Pads", "Bass"]
    assert data["sections"][-1]["end"] == 37


def test_specification_rejects_empty_prompt(client):
    response = client.post("/api/v1/specifications", json={"prompt": "   "})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_specification_rejects_invalid_llm_output(client, monkeypatch):
    async def fake_generate(self, prompt):
        return {
            "genre": "cinematic",
            "mood": ["peaceful"],
            "tempo": 400,
            "key": "C major",
            "instruments": ["piano"],
            "sections": [{"start": 0, "end": 45, "mood": "peaceful"}],
        }

    monkeypatch.setattr(LLMService, "generate_specification", fake_generate)
    response = client.post("/api/v1/specifications", json={"prompt": "Peaceful music"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "SPECIFICATION_VALIDATION_FAILED"


def test_specification_rejects_overlapping_sections(client, monkeypatch):
    async def fake_generate(self, prompt):
        return {
            "genre": "cinematic",
            "mood": ["peaceful", "hopeful"],
            "tempo": 90,
            "key": "C major",
            "instruments": ["piano"],
            "sections": [
                {"start": 0, "end": 30, "mood": "peaceful"},
                {"start": 20, "end": 45, "mood": "hopeful"},
            ],
        }

    monkeypatch.setattr(LLMService, "generate_specification", fake_generate)
    response = client.post("/api/v1/specifications", json={"prompt": "Progressive music"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "SPECIFICATION_VALIDATION_FAILED"


def test_auto_prefers_huggingface_when_configured(monkeypatch):
    from app.core.config import Settings

    settings = Settings(
        hf_api_key="hf-test-token",
        hf_model="openai/gpt-oss-20b:fastest",
        llm_api_key="openai-test-token",
        llm_provider="auto",
    )
    service = LLMService(settings)

    calls = []

    async def fake_call(**kwargs):
        calls.append(kwargs)
        return {
            "genre": "ambient",
            "mood": ["calm"],
            "tempo": 70,
            "key": "C major",
            "instruments": ["soft pads"],
            "sections": [{"start": 0, "end": 45, "mood": "calm"}],
        }

    monkeypatch.setattr(service, "_call_openai_compatible", fake_call)

    import asyncio

    result = asyncio.run(service.generate_specification("relaxing ambient music"))
    assert result["genre"] == "ambient"
    assert calls[0]["provider_name"] == "Hugging Face"
    assert calls[0]["model"] == "openai/gpt-oss-20b:fastest"


def test_auto_falls_back_to_mock_when_provider_fails(monkeypatch):
    from app.core.config import Settings

    settings = Settings(
        hf_api_key="hf-test-token",
        llm_provider="auto",
        llm_auto_fallback_to_mock=True,
    )
    service = LLMService(settings)

    from app.core.llm import LLMServiceError

    async def failing_call(**kwargs):
        raise LLMServiceError("simulated provider failure")

    monkeypatch.setattr(service, "_call_openai_compatible", failing_call)

    import asyncio

    result = asyncio.run(service.generate_specification("peaceful nature documentary"))
    assert result["genre"] == "cinematic"
    assert result["sections"][0]["mood"] == "peaceful"
