"""Central configuration. Every knob is env-overridable; nothing is hardcoded in a stage."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- text generation ---
    # `text_provider` selects the backend. Gemini is the default because its
    # free tier makes a run cost nothing; `claude` is the same pipeline on a
    # metered key and is the documented upgrade path for output quality.
    text_provider: str = "gemini"
    anthropic_api_key: str = ""
    gemini_api_key: str = ""

    # Per-provider default model. Stage-level overrides below win when set.
    gemini_model: str = "gemini-3.8-flash"
    claude_model: str = "claude-opus-5-5"
    # Seconds between text calls to one model. Free Gemini keys allow only
    # a handful of requests per minute, and spacing beats discovering the
    # cap with a 429.
    gemini_min_interval_seconds: float = 0.0

    # Leave blank to inherit the active provider's default model.
    outline_model: str = ""
    draft_model: str = ""
    prompt_model: str = ""
    outline_effort: str = "medium"
    draft_effort: str = "medium"
    prompt_effort: str = "low"

    # Optional paid-tier rates, for cost modelling on a free-tier provider.
    price_input_per_mtok: float = -1.0
    price_output_per_mtok: float = -1.0

    # --- content contract ---
    target_words_min: int = 500
    target_words_max: int = 800
    max_revisions: int = 2
    deadline_seconds: int = 300

    # A flash model that has not answered in this long is not going to. The
    # default was 120s, which let a single unavailable model consume a third of
    # the run budget before fall-forward even began - and on the hosted free
    # tier that starved the draft stage entirely. See docs/RUNBOOK.md.
    text_timeout_seconds: float = 45.0

    # Optional HTTP basic auth over the whole surface. Blank (the default)
    # leaves it open, which is what local development and the test suite want;
    # set both to gate a public deployment. /healthz stays open either way so an
    # uptime monitor does not need credentials.
    basic_auth_user: str = ""
    basic_auth_pass: str = ""

    # --- image generation ---
    image_providers: str = "gemini,pollinations,card"
    image_width: int = 1280
    image_height: int = 720
    image_timeout_seconds: int = 90
    pollinations_model: str = "flux"
    # Known per-IP floors, in seconds. Pollinations' anonymous tier is ~1 image
    # per 45s; a little headroom avoids tripping it on the boundary.
    pollinations_min_interval_seconds: float = 47.0
    gemini_image_min_interval_seconds: float = 0.0
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    openai_api_key: str = ""
    gemini_image_model: str = "gemini-3-pro-image-preview"

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

    @model_validator(mode="after")
    def _fill_stage_models(self) -> "Settings":
        default = {
            "gemini": self.gemini_model,
            "claude": self.claude_model,
            "fake": "fixture",
        }.get(self.text_provider, self.gemini_model)
        for field_name in ("outline_model", "draft_model", "prompt_model"):
            if not getattr(self, field_name):
                object.__setattr__(self, field_name, default)
        return self

    def image_min_interval(self, provider: str) -> float:
        return {
            "pollinations": self.pollinations_min_interval_seconds,
            "gemini": self.gemini_image_min_interval_seconds,
        }.get(provider, 0.0)

    @property
    def price_override(self) -> tuple[float, float] | None:
        if self.price_input_per_mtok < 0 or self.price_output_per_mtok < 0:
            return None
        return (self.price_input_per_mtok, self.price_output_per_mtok)

    @property
    def text_provider_key(self) -> str:
        """The API key the *active* text provider will actually use.

        Asking the provider-agnostic question in one place keeps health checks
        and diagnostics honest: a check hard-coded to one vendor's key reports
        a healthy service as misconfigured the moment the backend is switched,
        which is exactly what happened on the first hosted deploy.
        """
        return {
            "gemini": self.gemini_api_key,
            "claude": self.anthropic_api_key,
        }.get(self.text_provider, "")

    @property
    def active_text_model(self) -> str:
        """The model id the active provider starts from."""
        return {
            "gemini": self.gemini_model,
            "claude": self.claude_model,
        }.get(self.text_provider, "")

    @property
    def text_provider_configured(self) -> bool:
        return self.fake_providers or bool(self.text_provider_key)

    @property
    def image_provider_chain(self) -> list[str]:
        return [p.strip() for p in self.image_providers.split(",") if p.strip()]

    @property
    def sink_names(self) -> list[str]:
        return [s.strip() for s in self.sinks.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
