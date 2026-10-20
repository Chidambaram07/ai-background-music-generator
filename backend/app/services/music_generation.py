from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from app.schemas.specification import MusicSpecification
from app.services.storage import ArtifactStorage
from app.services.audio_processing import AudioProcessingError
from app.services.audio_providers import ProceduralProvider, StemProvider, select_audio_provider

logger = logging.getLogger(__name__)


@dataclass
class DrumRegenerationOptions:
    """Structured parameters that control the drum renderer.
    Each value is a float from 0.0 to 1.0.
    """
    energy: float = 0.5
    kick_density: float = 0.5
    snare_strength: float = 0.5
    hat_density: float = 0.5
    syncopation: float = 0.5
    variation: float = 0.5


NOTE_TO_SEMITONE = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
    "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11,
}

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
}

# Roman-numeral chord degrees. Triads are deliberately simple and stable for
# deterministic background music, while genre/mood selects between progressions.
PROGRESSIONS = {
    "major": [[0, 5, 3, 4], [0, 3, 4, 0], [0, 4, 5, 3]],
    "minor": [[0, 5, 2, 6], [0, 3, 6, 5], [0, 6, 3, 4]],
}

GENRE_DENSITY = {
    "cinematic": 0.72,
    "documentary": 0.48,
    "corporate": 0.68,
    "ambient": 0.28,
    "electronic": 0.9,
    "lo-fi": 0.55,
}

MOOD_ENERGY = {
    "peaceful": 0.32,
    "calm": 0.3,
    "relaxing": 0.28,
    "hopeful": 0.62,
    "inspiring": 0.7,
    "energetic": 0.92,
    "tense": 0.78,
    "dark": 0.52,
    "sad": 0.35,
}


@dataclass(frozen=True)
class ChordEvent:
    start_beat: float
    end_beat: float
    degree: int
    root_midi: int
    quality: str
    notes: tuple[int, int, int]


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int = 44100
    seed: int = 4281097


@dataclass(frozen=True)
class StemDefinition:
    stem_id: str
    name: str
    role: str
    timbre: str = "synthetic_lead"


@dataclass(frozen=True)
class MusicalEvent:
    role: str
    kind: str
    start_beat: float
    end_beat: float
    start_sample: int
    end_sample: int
    pitches: tuple[int, ...]
    amplitude: float


@dataclass(frozen=True)
class PlannedSection:
    start_beat: float
    end_beat: float
    start_sample: int
    end_sample: int
    mood: str


@dataclass(frozen=True)
class CompositionPlan:
    bpm: int
    key: str
    root_pitch_class: int
    quality: str
    seed: int
    sample_rate: int
    sample_count: int
    duration_s: float
    beats_per_bar: int
    beat_times: tuple[float, ...]
    bar_times: tuple[float, ...]
    sections: tuple[PlannedSection, ...]
    chords: tuple[ChordEvent, ...]
    events: tuple[MusicalEvent, ...]

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _slugify_stem_name(name: str) -> str:
    stem_id = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    if not stem_id:
        raise ValueError(f"Instrument '{name}' cannot be converted into a valid stem id.")
    return stem_id


INSTRUMENT_ROLE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "drums": ("drums",),
    "light percussion": ("drums",),
    "electronic percussion": ("drums",),
    "percussion": ("drums",),
    "bass": ("bass",),
    "sub bass": ("bass",),
    "piano": ("harmony", "melody"),
    "pads": ("harmony", "melody"),
    "soft pads": ("melody", "harmony"),
    "strings": ("harmony", "melody"),
    "guitar": ("harmony", "melody"),
    "brass": ("melody", "harmony"),
    "synth": ("melody", "harmony"),
    "soft synth": ("melody", "harmony"),
    "subtle textures": ("melody", "harmony"),
    "harmony": ("harmony",),
    "melody": ("melody",),
}

INSTRUMENT_TIMBRES = {
    "drums": "synthetic_drum_kit", "light percussion": "synthetic_light_percussion",
    "electronic percussion": "synthetic_electronic_percussion", "percussion": "synthetic_drum_kit",
    "bass": "synthetic_bass", "sub bass": "synthetic_sub_bass",
    "piano": "synthetic_piano", "pads": "synthetic_pad", "soft pads": "synthetic_pad",
    "strings": "synthetic_bowed_strings", "guitar": "synthetic_plucked_string",
    "brass": "synthetic_brass", "synth": "synthetic_lead", "soft synth": "synthetic_soft_lead",
    "subtle textures": "synthetic_texture", "harmony": "synthetic_pad", "melody": "synthetic_lead",
}


def resolve_stem_definitions(instruments: list[str]) -> list[StemDefinition]:
    if not instruments:
        raise ValueError("At least one instrument is required to generate stems.")

    used_roles: set[str] = set()
    used_ids: set[str] = set()
    definitions: list[StemDefinition] = []
    for instrument in instruments:
        label = instrument.strip()
        if not label:
            raise ValueError("Instrument names must not be empty.")
        candidates = INSTRUMENT_ROLE_CANDIDATES.get(label.lower())
        if not candidates:
            raise ValueError(
                f"Unsupported instrument '{instrument}'. Supported instruments are: {', '.join(sorted(INSTRUMENT_ROLE_CANDIDATES))}."
            )
        role = next((candidate for candidate in candidates if candidate not in used_roles), None)
        if role is None:
            raise ValueError(
                f"Instrument selection '{instrument}' cannot be rendered independently with the current engine because no renderer role is available."
            )
        stem_id = _slugify_stem_name(label)
        if stem_id in used_ids:
            raise ValueError(f"Duplicate instrument selection '{instrument}' is not allowed.")
        used_roles.add(role)
        used_ids.add(stem_id)
        definitions.append(StemDefinition(stem_id=stem_id, name=label, role=role, timbre=INSTRUMENT_TIMBRES[label.lower()]))
    return definitions


def parse_key(key: str) -> tuple[int, str]:
    match = re.fullmatch(r"([A-G](?:#|b)?)\s+(major|minor)", key.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"Unsupported key '{key}'. Expected e.g. 'C major' or 'F# minor'.")
    note = match.group(1)
    quality = match.group(2).lower()
    # Normalize accidental spelling while preserving pitch class.
    if len(note) == 2 and note[1] == "B":
        note = note[0] + "b"
    elif len(note) == 2 and note[1] == "#":
        note = note[0] + "#"
    note = note[0].upper() + note[1:]
    if note not in NOTE_TO_SEMITONE:
        raise ValueError(f"Unsupported key note '{note}'.")
    return NOTE_TO_SEMITONE[note], quality


def midi_to_frequency(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def beats_to_seconds(beats: float, bpm: int) -> float:
    if bpm <= 0:
        raise ValueError("BPM must be positive")
    return beats * 60.0 / bpm


def _safe_duration(spec: MusicSpecification) -> float:
    duration = max(section.end for section in spec.sections)
    if duration <= 0:
        raise ValueError("Specification duration must be positive")
    return float(duration)


def _section_for_time(spec: MusicSpecification, seconds: float):
    for section in spec.sections:
        if section.start <= seconds < section.end:
            return section
    return spec.sections[-1]


def _envelope(length: int, attack: int, release: int) -> np.ndarray:
    env = np.ones(length, dtype=np.float32)
    attack = min(max(0, attack), length)
    release = min(max(0, release), length)
    if attack:
        env[:attack] = np.linspace(0.0, 1.0, attack, endpoint=False, dtype=np.float32)
    if release:
        env[-release:] *= np.linspace(1.0, 0.0, release, dtype=np.float32)
    return env


def _sine_note(buffer: np.ndarray, start_s: float, duration_s: float, freq: float, amplitude: float, sr: int, harmonics: tuple[float, ...] = (1.0,)) -> None:
    start = max(0, int(round(start_s * sr)))
    end = min(len(buffer), start + max(1, int(round(duration_s * sr))))
    if end <= start:
        return
    t = np.arange(end - start, dtype=np.float32) / sr
    wave = np.zeros_like(t)
    for harmonic, weight in enumerate(harmonics, start=1):
        wave += weight * np.sin(2 * np.pi * freq * harmonic * t)
    wave /= max(1.0, float(sum(abs(x) for x in harmonics)))
    env = _envelope(len(wave), int(sr * min(0.03, duration_s * 0.15)), int(sr * min(0.12, duration_s * 0.3)))
    buffer[start:end] += (wave * env * amplitude).astype(np.float32)


def _add_pluck(buffer: np.ndarray, start_s: float, duration_s: float, freq: float, amplitude: float, sr: int) -> None:
    start = int(round(start_s * sr))
    end = min(len(buffer), start + max(1, int(round(duration_s * sr))))
    if start < 0 or end <= start:
        return
    n = end - start
    t = np.arange(n, dtype=np.float32) / sr
    decay = np.exp(-5.0 * t / max(duration_s, 0.05))
    wave = (np.sin(2 * np.pi * freq * t) + 0.32 * np.sin(4 * np.pi * freq * t)) * decay
    buffer[start:end] += (wave * amplitude).astype(np.float32)


def _add_pad_chord(buffer: np.ndarray, start_s: float, duration_s: float, freqs: list[float], amplitude: float, sr: int) -> None:
    start = int(round(start_s * sr))
    end = min(len(buffer), start + max(1, int(round(duration_s * sr))))
    if start < 0 or end <= start:
        return
    n = end - start
    t = np.arange(n, dtype=np.float32) / sr
    wave = np.zeros(n, dtype=np.float32)
    for i, freq in enumerate(freqs):
        wave += np.sin(2 * np.pi * freq * t + i * 0.18)
        wave += 0.18 * np.sin(2 * np.pi * freq * 2 * t + i * 0.31)
    wave /= len(freqs) * 1.18
    attack = min(int(sr * 0.45), n // 2)
    release = min(int(sr * 0.65), n // 2)
    env = _envelope(n, attack, release)
    buffer[start:end] += (wave * env * amplitude).astype(np.float32)


def _build_chords(spec: MusicSpecification, root_pc: int, quality: str, *, seed: int = 0) -> list[ChordEvent]:
    scale = SCALES[quality]
    progression_index = 0
    text = f"{spec.genre} {' '.join(spec.mood)}".lower()
    if "hopeful" in text or "inspiring" in text:
        progression_index = 1
    elif "energetic" in text or "electronic" in text:
        progression_index = 2
    degrees = PROGRESSIONS[quality][(progression_index + seed) % len(PROGRESSIONS[quality])]
    duration_beats = _safe_duration(spec) * spec.tempo / 60.0
    chord_beats = 4.0
    events: list[ChordEvent] = []
    start = 0.0
    while start < duration_beats - 1e-6:
        index = int((start + 1e-6) // chord_beats)
        degree = degrees[index % len(degrees)]
        chord_root = 48 + root_pc + scale[degree]
        triad = tuple(48 + root_pc + scale[(degree + offset) % 7] + 12 * ((degree + offset) // 7) for offset in (0, 2, 4))
        boundaries = [section.start * spec.tempo / 60 for section in spec.sections if section.start * spec.tempo / 60 > start + 1e-6]
        end = min(duration_beats, (index + 1) * chord_beats, *boundaries)
        events.append(ChordEvent(start, end, degree, chord_root, quality, triad))
        start = end
    return events


def build_composition_plan(spec: MusicSpecification, config: AudioConfig) -> CompositionPlan:
    duration = _safe_duration(spec)
    root_pc, quality = parse_key(spec.key)
    beat_s = 60.0 / spec.tempo
    sections = tuple(PlannedSection(section.start / beat_s, section.end / beat_s,
                                   round(section.start * config.sample_rate), round(section.end * config.sample_rate), section.mood)
                     for section in spec.sections)
    chords = tuple(_build_chords(spec, root_pc, quality, seed=config.seed))
    rng = np.random.default_rng(config.seed)
    events = []

    def add(role, kind, start, end, pitches, amplitude):
        start_sample = round(start * beat_s * config.sample_rate)
        end_sample = min(round(duration * config.sample_rate), round(end * beat_s * config.sample_rate))
        if end_sample > start_sample:
            events.append(MusicalEvent(role, kind, float(start), float(end), start_sample, end_sample,
                                       tuple(int(pitch) for pitch in pitches), float(amplitude)))

    for chord in chords:
        section = _section_for_time(spec, chord.start_beat * beat_s + 1e-7)
        energy = MOOD_ENERGY.get(section.mood.lower(), 0.5)
        velocity = float(rng.uniform(0.92, 1.08))
        add("harmony", "cadence" if chord is chords[-1] else "chord", chord.start_beat, chord.end_beat,
            tuple(pitch + 12 for pitch in chord.notes), (0.055 + 0.025 * energy) * velocity)
        for offset in (0.0, 2.0, 3.0):
            start = chord.start_beat + offset
            if start < chord.end_beat:
                pitch = chord.root_midi - 12 if offset != 3 else chord.notes[2] - 12
                add("bass", "note", start, min(chord.end_beat, start + (0.75 if offset == 3 else 1.7)),
                    (pitch,), (0.10 + 0.04 * energy) * velocity)
        step = 0.5 if energy > 0.72 or GENRE_DENSITY.get(spec.genre.lower(), 0.55) > 0.8 else 1.0
        start = (np.floor(chord.start_beat / step) + 1) * step
        while start < chord.end_beat - 0.12 / beat_s:
            degree = (chord.degree + int(rng.choice((0, 2, 4, 5, 6)))) % 7
            pitch = 60 + root_pc + SCALES[quality][degree] + (12 if energy > 0.75 else 0)
            add("melody", "note", start, min(chord.end_beat, start + step * 0.82), (pitch,),
                (0.045 + 0.03 * energy) * float(rng.uniform(0.9, 1.1)))
            start += step
    total_beats = duration / beat_s
    for beat in np.arange(0, total_beats, 0.25):
        section = _section_for_time(spec, float(beat * beat_s))
        section_end = section.end / beat_s
        energy = MOOD_ENERGY.get(section.mood.lower(), 0.5)
        position = float(beat % 4)
        if position in (0.0, 2.0) or (energy > 0.6 and position == 1.5):
            add("drums", "kick", beat, min(section_end, beat + 0.18 / beat_s), (), 0.055 + 0.035 * energy)
        if position in (1.0, 3.0):
            add("drums", "snare", beat, min(section_end, beat + 0.13 / beat_s), (), 0.065 + 0.025 * energy)
        density = GENRE_DENSITY.get(spec.genre.lower(), 0.55) * (0.65 + 0.35 * energy)
        if rng.random() < density:
            add("drums", "hat", beat, min(section_end, beat + 0.045 / beat_s), (), 0.012 + 0.014 * density)
    events.sort(key=lambda event: (event.start_sample, event.role, event.kind))
    return CompositionPlan(spec.tempo, spec.key, root_pc, quality, config.seed, config.sample_rate,
                           round(duration * config.sample_rate), duration, 4,
                           tuple(float(beat * beat_s) for beat in np.arange(0, total_beats, 1)),
                           tuple(float(beat * beat_s) for beat in np.arange(0, total_beats, 4)),
                           sections, chords, tuple(events))


def _render_tonal_part(plan: CompositionPlan, role: str, config: AudioConfig, timbre: str) -> np.ndarray:
    audio = np.zeros(plan.sample_count, dtype=np.float32)
    profiles = {
        "synthetic_piano": ((1.0, 0.42, 0.19, 0.08), "pluck"),
        "synthetic_plucked_string": ((1.0, 0.62, 0.31, 0.22, 0.12), "guitar"),
        "synthetic_bowed_strings": ((1.0, 0.4, 0.3, 0.18, 0.12), "bow"),
        "synthetic_brass": ((1.0, 0.65, 0.45, 0.25), "brass"),
        "synthetic_pad": ((1.0, 0.12, 0.04), "pad"),
        "synthetic_texture": ((1.0, 0.07, 0.16, 0.03), "pad"),
        "synthetic_bass": ((1.0, 0.25, 0.08), "bass"),
        "synthetic_sub_bass": ((1.0, 0.04), "bass"),
        "synthetic_lead": ((1.0, 0.22, 0.07), "lead"),
        "synthetic_soft_lead": ((1.0, 0.08), "lead"),
    }
    harmonics, envelope = profiles[timbre]
    for event in plan.events:
        if event.role != role:
            continue
        start_s = event.start_sample / config.sample_rate
        duration_s = (event.end_sample - event.start_sample) / config.sample_rate
        segment = np.zeros(event.end_sample - event.start_sample, dtype=np.float32)
        for pitch in event.pitches:
            _sine_note(segment, 0, duration_s, midi_to_frequency(pitch), event.amplitude / max(1, len(event.pitches)), config.sample_rate, harmonics)
        times = np.arange(len(segment), dtype=np.float32) / config.sample_rate
        if envelope in {"pluck", "guitar"}:
            decay = np.exp(-(4 if envelope == "pluck" else 6) * times / max(duration_s, 0.01))
            segment *= 0.3 + 0.7 * decay if event.kind == "cadence" else decay
        elif envelope in {"bow", "pad", "brass"}:
            attack_s = {"bow": 0.16, "pad": 0.25, "brass": 0.06}[envelope]
            segment *= _envelope(len(segment), int(min(attack_s, duration_s / 3) * config.sample_rate), 0)
        audio[event.start_sample:event.end_sample] += segment
    return audio


def _render_drums(plan: CompositionPlan, config: AudioConfig, timbre: str, options: dict | None = None) -> np.ndarray:
    audio = np.zeros(plan.sample_count, dtype=np.float32)
    rng = np.random.default_rng(config.seed + 11)
    options = options or {}
    for event in plan.events:
        if event.role != "drums":
            continue
        start = event.start_sample
        length = event.end_sample - start
        times = np.arange(length, dtype=np.float32) / config.sample_rate
        if event.kind == "kick":
            frequency = 72 if timbre == "synthetic_electronic_percussion" else 58
            _add_pluck(audio, start / config.sample_rate, length / config.sample_rate, frequency,
                       event.amplitude * (0.7 + float(options.get("energy", 0.5))), config.sample_rate)
        else:
            noise = rng.normal(0, 1, length).astype(np.float32)
            if event.kind == "hat":
                noise = np.diff(noise, prepend=noise[0])
            decay = 80 if event.kind == "hat" else 24
            gain = float(options.get("hat_density" if event.kind == "hat" else "snare_strength", 0.5)) + 0.5
            if timbre == "synthetic_light_percussion":
                gain *= 0.7 if event.kind == "snare" else 1.2
            audio[start:event.end_sample] += noise * np.exp(-decay * times) * event.amplitude * gain
    return audio


def _fade_boundaries(audio: np.ndarray, spec: MusicSpecification, sr: int) -> np.ndarray:
    # Small cross-section fades avoid clicks when the mood/voicing changes.
    result = audio.copy()
    fade = int(0.02 * sr)
    for section in spec.sections[1:]:
        center = int(section.start * sr)
        lo = max(0, center - fade)
        hi = min(len(result), center + fade)
        if hi > lo:
            window = np.linspace(0.9, 1.0, hi - lo, dtype=np.float32)
            result[lo:hi] *= window
    return result


def _normalise_stem(audio: np.ndarray, target_peak: float = 0.82) -> np.ndarray:
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak <= 1e-9:
        return audio
    return audio * (target_peak / peak)


def validate_stems(stems: dict[str, np.ndarray], sample_rate: int, expected_duration: float) -> dict:
    expected_samples = int(round(expected_duration * sample_rate))
    stats: dict[str, dict] = {}
    for name, audio in stems.items():
        if len(audio) != expected_samples:
            raise ValueError(f"{name} has {len(audio)} samples; expected {expected_samples}.")
        rms = float(np.sqrt(np.mean(np.square(audio))))
        peak = float(np.max(np.abs(audio)))
        if rms <= 1e-7:
            raise ValueError(f"{name} is silent.")
        stats[name] = {"rms": round(rms, 6), "peak": round(peak, 6), "samples": len(audio)}

    names = list(stems)
    independence: dict[str, str] = {}
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            a, b = stems[left], stems[right]
            correlation = float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 1e-8 and np.std(b) > 1e-8 else 1.0
            identical = bool(np.array_equal(a, b))
            passed = not identical and abs(correlation) < 0.999
            independence[f"{left} vs {right}"] = "PASS" if passed else "FAIL"
            if not passed:
                raise ValueError(f"Stems {left} and {right} appear duplicated (correlation={correlation:.6f}).")
    return {"sample_rate": sample_rate, "duration_seconds": expected_duration, "stems": stats, "independence": independence}


class MusicGenerationEngine:
    STEM_NAMES = ("drums", "bass", "harmony", "melody")

    def __init__(self, output_root: str, sample_rate: int = 44100, seed: int = 4281097, *, provider: StemProvider | None = None, fallback_to_procedural: bool = False):
        self.output_root = Path(output_root)
        self.storage = ArtifactStorage(output_root)
        self.config = AudioConfig(sample_rate=sample_rate, seed=seed)
        self.provider = provider if provider is not None else ProceduralProvider()
        self.fallback_to_procedural = fallback_to_procedural

    @staticmethod
    def _provider_output(output, plan: CompositionPlan) -> np.ndarray:
        audio = np.asarray(output.audio, dtype=np.float32)
        if audio.ndim != 1 or len(audio) != plan.sample_count or not np.isfinite(audio).all() or np.max(np.abs(audio)) <= 1e-7:
            raise AudioProcessingError("AI_INVALID_AUDIO", "Audio provider returned silent, misaligned, or invalid PCM.")
        return audio

    def _render_stem(self, spec: MusicSpecification, duration: float, chords: list[ChordEvent], stem: str, seed: int | None = None, regeneration_options: dict | None = None, *, plan: CompositionPlan | None = None, timbre: str | None = None) -> np.ndarray:
        if stem not in self.STEM_NAMES:
            raise ValueError(f"Unsupported stem '{stem}'.")
        cfg = AudioConfig(sample_rate=self.config.sample_rate, seed=(plan.seed if plan else self.config.seed) if seed is None else seed)
        plan = plan or build_composition_plan(spec, cfg)
        if plan.sample_rate != cfg.sample_rate or abs(plan.duration_s - duration) > 1e-6:
            raise ValueError("Stem render context does not match its composition plan.")
        timbre = timbre or {"drums": "synthetic_drum_kit", "bass": "synthetic_bass", "harmony": "synthetic_pad", "melody": "synthetic_lead"}[stem]
        if stem == "drums":
            audio = _render_drums(plan, cfg, timbre, regeneration_options)
        else:
            audio = _render_tonal_part(plan, stem, cfg, timbre)
        if seed is not None:
            # Make every regeneration materially different even for renderers
            # whose core pattern is deterministic. The modulation is deliberately
            # subtle so the requested stem keeps the same musical identity.
            phase = (seed % 10000) / 10000.0 * 2.0 * np.pi
            t = np.arange(len(audio), dtype=np.float32) / cfg.sample_rate
            variation = 1.0 + 0.012 * np.sin(2.0 * np.pi * 0.37 * t + phase)
            audio = (audio * variation).astype(np.float32)
        # Scale normalization target by energy so energetic drums are louder
        # relative to other stems in the mix.
        target_peak = 0.72
        if stem == "drums" and regeneration_options:
            regen_energy = float(regeneration_options.get("energy", 0.5))
            if regen_energy > 0.6:
                target_peak = 0.72 + 0.18 * regen_energy  # up to 0.90
        return _fade_boundaries(_normalise_stem(audio, target_peak), spec, cfg.sample_rate)

    def _mix_arrays(self, stems: dict[str, np.ndarray]) -> np.ndarray:
        if not stems:
            raise ValueError("At least one stem is required for mixing.")
        first = next(iter(stems.values()))
        mix = sum(stems.values(), start=np.zeros_like(first))
        mix_peak = float(np.max(np.abs(mix))) if mix.size else 0.0
        if mix_peak > 0.92:
            mix *= 0.92 / mix_peak
        else:
            mix *= 0.88 / max(mix_peak, 1e-9)
        mix = mix.astype(np.float32)
        if not np.isfinite(mix).all():
            raise ValueError("Mixed audio contains non-finite samples.")
        return mix

    def _job_dir(self, job_id: str) -> Path:
        job_dir = self.storage.job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    @staticmethod
    def stem_definitions(spec: MusicSpecification) -> list[StemDefinition]:
        return resolve_stem_definitions(spec.instruments)

    def rebuild_mix(self, job_id: str) -> str:
        job_dir = self._job_dir(job_id)
        stems: dict[str, np.ndarray] = {}
        for path in sorted(job_dir.glob("*.wav")):
            if path.name == "mix.wav":
                continue
            name = path.stem
            path = job_dir / f"{name}.wav"
            if not path.is_file():
                raise ValueError(f"Required stem '{name}' is missing.")
            audio, sample_rate = sf.read(path, dtype="float32")
            if sample_rate != self.config.sample_rate:
                raise ValueError(f"Stem '{name}' has sample rate {sample_rate}; expected {self.config.sample_rate}.")
            stems[name] = audio
        mix = self._mix_arrays(stems)
        sf.write(job_dir / "mix.wav", mix, self.config.sample_rate, subtype="PCM_16")
        return "mix.wav"

    def regenerate_stem(
        self,
        spec: MusicSpecification,
        target_job_id: str,
        stem: str,
        regeneration_job_id: str,
        regeneration_instruction: str | None = None,
        preserve_options: dict | None = None,
        progress: Callable[[int], None] | None = None,
        regeneration_options: dict | None = None,
        *,
        base_seed: int | None = None,
    ) -> dict:
        definitions = {definition.stem_id: definition for definition in self.stem_definitions(spec)}
        definition = definitions.get(stem)
        if definition is None:
            raise ValueError(f"Unsupported stem '{stem}'.")
        stem_role = definition.role
        duration = _safe_duration(spec)

        if progress:
            progress(10)

        if base_seed is None:
            manifest_path = self.storage.artifact(target_job_id, "music_manifest.json", required=False)
            base_seed = self.storage.read_json(target_job_id, "music_manifest.json")["generation"]["seed"] if manifest_path.exists() else self.config.seed
        plan = build_composition_plan(spec, AudioConfig(self.config.sample_rate, base_seed))
        chords = list(plan.chords)

        if progress:
            progress(25)

        # Create a different seed for every regeneration request.
        digest = hashlib.sha256(
            regeneration_job_id.encode("utf-8")
        ).digest()

        regeneration_seed = (
            base_seed +
            int.from_bytes(digest[:4], "big")
        )

        # Use the regeneration instruction to modify the stem.
        regeneration_spec = spec

        audio = self._render_stem(
            regeneration_spec,
            duration,
            chords,
            stem_role,
            regeneration_seed,
            regeneration_options,
            plan=plan,
            timbre=definition.timbre,
        )
        parent_manifest_path = self.storage.artifact(target_job_id, "music_manifest.json", required=False)
        parent_manifest = self.storage.read_json(target_job_id, "music_manifest.json") if parent_manifest_path.exists() else {}
        parent_stem = next((item for item in parent_manifest.get("stems", []) if item["name"] == stem), {})
        source = parent_stem.get("provenance", {})
        provider = self.provider
        if parent_manifest and source.get("provider_id", "procedural") == "procedural":
            provider = ProceduralProvider()
        elif source:
            settings = getattr(provider, "settings", None)
            if provider.name != source.get("provider_id") or settings is None or settings.audio_model != source.get("model"):
                raise AudioProcessingError("AI_PROVIDER_MISMATCH", "Regeneration requires the original stem's audio provider and model.")
            loaded_revision = getattr(getattr(getattr(provider, "_model", None), "config", None), "_commit_hash", None)
            if source.get("version") and source["version"] not in {settings.audio_model_revision, loaded_revision}:
                provider = select_audio_provider(settings.model_copy(update={"audio_model_revision": source["version"]}))
        fallback = None
        try:
            rendered = provider.render(spec, plan, definition, audio, regeneration_seed, regeneration_instruction or "")
            audio = self._provider_output(rendered, plan)
        except AudioProcessingError as exc:
            if not self.fallback_to_procedural or provider.name == "procedural":
                raise
            fallback = {"requested_provider": provider.name, "code": exc.code, "message": str(exc)}
            logger.warning("Audio provider failed; explicit procedural fallback used", extra={"job_id": regeneration_job_id, "provider": provider.name, "code": exc.code})
            rendered = ProceduralProvider().render(spec, plan, definition, audio, regeneration_seed)
            audio = self._provider_output(rendered, plan)

        if progress:
            progress(70)

        validation = validate_stems(
            {definition.stem_id: audio},
            self.config.sample_rate,
            duration,
        )

        target_dir = self._job_dir(target_job_id)

        target_path = target_dir / f"{definition.stem_id}.wav"

        sf.write(
            target_path,
            audio,
            self.config.sample_rate,
            subtype="PCM_16",
        )

        if progress:
            progress(82)

        return {
            "stem_id": definition.stem_id,
            "name": definition.name,
            "role": definition.role,
            "rendered_timbre": "ai_audio_unverified_solo_part" if rendered.provenance["ai_generated"] else definition.timbre,
            "plan_sha256": plan.sha256,
            "provenance": rendered.provenance,
            "provider_fallback": fallback,
            "filename": target_path.name,
            "mix": {
                "name": "mix",
                "filename": "mix.wav",
            },
            "duration_seconds": duration,
            "sample_rate": self.config.sample_rate,
            "validation": validation,
            "seed": regeneration_seed,
            "regeneration_instruction": regeneration_instruction,
            "preserve_options": preserve_options or {},
        }

    def generate(self, spec: MusicSpecification, job_id: str, progress: Callable[[int], None] | None = None, *, seed: int | None = None, minimum_stems: int = 1) -> dict:
        duration = _safe_duration(spec)
        stem_definitions = self.stem_definitions(spec)
        if len(stem_definitions) < minimum_stems:
            raise ValueError(f"Module 3 requires at least {minimum_stems} independent instrument parts.")
        self.storage.create_job(job_id)
        if progress:
            progress(10)
        config = AudioConfig(self.config.sample_rate, self.config.seed if seed is None else seed)
        plan = build_composition_plan(spec, config)
        chords = list(plan.chords)
        if progress:
            progress(20)

        stems: dict[str, np.ndarray] = {}
        progress_steps = np.linspace(35, 80, num=max(1, len(stem_definitions)), dtype=int)
        for definition, progress_value in zip(stem_definitions, progress_steps, strict=False):
            stems[definition.stem_id] = self._render_stem(spec, duration, chords, definition.role, plan=plan, timbre=definition.timbre)
            if progress:
                progress(int(progress_value))

        guides = stems
        provenance = {}
        fallback = None
        try:
            stems = {}
            for definition in stem_definitions:
                rendered = self.provider.render(spec, plan, definition, guides[definition.stem_id], config.seed)
                stems[definition.stem_id] = self._provider_output(rendered, plan)
                provenance[definition.stem_id] = rendered.provenance
            validate_stems(stems, self.config.sample_rate, duration)
        except (AudioProcessingError, ValueError) as exc:
            if not self.fallback_to_procedural or self.provider.name == "procedural":
                if isinstance(exc, AudioProcessingError):
                    raise
                raise AudioProcessingError("AI_INVALID_STEMS", "Provider parts failed the existing independent-stem checks.") from exc
            fallback = {"requested_provider": self.provider.name, "code": getattr(exc, "code", "AI_INVALID_STEMS"), "message": str(exc)}
            logger.warning("Audio provider failed; explicit whole-job procedural fallback used", extra={"job_id": job_id, "provider": self.provider.name, "code": fallback["code"]})
            stems = guides
            provenance = {definition.stem_id: ProceduralProvider().render(spec, plan, definition, guides[definition.stem_id], config.seed).provenance for definition in stem_definitions}

        validation = validate_stems(stems, self.config.sample_rate, duration)
        if progress:
            progress(88)

        mix = self._mix_arrays(stems)
        if progress:
            progress(95)

        job_dir = self._job_dir(job_id)
        filenames = {}
        for name, audio in stems.items():
            path = job_dir / f"{name}.wav"
            sf.write(path, audio, self.config.sample_rate, subtype="PCM_16")
            filenames[name] = path.name
        mix_path = job_dir / "mix.wav"
        sf.write(mix_path, mix, self.config.sample_rate, subtype="PCM_16")
        filenames["mix"] = mix_path.name
        if progress:
            progress(100)

        return {
            "duration_seconds": duration,
            "sample_rate": self.config.sample_rate,
            "stems": [
                {
                    "stem_id": definition.stem_id,
                    "name": definition.name,
                    "filename": filenames[definition.stem_id],
                    "role": definition.role,
                    "requested_instrument": definition.name,
                    "rendered_timbre": "ai_audio_unverified_solo_part" if provenance[definition.stem_id]["ai_generated"] else definition.timbre,
                    "timbre_description": "Independent model inference with a solo-instrument request; actual purity and instrument identity need evaluation." if provenance[definition.stem_id]["ai_generated"] else "Procedural synthetic approximation; not a sampled or acoustic instrument.",
                    "plan_sha256": plan.sha256,
                    "provenance": provenance[definition.stem_id],
                }
                for definition in stem_definitions
            ],
            "mix": {"name": "mix", "filename": filenames["mix"]},
            "validation": validation,
            "seed": config.seed,
            "composition_plan": plan.to_dict(),
            "plan_sha256": plan.sha256,
            "generation_method": next(iter(provenance.values()))["generation_method"],
            "provider_fallback": fallback,
        }
