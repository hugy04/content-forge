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


def test_every_auto_option_is_labelled_the_same_way(page):
    """Format used to say "Auto (rotate for diversity)" while the other five
    said "Auto", which read as a different kind of setting."""
    assert page.count(">Auto</option>") == len(DIMENSIONS)
    assert "rotate for diversity" not in page
    assert "diversity engine" not in page


def test_every_select_has_a_guide_line(page):
    """The description shows on the page, not only in a tooltip nobody hovers."""
    assert page.count('<small class="guide"></small>') == len(DIMENSIONS)
    assert "chosen for you" in page


@pytest.mark.parametrize("name", ["Setup", "Outline", "Writing", "Review",
                                  "Image", "Done"])
def test_the_stepper_names_the_six_steps_in_plain_words(page, name):
    assert f'["{name}",' in page


def test_the_stepper_does_not_use_the_code_s_own_stage_names(page):
    """"qc" and "publish" are module names; a content creator never says them."""
    assert '["qc",' not in page
    assert '["publish",' not in page


def test_the_run_id_is_not_shown_to_a_reader(page):
    """An internal identifier is for the run record, not the finished piece."""
    assert "run record</a>" not in page


def test_the_stepper_has_a_failure_state(page):
    """A dropped request should show where it stopped, not just vanish."""
    assert "step.fail" in page
    assert "paintSteps(step, step)" in page


def test_no_em_dash_reaches_the_page(page):
    """House style, and the one place a regex over the source could not see it
    was a JavaScript escape rather than a literal character."""
    assert "—" not in page
    assert "&mdash;" not in page
    assert "\\u2014" not in page


def test_a_format_does_not_claim_a_reading_level(page):
    """Format sets the shape; `audience` sets who it is pitched at.

    The explanatory article used to describe "an intelligent reader", which
    read as a contradiction next to a Written for of Beginners.
    """
    assert "intelligent reader" not in page


def test_a_dropped_connection_retries_itself(page):
    """A recycled free instance should not cost the reader a button press."""
    assert "submitWithRetry" in page
    assert "MAX_ATTEMPTS = 3" in page
    assert "waitForWake" in page


def test_only_a_transient_failure_is_retried(page):
    """An API error has a response and must be shown, not retried in silence.

    A gateway answering for a restarting server counts as transient: it was
    reaching the reader as Safari's "The string did not match the expected
    pattern", which explains nothing and was never retried because a
    SyntaxError is not a TypeError.
    """
    assert "err instanceof Transient || err instanceof TypeError" in page
    assert "if (!transient || attempt >= MAX_ATTEMPTS) throw err;" in page


def test_a_non_json_reply_is_read_as_text_first(page):
    """Calling .json() on an HTML error page throws, and the throw is useless."""
    assert "const body = await r.text();" in page
    assert "try { data = JSON.parse(body); }" in page
    assert "the server answered ${r.status} while restarting" in page


def test_the_retry_waits_for_the_instance_before_spending_an_attempt(page):
    assert '"/healthz", {cache:"no-store"}' in page


@pytest.mark.parametrize("jargon", ["falsifiable", "steelman", "axonometric"])
def test_no_jargon_reaches_the_dropdown(page, jargon):
    """The descriptions are read by someone choosing, not by an editor."""
    assert jargon.lower() not in page.lower()


def test_the_list_format_says_how_it_differs(page):
    """Otherwise it is indistinguishable from the explanatory article.

    It no longer says "numbered", because nothing is numbered any more: every
    format renders as headings and paragraphs so a reader meets one shape.
    """
    assert "Five to seven items, each its own heading" in page


def test_the_result_reports_choices_in_the_words_the_form_offered(page):
    """Picking "Clay" and being told "oxblood_stone" looks like being ignored."""
    import json
    import re

    labels = json.loads(re.search(r"const LABELS = (\{.*?\});", page, re.S).group(1))
    assert labels["palette"]["oxblood_stone"] == "Clay"
    assert labels["angle"]["economics"] == "Money"
    assert labels["audience"]["newcomers"] == "Beginners"
    assert labels["art_direction"]["risograph"] == "Poster print"
    assert labels["style"]["opinion"] == "Argued opinion piece"


def test_the_label_map_covers_every_dimension(page):
    import json
    import re

    labels = json.loads(re.search(r"const LABELS = (\{.*?\});", page, re.S).group(1))
    assert set(labels) == set(DIMENSIONS)


def test_the_result_shows_the_framing_it_used(page):
    """Image distance is a setting now, so a run has to report it back."""
    assert 'label("composition", sel.composition)' in page


def test_the_elapsed_clock_survives_a_retry(page):
    """The pipeline restarts on a retry; the person's wait does not.

    Showing "3s elapsed" to someone who has been watching for ninety seconds
    is the page telling them something they can see is untrue.
    """
    assert "t0 = tTotal = Date.now()" in page
    assert "const total = ((Date.now()-tTotal)/1000)" in page
    # The step pacing keeps its own clock, which does restart.
    assert "attemptNo = attempt + 1; t0 = Date.now(); step = 0;" in page


def test_a_retry_says_which_attempt_it_is_on(page):
    assert "attempt ${attemptNo} of ${MAX_ATTEMPTS}" in page


def test_the_steps_stay_on_screen_after_the_piece_arrives(page):
    """They are the account of how it was made, and hiding them at the moment
    someone finally has time to read them throws that away."""
    # The only place it hides is when the reader clears the page themselves.
    assert page.count('statusBox.style.display = "none"') == 1
    assert "newrun.addEventListener" in page
    assert "All six steps completed." in page
    assert "finished in ${total}s, inside the 300s budget" in page


def test_an_audience_describes_who_it_is_not_only_what_it_wants(page):
    """"cost, risk and timing" named their concerns without saying who they are."""
    assert "senior people weighing cost and risk" in page
    assert "cost, risk and timing" not in page


def test_a_finished_run_offers_a_clean_slate(page):
    """Clearing by hand is four actions - empty the topic, reset six selects,
    scroll past the piece - for one intention."""
    assert 'id="newrun"' in page
    assert "New content" in page
    assert 'topic.value = ""' in page
    assert 'document.getElementById("reset").click()' in page


def test_the_clean_slate_button_is_hidden_until_there_is_something_to_clear(page):
    assert 'id="newrun" class="pill" hidden' in page
    assert "newrun.hidden = false;" in page


def test_the_settings_do_not_wear_the_platform_s_own_chrome(page):
    """Six native selects read as six different controls, not one row."""
    assert "appearance:none" in page
    assert "-webkit-appearance:none" in page
    assert "image/svg+xml" in page, "one chevron, drawn in the page's own ink"


def test_the_image_rate_limit_is_stated_rather_than_felt(page):
    """The pipeline waits out the free service's one-image-per-45-seconds
    limit, which is correct and looks from the outside exactly like being
    slow. A run that takes two minutes for a stated reason is a different
    experience from one that takes two minutes."""
    assert 'id="pace"' in page
    assert "IMAGE_INTERVAL_MS = 47000" in page
    assert "one image every 45 seconds" in page


def test_the_pace_hint_survives_a_private_window(page):
    """localStorage throws there; the hint is a convenience, not a dependency."""
    assert "catch (_) { return 0; }" in page
    assert "private window; the hint simply will not show" in page


def test_the_image_step_says_why_it_might_wait(page):
    assert "so this step waits if a piece was made recently" in page


def test_a_failed_run_says_what_actually_failed(page):
    """A failed run returns its whole summary, which carries `errors`, not
    `detail` - so the page showed "request failed (500)" while holding the
    reason in its hand."""
    assert "Array.isArray(data.errors) && data.errors.length" in page


def test_a_busy_model_is_explained_not_quoted(page):
    """The provider's words are accurate and written for an operator."""
    assert "function humanError(raw)" in page
    assert "The writing model is busy right now" in page
    assert "The daily limit on the free model has been reached" in page
