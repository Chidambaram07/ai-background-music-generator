from __future__ import annotations

from typing import Any, Literal
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.specification import MusicSection, MusicSpecification


class OutputSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_rate: Literal[48000] = 48000
    target_lufs: Literal[-16.0] = -16.0
    formats: list[Literal["wav", "mp3"]] = Field(default_factory=lambda: ["wav"])

    @field_validator("formats")
    @classmethod
    def require_wav(cls, value):
        if "wav" not in value or len(set(value)) != len(value):
            raise ValueError("formats must include wav and contain no duplicates")
        return value


class SectionChange(BaseModel):
    model_config = ConfigDict(extra="forbid",allow_inf_nan=False)
    start_s: float = Field(ge=0)
    mood: str = Field(min_length=1)


class MusicJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    prompt: str = Field(min_length=1, description="Natural-language music description")
    duration_s: float = Field(gt=0, description="Requested duration in seconds")
    sections: list[MusicSection | SectionChange] = Field(default_factory=list)
    tempo_bpm: int | None = Field(default=None, ge=40, le=240)
    key: str | None = None
    loopable: bool = False
    narration_url: str | None = None
    seed: int = 42
    output: OutputSettings = Field(default_factory=OutputSettings)

    @model_validator(mode="after")
    def validate_sections(self):
        if any(isinstance(section,SectionChange) for section in self.sections):
            if not all(isinstance(section,SectionChange) for section in self.sections):
                raise ValueError("Do not mix start_s change points and start/end section ranges")
            self.sections = [MusicSection(start=section.start_s,
                end=self.sections[index+1].start_s if index+1 < len(self.sections) else self.duration_s,
                mood=section.mood) for index,section in enumerate(self.sections)]
        previous_end = 0.0
        for section in self.sections:
            if abs(section.start - previous_end) > 1e-6 or section.end > self.duration_s or section.end - section.start < 1.5:
                raise ValueError("sections must cover the track in order, without gaps/overlaps, and be at least 1.5 seconds long")
            previous_end = section.end
        if self.sections and abs(previous_end - self.duration_s) > 1e-6:
            raise ValueError("final section must end at duration_s")
        if self.key is not None:
            from app.schemas.specification import KEY_PATTERN
            if not KEY_PATTERN.fullmatch(self.key):
                raise ValueError("key must use a format such as C major")
        if self.narration_url:
            locator = urlsplit(self.narration_url)
            uploaded = re.fullmatch(r"/api/v1/music/files/[a-f0-9]{32}\.wav", locator.path) and not locator.query and not locator.fragment
            remote = locator.scheme in {"https", "http"} and locator.hostname and not locator.username and not locator.password and not locator.fragment
            if not uploaded and not remote:
                raise ValueError("narration_url must be an uploaded WAV locator or an HTTP(S) URL without embedded credentials")
        return self

    @field_validator("prompt")
    @classmethod
    def trim_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must not be empty")
        return value


class MusicGenerationRequest(BaseModel):
    """Module 3 accepts only the already-validated Module 2 specification."""

    specification: MusicSpecification
    seed: int | None = Field(default=None, ge=0, le=9223372036854775807)


class VoiceoverDuckingRequest(BaseModel):
    voiceover_path: str = Field(min_length=1, description="Path to the input voiceover WAV file")
    music_path: str | None = Field(default=None, description="Path to the music mix to duck; defaults to the generated mix for the provided job")
    duck_db: float = Field(default=12.0, ge=8.0, le=15.0, description="Music reduction during speech, in dB")
    attack_ms: float = Field(default=50.0, gt=0.0, le=500.0, description="Fade-in time for ducking")
    release_ms: float = Field(default=300.0, gt=0.0, le=2000.0, description="Fade-out time for ducking recovery")


class MusicJobAccepted(BaseModel):
    job_id: str
    status: str


class StemOutput(BaseModel):
    stem_id: str | None = None
    name: str
    filename: str
    url: str


class MusicGenerationResult(BaseModel):
    job_id: str
    status: str
    duration_seconds: float
    sample_rate: int
    stems: list[StemOutput]
    mix: StemOutput
    validation: dict


class StemListItem(BaseModel):
    stem_id: str
    name: str
    filename: str
    url: str


class StemListResponse(BaseModel):
    job_id: str
    stems: list[StemListItem]
    mix_url: str


class StemRegenerationAccepted(BaseModel):
    job_id: str
    stem_id: str
    status: str


class ValidationRequirement(BaseModel):
    requirement_id: str
    name: str
    category: str
    status: str
    summary: str
    evidence: dict[str, Any]
    associated_job_id: str | None = None


class Ps1ValidationReport(BaseModel):
    job_id: str
    overall_status: str
    passed_count: int
    warning_count: int
    failed_count: int
    percentage: float
    validated_at: str
    requirements: list[ValidationRequirement]
