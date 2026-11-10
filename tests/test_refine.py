"""A revision the reader asked for.

The pipeline always had a revision stage; only QC could reach it. A content
team that wanted a sharper opening had to re-run the whole thing and hope the
engine rolled the same settings, which it is specifically designed not to.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_PROVIDERS", "1")
    monkeypatch.setenv("BASIC_AUTH_USER", "")
    monkeypatch.setenv("BASIC_AUTH_PASS", "")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "forge.db"))
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    import forge.config
    forge.config.get_settings.cache_clear()
    import forge.api.app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app)


def _run(client) -> dict:
    body = client.post("/v1/generate",
                       json={"topic": "warehouse robotics", "style": "guide"}).json()
    return body.get("detail", body)


def test_a_revision_keeps_the_run_its_image_and_its_settings(client):
    first = _run(client)
    response = client.post(f"/v1/runs/{first['run_id']}/revise",
                           json={"instruction": "sharpen the opening"})
    assert response.status_code == 200
    revised = response.json()

    assert revised["run_id"] == first["run_id"]
    assert revised["selection"] == first["selection"], "settings are not re-rolled"
    assert revised["image"]["provider"] == first["image"]["provider"], "image is reused"
    assert revised["revisions"] > first["revisions"]


def test_a_revision_is_recorded(client):
    first = _run(client)
    client.post(f"/v1/runs/{first['run_id']}/revise", json={"instruction": "shorter"})
    stored = client.get(f"/v1/runs/{first['run_id']}").json()
    assert stored["revisions"] > first["revisions"]


def test_an_unknown_run_is_a_404(client):
    assert client.post("/v1/runs/nosuchrun/revise",
                       json={"instruction": "x"}).status_code == 404


@pytest.mark.parametrize("body", [{}, {"instruction": ""}, {"instruction": "   "},
                                  {"instruction": 7}])
def test_a_revision_needs_an_instruction(client, body):
    first = _run(client)
    assert client.post(f"/v1/runs/{first['run_id']}/revise",
                       json=body).status_code == 400


def test_a_very_long_instruction_is_truncated_not_rejected():
    """A pasted paragraph is a reasonable thing to do and a poor reason to fail."""
    from forge.pipeline.stages.refine import MAX_INSTRUCTION

    assert MAX_INSTRUCTION == 500


def test_the_page_offers_the_revision_box(client):
    page = client.get("/").text
    assert 'id="instruction"' in page
    assert 'id="revise-go"' in page
    assert "/revise" in page


def test_a_revision_shows_its_own_progress(client):
    """Ten to thirty seconds with nothing but a button label is a dead page."""
    page = client.get("/").text
    assert "REVISE_STEPS" in page
    assert '["Rewriting", "applying your change"' in page
    assert "paintSteps(0, undefined, REVISE_STEPS)" in page


def test_a_revision_retries_a_dropped_connection_too(client):
    """It drops for the same reason a generate does, and used to show the
    browser's own words with no retry behind them."""
    page = client.get("/").text
    assert "await submitWithRetry(" in page
    assert "/revise`," in page
    # The raw exception is never what the reader is shown.
    assert "String(err.message || err)" in page
    assert "The connection dropped three times." in page


def test_one_retry_policy_serves_both_calls(client):
    """Two copies of a retry policy is one copy that will be forgotten."""
    page = client.get("/").text
    assert page.count("async function submitWithRetry") == 1
    assert page.count("function waitForWake") == 1
