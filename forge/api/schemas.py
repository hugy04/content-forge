from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from forge.diversity.taxonomy import DIMENSIONS


class GenerateRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=300,
                       description="The subject to generate a content asset for.")
    style: str | None = Field(None, description=f"One of {list(DIMENSIONS['style'])}")
    angle: str | None = None
    audience: str | None = None
    art_direction: str | None = None
    palette: str | None = None
    composition: str | None = None
    notify_url: str | None = Field(None, description="Optional callback POSTed on completion.")

    @field_validator("style", "angle", "audience", "art_direction", "palette",
                     "composition")
    @classmethod
    def _known_option(cls, value: str | None, info) -> str | None:
        if value in (None, ""):
            return None
        pool = DIMENSIONS[info.field_name]
        if value not in pool:
            raise ValueError(f"{info.field_name} must be one of {pool}")
        return value

    def overrides(self) -> dict[str, str]:
        return {
            key: getattr(self, key)
            for key in DIMENSIONS
            if getattr(self, key, None)
        }


class BatchRequest(BaseModel):
    topics: list[str] = Field(..., min_length=1, max_length=50)
    concurrency: int = Field(3, ge=1, le=10)
    style: str | None = None


class RunSummary(BaseModel):
    run_id: str
    status: Literal["pending", "running", "completed", "partial", "failed"]
    topic: str
    title: str | None = None
    word_count: int | None = None
    detail: dict[str, Any] | None = None
