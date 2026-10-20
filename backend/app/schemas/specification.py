from __future__ import annotations

import re
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator


KEY_PATTERN = re.compile(r"^[A-G](?:#|b)? (?:major|minor)$", re.IGNORECASE)


class MusicSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, description="Optional section label such as INTRO or BUILD")
    start: float = Field(ge=0, description="Section start time in seconds")
    end: float = Field(gt=0, description="Section end time in seconds")
    mood: str = Field(min_length=1, description="Mood for this section")

    @model_validator(mode="after")
    def validate_range(self) -> "MusicSection":
        if self.end <= self.start:
            raise ValueError("section end must be greater than start")
        if self.name is not None and not self.name.strip():
            raise ValueError("section name must not be empty when provided")
        return self


class TimelineSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    start_s: float = Field(ge=0.0, description="Section start in seconds")
    end_s: float = Field(gt=0.0, description="Section end in seconds")
    duration_s: float = Field(gt=0.0, description="Section duration in seconds")

    @model_validator(mode="after")
    def validate_timing(self) -> "TimelineSection":
        if self.end_s <= self.start_s:
            raise ValueError("section end_s must be greater than start_s")
        if abs(self.duration_s - (self.end_s - self.start_s)) > 1e-3:
            raise ValueError("section duration_s must match end_s - start_s within tolerance")
        return self


class TimelineManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str | None = None
    duration_s: float = Field(gt=0.0)
    genre: str = Field(min_length=1)
    mood: list[StrictStr] = Field(min_length=1)
    instruments: list[StrictStr] = Field(min_length=1)
    stem_count: int = Field(gt=0)
    bpm: int = Field(gt=0, le=240)
    key: str = Field(min_length=1)
    beats_per_bar: int = Field(gt=0, le=8, default=4)
    beat_duration_s: float | None = Field(default=None, gt=0.0)
    bar_duration_s: float | None = Field(default=None, gt=0.0)
    sections: list[TimelineSection] = Field(min_length=1)
    commercial_rights: CommercialRightsMetadata | None = None

    @model_validator(mode="after")
    def validate_manifest(self) -> "TimelineManifest":
        if not self.sections:
            raise ValueError("manifest must include at least one section")
        if self.sections[0].start_s != 0.0:
            raise ValueError("first section must start at 0 seconds")
        if abs(self.sections[-1].end_s - self.duration_s) > 1e-3:
            raise ValueError("final section must end at the track duration")

        if self.beat_duration_s is None:
            self.beat_duration_s = 60.0 / self.bpm
        if self.bar_duration_s is None:
            self.bar_duration_s = self.beat_duration_s * self.beats_per_bar

        previous_end = 0.0
        for index, section in enumerate(self.sections):
            if section.start_s < previous_end - 1e-6:
                overlap_s = previous_end - section.start_s
                if overlap_s > 3.0:
                    raise ValueError("sections may overlap only within a small tolerance window")
            if section.start_s > previous_end + 1e-6:
                # Gaps are allowed in the stored manifest as a conscious section layout.
                pass
            previous_end = max(previous_end, section.end_s)
        return self


class CommercialRightsMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rights_source: str = Field(min_length=1)
    license_name: str = Field(min_length=1)
    commercial_use_permitted: bool = True
    third_party_material_used: bool = False
    summary: str = Field(min_length=1)


class MusicSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    genre: str = Field(min_length=1)
    mood: list[StrictStr] = Field(min_length=1)
    tempo: int = Field(ge=40, le=240)
    key: str = Field(min_length=3)
    instruments: list[StrictStr] = Field(min_length=1)
    sections: list[MusicSection] = Field(min_length=1)

    @field_validator("genre", "key")
    @classmethod
    def trim_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    @field_validator("mood", "instruments")
    @classmethod
    def validate_string_lists(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if isinstance(value, str) and value.strip()]
        if not cleaned:
            raise ValueError("list must contain at least one non-empty string")
        return cleaned

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if not KEY_PATTERN.match(value):
            raise ValueError("key must use a format such as 'C major', 'A minor', 'F# major', or 'Bb minor'")
        return value.title().replace("#", "#").replace("B", "b") if False else value

    @model_validator(mode="after")
    def validate_sections(self) -> "MusicSpecification":
        previous_end = 0.0
        for index, section in enumerate(self.sections):
            if index > 0 and section.start < previous_end:
                raise ValueError("sections must be chronologically ordered and must not overlap")
            previous_end = section.end
        return self

    def timeline_manifest(self, job_id: str | None = None) -> TimelineManifest:
        bpm = int(self.tempo)
        beat_duration_s = 60.0 / bpm
        bar_duration_s = beat_duration_s * 4
        normalized_sections: list[TimelineSection] = []
        duration = max(float(section.end) for section in self.sections)
        for index, section in enumerate(self.sections):
            start = float(section.start)
            end = float(section.end)
            name = (section.name or "").strip() or self._default_section_name(index, len(self.sections))
            normalized_sections.append(TimelineSection(name=name, start_s=start, end_s=end, duration_s=end - start))
        return TimelineManifest(
            job_id=job_id,
            duration_s=duration,
            genre=self.genre,
            mood=list(self.mood),
            instruments=list(self.instruments),
            stem_count=len(self.instruments),
            bpm=bpm,
            key=self.key,
            beats_per_bar=4,
            beat_duration_s=beat_duration_s,
            bar_duration_s=bar_duration_s,
            sections=normalized_sections,
            commercial_rights=CommercialRightsMetadata(
                rights_source="backend_deterministic_synthesis",
                license_name="Original backend-generated composition",
                commercial_use_permitted=True,
                third_party_material_used=False,
                summary="Generated entirely from backend-synthesized stems without third-party samples or loops."
            ),
        )

    @staticmethod
    def _default_section_name(index: int, total: int) -> str:
        labels = ["INTRO", "BUILD", "MAIN", "OUTRO"]
        if total <= len(labels):
            return labels[index] if index < len(labels) else f"SECTION_{index + 1}"
        if index < len(labels):
            return labels[index]
        return f"SECTION_{index + 1}"


class SpecificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=1000, description="Natural-language music request")
    genre: str | None = Field(default=None, min_length=1)
    mood: list[StrictStr] | None = Field(default=None, min_length=1)
    tempo: int | None = Field(default=None, ge=40, le=240)
    key: str | None = Field(default=None, min_length=3)
    instruments: list[StrictStr] | None = Field(default=None, min_length=1)
    sections: list[MusicSection] | None = Field(default=None, min_length=1)

    @field_validator("prompt")
    @classmethod
    def trim_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must not be empty")
        return value

    @field_validator("genre", "key")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    @field_validator("mood", "instruments")
    @classmethod
    def validate_optional_string_lists(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        cleaned = [value.strip() for value in values if isinstance(value, str) and value.strip()]
        if not cleaned:
            raise ValueError("list must contain at least one non-empty string")
        return cleaned


class SpecificationResponse(BaseModel):
    specification: MusicSpecification
