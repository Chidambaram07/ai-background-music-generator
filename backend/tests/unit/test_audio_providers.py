from types import SimpleNamespace

import numpy as np
import pytest

from app.core.config import Settings
from app.services.audio_processing import AudioProcessingError
from app.services.audio_providers import MusicGenMelodyProvider, ProceduralProvider, select_audio_provider
from app.schemas.specification import MusicSpecification
from app.services.music_generation import MusicGenerationEngine


SPECIFICATION = {
    "genre": "cinematic", "mood": ["hopeful"], "tempo": 90, "key": "C major",
    "instruments": ["drums", "bass", "harmony", "melody"],
    "sections": [{"start": 0, "end": 5, "mood": "hopeful"}],
}


def test_procedural_provider_is_deterministic_and_not_ai():
    guide = np.linspace(-0.2, 0.2, 100, dtype=np.float32)
    provider = select_audio_provider(Settings(_env_file=None))
    assert isinstance(provider, ProceduralProvider)
    first = provider.render(None, None, None, guide, 42)
    repeated = provider.render(None, None, None, guide, 42)
    np.testing.assert_array_equal(first.audio, repeated.audio)
    assert first.audio is not guide
    assert first.provenance["ai_generated"] is False
    assert first.provenance["model"] is None


def test_real_provider_configuration_is_lazy_and_secret_safe():
    settings = Settings(_env_file=None, audio_provider="musicgen_melody", audio_model_token="test-only-secret")
    provider = select_audio_provider(settings)
    assert isinstance(provider, MusicGenMelodyProvider)
    assert provider._model is None
    assert "test-only-secret" not in repr(settings)
    with pytest.raises(AudioProcessingError) as error:
        provider._load()
    assert error.value.code == "AI_MODEL_TERMS_REQUIRED"


def test_real_provider_does_not_tile_unsupported_long_audio():
    provider = MusicGenMelodyProvider(Settings(_env_file=None))
    with pytest.raises(AudioProcessingError) as error:
        provider.render(None, SimpleNamespace(duration_s=45), None, np.zeros(10), 42)
    assert error.value.code == "AI_DURATION_UNSUPPORTED"


@pytest.mark.parametrize("code", ["AI_PROVIDER_TIMEOUT", "AI_PROVIDER_UNAVAILABLE", "AI_INVALID_AUDIO"])
def test_explicit_fallback_keeps_actual_procedural_provenance(tmp_path, monkeypatch, code, caplog):
    provider = MusicGenMelodyProvider(Settings(_env_file=None))
    def fail(*args, **kwargs):
        raise AudioProcessingError(code, "Injected provider failure for testing")
    monkeypatch.setattr(provider, "render", fail)
    engine = MusicGenerationEngine(str(tmp_path), provider=provider, fallback_to_procedural=True)
    result = engine.generate(MusicSpecification.model_validate(SPECIFICATION), "fallback", seed=42)
    baseline = MusicGenerationEngine(str(tmp_path)).generate(MusicSpecification.model_validate(SPECIFICATION), "baseline", seed=42)
    assert result["provider_fallback"]["code"] == code
    assert "explicit whole-job procedural fallback" in caplog.text
    for stem in result["stems"]:
        assert not stem["provenance"]["ai_generated"]
        assert stem["provenance"]["provider_id"] == "procedural"
        assert (tmp_path / "fallback" / stem["filename"]).read_bytes() == (tmp_path / "baseline" / stem["filename"]).read_bytes()
    assert result["composition_plan"] == baseline["composition_plan"]


def test_no_fallback_does_not_write_successful_audio(tmp_path):
    provider = MusicGenMelodyProvider(Settings(_env_file=None))
    engine = MusicGenerationEngine(str(tmp_path), provider=provider)
    with pytest.raises(AudioProcessingError) as error:
        engine.generate(MusicSpecification.model_validate(SPECIFICATION), "failure", seed=42)
    assert error.value.code == "AI_MODEL_TERMS_REQUIRED"
    assert not list((tmp_path / "failure").glob("*.wav"))


def test_provider_output_rejects_nan_and_wrong_sample_count():
    from app.services.audio_providers import ProviderOutput
    for audio in (np.zeros(20), np.array([np.nan] * 10), np.ones((1, 10))):
        with pytest.raises(AudioProcessingError):
            MusicGenerationEngine._provider_output(ProviderOutput(audio, {}), SimpleNamespace(sample_count=10))


def test_missing_model_is_a_structured_unavailable_error(monkeypatch):
    import builtins
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("Deliberately absent optional runtime")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", unavailable)
    provider = MusicGenMelodyProvider(Settings(_env_file=None, audio_model_research_acknowledged=True))
    with pytest.raises(AudioProcessingError) as error:
        provider._load()
    assert error.value.code == "AI_PROVIDER_UNAVAILABLE"


def test_loaded_model_inference_errors_are_structured(monkeypatch):
    from app.services.music_generation import AudioConfig, build_composition_plan, resolve_stem_definitions
    specification = MusicSpecification.model_validate(SPECIFICATION)
    plan = build_composition_plan(specification, AudioConfig(44100, 42))
    provider = MusicGenMelodyProvider(Settings(_env_file=None))
    provider._model = SimpleNamespace(config=SimpleNamespace(audio_encoder=SimpleNamespace(sampling_rate=32000)))
    def fail(**kwargs):
        raise TimeoutError("Injected tokenizer/inference failure")
    provider._processor = fail
    with pytest.raises(AudioProcessingError) as error:
        provider.render(specification, plan, resolve_stem_definitions(specification.instruments)[0], np.zeros(plan.sample_count), 42)
    assert error.value.code == "AI_PROVIDER_FAILED"
    assert error.value.details["exception_type"] == "TimeoutError"


def test_regeneration_requests_original_model_revision(tmp_path, monkeypatch):
    from app.services import music_generation
    specification = MusicSpecification.model_validate(SPECIFICATION)
    engine = MusicGenerationEngine(str(tmp_path))
    engine.generate(specification, "parent", seed=42)
    engine.storage.write_json("parent", "music_manifest.json", {
        "generation": {"seed":42},
        "stems": [{"name":"drums", "provenance":{"provider_id":"musicgen_melody", "model":"facebook/musicgen-melody", "version":"unit-test-recorded-revision"}}],
    })
    engine.provider = MusicGenMelodyProvider(Settings(_env_file=None, audio_provider="musicgen_melody"))
    requested = []
    def select(settings):
        requested.append(settings.audio_model_revision)
        return ProceduralProvider()
    monkeypatch.setattr(music_generation, "select_audio_provider", select)
    result = engine.regenerate_stem(specification, "parent", "drums", "regeneration", base_seed=42)
    assert requested == ["unit-test-recorded-revision"]
    assert result["provenance"]["ai_generated"] is False