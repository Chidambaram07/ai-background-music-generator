from functools import lru_cache
from typing import Literal
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "IncuBrix PS1 Music Engine"
    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    database_path: str = "./data/jobs.db"
    frontend_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    job_simulation_step_seconds: float = 0.35
    max_prompt_length: int = 1000
    max_request_bytes: int = Field(default=33554432, gt=0)
    narration_max_bytes: int = Field(default=33554432, gt=0)
    narration_timeout_seconds: float = Field(default=20, gt=0)
    narration_max_redirects: int = Field(default=3, ge=0, le=10)
    narration_allow_http: bool = False
    narration_allowed_private_hosts: str = ""
    storage_backend: Literal["local", "s3"] = "local"
    s3_endpoint_url: str = "http://127.0.0.1:9000"
    s3_public_endpoint_url: str = "http://127.0.0.1:9000"
    s3_region: str = "us-east-1"
    s3_bucket: str = "incubrix-outputs"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    s3_url_ttl_seconds: int = Field(default=900, ge=60, le=604800)
    s3_timeout_seconds: float = Field(default=5, gt=0)
    min_duration_seconds: float = 5.0
    max_duration_seconds: float = 300.0
    default_duration_seconds: float = 45.0
    llm_provider: str = "auto"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 30.0
    hf_base_url: str = "https://router.huggingface.co/v1"
    hf_api_key: str = ""
    hf_model: str = "openai/gpt-oss-20b:fastest"
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_api_key: str = ""
    ollama_model: str = "qwen2.5:7b"
    ollama_enabled: bool = False
    llm_auto_fallback_to_mock: bool = True
    audio_output_dir: str = "./outputs"
    audio_sample_rate: int = 44100
    audio_seed: int = 4281097
    audio_provider: Literal["procedural", "musicgen_melody"] = "procedural"
    audio_provider_fallback: bool = False
    audio_model: str = "facebook/musicgen-melody"
    audio_model_revision: str = "main"
    audio_model_cache_dir: str = "./data/models"
    audio_model_local_files_only: bool = True
    audio_model_device: Literal["cpu", "cuda"] = "cpu"
    audio_model_timeout_seconds: float = Field(default=120.0, gt=0)
    audio_model_research_acknowledged: bool = False
    audio_model_token: SecretStr = SecretStr("")
    audio_processing_sample_rate: int = 48000
    audio_target_lufs: float = -16.0
    audio_loudness_tolerance_lufs: float = 1.0
    audio_true_peak_limit_dbtp: float = -1.0
    audio_duration_tolerance_seconds: float = 0.05
    audio_fade_in_ms: float = 10.0
    audio_fade_out_ms: float = 10.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.frontend_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
