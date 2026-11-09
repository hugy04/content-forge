from __future__ import annotations

import pathlib
import tempfile

import pytest

from forge.config import Settings
from forge.pipeline.runner import build_deps


@pytest.fixture
def tmp_settings() -> Settings:
    tmp = pathlib.Path(tempfile.mkdtemp())
    return Settings(
        fake_providers=True,
        image_providers="card",
        sinks="local",
        out_dir=tmp / "out",
        db_path=tmp / "data" / "forge.db",
        deadline_seconds=300,
        anthropic_api_key="",
    )


@pytest.fixture
def deps(tmp_settings):
    return build_deps(tmp_settings)
