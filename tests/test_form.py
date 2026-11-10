"""The page has to offer what the API accepts.

A control the API takes but the page hides is a control nobody uses. The six
settings are what every prompt is built from, so "how are the prompts decided?"
should have an answer a reader can check for themselves rather than take on
trust.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from forge.diversity.taxonomy import ANGLES, COMPOSITIONS, DIMENSIONS, PALETTES


@pytest.fixture()
def page(monkeypatch, tmp_path) -> str:
    monkeypatch.setenv("FAKE_PROVIDERS", "1")
    monkeypatch.setenv("BASIC_AUTH_USER", "")
    monkeypatch.setenv("BASIC_AUTH_PASS", "")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "forge.db"))
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    from forge.config import get_settings
    get_settings.cache_clear()
    import importlib

    import forge.api.app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app).get("/").text


def test_the_form_offers_every_dimension_the_api_accepts(page):
    for key in DIMENSIONS:
        assert f'id="{key}" name="{key}"' in page, f"{key} has no control"
        assert f'for="{key}"' in page, f"{key} has no label"


@pytest.mark.parametrize("description", [
    ANGLES["contrarian"],
    COMPOSITIONS["close_detail"],
    PALETTES["monochrome_pop"],
])
def test_each_option_carries_the_wording_the_model_is_given(page, description):
    """The tooltip is the taxonomy's own text, so the page cannot drift from it."""
    assert description in page


def test_every_added_control_defaults_to_auto(page):
    """An untouched form behaves exactly as it did before the panel existed."""
    assert page.count('<option value="">Auto</option>') == len(DIMENSIONS) - 1
    assert '<option value="">Auto (rotate for diversity)</option>' in page


def test_the_stepper_names_every_pipeline_stage(page):
    """The six markers are the six stages, so the page cannot misdescribe them."""
    for stage in ("plan", "outline", "draft", "qc", "image", "publish"):
        assert f'"{stage}"' in page


def test_the_run_id_is_not_shown_to_a_reader(page):
    """An internal identifier is for the run record, not the finished piece."""
    assert "run record</a>" not in page


def test_the_stepper_has_a_failure_state(page):
    """A dropped request should show where it stopped, not just vanish."""
    assert "step.fail" in page
    assert "paintSteps(step, step)" in page
