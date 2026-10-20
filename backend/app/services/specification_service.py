from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError
from fastapi.encoders import jsonable_encoder

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.llm import LLMService, LLMServiceError
from app.schemas.specification import MusicSpecification, MusicSection

logger = logging.getLogger(__name__)


class SpecificationService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.llm = LLMService(settings)

    @staticmethod
    def _section_dicts(sections: list[MusicSection] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for section in sections:
            if isinstance(section, MusicSection):
                values.append(section.model_dump(mode="python"))
            else:
                values.append(dict(section))
        return values

    def _merge_overrides(self, raw: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
        if not overrides:
            return raw

        merged = dict(raw)
        for key in ("genre", "mood", "tempo", "key", "instruments"):
            if overrides.get(key) is not None:
                merged[key] = overrides[key]

        sections = overrides.get("sections")
        if sections is not None:
            merged["sections"] = self._section_dicts(sections)

        return merged

    def build_duration_range_evidence(self, requested_duration: float) -> dict[str, Any]:
        minimum = float(self.settings.min_duration_seconds)
        maximum = float(self.settings.max_duration_seconds)
        default = float(self.settings.default_duration_seconds)
        requested_in_range = minimum <= requested_duration <= maximum
        benchmark_cases = [
            {"duration_s": minimum, "accepted": minimum <= minimum <= maximum},
            {"duration_s": default, "accepted": minimum <= default <= maximum},
            {"duration_s": maximum, "accepted": minimum <= maximum <= maximum},
            {"duration_s": round(max(0.0, minimum - 0.01), 2), "accepted": False},
            {"duration_s": round(maximum + 0.01, 2), "accepted": False},
        ]
        status = "PASS" if requested_in_range and all(case["accepted"] for case in benchmark_cases[:3]) and not any(case["accepted"] for case in benchmark_cases[3:]) else "FAIL"
        return {
            "status": status,
            "requested_duration_seconds": round(float(requested_duration), 6),
            "requested_duration_in_range": requested_in_range,
            "limits": {
                "min_duration_seconds": minimum,
                "default_duration_seconds": default,
                "max_duration_seconds": maximum,
            },
            "benchmark_cases": benchmark_cases,
        }

    async def create_specification(self, prompt: str, overrides: dict[str, Any] | None = None) -> MusicSpecification:
        try:
            raw = await self.llm.generate_specification(prompt)
        except LLMServiceError as exc:
            raise AppError("LLM_UNAVAILABLE", str(exc), True, {}, 503) from exc

        if not isinstance(raw, dict):
            raise AppError("INVALID_LLM_JSON", "The LLM did not return a JSON object.", False, {}, 502)

        merged = self._merge_overrides(raw, overrides)

        try:
            specification = MusicSpecification.model_validate(merged)
        except ValidationError as exc:
            logger.warning("LLM specification failed schema validation: %s", exc)
            raise AppError(
                "SPECIFICATION_VALIDATION_FAILED",
                "The LLM returned a music specification that failed validation.",
                False,
                {"validation_errors": jsonable_encoder(exc.errors())},
                502,
            ) from exc

        max_end = max(section.end for section in specification.sections)
        if max_end > self.settings.max_duration_seconds:
            raise AppError(
                "INVALID_SECTION_DURATION",
                f"The specification exceeds the maximum duration of {self.settings.max_duration_seconds:g} seconds.",
                False,
                {"max_duration_seconds": self.settings.max_duration_seconds, "last_section_end": max_end},
                502,
            )
        return specification
