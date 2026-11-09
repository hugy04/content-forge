"""Shared test fixtures.

The whole suite runs offline. That guarantee is only real if tests are also
isolated from the developer's own `.env` - otherwise a test asserting "this
provider is not configured" quietly starts making live calls the moment someone
adds a key, which is exactly what happened once. `isolated_settings()` builds a
Settings object from explicit arguments and nothing else.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from forge.config import Settings
from forge.pipeline.runner import build_deps


def isolated_settings(**overrides) -> Settings:
    """Settings with the on-disk .env deliberately ignored."""
    base: dict = {
        "anthropic_api_key": "",
        "gemini_api_key": "",
        "openai_api_key": "",
        "cloudflare_account_id": "",
        "cloudflare_api_token": "",
        "gdrive_webapp_url": "",
        "sinks": "local",
        "pollinations_min_interval_seconds": 0.0,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def tmp_settings() -> Settings:
    tmp = pathlib.Path(tempfile.mkdtemp())
    return isolated_settings(
        fake_providers=True,
        image_providers="card",
        out_dir=tmp / "out",
        db_path=tmp / "data" / "forge.db",
        deadline_seconds=300,
    )


@pytest.fixture
def deps(tmp_settings):
    return build_deps(tmp_settings)
