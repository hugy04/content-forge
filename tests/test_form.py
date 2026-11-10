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


@pytest.mark.parametrize("label", [
    "Disagree",        # angle: contrarian
    "Close-up",        # composition: close_detail
    "Mono",            # palette: monochrome_pop
    "Poster print",    # art_direction: risograph
    "Beginners",       # audience: newcomers
])
def test_options_read_as_english_not_as_keys(page, label):
    import html
    assert f">{html.escape(label)}</option>" in page


def test_no_label_is_longer_than_a_dropdown(page):
    """A label that wraps is a label nobody skims."""
    from forge.diversity.labels import LABELS

    for dimension, options in LABELS.items():
        for key, (label, hint) in options.items():
            assert len(label) <= 18, f"{dimension}.{key}: {label!r} too long"
            assert len(hint) <= 48, f"{dimension}.{key}: hint too long"


def test_the_model_s_own_wording_is_not_shown_to_a_reader(page):
    """`taxonomy.py` is written for the image prompt, not for a dropdown."""
    assert ANGLES["contrarian"] not in page
    assert PALETTES["monochrome_pop"] not in page
    assert COMPOSITIONS["close_detail"] not in page


def test_no_option_falls_back_to_a_raw_key(page):
    """Every option in every pool has a label, so none can leak underscores."""
    from forge.diversity.labels import LABELS
    from forge.diversity.taxonomy import (ANGLES as A, ART_DIRECTIONS, AUDIENCES,
                                          COMPOSITIONS as C, PALETTES as P)

    pools = {"angle": A, "audience": AUDIENCES, "art_direction": ART_DIRECTIONS,
             "palette": P, "composition": C}
    for dimension, pool in pools.items():
        assert set(pool) == set(LABELS[dimension]), f"{dimension} labels out of sync"


def test_every_control_defaults_to_auto(page):
    """An untouched form behaves exactly as it did before the panel existed."""
    assert page.count('<option value=""') == len(DIMENSIONS)


def test_every_select_explains_what_auto_would_do(page):
    """So "Auto" is a described choice rather than a shrug."""
    assert page.count("data-auto=") == len(DIMENSIONS)
    assert "a look recent runs have not used" in page
    assert "a format recent runs have not used" in page


def test_every_select_has_a_guide_line(page):
    """The description shows on the page, not only in a tooltip nobody hovers."""
    assert page.count('<small class="guide"></small>') == len(DIMENSIONS)
    assert "chosen for you" in page


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
