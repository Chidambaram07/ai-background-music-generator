from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a music specification engine.

Your only task is to convert a user's natural-language music request into a structured music specification for a deterministic downstream music engine.

Do not generate audio. Do not provide explanations. Do not return Markdown, code fences, comments, or any text outside the JSON object.

Return ONLY valid JSON with exactly these top-level fields:
- genre: non-empty string
- mood: array of one or more strings
- tempo: integer BPM from 40 to 240
- key: string in the form '<note> major' or '<note> minor', where note is A-G with optional # or b
- instruments: array of one or more strings
- sections: array of timeline objects, each containing start, end, and mood

Use seconds for section start/end. Sections must be chronologically ordered, must not overlap, and should cover the requested musical progression. The final section should normally end at the requested duration. If no duration is specified, use the application's configured default duration.

Infer reasonable musical defaults when the user omits tempo, key, instruments, or exact section boundaries. Preserve explicit user preferences when present. If the user describes a progression such as peaceful to hopeful, represent it with multiple sections and appropriate section moods.

Do not invent unnecessary properties. Keep the output compact and directly usable by the music engine."""
STEM_INSTRUCTION_PROMPT = """You are a music production instruction parser.

Convert the user's regeneration instruction into structured parameters for a drum/bass/harmony/melody renderer.

Stem: {stem}

Return ONLY valid JSON with exactly these numeric fields (each from 0.0 to 1.0):
- energy: overall intensity of the pattern (0.0 = very sparse/quiet, 1.0 = very intense/loud)
- kick_density: how many kick hits per bar (0.0 = minimal kicks, 1.0 = kick on every subdivision)
- snare_strength: snare amplitude relative to default (0.0 = very quiet, 0.5 = default, 1.0 = very loud)
- hat_density: how busy the hi-hats are (0.0 = no hats, 1.0 = constant hats)
- syncopation: how off-beat / unpredictable the pattern is (0.0 = straight, 1.0 = heavily syncopated)
- variation: how much the pattern changes over time (0.0 = static loop, 1.0 = constantly evolving)

Preserve: tempo, key, song duration, section boundaries, overall musical context.

Do not generate audio. Do not provide explanations. Return JSON only.

User instruction:
{instruction}"""


class LLMServiceError(Exception):
    """Raised when the configured LLM cannot produce a valid specification."""


class LLMService:
    """Provider-neutral LLM adapter for Module 2.

    Supported providers:
      - mock: deterministic local fallback; no network or API key required
      - openai: OpenAI-compatible endpoint configured by LLM_BASE_URL
      - huggingface: Hugging Face Inference Providers router
      - ollama: local Ollama OpenAI-compatible endpoint
      - openai_compatible: arbitrary OpenAI-compatible endpoint
      - auto: choose a configured provider, with safe fallback to mock

    The frontend never receives provider credentials. Only this backend service
    talks to external/local LLM providers.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    # ------------------------------------------------------------------
    # Module 2: Full music specification generation
    # ------------------------------------------------------------------

    async def generate_specification(self, user_prompt: str) -> dict[str, Any]:
        provider = self.settings.llm_provider.strip().lower()

        if provider == "mock":
            return self._mock_specification(user_prompt)

        if provider == "auto":
            return await self._generate_auto(user_prompt)

        if provider in {"openai", "openai_compatible"}:
            return await self._call_openai_compatible(
                user_prompt=user_prompt,
                base_url=self.settings.llm_base_url,
                api_key=self.settings.llm_api_key,
                model=self.settings.llm_model,
                provider_name="OpenAI" if provider == "openai" else "OpenAI-compatible provider",
            )

        if provider in {"huggingface", "hf"}:
            if not self.settings.hf_api_key:
                raise LLMServiceError("HF_API_KEY is not configured for the Hugging Face provider.")
            return await self._call_openai_compatible(
                user_prompt=user_prompt,
                base_url=self.settings.hf_base_url,
                api_key=self.settings.hf_api_key,
                model=self.settings.hf_model,
                provider_name="Hugging Face",
            )

        if provider == "ollama":
            return await self._call_openai_compatible(
                user_prompt=user_prompt,
                base_url=self.settings.ollama_base_url,
                api_key=self.settings.ollama_api_key or "ollama",
                model=self.settings.ollama_model,
                provider_name="Ollama",
            )

        raise LLMServiceError(f"Unsupported LLM_PROVIDER '{self.settings.llm_provider}'.")

    # ------------------------------------------------------------------
    # Module 4: Stem regeneration instruction interpretation
    # ------------------------------------------------------------------

    async def interpret_stem_instruction(
        self,
        instruction: str,
        stem: str,
    ) -> dict[str, float]:
        """Convert a free-text regeneration instruction into structured
        musical parameters using the configured LLM provider.

        Falls back to a deterministic keyword-based parser when no LLM
        provider is available, so the application works without credentials.
        """
        if not instruction or not instruction.strip():
            return self._mock_stem_instruction("", stem)

        provider = self.settings.llm_provider.strip().lower()

        if provider == "mock":
            return self._mock_stem_instruction(instruction, stem)

        # For auto / named providers, try the LLM first.
        prompt_text = STEM_INSTRUCTION_PROMPT.format(
            stem=stem,
            instruction=instruction,
        )
        try:
            result = await self._call_stem_instruction_llm(prompt_text)
            return self._validate_stem_params(result)
        except (LLMServiceError, Exception) as exc:
            logger.warning(
                "LLM stem instruction interpretation failed (%s); using keyword fallback.",
                exc,
            )
            return self._mock_stem_instruction(instruction, stem)

    async def _call_stem_instruction_llm(self, prompt_text: str) -> dict[str, Any]:
        """Call the configured LLM with the stem-instruction prompt."""
        candidates: list[tuple[str, str, str, str]] = []

        if self.settings.hf_api_key:
            candidates.append((
                "Hugging Face",
                self.settings.hf_base_url,
                self.settings.hf_api_key,
                self.settings.hf_model,
            ))
        if self.settings.llm_api_key:
            candidates.append((
                "OpenAI",
                self.settings.llm_base_url,
                self.settings.llm_api_key,
                self.settings.llm_model,
            ))
        if self.settings.ollama_enabled:
            candidates.append((
                "Ollama",
                self.settings.ollama_base_url,
                self.settings.ollama_api_key or "ollama",
                self.settings.ollama_model,
            ))

        if not candidates:
            raise LLMServiceError("No LLM provider configured for stem instruction parsing.")

        for provider_name, base_url, api_key, model in candidates:
            try:
                url = base_url.rstrip("/") + "/chat/completions"
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "user", "content": prompt_text},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                }
                headers = {"Content-Type": "application/json"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    body = response.json()
                return self._parse_json_response(body, provider_name)
            except Exception as exc:
                logger.warning("%s failed for stem instruction: %s", provider_name, exc)
                continue

        raise LLMServiceError("All LLM providers failed for stem instruction parsing.")

    @staticmethod
    def _validate_stem_params(raw: dict[str, Any]) -> dict[str, float]:
        """Clamp each parameter to [0.0, 1.0] and fill missing keys."""
        defaults = {
            "energy": 0.5,
            "kick_density": 0.5,
            "snare_strength": 0.5,
            "hat_density": 0.5,
            "syncopation": 0.5,
            "variation": 0.5,
        }
        result: dict[str, float] = {}
        for key, default in defaults.items():
            try:
                val = float(raw.get(key, default))
            except (TypeError, ValueError):
                val = default
            result[key] = max(0.0, min(1.0, val))
        return result

    @staticmethod
    def _mock_stem_instruction(instruction: str, stem: str) -> dict[str, float]:
        """Deterministic keyword-based fallback when no LLM is configured.

        Produces a structured parameter dict from keyword analysis so the
        renderer always receives meaningful values, even without a provider.
        """
        text = (instruction or "").lower()

        energy = 0.5
        kick_density = 0.3
        snare_strength = 0.5
        hat_density = 0.5
        syncopation = 0.0
        variation = 0.0

        # Energy / intensity
        if "extremely energetic" in text or "very energetic" in text:
            energy = 0.95
        elif "energetic" in text or "powerful" in text or "aggressive" in text:
            energy = 0.8
        elif "more energy" in text:
            energy = 0.75
        elif "soft" in text or "gentle" in text or "quiet" in text:
            energy = 0.2
        elif "minimal" in text or "sparse" in text or "laid-back" in text or "relaxed" in text:
            energy = 0.25

        # Kick density
        if "many extra kick" in text or "lots of kick" in text or "extra kick hit" in text:
            kick_density = 0.95
        elif "extra kick" in text or "more kick" in text:
            kick_density = 0.8
        elif "strong kick" in text or "stronger kick" in text or "heavy kick" in text:
            kick_density = 0.7
        elif "fewer kick" in text or "less kick" in text:
            kick_density = 0.2
        elif energy > 0.7:
            kick_density = energy * 0.9
        elif energy < 0.35:
            kick_density = energy * 0.8

        # Snare
        if "much stronger snare" in text:
            snare_strength = 0.95
        elif "stronger snare" in text or "strong snare" in text or "heavy snare" in text:
            snare_strength = 0.8
        elif "steady snare" in text:
            snare_strength = 0.6
        elif "soft snare" in text or "quiet snare" in text:
            snare_strength = 0.2
        elif energy > 0.7:
            snare_strength = 0.7

        # Hat density
        if "busy hi-hat" in text or "busy hat" in text or "lots of hi-hat" in text:
            hat_density = 0.9
        elif "more hat" in text or "more hi-hat" in text:
            hat_density = 0.75
        elif "fewer hat" in text or "less hat" in text or "no hat" in text:
            hat_density = 0.15
        elif energy > 0.7:
            hat_density = 0.7
        elif energy < 0.35:
            hat_density = 0.3

        # Syncopation
        if "syncopated" in text or "unpredictable" in text or "off-beat" in text:
            syncopation = 0.85
        elif "aggressive" in text:
            syncopation = 0.75
        elif "straight" in text or "steady" in text:
            syncopation = 0.2
        elif energy > 0.7:
            syncopation = 0.6

        # Variation
        if "live performance" in text or "constantly changing" in text:
            variation = 0.85
        elif "varied" in text or "dynamic" in text:
            variation = 0.75
        elif "static" in text or "loop" in text or "repetitive" in text:
            variation = 0.2
        elif energy > 0.7:
            variation = 0.65

        return {
            "energy": round(energy, 2),
            "kick_density": round(kick_density, 2),
            "snare_strength": round(snare_strength, 2),
            "hat_density": round(hat_density, 2),
            "syncopation": round(syncopation, 2),
            "variation": round(variation, 2),
        }


    async def _generate_auto(self, user_prompt: str) -> dict[str, Any]:
        """Use the first configured provider, then fall back safely.

        Priority is Hugging Face -> OpenAI -> Ollama. This makes the project
        easy to run with a free/low-cost hosted provider while still allowing
        local inference. If all configured providers fail, mock mode keeps the
        application usable for development and tests.
        """

        candidates: list[tuple[str, str, str, str, str]] = []

        if self.settings.hf_api_key:
            candidates.append(
                (
                    "Hugging Face",
                    self.settings.hf_base_url,
                    self.settings.hf_api_key,
                    self.settings.hf_model,
                    "huggingface",
                )
            )

        if self.settings.llm_api_key:
            candidates.append(
                (
                    "OpenAI",
                    self.settings.llm_base_url,
                    self.settings.llm_api_key,
                    self.settings.llm_model,
                    "openai",
                )
            )

        if self.settings.ollama_enabled:
            candidates.append(
                (
                    "Ollama",
                    self.settings.ollama_base_url,
                    self.settings.ollama_api_key or "ollama",
                    self.settings.ollama_model,
                    "ollama",
                )
            )

        if not candidates:
            logger.warning("No LLM credentials/provider configured; using deterministic local specification fallback.")
            return self._mock_specification(user_prompt)

        errors: list[str] = []
        for provider_name, base_url, api_key, model, _provider_id in candidates:
            try:
                return await self._call_openai_compatible(
                    user_prompt=user_prompt,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    provider_name=provider_name,
                )
            except LLMServiceError as exc:
                errors.append(f"{provider_name}: {exc}")
                logger.warning("%s provider failed; trying next configured provider", provider_name)

        if self.settings.llm_auto_fallback_to_mock:
            logger.warning("All configured LLM providers failed; using deterministic local specification fallback.")
            return self._mock_specification(user_prompt)

        raise LLMServiceError("All configured LLM providers failed: " + " | ".join(errors))

    async def _call_openai_compatible(
        self,
        *,
        user_prompt: str,
        base_url: str,
        api_key: str,
        model: str,
        provider_name: str,
    ) -> dict[str, Any]:
        if not base_url.strip():
            raise LLMServiceError(f"{provider_name} base URL is not configured.")
        if not model.strip():
            raise LLMServiceError(f"{provider_name} model is not configured.")

        url = base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                    + f"\n\nConfigured default duration: {self.settings.default_duration_seconds:g} seconds.",
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        logger.info("Calling LLM provider: model=%s, url=%s", model, url)

        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code >= 400:
                    logger.warning("%s provider returned HTTP %s", provider_name, response.status_code)
                    try:
                        logger.warning("%s provider response: %s", provider_name, response.text[:2000])
                    except Exception:
                        pass
                response.raise_for_status()
                body = response.json()
        except httpx.TimeoutException as exc:
            raise LLMServiceError(f"{provider_name} request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                raise LLMServiceError(f"{provider_name} authentication failed. Check the API token.") from exc
            if status == 403:
                raise LLMServiceError(f"{provider_name} rejected the request. Check model/provider access.") from exc
            if status == 429:
                raise LLMServiceError(f"{provider_name} rate limit or quota was exceeded.") from exc
            raise LLMServiceError(f"{provider_name} returned HTTP {status}.") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMServiceError(f"Unable to reach {provider_name}.") from exc

        return self._parse_json_response(body, provider_name)

    @staticmethod
    def _parse_json_response(body: dict[str, Any], provider_name: str) -> dict[str, Any]:
        try:
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            if not isinstance(content, str):
                raise ValueError("LLM content was not a string")

            # Be defensive with providers that ignore JSON mode and wrap the
            # object in a code fence. The system prompt still requires JSON-only.
            cleaned = content.strip()
            if cleaned.startswith("```") and cleaned.endswith("```"):
                lines = cleaned.splitlines()
                if lines and lines[0].strip().lower().startswith("```json"):
                    lines = lines[1:]
                elif lines:
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()

            parsed = json.loads(cleaned)
            if not isinstance(parsed, dict):
                raise ValueError("LLM JSON was not an object")
            return parsed
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMServiceError(f"{provider_name} returned invalid JSON.") from exc

    def _mock_specification(self, prompt: str) -> dict[str, Any]:
        text = prompt.lower()
        duration = self.settings.default_duration_seconds

        if "electronic" in text or "edm" in text:
            genre = "electronic"
        elif "ambient" in text or "meditation" in text:
            genre = "ambient"
        elif "lo-fi" in text or "lofi" in text:
            genre = "lo-fi"
        elif "nature" in text or "documentary" in text or "cinematic" in text or "film" in text:
            genre = "cinematic"
        else:
            genre = "cinematic"

        moods: list[str] = []
        for candidate in [
            "peaceful",
            "hopeful",
            "energetic",
            "sad",
            "relaxing",
            "calm",
            "dark",
            "tense",
            "inspiring",
        ]:
            if candidate in text:
                moods.append(candidate)
        if not moods:
            moods = ["calm"]

        if "energetic" in moods or "electronic" in genre:
            tempo = 125
        elif "sad" in moods or "meditation" in text or "relaxing" in moods:
            tempo = 68
        elif "peaceful" in moods or "calm" in moods:
            tempo = 82
        else:
            tempo = 100

        if "piano" in text:
            instruments = ["piano"]
        elif "electronic" in genre:
            instruments = ["synth", "bass", "electronic percussion", "pads"]
        elif "ambient" in genre:
            instruments = ["soft pads", "piano", "subtle textures"]
        else:
            instruments = ["piano", "soft pads", "bass", "light percussion"]

        if len(moods) >= 2 and ("gradually" in text or "becoming" in text or "progress" in text):
            midpoint = round(duration / 2, 2)
            sections = [
                {"start": 0, "end": midpoint, "mood": moods[0]},
                {"start": midpoint, "end": duration, "mood": moods[1]},
            ]
        else:
            sections = [{"start": 0, "end": duration, "mood": moods[0]}]

        return {
            "genre": genre,
            "mood": moods,
            "tempo": tempo,
            "key": "C major" if "sad" not in moods else "A minor",
            "instruments": instruments,
            "sections": sections,
        }

    def build_prompt_variety_evidence(self) -> dict[str, Any]:
        prompts = [
            "A peaceful cinematic underscore with piano and warm pads",
            "An energetic electronic trailer cue with bass and percussion",
            "A minimal ambient meditation bed with subtle textures",
        ]
        outputs = [self._mock_specification(prompt) for prompt in prompts]
        signatures = [
            {
                "genre": output.get("genre"),
                "tempo": output.get("tempo"),
                "key": output.get("key"),
                "mood": output.get("mood"),
                "instruments": output.get("instruments"),
            }
            for output in outputs
        ]
        distinct_signature_count = len({json.dumps(signature, sort_keys=True) for signature in signatures})
        status = "PASS" if distinct_signature_count == len(prompts) else "FAIL"
        return {
            "status": status,
            "benchmark": "deterministic_mock_specification",
            "prompt_count": len(prompts),
            "prompts": prompts,
            "outputs": signatures,
            "distinct_signature_count": distinct_signature_count,
        }
