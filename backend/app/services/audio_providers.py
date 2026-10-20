from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from importlib.metadata import version
from typing import TYPE_CHECKING, Protocol

import numpy as np
from scipy.signal import resample_poly

from app.services.audio_processing import AudioProcessingError

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.schemas.specification import MusicSpecification
    from app.services.music_generation import CompositionPlan, StemDefinition


@dataclass(frozen=True)
class ProviderOutput:
    audio: np.ndarray
    provenance: dict


class StemProvider(Protocol):
    name: str

    def render(self, specification: MusicSpecification, plan: CompositionPlan, definition: StemDefinition,
               guide: np.ndarray, seed: int, instruction: str = "") -> ProviderOutput: ...


class ProceduralProvider:
    name = "procedural"

    def render(self, specification, plan, definition, guide, seed, instruction=""):
        return ProviderOutput(guide.copy(), {
            "provider": "local", "provider_id": self.name, "model": None,
            "version": "local-renderer-2", "generation_method": "procedural_synthesis",
            "ai_generated": False, "seed": seed, "licence": None,
            "warnings": ["Audio is procedural synthesis, not an AI audio model."],
        })


class MusicGenMelodyProvider:
    name = "musicgen_melody"
    _inference_lock = threading.Lock()

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._processor = None
        self._torch = None

    def _load(self):
        if self._model is not None:
            return
        if not self.settings.audio_model_research_acknowledged:
            raise AudioProcessingError("AI_MODEL_TERMS_REQUIRED", "MusicGen Melody has non-commercial weights and unverified solo-stem fidelity. Explicit research acknowledgement is required.")
        try:
            import torch
            from transformers import AutoProcessor, MusicgenMelodyForConditionalGeneration

            options = {"revision": self.settings.audio_model_revision,
                       "cache_dir": self.settings.audio_model_cache_dir,
                       "local_files_only": self.settings.audio_model_local_files_only}
            token = self.settings.audio_model_token.get_secret_value()
            if token:
                options["token"] = token
            processor = AutoProcessor.from_pretrained(self.settings.audio_model, **options)
            model, loading = MusicgenMelodyForConditionalGeneration.from_pretrained(self.settings.audio_model, output_loading_info=True, **options)
            if model.config.model_type != "musicgen_melody" or loading.get("missing_keys") or loading.get("mismatched_keys") or loading.get("error_msgs"):
                raise ValueError("Pretrained checkpoint does not fully match the MusicGen Melody architecture")
            model.to(self.settings.audio_model_device)
            model.eval()
            self._torch, self._processor, self._model = torch, processor, model
        except Exception as exc:
            raise AudioProcessingError("AI_PROVIDER_UNAVAILABLE", "MusicGen Melody weights or optional dependencies are unavailable. No AI audio was generated.", {"exception_type": type(exc).__name__}) from exc

    def render(self, specification, plan, definition, guide, seed, instruction=""):
        if plan.duration_s > 29:
            raise AudioProcessingError("AI_DURATION_UNSUPPORTED", "This MusicGen adapter supports at most 29 seconds per inference; long audio is not tiled or misrepresented.", {"requested_duration_s": plan.duration_s, "maximum_duration_s": 29})
        with self._inference_lock:
            self._load()
            model, processor, torch = self._model, self._processor, self._torch
            model_rate = int(model.config.audio_encoder.sampling_rate)
            divisor = math.gcd(plan.sample_rate, model_rate)
            conditioning = resample_poly(guide, model_rate // divisor, plan.sample_rate // divisor).astype(np.float32)
            sections = "; ".join(f"{section.start_sample / plan.sample_rate:.2f}-{section.end_sample / plan.sample_rate:.2f}s {section.mood}" for section in plan.sections)
            prompt = (f"Solo {definition.name}, isolated {definition.role} instrument part only, instrumental, no vocals, no speech, no other instruments. "
                      f"{specification.genre}, {', '.join(specification.mood)}, {plan.bpm} BPM, {plan.key}, 4/4. "
                      f"Follow the supplied audio guide and sections: {sections}. End intentionally. {instruction}")
            started = time.monotonic()
            try:
                inputs = processor(audio=conditioning, sampling_rate=model_rate, text=[prompt], padding=True, return_tensors="pt")
                inputs = {name: value.to(model.device) for name, value in inputs.items()}
                devices = [model.device.index or 0] if model.device.type == "cuda" else []
                with torch.random.fork_rng(devices=devices), torch.inference_mode():
                    torch.manual_seed(seed % (2 ** 63 - 1))
                    if devices:
                        torch.cuda.manual_seed_all(seed % (2 ** 63 - 1))
                    generated = model.generate(**inputs, do_sample=True, guidance_scale=3.0,
                                               max_new_tokens=math.ceil(plan.duration_s * 50) + 4,
                                               max_time=self.settings.audio_model_timeout_seconds)
                if time.monotonic() - started > self.settings.audio_model_timeout_seconds:
                    raise AudioProcessingError("AI_PROVIDER_TIMEOUT", "MusicGen inference exceeded its configured time budget.")
                audio = generated[0].detach().cpu().float().numpy()
                if audio.ndim == 2:
                    audio = np.mean(audio, axis=0)
                if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
                    raise AudioProcessingError("AI_INVALID_AUDIO", "MusicGen returned empty, non-finite, or unsupported audio.")
                expected = round(plan.duration_s * model_rate)
                if len(audio) < expected:
                    raise AudioProcessingError("AI_INCOMPLETE_AUDIO", "MusicGen did not generate the full requested duration.")
                audio = resample_poly(audio[:expected], plan.sample_rate // divisor, model_rate // divisor).astype(np.float32)
                audio = audio[:plan.sample_count]
                if len(audio) != plan.sample_count or float(np.max(np.abs(audio))) <= 1e-7:
                    raise AudioProcessingError("AI_INVALID_AUDIO", "MusicGen output is silent or does not match the sample clock.")
            except AudioProcessingError:
                raise
            except Exception as exc:
                raise AudioProcessingError("AI_PROVIDER_FAILED", "MusicGen inference failed; no procedural audio is being labelled as AI.", {"exception_type": type(exc).__name__}) from exc
            revision = getattr(model.config, "_commit_hash", None)
            return ProviderOutput(audio, {
                "provider": "huggingface_transformers", "provider_id": self.name,
                "model": self.settings.audio_model, "version": revision,
                "requested_revision": self.settings.audio_model_revision,
                "generation_method": "ai_audio_conditioned_independent_inference",
                "ai_generated": True, "seed": seed,
                "licence": "CC-BY-NC-4.0" if self.settings.audio_model == "facebook/musicgen-melody" else None,
                "inference_time_s": time.monotonic() - started,
                "runtime_versions": {name: version(name) for name in ("torch", "transformers")},
                "prompt": prompt, "conditioning_plan_sha256": plan.sha256,
                "warnings": ["MusicGen weights are non-commercial (CC-BY-NC-4.0).",
                             "Separate solo requests are not proof of stem purity, exact BPM/key adherence, or vocal absence.",
                             "Seeded inference is not a cross-hardware bitwise determinism guarantee.",
                             "Melody conditioning is weaker for low-frequency bass and unpitched drums."],
            })


def select_audio_provider(settings: Settings) -> StemProvider:
    if settings.audio_provider == "procedural":
        return ProceduralProvider()
    if settings.audio_provider == "musicgen_melody":
        return MusicGenMelodyProvider(settings)
    raise AudioProcessingError("INVALID_AUDIO_PROVIDER", "Unknown audio provider.")