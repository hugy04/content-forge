"""Central configuration. Every knob is env-overridable; nothing is hardcoded in a stage."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- text generation ---
    anthropic_api_key: str = ""
    outline_model: str = "claude-opus-5-5"
    draft_model: str = "claude-opus-5-5"
    prompt_model: str = "claude-opus-5-5"
    outline_effort: str = "medium"
    draft_effort: str = "medium"
    prompt_effort: str = "low"

    # --- content contract ---
    target_words_min: int = 500
    target_words_max: int = 800
    max_revisions: int = 2
    deadline_seconds: int = 300

    # --- image generation ---
    image_providers: str = "gemini,pollinations,card"
    image_width: int = 1280
    image_height: int = 720
    image_timeout_seconds: int = 90
    pollinations_model: str = "flux"
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    gemini_image_model: str = "gemini-2.5-flash-image"

    # --- sinks ---
    sinks: str = "local"
    gdrive_webapp_url: str = ""
    gdrive_shared_secret: str = ""

    # --- diversity ---
    diversity_cooldown: int = 3
    diversity_history: int = 50

    # --- execution ---
    run_mode: str = "inline"
    redis_url: str = "redis://localhost:6379/0"
    fake_providers: bool = False
    notify_url: str = ""

    # --- paths ---
    out_dir: Path = ROOT / "out"
    db_path: Path = ROOT / "data" / "forge.db"

    @property
    def image_provider_chain(self) -> list[str]:
        return [p.strip() for p in self.image_providers.split(",") if p.strip()]

    @property
    def sink_names(self) -> list[str]:
        return [s.strip() for s in self.sinks.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
