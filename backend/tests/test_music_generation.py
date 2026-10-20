from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.schemas.specification import MusicSpecification
from app.services.music_generation import AudioConfig, MusicGenerationEngine, SCALES, beats_to_seconds, build_composition_plan, midi_to_frequency, parse_key, resolve_stem_definitions, validate_stems


SPEC = {
    "genre": "cinematic",
    "mood": ["peaceful", "hopeful"],
    "tempo": 82,
    "key": "C major",
    "instruments": ["piano", "soft pads", "bass", "light percussion"],
    "sections": [
        {"start": 0, "end": 22.5, "mood": "peaceful"},
        {"start": 22.5, "end": 45, "mood": "hopeful"},
    ],
}


def test_music_math():
    assert parse_key("C major") == (0, "major")
    assert parse_key("F# minor") == (6, "minor")
    assert round(beats_to_seconds(4, 82), 4) == round(240 / 82, 4)
    assert round(midi_to_frequency(69), 4) == 440.0


def test_melody_transposes_with_requested_key(monkeypatch, tmp_path):
    from app.services import music_generation

    frequencies = []
    original = music_generation._sine_note

    def record_note(buffer, start_s, duration_s, freq, amplitude, sample_rate, harmonics=(1.0,)):
        frequencies.append(freq)
        original(buffer, start_s, duration_s, freq, amplitude, sample_rate, harmonics)

    monkeypatch.setattr(music_generation, "_sine_note", record_note)
    engine = MusicGenerationEngine(str(tmp_path), seed=42)
    observed = []
    for key in ("C major", "D major"):
        specification = MusicSpecification.model_validate({**SPEC, "key": key})
        chords = music_generation._build_chords(specification, *parse_key(key))
        engine._render_stem(specification, 45, chords, "melody")
        observed.append(np.asarray(frequencies))
        frequencies.clear()
    assert observed[0].size > 0
    np.testing.assert_allclose(observed[1] / observed[0], 2 ** (2 / 12), rtol=1e-6)


def test_engine_generates_independent_stems(tmp_path: Path):
    spec = MusicSpecification.model_validate(SPEC)
    result = MusicGenerationEngine(str(tmp_path)).generate(spec, "test-job")
    root = tmp_path / "test-job"
    expected_stems = [
        ("piano", "piano"),
        ("soft_pads", "soft pads"),
        ("bass", "bass"),
        ("light_percussion", "light percussion"),
    ]
    for stem_id, _ in expected_stems:
        assert (root / f"{stem_id}.wav").exists()
    assert (root / "mix.wav").exists()

    arrays = {}
    for stem_id, _ in expected_stems:
        data, sr = sf.read(root / f"{stem_id}.wav", dtype="float32")
        arrays[stem_id] = data
        assert sr == 44100
        assert len(data) == round(45 * 44100)
        assert np.sqrt(np.mean(data**2)) > 0
    mix, mix_sr = sf.read(root / "mix.wav", dtype="float32")
    assert mix_sr == 44100
    assert len(mix) == len(arrays["piano"])
    assert np.max(np.abs(mix)) <= 1.0
    for i, left in enumerate(arrays):
        for right in list(arrays)[i + 1:]:
            assert not np.array_equal(arrays[left], arrays[right])
    assert result["validation"]["independence"]
    assert [stem["stem_id"] for stem in result["stems"]] == [stem_id for stem_id, _ in expected_stems]
    assert [stem["name"] for stem in result["stems"]] == [name for _, name in expected_stems]


def test_generation_api_returns_audio_result(client):
    response = client.post("/api/v1/music/generate", json={"specification": SPEC}, headers={"Idempotency-Key": "module3-test-1"})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    import time
    body = None
    for _ in range(2000):
        status_response = client.get(f"/api/v1/jobs/{job_id}")
        body = status_response.json()
        if body["status"] == "succeeded":
            break
        time.sleep(0.03)
    assert body["status"] == "succeeded"
    assert len(body["result"]["stems"]) == 4
    assert [stem["stem_id"] for stem in body["result"]["stems"]] == ["piano", "soft_pads", "bass", "light_percussion"]
    assert body["result"]["mix"]["url"].endswith("/mix")
    audio = client.get(f"/api/v1/music/jobs/{job_id}/stems/piano")
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")


def short_specification(**overrides):
    return MusicSpecification.model_validate({
        **SPEC,
        "sections": [{"start": 0, "end": 2.5, "mood": "peaceful"}, {"start": 2.5, "end": 6, "mood": "hopeful"}],
        **overrides,
    })


def test_same_seed_reproduces_every_stem_after_another_job(tmp_path):
    import hashlib

    engine = MusicGenerationEngine(str(tmp_path), seed=999)
    specification = short_specification()
    first = engine.generate(specification, "first", seed=42)
    different = engine.generate(specification, "different", seed=43)
    repeated = engine.generate(specification, "repeated", seed=42)
    assert engine.config.seed == 999
    assert first["seed"] == repeated["seed"] == first["composition_plan"]["seed"] == 42
    assert first["composition_plan"] == repeated["composition_plan"]
    for stem in first["stems"]:
        filename = stem["filename"]
        original = (tmp_path / "first" / filename).read_bytes()
        assert original == (tmp_path / "repeated" / filename).read_bytes()
        assert hashlib.sha256(original).digest() != hashlib.sha256((tmp_path / "different" / filename).read_bytes()).digest()
    assert first["plan_sha256"] != different["plan_sha256"]


def test_every_renderer_consumes_the_same_plan_object(tmp_path, monkeypatch):
    engine = MusicGenerationEngine(str(tmp_path))
    original = engine._render_stem
    received = []

    def observe(*args, **kwargs):
        received.append(kwargs["plan"])
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, "_render_stem", observe)
    engine.generate(short_specification(), "shared-plan", seed=42)
    assert len(received) == 4
    assert all(plan is received[0] for plan in received)


def test_fewer_than_three_parts_are_rejected_before_writing(tmp_path):
    engine = MusicGenerationEngine(str(tmp_path))
    with pytest.raises(ValueError, match="at least 3"):
        engine.generate(short_specification(instruments=["piano", "bass"]), "too-few", seed=42, minimum_stems=3)
    assert not (tmp_path / "too-few").exists()


@pytest.mark.parametrize("tempo", [60, 90, 137])
@pytest.mark.parametrize("key", ["C major", "D major", "F# minor"])
def test_shared_plan_clock_key_sections_and_events(tempo, key):
    specification = short_specification(tempo=tempo, key=key)
    plan = build_composition_plan(specification, AudioConfig(48000, 42))
    assert plan.bpm == tempo and plan.key == key
    assert plan.sample_count == 6 * 48000
    np.testing.assert_allclose(np.diff(plan.beat_times), 60 / tempo)
    np.testing.assert_allclose(np.diff(plan.bar_times), 240 / tempo)
    assert plan.sections[1].start_sample == round(2.5 * 48000)
    assert {event.role for event in plan.events} == {"drums", "bass", "harmony", "melody"}
    allowed = {(plan.root_pitch_class + offset) % 12 for offset in SCALES[plan.quality]}
    for event in plan.events:
        assert event.start_sample == round(event.start_beat * 60 / tempo * 48000)
        assert event.end_sample <= plan.sample_count
        assert event.end_sample > event.start_sample >= 0
        assert all(pitch % 12 in allowed for pitch in event.pitches)
        assert not (event.start_sample < plan.sections[1].start_sample < event.end_sample)
    assert plan == build_composition_plan(specification, AudioConfig(48000, 42))


def test_serialized_melody_wav_transposes(tmp_path):
    engine = MusicGenerationEngine(str(tmp_path), sample_rate=48000)
    observed = []
    for key, job_id in (("C major", "c-major"), ("D major", "d-major")):
        specification = short_specification(key=key)
        result = engine.generate(specification, job_id, seed=42)
        stem = next(item for item in result["stems"] if item["role"] == "melody")
        audio, sample_rate = sf.read(tmp_path / job_id / stem["filename"], dtype="float32")
        event = next(item for item in result["composition_plan"]["events"] if item["role"] == "melody")
        segment = audio[event["start_sample"]:event["end_sample"]]
        frequencies = np.fft.rfftfreq(len(segment), 1 / sample_rate)
        peak = frequencies[np.argmax(np.abs(np.fft.rfft(segment)))]
        expected = midi_to_frequency(event["pitches"][0])
        assert abs(peak - expected) <= 2 * sample_rate / len(segment)
        observed.append(event["pitches"][0])
    assert observed[1] - observed[0] == 2


@pytest.mark.parametrize("instruments", [
    ["drums", "bass", "harmony", "melody"],
    ["piano", "soft pads", "bass", "light percussion"],
    ["guitar", "strings", "bass"],
])
def test_rendered_parts_are_nonempty_independent_and_sample_aligned(tmp_path, instruments):
    engine = MusicGenerationEngine(str(tmp_path), sample_rate=48000)
    result = engine.generate(short_specification(instruments=instruments), "aligned", seed=42)
    assert len(result["stems"]) >= 3
    arrays = []
    for stem in result["stems"]:
        audio, sample_rate = sf.read(tmp_path / "aligned" / stem["filename"], dtype="float32")
        assert sample_rate == 48000 and len(audio) == 288000
        assert np.isfinite(audio).all() and float(np.sqrt(np.mean(audio ** 2))) > 1e-5
        assert stem["plan_sha256"] == result["plan_sha256"]
        assert stem["rendered_timbre"].startswith("synthetic_")
        arrays.append(audio)
    for left_index, left in enumerate(arrays):
        for right in arrays[left_index + 1:]:
            assert not np.array_equal(left, right)
            assert abs(float(np.corrcoef(left, right)[0, 1])) < 0.999


def test_requested_timbres_are_distinct_and_honest(tmp_path):
    engine = MusicGenerationEngine(str(tmp_path))
    specification = short_specification()
    plan = build_composition_plan(specification, engine.config)
    arrays = []
    for instrument in ("piano", "guitar", "strings", "brass", "pads"):
        definition = resolve_stem_definitions([instrument])[0]
        assert definition.timbre.startswith("synthetic_")
        arrays.append(engine._render_stem(specification, 6, list(plan.chords), "harmony", plan=plan, timbre=definition.timbre))
    for left_index, left in enumerate(arrays):
        for right in arrays[left_index + 1:]:
            assert not np.array_equal(left, right)
    with pytest.raises(ValueError, match="Unsupported instrument"):
        resolve_stem_definitions(["female singer"])


def test_regeneration_uses_explicit_parent_seed_and_preserves_other_parts(tmp_path):
    engine = MusicGenerationEngine(str(tmp_path), seed=999)
    specification = short_specification(instruments=["drums", "bass", "harmony", "melody"])
    engine.generate(specification, "parent-one", seed=42)
    engine.generate(specification, "parent-two", seed=42)
    original = {stem: (tmp_path / "parent-one" / f"{stem}.wav").read_bytes() for stem in engine.STEM_NAMES}
    engine.generate(specification, "unrelated", seed=123)
    first = engine.regenerate_stem(specification, "parent-one", "drums", "same-regeneration-id", base_seed=42)
    engine.config = AudioConfig(seed=777)
    repeated = engine.regenerate_stem(specification, "parent-two", "drums", "same-regeneration-id", base_seed=42)
    assert first["seed"] == repeated["seed"]
    assert (tmp_path / "parent-one/drums.wav").read_bytes() == (tmp_path / "parent-two/drums.wav").read_bytes()
    assert (tmp_path / "parent-one/drums.wav").read_bytes() != original["drums"]
    for stem in ("bass", "harmony", "melody"):
        assert (tmp_path / "parent-one" / f"{stem}.wav").read_bytes() == original[stem]
